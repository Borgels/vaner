# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.context_preparation import ContextPreparationProfile, ContextToolTrace


@dataclass(frozen=True)
class SchedulingEvidence:
    source_key: str
    path: str
    snippet: str
    score: float
    time_windows: list[str]
    confidence: float
    status: str = "candidate"


def build_scheduling_evidence(
    prompt: str,
    candidates: list[Artefact],
    profile: ContextPreparationProfile,
    *,
    max_sources: int = 24,
) -> tuple[Artefact | None, ContextToolTrace]:
    """Prepare compact meeting/time evidence from scheduling candidates."""

    trace = ContextToolTrace(tool="prepare_scheduling_evidence", input_count=len(candidates))
    if profile.need != "scheduling":
        trace.notes.append("skipped:not_scheduling")
        return None, trace
    evidence = [
        item
        for item in (
            _scheduling_evidence_for(prompt, artefact, profile)
            for artefact in _dedupe_candidates(candidates)
        )
        if item is not None
    ]
    evidence.sort(key=lambda item: (-item.score, item.path))
    evidence = evidence[:max_sources]
    if not evidence:
        trace.notes.append("skipped:no_scheduling_evidence")
        return None, trace

    sections = [
        "Prepared scheduling evidence",
        f"Question: {prompt.strip()}",
        "",
        "Candidate time evidence:",
        *_evidence_lines(evidence),
        "",
        "Coverage:",
        f"- sources_considered: {len(candidates)}",
        f"- evidence_sources: {len(evidence)}",
        "- gaps: " + ("none detected" if any(item.time_windows for item in evidence) else "no explicit time window found"),
    ]
    digest = hashlib.sha256((prompt + "\n" + "\n".join(item.source_key for item in evidence)).encode("utf-8")).hexdigest()[:16]
    metadata = {
        "context_sources": ["prepare_scheduling_evidence"],
        "scheduling_evidence_source_keys": [item.source_key for item in evidence],
        "scheduling_evidence_source_paths": [item.path for item in evidence],
        "scheduling_evidence": [
            {
                "source_key": item.source_key,
                "path": item.path,
                "snippet": item.snippet,
                "score": item.score,
                "time_windows": item.time_windows,
                "confidence": item.confidence,
                "status": item.status,
            }
            for item in evidence
        ],
        "provenance": "prepared_scheduling_evidence",
    }
    trace.output_count = 1
    trace.notes.append(f"evidence_sources:{len(evidence)}")
    trace.notes.append(f"top_score:{evidence[0].score:.1f}")
    return (
        Artefact(
            key=f"prepared_context:scheduling_evidence:{digest}",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path=f"prepared_context/scheduling_evidence/{digest}.md",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="vaner-context-tools",
            content="\n".join(sections).strip(),
            metadata=metadata,
        ),
        trace,
    )


def score_scheduling_evidence(prompt: str, artefact: Artefact, profile: ContextPreparationProfile | None = None) -> float:
    item = _scheduling_evidence_for(prompt, artefact, profile)
    return item.score if item is not None else 0.0


def _scheduling_evidence_for(
    prompt: str,
    artefact: Artefact,
    profile: ContextPreparationProfile | None = None,
) -> SchedulingEvidence | None:
    lowered = prompt.lower()
    if profile is not None and profile.need != "scheduling":
        return None
    if not re.search(r"\b(when|scheduled|schedule|calendar|invite|meeting|time window|booking|booked)\b", lowered):
        return None
    path = artefact.source_path.lower()
    text = f"{artefact.source_path}\n{artefact.content}"
    text_lower = text.lower()
    score = 0.0
    if path.startswith(("gmail/", "calendar/")):
        score += 8.0
    elif path.startswith(("hubspot/", "fireflies/")):
        score -= 2.0

    time_windows = _time_windows(text)
    if time_windows:
        score += min(18.0, 8.0 + 3.0 * len(time_windows))
    if re.search(r"\b(?:pt|pst|pdt|pacific|-0[78]00)\b", text_lower):
        score += 6.0
    if any(term in text_lower for term in ("calendar", "invite", ".ics", "calendar.redwood", "event/")):
        score += 9.0
    status = "candidate"
    if any(term in text_lower for term in ("works for our team", "confirmed", "confirming", "accepted", "locked in")):
        score += 12.0
        status = "confirmed"
    elif any(term in text_lower for term in ("proposed", "tentative", "hold", "requesting")):
        score += 2.0
        status = "tentative"

    if "technical deep dive" in lowered and "technical deep dive" in text_lower:
        score += 6.0
    if "architecture" in lowered and "architecture review" in text_lower:
        score += 5.0
    if re.search(r"\b(isolated network|private|vpc|on-prem|own network)\b", lowered) and any(
        term in text_lower for term in ("private", "vpc", "on-prem", "isolated", "network diagram", "private hosting")
    ):
        score += 8.0
    if re.search(r"\b(healthcare|health care|clinical|medical|hospital)\b", lowered) and any(
        term in text_lower for term in ("health", "clinical", "medical", "patient", "phi", "hipaa")
    ):
        score += 10.0

    numeric_terms = set(re.findall(r"\b\d{2,4}\b", lowered))
    if numeric_terms:
        score += min(6.0, 2.0 * sum(1 for term in numeric_terms if term in text_lower))
    if not time_windows and score < 18.0:
        return None
    confidence = min(0.95, 0.35 + score / 80.0)
    return SchedulingEvidence(
        source_key=artefact.key,
        path=artefact.source_path,
        snippet=_best_scheduling_snippet(artefact.content),
        score=score,
        time_windows=time_windows,
        confidence=confidence,
        status=status,
    )


def _time_windows(text: str) -> list[str]:
    patterns = (
        r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+[a-z]{3,9}\s+\d{1,2}\s*[—-]\s*\d{1,2}:\d{2}\s*[–-]\s*\d{1,2}:\d{2}(?:\s*[ap]m)?(?:\s*(?:pt|pst|pdt|pacific))?",
        r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+[a-z]{3,9}\s+\d{1,2}\s+\d{1,2}:\d{2}(?:\s*[ap]m)?(?:\s*(?:pt|pst|pdt|pacific))?",
        r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\s*[–-]\s*\d{1,2}:\d{2}\s*(?:am|pm)?(?:\s*(?:pt|pst|pdt|pacific))?",
    )
    windows: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = " ".join(match.group(0).split())
            if value not in windows:
                windows.append(value)
    return windows[:8]


def _best_scheduling_snippet(content: str) -> str:
    lines = [" ".join(line.split()) for line in content.splitlines() if line.strip()]
    preferred = [
        line
        for line in lines
        if re.search(
            r"\b(works for our team|confirmed|confirming|invite|calendar|scheduled|proposed|pt|pst|pdt|\d{1,2}:\d{2})\b",
            line,
            re.IGNORECASE,
        )
    ]
    return "\n".join(preferred[:5])[:700] if preferred else " ".join(content.split())[:700]


def _evidence_lines(evidence: list[SchedulingEvidence]) -> list[str]:
    lines = []
    for index, item in enumerate(evidence[:8], start=1):
        windows = "; ".join(item.time_windows) if item.time_windows else "time not explicit"
        lines.append(f"- [{index}] {item.status} score={item.score:.1f} time={windows} source={item.path}")
        lines.append(f"  evidence: {item.snippet[:320]}")
    return lines


def _dedupe_candidates(candidates: list[Artefact]) -> list[Artefact]:
    seen: set[str] = set()
    deduped: list[Artefact] = []
    for artefact in candidates:
        if artefact.key in seen:
            continue
        seen.add(artefact.key)
        deduped.append(artefact)
    return deduped
