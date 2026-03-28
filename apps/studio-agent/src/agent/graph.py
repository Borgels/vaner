"""LangGraph coding/planning agent for the Vaner repo.

Supports:
- multiple JSON tool calls returned by the model
- async-safe filesystem access via asyncio.to_thread
- repo root pinned to ~/repos/Vaner
- tools:
  - list_files
  - read_file
  - find_files
  - grep_text
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict


REPO_ROOT = Path("~/repos/Vaner").expanduser().resolve()

model = ChatOllama(
    model="devstral",
    temperature=0,
)


SYSTEM_PROMPT = f"""You are a software engineering agent working inside a git repository.

Repository root:
{REPO_ROOT}

Rules:
- Be concise.
- Prefer action over explanation.
- Do not be chatty.
- For non-trivial tasks, first think in a compact plan.
- When you need repository information, you may call tools.
- To call tools, respond ONLY with valid JSON.
- You may return either:
  1) a single tool call:
     {{"name": "tool_name", "arguments": {{"arg1": "value"}}}}
  2) multiple tool calls, one JSON object per line
- Available tools:
  - list_files(path: string = ".") -> list files in a directory
  - read_file(path: string) -> read a text file
  - find_files(pattern: string, path: string = ".") -> find files by glob-style pattern
  - grep_text(query: string, path: string = ".", file_pattern: string = "*", max_results: int = 50) -> search text in files
- Paths are relative to the repository root unless absolute.
- Stay inside the repository root.
- Prefer targeted tools over broad ones when possible.
- After receiving tool results, continue normally and answer the user.
- Do not output tool-call JSON unless you are making a tool call.
- Do not invent files, APIs, or project details.
"""


class Context(TypedDict, total=False):
    my_configurable_param: str


@dataclass
class State:
    user_input: str = ""
    response: str = ""
    tool_requests: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


def resolve_repo_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    candidate = candidate.resolve()

    if candidate != REPO_ROOT and REPO_ROOT not in candidate.parents:
        raise ValueError(f"Path escapes repository root: {path}")

    return candidate


def _list_files_sync(path: str = ".") -> str:
    target = resolve_repo_path(path)
    if not target.exists():
        return f"Path does not exist: {path}"
    if not target.is_dir():
        return f"Path is not a directory: {path}"

    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    if not entries:
        return f"No files found in: {path}"

    return "\n".join(entries)


async def list_files(path: str = ".") -> str:
    try:
        return await asyncio.to_thread(_list_files_sync, path)
    except Exception as e:
        return f"Error listing files in {path}: {e}"


def _read_file_sync(path: str) -> str:
    target = resolve_repo_path(path)
    if not target.exists():
        return f"File does not exist: {path}"
    if not target.is_file():
        return f"Path is not a file: {path}"

    text = target.read_text(encoding="utf-8")
    max_chars = 12000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[truncated]"
    return text


async def read_file(path: str) -> str:
    try:
        return await asyncio.to_thread(_read_file_sync, path)
    except UnicodeDecodeError:
        return f"File is not valid UTF-8 text: {path}"
    except Exception as e:
        return f"Error reading file {path}: {e}"


def _find_files_sync(pattern: str, path: str = ".") -> str:
    target = resolve_repo_path(path)
    if not target.exists():
        return f"Path does not exist: {path}"
    if not target.is_dir():
        return f"Path is not a directory: {path}"

    matches: list[str] = []
    for p in target.rglob("*"):
        rel = str(p.relative_to(REPO_ROOT))
        name = p.name + ("/" if p.is_dir() else "")
        if fnmatch.fnmatch(p.name, pattern) or fnmatch.fnmatch(rel, pattern):
            matches.append(rel + ("/" if p.is_dir() else ""))

    matches = sorted(set(matches))
    if not matches:
        return f"No files matched pattern '{pattern}' under {path}"

    return "\n".join(matches[:200])


async def find_files(pattern: str, path: str = ".") -> str:
    try:
        return await asyncio.to_thread(_find_files_sync, pattern, path)
    except Exception as e:
        return f"Error finding files with pattern {pattern} in {path}: {e}"


def _grep_text_sync(
    query: str,
    path: str = ".",
    file_pattern: str = "*",
    max_results: int = 50,
) -> str:
    target = resolve_repo_path(path)
    if not target.exists():
        return f"Path does not exist: {path}"
    if not target.is_dir():
        return f"Path is not a directory: {path}"

    results: list[str] = []
    scanned = 0

    for p in target.rglob("*"):
        if not p.is_file():
            continue

        rel = str(p.relative_to(REPO_ROOT))
        if not (fnmatch.fnmatch(p.name, file_pattern) or fnmatch.fnmatch(rel, file_pattern)):
            continue

        scanned += 1
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append(f"{rel}:{lineno}: {line.strip()}")
                if len(results) >= max_results:
                    return "\n".join(results)

    if not results:
        return f"No matches for '{query}' under {path} with file pattern '{file_pattern}'"

    return "\n".join(results)


async def grep_text(
    query: str,
    path: str = ".",
    file_pattern: str = "*",
    max_results: int = 50,
) -> str:
    try:
        return await asyncio.to_thread(_grep_text_sync, query, path, file_pattern, max_results)
    except Exception as e:
        return f"Error searching for '{query}' in {path}: {e}"


def try_parse_tool_calls(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []

    calls: list[dict[str, Any]] = []

    try:
        data = json.loads(text)
        if (
            isinstance(data, dict)
            and "name" in data
            and "arguments" in data
            and isinstance(data["arguments"], dict)
        ):
            return [data]
    except json.JSONDecodeError:
        pass

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []
        if (
            not isinstance(data, dict)
            or "name" not in data
            or "arguments" not in data
            or not isinstance(data["arguments"], dict)
        ):
            return []
        calls.append(data)

    return calls


async def first_model_call(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    user_prompt = state.user_input.strip()

    if not user_prompt:
        return {"response": "No input provided."}

    extra_context = ""
    if runtime.context and runtime.context.get("my_configurable_param"):
        extra_context = (
            "\n\nAdditional runtime context:\n"
            f"{runtime.context['my_configurable_param']}"
        )

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
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

    return {"response": content, "tool_requests": [], "tool_results": []}


def route_after_first_call(state: State) -> str:
    return "run_tools" if state.tool_requests else "end"


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


async def second_model_call(state: State, runtime: Runtime[Context]) -> dict[str, str]:
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Original user request:\n{state.user_input}\n\n"
        f"Tool calls used:\n{json.dumps(state.tool_requests, indent=2)}\n\n"
        f"Tool results:\n{json.dumps(state.tool_results, indent=2)}\n\n"
        "Now answer the user normally. Do not output tool-call JSON."
    )

    result = await model.ainvoke(prompt)

    content = result.content
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", str(part)) if isinstance(part, dict) else str(part)
            for part in content
        )

    return {"response": str(content).strip()}


graph = (
    StateGraph(State, context_schema=Context)
    .add_node("first_model_call", first_model_call)
    .add_node("run_tools", run_tools)
    .add_node("second_model_call", second_model_call)
    .add_edge("__start__", "first_model_call")
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
    .compile(name="Vaner Coding Assistant")
)
