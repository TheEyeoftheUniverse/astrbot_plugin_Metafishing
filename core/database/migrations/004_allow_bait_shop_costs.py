import sqlite3


def _table_sql(cursor: sqlite3.Cursor, table: str) -> str:
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else ""


def up(cursor: sqlite3.Cursor):
    current_sql = _table_sql(cursor, "shop_item_costs")
    if "'bait'" in current_sql:
        return

    cursor.execute("PRAGMA foreign_keys = OFF")
    cursor.execute(
        """
        CREATE TABLE shop_item_costs_new (
            cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            cost_type TEXT NOT NULL CHECK (cost_type IN ('coins','premium','item','fish','rod','accessory','bait')),
            cost_amount INTEGER NOT NULL CHECK (cost_amount > 0),
            cost_item_id INTEGER,
            cost_relation TEXT DEFAULT 'and' CHECK (cost_relation IN ('and', 'or')),
            group_id INTEGER,
            quality_level INTEGER DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES shop_items(item_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO shop_item_costs_new (
            cost_id, item_id, cost_type, cost_amount, cost_item_id,
            cost_relation, group_id, quality_level
        )
        SELECT
            cost_id, item_id, cost_type, cost_amount, cost_item_id,
            cost_relation, group_id, COALESCE(quality_level, 0)
        FROM shop_item_costs
        """
    )
    cursor.execute("DROP TABLE shop_item_costs")
    cursor.execute("ALTER TABLE shop_item_costs_new RENAME TO shop_item_costs")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shop_item_costs_item ON shop_item_costs(item_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shop_item_costs_type ON shop_item_costs(cost_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shop_item_costs_relation ON shop_item_costs(cost_relation)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shop_item_costs_group ON shop_item_costs(group_id)")
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_shop_item_costs_fish_quality
        ON shop_item_costs(cost_type, cost_item_id, quality_level)
        WHERE cost_type = 'fish'
        """
    )
    cursor.execute("PRAGMA foreign_keys = ON")


def down(cursor: sqlite3.Cursor):
    cursor.execute("PRAGMA foreign_keys = OFF")
    cursor.execute(
        """
        CREATE TABLE shop_item_costs_old (
            cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            cost_type TEXT NOT NULL CHECK (cost_type IN ('coins','premium','item','fish','rod','accessory')),
            cost_amount INTEGER NOT NULL CHECK (cost_amount > 0),
            cost_item_id INTEGER,
            cost_relation TEXT DEFAULT 'and' CHECK (cost_relation IN ('and', 'or')),
            group_id INTEGER,
            quality_level INTEGER DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES shop_items(item_id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO shop_item_costs_old (
            cost_id, item_id, cost_type, cost_amount, cost_item_id,
            cost_relation, group_id, quality_level
        )
        SELECT
            cost_id, item_id, cost_type, cost_amount, cost_item_id,
            cost_relation, group_id, COALESCE(quality_level, 0)
        FROM shop_item_costs
        WHERE cost_type != 'bait'
        """
    )
    cursor.execute("DROP TABLE shop_item_costs")
    cursor.execute("ALTER TABLE shop_item_costs_old RENAME TO shop_item_costs")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shop_item_costs_item ON shop_item_costs(item_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shop_item_costs_type ON shop_item_costs(cost_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shop_item_costs_relation ON shop_item_costs(cost_relation)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shop_item_costs_group ON shop_item_costs(group_id)")
    cursor.execute("PRAGMA foreign_keys = ON")
