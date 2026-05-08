import sqlite3


def up(cursor: sqlite3.Cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS aquarium_income_pending (
            user_id TEXT NOT NULL,
            window_date TEXT NOT NULL,
            window_time TEXT NOT NULL,
            raw_score INTEGER NOT NULL DEFAULT 0,
            equipment_multiplier REAL NOT NULL DEFAULT 1.0,
            randomness REAL NOT NULL DEFAULT 1.0,
            computed_amount INTEGER NOT NULL DEFAULT 0,
            capped_amount INTEGER NOT NULL DEFAULT 0,
            fish_snapshot TEXT,
            created_at TEXT NOT NULL,
            claimed_at TEXT,
            PRIMARY KEY (user_id, window_date, window_time),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_aquarium_income_pending_user_unclaimed "
        "ON aquarium_income_pending(user_id) WHERE claimed_at IS NULL"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_aquarium_income_pending_window_date "
        "ON aquarium_income_pending(window_date)"
    )


def down(cursor: sqlite3.Cursor):
    cursor.execute("DROP INDEX IF EXISTS idx_aquarium_income_pending_window_date")
    cursor.execute("DROP INDEX IF EXISTS idx_aquarium_income_pending_user_unclaimed")
    cursor.execute("DROP TABLE IF EXISTS aquarium_income_pending")
