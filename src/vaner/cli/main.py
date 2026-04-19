# SPDX-License-Identifier: Apache-2.0

"""Thin CLI dispatcher.

Command implementations live in ``vaner.cli.commands`` modules; this entrypoint
preserves the historical ``vaner.cli.main:run`` target.
"""

from __future__ import annotations

from vaner.cli.commands.app_legacy import app, run

__all__ = ["app", "run"]


if __name__ == "__main__":
    run()
