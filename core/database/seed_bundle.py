from __future__ import annotations

from pathlib import Path


DATABASE_DIR = Path(__file__).resolve().parent
WORKBOOK_STATIC_DIR = DATABASE_DIR / "seeds" / "workbook_static"
SUPPLEMENTAL_STATIC_PATH = DATABASE_DIR / "seeds" / "supplemental_static.sql"

CURRENT_SEED_COUNTS = {
    "fish": 764,
    "commodities": 15,
    "exchange_commodity_rules": 15,
    "exchange_prices": 15,
    "rods": 42,
    "accessories": 42,
    "baits": 22,
    "items": 57,
    "titles": 36,
    "fishing_zones": 8,
    "zone_fish_mapping": 862,
    "shops": 8,
    "shop_items": 128,
    "shop_item_costs": 188,
    "shop_item_rewards": 128,
    "gacha_pools": 8,
    "gacha_pool_items": 108,
    "aquarium_upgrades": 10,
    "cthulhu_authority": 12,
    "cthulhu_global_pollution": 7,
    "user_cthulhu_state": 0,
}


def _read_sql(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"seed file not found: {path}")
    return path.read_text(encoding="utf-8")


def _strip_transaction_lines(sql_text: str) -> str:
    lines = []
    for line in sql_text.splitlines():
        stripped = line.strip().rstrip(";").upper()
        if stripped in {"BEGIN", "COMMIT"}:
            continue
        lines.append(line)
    return "\n".join(lines)


def iter_seed_sql_texts() -> list[str]:
    sql_texts: list[str] = []
    if not WORKBOOK_STATIC_DIR.exists():
        raise FileNotFoundError(f"workbook static dir not found: {WORKBOOK_STATIC_DIR}")

    for path in sorted(WORKBOOK_STATIC_DIR.glob("*.sql")):
        sql_texts.append(_strip_transaction_lines(_read_sql(path)))

    sql_texts.append(_strip_transaction_lines(_read_sql(SUPPLEMENTAL_STATIC_PATH)))
    return sql_texts


def build_seed_sql() -> str:
    return "\n\n".join(iter_seed_sql_texts()) + "\n"
