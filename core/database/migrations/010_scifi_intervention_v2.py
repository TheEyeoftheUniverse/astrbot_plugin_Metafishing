"""Sci-Fi Intervention V2 schema changes."""

from __future__ import annotations

import sqlite3


def _has_column(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def up(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_scifi_state (
            user_id                        TEXT PRIMARY KEY,
            research_points                INTEGER NOT NULL DEFAULT 0,
            abyss_compression_level        INTEGER NOT NULL DEFAULT 0 CHECK(abyss_compression_level BETWEEN 0 AND 5),
            fate_severance_level           INTEGER NOT NULL DEFAULT 0 CHECK(fate_severance_level BETWEEN 0 AND 5),
            resonance_dampening_level      INTEGER NOT NULL DEFAULT 0 CHECK(resonance_dampening_level BETWEEN 0 AND 5),
            apex_protocol                  TEXT CHECK(apex_protocol IN ('singularity','abyss_unity','fate_solitude','resonance_summit') OR apex_protocol IS NULL),
            apex_acquired_at               TEXT,
            last_recompose_at              TEXT,
            total_research_points_earned   INTEGER NOT NULL DEFAULT 0,
            total_append_triggered         INTEGER NOT NULL DEFAULT 0,
            created_at                     TEXT NOT NULL,
            updated_at                     TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scifi_event_log (
            log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            event_detail  TEXT,
            occurred_at   TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES user_scifi_state(user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scifi_event_log_user_time ON scifi_event_log(user_id, occurred_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scifi_event_log_type_time ON scifi_event_log(event_type, occurred_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_scifi_state_apex ON user_scifi_state(apex_protocol)"
    )

    if not _has_column(cursor, "user_cthulhu_state", "sci_fi_apex_abyss_unity"):
        cursor.execute(
            "ALTER TABLE user_cthulhu_state ADD COLUMN sci_fi_apex_abyss_unity INTEGER NOT NULL DEFAULT 0"
        )

    if not _has_column(cursor, "user_cultivation", "sci_fi_apex_fate_solitude"):
        cursor.execute(
            "ALTER TABLE user_cultivation ADD COLUMN sci_fi_apex_fate_solitude INTEGER NOT NULL DEFAULT 0"
        )

    if not _has_column(cursor, "user_cthulhu_state", "sci_fi_intervention_level"):
        cursor.execute(
            "ALTER TABLE user_cthulhu_state ADD COLUMN sci_fi_intervention_level INTEGER NOT NULL DEFAULT 0"
        )

    if not _has_column(cursor, "user_cultivation", "sci_fi_intervention_level"):
        cursor.execute(
            "ALTER TABLE user_cultivation ADD COLUMN sci_fi_intervention_level INTEGER NOT NULL DEFAULT 0"
        )

    cursor.execute(
        """
        INSERT OR IGNORE INTO items
        (item_id, name, description, rarity, effect_description, cost, is_consumable, effect_type, effect_payload)
        VALUES (
            58,
            '协议重写芯',
            '重写当前觉醒协议的稀缺芯片。使用后仅清空觉醒协议，不退还科研点与分支等级。',
            8,
            '用于 /重写协议 或科幻页面重置当前觉醒协议。',
            0,
            1,
            'NONE',
            '{}'
        )
        """
    )


def down(cursor: sqlite3.Cursor) -> None:
    cursor.execute("DROP INDEX IF EXISTS idx_user_scifi_state_apex")
    cursor.execute("DROP INDEX IF EXISTS idx_scifi_event_log_type_time")
    cursor.execute("DROP INDEX IF EXISTS idx_scifi_event_log_user_time")
    cursor.execute("DROP TABLE IF EXISTS scifi_event_log")
    cursor.execute("DROP TABLE IF EXISTS user_scifi_state")
