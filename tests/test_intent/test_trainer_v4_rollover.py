from __future__ import annotations

from pathlib import Path

import pytest

from vaner.intent.trainer import IntentTrainer, TrainingConfig
from vaner.store.artefacts import ArtefactStore


class _DummyScorer:
    def __init__(self) -> None:
        self.last_train_metrics: dict[str, float] = {"improvement": 0.5}

    def train(
        self,
        train_vectors,
        train_labels,
        *,
        output_path: Path,
        backend: str,
        random_seed: int,
        valid_vectors,
        valid_labels,
        num_threads: int,
    ) -> Path:
        output_path.write_text("dummy-model", encoding="utf-8")
        return output_path


@pytest.mark.asyncio
async def test_trainer_rolls_over_v3_feature_snapshot_to_v4(temp_repo) -> None:
    store = ArtefactStore(temp_repo / ".vaner" / "store.db")
    await store.initialize()
    await store.insert_replay_entry(
        payload={
            "prompt": "yes please",
            "reward_total": 0.2,
            "access_count": 1.0,
            "artefact_age_seconds": 3.0,
            # Simulate persisted v3 snapshot without follow_up_offer_strength.
            "feature_snapshot": {
                "signal_count_recent_15m": 1.0,
                "query_count_total": 4.0,
                "hypothesis_count": 1.0,
            },
        },
        priority=1.0,
    )

    trainer = IntentTrainer(
        store,
        _DummyScorer(),
        config=TrainingConfig(holdout_fraction=0.0, promotion_threshold=0.0),
    )

    trained = await trainer.train_batch(temp_repo / ".vaner")

    assert trained is not None
    assert trainer.last_train_metrics["feature_schema_version"] == "v4"
    assert trainer.last_train_metrics["feature_schema_rolled_over"] is True
    assert trainer.last_train_metrics["feature_schema_rolled_over_rows"] == 1
