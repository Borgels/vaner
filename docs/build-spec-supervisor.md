# Build Spec: Supervisor + CLI + Multi-turn

**Status:** Ready to build  
**Builder:** OpenClaw + Claude  
**Depends on:** build-spec-two-agent.md (completed)

This spec adds the missing orchestration layer: a supervisor agent that wires the analyzer and broker together, multi-turn conversation threads for the broker, and a CLI entrypoint so you can just run `python vaner.py "your task"`.

---

## 1. Repo layout after this build

```
apps/
  supervisor/             ← NEW: top-level orchestrator
    src/supervisor/
      __init__.py
      graph.py
    pyproject.toml
    langgraph.json
    .env.example
  broker/                 ← RENAME from studio-agent (internal only, dir stays)
  repo-analyzer/

libs/
  vaner-tools/

vaner.py                  ← NEW: CLI entrypoint (repo root)
.vaner/
  tasks.md                ← NEW: simple task log (created at runtime)
```

---

## 2. Supervisor graph (`apps/supervisor/`)

The supervisor is the single entry point. It receives a user prompt, decides what to do, and coordinates the analyzer and broker.

### State

```python
@dataclass
class SupervisorState:
    user_input: str = ""
    response: str = ""
    cache_refreshed: bool = False
    plan: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
```

### Graph flow

```
__start__
    │
    ▼
check_cache_freshness     # is repo index missing or stale (>30 min)?
    │
    ├─ stale → refresh_cache → route_to_broker
    │
    └─ fresh → route_to_broker
    │
    ▼
route_to_broker           # invoke broker graph with user_input
    │
    ▼
log_task                  # append to .vaner/tasks.md
    │
    ▼
END
```

### Node: `check_cache_freshness`

```python
async def check_cache_freshness(state: SupervisorState) -> dict:
    index = read_repo_index()
    if index is None:
        return {"cache_refreshed": False, "_needs_refresh": True}
    
    age = time.time() - index.get("generated_at", 0)
    needs_refresh = age > 1800  # 30 minutes
    return {"_needs_refresh": needs_refresh}
```

Route: if `_needs_refresh` → `refresh_cache`, else → `route_to_broker`

### Node: `refresh_cache`

Import and invoke the analyzer graph directly:

```python
from analyzer.graph import graph as analyzer_graph

async def refresh_cache(state: SupervisorState) -> dict:
    print("[supervisor] Cache stale — running repo-analyzer...")
    result = await analyzer_graph.ainvoke({
        "target_path": ".",
        "force_refresh": False,  # only refresh what's actually stale
    })
    written = result.get("artefacts_written", [])
    print(f"[supervisor] Analyzer done: {len(written)} artefacts updated")
    return {"cache_refreshed": True}
```

**Important:** The supervisor imports the analyzer graph as a Python module (not via HTTP/LangGraph server). Both share the same venv or the supervisor's venv has both installed.

### Node: `route_to_broker`

Import and invoke the broker graph:

```python
from agent.graph import graph as broker_graph

async def route_to_broker(state: SupervisorState) -> dict:
    result = await broker_graph.ainvoke(
        {"user_input": state.user_input},
        config={"configurable": {"thread_id": "default"}},
    )
    return {"response": result.get("response", "")}
```

### Node: `log_task`

Append to `.vaner/tasks.md`:

```python
async def log_task(state: SupervisorState) -> dict:
    tasks_path = REPO_ROOT / ".vaner" / "tasks.md"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## {timestamp}\n**Input:** {state.user_input}\n\n**Response:**\n{state.response}\n\n---\n"
    
    with tasks_path.open("a", encoding="utf-8") as f:
        f.write(entry)
    
    return {}
```

---

## 3. Multi-turn broker (checkpointing)

LangGraph supports persistent conversation threads via a checkpointer. Add this to the broker graph so context is preserved across invocations.

### Changes to `apps/studio-agent/src/agent/graph.py`

1. Add `MemorySaver` checkpointer:

```python
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()

graph = (
    StateGraph(State, context_schema=Context)
    # ... same nodes and edges as before ...
    .compile(name="Vaner Broker", checkpointer=checkpointer)
)
```

2. Add `messages` field to State to accumulate conversation history:

```python
@dataclass
class State:
    user_input: str = ""
    response: str = ""
    tool_requests: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    cached_context: str = ""
    cache_hit: bool = False
    messages: list[dict] = field(default_factory=list)  # ← NEW
```

3. In `first_model_call`, prepend conversation history to the prompt:

```python
history_section = ""
if state.messages:
    history_lines = []
    for msg in state.messages[-10:]:  # last 10 exchanges
        role = msg.get("role", "?")
        content = msg.get("content", "")
        history_lines.append(f"{role.upper()}: {content}")
    history_section = "\n\n## Conversation history\n" + "\n\n".join(history_lines)
```

4. In `second_model_call` (and direct response path in `first_model_call`), append to messages:

```python
# After getting response, append to messages
new_messages = list(state.messages) + [
    {"role": "user", "content": state.user_input},
    {"role": "assistant", "content": response},
]
return {"response": response, "messages": new_messages}
```

### Thread IDs

Threads are identified by `thread_id` in the config. The CLI passes `thread_id` as a command-line option (default: `"main"`). Each thread has its own memory.

---

## 4. CLI entrypoint (`vaner.py` at repo root)

```python
#!/usr/bin/env python3
"""
Vaner CLI — talk to the orchestration system.

Usage:
    python vaner.py "your task or question"
    python vaner.py "your task" --thread work-session-1
    python vaner.py "your task" --no-supervisor   # bypass supervisor, direct broker
    python vaner.py --analyze                     # run analyzer only
    python vaner.py --history                     # show recent task log
"""
```

### Implementation

```python
import argparse
import asyncio
import sys
from pathlib import Path

# Add both app src paths so we can import their graphs directly
REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT / "apps/supervisor/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/studio-agent/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/repo-analyzer/src"))
sys.path.insert(0, str(REPO_ROOT / "libs/vaner-tools/src"))


async def run_supervisor(user_input: str, thread_id: str = "main") -> str:
    from supervisor.graph import graph as supervisor_graph
    result = await supervisor_graph.ainvoke(
        {"user_input": user_input},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result.get("response", "")


async def run_broker_direct(user_input: str, thread_id: str = "main") -> str:
    from agent.graph import graph as broker_graph
    result = await broker_graph.ainvoke(
        {"user_input": user_input},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result.get("response", "")


async def run_analyzer() -> None:
    from analyzer.graph import graph as analyzer_graph
    print("Running repo-analyzer...")
    result = await analyzer_graph.ainvoke({
        "target_path": ".",
        "force_refresh": True,
    })
    written = result.get("artefacts_written", [])
    errors = result.get("errors", [])
    print(f"Done: {len(written)} artefacts written")
    if errors:
        print(f"Errors: {errors}")


def show_history() -> None:
    tasks_path = REPO_ROOT / ".vaner" / "tasks.md"
    if not tasks_path.exists():
        print("No task history yet.")
        return
    content = tasks_path.read_text(encoding="utf-8")
    # Show last ~2000 chars
    if len(content) > 2000:
        content = "...\n" + content[-2000:]
    print(content)


def main():
    parser = argparse.ArgumentParser(description="Vaner orchestration CLI")
    parser.add_argument("input", nargs="?", help="Task or question")
    parser.add_argument("--thread", default="main", help="Thread ID for conversation memory")
    parser.add_argument("--no-supervisor", action="store_true", help="Bypass supervisor, direct to broker")
    parser.add_argument("--analyze", action="store_true", help="Run repo-analyzer only")
    parser.add_argument("--history", action="store_true", help="Show recent task log")
    args = parser.parse_args()

    if args.history:
        show_history()
        return

    if args.analyze:
        asyncio.run(run_analyzer())
        return

    if not args.input:
        parser.print_help()
        sys.exit(1)

    if args.no_supervisor:
        response = asyncio.run(run_broker_direct(args.input, args.thread))
    else:
        response = asyncio.run(run_supervisor(args.input, args.thread))

    print(response)


if __name__ == "__main__":
    main()
```

---

## 5. Supervisor `pyproject.toml`

```toml
[project]
name = "supervisor"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "langchain-ollama",
    "langgraph",
]

[tool.uv.sources]
vaner-tools = { path = "../../libs/vaner-tools", editable = true }
```

The supervisor's venv needs all three packages installed:
```bash
cd apps/supervisor
python3 -m venv .venv
.venv/bin/pip install -e . -e ../../libs/vaner-tools/ -q
# Also install the broker and analyzer so we can import their graphs
.venv/bin/pip install -e ../studio-agent/ -e ../repo-analyzer/ -q
```

---

## 6. `langgraph.json` for supervisor

```json
{
  "$schema": "https://langgra.ph/schema.json",
  "dependencies": ["."],
  "graphs": {
    "supervisor": "./src/supervisor/graph.py:graph"
  },
  "env": ".env",
  "image_distro": "wolfi"
}
```

---

## 7. Build order

1. Create `apps/supervisor/` with the supervisor graph
2. Add `MemorySaver` checkpointer + `messages` field to broker
3. Write `vaner.py` at repo root
4. Install supervisor venv with all dependencies
5. Verify: `python vaner.py --analyze` (analyzer runs)
6. Verify: `python vaner.py "what files are in apps/studio-agent/src?"` (supervisor → broker → response)
7. Verify multi-turn: run two sequential prompts with same `--thread`, confirm second knows about first
8. Commit

---

## 8. Out of scope for this build

- Streaming output (print tokens as they arrive) — nice to have, next iteration
- Multiple parallel broker tasks
- Eval agent
- File watcher / auto-refresh
- Web UI

---

## Success criteria

```bash
# Fresh start — analyzer refreshes stale cache, broker answers
python vaner.py "what is the artefact store and how does staleness work?"

# Multi-turn — second prompt references first
python vaner.py "list the tools available in the broker" --thread dev
python vaner.py "now show me the implementation of write_file" --thread dev

# Analyze only
python vaner.py --analyze

# History
python vaner.py --history
```
