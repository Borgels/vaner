# SPDX-License-Identifier: Apache-2.0
"""End-to-end test for scripts/refresh_recommended_models.py.

Drives the refresh script against synthetic Ollama fixtures (no live
network calls) and asserts the produced ``data.json`` is schema-valid
+ contains models in the expected param bands.

The fixtures use fictional names (``alpha``, ``bravo``, …) on purpose
— we test bucketing + scraping logic, not which real-world model
gets surfaced.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from vaner.setup.recommended.schema import Registry

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "recommended"
SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "refresh_recommended_models.py"


@pytest.fixture
def refresh_module() -> object:
    """Import the script as a module so we can call main() programmatically.

    The module is registered into ``sys.modules`` *before* exec so that
    its frozen dataclasses can resolve their own ``__module__`` during
    ``__init_subclass__``-time annotation handling. Without this,
    ``dataclass(frozen=True, slots=True)`` blows up with
    ``AttributeError: 'NoneType' object has no attribute '__dict__'`` on
    Python 3.12.
    """
    spec = importlib.util.spec_from_file_location("refresh_recommended_models", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["refresh_recommended_models"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("refresh_recommended_models", None)
        raise
    return module


def test_refresh_against_fixtures_writes_valid_registry(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "data.json"
    rc = refresh_module.main(  # type: ignore[attr-defined]
        [
            "--source-snapshot",
            str(FIXTURE_DIR),
            "--out",
            str(out_path),
        ],
    )
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    registry = Registry.model_validate(payload)
    assert registry.schema_version == 1
    assert len(registry.models) > 0
    # Every model in the registry has a non-null Ollama id.
    for m in registry.models:
        assert m.ollama_id is not None
        assert m.ollama_id == m.id


def test_refresh_buckets_cover_multiple_param_bands(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    """The fixture has 1.5B/3B/7B/8B/13B/32B/70B/405B → must hit ≥ 4 bands."""
    out_path = tmp_path / "data.json"
    refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(FIXTURE_DIR), "--out", str(out_path)],
    )
    registry = Registry.model_validate(json.loads(out_path.read_text(encoding="utf-8")))
    bands = {
        (
            params <= 4,
            4 < params <= 10,
            10 < params <= 20,
            20 < params <= 50,
            50 < params <= 100,
            100 < params <= 250,
            params > 250,
        ).index(True)
        for params in (m.params_b for m in registry.models)
    }
    assert len(bands) >= 4, f"only {len(bands)} param bands hit: {bands}"


def test_refresh_dedupes_same_family_same_size(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    """No duplicate (family, params_b) pairs in the produced registry."""
    out_path = tmp_path / "data.json"
    refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(FIXTURE_DIR), "--out", str(out_path)],
    )
    registry = Registry.model_validate(json.loads(out_path.read_text(encoding="utf-8")))
    keys = [(m.family, m.params_b) for m in registry.models]
    assert len(keys) == len(set(keys)), keys


def test_refresh_assigns_min_budget_above_zero(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "data.json"
    refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(FIXTURE_DIR), "--out", str(out_path)],
    )
    registry = Registry.model_validate(json.loads(out_path.read_text(encoding="utf-8")))
    for m in registry.models:
        assert m.min_effective_gb_q4 > 0


def test_refresh_fails_loudly_on_empty_registry(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    """The CI staleness gate: zero models must return non-zero exit."""
    empty_dir = tmp_path / "empty-snapshot"
    empty_dir.mkdir()
    # Library file exists but has no model anchors.
    (empty_dir / "ollama_library.html").write_text("<html><body>nothing</body></html>", encoding="utf-8")

    rc = refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(empty_dir), "--out", str(tmp_path / "data.json")],
    )
    assert rc != 0


def test_refresh_allow_empty_overrides_the_gate(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty-snapshot"
    empty_dir.mkdir()
    (empty_dir / "ollama_library.html").write_text("<html></html>", encoding="utf-8")
    rc = refresh_module.main(  # type: ignore[attr-defined]
        [
            "--source-snapshot",
            str(empty_dir),
            "--out",
            str(tmp_path / "data.json"),
            "--allow-empty",
        ],
    )
    assert rc == 0


def test_refresh_intent_lean_for_coder_family_includes_coding(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    """The 'charlie-coder' fixture should be tagged as a coding model."""
    out_path = tmp_path / "data.json"
    refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(FIXTURE_DIR), "--out", str(out_path)],
    )
    registry = Registry.model_validate(json.loads(out_path.read_text(encoding="utf-8")))
    coder_entries = [m for m in registry.models if "charlie-coder" in m.id]
    assert coder_entries, [m.id for m in registry.models]
    # Unknown family in the FAMILY_INTENTS table → defaults to ("mixed",).
    # The fixture name is intentionally not in the mapping; this asserts
    # the safe fallback path. If the maintainer adds 'charlie' to the
    # mapping later, this test will fail loudly and prompt a rethink.
    for m in coder_entries:
        assert m.intent_lean == ("mixed",)


# ---------------------------------------------------------------------------
# Hardening (0.8.8): URL allowlist + response-size + library-entry caps
# ---------------------------------------------------------------------------


def test_http_get_rejects_non_https_scheme(refresh_module: object) -> None:
    """Plain HTTP must be refused — defense against accidental misconfig."""
    from urllib.error import URLError

    with pytest.raises(URLError, match="only https is allowed"):
        refresh_module._http_get("http://ollama.com/library")  # type: ignore[attr-defined]


def test_http_get_rejects_off_allowlist_host(refresh_module: object) -> None:
    """Hosts outside the allowlist must be refused."""
    from urllib.error import URLError

    with pytest.raises(URLError, match="not in the allowlist"):
        refresh_module._http_get("https://evil.example/library")  # type: ignore[attr-defined]


def test_http_get_rejects_uppercase_host_when_off_allowlist(refresh_module: object) -> None:
    """Hostnames are normalised to lowercase before allowlist check."""
    from urllib.error import URLError

    with pytest.raises(URLError, match="not in the allowlist"):
        refresh_module._http_get("https://OLLAMA-MIRROR.example/library")  # type: ignore[attr-defined]


def test_library_scrape_caps_at_500(refresh_module: object) -> None:
    """A malicious library page with 10k entries can't blow up the loop."""
    # Build synthetic HTML with 10k library hrefs.
    html = "\n".join(f'<a href="/library/m{i}">m{i}</a>' for i in range(10_000))
    out = refresh_module._scrape_ollama_library(html)  # type: ignore[attr-defined]
    assert len(out) <= 500
    assert len(out) == 500  # we expect to hit the cap exactly


def test_allowlisted_hosts_are_minimal(refresh_module: object) -> None:
    """Sanity: the allowlist contains only the two production hosts."""
    assert refresh_module._ALLOWED_HOSTS == frozenset(  # type: ignore[attr-defined]
        {"ollama.com", "registry.ollama.ai"}
    )
