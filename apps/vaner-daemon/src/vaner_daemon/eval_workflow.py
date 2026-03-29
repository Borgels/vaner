"""Eval workflow — LangGraph aggregate metrics for EvalSignals.

Metrics reported (all derived from stored EvalSignal fields — no LLM scoring):
  - total_signals: total number of signals in the window.
  - injected / non_injected: counts split by whether context was injected.
  - reprompt_rate_injected: fraction of injected signals where the user re-prompted.
  - reprompt_rate_non_injected: same for non-injected signals.
  - model_referenced_pct: fraction of signals where the model name was referenced.

Limitations:
  - Helpfulness is not scored because only prompt_hash (not raw text) is stored.
    Meaningful LLM-based scoring would require storing the full prompt/response,
    which raises privacy concerns. The aggregate metrics above are privacy-safe
    proxies derived purely from the flags already persisted in EvalSignal.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from vaner_runtime.eval import EvalSignal, load_signals

logger = logging.getLogger("vaner.eval_workflow")

_DEFAULT_DB = Path.home() / ".vaner" / "eval.db"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def load_signals_node(state: dict) -> dict:
    db_path: Path = state.get("db_path", _DEFAULT_DB)
    since_days: int = state.get("since_days", 7)
    signals = load_signals(db_path, since_days=since_days)
    logger.info("load_signals: %d total", len(signals))
    return {"signals": signals}


async def aggregate_node(state: dict) -> dict:
    """Compute aggregate metrics from stored EvalSignal fields.

    No LLM scoring is performed — raw prompt/response text is not stored
    (only a hash), so helpfulness cannot be meaningfully estimated.

    Metrics:
      reprompt_rate_injected     — re-prompt fraction for context-injected turns.
      reprompt_rate_non_injected — re-prompt fraction for non-injected turns.
      model_referenced_pct       — fraction of all turns where the model was cited.
      total_signals              — window size used for all rates above.
    """
    signals: list[EvalSignal] = state.get("signals", [])

    injected = [s for s in signals if s.injected]
    non_injected = [s for s in signals if not s.injected]

    def _reprompt_rate(subset: list[EvalSignal]) -> float:
        if not subset:
            return 0.0
        return sum(1 for s in subset if s.reprompted) / len(subset)

    summary: dict[str, Any] = {
        "total_signals": len(signals),
        "injected": len(injected),
        "non_injected": len(non_injected),
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


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def build_eval_graph() -> Any:
    workflow = StateGraph(dict)
    workflow.add_node("load_signals", load_signals_node)
    workflow.add_node("aggregate", aggregate_node)

    workflow.set_entry_point("load_signals")
    workflow.add_edge("load_signals", "aggregate")
    workflow.add_edge("aggregate", END)

    return workflow.compile(name="Vaner Eval Workflow")


async def run_eval_workflow(
    db_path: Path = _DEFAULT_DB,
    since_days: int = 7,
) -> dict[str, Any]:
    graph = build_eval_graph()
    result = await graph.ainvoke({
        "db_path": db_path,
        "since_days": since_days,
    })
    return result.get("summary", {})
