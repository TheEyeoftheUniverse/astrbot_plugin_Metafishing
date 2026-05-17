import os
import sqlite3
from typing import Any, Dict, Optional


DEFAULT_BUSY_TIMEOUT_SECONDS = 30
DEFAULT_WAL_AUTOCHECKPOINT_PAGES = 512
SQLITE_INT64_MIN = -(2**63)
SQLITE_INT64_MAX = 2**63 - 1
_ALLOWED_CHECKPOINT_MODES = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}


def connect_sqlite(
    db_path: str,
    *,
    detect_types: int = 0,
    row_factory: Optional[Any] = None,
    foreign_keys: bool = True,
    timeout: int = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> sqlite3.Connection:
    """Create a SQLite connection with a consistent WAL-oriented configuration."""
    conn = sqlite3.connect(
        db_path,
        detect_types=detect_types,
        timeout=timeout,
    )
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute(f"PRAGMA busy_timeout = {max(1, int(timeout)) * 1000};")
    conn.execute("PRAGMA journal_mode = WAL;")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute(f"PRAGMA wal_autocheckpoint = {DEFAULT_WAL_AUTOCHECKPOINT_PAGES};")
    return conn


def close_thread_local_connection(local_obj: Any) -> bool:
    """Close and detach a thread-local SQLite connection when present."""
    conn = getattr(local_obj, "connection", None)
    if conn is None:
        return False

    try:
        conn.close()
    finally:
        try:
            delattr(local_obj, "connection")
        except AttributeError:
            pass
    return True


def clamp_sqlite_int(value: Any) -> int:
    """Clamp Python integers into SQLite INTEGER's signed 64-bit range."""
    integer_value = int(value)
    if integer_value < SQLITE_INT64_MIN:
        return SQLITE_INT64_MIN
    if integer_value > SQLITE_INT64_MAX:
        return SQLITE_INT64_MAX
    return integer_value


def get_wal_path(db_path: str) -> str:
    return f"{db_path}-wal"


def get_wal_size_bytes(db_path: str) -> int:
    wal_path = get_wal_path(db_path)
    if not os.path.exists(wal_path):
        return 0
    try:
        return os.path.getsize(wal_path)
    except OSError:
        return 0


def run_wal_checkpoint(
    db_path: str,
    *,
    mode: str = "PASSIVE",
    timeout: int = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> Dict[str, int]:
    normalized_mode = str(mode or "PASSIVE").upper()
    if normalized_mode not in _ALLOWED_CHECKPOINT_MODES:
        raise ValueError(f"Unsupported wal_checkpoint mode: {mode}")

    conn = connect_sqlite(
        db_path,
        foreign_keys=False,
        timeout=timeout,
    )
    try:
        row = conn.execute(f"PRAGMA wal_checkpoint({normalized_mode});").fetchone()
    finally:
        conn.close()

    busy, log_frames, checkpointed_frames = row or (0, 0, 0)
    return {
        "busy": int(busy),
        "log_frames": int(log_frames),
        "checkpointed_frames": int(checkpointed_frames),
    }
