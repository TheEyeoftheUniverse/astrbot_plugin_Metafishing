import sqlite3


def up(cursor: sqlite3.Cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_zone_stays (
            user_id TEXT NOT NULL,
            zone_id INTEGER NOT NULL,
            pass_item_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, zone_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (zone_id) REFERENCES fishing_zones(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_zone_stays_expires_at ON user_zone_stays(expires_at)")


def down(cursor: sqlite3.Cursor):
    cursor.execute("DROP INDEX IF EXISTS idx_user_zone_stays_expires_at")
    cursor.execute("DROP TABLE IF EXISTS user_zone_stays")
