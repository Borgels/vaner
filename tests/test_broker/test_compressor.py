# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.broker.compressor import compress_context
from vaner.models.artefact import Artefact, ArtefactKind


def _artefact(key: str, source: str, content: str) -> Artefact:
    ts = time.time()
    return Artefact(
        key=key,
        kind=ArtefactKind.FILE_SUMMARY,
        source_path=source,
        source_mtime=ts,
        generated_at=ts,
        model="test",
        content=content,
    )


def test_compressor_empty_input():
    context, token_map, used, kept = compress_context([], max_tokens=100)
    assert context == ""
    assert token_map == {}
    assert used == 0
    assert kept == set()


def test_compressor_single_chunk_over_budget():
    artefacts = [_artefact("a", "a.py", "word " * 400)]
    context, token_map, used, kept = compress_context(artefacts, max_tokens=10)
    assert "a.py" in context
    assert "[trimmed]" in context
    assert token_map["a"] <= 10
    assert used == token_map["a"]
    assert kept == {"a"}


def test_compressor_trims_top_scored_chunk_instead_of_dropping_it():
    artefacts = [_artefact("low", "low.py", "tiny"), _artefact("high", "high.py", "alpha " * 500)]
    context, token_map, used, kept = compress_context(
        artefacts,
        max_tokens=24,
        score_by_key={"high": 10.0, "low": 1.0},
    )
    assert "high.py" in context
    assert context.index("high.py") < context.find("low.py") if "low.py" in context else True
    assert "high" in kept
    assert used == sum(token_map[key] for key in kept)


def test_compressor_counts_tokens_once():
    artefacts = [_artefact("a", "a.py", "alpha " * 20), _artefact("b", "b.py", "beta " * 20)]
    _, token_map, used, kept = compress_context(artefacts, max_tokens=200)
    assert used == sum(token_map[key] for key in kept)


def test_compressor_prefers_high_score_when_order_unsorted():
    artefacts = [
        _artefact("low", "low.py", "tiny chunk"),
        _artefact("high", "high.py", "tiny chunk"),
    ]
    context, _, _, kept = compress_context(
        artefacts,
        max_tokens=100,
        score_by_key={"high": 10.0, "low": 1.0},
    )
    assert kept == {"high", "low"}
    assert context.index("high.py") < context.index("low.py")


def test_compressor_compacts_large_chunks_to_implementation_anchors():
    content = "\n".join(
        [
            "intro " * 300,
            "Schema: CREATE TABLE artefacts (key TEXT PRIMARY KEY, source_path TEXT NOT NULL)",
            "Functions: list_by_keys(keys: list[str]) -> list[Artefact]",
            "filler " * 300,
        ]
    )
    artefacts = [_artefact("store", "src/vaner/store/artefacts.py", content)]
    context, _, used, kept = compress_context(artefacts, max_tokens=180)

    assert kept == {"store"}
    assert used <= 180
    assert "CREATE TABLE artefacts" in context
    assert "list_by_keys" in context
    assert "compacted to important implementation anchors" in context


def test_query_aware_compressor_preserves_dispersed_reward_ingredients():
    content = "\n".join(
        [
            "from dataclasses import dataclass",
            "DEFAULT_REWARD_WEIGHTS = {'cache_tier': 0.25, 'quality_lift': 0.35, 'raw_reward': 0.10}",
            *[f"# filler {index}" for index in range(40)],
            "def compute_reward(inputs):",
            "    raw_reward = inputs.cache_tier + inputs.quality_lift",
            "    return {'reward_total': raw_reward, 'reward_components': DEFAULT_REWARD_WEIGHTS}",
            *[f"# gap {index}" for index in range(40)],
            "def update_replay_priority(outcome):",
            "    downstream_consumer = outcome.reward_total",
            "    return 1.0 + downstream_consumer",
        ]
    )
    artefacts = [_artefact("reward", "src/vaner/learning/reward.py", content)]

    context, token_map, used, kept = compress_context(
        artefacts,
        max_tokens=170,
        score_by_key={"reward": 30.0},
        query="How does reward computation use default weights and downstream consumers?",
    )

    assert kept == {"reward"}
    assert used <= 170
    assert token_map["reward"] == used
    assert "DEFAULT_REWARD_WEIGHTS" in context
    assert "reward_total" in context
    assert "downstream_consumer" in context
    assert "@@ lines" in context


def test_query_aware_compressor_keeps_non_redundant_bridging_file():
    direct = _artefact(
        "direct",
        "src/vaner/intent/scorer.py",
        "\n".join(
            [
                "def feature_vector_for_artefact(item):",
                "    return [item.path_score, item.content_score]",
                *["# scorer filler" for _ in range(80)],
            ]
        ),
    )
    bridge = _artefact(
        "bridge",
        "src/vaner/intent/trainer.py",
        "def train_target_from_reward(payload):\n"
        "    target = (payload['reward_total'] + 1.0) * 0.5\n"
        "    return target\n",
    )

    context, _, used, kept = compress_context(
        [direct, bridge],
        max_tokens=150,
        score_by_key={"direct": 50.0, "bridge": 5.0},
        query="How does the IntentScorer use GBDT feature groups, training target, and blending logic?",
    )

    assert used <= 150
    assert {"direct", "bridge"} <= kept
    assert "feature_vector_for_artefact" in context
    assert "train_target_from_reward" in context
    assert "reward_total" in context


def test_query_aware_compressor_preserves_schema_and_methods_under_budget():
    content = "\n".join(
        [
            "class ArtefactStore:",
            "    SCHEMA = '''CREATE TABLE context_packages (id TEXT PRIMARY KEY, injected_context TEXT)'''",
            *["    # storage filler" for _ in range(50)],
            "    def persist_context_package(self, package):",
            "        self.conn.execute('INSERT INTO context_packages VALUES (?, ?)', (package.id, package.injected_context))",
            *["    # retrieval filler" for _ in range(50)],
            "    def get_context_package(self, package_id):",
            "        return self.conn.execute('SELECT id, injected_context FROM context_packages WHERE id=?', (package_id,)).fetchone()",
        ]
    )
    artefacts = [_artefact("store", "src/vaner/store/artefacts.py", content)]

    context, _, used, kept = compress_context(
        artefacts,
        max_tokens=190,
        score_by_key={"store": 40.0},
        query="How does the ArtefactStore persist and retrieve context packages? What database schema does it use?",
    )

    assert kept == {"store"}
    assert used <= 190
    assert "CREATE TABLE context_packages" in context
    assert "persist_context_package" in context
    assert "get_context_package" in context
    assert "SELECT id, injected_context" in context
