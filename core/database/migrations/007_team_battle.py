"""魔幻团战玩法 V2：数据库表结构初始化。

新增 5 张表：
- team_battle_boss             : 当前活跃 Boss（单只）
- team_battle_damage           : 玩家对 Boss 的累计伤害
- team_battle_reward_inventory : 未领奖励背包
- team_battle_history_kills    : 10 星 Boss 击杀历史
- team_battle_daily_settle     : 每日结算幂等标记
"""

import sqlite3


def up(cursor: sqlite3.Cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS team_battle_boss (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            region_key          TEXT NOT NULL,
            boss_name           TEXT NOT NULL,
            fish_id             INTEGER NOT NULL,
            boss_star           INTEGER NOT NULL,
            max_hp              INTEGER NOT NULL,
            current_hp          INTEGER NOT NULL,
            image_path          TEXT,
            intro_story         TEXT,
            intro_quote         TEXT,
            stages_triggered    TEXT NOT NULL DEFAULT '[]',
            spawned_at          TEXT NOT NULL,
            killed_at           TEXT,
            is_active           INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_battle_boss_active "
        "ON team_battle_boss(is_active, spawned_at DESC)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS team_battle_damage (
            boss_id                     INTEGER NOT NULL,
            user_id                     TEXT NOT NULL,
            total_damage                INTEGER NOT NULL DEFAULT 0,
            last_settled_at             TEXT,
            is_leader_at_last_settle    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (boss_id, user_id)
        )
        """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_battle_damage_rank "
        "ON team_battle_damage(boss_id, total_damage DESC)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS team_battle_reward_inventory (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            boss_id         INTEGER NOT NULL,
            reward_type     TEXT NOT NULL,
            item_id         INTEGER,
            quantity        INTEGER NOT NULL DEFAULT 1,
            source_stage    TEXT NOT NULL,
            source_label    TEXT,
            granted_at      TEXT NOT NULL,
            claimed_at      TEXT,
            expired_at      TEXT
        )
        """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_battle_reward_unclaimed "
        "ON team_battle_reward_inventory(user_id, claimed_at, expired_at)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS team_battle_history_kills (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            boss_id                 INTEGER NOT NULL,
            region_key              TEXT NOT NULL,
            boss_name               TEXT NOT NULL,
            boss_star               INTEGER NOT NULL,
            finisher_user_id        TEXT,
            final_rank_snapshot     TEXT NOT NULL,
            killed_at               TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_battle_history_killed_at "
        "ON team_battle_history_kills(killed_at DESC)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS team_battle_daily_settle (
            daily_marker    TEXT PRIMARY KEY,
            boss_id         INTEGER,
            settled_at      TEXT NOT NULL,
            settle_summary  TEXT
        )
        """
    )
