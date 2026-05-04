# SPDX-License-Identifier: Apache-2.0
"""Generic next-prompt horizon generation.

The horizon source predicts families of likely next user turns from workflow
stage, recent intent, workspace shape, and available evidence. It is deliberately
domain-neutral: coding repositories get code-shaped evidence targets, but the
families also apply to research, writing, and operations work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_v2 import structured_from_prediction_fields
from vaner.intent.taxonomy import classify_taxonomy


@dataclass(frozen=True, slots=True)
class HorizonFamily:
    key: str
    label: str
    description: str
    base_confidence: float
    action_hint: str
    keywords: tuple[str, ...]


_FAMILIES: tuple[HorizonFamily, ...] = (
    HorizonFamily(
        key="validate",
        label="Validate current work",
        description="Check the current work against expected behavior before moving on.",
        base_confidence=0.50,
        action_hint="test",
        keywords=("test", "spec", "check", "ci", "eval", "benchmark", "fixture", "verification"),
    ),
    HorizonFamily(
        key="review",
        label="Review quality and risks",
        description="Look for correctness, missing cases, tradeoffs, and trust risks in the current direction.",
        base_confidence=0.48,
        action_hint="review",
        keywords=("review", "audit", "risk", "quality", "policy", "contract", "decision"),
    ),
    HorizonFamily(
        key="harden",
        label="Harden edge cases and failure modes",
        description="Probe where the current work could fail under unusual inputs, constraints, or lifecycle changes.",
        base_confidence=0.45,
        action_hint="review",
        keywords=("error", "edge", "failure", "fallback", "privacy", "security", "timeout", "queue", "limit"),
    ),
    HorizonFamily(
        key="static_security",
        label="Run static and security checks",
        description="Prepare static analysis, dependency, credential, workflow, and security-audit evidence.",
        base_confidence=0.44,
        action_hint="review",
        keywords=(
            "codeql",
            "security",
            "audit",
            "scorecard",
            "creds",
            "credential",
            "lint",
            "ruff",
            "actionlint",
            "pre-commit",
            "threat",
        ),
    ),
    HorizonFamily(
        key="document",
        label="Document decisions and next steps",
        description="Capture what changed, why it matters, and what someone should do next.",
        base_confidence=0.42,
        action_hint="summarize",
        keywords=("docs", "readme", "guide", "notes", "summary", "changelog", "decision", "handoff"),
    ),
    HorizonFamily(
        key="handoff",
        label="Prepare commit and release handoff",
        description="Package the current work for commit, release, publication, or operational handoff.",
        base_confidence=0.40,
        action_hint="plan",
        keywords=("commit", "release", "publish", "deploy", "package", "workflow", "runbook", "changelog", "version", "preflight"),
    ),
    HorizonFamily(
        key="explore",
        label="Explore alternative next branches",
        description="Compare plausible next directions before committing more effort.",
        base_confidence=0.39,
        action_hint="compare",
        keywords=("plan", "roadmap", "architecture", "strategy", "scenario", "option", "alternative", "goal"),
    ),
    HorizonFamily(
        key="continue",
        label="Continue current direction",
        description="Carry the active thread forward using the most recent intent and workspace evidence.",
        base_confidence=0.44,
        action_hint="plan",
        keywords=("intent", "current", "next", "work", "plan", "goal", "thread"),
    ),
)

_DOMAIN_BOOSTS: dict[str, dict[str, float]] = {
    "coding": {"validate": 0.08, "review": 0.06, "harden": 0.07, "static_security": 0.09, "document": 0.03, "handoff": 0.06},
    "research": {"validate": 0.07, "review": 0.04, "document": 0.06, "explore": 0.06},
    "writing": {"review": 0.08, "document": 0.06, "continue": 0.06, "explore": 0.04},
    "ops": {"validate": 0.07, "harden": 0.08, "static_security": 0.06, "handoff": 0.07, "review": 0.04},
}

_STAGE_BOOSTS: dict[str, dict[str, float]] = {
    "planning": {"explore": 0.08, "continue": 0.06, "document": 0.03},
    "building": {"validate": 0.06, "review": 0.05, "harden": 0.04, "continue": 0.03},
    "validation": {"validate": 0.09, "review": 0.08, "harden": 0.07, "static_security": 0.08, "document": 0.04},
    "stabilizing": {"harden": 0.09, "static_security": 0.09, "review": 0.07, "handoff": 0.08, "document": 0.05},
    "discovery": {"explore": 0.07, "continue": 0.04, "document": 0.03},
    "exploring": {"explore": 0.06, "continue": 0.04},
}

_POST_PLAN_BOOSTS: dict[str, float] = {
    "validate": 0.10,
    "review": 0.09,
    "harden": 0.08,
    "static_security": 0.09,
    "document": 0.07,
    "handoff": 0.08,
}

_CODE_PATH_HINTS = (
    "src/",
    "tests/",
    "test/",
    ".github/",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "README",
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_/-]+")
_STOPWORDS = {"and", "for", "from", "into", "that", "the", "this", "with", "you", "your", "what", "where", "when"}


def build_next_horizon_specs(
    *,
    recent_queries: list[str],
    available_paths: list[str],
    changed_paths: list[str] | None = None,
    active_goals: list[dict[str, object]] | None = None,
    completed_plan_titles: list[str] | None = None,
    horizon_priors: dict[str, object] | None = None,
    workflow_phase: str = "",
    max_specs: int = 8,
) -> list[PredictionSpec]:
    """Return generic horizon predictions for plausible next user turns."""

    if max_specs <= 0:
        return []

    recent_text = " ".join(query.strip() for query in recent_queries[-5:] if query.strip())
    taxonomy = classify_taxonomy(recent_text)
    paths = _clean_paths(available_paths)
    changed = _clean_paths(changed_paths or [])
    domain = _infer_domain(taxonomy.domain, paths, recent_text)
    phase = (workflow_phase or _infer_phase(recent_text)).strip().lower() or "exploring"
    post_plan = bool(completed_plan_titles)
    goal_text = " ".join(str(goal.get("title") or goal.get("description") or "") for goal in active_goals or [])
    recent_terms = _terms(" ".join((recent_text, goal_text)))

    ranked: list[tuple[float, PredictionSpec]] = []
    for family in _FAMILIES:
        confidence = _family_confidence(
            family,
            domain=domain,
            phase=phase,
            post_plan=post_plan,
            changed_count=len(changed),
            recent_terms=recent_terms,
            priors=horizon_priors or {},
        )
        targets = _select_evidence_targets(
            family=family,
            domain=domain,
            paths=paths,
            changed_paths=changed,
            recent_terms=recent_terms,
            limit=8,
        )
        if targets:
            confidence += 0.03
        confidence = _clamp(confidence)
        anchor = _anchor_for(family, domain=domain, phase=phase, targets=targets)
        structured_description = f"{family.action_hint}: {family.description}"
        structured = structured_from_prediction_fields(
            label=family.label,
            description=structured_description,
            anchor=anchor,
            evidence_targets=tuple(targets),
            readiness_mode="evidence_ready",
            confidence=confidence,
            reason_codes=("horizon", family.key, f"domain:{domain}", f"stage:{phase}"),
        )
        spec = PredictionSpec(
            id=prediction_id("horizon", anchor, family.label),
            label=family.label,
            description=family.description,
            source="horizon",
            anchor=anchor,
            confidence=confidence,
            hypothesis_type="likely_next" if confidence >= 0.62 else "possible_branch",
            specificity="concrete" if targets else "anchor",
            structured=structured,
        )
        ranked.append((confidence, spec))

    ranked.sort(key=lambda item: (-item[0], item[1].label))
    return [spec for _confidence, spec in ranked[:max_specs]]


def _family_confidence(
    family: HorizonFamily,
    *,
    domain: str,
    phase: str,
    post_plan: bool,
    changed_count: int,
    recent_terms: set[str],
    priors: dict[str, object],
) -> float:
    confidence = family.base_confidence
    confidence += _DOMAIN_BOOSTS.get(domain, {}).get(family.key, 0.0)
    confidence += _STAGE_BOOSTS.get(phase, {}).get(family.key, 0.0)
    if post_plan:
        confidence += _POST_PLAN_BOOSTS.get(family.key, 0.0)
    if changed_count and family.key in {"validate", "review", "harden", "static_security", "document"}:
        confidence += 0.05
    if recent_terms & set(family.keywords):
        confidence += 0.04
    confidence += _nested_prior(priors, "domain_family_boosts", domain, family.key)
    confidence += _nested_prior(priors, "stage_family_boosts", phase, family.key)
    if post_plan:
        confidence += _flat_prior(priors, "post_plan_boosts", family.key)
    weight = _flat_prior(priors, "family_weights", family.key, default=1.0)
    return confidence * max(0.25, min(2.0, weight))


def _select_evidence_targets(
    *,
    family: HorizonFamily,
    domain: str,
    paths: list[str],
    changed_paths: list[str],
    recent_terms: set[str],
    limit: int,
) -> list[str]:
    if not paths:
        return []
    path_set = set(paths)
    ordered_candidates = [path for path in changed_paths if path in path_set] + [path for path in paths if path not in changed_paths]
    scored: list[tuple[float, str]] = []
    for index, path in enumerate(ordered_candidates):
        score = _path_score(path, family=family, domain=domain, recent_terms=recent_terms)
        if path in changed_paths:
            score += 1.5
        score -= index * 0.0001
        if score > 0:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [path for _score, path in scored[:limit]]


def _path_score(path: str, *, family: HorizonFamily, domain: str, recent_terms: set[str]) -> float:
    lower = path.lower()
    score = 0.0
    for keyword in family.keywords:
        if keyword.lower() in lower:
            score += 1.0
    for term in recent_terms:
        if len(term) >= 4 and term in lower:
            score += 0.35
    if domain == "coding" and any(hint.lower() in lower for hint in _CODE_PATH_HINTS):
        score += 0.35
    if family.key == "validate" and any(part in lower for part in ("test", "spec", ".github", "ci", "benchmark")):
        score += 1.0
        if "/fixtures/" in lower or "conformance-fixtures" in lower:
            score -= 0.7
    if family.key == "static_security" and any(
        part in lower
        for part in (
            "codeql",
            "security",
            "scorecard",
            "creds",
            "credential",
            "actionlint",
            "pre-commit",
            "fuzz",
            "threat-model",
            "internal-boundary",
        )
    ):
        score += 1.3
    if family.key == "document" and lower.endswith((".md", ".rst", ".txt")):
        score += 0.85
    if family.key == "handoff" and any(
        part in lower for part in ("commit", "release", "deploy", "workflow", "changelog", "readme", "package", "preflight", "semantic-pr")
    ):
        score += 0.9
    if family.key in {"review", "harden"} and any(part in lower for part in ("policy", "contract", "security", "test", "error")):
        score += 0.7
    return score


def _infer_domain(taxonomy_domain: str, paths: list[str], recent_text: str) -> str:
    lowered = " ".join(paths[:200]).lower()
    if any(hint.lower() in lowered for hint in _CODE_PATH_HINTS):
        return "coding"
    text = recent_text.lower()
    if any(word in text for word in ("draft", "copy", "essay", "article", "voice", "tone")):
        return "writing"
    if any(word in text for word in ("paper", "study", "dataset", "benchmark", "evidence")):
        return "research"
    if any(word in text for word in ("deploy", "incident", "runtime", "monitor", "ops")):
        return "ops"
    return taxonomy_domain if taxonomy_domain in {"coding", "research", "writing", "ops"} else "coding"


def _infer_phase(recent_text: str) -> str:
    text = recent_text.lower()
    if any(word in text for word in ("validate", "verify", "test", "review", "audit")):
        return "validation"
    if any(word in text for word in ("harden", "release", "ship", "stabil", "polish")):
        return "stabilizing"
    if any(word in text for word in ("implement", "build", "fix", "change", "add")):
        return "building"
    if any(word in text for word in ("plan", "design", "strategy", "roadmap")):
        return "planning"
    if any(word in text for word in ("explore", "understand", "investigate", "research")):
        return "discovery"
    return "exploring"


def _anchor_for(family: HorizonFamily, *, domain: str, phase: str, targets: list[str]) -> str:
    if targets:
        return f"{family.key}:{domain}:{phase}:{','.join(targets[:3])}"
    return f"{family.key}:{domain}:{phase}"


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in _TOKEN_RE.findall(text.lower()):
        for part in re.split(r"[/_-]+", raw):
            if len(part) >= 3 and part not in _STOPWORDS:
                terms.add(part)
    return terms


def _clean_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    clean: list[str] = []
    for path in paths:
        value = str(path).strip().replace("\\", "/")
        if not value or value in seen:
            continue
        seen.add(value)
        clean.append(value)
    return clean


def _clamp(value: float) -> float:
    return max(0.0, min(0.85, float(value)))


def _flat_prior(priors: dict[str, object], section: str, key: str, *, default: float = 0.0) -> float:
    values = priors.get(section)
    if not isinstance(values, dict):
        return default
    try:
        return max(-0.35, min(0.35, float(values.get(key, default))))
    except (TypeError, ValueError):
        return default


def _nested_prior(priors: dict[str, object], section: str, outer: str, inner: str) -> float:
    values = priors.get(section)
    if not isinstance(values, dict):
        return 0.0
    nested = values.get(outer)
    if not isinstance(nested, dict):
        return 0.0
    try:
        return max(-0.35, min(0.35, float(nested.get(inner, 0.0))))
    except (TypeError, ValueError):
        return 0.0
