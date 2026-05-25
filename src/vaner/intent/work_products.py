# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from vaner.daemon.signals.git_reader import read_content_hashes, read_git_state, read_head_sha
from vaner.external_state.models import ExternalStateSnapshot
from vaner.models.artefact import Artefact
from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductEvidenceRef,
    WorkProductExternalInput,
    WorkProductFreshness,
    WorkProductSelfEval,
    WorkProductSensitivity,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)
from vaner.policy.privacy import sanitize_no_absolute_paths

_CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".cs",
    ".rb",
}
_DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
_WORK_PRODUCT_VERSION = "work_product.v1"
_FINANCE_TERMS = {
    "account",
    "assignment",
    "balance",
    "delta",
    "earnings",
    "expiry",
    "greek",
    "hedge",
    "iv",
    "option",
    "order",
    "portfolio",
    "position",
    "premium",
    "screen",
    "spread",
    "strike",
    "theta",
    "ticker",
    "underlying",
    "vega",
    "volatility",
    "watchlist",
}


def _stable_id(kind: WorkProductType, target_key: str, body: str) -> str:
    digest = hashlib.sha1(f"{kind.value}\n{target_key}\n{body}".encode()).hexdigest()[:16]  # noqa: S324
    return f"wp-{kind.value}-{digest}"


def _project_id(repo_root: Path) -> str:
    try:
        resolved = str(repo_root.resolve())
    except OSError:
        resolved = str(repo_root)
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:12]  # noqa: S324


def _safe_relpath(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("/")


def build_source_snapshot(
    repo_root: Path,
    paths: list[str],
    *,
    generated_at: float | None = None,
    generator_version: str = _WORK_PRODUCT_VERSION,
    config_id: str = "default",
) -> WorkProductSourceSnapshot:
    rel_paths = sorted({_safe_relpath(path) for path in paths if _safe_relpath(path)})
    return WorkProductSourceSnapshot(
        project_id=_project_id(repo_root),
        relative_paths=rel_paths,
        file_hashes=read_content_hashes(repo_root, rel_paths),
        base_commit=read_head_sha(repo_root) or None,
        generated_at=float(generated_at if generated_at is not None else time.time()),
        generator_version=generator_version,
        config_id=config_id,
    )


def _self_eval(
    *,
    evidence_count: int,
    confidence: float,
    stale_risk: float,
    contradiction_risk: float = 0.05,
    reason: str,
) -> WorkProductSelfEval:
    evidence_coverage = min(1.0, 0.35 + evidence_count * 0.25)
    groundedness = min(1.0, 0.45 + evidence_count * 0.2)
    return WorkProductSelfEval(
        evidence_coverage=round(evidence_coverage, 3),
        groundedness=round(groundedness, 3),
        contradiction_risk=round(max(0.0, min(1.0, contradiction_risk)), 3),
        stale_risk=round(max(0.0, min(1.0, stale_risk)), 3),
        goal_alignment=round(max(0.0, min(1.0, confidence)), 3),
        reason=reason,
    )


def _make_product(
    *,
    repo_root: Path,
    kind: WorkProductType,
    title: str,
    summary: str,
    body: str,
    paths: list[str],
    evidence_reason: str,
    confidence: float,
    adoptability: WorkProductAdoptability,
    target_key: str,
    status: WorkProductStatus = WorkProductStatus.SURFACED,
    created_at: float | None = None,
    provenance: dict[str, Any] | None = None,
) -> WorkProduct:
    now = float(created_at if created_at is not None else time.time())
    evidence_refs = [
        WorkProductEvidenceRef(kind="file", path=path, reason=evidence_reason, confidence=confidence)
        for path in sorted({_safe_relpath(path) for path in paths if _safe_relpath(path)})
    ]
    clean_body = str(sanitize_no_absolute_paths(body))
    product = WorkProduct(
        id=_stable_id(kind, target_key, clean_body),
        type=kind,
        title=str(sanitize_no_absolute_paths(title)),
        summary=str(sanitize_no_absolute_paths(summary)),
        body=clean_body,
        evidence_refs=evidence_refs,
        source_snapshot=build_source_snapshot(repo_root, paths, generated_at=now),
        confidence=confidence,
        freshness=WorkProductFreshness.FRESH,
        expires_at=now + 7 * 24 * 60 * 60,
        status=status,
        adoptability=adoptability,
        provenance={"generator": _WORK_PRODUCT_VERSION, **(provenance or {})},
        self_eval=_self_eval(
            evidence_count=len(evidence_refs),
            confidence=confidence,
            stale_risk=0.1 if kind == WorkProductType.VIRTUAL_DIFF else 0.2,
            reason="deterministic v1 self-eval gate",
        ),
        feedback_state="none",
        created_at=now,
        updated_at=now,
        target_key=target_key,
    )
    return product


def _candidate_paths(repo_root: Path, artefacts: list[Artefact], git_state: dict[str, str]) -> list[str]:
    paths: list[str] = []
    for value in (git_state.get("staged", ""), git_state.get("recent_diff", "")):
        paths.extend(line.strip() for line in value.splitlines() if line.strip())
    paths.extend(str(artefact.source_path) for artefact in artefacts[:20] if artefact.source_path)
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = _safe_relpath(raw)
        if not path or path in seen:
            continue
        if path.startswith(".vaner/") or "/.vaner/" in path:
            continue
        abs_path = repo_root / path
        if not abs_path.exists() or not abs_path.is_file():
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _read_text(repo_root: Path, path: str, *, max_chars: int = 24000) -> str:
    try:
        data = (repo_root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return data[:max_chars]


def _line_number(text: str, needle: str) -> int:
    for idx, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return idx
    return 1


def _generate_virtual_diff(repo_root: Path, paths: list[str]) -> WorkProduct | None:
    for path in paths:
        if Path(path).suffix.lower() not in _CODE_SUFFIXES | _DOC_SUFFIXES:
            continue
        text = _read_text(repo_root, path, max_chars=20000)
        if not text:
            continue
        original = text.splitlines(keepends=True)
        patched_text = text
        reason = ""
        if not text.endswith("\n"):
            patched_text = f"{text}\n"
            reason = "adds the missing trailing newline so tooling sees a complete final line"
        elif any(line.rstrip("\n").rstrip("\r").endswith((" ", "\t")) for line in original):
            patched_text = "".join(
                line.rstrip("\n").rstrip("\r").rstrip(" \t") + ("\n" if line.endswith(("\n", "\r")) else "") for line in original
            )
            reason = "removes trailing whitespace without changing program structure"
        if not reason or patched_text == text:
            continue
        patched = patched_text.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                original,
                patched,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        diff_text = "\n".join(diff_lines)
        if not diff_text or len(diff_lines) > 80 or len(diff_text) > 6000:
            continue
        body = f"Rationale: {reason}.\n\nTarget: `{path}`\n\n```diff\n{diff_text}\n```"
        return _make_product(
            repo_root=repo_root,
            kind=WorkProductType.VIRTUAL_DIFF,
            title=f"Small virtual diff for {path}",
            summary=reason,
            body=body,
            paths=[path],
            evidence_reason="source snapshot captured before building virtual diff",
            confidence=0.82,
            adoptability=WorkProductAdoptability.EXPORTABLE,
            target_key=f"virtual_diff:{path}:mechanical-cleanup",
        )
    return None


def _generate_bug_hypothesis(repo_root: Path, paths: list[str]) -> WorkProduct | None:
    patterns = [
        (re.compile(r"except\s+Exception\s*:\s*\n\s*pass\b"), "broad exception handler silently discards failures"),
        (re.compile(r"TODO|FIXME", re.IGNORECASE), "open TODO/FIXME marker may indicate unfinished behavior"),
        (re.compile(r"return\s+None\s*(?:#.*)?$", re.MULTILINE), "explicit None return may need caller-side handling"),
    ]
    for path in paths:
        if Path(path).suffix.lower() not in _CODE_SUFFIXES:
            continue
        text = _read_text(repo_root, path)
        if not text:
            continue
        for pattern, reason in patterns:
            match = pattern.search(text)
            if match is None:
                continue
            line = _line_number(text, match.group(0).splitlines()[0])
            body = (
                f"`{path}:{line}` has a possible risk: {reason}.\n\nThis is advisory until inspected against the surrounding control flow."
            )
            return _make_product(
                repo_root=repo_root,
                kind=WorkProductType.BUG_HYPOTHESIS,
                title=f"Possible issue in {path}",
                summary=reason,
                body=body,
                paths=[path],
                evidence_reason="pattern observed in source file",
                confidence=0.58,
                adoptability=WorkProductAdoptability.ADVISORY,
                target_key=f"bug_hypothesis:{path}:{line}:{reason}",
            )
    return None


def _generate_review_note(repo_root: Path, paths: list[str]) -> WorkProduct | None:
    code_paths = [path for path in paths if Path(path).suffix.lower() in _CODE_SUFFIXES]
    if not code_paths:
        return None
    selected = code_paths[:3]
    body = (
        "Prepared review focus:\n\n"
        + "\n".join(f"- Inspect `{path}` for API behavior, error handling, and tests tied to the current change set." for path in selected)
        + "\n\nThis note is grounded in recent or high-signal source files and is meant to focus the next review pass."
    )
    return _make_product(
        repo_root=repo_root,
        kind=WorkProductType.REVIEW_NOTE,
        title="Prepared code review focus",
        summary=f"Review focus for {', '.join(selected[:2])}",
        body=body,
        paths=selected,
        evidence_reason="recent source path selected for review preparation",
        confidence=0.72,
        adoptability=WorkProductAdoptability.INSPECTABLE,
        target_key="review_note:" + ",".join(selected),
    )


def _generate_docs_drift(repo_root: Path, paths: list[str]) -> WorkProduct | None:
    code_paths = [path for path in paths if Path(path).suffix.lower() in _CODE_SUFFIXES]
    if not code_paths:
        return None
    doc_paths = [path for path in paths if Path(path).suffix.lower() in _DOC_SUFFIXES]
    readme_exists = any((repo_root / candidate).exists() for candidate in ("README.md", "docs", "doc"))
    if doc_paths or not readme_exists:
        return None
    selected = code_paths[:2]
    body = (
        "Potential docs drift: recent code evidence exists without a nearby docs artifact in the prepared set.\n\n"
        + "\n".join(f"- `{path}`" for path in selected)
        + "\n\nInspect before acting; v1 does not infer the exact doc change."
    )
    return _make_product(
        repo_root=repo_root,
        kind=WorkProductType.DOCS_DRIFT,
        title="Potential docs drift",
        summary="Code evidence may need matching docs review.",
        body=body,
        paths=selected,
        evidence_reason="code path appeared without nearby doc evidence",
        confidence=0.54,
        adoptability=WorkProductAdoptability.ADVISORY,
        target_key="docs_drift:" + ",".join(selected),
    )


def _generate_research_brief(repo_root: Path, recent_queries: list[str], artefacts: list[Artefact]) -> WorkProduct | None:
    research_terms = {"research", "compare", "benchmark", "evaluate", "study", "evidence", "source"}
    query_text = " ".join(recent_queries[-5:]).lower()
    if not query_text or not any(term in query_text for term in research_terms):
        return None
    doc_artefacts = [artefact for artefact in artefacts if Path(str(artefact.source_path)).suffix.lower() in _DOC_SUFFIXES]
    if not doc_artefacts:
        return None
    selected = doc_artefacts[:3]
    bullets: list[str] = []
    for artefact in selected:
        excerpt = " ".join(str(artefact.content).split())[:260]
        bullets.append(f"- `{artefact.source_path}`: {excerpt}")
    body = (
        "Source-backed research brief tied to recent intent.\n\n"
        + "\n".join(bullets)
        + "\n\nFreshness note: this brief uses local prepared sources only; external claims still need live-source verification."
    )
    paths = [str(artefact.source_path) for artefact in selected]
    return _make_product(
        repo_root=repo_root,
        kind=WorkProductType.RESEARCH_BRIEF,
        title="Prepared research brief",
        summary="Cited local sources for the likely research follow-up.",
        body=body,
        paths=paths,
        evidence_reason="source-backed local document evidence",
        confidence=0.7,
        adoptability=WorkProductAdoptability.EXPORTABLE,
        target_key="research_brief:" + hashlib.sha1(query_text.encode("utf-8")).hexdigest()[:12],  # noqa: S324
    )


def _finance_signal(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _FINANCE_TERMS)


def _finance_sensitivity(text: str) -> WorkProductSensitivity:
    lowered = text.lower()
    if any(term in lowered for term in ("order", "fill", "activity", "cancel", "modify")):
        return WorkProductSensitivity.ORDER_ACTIVITY
    if any(term in lowered for term in ("position", "holding", "assignment", "short leg", "long leg")):
        return WorkProductSensitivity.POSITION_SPECIFIC
    if any(term in lowered for term in ("account", "balance", "margin", "cash", "portfolio")):
        return WorkProductSensitivity.ACCOUNT_SUMMARY
    if "watchlist" in lowered:
        return WorkProductSensitivity.USER_WATCHLIST
    return WorkProductSensitivity.PUBLIC_MARKET_ONLY


def _finance_kind(query_text: str, evidence_text: str) -> WorkProductType:
    lowered = f"{query_text}\n{evidence_text}".lower()
    if any(term in lowered for term in ("position", "portfolio", "balance", "order", "assignment")):
        return WorkProductType.FINANCE_POSITION_BRIEF
    if any(term in lowered for term in ("option", "spread", "strike", "expiry", "delta", "theta", "vega", "iv")):
        return WorkProductType.FINANCE_OPTION_STRATEGY_PLAN
    if any(term in lowered for term in ("screen", "watchlist", "candidate")):
        return WorkProductType.FINANCE_SCREENING_RESULT
    if any(term in lowered for term in ("trade", "entry", "exit", "hedge", "rebalance")):
        return WorkProductType.FINANCE_TRADE_BRIEF
    return WorkProductType.FINANCE_MORNING_BRIEF


def _decode_external_payload(payload: dict[str, Any]) -> Any:
    content = payload.get("content")
    if isinstance(content, list):
        decoded: list[Any] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else None
            if not isinstance(text, str) or not text.strip():
                continue
            try:
                decoded.append(json.loads(text))
            except json.JSONDecodeError:
                decoded.append({"text": text})
        if decoded:
            return decoded[0] if len(decoded) == 1 else decoded
    return payload


def _candidate_lists(value: Any, *, depth: int = 0) -> list[list[dict[str, Any]]]:
    if depth > 4:
        return []
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return [list(value)]
        lists: list[list[dict[str, Any]]] = []
        for item in value:
            lists.extend(_candidate_lists(item, depth=depth + 1))
        return lists
    if not isinstance(value, dict):
        return []
    preferred_keys = (
        "candidates",
        "results",
        "items",
        "instruments",
        "securities",
        "strategies",
        "quotes",
        "data",
        "rows",
    )
    lists = []
    for key in preferred_keys:
        if key in value:
            lists.extend(_candidate_lists(value[key], depth=depth + 1))
    if lists:
        return lists
    for nested in value.values():
        lists.extend(_candidate_lists(nested, depth=depth + 1))
    return lists


def _field_value(item: dict[str, Any], names: tuple[str, ...]) -> Any:
    lowered = {str(key).lower(): value for key, value in item.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    for value in item.values():
        if isinstance(value, dict):
            nested = _field_value(value, names)
            if nested not in (None, ""):
                return nested
    return None


def _candidate_label(item: dict[str, Any]) -> str:
    value = _field_value(
        item,
        (
            "symbol",
            "ticker",
            "displaySymbol",
            "identifier",
            "uic",
            "name",
            "description",
            "instrument",
        ),
    )
    if isinstance(value, dict):
        value = _field_value(value, ("symbol", "ticker", "name", "description", "uic"))
    label = str(value or "candidate").strip()
    return sanitize_no_absolute_paths(label[:80])


def _score_finance_candidate(item: dict[str, Any], query_terms: set[str], *, snapshot_stale: bool) -> tuple[float, list[str]]:
    label = _candidate_label(item).lower()
    present_fields = 0
    field_groups: tuple[tuple[str, ...], ...] = (
        ("symbol", "ticker", "uic", "identifier"),
        ("name", "description"),
        ("price", "lastPrice", "last", "close"),
        ("change", "changePercent", "percentChange"),
        ("volume", "turnover"),
        ("score", "rank", "rankScore"),
    )
    for group in field_groups:
        if _field_value(item, group) not in (None, ""):
            present_fields += 1
    relevance_hits = sum(1 for term in query_terms if len(term) > 2 and term in label)
    score = 0.25 + min(0.4, present_fields * 0.075) + min(0.2, relevance_hits * 0.08)
    reasons = ["complete-enough evidence"] if present_fields >= 3 else ["thin evidence"]
    if relevance_hits:
        reasons.append("matches recent finance intent")
    if not snapshot_stale:
        score += 0.1
        reasons.append("fresh snapshot")
    else:
        score -= 0.2
        reasons.append("stale snapshot")
    return (max(0.0, min(1.0, score)), reasons)


def _finance_exploration_summary(snapshots: list[ExternalStateSnapshot], query_text: str) -> dict[str, Any]:
    query_terms = set(re.findall(r"[a-zA-Z0-9_.$-]+", query_text.lower()))
    candidate_rows: list[tuple[float, str, str, list[str]]] = []
    for snapshot in snapshots:
        payload = _decode_external_payload(snapshot.payload)
        snapshot_stale = snapshot.is_stale()
        for candidate_list in _candidate_lists(payload):
            for item in candidate_list[:100]:
                score, reasons = _score_finance_candidate(item, query_terms, snapshot_stale=snapshot_stale)
                candidate_rows.append((score, snapshot.capability, _candidate_label(item), reasons))
    candidate_rows.sort(key=lambda row: row[0], reverse=True)
    kept = candidate_rows[:5]
    pruning_rules = [
        "prefer fresh snapshots over stale branches",
        "prefer candidates with enough comparable market fields",
        "prefer candidates aligned with recent user finance intent",
        "prune low-evidence branches before requesting deeper provider data",
    ]
    return {
        "objective": "risk_adjusted_preparation",
        "candidate_count": len(candidate_rows),
        "kept_candidate_count": len(kept),
        "pruned_candidate_count": max(0, len(candidate_rows) - len(kept)),
        "kept_candidates": [
            {"label": label, "score": round(score, 3), "capability": capability, "reasons": reasons}
            for score, capability, label, reasons in kept
        ],
        "pruning_rules": pruning_rules,
        "abstentions": [] if candidate_rows else ["external snapshot did not expose a comparable candidate list"],
    }


def _generate_finance_brief(repo_root: Path, recent_queries: list[str], artefacts: list[Artefact]) -> WorkProduct | None:
    query_text = " ".join(recent_queries[-5:])
    finance_artefacts = [
        artefact
        for artefact in artefacts
        if Path(str(artefact.source_path)).suffix.lower() in _DOC_SUFFIXES
        and _finance_signal(f"{artefact.source_path}\n{artefact.content}")
    ]
    if not finance_artefacts and not _finance_signal(query_text):
        return None
    selected = finance_artefacts[:3]
    evidence_text = "\n".join(str(artefact.content) for artefact in selected)
    if not selected:
        return None
    paths = [str(artefact.source_path) for artefact in selected]
    sensitivity = _finance_sensitivity(f"{query_text}\n{evidence_text}")
    kind = _finance_kind(query_text, evidence_text)
    bullets: list[str] = []
    for artefact in selected:
        excerpt = " ".join(str(artefact.content).split())[:260]
        bullets.append(f"- `{artefact.source_path}`: {excerpt}")
    body = (
        "Finance preparation from local workspace evidence only.\n\n"
        + "\n".join(bullets)
        + "\n\nFreshness note: no external market/account snapshot was used. "
        "Treat this as planning context, not an execution-ready recommendation."
    )
    target_digest = hashlib.sha1(f"{kind.value}\n{query_text}\n{','.join(paths)}".encode()).hexdigest()[:12]  # noqa: S324
    product = _make_product(
        repo_root=repo_root,
        kind=kind,
        title="Prepared finance brief",
        summary="Local finance notes prepared without external market or account data.",
        body=body,
        paths=paths,
        evidence_reason="local finance evidence; external data access was not required",
        confidence=0.62 if query_text else 0.55,
        adoptability=WorkProductAdoptability.ADVISORY,
        target_key=f"{kind.value}:local:{target_digest}",
        provenance={
            "finance": {
                "external_state_enabled": False,
                "abstention_or_downgrade": "local_only_no_external_snapshot",
            }
        },
    )
    product.sensitivity_class = sensitivity
    product.fresh_precheck_required = False
    product.stale_after = product.expires_at
    product.prohibited_actions = ["execution"]
    product.self_eval.stale_risk = max(product.self_eval.stale_risk, 0.35)
    return product


def generate_external_finance_work_products(
    *,
    repo_root: Path,
    recent_queries: list[str],
    snapshots: list[ExternalStateSnapshot],
    max_products: int = 2,
) -> list[WorkProduct]:
    if not snapshots:
        return []
    now = time.time()
    strict_expiry = min((snapshot.expires_at for snapshot in snapshots if snapshot.expires_at is not None), default=now + 300)
    sensitivity = WorkProductSensitivity.PUBLIC_MARKET_ONLY
    if any(snapshot.sensitivity_class.value in {"position_specific", "order_activity"} for snapshot in snapshots):
        sensitivity = WorkProductSensitivity.POSITION_SPECIFIC
    elif any(snapshot.sensitivity_class.value == "account_summary" for snapshot in snapshots):
        sensitivity = WorkProductSensitivity.ACCOUNT_SUMMARY
    query_text = " ".join(recent_queries[-5:])
    capabilities = ", ".join(sorted({snapshot.capability for snapshot in snapshots}))
    exploration = _finance_exploration_summary(snapshots, query_text)
    candidate_lines = ""
    if exploration["kept_candidates"]:
        bullets = [
            f"- {item['label']} ({item['capability']}, score {item['score']}): {', '.join(item['reasons'])}"
            for item in exploration["kept_candidates"]
        ]
        candidate_lines = "\n\nCandidate branches kept for review:\n" + "\n".join(bullets)
    else:
        candidate_lines = (
            "\n\nNo comparable candidate list was available from the external snapshots, "
            "so Vaner abstained from candidate-level ranking."
        )
    body = (
        "Finance preparation from fresh external-state snapshots.\n\n"
        f"Capabilities observed: {capabilities}.\n\n"
        "Exploration summary: "
        f"evaluated {exploration['candidate_count']} candidate records, "
        f"kept {exploration['kept_candidate_count']}, "
        f"pruned {exploration['pruned_candidate_count']} lower-evidence branches."
        f"{candidate_lines}\n\n"
        "Pruning rules: "
        + "; ".join(exploration["pruning_rules"])
        + ".\n\n"
        "This prepared work is advisory. It ranks branches for risk-adjusted preparation, summarizes read-only provider data, "
        "and requires a fresh precheck before any action. It is not execution guidance."
    )
    kind = (
        WorkProductType.FINANCE_POSITION_BRIEF
        if sensitivity in {WorkProductSensitivity.POSITION_SPECIFIC, WorkProductSensitivity.ACCOUNT_SUMMARY}
        else WorkProductType.FINANCE_SCREENING_RESULT
    )
    target_digest = hashlib.sha1(  # noqa: S324
        f"{kind.value}\n{query_text}\n{','.join(snapshot.id for snapshot in snapshots)}".encode()
    ).hexdigest()[:12]
    product = WorkProduct(
        id=_stable_id(kind, f"{kind.value}:external:{target_digest}", body),
        type=kind,
        title="Prepared finance snapshot brief",
        summary="Read-only external finance snapshots prepared for inspection.",
        body=body,
        evidence_refs=[
            WorkProductEvidenceRef(
                kind="record",
                reason=f"external-state snapshot for {snapshot.capability}",
                confidence=0.72,
            )
            for snapshot in snapshots
        ],
        source_snapshot=build_source_snapshot(
            repo_root,
            [],
            generated_at=now,
            generator_version=f"{_WORK_PRODUCT_VERSION}.external_finance",
        ),
        confidence=0.72,
        freshness=WorkProductFreshness.FRESH,
        expires_at=strict_expiry,
        status=WorkProductStatus.SURFACED,
        adoptability=WorkProductAdoptability.ADVISORY,
        provenance={
            "generator": f"{_WORK_PRODUCT_VERSION}.external_finance",
            "finance": {
                "fresh_precheck_required": True,
                "external_state_enabled": True,
                "snapshot_count": len(snapshots),
                "exploration": exploration,
            },
        },
        self_eval=_self_eval(
            evidence_count=len(snapshots),
            confidence=0.72,
            stale_risk=0.45,
            contradiction_risk=0.12,
            reason="external finance snapshots decay and remain advisory",
        ),
        feedback_state="none",
        sensitivity_class=sensitivity,
        fresh_precheck_required=True,
        external_inputs=[
            WorkProductExternalInput(
                provider_id=snapshot.provider_id,
                capability=snapshot.capability,
                snapshot_id=snapshot.id,
                freshness_class=snapshot.freshness_class.value,
                captured_at=snapshot.captured_at,
                expires_at=snapshot.expires_at,
                payload_fingerprint=snapshot.payload_fingerprint,
            )
            for snapshot in snapshots
        ],
        stale_after=strict_expiry,
        prohibited_actions=["execution"],
        created_at=now,
        updated_at=now,
        target_key=f"{kind.value}:external:{target_digest}",
    )
    return [product][: max(1, int(max_products))]


def generate_work_products(
    *,
    repo_root: Path,
    recent_queries: list[str],
    artefacts: list[Artefact],
    max_products: int = 4,
) -> list[WorkProduct]:
    git_state = read_git_state(repo_root)
    paths = _candidate_paths(repo_root, artefacts, git_state)
    products: list[WorkProduct] = []
    for builder in (
        _generate_virtual_diff,
        _generate_review_note,
        _generate_bug_hypothesis,
        _generate_docs_drift,
    ):
        try:
            product = builder(repo_root, paths)
        except Exception:
            product = None
        if product is not None:
            products.append(product)
        if len(products) >= max_products:
            return products
    try:
        research = _generate_research_brief(repo_root, recent_queries, artefacts)
    except Exception:
        research = None
    if research is not None:
        products.append(research)
    if len(products) < max_products:
        try:
            finance = _generate_finance_brief(repo_root, recent_queries, artefacts)
        except Exception:
            finance = None
        if finance is not None:
            products.append(finance)
    return products[:max_products]
