# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner.intent.symbol_index import rank_exact_paths, symbol_candidates_for_text


def test_python_ast_definition_beats_test_and_usage(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "frontier.py").write_text(
        "class ExplorationFrontier:\n    def plan_next(self):\n        return None\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "runner.py").write_text("from src.frontier import ExplorationFrontier\n", encoding="utf-8")
    (tmp_path / "tests" / "test_frontier.py").write_text(
        "from src.frontier import ExplorationFrontier\ndef test_frontier():\n    assert ExplorationFrontier\n",
        encoding="utf-8",
    )

    candidates = symbol_candidates_for_text(tmp_path, "Explain ExplorationFrontier")

    assert candidates[0].path == "src/frontier.py"
    assert candidates[0].relation == "definition_match"
    assert any(candidate.relation == "test_match" for candidate in candidates)
    assert any(candidate.relation == "usage_match" for candidate in candidates)


def test_regex_extracts_typescript_rust_and_go_definitions(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "rust").mkdir()
    (tmp_path / "go").mkdir()
    (tmp_path / "web" / "store.ts").write_text("export class ArtefactStore {}\n", encoding="utf-8")
    (tmp_path / "rust" / "reward.rs").write_text("pub fn compute_reward() {}\n", encoding="utf-8")
    (tmp_path / "go" / "frontier.go").write_text("type ExplorationFrontier struct {}\n", encoding="utf-8")

    paths = rank_exact_paths(tmp_path, "ArtefactStore compute_reward ExplorationFrontier", max_paths=3)

    assert paths == ["go/frontier.go", "rust/reward.rs", "web/store.ts"]


def test_available_paths_limit_output_to_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "store.py").write_text("class ArtefactStore:\n    pass\n", encoding="utf-8")
    (tmp_path / "src" / "ignored.py").write_text("class ArtefactStore:\n    pass\n", encoding="utf-8")

    paths = rank_exact_paths(tmp_path, "ArtefactStore", available_paths=("src/store.py",), max_paths=5)

    assert paths == ["src/store.py"]
    assert not paths[0].startswith(str(tmp_path))
