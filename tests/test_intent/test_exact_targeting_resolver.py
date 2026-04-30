# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner.intent.cache import TieredPredictionCache
from vaner.intent.symbol_index import rank_exact_paths


def test_exact_symbol_relevant_paths_defeat_stale_broad_cluster(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "artefact_store.py").write_text("class ArtefactStore:\n    pass\n", encoding="utf-8")
    (tmp_path / "src" / "cache.py").write_text("class CacheCluster:\n    pass\n", encoding="utf-8")
    (tmp_path / "docs" / "architecture.md").write_text("Cache cluster overview\n", encoding="utf-8")

    exact_paths = set(rank_exact_paths(tmp_path, "Implement ArtefactStore"))
    stale_cluster = {"source_units": ["src/cache.py", "docs/architecture.md"]}
    exact_cluster = {"source_units": ["src/artefact_store.py"]}

    assert exact_paths == {"src/artefact_store.py"}
    assert TieredPredictionCache._unit_overlap_score(exact_cluster, exact_paths) == 1.0
    assert TieredPredictionCache._unit_overlap_score(stale_cluster, exact_paths) == 0.0
