"""玄幻渡劫玩法 V2：所有数值常量。

数据来源：玄幻渡劫玩法V2策划案.md §15 节。
"""

from __future__ import annotations

# 修行区域
CULTIVATION_ZONE_ID = 6

# §15.1 鱼星级 → 修为
XIUWEI_BY_RARITY = {
    1: 1,
    2: 2,
    3: 4,
    4: 7,
    5: 11,
    6: 16,
    7: 24,
    8: 35,
    9: 49,
    10: 68,
}

# §15.2 各境界圆满修为（炼气=400 视为初始境界圆满线）
REALM_XIUWEI_CAP = {
    "lianqi": 400,
    "zhuji": 1600,
    "jindan": 2800,
    "yuanying": 4200,
    "huashen": 5600,
}

# §15.4 基础渡劫成功率（百分比）
BASE_SUCCESS_RATE = {
    "zhuji": 70.0,
    "jindan": 55.0,
    "yuanying": 40.0,
    "huashen": 28.0,
}

# §15.5 低星材料在高境界的成功率贡献上限（百分比）
# key = 目标境界, sub = 星级分桶 ('low56' / 'star7' / 'star8' / 'star910')
ITEM_SUCCESS_BONUS_CAP = {
    "zhuji":    {"low56": 12.0, "star7": 18.0, "star8": 10.0, "star910": 6.0},
    "jindan":   {"low56": 8.0,  "star7": 15.0, "star8": 18.0, "star910": 12.0},
    "yuanying": {"low56": 5.0,  "star7": 10.0, "star8": 18.0, "star910": 20.0},
    "huashen":  {"low56": 3.0,  "star7": 8.0,  "star8": 15.0, "star910": 24.0},
}

# §15.6 渡劫品基础权重
ITEM_BASE_WEIGHT_BY_RARITY = {
    5: 1,
    6: 2,
    7: 4,
    8: 7,
    9: 12,
    10: 18,
}

# §15.7 品级折算系数
QUALITY_COEFFICIENT = {
    "zhuji":    {"low56": 1.0, "star7": 1.0, "star8": 1.0, "star910": 1.1},
    "jindan":   {"low56": 0.7, "star7": 1.0, "star8": 1.1, "star910": 1.2},
    "yuanying": {"low56": 0.4, "star7": 0.7, "star8": 1.0, "star910": 1.3},
    "huashen":  {"low56": 0.2, "star7": 0.5, "star8": 0.8, "star910": 1.4},
}

# §15.8 品级冲击区间：每条 (min_weight, quality, hit_rate%)
# 列表按 min_weight 升序，凡蜕为保底，由查表函数负责返回。
QUALITY_TIER_TABLE = {
    "zhuji": [
        (20, "lingyun", 45.0),
        (50, "zhenyi", 30.0),
        (90, "tiancheng", 8.0),
    ],
    "jindan": [
        (30, "lingyun", 40.0),
        (70, "zhenyi", 24.0),
        (130, "tiancheng", 6.0),
    ],
    "yuanying": [
        (45, "lingyun", 36.0),
        (100, "zhenyi", 18.0),
        (180, "tiancheng", 4.0),
    ],
    "huashen": [
        (60, "lingyun", 32.0),
        (140, "zhenyi", 14.0),
        (260, "tiancheng", 3.0),
    ],
}

# 品级降档表（命中失败时）
QUALITY_DOWNGRADE = {
    "tiancheng": "zhenyi",
    "zhenyi": "lingyun",
    "lingyun": "fanxue",
    "fanxue": "fanxue",
}

# §12.4 品级属性加成
QUALITY_BONUS_PCT = {
    "fanxue": 0.1,
    "lingyun": 0.2,
    "zhenyi": 0.3,
    "tiancheng": 0.5,
}

# §11 失败保护
FAILURE_BUFF_PER_STACK = 15.0  # 每层 +15%
SELF_SUCCESS_RATE_CAP = 95.0
FINAL_SUCCESS_RATE_CAP = 100.0

# §10 道望失败返还
REFUND_BASE = 0.50
REFUND_PER_DAOWANG = 0.01
REFUND_MAX = 0.80

# §12.3 天成执念
TIANCHENG_PROTECTION_PER_STACK = 5.0   # 每层 +5%
TIANCHENG_PROTECTION_CAP = 50.0         # 封顶 +50%

# §9 日参与奖励上限
DAILY_GUARD_REWARD_CAP = 3
DAILY_OBSERVER_REWARD_CAP = 3

# §9.2 护法奖励池系数
GUARD_BASE_POOL_RATIO = 0.01
GUARD_EXTRA_POOL_RATIO = 0.01

# §9.3 观道修为系数
OBSERVER_XIUWEI_RATIO = 0.08

# §19.2 报名截止时间（结算前 N 分钟）
JOIN_DEADLINE_MINUTES_BEFORE = 5

# §13 境界重修修为继承比例
REPAIR_XIUWEI_INHERIT_RATIO = 0.9


def star_bucket(rarity: int) -> str:
    """将物品 rarity 映射为成功率/系数表的 bucket key。"""
    if rarity in (5, 6):
        return "low56"
    if rarity == 7:
        return "star7"
    if rarity == 8:
        return "star8"
    if rarity in (9, 10):
        return "star910"
    return "low56"


def get_item_base_weight(rarity: int) -> int:
    """渡劫品基础权重；超出 5-10 星视为不可投入（返回 0）。"""
    return int(ITEM_BASE_WEIGHT_BY_RARITY.get(int(rarity), 0))


def get_xiuwei_for_fish_rarity(rarity: int) -> int:
    """钓鱼修为产出。"""
    return int(XIUWEI_BY_RARITY.get(int(rarity), 0))


def get_realm_cap(realm: str) -> int:
    """境界对应的修为上限。"""
    return int(REALM_XIUWEI_CAP.get(realm, 0))
