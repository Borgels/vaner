from __future__ import annotations

from pathlib import Path

from vaner.intent.graph import extract_code_relationship_edges


def test_extract_relationship_edges_finds_engine_to_cache_import():
    repo_root = Path(__file__).resolve().parents[2]

    edges = extract_code_relationship_edges(repo_root / "src")
    edge_pairs = {(edge.source_key, edge.target_key) for edge in edges}

    assert ("file:vaner/engine.py", "file:vaner/intent/cache.py") in edge_pairs
