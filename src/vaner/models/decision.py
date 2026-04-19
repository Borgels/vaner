# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ScoreFactor(BaseModel):
    name: str
    contribution: float
    detail: str = ""


class SelectionDecision(BaseModel):
    artefact_key: str
    source_path: str
    final_score: float
    token_count: int
    stale: bool
    kept: bool = True
    drop_reason: str | None = None
    rationale: str = ""
    factors: list[ScoreFactor] = Field(default_factory=list)


class PredictionLink(BaseModel):
    source: str
    scenario_question: str | None = None
    scenario_rationale: str | None = None
    confidence: float | None = None


class DecisionRecord(BaseModel):
    id: str
    prompt: str
    prompt_hash: str
    assembled_at: float
    cache_tier: str = "miss"
    partial_similarity: float = 0.0
    token_budget: int
    token_used: int
    selections: list[SelectionDecision] = Field(default_factory=list)
    prediction_links: dict[str, PredictionLink] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def to_legacy_markdown(self) -> str:
        lines = [
            f"prompt: {self.prompt}",
            f"token_used: {self.token_used}/{self.token_budget}",
            "",
        ]
        for selection in self.selections:
            if not selection.kept:
                continue
            lines.append(
                "- "
                f"{selection.artefact_key} "
                f"score={selection.final_score:.2f} "
                f"stale={selection.stale} "
                f"tokens={selection.token_count} "
                f"rationale={selection.rationale}"
            )
        return "\n".join(lines)

    @staticmethod
    def _runtime_dir(repo_root: Path) -> Path:
        return repo_root / ".vaner" / "runtime"

    @classmethod
    def _decisions_dir(cls, repo_root: Path) -> Path:
        return cls._runtime_dir(repo_root) / "decisions"

    def write(self, repo_root: Path) -> Path:
        runtime_dir = self._runtime_dir(repo_root)
        decisions_dir = self._decisions_dir(repo_root)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        decisions_dir.mkdir(parents=True, exist_ok=True)

        record_path = decisions_dir / f"{self.id}.json"
        record_path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        (runtime_dir / "last_decision.json").write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return record_path

    @classmethod
    def read_latest(cls, repo_root: Path) -> DecisionRecord | None:
        path = cls._runtime_dir(repo_root) / "last_decision.json"
        if not path.exists():
            return None
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def read_by_id(cls, repo_root: Path, decision_id: str) -> DecisionRecord | None:
        path = cls._decisions_dir(repo_root) / f"{decision_id}.json"
        if not path.exists():
            return None
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def list_recent_ids(cls, repo_root: Path, limit: int = 20) -> list[str]:
        decisions_dir = cls._decisions_dir(repo_root)
        if not decisions_dir.exists():
            return []
        records = sorted(
            decisions_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return [record.stem for record in records[:limit]]

    def to_json(self) -> str:
        payload = self.model_dump(mode="json")
        return json.dumps(payload, indent=2)
