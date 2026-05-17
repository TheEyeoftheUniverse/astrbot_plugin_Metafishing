"""玄幻渡劫玩法 V2：数据库表结构初始化。

新增三张表：
- user_cultivation: 玩家修行状态（修为 / 境界 / 失败保护 / 天成执念 / 日次数）
- tribulation_events: 渡劫事件主表
- tribulation_participants: 护法 / 观道记录
"""

import sqlite3


def up(cursor: sqlite3.Cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_cultivation (
            user_id                       TEXT PRIMARY KEY,
            current_realm                 TEXT NOT NULL DEFAULT 'lianqi',
            current_realm_quality         TEXT,
            accumulated_xiuwei            INTEGER NOT NULL DEFAULT 0,
            consecutive_failures          INTEGER NOT NULL DEFAULT 0,
            realm_history                 TEXT,
            tiancheng_protection          TEXT,
            daily_observer_reward_count   INTEGER NOT NULL DEFAULT 0,
            daily_guard_reward_count      INTEGER NOT NULL DEFAULT 0,
            daily_count_reset_at          TEXT,
            sci_fi_intervention_level     INTEGER NOT NULL DEFAULT 0,
            updated_at                    TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tribulation_events (
            event_id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id              TEXT NOT NULL,
            target_realm         TEXT NOT NULL,
            mode                 TEXT NOT NULL,
            status               TEXT NOT NULL,
            equipment_snapshot   TEXT,
            items_invested       TEXT,
            accumulated_xiuwei   INTEGER NOT NULL DEFAULT 0,
            created_at           TEXT NOT NULL,
            announce_at          TEXT,
            scheduled_at         TEXT NOT NULL,
            resolved_at          TEXT,
            result               TEXT,
            quality              TEXT,
            final_success_rate   REAL,
            final_total_weight   INTEGER,
            daowang_collected    INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tribulation_events_user_status "
        "ON tribulation_events(user_id, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tribulation_events_schedule "
        "ON tribulation_events(scheduled_at, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tribulation_events_announce "
        "ON tribulation_events(announce_at, status)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tribulation_participants (
            participant_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id         INTEGER NOT NULL,
            user_id          TEXT NOT NULL,
            type             TEXT NOT NULL,
            joined_at        TEXT NOT NULL,
            reward_paid      INTEGER NOT NULL DEFAULT 0,
            reward_amount    TEXT,
            is_effective     INTEGER NOT NULL DEFAULT 1,
            xiuwei_granted   INTEGER NOT NULL DEFAULT 0,
            UNIQUE(event_id, user_id),
            FOREIGN KEY (event_id) REFERENCES tribulation_events(event_id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tribulation_participants_event "
        "ON tribulation_participants(event_id, type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tribulation_participants_user "
        "ON tribulation_participants(user_id)"
    )


def down(cursor: sqlite3.Cursor):
    cursor.execute("DROP INDEX IF EXISTS idx_tribulation_participants_user")
    cursor.execute("DROP INDEX IF EXISTS idx_tribulation_participants_event")
    cursor.execute("DROP TABLE IF EXISTS tribulation_participants")
    cursor.execute("DROP INDEX IF EXISTS idx_tribulation_events_announce")
    cursor.execute("DROP INDEX IF EXISTS idx_tribulation_events_schedule")
    cursor.execute("DROP INDEX IF EXISTS idx_tribulation_events_user_status")
    cursor.execute("DROP TABLE IF EXISTS tribulation_events")
    cursor.execute("DROP TABLE IF EXISTS user_cultivation")
