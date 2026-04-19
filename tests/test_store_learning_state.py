from __future__ import annotations

from pathlib import Path

import pytest

from vaner.store.artefacts import ArtefactStore


@pytest.mark.asyncio
async def test_learning_state_round_trip(tmp_path: Path) -> None:
    store = ArtefactStore(tmp_path / "learning.db")
    await store.initialize()

    await store.upsert_learning_state(
        key="scoring_policy",
        value={"policy_json": '{"score_weights":[0.2,0.2,0.2,0.2,0.2]}'},
    )
    await store.upsert_learning_state(
        key="intent_scorer",
        value={"model_path": "/tmp/model.txt", "model_influence": 0.1},
    )

    policy = await store.get_learning_state("scoring_policy")
    scorer = await store.get_learning_state("intent_scorer")

    assert policy is not None
    assert policy["policy_json"]
    assert scorer is not None
    assert scorer["model_path"] == "/tmp/model.txt"
