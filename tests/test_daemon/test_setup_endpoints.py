# SPDX-License-Identifier: Apache-2.0
"""WS8 — Daemon HTTP /setup/* + /policy/* + /hardware/* endpoint tests (0.8.6).

Covers the seven endpoints WS8 adds:

- ``GET /setup/questions`` — static five-question payload.
- ``POST /setup/recommend`` — pure read; SetupAnswers in, SelectionResult out.
- ``POST /setup/apply`` — persists, with cloud-widening guard + dry-run.
- ``GET /setup/status`` — composite read (setup + policy + hardware).
- ``GET /policy/current`` — applied-policy summary + bundle.
- ``GET /hardware/profile`` — cached hardware probe.
- ``POST /policy/refresh`` — engine hot-reload trigger.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo
from vaner.daemon.http import create_daemon_http_app


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def client(repo_root: Path) -> TestClient:
    init_repo(repo_root)
    config = load_config(repo_root)
    app = create_daemon_http_app(config)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /setup/questions
# ---------------------------------------------------------------------------


def test_get_setup_questions_returns_five(client: TestClient) -> None:
    resp = client.get("/setup/questions")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["version"] == 1
    questions = payload["questions"]
    assert isinstance(questions, list)
    assert len(questions) == 5
    ids = [q["id"] for q in questions]
    assert ids == [
        "work_styles",
        "priority",
        "compute_posture",
        "cloud_posture",
        "background_posture",
    ]
    # Each question carries kind + default + choices
    for q in questions:
        assert "title" in q and isinstance(q["title"], str)
        assert q["kind"] in ("single", "multi")
        assert "default" in q
        assert isinstance(q["choices"], list) and len(q["choices"]) >= 2
        for choice in q["choices"]:
            assert "value" in choice and "label" in choice
    # work_styles is the only multi-select
    assert questions[0]["kind"] == "multi"


# ---------------------------------------------------------------------------
# /setup/recommend
# ---------------------------------------------------------------------------


def _good_answers() -> dict:
    return {
        "work_styles": ["writing", "research"],
        "priority": "balanced",
        "compute_posture": "balanced",
        "cloud_posture": "ask_first",
        "background_posture": "normal",
    }


def test_post_setup_recommend_happy_path(client: TestClient) -> None:
    resp = client.post("/setup/recommend", json=_good_answers())
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "bundle" in payload
    assert payload["bundle"]["id"]
    assert "score" in payload
    assert isinstance(payload["reasons"], list)
    assert isinstance(payload["runner_ups"], list)
    assert "forced_fallback" in payload


def test_post_setup_recommend_invalid_body(client: TestClient) -> None:
    # Malformed JSON body
    resp = client.post(
        "/setup/recommend",
        content="not json {{{",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


def test_post_setup_recommend_invalid_work_styles(client: TestClient) -> None:
    bad = _good_answers()
    bad["work_styles"] = [1, 2, 3]  # not strings
    resp = client.post("/setup/recommend", json=bad)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /setup/apply
# ---------------------------------------------------------------------------


def test_post_setup_apply_dry_run(client: TestClient, repo_root: Path) -> None:
    config_path = repo_root / ".vaner" / "config.toml"
    before = config_path.read_text(encoding="utf-8")
    resp = client.post(
        "/setup/apply",
        json={"answers": _good_answers(), "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["written"] is False
    assert payload["dry_run"] is True
    assert payload["selected_bundle_id"]
    assert "applied_policy" in payload
    assert payload["bundle"]["id"] == payload["selected_bundle_id"]
    after = config_path.read_text(encoding="utf-8")
    assert after == before, "dry_run must not modify config.toml"


def test_post_setup_apply_blocks_on_widening(client: TestClient, repo_root: Path) -> None:
    """When the new bundle would widen cloud posture and the caller did
    not pass confirm_cloud_widening=true, the endpoint must return 200
    with widens_cloud_posture=true and written=false.
    """

    # Seed a local-only baseline so any non-local bundle widens.
    seed = client.post(
        "/setup/apply",
        json={
            "answers": {
                "work_styles": ["coding"],
                "priority": "privacy",
                "compute_posture": "balanced",
                "cloud_posture": "local_only",
                "background_posture": "normal",
            },
            "confirm_cloud_widening": True,
        },
    )
    assert seed.status_code == 200, seed.text
    assert seed.json()["written"] is True

    # Now try to apply a bundle that widens to hybrid/cloud — without confirm.
    config_path = repo_root / ".vaner" / "config.toml"
    before = config_path.read_text(encoding="utf-8")
    resp = client.post(
        "/setup/apply",
        json={
            "bundle_id": "hybrid_balanced",
            "confirm_cloud_widening": False,
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["widens_cloud_posture"] is True
    assert payload["written"] is False
    after = config_path.read_text(encoding="utf-8")
    assert after == before, "blocked apply must not write config.toml"


def test_post_setup_apply_proceeds_with_confirm(client: TestClient, repo_root: Path) -> None:
    # Seed local-only.
    client.post(
        "/setup/apply",
        json={
            "answers": {
                "work_styles": ["coding"],
                "priority": "privacy",
                "compute_posture": "balanced",
                "cloud_posture": "local_only",
                "background_posture": "normal",
            },
            "confirm_cloud_widening": True,
        },
    )
    resp = client.post(
        "/setup/apply",
        json={
            "bundle_id": "hybrid_balanced",
            "confirm_cloud_widening": True,
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["written"] is True
    assert payload["selected_bundle_id"] == "hybrid_balanced"
    config_path = repo_root / ".vaner" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    assert "selected_bundle_id" in text
    assert "hybrid_balanced" in text


def test_post_setup_apply_unknown_bundle_400(client: TestClient) -> None:
    resp = client.post("/setup/apply", json={"bundle_id": "no_such_bundle_xyz"})
    assert resp.status_code == 400


def test_post_setup_apply_no_answers_no_section(client: TestClient) -> None:
    resp = client.post("/setup/apply", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /setup/status
# ---------------------------------------------------------------------------


def test_get_setup_status_well_formed(client: TestClient) -> None:
    resp = client.get("/setup/status")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    # Keys required by the contract:
    for key in (
        "mode",
        "completed",
        "selected_bundle_id",
        "applied_policy",
        "bundle",
        "hardware",
        "setup",
        "policy",
    ):
        assert key in payload, f"missing key {key!r} in {payload!r}"
    # Hardware shape mirrors WS6's _hardware_to_dict.
    hw = payload["hardware"]
    for key in ("os", "cpu_class", "ram_gb", "tier"):
        assert key in hw


def test_get_setup_status_after_apply_marks_completed(
    client: TestClient,
) -> None:
    apply_resp = client.post(
        "/setup/apply",
        json={"answers": _good_answers(), "confirm_cloud_widening": True},
    )
    assert apply_resp.status_code == 200, apply_resp.text
    assert apply_resp.json()["written"] is True

    status = client.get("/setup/status").json()
    assert status["completed"] is True
    assert status["mode"] == "simple"
    assert status["selected_bundle_id"]


# ---------------------------------------------------------------------------
# /policy/current
# ---------------------------------------------------------------------------


def test_get_policy_current_includes_overrides(client: TestClient) -> None:
    resp = client.get("/policy/current")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "selected_bundle_id" in payload
    assert "bundle" in payload
    assert payload["bundle"]["id"] == payload["selected_bundle_id"]
    assert "applied_policy" in payload
    applied = payload["applied_policy"]
    assert "bundle_id" in applied
    assert "overrides_applied" in applied
    assert isinstance(applied["overrides_applied"], list)
    assert "engine_wired" in payload
    assert payload["engine_wired"] is False


# ---------------------------------------------------------------------------
# /hardware/profile (caches)
# ---------------------------------------------------------------------------


def test_get_hardware_profile_caches(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """First call probes detect(); second call within process lifetime
    returns the cached profile without re-probing.
    """

    # Reset cache so we know the first call is the cold path.
    client.app.state.reset_hardware_cache()

    import vaner.setup.hardware as hardware_module

    counter = {"n": 0}
    original_detect = hardware_module.detect

    def _counting_detect():
        counter["n"] += 1
        return original_detect()

    monkeypatch.setattr(hardware_module, "detect", _counting_detect)

    r1 = client.get("/hardware/profile")
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    for key in ("os", "tier", "ram_gb", "cpu_class"):
        assert key in body1

    r2 = client.get("/hardware/profile")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body1 == body2
    # Cold start probed once. Warm read must not re-probe.
    assert counter["n"] == 1


# ---------------------------------------------------------------------------
# /policy/refresh
# ---------------------------------------------------------------------------


def test_post_policy_refresh_503_when_engine_missing(client: TestClient) -> None:
    """The default fixture does not wire an engine; refresh must 503."""

    resp = client.post("/policy/refresh", json={})
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] in ("engine_unavailable", "engine_unsupported")


def test_post_policy_refresh_happy_path(repo_root: Path) -> None:
    """When an engine with _refresh_policy_bundle_state is wired, the
    endpoint fires the hook and returns a well-formed envelope.
    """

    init_repo(repo_root)
    config = load_config(repo_root)

    class FakeApplied:
        bundle_id = "hybrid_balanced"
        overrides_applied = ("info: refreshed",)

    class FakeEngine:
        def __init__(self) -> None:
            self.refresh_calls = 0
            self._applied_policy = FakeApplied()

        def _refresh_policy_bundle_state(self) -> None:
            self.refresh_calls += 1

        async def initialize(self) -> None:  # required by lifespan
            return None

        async def precompute_cycle(self) -> int:  # required by lifespan loop
            return 0

    fake = FakeEngine()
    app = create_daemon_http_app(config, engine=fake)
    # Skip the lifespan's precompute task by using TestClient without
    # entering the context manager — endpoints work on the raw app.
    client = TestClient(app)
    resp = client.post("/policy/refresh", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["refreshed"] is True
    assert body["applied_policy_summary"]["bundle_id"] == "hybrid_balanced"
    assert "info: refreshed" in body["applied_policy_summary"]["overrides_applied"]
    assert fake.refresh_calls == 1


def test_post_policy_refresh_503_on_engine_exception(repo_root: Path) -> None:
    init_repo(repo_root)
    config = load_config(repo_root)

    class BoomEngine:
        def _refresh_policy_bundle_state(self) -> None:
            raise RuntimeError("kaboom")

        async def initialize(self) -> None:
            return None

        async def precompute_cycle(self) -> int:
            return 0

    app = create_daemon_http_app(config, engine=BoomEngine())
    client = TestClient(app)
    resp = client.post("/policy/refresh", json={})
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "refresh_failed"
    assert "kaboom" in body["message"]
