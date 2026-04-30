# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import pytest

from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductEvidenceRef,
    WorkProductFreshness,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)


def _snapshot() -> WorkProductSourceSnapshot:
    return WorkProductSourceSnapshot(
        project_id="proj",
        relative_paths=["src/app.py"],
        file_hashes={"src/app.py": "abc123"},
        base_commit="deadbeef",
        generated_at=time.time(),
    )


def test_exportable_requires_active_fresh_state() -> None:
    product = WorkProduct(
        id="wp-1",
        type=WorkProductType.RESEARCH_BRIEF,
        title="Brief",
        summary="Summary",
        body="Body",
        source_snapshot=_snapshot(),
        confidence=0.8,
        freshness=WorkProductFreshness.FRESH,
        status=WorkProductStatus.SURFACED,
        adoptability=WorkProductAdoptability.EXPORTABLE,
        created_at=time.time(),
        updated_at=time.time(),
    )

    assert product.can_export(now=time.time()) is True


def test_candidate_cannot_be_exportable() -> None:
    with pytest.raises(ValueError):
        WorkProduct(
            id="wp-2",
            type=WorkProductType.VIRTUAL_DIFF,
            title="Diff",
            summary="Summary",
            body="Body",
            source_snapshot=_snapshot(),
            confidence=0.8,
            freshness=WorkProductFreshness.FRESH,
            status=WorkProductStatus.CANDIDATE,
            adoptability=WorkProductAdoptability.EXPORTABLE,
            created_at=time.time(),
            updated_at=time.time(),
        )


def test_paths_must_be_relative() -> None:
    with pytest.raises(ValueError):
        WorkProductEvidenceRef(path="/" + "home/user/repo/src/app.py")

    with pytest.raises(ValueError):
        WorkProductSourceSnapshot(
            project_id="proj",
            relative_paths=["../secret.txt"],
            file_hashes={},
            generated_at=time.time(),
        )
