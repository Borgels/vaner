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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from vaner_tools.artefact_store import is_stale, list_artefacts, read_repo_index
from vaner_tools.paths import REPO_ROOT
from vaner_tools.repo_tools import find_files, grep_text, list_files, read_file

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
    cached_context: str = ""
    cache_hit: bool = False


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
    import time
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
    if runtime.context and runtime.context.get("my_configurable_param"):
        extra_context = (
            "\n\nAdditional runtime context:\n"
            f"{runtime.context['my_configurable_param']}"
        )

    cache_section = ""
    if state.cache_hit and state.cached_context:
        cache_section = f"\n\n{state.cached_context}"

    prompt = (
        f"{SYSTEM_PROMPT}"
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

    return {"response": content, "tool_requests": [], "tool_results": []}


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


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

graph = (
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
    .compile(name="Vaner Coding Assistant")
)
