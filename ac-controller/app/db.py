"""
SQLite storage layer.

Tables:
  settings  — key/value pairs for GUI-editable overrides
  events    — bounded log of controller actions
"""
from __future__ import annotations

import time
from typing import Any, Optional

import aiosqlite

from .config import get_config
from .models import EventEntry

# Module-level handle opened at startup
_db: Optional[aiosqlite.Connection] = None


async def init_db() -> None:
    global _db
    cfg = get_config()
    db_path = cfg.data_dir / "ac.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             REAL    NOT NULL,
            action         TEXT    NOT NULL,
            reason         TEXT    NOT NULL,
            room_temp      REAL,
            outdoor_temp   REAL,
            committed_mode TEXT    NOT NULL
        )
        """
    )
    await _db.commit()


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("DB not initialised — call init_db() first")
    return _db


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

async def get_setting(key: str, default: Any = None) -> Optional[str]:
    async with _conn().execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: Any) -> None:
    await _conn().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    await _conn().commit()


async def get_all_settings() -> dict[str, str]:
    async with _conn().execute("SELECT key, value FROM settings") as cur:
        rows = await cur.fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

async def log_event(
    action: str,
    reason: str,
    committed_mode: str,
    room_temp: Optional[float] = None,
    outdoor_temp: Optional[float] = None,
) -> None:
    cfg = get_config()
    conn = _conn()
    await conn.execute(
        """
        INSERT INTO events (ts, action, reason, room_temp, outdoor_temp, committed_mode)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (time.time(), action, reason, room_temp, outdoor_temp, committed_mode),
    )
    # Prune old events beyond the configured limit
    await conn.execute(
        """
        DELETE FROM events WHERE id NOT IN (
            SELECT id FROM events ORDER BY id DESC LIMIT ?
        )
        """,
        (cfg.app.event_log_limit,),
    )
    await conn.commit()


async def get_events(limit: int = 100) -> list[EventEntry]:
    async with _conn().execute(
        "SELECT id, ts, action, reason, room_temp, outdoor_temp, committed_mode "
        "FROM events ORDER BY id DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        EventEntry(
            id=r["id"],
            ts=r["ts"],
            action=r["action"],
            reason=r["reason"],
            room_temp=r["room_temp"],
            outdoor_temp=r["outdoor_temp"],
            committed_mode=r["committed_mode"],
        )
        for r in rows
    ]
