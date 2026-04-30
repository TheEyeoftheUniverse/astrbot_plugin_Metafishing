import sqlite3


def up(cursor: sqlite3.Cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_pokedex_reward_claims (
            user_id TEXT NOT NULL,
            milestone_percent INTEGER NOT NULL,
            reward_premium INTEGER NOT NULL,
            claimed_unlocked_fish_count INTEGER NOT NULL,
            claimed_total_fish_count INTEGER NOT NULL,
            claimed_at DATETIME NOT NULL,
            PRIMARY KEY (user_id, milestone_percent),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )


def down(cursor: sqlite3.Cursor):
    cursor.execute("DROP TABLE IF EXISTS user_pokedex_reward_claims")
