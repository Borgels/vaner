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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import os

import aiosqlite
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from vaner_tools.artefact_store import is_stale, list_artefacts, read_repo_index
from vaner_tools.paths import REPO_ROOT, resolve_repo_path
from vaner_tools.repo_tools import find_files, grep_text, list_files, read_file

model = ChatOllama(
    model="devstral",
    temperature=0,
)


SYSTEM_PROMPT = f"""You are a software engineering agent working on Vaner — a predictive context platform.

Vaner's core idea: instead of only reacting at prompt time, the system pre-computes likely-useful context
in the background (repo-analyzer agent), and a broker (you) decides at prompt time what cached material
is still fresh and relevant to inject into the model's context.

You are the broker agent. Your job is to help developers build, navigate, debug, and extend the Vaner
codebase. You have access to cached file summaries from the repo-analyzer (injected above as Pre-loaded
context when available) and a set of tools to read and write files directly.

Repository root: {REPO_ROOT}

Architecture:
- apps/studio-agent/   → broker agent (you)
- apps/repo-analyzer/  → analyzer agent (pre-computes file/dir summaries)
- libs/vaner-tools/    → shared tools and artefact store
- .vaner/cache/        → artefact cache (file summaries, dir summaries, repo index)
- docs/                → architecture docs and build specs

Rules:
- Be concise. Prefer action over explanation.
- Do not be chatty or add filler.
- For non-trivial tasks, plan briefly first, then act.
- Use tools to read and write files. Do not invent file contents.
- To call a tool, respond ONLY with valid JSON (one object per line for multiple calls).
- After receiving tool results, answer the user in plain language. Do not output JSON then.
- Stay inside the repository root.
- Prefer targeted reads over broad ones.

Available tools:
  - list_files(path: string = ".") -> list files in a directory
  - read_file(path: string) -> read a text file (max 50KB)
  - find_files(pattern: string, path: string = ".") -> glob-style file search
  - grep_text(query: string, path: string = ".", file_pattern: string = "*", max_results: int = 50) -> text search
  - write_file(path: string, content: string) -> write or overwrite a file inside the repo
  - run_command(command: string, cwd: string = ".") -> run a safe shell command inside the repo (read-only: ls, cat, python, pytest, grep, find, git status, git log, git diff — no destructive commands)
  - write_todos(todos: list[{{"task": str, "status": "pending"|"in_progress"|"done"}}]) -> save a task plan to disk
  - read_todos() -> read the current task plan
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


def try_parse_tool_calls(content: str) -> list[dict] | None:
    """Parse tool calls from model output.

    Handles multiple formats devstral uses:
    1. Standard: {"name": "tool_name", "arguments": {...}}
    2. Devstral variant: {"tool_name": {"args": {...}}} — single key is the tool name
    3. Devstral variant: {"tool_name": {"arg1": val1}} — single key, value IS the args
    4. Array of any of the above
    5. JSON embedded in markdown code fences
    """
    import re

    if not content or not content.strip():
        return None

    text = content.strip()

    # Strip markdown code fences
    if "```" in text:
        fence_match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
        if fence_match:
            text = fence_match.group(1).strip()

    def normalize_call(obj: dict) -> dict | None:
        if "name" in obj and isinstance(obj.get("name"), str):
            return {
                "name": obj["name"],
                "arguments": obj.get("arguments", obj.get("args", obj.get("parameters", {}))),
            }
        keys = [k for k in obj if not k.startswith("_")]
        if len(keys) == 1:
            tool_name = keys[0]
            val = obj[tool_name]
            if isinstance(val, dict):
                args = val.get("args", val.get("arguments", val.get("parameters", val)))
                return {"name": tool_name, "arguments": args if isinstance(args, dict) else val}
        return None

    # Try to find and parse JSON from the text
    candidates = [text]
    # Also try extracting first JSON object/array via brace matching
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
                    n = normalize_call(item)
                    if n:
                        calls.append(n)
        elif isinstance(parsed, dict):
            n = normalize_call(parsed)
            if n:
                calls.append(n)

        if calls:
            return calls

    return None


# ---------------------------------------------------------------------------
# Node: load_context_from_cache
# ---------------------------------------------------------------------------


async def load_context_from_cache(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    """Load relevant file summaries from the repo index cache.

    If the index is missing or stale, falls through silently (cache_hit=False).
    Otherwise keyword-matches against user_input and injects the top 5 summaries.
    """
    index = read_repo_index()
    if index is None:
        return {"cache_hit": False, "cached_context": ""}

    # Check staleness of the index itself (treat generated_at as its own timestamp)
    generated_at = index.get("generated_at", 0)
    age = time.time() - generated_at
    if age > 3600:
        return {"cache_hit": False, "cached_context": ""}

    files: dict[str, dict] = index.get("files", {})
    if not files:
        return {"cache_hit": False, "cached_context": ""}

    # Simple keyword match: count overlapping words between user_input and path+summary
    user_words = set(state.user_input.lower().split())
    scored: list[tuple[float, str, str]] = []

    for path, info in files.items():
        summary = info.get("summary", "")
        candidate_text = (path + " " + summary).lower()
        candidate_words = set(candidate_text.split())
        overlap = len(user_words & candidate_words)
        if overlap > 0:
            scored.append((overlap, path, summary))

    if not scored:
        return {"cache_hit": False, "cached_context": ""}

    scored.sort(key=lambda x: x[0], reverse=True)
    top5 = scored[:5]

    lines = ["## Pre-loaded context (from cache)\n"]
    for _, path, summary in top5:
        lines.append(f"### {path}\n{summary}\n")

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
            path = arguments.get("path")
            content = arguments.get("content", "")
            if not path:
                result = "Missing required argument: path"
            else:
                result = await write_file_tool(path, content)
        elif name == "run_command":
            command = arguments.get("command")
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


async def second_model_call(state: State, runtime: Runtime[Context]) -> dict[str, str]:
    cache_section = ""
    if state.cache_hit and state.cached_context:
        cache_section = f"\n\n{state.cached_context}"

    prompt = (
        f"{SYSTEM_PROMPT}"
        f"{cache_section}\n\n"
        f"Original user request:\n{state.user_input}\n\n"
        f"Tool calls used:\n{json.dumps(state.tool_requests, indent=2)}\n\n"
        f"Tool results:\n{json.dumps(state.tool_results, indent=2)}\n\n"
        "Now answer the user. Do not output tool-call JSON."
    )

    result = await model.ainvoke(prompt)

    content = result.content
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", str(part)) if isinstance(part, dict) else str(part)
            for part in content
        )

    new_messages = list(state.messages) + [
        {"role": "user", "content": state.user_input},
        {"role": "assistant", "content": str(content).strip()},
    ]
    return {"response": str(content).strip(), "messages": new_messages}


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
    .add_edge("second_model_call", END)
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
