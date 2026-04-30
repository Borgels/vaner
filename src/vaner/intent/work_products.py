# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import difflib
import hashlib
import re
import time
from pathlib import Path
from typing import Any

from vaner.daemon.signals.git_reader import read_content_hashes, read_git_state, read_head_sha
from vaner.models.artefact import Artefact
from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductEvidenceRef,
    WorkProductFreshness,
    WorkProductSelfEval,
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


def _stable_id(kind: WorkProductType, target_key: str, body: str) -> str:
    digest = hashlib.sha1(f"{kind.value}\n{target_key}\n{body}".encode("utf-8")).hexdigest()[:16]  # noqa: S324
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
            patched_text = "".join(line.rstrip("\n").rstrip("\r").rstrip(" \t") + ("\n" if line.endswith(("\n", "\r")) else "") for line in original)
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
        body = (
            f"Rationale: {reason}.\n\n"
            f"Target: `{path}`\n\n"
            "```diff\n"
            f"{diff_text}\n"
            "```"
        )
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
                f"`{path}:{line}` has a possible risk: {reason}.\n\n"
                "This is advisory until inspected against the surrounding control flow."
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
    return products[:max_products]
