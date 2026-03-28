from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
from vaner_tools.artefact_store import list_artefacts

from .generator import generate_diff_summary, generate_file_summary
from .planner import ArtifactJob, PreparationPlanner
from .triggers import PrepTrigger

logger = logging.getLogger("vaner.prep_graph")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def check_jobs_node(state: dict) -> dict:
    repo_root = Path(state["repo_root"])
    cache_root = repo_root / ".vaner" / "cache"
    trigger: PrepTrigger = state["trigger"]
    existing = list_artefacts(cache_root) if cache_root.exists() else []
    planner = PreparationPlanner(repo_root)
    jobs = planner.plan(trigger, existing)
    logger.info("check_jobs: %d job(s) planned for context_key=%s", len(jobs), trigger.context_key)
    return {"jobs": jobs, "context_key": trigger.context_key}


async def run_jobs_node(state: dict) -> dict:
    repo_root = Path(state["repo_root"])
    model_name: str = state.get("model_name", "qwen2.5-coder:32b")
    jobs: list[ArtifactJob] = state["jobs"]
    completed: list[str] = []
    errors: list[str] = []

    async def _run_one(job: ArtifactJob) -> None:
        try:
            if job.artifact_kind == "file_summary":
                result = await generate_file_summary(
                    Path(job.source_path), repo_root, model_name
                )
            else:
                result = await generate_diff_summary(repo_root, model_name)
            if result is not None:
                completed.append(job.job_id)
            else:
                errors.append(f"{job.job_id}: generator returned None")
        except Exception as exc:
            errors.append(f"{job.job_id}: {exc}")

    # Run in batches of 3
    for i in range(0, len(jobs), 3):
        batch = jobs[i : i + 3]
        await asyncio.gather(*[_run_one(j) for j in batch])

    logger.info("run_jobs: %d completed, %d errors", len(completed), len(errors))
    return {"completed": completed, "errors": errors}


async def update_index_node(state: dict) -> dict:
    repo_root = Path(state["repo_root"])
    cache_root = repo_root / ".vaner" / "cache"
    index_path = cache_root / "repo_index" / "root.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)

    all_artefacts = list_artefacts(cache_root) if cache_root.exists() else []
    index = {
        a.source_path: a.content
        for a in all_artefacts
        if a.kind == "file_summary" and a.content
    }
    index_path.write_text(json.dumps(index, indent=2))
    logger.info("update_index: wrote %d entries to repo_index", len(index))
    return {}


def _route_after_check(state: dict) -> str:
    return "run_jobs" if state.get("jobs") else END


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def build_preparation_graph(checkpoint_db_path: Path) -> Any:
    checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(checkpoint_db_path), check_same_thread=False)
    saver = SqliteSaver(conn=conn)

    workflow = StateGraph(dict)
    workflow.add_node("check_jobs", check_jobs_node)
    workflow.add_node("run_jobs", run_jobs_node)
    workflow.add_node("update_index", update_index_node)
    workflow.set_entry_point("check_jobs")
    workflow.add_conditional_edges("check_jobs", _route_after_check)
    workflow.add_edge("run_jobs", "update_index")
    workflow.add_edge("update_index", END)

    return workflow.compile(checkpointer=saver, name="Vaner Preparation Engine")
