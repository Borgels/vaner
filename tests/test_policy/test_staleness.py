# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.policy.staleness import is_stale_timestamp


def test_is_stale_timestamp():
    assert is_stale_timestamp(time.time() - 120, 60) is True
    assert is_stale_timestamp(time.time(), 60) is False
