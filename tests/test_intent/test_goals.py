# SPDX-License-Identifier: Apache-2.0
"""WS7 — WorkspaceGoal + branch parser tests."""

from __future__ import annotations

import json as _json

import pytest

from vaner.intent.branch_parser import parse_branch_name
from vaner.intent.goals import GoalEvidence, WorkspaceGoal, goal_id
from vaner.store.artefacts import ArtefactStore

# ---------------------------------------------------------------------------
# branch_parser.parse_branch_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "branch,expected_title,expected_category",
    [
        ("feat/jwt-migration", "JWT migration", "feature"),
        ("feature/jwt-migration", "JWT migration", "feature"),
        ("fix/auth-token-leak", "Auth token leak", "fix"),
        ("bugfix/auth-token-leak", "Auth token leak", "fix"),
        ("hotfix/login-500", "Login 500", "fix"),
        ("refactor/session-to-jwt", "Session to JWT", "refactor"),
        ("perf/cache-warmup", "Cache warmup", "performance"),
        ("docs/api-reference", "API reference", "docs"),
        ("test/parser-edge-cases", "Parser edge cases", "tests"),
    ],
)
def test_parse_branch_name_recognises_common_prefixes(branch, expected_title, expected_category):
    hint = parse_branch_name(branch)
    assert hint is not None
    assert hint.title == expected_title
    assert hint.category == expected_category
    assert 0.3 <= hint.confidence <= 0.9


@pytest.mark.parametrize(
    "branch",
    [
        "",
        "   ",
        "main",
        "master",
        "develop",
        "DEVELOP",
        "some-random-branch",
        "feat",  # prefix without slug
    ],
)
def test_parse_branch_name_returns_none_for_irrelevant(branch):
    assert parse_branch_name(branch) is None


def test_parse_branch_name_handles_personal_namespace():
    """Branches like ``user/alice/feat/...`` should still parse — the
    parser walks segments and picks the first recognised prefix."""
    hint = parse_branch_name("user/alice/feat/jwt-migration")
    assert hint is not None
    assert hint.title == "JWT migration"
    assert hint.category == "feature"


def test_parse_branch_name_preserves_short_uppercase_acronyms():
    hint = parse_branch_name("feat/RBAC-for-admin")
    assert hint is not None
    assert "RBAC" in hint.title


def test_parse_branch_name_handles_underscores():
    hint = parse_branch_name("fix/auth_token_leak")
    assert hint is not None
    assert hint.title == "Auth token leak"


def test_goal_hint_confidence_varies_by_category():
    """Feature branches claim higher confidence than chore/wip."""
    feat = parse_branch_name("feat/login-page")
    chore = parse_branch_name("chore/update-deps")
    wip = parse_branch_name("wip/notes")
    assert feat is not None and chore is not None and wip is not None
    assert feat.confidence > chore.confidence
    assert chore.confidence > wip.confidence


# ---------------------------------------------------------------------------
# goals.WorkspaceGoal
# ---------------------------------------------------------------------------


def test_goal_id_is_stable_for_same_inputs():
    a = goal_id("branch_name", "JWT migration")
    b = goal_id("branch_name", "JWT migration")
    c = goal_id("user_declared", "JWT migration")
    assert a == b
    assert a != c  # different source → different id


def test_workspace_goal_from_hint_constructs_consistent_id():
    goal = WorkspaceGoal.from_hint(
        title="JWT migration",
        source="branch_name",
        confidence=0.8,
    )
    assert goal.id == goal_id("branch_name", "JWT migration")
    assert goal.title == "JWT migration"
    assert goal.status == "active"
    assert goal.evidence == []
    assert goal.related_files == []


def test_workspace_goal_carries_evidence_and_related_files():
    goal = WorkspaceGoal.from_hint(
        title="Add auth endpoint",
        source="user_declared",
        confidence=1.0,
        evidence=[GoalEvidence(kind="file_path", value="src/auth.py")],
        related_files=["src/auth.py", "tests/test_auth.py"],
    )
    assert goal.evidence[0].value == "src/auth.py"
    assert goal.related_files == ["src/auth.py", "tests/test_auth.py"]


# ---------------------------------------------------------------------------
# Store round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_goals_store_upsert_and_list(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    await store.upsert_workspace_goal(
        id="abc123",
        title="JWT migration",
        description="Replace session tokens with JWT",
        source="user_declared",
        confidence=1.0,
        status="active",
        evidence_json=_json.dumps([{"kind": "file_path", "value": "src/auth.py"}]),
        related_files_json=_json.dumps(["src/auth.py"]),
    )
    rows = await store.list_workspace_goals()
    assert len(rows) == 1
    assert rows[0]["id"] == "abc123"
    assert rows[0]["title"] == "JWT migration"
    assert rows[0]["status"] == "active"
    assert _json.loads(rows[0]["related_files_json"]) == ["src/auth.py"]


@pytest.mark.asyncio
async def test_workspace_goals_store_update_status(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    await store.upsert_workspace_goal(
        id="gid1",
        title="g1",
        description="",
        source="branch_name",
        confidence=0.7,
        status="active",
        evidence_json="[]",
        related_files_json="[]",
    )
    changed = await store.update_workspace_goal_status("gid1", "achieved")
    assert changed is True
    row = await store.get_workspace_goal("gid1")
    assert row is not None
    assert row["status"] == "achieved"


@pytest.mark.asyncio
async def test_workspace_goals_store_delete(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    await store.upsert_workspace_goal(
        id="gid2",
        title="g2",
        description="",
        source="branch_name",
        confidence=0.7,
        status="active",
        evidence_json="[]",
        related_files_json="[]",
    )
    assert await store.get_workspace_goal("gid2") is not None
    assert await store.delete_workspace_goal("gid2") is True
    assert await store.get_workspace_goal("gid2") is None


@pytest.mark.asyncio
async def test_workspace_goals_store_filter_by_status(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    await store.upsert_workspace_goal(
        id="g_active",
        title="active one",
        description="",
        source="branch_name",
        confidence=0.7,
        status="active",
        evidence_json="[]",
        related_files_json="[]",
    )
    await store.upsert_workspace_goal(
        id="g_done",
        title="done one",
        description="",
        source="branch_name",
        confidence=0.7,
        status="achieved",
        evidence_json="[]",
        related_files_json="[]",
    )
    active = await store.list_workspace_goals(status="active")
    assert [row["id"] for row in active] == ["g_active"]
    done = await store.list_workspace_goals(status="achieved")
    assert [row["id"] for row in done] == ["g_done"]
