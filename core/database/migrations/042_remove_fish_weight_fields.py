import sqlite3

from astrbot.api import logger


def _drop_column_if_exists(cursor: sqlite3.Cursor, table: str, column: str) -> None:
    cursor.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cursor.fetchall()}
    if column not in columns:
        return
    cursor.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    logger.info(f"已移除 {table}.{column}")


def up(cursor: sqlite3.Cursor):
    """移除鱼体重相关字段。"""
    _drop_column_if_exists(cursor, "fish", "max_weight")
    _drop_column_if_exists(cursor, "fish", "min_weight")
    _drop_column_if_exists(cursor, "users", "total_weight_caught")
    _drop_column_if_exists(cursor, "fishing_records", "weight")
    _drop_column_if_exists(cursor, "user_fish_stats", "max_weight")
    _drop_column_if_exists(cursor, "user_fish_stats", "min_weight")
    _drop_column_if_exists(cursor, "user_fish_stats", "total_weight")
    cursor.execute(
        "UPDATE titles SET name = ?, description = ?, display_format = ? WHERE title_id = ?",
        ("老练钓手", "累计钓鱼次数达到进阶目标", "{username}, 老练钓手!", 15),
    )
    cursor.execute(
        "UPDATE titles SET name = ?, description = ?, display_format = ? WHERE title_id = ?",
        ("远航钓手", "累计钓鱼次数达到资深目标", "{username}, 远航钓手!", 16),
    )


def down(cursor: sqlite3.Cursor):
    """回滚时仅恢复列结构，历史体重数据无法重建。"""
    cursor.execute("PRAGMA table_info(fish)")
    fish_columns = {row[1] for row in cursor.fetchall()}
    if "min_weight" not in fish_columns:
        cursor.execute("ALTER TABLE fish ADD COLUMN min_weight INTEGER NOT NULL DEFAULT 1")
    if "max_weight" not in fish_columns:
        cursor.execute("ALTER TABLE fish ADD COLUMN max_weight INTEGER NOT NULL DEFAULT 100")

    cursor.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in cursor.fetchall()}
    if "total_weight_caught" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN total_weight_caught INTEGER DEFAULT 0")

    cursor.execute("PRAGMA table_info(fishing_records)")
    record_columns = {row[1] for row in cursor.fetchall()}
    if "weight" not in record_columns:
        cursor.execute("ALTER TABLE fishing_records ADD COLUMN weight INTEGER NOT NULL DEFAULT 0")

    cursor.execute("PRAGMA table_info(user_fish_stats)")
    stat_columns = {row[1] for row in cursor.fetchall()}
    if "max_weight" not in stat_columns:
        cursor.execute("ALTER TABLE user_fish_stats ADD COLUMN max_weight INTEGER NOT NULL DEFAULT 0")
    if "min_weight" not in stat_columns:
        cursor.execute("ALTER TABLE user_fish_stats ADD COLUMN min_weight INTEGER NOT NULL DEFAULT 0")
    if "total_weight" not in stat_columns:
        cursor.execute("ALTER TABLE user_fish_stats ADD COLUMN total_weight INTEGER NOT NULL DEFAULT 0")
