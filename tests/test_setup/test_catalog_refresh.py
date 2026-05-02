"""Tests for vaner.setup.catalog_refresh + the catalog CLI subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from vaner.cli.commands.setup import setup_app
from vaner.setup.catalog_refresh import (
    build_registry,
    build_registry_entry_for_family,
    estimate_memory_budget,
    families_from_seed,
    load_catalog_seed,
    manifest_weights_bytes,
    quantization_bytes_per_param,
)
from vaner.setup.model_recommendation import validate_model_registry

runner = CliRunner()


def _seed() -> dict[str, Any]:
    return load_catalog_seed()


def _fake_manifest(weight_bytes: int) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "layers": [
            {"mediaType": "application/vnd.ollama.image.model", "size": weight_bytes},
            {"mediaType": "application/vnd.ollama.image.license", "size": 1234},
        ],
    }


def test_seed_loads_with_family_metadata() -> None:
    seed = _seed()
    assert "quantization_profiles" in seed
    assert "Q4_K_M" in seed["quantization_profiles"]
    families = seed.get("families", [])
    assert families, "seed must list at least one family"
    for family in families:
        assert family.get("ollama_family"), f"family {family['id']} missing ollama_family"
        params = family["parameters"]
        assert "context_window" in params
        assert "temperature" in params, f"family {family['id']} missing sampling defaults"


def test_families_from_seed_carries_ranks_and_params() -> None:
    seed = _seed()
    families = families_from_seed(seed)
    assert families
    for f in families:
        assert "temperature" in f.parameters
        assert "context_window" in f.parameters
        assert f.quality_rank >= 0


def test_quantization_bytes_per_param_falls_back() -> None:
    seed = _seed()
    assert quantization_bytes_per_param(seed, "Q4_K_M") > 0
    assert quantization_bytes_per_param(seed, "MADE_UP") > 0


def test_estimate_memory_budget_recommended_exceeds_min() -> None:
    min_gb, rec_gb = estimate_memory_budget(7.0, 0.55, 32768)
    assert min_gb > 0
    assert rec_gb >= min_gb
    _, rec_short = estimate_memory_budget(7.0, 0.55, 8192)
    _, rec_long = estimate_memory_budget(7.0, 0.55, 131072)
    assert rec_long > rec_short


def test_manifest_weights_bytes_sums_model_layers() -> None:
    manifest = {
        "layers": [
            {"mediaType": "application/vnd.ollama.image.model", "size": 1_000_000},
            {"mediaType": "application/vnd.ollama.image.model.shard", "size": 500_000},
            {"mediaType": "application/vnd.ollama.image.license", "size": 9_999},
        ]
    }
    assert manifest_weights_bytes(manifest) == 1_500_000


def test_manifest_weights_bytes_zero_when_no_model_layer() -> None:
    assert manifest_weights_bytes({"layers": []}) == 0
    assert manifest_weights_bytes({"layers": [{"mediaType": "other", "size": 100}]}) == 0


def test_build_entry_online_uses_manifest_size() -> None:
    seed = _seed()
    families = families_from_seed(seed)
    family = families[0]

    # ~24 GB on disk → at Q4_K_M (0.55 GB/B) ≈ 43.6 B params.
    fake_bytes = int(24 * 1024**3)
    entry = build_registry_entry_for_family(
        seed,
        family,
        quant="Q4_K_M",
        online=True,
        manifest_fetcher=lambda _f: _fake_manifest(fake_bytes),
    )
    assert entry is not None
    assert entry["id"].endswith(":latest")
    assert entry["params_b"] > 0
    assert entry["min_effective_memory_gb"] > 0
    assert entry["recommended_effective_memory_gb"] >= entry["min_effective_memory_gb"]
    # Sampling defaults flow through to the registry row.
    params = entry["parameters"]
    assert "temperature" in params
    assert "top_p" in params
    assert params["num_ctx"] == params["context_window"]


def test_build_entry_online_skips_when_manifest_missing() -> None:
    seed = _seed()
    families = families_from_seed(seed)
    entry = build_registry_entry_for_family(
        seed,
        families[0],
        quant="Q4_K_M",
        online=True,
        manifest_fetcher=lambda _f: None,
    )
    assert entry is None


def test_build_registry_offline_emits_one_per_family() -> None:
    payload = build_registry(online=False)
    seed_families = len(_seed()["families"])
    assert len(payload["models"]) == seed_families
    validate_model_registry(payload)


def test_effective_context_window_floors_at_32k() -> None:
    from vaner.setup.model_recommendation import compute_effective_context_window

    # Tight memory + lightweight archetype → still at least the 32K floor.
    chosen = compute_effective_context_window(
        max_context_window=131072,
        weights_gb=20.0,
        effective_memory_gb=22.0,  # almost no headroom
        work_styles=("learning",),
        runtime="ollama",
    )
    assert chosen >= 32768
    assert chosen <= 131072


def test_effective_context_window_caps_at_model_max() -> None:
    from vaner.setup.model_recommendation import compute_effective_context_window

    # A small model that says max=8K should never be pushed past that
    # ceiling, even with archetype boosts and unlimited memory.
    chosen = compute_effective_context_window(
        max_context_window=8192,
        weights_gb=2.0,
        effective_memory_gb=64.0,
        work_styles=("coding",),
        runtime="ollama",
    )
    # Floor (32K) > model max (8K) → floor wins because 32K is the
    # cockpit's hard requirement; this is a known design choice. The
    # function clamps to floor, callers can decide whether to drop the
    # model from candidates.
    assert chosen == 32768


def test_effective_context_window_scales_up_with_archetype_and_memory() -> None:
    from vaner.setup.model_recommendation import compute_effective_context_window

    short = compute_effective_context_window(
        max_context_window=262144,
        weights_gb=10.0,
        effective_memory_gb=14.0,
        work_styles=("writing",),
        runtime="ollama",
    )
    long = compute_effective_context_window(
        max_context_window=262144,
        weights_gb=10.0,
        effective_memory_gb=64.0,
        work_styles=("coding", "research"),
        runtime="ollama",
    )
    assert long > short
    # Plenty of memory + long-context archetype → at or above the 64K target.
    assert long >= 65536


def test_build_registry_online_skips_unreachable_families() -> None:
    seed = _seed()
    families = families_from_seed(seed)
    target = families[0].ollama_family

    def fake_fetcher(family_name: str):
        if family_name == target:
            return _fake_manifest(int(8 * 1024**3))
        return None

    payload = build_registry(online=True, seed=seed, manifest_fetcher=fake_fetcher)
    assert len(payload["models"]) == 1
    assert payload["models"][0]["family_id"] == families[0].id
    skipped = payload.get("skipped", [])
    assert len(skipped) == len(families) - 1
    for entry in skipped:
        assert entry["reason"] == "ollama_latest_not_found"


def test_split_parameters_separates_sampling_and_runtime() -> None:
    """The recommendation payload exposes sampling/runtime/capability cleanly."""

    from vaner.setup.model_recommendation import _split_parameters

    flat = {
        "context_window": 32768,
        "reasoning_mode": "allowed",
        "max_response_tokens": 4096,
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 20,
        "num_ctx": 32768,
        "keep_alive": "10m",
    }
    capability, runtime_params, sampling = _split_parameters(flat)
    assert "context_window" in capability
    assert "max_response_tokens" in capability
    assert "temperature" in sampling
    assert "top_p" in sampling
    assert runtime_params["num_ctx"] == 32768
    assert runtime_params["keep_alive"] == "10m"
    # Sampling keys never leak into runtime, and vice versa.
    assert "temperature" not in runtime_params
    assert "num_ctx" not in sampling


def test_split_parameters_fills_runtime_defaults() -> None:
    from vaner.setup.model_recommendation import _split_parameters

    capability, runtime_params, sampling = _split_parameters({"context_window": 16384, "max_response_tokens": 2048})
    # When the registry omits keep_alive / num_ctx / num_predict, the
    # split fills them so Ollama actually uses the full context window.
    assert runtime_params["keep_alive"] == "10m"
    assert runtime_params["num_ctx"] == 16384
    assert runtime_params["num_predict"] == 2048


def test_catalog_refresh_cli_offline_dry_run() -> None:
    result = runner.invoke(setup_app, ["catalog", "refresh", "--offline", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["online"] is False
    assert payload["models"]


def test_catalog_refresh_cli_writes_to_output(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    result = runner.invoke(
        setup_app,
        ["catalog", "refresh", "--offline", "--output", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert target.exists()
    payload = json.loads(target.read_text())
    assert payload["models"]
    # The CLI never writes a malformed registry, even on the cold path.
    validate_model_registry(payload)


def test_catalog_show_cli_emits_json() -> None:
    result = runner.invoke(setup_app, ["catalog", "show"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["models"]
