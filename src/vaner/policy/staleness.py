# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time


def is_stale_timestamp(generated_at: float, max_age_seconds: int) -> bool:
    return time.time() - generated_at > max_age_seconds
