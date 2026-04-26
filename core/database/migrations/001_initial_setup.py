import sqlite3
from pathlib import Path

from astrbot.api import logger

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema_latest.sql"


def up(cursor: sqlite3.Cursor):
    """应用当前项目的最新初始 schema。"""
    logger.debug("正在执行 001_initial_setup: 应用最新初始 schema...")
    cursor.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def down(cursor: sqlite3.Cursor):
    """新项目基线迁移不提供回滚。"""
    logger.warning("001_initial_setup 不提供回滚。")
