"""LangGraph coding/planning agent for the Vaner repo (broker).

Supports:
- multiple JSON tool calls returned by the model
- async-safe filesystem access via asyncio.to_thread
- repo root pinned to ~/repos/Vaner
- cache-backed context via vaner_tools.artefact_store
- tools:
  - list_files
  - read_file
  - find_files
  - grep_text
  - write_file
  - run_command
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict
from vaner_tools.artefact_store import list_artefacts
from vaner_tools.paths import REPO_ROOT, resolve_repo_path
from vaner_tools.repo_tools import find_files, grep_text, list_files, read_file

model = ChatOllama(
    model="qwen2.5-coder:32b",
    temperature=0,
)


SYSTEM_PROMPT = f"""You are a senior software engineering agent working on Vaner — a predictive context runtime.

Model: qwen2.5-coder:32b running locally on RTX 5090.
Repository root: {REPO_ROOT}
Working branch: develop

Architecture:
- apps/vaner-daemon/   → vaner.ai product: daemon, event collector, state engine
- apps/vaner-builder/  → this agent (builder tooling, not the product)
- apps/repo-analyzer/  → pre-computes file/dir summaries into .vaner/cache/
- apps/supervisor/     → orchestrates the builder stack
- libs/vaner-tools/    → shared artefact store + scoring
- libs/vaner-runtime/  → job store, retry, queue, structured logging
- docs/roadmap.md      → architecture decisions and phase plan
- eval/                → A/B evaluation framework

Operating mode — SEQUENTIAL TASK EXECUTION:
You receive one clearly-scoped task at a time. For each task:
1. Read relevant files FIRST — never write without reading the context
2. Use write_todos to break the task into steps and track progress
3. Implement one file at a time, completely — no stubs, no placeholders
4. Run tests after each significant change: run_command("python -m pytest tests/ -v", cwd=...)
5. Run lint: run_command("python -m ruff check src/ --ignore E501,D,T201,ANN", cwd=...)
6. When done: summarise exactly what was changed and what tests pass
7. STOP — do not proceed to the next task without explicit instruction

Quality rules (non-negotiable):
- No stubs. No TODOs in production code. Every function fully implemented.
- Tests must pass before reporting done.
- Lint must be clean before reporting done.
- Read existing code carefully — match patterns, imports, and conventions already in use.
- When integrating with libs/vaner-runtime/ or libs/vaner-tools/, read those files first.
- After writing any code: run pytest and ruff. Report confidence 1-10. Explicitly confirm each acceptance criterion from the issue is met.

Tool calling — use {{"command": "tool_name", "arg": "value"}} format.

Available tools — call as raw JSON (no markdown, no explanation before the JSON):

  list_files:   {{"command": "list_files", "path": "."}}
  read_file:    {{"command": "read_file", "path": "src/foo.py"}}
  find_files:   {{"command": "find_files", "pattern": "*.py", "path": "src/"}}
  grep_text:    {{"command": "grep_text", "query": "class Foo", "path": "src/", "file_pattern": "*.py", "max_results": 20}}
  write_file:   {{"command": "write_file", "path": "src/foo.py", "content": "# full file content here"}}
  run_command:  {{"command": "run_command", "command": "python -m pytest tests/ -v", "cwd": "apps/vaner-daemon"}}
  write_todos:  {{"command": "write_todos", "todos": [{{"task": "step 1", "status": "pending"}}]}}
  read_todos:   {{"command": "read_todos"}}

IMPORTANT: Output ONLY the JSON object for a tool call. Do NOT wrap in markdown fences. Do NOT narrate. After receiving tool results, you may write a plain text summary.
"""


class Context(TypedDict, total=False):
    extra_context: str


os.makedirs(str(REPO_ROOT / ".vaner"), exist_ok=True)
_BROKER_DB = str(REPO_ROOT / ".vaner" / "memory.db")


@dataclass
class State:
    user_input: str = ""
    response: str = ""
    tool_requests: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    cached_context: str = ""
    cache_hit: bool = False
    messages: list[dict] = field(default_factory=list)
    tool_round: int = 0


# ---------------------------------------------------------------------------
# Tool implementations: write_file and run_command
# ---------------------------------------------------------------------------


def _write_file_sync(path: str, content: str) -> str:
    try:
        target = resolve_repo_path(path)
    except ValueError as e:
        return f"Error: {e}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Written: {path} ({len(content)} bytes)"


async def write_file_tool(path: str, content: str) -> str:
    try:
        return await asyncio.to_thread(_write_file_sync, path, content)
    except Exception as e:
        return f"Error writing {path}: {e}"


SAFE_COMMANDS = {"ls", "cat", "python", "python3", "pytest", "grep", "find", "git", "uv", "pip", "head", "tail", "wc", "echo", "tree"}


def _run_command_sync(command: str, cwd: str = ".") -> str:
    import shlex
    import subprocess
    try:
        cwd_path = resolve_repo_path(cwd)
    except ValueError as e:
        return f"Error: {e}"

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return f"Invalid command: {e}"

    if not parts:
        return "Empty command"

    if parts[0] not in SAFE_COMMANDS:
        return f"Command not allowed: {parts[0]}. Allowed: {', '.join(sorted(SAFE_COMMANDS))}"

    try:
        result = subprocess.run(
            parts,
            cwd=cwd_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out (30s)"
    except Exception as e:
        return f"Error running command: {e}"


async def run_command_tool(command: str, cwd: str = ".") -> str:
    try:
        return await asyncio.to_thread(_run_command_sync, command, cwd)
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool call parser
# ---------------------------------------------------------------------------


def _normalize_tool_call(obj: dict) -> dict | None:
    """Normalize a parsed JSON object into a standard {name, arguments} tool call dict."""
    # Format 1: standard {"name": "tool_name", "arguments": {...}}
    if "name" in obj and isinstance(obj.get("name"), str):
        return {
            "name": obj["name"],
            "arguments": obj.get("arguments", obj.get("args", obj.get("parameters", {}))),
        }
    # Format 2a: devstral {"type": "tool_name", "arg1": val1, ...}
    if "type" in obj and isinstance(obj.get("type"), str):
        tool_name = obj["type"]
        args = {k: v for k, v in obj.items() if k != "type"}
        return {"name": tool_name, "arguments": args}
    # Format 2b: devstral/qwen {"command": "tool_name", "arg1": val1, ...}
    if "command" in obj and isinstance(obj.get("command"), str):
        tool_name = obj["command"]
        args = {k: v for k, v in obj.items() if k != "command"}
        return {"name": tool_name, "arguments": args}
    # Format 3: single-key {"tool_name": {"args": {...}}} or {"tool_name": {...}}
    keys = [k for k in obj if not k.startswith("_")]
    if len(keys) == 1:
        tool_name = keys[0]
        val = obj[tool_name]
        if isinstance(val, dict):
            args = val.get("args", val.get("arguments", val.get("parameters", val)))
            return {"name": tool_name, "arguments": args if isinstance(args, dict) else val}
    return None


def _extract_calls_from_text(text: str) -> list[dict]:
    """Try to extract tool calls from a raw text block (no fence stripping)."""
    candidates = [text]
    # Extract all JSON objects/arrays via brace matching
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        depth = 0
        start_idx = None
        for i, ch in enumerate(text):
            if ch == start_char:
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0 and start_idx is not None:
                    candidates.append(text[start_idx:i + 1])
                    start_idx = None

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue

        calls = []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    n = _normalize_tool_call(item)
                    if n:
                        calls.append(n)
        elif isinstance(parsed, dict):
            n = _normalize_tool_call(parsed)
            if n:
                calls.append(n)
        if calls:
            return calls
    return []


def try_parse_tool_calls(content: str) -> list[dict] | None:
    """Parse tool calls from model output.

    Handles multiple formats:
    1. Standard: {"name": "tool_name", "arguments": {...}}
    2. Devstral: {"type": "tool_name", ...} or {"command": "tool_name", ...}
    3. Single-key: {"tool_name": {"args": {...}}}
    4. Array of any of the above
    5. JSON inside markdown code fences (qwen emits one block per call)
    6. Multiple fence blocks → multiple tool calls collected in order
    """
    import re

    if not content or not content.strip():
        return None

    text = content.strip()

    # Extract all JSON blocks from markdown code fences
    # qwen2.5-coder typically emits one ```json\n{...}\n``` block per tool call
    fence_blocks: list[str] = []
    if "```" in text:
        fence_blocks = re.findall(r"```(?:json|python)?\s*\n?([\s\S]*?)\n?```", text)
        fence_blocks = [b.strip() for b in fence_blocks if b.strip()]

    if fence_blocks:
        all_calls: list[dict] = []
        for block in fence_blocks:
            all_calls.extend(_extract_calls_from_text(block))
        if all_calls:
            return all_calls
        # Fences found but no tool calls — fall through with first block as text
        text = fence_blocks[0]

    calls = _extract_calls_from_text(text)
    if calls:
        return calls

    return None


# ---------------------------------------------------------------------------
# Node: load_context_from_cache
# ---------------------------------------------------------------------------


async def load_context_from_cache(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    """Load relevant file summaries from the repo index cache.

    If the cache is empty or no artifacts score above threshold, falls through
    silently (cache_hit=False). Uses TF-IDF scoring with stopword filtering and
    path boost to surface the most relevant cached summaries.
    """
    all_artefacts = await asyncio.to_thread(list_artefacts, "file_summary")
    if not all_artefacts:
        return {"cache_hit": False, "cached_context": ""}

    from vaner_tools.scoring import score_artifacts as _score_artifacts

    scored = _score_artifacts(state.user_input, all_artefacts, max_results=5)
    top_artefacts = [s.artifact for s in scored]

    if not top_artefacts:
        return {"cache_hit": False, "cached_context": ""}

    lines = ["## Pre-loaded context (from cache)\n"]
    for artefact in top_artefacts:
        lines.append(f"### {artefact.source_path}\n{artefact.content}\n")

    cached_context = "\n".join(lines)
    return {"cache_hit": True, "cached_context": cached_context}


# ---------------------------------------------------------------------------
# Node: first_model_call
# ---------------------------------------------------------------------------


async def first_model_call(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    user_prompt = state.user_input.strip()

    if not user_prompt:
        return {"response": "No input provided."}

    extra_context = ""
    if runtime.context and runtime.context.get("extra_context"):
        extra_context = (
            "\n\nAdditional runtime context:\n"
            f"{runtime.context['extra_context']}"
        )

    history_section = ""
    if state.messages:
        history_lines = []
        for msg in state.messages[-10:]:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            history_lines.append(f"{role.upper()}: {content}")
        history_section = "\n\n## Conversation history\n" + "\n\n".join(history_lines)

    cache_section = ""
    if state.cache_hit and state.cached_context:
        cache_section = f"\n\n{state.cached_context}"

    prompt = (
        f"{SYSTEM_PROMPT}"
        f"{history_section}"
        f"{cache_section}\n\n"
        f"User request:\n{user_prompt}"
        f"{extra_context}"
    )

    result = await model.ainvoke(prompt)

    content = result.content
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", str(part)) if isinstance(part, dict) else str(part)
            for part in content
        )
    content = str(content).strip()

    tool_calls = try_parse_tool_calls(content)
    if tool_calls:
        return {"tool_requests": tool_calls, "response": "", "tool_results": []}

    new_messages = list(state.messages) + [
        {"role": "user", "content": user_prompt},
        {"role": "assistant", "content": content},
    ]
    return {"response": content, "tool_requests": [], "tool_results": [], "messages": new_messages}


def route_after_first_call(state: State) -> str:
    return "run_tools" if state.tool_requests else "end"


# ---------------------------------------------------------------------------
# Node: run_tools
# ---------------------------------------------------------------------------


async def run_tools(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    for tool_call in state.tool_requests:
        name = tool_call["name"]
        arguments = tool_call["arguments"]

        if name == "list_files":
            result = await list_files(arguments.get("path", "."))
        elif name == "read_file":
            path = arguments.get("path")
            result = "Missing required argument: path" if not path else await read_file(path)
        elif name == "find_files":
            pattern = arguments.get("pattern")
            if not pattern:
                result = "Missing required argument: pattern"
            else:
                result = await find_files(pattern, arguments.get("path", "."))
        elif name == "grep_text":
            query = arguments.get("query")
            if not query:
                result = "Missing required argument: query"
            else:
                result = await grep_text(
                    query=query,
                    path=arguments.get("path", "."),
                    file_pattern=arguments.get("file_pattern", "*"),
                    max_results=int(arguments.get("max_results", 50)),
                )
        elif name == "write_file":
            path = arguments.get("path") or arguments.get("file") or arguments.get("filename")
            content = arguments.get("content") or arguments.get("text") or arguments.get("data") or ""
            # Handle qwen packing both into "arg": "path, content"
            if not path and "arg" in arguments and isinstance(arguments["arg"], str):
                parts = arguments["arg"].split(",", 1)
                path = parts[0].strip()
                content = parts[1].strip() if len(parts) > 1 else ""
            if not path:
                result = "Missing required argument: path"
            else:
                result = await write_file_tool(path, content)
        elif name == "run_command":
            command = (
                arguments.get("command")
                or arguments.get("cmd")
                or arguments.get("arg")
            )
            if not command:
                result = "Missing required argument: command"
            else:
                result = await run_command_tool(command, arguments.get("cwd", "."))
        elif name == "write_todos":
            todos_arg = arguments.get("todos", arguments.get("items", []))
            if not isinstance(todos_arg, list):
                todos_arg = []
            todos_path = REPO_ROOT / ".vaner" / "todos.json"
            todos_path.parent.mkdir(parents=True, exist_ok=True)
            todos_path.write_text(json.dumps(todos_arg, indent=2))
            pending = [t for t in todos_arg if isinstance(t, dict) and t.get("status") != "done"]
            result = f"Plan saved: {len(todos_arg)} tasks, {len(pending)} pending"
        elif name == "read_todos":
            todos_path = REPO_ROOT / ".vaner" / "todos.json"
            if todos_path.exists():
                result = todos_path.read_text()
            else:
                result = "No plan yet. Use write_todos to create one."
        else:
            result = f"Unknown tool: {name}"

        results.append(
            {
                "tool": name,
                "arguments": arguments,
                "result": result,
            }
        )

    return {"tool_results": results}


# ---------------------------------------------------------------------------
# Node: second_model_call
# ---------------------------------------------------------------------------


MAX_TOOL_ROUNDS = 12  # safety limit — prevents infinite loops


async def second_model_call(state: State, runtime: Runtime[Context]) -> dict:
    """Runs after tool execution. May issue more tool calls (up to MAX_TOOL_ROUNDS)."""
    cache_section = ""
    if state.cache_hit and state.cached_context:
        cache_section = f"\n\n{state.cached_context}"

    # Build tool history for context
    tool_history = ""
    if state.tool_requests or state.tool_results:
        tool_history = (
            f"\n\nTool calls so far:\n{json.dumps(state.tool_requests, indent=2)}\n\n"
            f"Tool results so far:\n{json.dumps(state.tool_results, indent=2)}\n\n"
        )

    tool_round = getattr(state, "tool_round", 1)
    at_limit = tool_round >= MAX_TOOL_ROUNDS

    instruction = (
        "You have reached the maximum tool rounds. Summarise what was done and answer the user."
        if at_limit
        else "If more tool calls are needed, output the next JSON tool call. Otherwise summarise and answer."
    )

    prompt = (
        f"{SYSTEM_PROMPT}"
        f"{cache_section}\n\n"
        f"Original user request:\n{state.user_input}"
        f"{tool_history}"
        f"{instruction}"
    )

    result = await model.ainvoke(prompt)
    content = result.content
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", str(part)) if isinstance(part, dict) else str(part)
            for part in content
        )
    content = str(content).strip()

    if not at_limit:
        tool_calls = try_parse_tool_calls(content)
        if tool_calls:
            return {
                "tool_requests": tool_calls,
                "tool_results": [],
                "response": "",
                "tool_round": tool_round + 1,
            }

    new_messages = list(state.messages) + [
        {"role": "user", "content": state.user_input},
        {"role": "assistant", "content": content},
    ]
    return {
        "response": content,
        "messages": new_messages,
        "tool_requests": [],
        "tool_results": [],
    }


def route_after_second_call(state: State) -> str:
    return "run_tools" if getattr(state, "tool_requests", []) else END


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

_builder = (
    StateGraph(State, context_schema=Context)
    .add_node("load_context_from_cache", load_context_from_cache)
    .add_node("first_model_call", first_model_call)
    .add_node("run_tools", run_tools)
    .add_node("second_model_call", second_model_call)
    .add_edge("__start__", "load_context_from_cache")
    .add_edge("load_context_from_cache", "first_model_call")
    .add_conditional_edges(
        "first_model_call",
        route_after_first_call,
        {
            "run_tools": "run_tools",
            "end": END,
        },
    )
    .add_edge("run_tools", "second_model_call")
    .add_conditional_edges(
        "second_model_call",
        route_after_second_call,
        {
            "run_tools": "run_tools",
            END: END,
        },
    )
)

# graph without checkpointer — for import-time validation / test
graph = _builder.compile(name="Vaner Broker")


async def build_graph():
    """Return a compiled broker graph with AsyncSqliteSaver checkpointer.

    Must be called from within a running event loop (e.g. inside asyncio.run()).
    Creates a fresh aiosqlite connection bound to the current event loop.
    """
    _conn_cm = aiosqlite.connect(_BROKER_DB)
    conn = await _conn_cm.__aenter__()
    checkpointer = AsyncSqliteSaver(conn)
    return _builder.compile(name="Vaner Broker", checkpointer=checkpointer)
