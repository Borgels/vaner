"""Eval workflow — LangGraph batch scoring for EvalSignals."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from vaner_runtime.eval import EvalSignal, load_signals
from vaner_runtime.judge import judge_helpfulness

logger = logging.getLogger("vaner.eval_workflow")

_DEFAULT_DB = Path.home() / ".vaner" / "eval.db"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def load_signals_node(state: dict) -> dict:
    db_path: Path = state.get("db_path", _DEFAULT_DB)
    since_days: int = state.get("since_days", 7)
    signals = load_signals(db_path, since_days=since_days)
    # Only re-score signals that were injected but have no helpfulness score yet
    pending = [s for s in signals if s.injected and s.helpfulness is None]
    logger.info("load_signals: %d total, %d pending scoring", len(signals), len(pending))
    return {"signals": signals, "pending": pending}


async def score_signals_node(state: dict) -> dict:
    pending: list[EvalSignal] = state.get("pending", [])
    ollama_url: str = state.get("ollama_url", "http://localhost:11434")
    model: str = state.get("model", "qwen2.5-coder:32b")
    scored: list[EvalSignal] = []

    async def _score_one(signal: EvalSignal) -> EvalSignal:
        # We only have the prompt_hash stored; judge with what we have
        score = await judge_helpfulness(
            context="(stored hash only — no raw context available)",
            prompt=signal.prompt_hash,
            response="",
            model=model,
            ollama_url=ollama_url,
        )
        signal.helpfulness = score
        return signal

    # Batch in groups of 3
    for i in range(0, len(pending), 3):
        batch = pending[i: i + 3]
        results = await asyncio.gather(*[_score_one(s) for s in batch], return_exceptions=True)
        for r in results:
            if isinstance(r, EvalSignal):
                scored.append(r)
            else:
                logger.warning("score_signals: error in batch: %s", r)

    logger.info("score_signals: scored %d signals", len(scored))
    return {"scored": scored}


async def aggregate_node(state: dict) -> dict:
    signals: list[EvalSignal] = state.get("signals", [])
    scored: list[EvalSignal] = state.get("scored", [])

    all_with_score = [s for s in signals if s.helpfulness is not None] + [
        s for s in scored if s.helpfulness is not None
    ]
    injected = [s for s in signals if s.injected]
    non_injected = [s for s in signals if not s.injected]

    def _reprompt_rate(subset: list[EvalSignal]) -> float:
        if not subset:
            return 0.0
        return sum(1 for s in subset if s.reprompted) / len(subset)

    avg_helpfulness = (
        sum(s.helpfulness for s in all_with_score) / len(all_with_score)
        if all_with_score
        else None
    )

    summary: dict[str, Any] = {
        "total_signals": len(signals),
        "injected": len(injected),
        "non_injected": len(non_injected),
        "avg_helpfulness": avg_helpfulness,
        "reprompt_rate_injected": _reprompt_rate(injected),
        "reprompt_rate_non_injected": _reprompt_rate(non_injected),
        "model_referenced_pct": (
            sum(1 for s in signals if s.model_referenced) / len(signals)
            if signals
            else 0.0
        ),
    }
    logger.info("aggregate: summary=%s", summary)
    return {"summary": summary}


def _route_after_load(state: dict) -> str:
    return "score_signals" if state.get("pending") else "aggregate"


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def build_eval_graph() -> Any:
    workflow = StateGraph(dict)
    workflow.add_node("load_signals", load_signals_node)
    workflow.add_node("score_signals", score_signals_node)
    workflow.add_node("aggregate", aggregate_node)

    workflow.set_entry_point("load_signals")
    workflow.add_conditional_edges("load_signals", _route_after_load)
    workflow.add_edge("score_signals", "aggregate")
    workflow.add_edge("aggregate", END)

    return workflow.compile(name="Vaner Eval Workflow")


async def run_eval_workflow(
    db_path: Path = _DEFAULT_DB,
    since_days: int = 7,
    ollama_url: str = "http://localhost:11434",
    model: str = "qwen2.5-coder:32b",
) -> dict[str, Any]:
    graph = build_eval_graph()
    result = await graph.ainvoke({
        "db_path": db_path,
        "since_days": since_days,
        "ollama_url": ollama_url,
        "model": model,
    })
    return result.get("summary", {})
