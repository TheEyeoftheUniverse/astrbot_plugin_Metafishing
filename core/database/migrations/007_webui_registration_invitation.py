from astrbot.api import logger


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(str(row[1]) == column_name for row in cursor.fetchall())


def up(cursor):
    logger.debug("正在执行 007_webui_registration_invitation")

    if not _column_exists(cursor, "users", "password_hash"):
        cursor.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if not _column_exists(cursor, "users", "auth_source"):
        cursor.execute("ALTER TABLE users ADD COLUMN auth_source TEXT")
    if not _column_exists(cursor, "users", "invited_by_user_id"):
        cursor.execute("ALTER TABLE users ADD COLUMN invited_by_user_id TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS invitation_codes (
            code TEXT PRIMARY KEY,
            issuer_user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            used_by_user_id TEXT,
            used_at INTEGER,
            FOREIGN KEY (issuer_user_id) REFERENCES users(user_id),
            FOREIGN KEY (used_by_user_id) REFERENCES users(user_id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_invitation_issuer ON invitation_codes(issuer_user_id)"
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_invitation_unused
        ON invitation_codes(used_by_user_id)
        WHERE used_by_user_id IS NULL
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS web_registration_success_audit (
            ip_address TEXT NOT NULL,
            registered_user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (ip_address, registered_user_id),
            FOREIGN KEY (registered_user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_registration_success_day
        ON web_registration_success_audit(ip_address, day)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS web_registration_invite_failures (
            ip_address TEXT PRIMARY KEY,
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_failed_at INTEGER NOT NULL DEFAULT 0,
            blocked_until INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def down(cursor):
    logger.warning("007_webui_registration_invitation 不提供回滚。")
