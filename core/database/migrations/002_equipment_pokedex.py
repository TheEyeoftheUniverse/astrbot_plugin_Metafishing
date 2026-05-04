import sqlite3


def _column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up(cursor: sqlite3.Cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_equipment_stats (
            user_id TEXT NOT NULL,
            equipment_type TEXT NOT NULL CHECK (equipment_type IN ('rod', 'accessory', 'bait')),
            equipment_id INTEGER NOT NULL,
            first_obtained_at DATETIME,
            last_obtained_at DATETIME,
            total_obtained INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, equipment_type, equipment_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_equipment_stats_user
        ON user_equipment_stats(user_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_equipment_stats_equipment
        ON user_equipment_stats(equipment_type, equipment_id)
        """
    )

    if not _column_exists(cursor, "user_pokedex_reward_claims", "reward_type"):
        cursor.execute(
            """
            ALTER TABLE user_pokedex_reward_claims
            ADD COLUMN reward_type TEXT NOT NULL DEFAULT 'premium'
            """
        )
    if not _column_exists(cursor, "user_pokedex_reward_claims", "reward_amount"):
        cursor.execute(
            """
            ALTER TABLE user_pokedex_reward_claims
            ADD COLUMN reward_amount INTEGER NOT NULL DEFAULT 0
            """
        )
    cursor.execute(
        """
        UPDATE user_pokedex_reward_claims
        SET reward_type = 'premium',
            reward_amount = reward_premium
        WHERE reward_amount = 0
          AND reward_premium > 0
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_equipment_pokedex_reward_claims (
            user_id TEXT NOT NULL,
            milestone_percent INTEGER NOT NULL,
            reward_type TEXT NOT NULL CHECK (reward_type IN ('coins', 'premium')),
            reward_amount INTEGER NOT NULL,
            claimed_unlocked_equipment_count INTEGER NOT NULL,
            claimed_total_equipment_count INTEGER NOT NULL,
            claimed_at DATETIME NOT NULL,
            PRIMARY KEY (user_id, milestone_percent),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )


def down(cursor: sqlite3.Cursor):
    cursor.execute("DROP TABLE IF EXISTS user_equipment_pokedex_reward_claims")
    cursor.execute("DROP TABLE IF EXISTS user_equipment_stats")
