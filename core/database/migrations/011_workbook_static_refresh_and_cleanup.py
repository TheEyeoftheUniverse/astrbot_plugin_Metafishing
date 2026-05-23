from pathlib import Path
import re

from astrbot.api import logger


WORKBOOK_STATIC_DIR = Path(__file__).resolve().parent.parent / "seeds" / "workbook_static"
SUPPLEMENTAL_STATIC_PATH = Path(__file__).resolve().parent.parent / "seeds" / "supplemental_static.sql"
INSERT_RE = re.compile(r'^INSERT INTO "([^"]+)" \((.+)\) VALUES \((.+)\);$')

DELETE_ORDER = [
    "shop_item_costs",
    "shop_item_rewards",
    "shop_items",
    "shops",
    "gacha_pool_items",
    "gacha_pools",
    "zone_fish_mapping",
    "exchange_commodity_rules",
    "aquarium_upgrades",
]
UPSERT_KEYS = {
    "fish": ("fish_id",),
    "rods": ("rod_id",),
    "accessories": ("accessory_id",),
    "baits": ("bait_id",),
    "items": ("item_id",),
    "titles": ("title_id",),
    "commodities": ("commodity_id",),
    "exchange_commodity_rules": ("commodity_id",),
    "fishing_zones": ("id",),
    "zone_fish_mapping": ("zone_id", "fish_id"),
    "gacha_pools": ("gacha_pool_id",),
    "gacha_pool_items": ("gacha_pool_item_id",),
    "shops": ("shop_id",),
    "shop_items": ("item_id",),
    "shop_item_costs": ("cost_id",),
    "shop_item_rewards": ("reward_id",),
    "aquarium_upgrades": ("upgrade_id",),
    "exchange_prices": ("price_id",),
}


def _load_sql_files() -> list[Path]:
    if not WORKBOOK_STATIC_DIR.exists():
        raise FileNotFoundError(f"workbook static seed dir not found: {WORKBOOK_STATIC_DIR}")
    return sorted(path for path in WORKBOOK_STATIC_DIR.iterdir() if path.suffix == ".sql")


def _ensure_column(cursor, table: str, column: str, ddl: str) -> None:
    cursor.execute(f'PRAGMA table_info("{table}")')
    columns = {row[1] for row in cursor.fetchall()}
    if column not in columns:
        cursor.execute(ddl)


def _to_upsert_sql(sql_text: str) -> str:
    lines = []
    for raw_line in sql_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            lines.append(raw_line)
            continue
        match = INSERT_RE.match(line)
        if not match:
            lines.append(raw_line)
            continue
        table_name, columns_text, values_text = match.groups()
        columns = [column.strip().strip('"') for column in columns_text.split(",")]
        conflict_keys = UPSERT_KEYS.get(table_name)
        if not conflict_keys:
            lines.append(raw_line)
            continue
        update_columns = [column for column in columns if column not in conflict_keys]
        if update_columns:
            assignments = ", ".join(f'"{column}" = excluded."{column}"' for column in update_columns)
            conflict_sql = ", ".join(f'"{column}"' for column in conflict_keys)
            lines.append(
                f'INSERT INTO "{table_name}" ({columns_text}) VALUES ({values_text}) '
                f'ON CONFLICT({conflict_sql}) DO UPDATE SET {assignments};'
            )
        else:
            lines.append(
                f'INSERT INTO "{table_name}" ({columns_text}) VALUES ({values_text}) '
                f'ON CONFLICT DO NOTHING;'
            )
    return "\n".join(lines)


def up(cursor):
    logger.info("正在执行 011_workbook_static_refresh_and_cleanup")

    _ensure_column(
        cursor,
        "user_cthulhu_state",
        "pending_event_snapshot",
        'ALTER TABLE user_cthulhu_state ADD COLUMN pending_event_snapshot TEXT',
    )

    for table in DELETE_ORDER:
        cursor.execute(f'DELETE FROM "{table}"')

    cursor.execute("DROP TABLE IF EXISTS user_achievement_progress")

    for sql_path in _load_sql_files():
        logger.debug(f"导入 workbook 静态表: {sql_path.name}")
        cursor.executescript(_to_upsert_sql(sql_path.read_text(encoding="utf-8")))

    if SUPPLEMENTAL_STATIC_PATH.exists():
        cursor.executescript(SUPPLEMENTAL_STATIC_PATH.read_text(encoding="utf-8"))


def down(cursor):
    logger.warning("011_workbook_static_refresh_and_cleanup 不提供回滚。")
