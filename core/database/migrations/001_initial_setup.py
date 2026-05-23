import sqlite3
import importlib.util
from pathlib import Path

from astrbot.api import logger

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema_latest.sql"
SEED_BUNDLE_PATH = Path(__file__).resolve().parent.parent / "seed_bundle.py"


def _load_seed_bundle():
    spec = importlib.util.spec_from_file_location("metafishing_seed_bundle", SEED_BUNDLE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载种子模块: {SEED_BUNDLE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_seed_bundle = _load_seed_bundle()
build_seed_sql = _seed_bundle.build_seed_sql
CURRENT_SEED_COUNTS = _seed_bundle.CURRENT_SEED_COUNTS


def _read_required_sql(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"数据库基线文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def _verify_seed_data(cursor: sqlite3.Cursor) -> None:
    for table, expected_count in CURRENT_SEED_COUNTS.items():
        cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
        actual_count = cursor.fetchone()[0]
        if actual_count != expected_count:
            raise RuntimeError(
                f"数据库基线数据校验失败: {table} 期望 {expected_count} 行，实际 {actual_count} 行"
            )

    cursor.execute(
        """
        SELECT c.commodity_id
        FROM commodities c
        LEFT JOIN exchange_prices ep
          ON ep.commodity_id = c.commodity_id
         AND ep.update_type = 'initial'
        WHERE ep.commodity_id IS NULL
        ORDER BY c.commodity_id
        """
    )
    missing_prices = [row[0] for row in cursor.fetchall()]
    if missing_prices:
        raise RuntimeError(
            "数据库基线数据校验失败: 缺少期货初始价格 "
            + ", ".join(missing_prices)
        )


def up(cursor: sqlite3.Cursor):
    """应用唯一数据库基线：最新 schema + 最新静态 seed。"""
    logger.debug("正在执行 001_initial_setup: 应用最新 schema 与 seed...")
    cursor.executescript(_read_required_sql(SCHEMA_PATH))
    cursor.executescript(build_seed_sql())
    _verify_seed_data(cursor)


def down(cursor: sqlite3.Cursor):
    """唯一基线迁移不提供回滚。"""
    logger.warning("001_initial_setup 不提供回滚。")
