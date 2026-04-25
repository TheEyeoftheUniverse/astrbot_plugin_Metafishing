import sqlite3


def up(cursor: sqlite3.Cursor):
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_auto_fishing_enabled ON users(auto_fishing_enabled)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fishing_records_timestamp ON fishing_records(timestamp)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_gacha_records_timestamp ON gacha_records(timestamp)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_wipe_bomb_log_timestamp ON wipe_bomb_log(timestamp)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_check_ins_date ON check_ins(check_in_date)"
    )


def down(cursor: sqlite3.Cursor):
    cursor.execute("DROP INDEX IF EXISTS idx_users_auto_fishing_enabled")
    cursor.execute("DROP INDEX IF EXISTS idx_fishing_records_timestamp")
    cursor.execute("DROP INDEX IF EXISTS idx_gacha_records_timestamp")
    cursor.execute("DROP INDEX IF EXISTS idx_wipe_bomb_log_timestamp")
    cursor.execute("DROP INDEX IF EXISTS idx_check_ins_date")
