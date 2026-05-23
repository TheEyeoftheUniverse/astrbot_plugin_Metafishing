"""迁移 013：为 user_cultivation 和 user_cthulhu_state 添加觉醒协议字段。"""

from __future__ import annotations
import sqlite3


def _has_column(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def _add_column_if_missing(
    cursor: sqlite3.Cursor,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    if _has_column(cursor, table_name, column_name):
        return
    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def up(cursor: sqlite3.Cursor) -> None:
    # user_cultivation 表添加四个觉醒协议字段
    _add_column_if_missing(
        cursor,
        "user_cultivation",
        "sci_fi_apex_singularity",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        cursor,
        "user_cultivation",
        "sci_fi_apex_abyss_unity",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        cursor,
        "user_cultivation",
        "sci_fi_apex_fate_solitude",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        cursor,
        "user_cultivation",
        "sci_fi_apex_resonance_summit",
        "INTEGER NOT NULL DEFAULT 0",
    )

    # user_cthulhu_state 表添加两个觉醒协议字段（singularity 和 fate_solitude 影响深渊偏移）
    _add_column_if_missing(
        cursor,
        "user_cthulhu_state",
        "sci_fi_apex_singularity",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        cursor,
        "user_cthulhu_state",
        "sci_fi_apex_fate_solitude",
        "INTEGER NOT NULL DEFAULT 0",
    )


def down(cursor: sqlite3.Cursor) -> None:
    # SQLite 不支持 DROP COLUMN，需要重建表
    # 这里简化处理，实际生产环境可能需要更复杂的回滚逻辑
    cursor.execute(
        """
        CREATE TABLE user_cultivation_backup AS
        SELECT
            user_id, current_realm, current_realm_quality,
            accumulated_xiuwei, consecutive_failures,
            realm_history, tiancheng_protection,
            daily_observer_reward_count, daily_guard_reward_count,
            daily_count_reset_at, sci_fi_intervention_level,
            updated_at
        FROM user_cultivation
        """
    )
    cursor.execute("DROP TABLE user_cultivation")
    cursor.execute("ALTER TABLE user_cultivation_backup RENAME TO user_cultivation")

    cursor.execute(
        """
        CREATE TABLE user_cthulhu_state_backup AS
        SELECT
            user_id, current_san, max_san, pending_san_cap_tokens,
            sci_fi_intervention_level, sci_fi_apex_abyss_unity,
            is_in_deepdive_today, pending_event_id, pending_event_tier,
            pending_event_force_pollute, pending_event_choice,
            pending_event_snapshot, pending_predict_candidates,
            daily_marker, updated_at
        FROM user_cthulhu_state
        """
    )
    cursor.execute("DROP TABLE user_cthulhu_state")
    cursor.execute("ALTER TABLE user_cthulhu_state_backup RENAME TO user_cthulhu_state")
