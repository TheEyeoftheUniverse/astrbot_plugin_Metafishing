import sqlite3


def up(cursor: sqlite3.Cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS exchange_market_snapshots (
            date TEXT PRIMARY KEY,
            supply_demand TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def down(cursor: sqlite3.Cursor):
    cursor.execute("DROP TABLE IF EXISTS exchange_market_snapshots")
