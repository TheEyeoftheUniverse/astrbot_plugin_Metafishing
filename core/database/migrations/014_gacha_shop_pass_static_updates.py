"""迁移 014：补写卡池鱼饵与 shop5-8 一次性通行证静态数据。"""

from __future__ import annotations

import sqlite3


GACHA_BAIT_UPDATES = (
    (20, 26),
    (16, 27),
    (21, 53),
    (17, 54),
    (22, 80),
    (18, 81),
    (19, 107),
    (15, 108),
)

SHOP_ITEMS = (
    (
        129,
        5,
        "工厂污染区域通行证",
        "你被允许前往该海域的放行理由是能通过钓鱼净化水质。",
    ),
    (
        130,
        6,
        "化龙碎片",
        "化为龙的鲤鱼褪下的残壳碎片。",
    ),
    (
        131,
        7,
        "千面碎片",
        "千百种生物皮肤的细小碎片。",
    ),
    (
        132,
        8,
        "龙窟金币",
        "因沾染到龙息，有些许融化的普通金币。",
    ),
)

SHOP_COSTS = (
    (308, 129, 10, 47),
    (309, 129, 5, 48),
    (310, 130, 10, 49),
    (311, 130, 5, 50),
    (312, 131, 10, 51),
    (313, 131, 5, 52),
    (314, 132, 10, 53),
    (315, 132, 5, 54),
)

SHOP_REWARDS = (
    (194, 129, 14),
    (195, 130, 15),
    (196, 131, 16),
    (197, 132, 17),
)


def up(cursor: sqlite3.Cursor) -> None:
    cursor.executemany(
        """
        UPDATE gacha_pool_items
        SET item_id = ?
        WHERE gacha_pool_item_id = ? AND item_type = 'bait'
        """,
        GACHA_BAIT_UPDATES,
    )

    cursor.executemany(
        """
        INSERT INTO shop_items (
            item_id, shop_id, name, description, category,
            stock_total, stock_sold, per_user_limit, per_user_daily_limit,
            is_active, start_time, end_time, sort_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'general', NULL, 0, NULL, NULL, 1, NULL, NULL, 100, '2026-05-23 22:30:00', NULL)
        ON CONFLICT(item_id) DO UPDATE SET
            shop_id = excluded.shop_id,
            name = excluded.name,
            description = excluded.description,
            category = excluded.category,
            is_active = excluded.is_active,
            sort_order = excluded.sort_order
        """,
        SHOP_ITEMS,
    )

    cursor.executemany(
        """
        INSERT INTO shop_item_costs (
            cost_id, item_id, cost_type, cost_amount, cost_item_id,
            cost_relation, group_id, quality_level
        )
        VALUES (?, ?, 'item', ?, ?, 'or', 1, 0)
        ON CONFLICT(cost_id) DO UPDATE SET
            item_id = excluded.item_id,
            cost_type = excluded.cost_type,
            cost_amount = excluded.cost_amount,
            cost_item_id = excluded.cost_item_id,
            cost_relation = excluded.cost_relation,
            group_id = excluded.group_id,
            quality_level = excluded.quality_level
        """,
        SHOP_COSTS,
    )

    cursor.executemany(
        """
        INSERT INTO shop_item_rewards (
            reward_id, item_id, reward_type, reward_item_id,
            reward_quantity, reward_refine_level, quality_level
        )
        VALUES (?, ?, 'item', ?, 1, NULL, 0)
        ON CONFLICT(reward_id) DO UPDATE SET
            item_id = excluded.item_id,
            reward_type = excluded.reward_type,
            reward_item_id = excluded.reward_item_id,
            reward_quantity = excluded.reward_quantity,
            reward_refine_level = excluded.reward_refine_level,
            quality_level = excluded.quality_level
        """,
        SHOP_REWARDS,
    )


def down(cursor: sqlite3.Cursor) -> None:
    cursor.executemany(
        """
        UPDATE gacha_pool_items
        SET item_id = ?
        WHERE gacha_pool_item_id = ? AND item_type = 'bait'
        """,
        (
            (16, 26),
            (12, 27),
            (17, 53),
            (13, 54),
            (18, 80),
            (14, 81),
            (15, 107),
            (11, 108),
        ),
    )
    cursor.execute("DELETE FROM shop_item_costs WHERE cost_id BETWEEN 308 AND 315")
    cursor.execute("DELETE FROM shop_item_rewards WHERE reward_id BETWEEN 194 AND 197")
    cursor.execute("DELETE FROM shop_items WHERE item_id BETWEEN 129 AND 132")
