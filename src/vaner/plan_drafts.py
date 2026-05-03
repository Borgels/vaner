# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from vaner.policy.privacy import sanitize_no_absolute_paths

_LINE_MARKER_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")
_PLAN_OPEN_TAG = "<proposed_plan>"
_PLAN_CLOSE_TAG = "</proposed_plan>"
ACTIVE_PLAN_DRAFT_STATUSES = {"draft", "active"}
TERMINAL_PLAN_DRAFT_STATUSES = {"implemented", "completed", "superseded", "abandoned"}
PLAN_DRAFT_STATUSES = ACTIVE_PLAN_DRAFT_STATUSES | TERMINAL_PLAN_DRAFT_STATUSES
_GENERIC_PLAN_TITLES = {"captured plan", "draft plan", "proposed plan", "plan"}
_GENERIC_PLAN_TOKENS = {
    "add",
    "change",
    "check",
    "continue",
    "do",
    "fix",
    "implement",
    "make",
    "next",
    "one",
    "plan",
    "prepare",
    "review",
    "run",
    "step",
    "test",
    "thing",
    "update",
    "verify",
    "work",
}


@dataclass(slots=True)
class PlanDraft:
    id: str
    title: str
    summary: str
    tasks: list[str]
    source_client: str = "unknown"
    session_id: str = ""
    turn_id: str = ""
    workspace_id: str = ""
    source_event_id: str = ""
    status: str = "draft"
    accepted: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    prep_dir: str = ""


def extract_proposed_plan_blocks(value: Any) -> list[str]:
    """Return proposed-plan blocks from a string or nested JSON-like value."""

    blocks: list[str] = []

    def plan_blocks(text: str) -> list[str]:
        lower = text.lower()
        found: list[str] = []
        cursor = 0
        while True:
            start = lower.find(_PLAN_OPEN_TAG, cursor)
            if start < 0:
                return found
            content_start = start + len(_PLAN_OPEN_TAG)
            end = lower.find(_PLAN_CLOSE_TAG, content_start)
            if end < 0:
                return found
            found.append(text[content_start:end].strip())
            cursor = end + len(_PLAN_CLOSE_TAG)

    def walk(item: Any) -> None:
        if isinstance(item, str):
            for block in plan_blocks(item):
                text = _normalize_plan_text(block)
                if _is_valid_plan_text(text):
                    blocks.append(text)
            return
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
            return
        if isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return blocks


def list_plan_drafts(repo_root: Path, *, limit: int = 20, include_inactive: bool = False) -> list[PlanDraft]:
    payload = _read_store(repo_root)
    drafts = [
        draft
        for item in payload.get("drafts", [])
        if isinstance(item, dict)
        for draft in [PlanDraft(**item)]
        if _is_valid_stored_draft(draft)
        if include_inactive or _is_active_plan_draft(draft)
    ]
    drafts.sort(key=_draft_rank_key, reverse=True)
    return drafts[: max(1, min(100, int(limit)))]


def latest_plan_draft(repo_root: Path) -> PlanDraft | None:
    drafts = list_plan_drafts(repo_root, limit=1)
    return drafts[0] if drafts else None


def record_plan_draft(
    repo_root: Path,
    *,
    text: str,
    source_client: str = "unknown",
    session_id: str = "",
    turn_id: str = "",
    workspace_id: str = "",
    source_event_id: str = "",
    now: float | None = None,
) -> PlanDraft:
    ts = time.time() if now is None else float(now)
    clean_text = str(sanitize_no_absolute_paths(_normalize_plan_text(text)))
    plan_id = (
        "plan_" + hashlib.sha256(f"{source_client}\n{session_id}\n{turn_id}\n{source_event_id}\n{clean_text}".encode()).hexdigest()[:16]
    )
    title = _title_from_plan(clean_text)
    tasks = _tasks_from_plan(clean_text)
    summary = _summary_from_plan(clean_text, title=title, tasks=tasks)
    prep_dir = _prep_dir(repo_root, plan_id)
    prep_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(prep_dir / "plan.md", clean_text + "\n")
    _atomic_write_text(
        prep_dir / "summary.md",
        "\n".join(
            [
                f"# {title}",
                "",
                summary,
                "",
                "## Prepared Area",
                "",
                "Vaner may prepare evidence, candidate patches, and checks here. The real workspace is not modified by this draft.",
            ]
        )
        + "\n",
    )
    payload = _read_store(repo_root)
    existing = [item for item in payload.get("drafts", []) if isinstance(item, dict) and item.get("id") != plan_id]
    draft = PlanDraft(
        id=plan_id,
        title=title,
        summary=summary,
        tasks=tasks,
        source_client=source_client,
        session_id=session_id,
        turn_id=turn_id,
        workspace_id=workspace_id,
        source_event_id=source_event_id,
        created_at=ts,
        updated_at=ts,
        prep_dir=str(prep_dir.relative_to(repo_root)),
    )
    existing.append(asdict(draft))
    existing.sort(key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
    payload["drafts"] = existing[:100]
    _atomic_write_json(_store_path(repo_root), payload)
    return draft


def update_plan_draft_status(
    repo_root: Path,
    plan_id: str,
    *,
    status: str,
    accepted: bool | None = None,
    now: float | None = None,
) -> PlanDraft | None:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in PLAN_DRAFT_STATUSES:
        raise ValueError(f"status must be one of {', '.join(sorted(PLAN_DRAFT_STATUSES))}")
    ts = time.time() if now is None else float(now)
    payload = _read_store(repo_root)
    updated: PlanDraft | None = None
    next_drafts: list[dict[str, Any]] = []
    for item in payload.get("drafts", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") == plan_id:
            item = dict(item)
            item["status"] = normalized_status
            item["accepted"] = bool(accepted) if accepted is not None else bool(item.get("accepted"))
            item["updated_at"] = ts
            updated = PlanDraft(**item)
        next_drafts.append(item)
    if updated is None:
        return None
    payload["drafts"] = next_drafts
    _atomic_write_json(_store_path(repo_root), payload)
    return updated


def plan_draft_to_public(draft: PlanDraft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "title": draft.title,
        "summary": draft.summary,
        "tasks": list(draft.tasks),
        "source_client": draft.source_client,
        "session_id": draft.session_id,
        "turn_id": draft.turn_id,
        "workspace_id": draft.workspace_id,
        "source_event_id": draft.source_event_id,
        "status": draft.status,
        "accepted": draft.accepted,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
        "prep_dir": draft.prep_dir,
    }


def _normalize_plan_text(text: str) -> str:
    value = str(text or "").strip()
    for _ in range(2):
        if "\\n" in value and "\n" not in value:
            value = value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    value = "\n".join(line.rstrip("\\").rstrip() for line in value.splitlines())
    return value.strip()


def _is_valid_plan_text(text: str) -> bool:
    """Reject accidental regex/code matches while keeping normal draft plans."""

    normalized = _normalize_plan_text(text)
    if len(normalized) < 12:
        return False
    if re.fullmatch(r"[\s\\().*?+|^${}\[\]-]+", normalized):
        return False
    if "\\s" in normalized or "(.*?)" in normalized:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", normalized)
    if len(words) < 3:
        return False
    lower = normalized.lower()
    has_plan_shape = bool(re.search(r"(?m)^\s*(?:#{1,3}\s+|\s*(?:[-*]|\d+[.)])\s+)", normalized))
    has_plan_language = any(term in lower for term in ("plan", "todo", "implement", "fix", "test", "verify", "ship", "prepare"))
    return has_plan_shape or has_plan_language


def _is_valid_stored_draft(draft: PlanDraft) -> bool:
    raw_fields = [draft.title, draft.summary, *draft.tasks]
    if any(str(field).rstrip().endswith("\\") for field in raw_fields):
        return False
    value = "\n".join(raw_fields)
    return _is_valid_plan_text(value)


def _is_active_plan_draft(draft: PlanDraft) -> bool:
    status = str(draft.status or "draft").strip().lower()
    if status not in ACTIVE_PLAN_DRAFT_STATUSES:
        return False
    return _has_actionable_plan_content(draft)


def _has_actionable_plan_content(draft: PlanDraft) -> bool:
    title = str(draft.title or "").strip().lower()
    tasks = [str(task or "").strip() for task in draft.tasks if str(task or "").strip()]
    if len(tasks) >= 2:
        return True
    text = " ".join([title, *tasks]).lower()
    tokens = re.findall(r"[a-z][a-z0-9_-]{2,}", text)
    concrete_tokens = {token for token in tokens if token not in _GENERIC_PLAN_TOKENS}
    if title in _GENERIC_PLAN_TITLES and len(concrete_tokens) < 2:
        return False
    return bool(concrete_tokens)


def _draft_rank_key(draft: PlanDraft) -> tuple[int, float]:
    """Prefer actionable draft plans over tiny incidental captures."""

    task_count = min(12, len(draft.tasks))
    title_words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", draft.title)
    summary_words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", draft.summary)
    structure_score = task_count * 10 + min(10, len(set(title_words + summary_words)))
    return (structure_score, float(draft.updated_at or draft.created_at or 0.0))


def _title_from_plan(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return str(sanitize_no_absolute_paths(title[:120]))
    for line in text.splitlines():
        stripped = _LINE_MARKER_RE.sub("", line).strip()
        if stripped and not stripped.startswith("<"):
            return str(sanitize_no_absolute_paths(stripped[:120]))
    return "Draft plan"


def _tasks_from_plan(text: str) -> list[str]:
    tasks: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not _LINE_MARKER_RE.match(stripped):
            continue
        task = _LINE_MARKER_RE.sub("", stripped).strip()
        if not task or len(task) < 4:
            continue
        tasks.append(str(sanitize_no_absolute_paths(task[:180])))
        if len(tasks) >= 12:
            break
    return tasks


def _summary_from_plan(text: str, *, title: str, tasks: list[str]) -> str:
    if tasks:
        return f"{title}: {len(tasks)} planned step{'s' if len(tasks) != 1 else ''} ready for shadow preparation."
    body = " ".join(line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))
    if body:
        return str(sanitize_no_absolute_paths(body[:220]))
    return "Vaner captured a draft plan and can prepare against it locally."


def _store_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "runtime" / "plan_drafts.json"


def _prep_dir(repo_root: Path, plan_id: str) -> Path:
    return repo_root / ".vaner" / "runtime" / "prep" / plan_id


def _read_store(repo_root: Path) -> dict[str, Any]:
    path = _store_path(repo_root)
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {"drafts": []}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"drafts": []}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
