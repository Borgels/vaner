# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed persistence for the cross-workspace ``UserProfile``.

Prior to 0.8.0 the profile was stored as a JSON file. SQLite gives us atomic
writes, concurrent read safety, and a migration path — the JSON file is
imported on first run and then removed.

The profile is keyed by ``profile_key`` (default ``"default"``) so one DB can
hold multiple logical profiles if we ever extend beyond a single user.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

from vaner.intent.profile import UserProfile

logger = logging.getLogger(__name__)


class UserProfileStore:
    def __init__(self, db_path: Path, *, profile_key: str = "default") -> None:
        self.db_path = db_path
        self.profile_key = profile_key
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profile (
                    profile_key TEXT PRIMARY KEY,
                    pace_ema_seconds REAL NOT NULL DEFAULT 0.0,
                    pivot_rate REAL NOT NULL DEFAULT 0.0,
                    depth_preference REAL NOT NULL DEFAULT 0.0,
                    mode_mix_json TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL,
                    last_query_ts REAL NOT NULL DEFAULT 0.0,
                    query_count INTEGER NOT NULL DEFAULT 0,
                    pivots INTEGER NOT NULL DEFAULT 0,
                    last_mode TEXT NOT NULL DEFAULT ''
                )
                """
            )
            await db.commit()
        self._initialized = True

    async def load(self) -> UserProfile:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT pace_ema_seconds, pivot_rate, depth_preference, mode_mix_json,
                       updated_at, last_query_ts, query_count, pivots, last_mode
                FROM user_profile WHERE profile_key = ?
                """,
                (self.profile_key,),
            )
            row = await cursor.fetchone()
        if row is None:
            return UserProfile()
        profile = UserProfile()
        profile.pace_ema_seconds = float(row[0])
        profile.pivot_rate = float(row[1])
        profile.depth_preference = float(row[2])
        try:
            mix = json.loads(row[3])
            profile.mode_mix = {str(k): float(v) for k, v in mix.items()} if isinstance(mix, dict) else {}
        except Exception:
            profile.mode_mix = {}
        profile.updated_at = float(row[4])
        profile._last_query_ts = float(row[5])
        profile._query_count = int(row[6])
        profile._pivots = int(row[7])
        profile._last_mode = str(row[8])
        return profile

    async def save(self, profile: UserProfile) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO user_profile (
                    profile_key, pace_ema_seconds, pivot_rate, depth_preference,
                    mode_mix_json, updated_at, last_query_ts, query_count, pivots, last_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                    pace_ema_seconds=excluded.pace_ema_seconds,
                    pivot_rate=excluded.pivot_rate,
                    depth_preference=excluded.depth_preference,
                    mode_mix_json=excluded.mode_mix_json,
                    updated_at=excluded.updated_at,
                    last_query_ts=excluded.last_query_ts,
                    query_count=excluded.query_count,
                    pivots=excluded.pivots,
                    last_mode=excluded.last_mode
                """,
                (
                    self.profile_key,
                    float(profile.pace_ema_seconds),
                    float(profile.pivot_rate),
                    float(profile.depth_preference),
                    json.dumps(profile.mode_mix, sort_keys=True),
                    float(profile.updated_at),
                    float(profile._last_query_ts),
                    int(profile._query_count),
                    int(profile._pivots),
                    str(profile._last_mode),
                ),
            )
            await db.commit()

    async def migrate_from_json(self, json_path: Path) -> bool:
        """Import a legacy JSON profile on first start. Returns True if migration ran.

        Fails closed: on any error we keep the JSON file on disk (untouched) so the
        operator can investigate. Only removes the JSON after the SQLite upsert
        completes successfully and we have confirmed the row is readable.
        """
        if not json_path.exists():
            return False
        # Don't stomp on a profile that already has SQLite data.
        existing = await self.load()
        if existing._query_count > 0:
            return False
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return False
        except Exception as exc:
            logger.warning("Failed to read legacy profile JSON at %s: %s", json_path, exc)
            return False
        legacy = UserProfile()
        legacy.pace_ema_seconds = float(raw.get("pace_ema_seconds", 0.0))
        legacy.pivot_rate = float(raw.get("pivot_rate", 0.0))
        legacy.depth_preference = float(raw.get("depth_preference", 0.0))
        mix = raw.get("mode_mix", {})
        if isinstance(mix, dict):
            legacy.mode_mix = {str(k): float(v) for k, v in mix.items()}
        legacy.updated_at = float(raw.get("updated_at", 0.0))
        legacy._last_query_ts = float(raw.get("_last_query_ts", 0.0))
        legacy._query_count = int(raw.get("_query_count", 0))
        legacy._pivots = int(raw.get("_pivots", 0))
        legacy._last_mode = str(raw.get("_last_mode", ""))
        if legacy._query_count == 0:
            # Empty JSON profile — nothing worth migrating, but still remove stub.
            try:
                json_path.unlink()
            except Exception:
                pass
            return False
        await self.save(legacy)
        # Only remove after save + re-read roundtrip succeeds.
        try:
            persisted = await self.load()
            if persisted._query_count == legacy._query_count:
                json_path.unlink()
                return True
        except Exception as exc:
            logger.warning("Profile migration roundtrip check failed for %s: %s", json_path, exc)
        return False
