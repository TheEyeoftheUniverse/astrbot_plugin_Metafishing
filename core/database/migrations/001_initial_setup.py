import sqlite3
from pathlib import Path

from astrbot.api import logger

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema_latest.sql"
SEED_PATH = Path(__file__).resolve().parent.parent / "seeds" / "initial_seed.sql"


REQUIRED_SEED_COUNTS = {
    "fish": 672,
    "commodities": 15,
    "exchange_commodity_rules": 15,
    "exchange_prices": 15,
    "rods": 42,
    "accessories": 42,
    "baits": 22,
    "items": 57,
    "fishing_zones": 8,
    "zone_fish_mapping": 862,
    "shops": 8,
    "shop_items": 128,
    "shop_item_costs": 188,
    "shop_item_rewards": 128,
    "gacha_pools": 8,
    "gacha_pool_items": 108,
    "cthulhu_authority": 12,
    "cthulhu_global_pollution": 7,
    "user_cthulhu_state": 0,
}


def _read_required_sql(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"数据库基线文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def _read_seed_sql() -> str:
    lines = []
    for line in _read_required_sql(SEED_PATH).splitlines():
        stripped = line.strip().rstrip(";").upper()
        if stripped in {"BEGIN", "COMMIT"}:
            continue
        lines.append(line)
    return "\n".join(lines)


def _verify_seed_data(cursor: sqlite3.Cursor) -> None:
    for table, expected_count in REQUIRED_SEED_COUNTS.items():
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
    cursor.executescript(_read_seed_sql())
    _verify_seed_data(cursor)


def down(cursor: sqlite3.Cursor):
    """唯一基线迁移不提供回滚。"""
    logger.warning("001_initial_setup 不提供回滚。")
