from __future__ import annotations

import sqlite3

from astrbot.api import logger


def _has_column(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def up(cursor: sqlite3.Cursor) -> None:
    logger.info("正在执行 012_cthulhu_pending_snapshot_column")
    if not _has_column(cursor, "user_cthulhu_state", "pending_event_snapshot"):
        cursor.execute(
            "ALTER TABLE user_cthulhu_state ADD COLUMN pending_event_snapshot TEXT"
        )


def down(cursor: sqlite3.Cursor) -> None:
    logger.warning("012_cthulhu_pending_snapshot_column 不提供回滚。")
