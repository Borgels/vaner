# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.policy.budget import enforce_budget


def test_enforce_budget_limits_chunks():
    chunks = ["a " * 100, "b " * 100, "c " * 100]
    kept = enforce_budget(chunks, max_tokens=120)
    assert len(kept) >= 1
    assert len(kept) < len(chunks)
