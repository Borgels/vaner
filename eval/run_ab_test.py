#!/usr/bin/env python3
"""A/B evaluation: measures whether context injection improves broker responses.

Runs 20 queries in two conditions (with/without injection), scores with Claude judge.
"""
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps/supervisor/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/vaner-builder/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/repo-analyzer/src"))
sys.path.insert(0, str(REPO_ROOT / "libs/vaner-tools/src"))

# Load env
def _load_env(p):
    if not Path(p).exists(): return
    for line in Path(p).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

_load_env(REPO_ROOT / "apps/supervisor/.env")
_load_env(REPO_ROOT / "apps/vaner-builder/.env")

# Load Anthropic API key from OpenClaw config if not set
def _load_openclaw_anthropic_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
    if not openclaw_config.exists():
        return
    try:
        cfg = json.loads(openclaw_config.read_text())
        key = cfg.get("models", {}).get("providers", {}).get("anthropic", {}).get("apiKey", "")
        if key and key.startswith("sk-ant-"):
            os.environ["ANTHROPIC_API_KEY"] = key
            print(f"  [info] Loaded ANTHROPIC_API_KEY from OpenClaw config")
    except Exception as e:
        print(f"  [warn] Could not load OpenClaw config: {e}")

_load_openclaw_anthropic_key()

# 20 test queries — mix of navigation, debugging, architecture, implementation
TEST_QUERIES = [
    # Navigation
    "Where is the staleness check for artifacts implemented and what are the rules?",
    "What functions does repo_tools.py export?",
    "Where does the supervisor decide whether to refresh the cache?",
    "What is the directory structure of libs/vaner-tools?",
    "Where is try_parse_tool_calls defined and what formats does it handle?",
    # Code understanding
    "What does the Artefact dataclass look like and what fields does it have?",
    "How does load_context_from_cache work in the broker?",
    "What LangGraph nodes make up the analyzer graph and in what order do they run?",
    "What is the SKIP_DIRS set used for in the analyzer?",
    "How does the broker's scoring decide which artifacts to inject?",
    # Debugging
    "The analyzer is generating summaries for .egg-info files. How would I fix that?",
    "A broker response is returning raw JSON instead of an answer. What's wrong?",
    "The repo_index is missing from the cache after running the analyzer. Where should I look?",
    "Why would cache_hit be False even though artifacts exist in the store?",
    "The supervisor is always triggering a cache refresh. What controls the freshness threshold?",
    # Implementation
    "How would I add a new artifact type to the preparation pipeline?",
    "What changes would be needed to add a new tool to the broker?",
    "How does the artefact_store write artifacts to disk?",
    "What does the scoring.py tokenize function do and why?",
    "How would I check if a specific file has an up-to-date summary in the cache?",
]


@dataclass
class QueryResult:
    query: str
    condition: str  # "WITH" or "WITHOUT"
    response: str
    cache_hit: bool
    tool_calls_made: int
    latency_ms: float
    error: str = ""


@dataclass
class EvalResult:
    query: str
    response_with: str
    response_without: str
    cache_hit_with: bool
    tool_calls_with: int
    tool_calls_without: int
    latency_with_ms: float
    latency_without_ms: float
    judge_score_with: int = 0      # 1-5
    judge_score_without: int = 0   # 1-5
    judge_winner: str = ""          # "WITH", "WITHOUT", "TIE"
    judge_reasoning: str = ""
    direct_answer_with: bool = False   # answered without needing tools
    direct_answer_without: bool = False


async def run_broker(user_input: str, inject_context: bool, thread_id: str) -> QueryResult:
    """Run broker with or without context injection.

    NOTE: LangGraph captures node functions at .add_node() time (module-level compile),
    so monkey-patching load_context_from_cache on the module doesn't work.
    Instead, we patch graph_module.list_artefacts → empty list, which prevents
    load_context_from_cache from finding any artifacts (cache_hit=False).
    This is the correct disable path since load_context_from_cache calls
    list_artefacts from the module's global namespace at runtime.
    """
    import agent  # noqa: ensure agent package + agent.graph are loaded into sys.modules
    graph_module = sys.modules["agent.graph"]

    start = time.perf_counter()
    error = ""
    response = ""
    cache_hit = False
    tool_calls = 0

    orig_list = graph_module.list_artefacts

    try:
        if not inject_context:
            # Patch list_artefacts to return empty — disables cache injection
            graph_module.list_artefacts = lambda kind=None: []

        g = await graph_module.build_graph()

        result = await g.ainvoke(
            {"user_input": user_input},
            config={"configurable": {"thread_id": thread_id}},
        )
        response = result.get("response", "")
        cache_hit = result.get("cache_hit", False)
        tool_calls = len(result.get("tool_requests", []))

    except Exception as e:
        import traceback
        error = str(e)
        tb = traceback.format_exc()
        response = f"ERROR: {e}"
        print(f"\n    [error] {error}")
        if len(tb) < 500:
            print(f"    {tb}")
    finally:
        # Always restore
        graph_module.list_artefacts = orig_list

    latency_ms = (time.perf_counter() - start) * 1000
    return QueryResult(
        query=user_input,
        condition="WITH" if inject_context else "WITHOUT",
        response=response,
        cache_hit=cache_hit,
        tool_calls_made=tool_calls,
        latency_ms=latency_ms,
        error=error,
    )


def judge_with_claude(query: str, response_with: str, response_without: str) -> dict:
    """Use Claude to judge which response is better. Returns scores and reasoning."""
    import anthropic

    client = anthropic.Anthropic()

    prompt = f"""You are evaluating two AI assistant responses to the same developer question about a Python codebase called vaner.ai.

Response A was generated WITH pre-computed file summaries injected as context.
Response B was generated WITHOUT any pre-injected context.

Question: {query}

Response A (WITH context injection):
{response_with[:1500]}

Response B (WITHOUT context injection):
{response_without[:1500]}

Score each response on:
1. Correctness (is the information accurate and specific to what was asked?)
2. Specificity (does it name actual functions, files, classes — not generic statements?)
3. Completeness (does it fully answer the question or leave important gaps?)

Respond with ONLY valid JSON in this exact format:
{{
  "score_a": <1-5>,
  "score_b": <1-5>,
  "winner": "<A|B|TIE>",
  "direct_answer_a": <true if A answered directly without needing to look things up, false otherwise>,
  "direct_answer_b": <true if B answered directly without needing to look things up, false otherwise>,
  "reasoning": "<one sentence explaining the key difference>"
}}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # Extract JSON if wrapped in markdown
        import re
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        print(f"  Judge error: {e}")

    return {"score_a": 0, "score_b": 0, "winner": "TIE", "direct_answer_a": False, "direct_answer_b": False, "reasoning": "judge failed"}


async def run_evaluation() -> list[EvalResult]:
    results = []

    for i, query in enumerate(TEST_QUERIES):
        print(f"\n[{i+1:02d}/20] {query[:70]}...")

        # Run WITH injection
        print("  → WITH injection...", end="", flush=True)
        with_result = await run_broker(query, inject_context=True, thread_id=f"eval-with-{i}")
        print(f" {with_result.latency_ms:.0f}ms, cache_hit={with_result.cache_hit}, tools={with_result.tool_calls_made}")
        if with_result.error:
            print(f"    [with error] {with_result.error[:100]}")

        # Run WITHOUT injection
        print("  → WITHOUT injection...", end="", flush=True)
        without_result = await run_broker(query, inject_context=False, thread_id=f"eval-without-{i}")
        print(f" {without_result.latency_ms:.0f}ms, tools={without_result.tool_calls_made}")
        if without_result.error:
            print(f"    [without error] {without_result.error[:100]}")

        # Judge
        print("  → Judging...", end="", flush=True)
        judgment = judge_with_claude(query, with_result.response, without_result.response)
        print(f" winner={judgment.get('winner','?')} (A={judgment.get('score_a',0)}, B={judgment.get('score_b',0)})")

        results.append(EvalResult(
            query=query,
            response_with=with_result.response,
            response_without=without_result.response,
            cache_hit_with=with_result.cache_hit,
            tool_calls_with=with_result.tool_calls_made,
            tool_calls_without=without_result.tool_calls_made,
            latency_with_ms=with_result.latency_ms,
            latency_without_ms=without_result.latency_ms,
            judge_score_with=judgment.get("score_a", 0),
            judge_score_without=judgment.get("score_b", 0),
            judge_winner="WITH" if judgment.get("winner") == "A" else ("WITHOUT" if judgment.get("winner") == "B" else "TIE"),
            judge_reasoning=judgment.get("reasoning", ""),
            direct_answer_with=judgment.get("direct_answer_a", False),
            direct_answer_without=judgment.get("direct_answer_b", False),
        ))

    return results


def print_report(results: list[EvalResult]) -> None:
    total = len(results)
    with_wins = sum(1 for r in results if r.judge_winner == "WITH")
    without_wins = sum(1 for r in results if r.judge_winner == "WITHOUT")
    ties = sum(1 for r in results if r.judge_winner == "TIE")
    cache_hits = sum(1 for r in results if r.cache_hit_with)
    direct_with = sum(1 for r in results if r.direct_answer_with)
    direct_without = sum(1 for r in results if r.direct_answer_without)
    avg_score_with = sum(r.judge_score_with for r in results) / total if total else 0
    avg_score_without = sum(r.judge_score_without for r in results) / total if total else 0
    avg_tools_with = sum(r.tool_calls_with for r in results) / total if total else 0
    avg_tools_without = sum(r.tool_calls_without for r in results) / total if total else 0
    avg_latency_with = sum(r.latency_with_ms for r in results) / total if total else 0
    avg_latency_without = sum(r.latency_without_ms for r in results) / total if total else 0

    print("\n" + "="*70)
    print("VANER.AI A/B EVALUATION REPORT")
    print("="*70)
    print(f"\nQueries evaluated: {total}")
    print(f"\n--- Judge Verdicts ---")
    print(f"WITH injection wins:    {with_wins:2d} / {total}  ({100*with_wins//total}%)")
    print(f"WITHOUT injection wins: {without_wins:2d} / {total}  ({100*without_wins//total}%)")
    print(f"Ties:                   {ties:2d} / {total}  ({100*ties//total}%)")
    print(f"\n--- Quality Scores (1-5) ---")
    print(f"Avg score WITH:         {avg_score_with:.2f}")
    print(f"Avg score WITHOUT:      {avg_score_without:.2f}")
    print(f"Score delta:            {avg_score_with - avg_score_without:+.2f}")
    print(f"\n--- Key Metrics ---")
    print(f"Cache hit rate:         {cache_hits}/{total} ({100*cache_hits//total}%)")
    print(f"Direct answers WITH:    {direct_with}/{total} ({100*direct_with//total}%)")
    print(f"Direct answers WITHOUT: {direct_without}/{total} ({100*direct_without//total}%)")
    print(f"Avg tool calls WITH:    {avg_tools_with:.1f}")
    print(f"Avg tool calls WITHOUT: {avg_tools_without:.1f}")
    print(f"Avg latency WITH:       {avg_latency_with:.0f}ms")
    print(f"Avg latency WITHOUT:    {avg_latency_without:.0f}ms")

    print(f"\n--- Per-Query Results ---")
    for i, r in enumerate(results):
        winner_str = {"WITH": "✓ WITH", "WITHOUT": "✗ WITHOUT", "TIE": "= TIE"}[r.judge_winner]
        print(f"[{i+1:02d}] {winner_str} | A={r.judge_score_with} B={r.judge_score_without} | tools {r.tool_calls_with}→{r.tool_calls_without} | {r.judge_reasoning[:60]}")

    print(f"\n--- Go/No-Go Assessment ---")
    injection_advantage = with_wins / total if total else 0
    direct_lift = (direct_with - direct_without) / total if total else 0
    tool_reduction = (avg_tools_without - avg_tools_with) / max(avg_tools_without, 0.1)

    if injection_advantage >= 0.40 and avg_score_with > avg_score_without:
        verdict = "GO ✅"
        reason = "Injection wins >40% of queries AND improves average quality"
    elif injection_advantage >= 0.30 and direct_lift > 0.15:
        verdict = "CONDITIONAL GO ⚠️"
        reason = "Directness improvement is clear but win rate below 40% — improve artifact quality before scaling"
    elif injection_advantage >= 0.25:
        verdict = "MARGINAL — IMPROVE FIRST ⚠️"
        reason = "Some signal but not enough to justify Phase 1 infrastructure without improving artifact relevance"
    else:
        verdict = "NO-GO ❌"
        reason = "Injection not providing consistent benefit — root cause must be identified before proceeding"

    print(f"\nVerdict: {verdict}")
    print(f"Reason:  {reason}")
    print(f"\nKey signal: injection wins {100*injection_advantage:.0f}% of queries, direct answer lift = {100*direct_lift:+.0f}pp, tool call reduction = {100*tool_reduction:.0f}%")

    # Save full results to JSON
    out_path = Path(__file__).parent / "ab_results.json"
    with open(str(out_path), "w") as f:
        json.dump([
            {
                "query": r.query,
                "winner": r.judge_winner,
                "score_with": r.judge_score_with,
                "score_without": r.judge_score_without,
                "cache_hit": r.cache_hit_with,
                "tool_calls_with": r.tool_calls_with,
                "tool_calls_without": r.tool_calls_without,
                "latency_with_ms": r.latency_with_ms,
                "latency_without_ms": r.latency_without_ms,
                "direct_with": r.direct_answer_with,
                "direct_without": r.direct_answer_without,
                "reasoning": r.judge_reasoning,
                "response_with": r.response_with[:500],
                "response_without": r.response_without[:500],
            }
            for r in results
        ], f, indent=2)
    print(f"\nFull results saved to: {out_path}")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = loop.run_until_complete(run_evaluation())
        print_report(results)
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
