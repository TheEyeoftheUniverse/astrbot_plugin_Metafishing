import random
from datetime import datetime, date, timedelta, timezone
from typing import List, Tuple, Any

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# 获取当前的UTC+8时间（naive datetime，无时区信息）
def get_now() -> datetime:
    # 返回naive datetime以避免时区比较问题
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

def get_today() -> date:
    return get_now().date()

def get_last_reset_time(reset_hour: int = 0) -> datetime:
    """
    获取最近一次刷新时间点
    
    Args:
        reset_hour: 每日刷新的小时数（0-23），默认为0表示0点刷新
    
    Returns:
        最近一次刷新的时间点（datetime对象）
    
    Example:
        如果 reset_hour=6，当前时间是今天8点，返回今天6点
        如果 reset_hour=6，当前时间是今天5点，返回昨天6点
    """
    now = get_now()
    # 创建今天的刷新时间点
    today_reset = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    
    # 如果当前时间已经过了今天的刷新时间点，返回今天的刷新时间点
    if now >= today_reset:
        return today_reset
    else:
        # 否则返回昨天的刷新时间点
        return today_reset - timedelta(days=1)

def get_fish_template(new_fish_list, value_bonus):
    """
    按同星级价值池规则抽取鱼模板。
    - 同星级内不再直接按 base_value 加权。
    - 先按 base_value 划分低/中/高三档价值池。
    - 默认池概率为 20% / 60% / 20%。
    - 价值加成只允许把中价值池概率转移给高价值池。
    - 低价值池概率始终保留，避免任务鱼/低价值鱼绝迹。
    """
    if not new_fish_list:
        return None

    if len(new_fish_list) == 1:
        return new_fish_list[0]

    sorted_fish = sorted(new_fish_list, key=lambda fish: (fish.base_value, fish.fish_id))
    total_count = len(sorted_fish)

    low_end = max(1, (total_count + 4) // 5)
    high_start = min(total_count - 1, (total_count * 4) // 5)
    if high_start < low_end:
        high_start = low_end

    pools = {
        "low": sorted_fish[:low_end],
        "mid": sorted_fish[low_end:high_start],
        "high": sorted_fish[high_start:],
    }
    pool_weights = {
        "low": 0.2,
        "mid": 0.6 - min(max(value_bonus, 0.0), 0.6),
        "high": 0.2 + min(max(value_bonus, 0.0), 0.6),
    }

    available_pools = [
        fish_pool
        for pool_name, fish_pool in pools.items()
        if fish_pool and pool_weights[pool_name] > 0
    ]
    available_weights = [
        pool_weights[pool_name]
        for pool_name, fish_pool in pools.items()
        if fish_pool and pool_weights[pool_name] > 0
    ]

    chosen_pool = random.choices(available_pools, weights=available_weights, k=1)[0]
    return random.choice(chosen_pool)

def calculate_after_refine(before_value: float, refine_level: int, rarity: int = None) -> float:
    """
    计算经过精炼后的值
    根据装备稀有度使用不同的精炼加成比例
    
    精炼加成比例：
    - 1-2★装备: 15%/级 (让低星装备有更多成长空间)
    - 3★装备: 15%/级
    - 4★装备: 12%/级
    - 5★装备: 8%/级
    - 6★装备: 5%/级
    - 7★+装备: 3%/级
    
    Args:
        before_value: 精炼前的值
        refine_level: 精炼等级 (1-10)
        rarity: 装备稀有度 (如果不提供则使用默认10%)
    
    Returns:
        精炼后的值
    """
    # 如果没有提供稀有度，使用旧的10%逻辑保持兼容性
    if rarity is None:
        bonus_per_level = 0.1
    else:
        # 基于稀有度的差异化加成
        if rarity <= 3:
            bonus_per_level = 0.15  # 15%/级
        elif rarity == 4:
            bonus_per_level = 0.12  # 12%/级
        elif rarity == 5:
            bonus_per_level = 0.08  # 8%/级
        elif rarity == 6:
            bonus_per_level = 0.05  # 5%/级
        else:  # 7星+
            bonus_per_level = 0.03  # 3%/级
    
    # 计算总加成
    effective_refine_level = refine_level - 1 if refine_level <= 10 else 9
    total_bonus = bonus_per_level * effective_refine_level
    
    # 应用加成
    if before_value < 1:
        return before_value * (1 + total_bonus)
    return (before_value - 1) * (1 + total_bonus) + 1
