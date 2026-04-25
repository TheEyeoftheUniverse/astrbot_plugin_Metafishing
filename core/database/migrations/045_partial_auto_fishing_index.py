import sqlite3


def up(cursor: sqlite3.Cursor):
    cursor.execute("DROP INDEX IF EXISTS idx_users_auto_fishing_enabled")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_auto_fishing_enabled "
        "ON users(auto_fishing_enabled) WHERE auto_fishing_enabled = 1"
    )


def down(cursor: sqlite3.Cursor):
    cursor.execute("DROP INDEX IF EXISTS idx_users_auto_fishing_enabled")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_auto_fishing_enabled "
        "ON users(auto_fishing_enabled)"
    )
