# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite


class TelemetryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            await db.commit()

    async def record(self, name: str, value: float) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO metrics(name, value, ts) VALUES (?, ?, ?)",
                (name, value, time.time()),
            )
            await db.commit()
