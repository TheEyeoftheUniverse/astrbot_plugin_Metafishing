import math
import random
import threading
import time
import zlib
from typing import Dict, Any, Optional, List
from datetime import timedelta
from astrbot.api import logger

# 导入仓储接口和领域模型
from ..repositories.abstract_repository import (
    AbstractUserRepository,
    AbstractInventoryRepository,
    AbstractItemTemplateRepository,
    AbstractLogRepository,
    AbstractUserBuffRepository,
)
from ..domain.models import FishingRecord, TaxRecord, FishingZone
from ..services.fishing_zone_service import FishingZoneService
from ..services.wipe_bomb_daily_service import add_wipe_bomb_jackpot
from ..utils import get_now, get_fish_template, get_today, get_last_reset_time, calculate_after_refine

POKEDEX_REWARD_MILESTONES = [
    (5, "coins", 5000),
    (10, "coins", 12000),
    (20, "coins", 30000),
    (25, "premium", 188),
    (30, "premium", 288),
    (40, "premium", 520),
    (50, "premium", 888),
    (60, "premium", 1288),
    (70, "premium", 1888),
    (80, "premium", 2888),
    (90, "premium", 6666),
    (100, "premium", 66666),
]

EQUIPMENT_POKEDEX_REWARD_MILESTONES = POKEDEX_REWARD_MILESTONES

EQUIPMENT_TYPE_META = {
    "rod": {"label": "鱼竿", "order": 0},
    "accessory": {"label": "饰品", "order": 1},
    "bait": {"label": "鱼饵", "order": 2},
}


def _format_reward_text(reward_type: str, reward_amount: int) -> str:
    unit = "金币" if reward_type == "coins" else "钻石"
    return f"{reward_amount} {unit}"


class FishingService:
    """封装核心的钓鱼动作及后台任务"""

    def __init__(
        self,
        user_repo: AbstractUserRepository,
        inventory_repo: AbstractInventoryRepository,
        item_template_repo: AbstractItemTemplateRepository,
        log_repo: AbstractLogRepository,
        buff_repo: AbstractUserBuffRepository,
        fishing_zone_service: FishingZoneService,
        config: Dict[str, Any],
        expedition_service=None,  # 可选的科考服务
    ):
        self.user_repo = user_repo
        self.inventory_repo = inventory_repo
        self.item_template_repo = item_template_repo
        self.log_repo = log_repo
        self.buff_repo = buff_repo
        self.fishing_zone_service = fishing_zone_service
        self.config = config
        self.expedition_service = expedition_service

        # 获取每日刷新时间配置
        self.daily_reset_hour = self.config.get("daily_reset_hour", 0)
        self.last_reset_time = get_last_reset_time(self.daily_reset_hour)
        # 自动钓鱼线程相关属性
        self.auto_fishing_thread: Optional[threading.Thread] = None
        self.auto_fishing_running = False
        # 自动钓鱼分桶轮询：每 tick 只处理 user_id hash 落在当前桶的玩家
        self._auto_bucket_tick = 0
        # 税收线程相关属性
        self.tax_thread: Optional[threading.Thread] = None
        self.tax_running = False
        self.last_tax_reset_time = get_last_reset_time(self.daily_reset_hour)
        self.tax_execution_lock = threading.Lock()  # 防止税收并发执行的锁
        self.tax_start_lock = threading.Lock()  # 防止重复创建税收线程的锁
        self.rare_fish_reset_lock = threading.Lock()  # 防止稀有鱼重置并发执行的锁
        self.log_cleanup_lock = threading.Lock()  # 防止日志清理并发执行的锁
        self.zone_pass_reset_lock = threading.Lock()  # 防止通行证日检并发执行的锁
        self.pokedex_reward_claim_lock = threading.Lock()  # 防止图鉴奖励重复领取
        self.last_zone_pass_check_reset_time = None
        self.last_log_cleanup_reset_time = None
        # 可选的消息通知回调：签名 (target: str, message: str) -> None，用于消息通知
        self._notifier = None
        # 通知目标可配置，默认群聊。可由 config['notifications']['relocation_target'] 覆盖
        notifications_cfg = self.config.get("notifications", {}) if isinstance(self.config, dict) else {}
        self._notification_target = notifications_cfg.get("relocation_target", "group")

    def register_notifier(self, notifier, default_target: Optional[str] = None):
        """
        注册一个用于发送系统消息的回调（如群聊推送）。
        回调应为同步函数，签名为 (target: str, message: str) -> None。
        默认目标可通过参数或配置指定。
        """
        self._notifier = notifier
        if default_target:
            self._notification_target = default_target

    def toggle_auto_fishing(self, user_id: str) -> Dict[str, Any]:
        """
        切换用户的自动钓鱼状态。

        Args:
            user_id: 用户ID。

        Returns:
            一个包含操作结果的字典。
        """
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "❌您还没有注册，请先使用 /注册 命令注册。"}

        user.auto_fishing_enabled = not user.auto_fishing_enabled
        self.user_repo.update(user)

        if user.auto_fishing_enabled:
            return {"success": True, "message": "🎣 自动钓鱼已开启！"}
        else:
            return {"success": True, "message": "🚫 自动钓鱼已关闭！"}

    def go_fish(self, user_id: str) -> Dict[str, Any]:
        """
        执行一次完整的钓鱼动作。

        Args:
            user_id: 尝试钓鱼的用户ID。

        Returns:
            一个包含结果的字典。
        """
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在，无法钓鱼。"}

        return self._go_fish_with_user(user)

    def _go_fish_with_user(self, user, skip_daily_maintenance: bool = False, is_auto: bool = False) -> Dict[str, Any]:
        """对已加载的用户对象执行一次钓鱼动作。"""
        if not skip_daily_maintenance:
            self.run_daily_maintenance_if_needed()

        user_id = user.user_id

        # 1. 检查成本（从区域配置中读取）
        zone = self.inventory_repo.get_zone_by_id(user.fishing_zone_id)
        if not zone:
            return {"success": False, "message": "钓鱼区域不存在"}
        
        # 检查区域是否激活
        if not zone.is_active:
            return {"success": False, "message": "该钓鱼区域被浓雾隐匿了，暂时无法进入"}
        
        # 检查时间限制
        now = get_now()
        if zone.available_from and now < zone.available_from:
            return {"success": False, "message": f"该钓鱼区域将在 {zone.available_from.strftime('%Y-%m-%d %H:%M')} 开放"}
        
        if zone.available_until and now > zone.available_until:
            # 区域已关闭，自动传送回初始区域
            user.fishing_zone_id = 1
            self.user_repo.update(user)
            # 获取初始区域的名字
            first_zone = self.inventory_repo.get_zone_by_id(1)
            first_zone_name = first_zone.name if first_zone else "初始区域"
            return {"success": False, "message": f"该钓鱼区域已于 {zone.available_until.strftime('%Y-%m-%d %H:%M')} 关闭，已自动传送回{first_zone_name}"}

        access_result = self._ensure_current_zone_pass_or_relocate(user, zone)
        if not access_result.get("success", False):
            return access_result
        
        fishing_cost = zone.fishing_cost
        if not user.can_afford(fishing_cost):
            return {"success": False, "message": f"金币不足，需要 {fishing_cost} 金币。"}

        # 先扣除成本
        user.coins -= fishing_cost

        # 2. 计算各种加成和修正值
        base_success_rate = 0.5 # 基础成功率50%
        quality_bonus_total = 0.0 # 品质加成（加法累计）
        quantity_bonus_total = 0.0 # 数量加成（加法累计）
        rare_chance = 0.0 # 稀有鱼出现几率
        value_bonus = 0.0 # 同星级价值池加成

        active_buffs = self.buff_repo.get_all_active_by_user(user_id)

        # 获取装备鱼竿并应用加成
        equipped_rod_instance = self.inventory_repo.get_user_equipped_rod(user.user_id)
        if equipped_rod_instance:
            rod_template = self.item_template_repo.get_rod_by_id(equipped_rod_instance.rod_id)
            if rod_template:
                quality_bonus_total += self._get_additive_modifier_bonus(
                    calculate_after_refine(
                        rod_template.bonus_fish_quality_modifier,
                        refine_level=equipped_rod_instance.refine_level,
                        rarity=rod_template.rarity,
                    )
                )
                quantity_bonus_total += self._get_additive_modifier_bonus(
                    calculate_after_refine(
                        rod_template.bonus_fish_quantity_modifier,
                        refine_level=equipped_rod_instance.refine_level,
                        rarity=rod_template.rarity,
                    )
                )
                base_success_rate += calculate_after_refine(
                    getattr(rod_template, "success_rate_modifier", 0.0),
                    refine_level=equipped_rod_instance.refine_level,
                    rarity=rod_template.rarity,
                )
                rare_chance += calculate_after_refine(rod_template.bonus_rare_fish_chance, refine_level= equipped_rod_instance.refine_level, rarity=rod_template.rarity)
        # 获取装备饰品并应用加成
        equipped_accessory_instance = self.inventory_repo.get_user_equipped_accessory(user.user_id)
        if equipped_accessory_instance:
            acc_template = self.item_template_repo.get_accessory_by_id(equipped_accessory_instance.accessory_id)
            if acc_template:
                quality_bonus_total += self._get_additive_modifier_bonus(
                    calculate_after_refine(
                        acc_template.bonus_fish_quality_modifier,
                        refine_level=equipped_accessory_instance.refine_level,
                        rarity=acc_template.rarity,
                    )
                )
                quantity_bonus_total += self._get_additive_modifier_bonus(
                    calculate_after_refine(
                        acc_template.bonus_fish_quantity_modifier,
                        refine_level=equipped_accessory_instance.refine_level,
                        rarity=acc_template.rarity,
                    )
                )
                rare_chance += calculate_after_refine(acc_template.bonus_rare_fish_chance, refine_level= equipped_accessory_instance.refine_level, rarity=acc_template.rarity)
                value_bonus += self._get_additive_modifier_bonus(
                    calculate_after_refine(
                        acc_template.bonus_coin_modifier,
                        refine_level=equipped_accessory_instance.refine_level,
                        rarity=acc_template.rarity,
                    )
                )
        # 获取鱼饵并应用加成
        cur_bait_id = user.current_bait_id
        garbage_reduction_modifier = None

        # 判断鱼饵是否过期
        if user.current_bait_id is not None:
            bait_template = self.item_template_repo.get_bait_by_id(cur_bait_id)
            if bait_template and bait_template.duration_minutes > 0:
                # 检查鱼饵是否过期
                bait_expiry_time = user.bait_start_time
                if bait_expiry_time:
                    now = get_now()
                    expiry_time = bait_expiry_time + timedelta(minutes=bait_template.duration_minutes)
                    # 移除两个时间的时区信息
                    if now.tzinfo is not None:
                        now = now.replace(tzinfo=None)
                    if expiry_time.tzinfo is not None:
                        expiry_time = expiry_time.replace(tzinfo=None)
                    if now > expiry_time:
                        # 鱼饵已过期，清除当前鱼饵
                        user.current_bait_id = None
                        user.bait_start_time = None
                        self.inventory_repo.update_bait_quantity(user_id, cur_bait_id, -1)
                        self.user_repo.update(user)
                        logger.warning(f"用户 {user_id} 的当前鱼饵{bait_template}已过期，已被清除。")
            else:
                if bait_template:
                    # 如果鱼饵没有设置持续时间, 是一次性鱼饵，消耗一个鱼饵
                    user_bait_inventory = self.inventory_repo.get_user_bait_inventory(user_id)
                    if user_bait_inventory is not None and user_bait_inventory.get(user.current_bait_id, 0) > 0:
                        self.inventory_repo.update_bait_quantity(user_id, user.current_bait_id, -1)
                    else:
                        # 如果用户没有库存鱼饵，清除当前鱼饵
                        user.current_bait_id = None
                        user.bait_start_time = None
                        self.user_repo.update(user)
                        logger.warning(f"用户 {user_id} 的当前鱼饵{bait_template.bait_id}已被清除，因为库存不足。")
                else:
                    # 如果鱼饵模板不存在，清除当前鱼饵
                    user.current_bait_id = None
                    user.bait_start_time = None
                    self.user_repo.update(user)
                    logger.warning(f"用户 {user_id} 的当前鱼饵已被清除，因为鱼饵模板不存在。")

        # 不再在当前鱼饵为空时从库存自动补鱼饵。
        # 鱼饵队列只由玩家主动使用鱼饵建立；队列耗尽后保持空，直到玩家下一次使用鱼饵。

        if user.current_bait_id is not None:
            bait_template = self.item_template_repo.get_bait_by_id(user.current_bait_id)
            # logger.info(f"鱼饵信息: {bait_template}")
            if bait_template:
                quantity_bonus_total += self._get_additive_modifier_bonus(bait_template.quantity_modifier)
                rare_chance += bait_template.rare_chance_modifier
                base_success_rate += bait_template.success_rate_modifier
                garbage_reduction_modifier = bait_template.garbage_reduction_modifier
                value_bonus += self._get_additive_modifier_bonus(bait_template.value_modifier)
        # 3. 判断是否成功钓到
        if random.random() >= base_success_rate:
            # 失败逻辑
            user.last_fishing_time = get_now()
            self.user_repo.update(user)
            return {"success": False, "message": "💨 什么都没钓到..."}

        # 4. 成功，生成渔获
        # 使用区域策略获取基础稀有度分布
        strategy = self.fishing_zone_service.get_strategy(user.fishing_zone_id)
        rarity_distribution = strategy.get_fish_rarity_distribution(user)
        
        zone = self.inventory_repo.get_zone_by_id(user.fishing_zone_id)
        is_rare_fish_available = zone.rare_fish_caught_today < zone.daily_rare_fish_quota
        
        if not is_rare_fish_available:
            # 稀有鱼定义：4星及以上（包括5星和6+星组合）
            # 若达到配额，屏蔽4星、5星和6+星概率，其它星级不受影响
            if len(rarity_distribution) >= 4:
                rarity_distribution[3] = 0.0  # 4星
            if len(rarity_distribution) >= 5:
                rarity_distribution[4] = 0.0  # 5星
            if len(rarity_distribution) >= 6:
                rarity_distribution[5] = 0.0  # 6+星
            # 重新归一化概率分布
            total = sum(rarity_distribution)
            if total > 0:
                rarity_distribution = [x / total for x in rarity_distribution]
        
        # 应用稀有度加成（rare_chance）调整分布权重
        # 如果玩家有装备/Buff/鱼饵提供的稀有度加成，会提升 4-5 星鱼的概率
        # 6+ 星鱼的概率不受影响，保持其作为"运气时刻"的设计
        if rare_chance > 0:
            adjusted_distribution = self._apply_rare_chance_to_distribution(
                rarity_distribution, rare_chance
            )
        else:
            adjusted_distribution = rarity_distribution
        
        # 根据调整后的分布加权随机抽取稀有度
        rarity_index = random.choices(range(len(adjusted_distribution)), weights=adjusted_distribution, k=1)[0]
        
        if rarity_index == 5:  # 抽中6+星组合
            # 从6星及以上的鱼中随机选择，兼容区域限定鱼
            rarity = self._get_random_high_rarity(zone)
        else:
            # 1-5星直接对应
            rarity = rarity_index + 1
            
        fish_template = self._get_fish_template(rarity, zone, value_bonus)

        if not fish_template:
             return {"success": False, "message": "错误：当前条件下没有可钓的鱼！"}

        # 如果有垃圾鱼减少修正，则应用，价值 < 5则被视为垃圾鱼
        if garbage_reduction_modifier is not None and fish_template.base_value < 5:
            # 根据垃圾鱼减少修正值决定是否重新选择一次
            if random.random() < garbage_reduction_modifier:
                # 重新选择一条鱼
                new_rarity_index = random.choices(
                    range(len(adjusted_distribution)),
                    weights=adjusted_distribution,
                    k=1,
                )[0]
                if new_rarity_index == 5:
                    new_rarity = self._get_random_high_rarity(zone)
                else:
                    new_rarity = new_rarity_index + 1
                new_fish_template = self._get_fish_template(new_rarity, zone, value_bonus)

                if new_fish_template:
                    fish_template = new_fish_template

        # 计算最终属性
        value = fish_template.base_value

        # 4.2 按品质加成给予额外品质价值奖励
        # 品质加成来自：鱼竿 + 饰品 + 鱼饵（加法累计）
        quality_bonus = False
        quality_level = 0  # 默认普通品质
        quality_chance = max(0.0, quality_bonus_total) * self._get_quality_decay_factor(fish_template.rarity)
        if quality_chance > 0:
            quality_bonus = random.random() < quality_chance
        if quality_bonus:
            # 标记为高品质鱼，价值在出售时按2倍计算
            quality_level = 1

        # 4.3 按数量加成决定额外渔获数量
        total_catches = 1
        extra_catch_chance = max(0.0, quantity_bonus_total) * self._get_quantity_decay_factor(fish_template.rarity)
        if extra_catch_chance > 0 and random.random() < extra_catch_chance:
            total_catches += 1

        # 5. 处理鱼塘容量（在确定总渔获量后）
        user_fish_inventory = self.inventory_repo.get_fish_inventory(user.user_id)
        current_fish_count = sum(item.quantity for item in user_fish_inventory)
        
        # 计算放入新鱼后是否会溢出，以及溢出多少
        overflow_amount = (current_fish_count + total_catches) - user.fish_pond_capacity

        if overflow_amount > 0:
            # 鱼塘空间不足，需要移除 `overflow_amount` 条鱼
            # 采用循环随机移除的策略，确保腾出足够空间
            for _ in range(overflow_amount):
                # 每次循环都重新获取一次库存，防止某个种类的鱼被移除完
                current_inventory_for_removal = self.inventory_repo.get_fish_inventory(user.user_id)
                if not current_inventory_for_removal:
                    break # 如果鱼塘已经空了，就停止移除
                
                # 随机选择一个鱼种（堆叠）来移除
                random_fish_stack = random.choice(current_inventory_for_removal)
                self.inventory_repo.update_fish_quantity(
                    user.user_id,
                    random_fish_stack.fish_id,
                    -1
                )

        if fish_template.rarity >= 4:
            # 如果是4星及以上稀有鱼，增加用户的稀有鱼捕获计数
            zone = self.inventory_repo.get_zone_by_id(user.fishing_zone_id)
            if zone:
                zone.rare_fish_caught_today += 1
                self.inventory_repo.update_fishing_zone(zone)

        # 6. 更新数据库
        self.inventory_repo.add_fish_to_inventory(user.user_id, fish_template.fish_id, quantity=total_catches, quality_level=quality_level)

        # 更新用户统计数据
        user.total_fishing_count += total_catches
        # 高品质鱼的统计价值按双倍计算
        if quality_level == 1:
            user.total_coins_earned += fish_template.base_value * total_catches * 2
        else:
            user.total_coins_earned += fish_template.base_value * total_catches
        user.last_fishing_time = get_now()
        
        # 处理装备耐久度消耗
        equipment_broken_messages = []

        # 判断用户的鱼竿是否存在并处理耐久度
        if user.equipped_rod_instance_id:
            rod_instance = self.inventory_repo.get_user_rod_instance_by_id(user.user_id, user.equipped_rod_instance_id)
            if not rod_instance:
                user.equipped_rod_instance_id = None
            else:
                # 减少鱼竿耐久度（仅当为有限耐久时）
                if rod_instance.current_durability is not None and rod_instance.current_durability > 0:
                    rod_instance.current_durability -= 1
                    self.inventory_repo.update_rod_instance(rod_instance)

                # 无论是刚减为0，还是之前就是0，都进行一次破损检查与卸下，保证一致性
                if rod_instance.current_durability is not None and rod_instance.current_durability <= 0:
                    # 鱼竿损坏，自动卸下（同步 user 与实例 is_equipped 状态）
                    user.equipped_rod_instance_id = None
                    # 统一使用仓储方法重置装备状态，避免前端/状态页不一致
                    self.inventory_repo.set_equipment_status(
                        user.user_id,
                        rod_instance_id=None,
                        accessory_instance_id=user.equipped_accessory_instance_id
                    )
                    rod_template = self.item_template_repo.get_rod_by_id(rod_instance.rod_id)
                    rod_name = rod_template.name if rod_template else "鱼竿"
                    equipment_broken_messages.append(f"⚠️ 您的{rod_name}已损坏，自动卸下！")
        
        # 判断用户的饰品是否存在（饰品暂时不消耗耐久度）
        if user.equipped_accessory_instance_id:
            accessory_instance = self.inventory_repo.get_user_accessory_instance_by_id(user.user_id, user.equipped_accessory_instance_id)
            if not accessory_instance:
                user.equipped_accessory_instance_id = None

        # 更新用户信息
        self.user_repo.update(user)

        # 记录日志
        record = FishingRecord(
            record_id=0, # DB自增
            user_id=user.user_id,
            fish_id=fish_template.fish_id,
            value=value,
            timestamp=user.last_fishing_time,
            rod_instance_id=user.equipped_rod_instance_id,
            accessory_instance_id=user.equipped_accessory_instance_id,
            bait_id=user.current_bait_id
        )
        self.log_repo.add_fishing_record(record, log_to_records=not is_auto)

        # 8. 构建成功返回结果
        result = {
            "success": True,
            "fish": {
                "name": fish_template.name,
                "rarity": fish_template.rarity,
                "value": value * 2 if quality_level == 1 else value,  # 高品质鱼双倍价值
                "quality_level": quality_level,  # 添加品质等级
                "quality_label": "✨高品质" if quality_level == 1 else "普通"  # 添加品质标签
            }
        }
        
        # 添加装备损坏消息
        if equipment_broken_messages:
            result["equipment_broken_messages"] = equipment_broken_messages
        
        return result

    def get_user_pokedex(self, user_id: str) -> Dict[str, Any]:
        """获取用户的图鉴信息。"""
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}
        # 使用聚合统计作为图鉴数据来源
        stats = self.log_repo.get_user_fish_stats(user_id)
        if not stats:
            return {"success": True, "pokedex": []}
        all_fish_count = len(self.item_template_repo.get_all_fish())
        unlock_fish_count = len(stats)
        pokedex = []
        for stat in stats:
            fish_template = self.item_template_repo.get_fish_by_id(stat.fish_id)
            if fish_template:
                pokedex.append({
                    "fish_id": stat.fish_id,
                    "name": fish_template.name,
                    "rarity": fish_template.rarity,
                    "description": fish_template.description,
                    "value": fish_template.base_value,
                    "icon_url": fish_template.icon_url,
                    "first_caught_time": stat.first_caught_at,
                    "last_caught_time": stat.last_caught_at,
                    "total_caught": stat.total_caught,
                })
        # 将图鉴按稀有度从大到小排序
        pokedex.sort(key=lambda x: x["rarity"], reverse=True)
        return {
            "success": True,
            "pokedex": pokedex,
            "total_fish_count": all_fish_count,
            "unlocked_fish_count": unlock_fish_count,
            "unlocked_percentage": (unlock_fish_count / all_fish_count) if all_fish_count > 0 else 0
    }

    def _sum_rewards_by_type(self, rewards: List[Dict[str, Any]]) -> Dict[str, int]:
        totals = {"coins": 0, "premium": 0}
        for reward in rewards:
            reward_type = reward.get("reward_type", "premium")
            totals[reward_type] = totals.get(reward_type, 0) + int(reward.get("reward_amount", 0) or 0)
        return totals

    def _sum_claimed_rewards_by_type(self, rewards: List[Dict[str, Any]]) -> Dict[str, int]:
        totals = {"coins": 0, "premium": 0}
        for reward in rewards:
            reward_type = reward.get("claimed_reward_type", reward.get("reward_type", "premium"))
            reward_amount = reward.get("claimed_reward_amount", reward.get("reward_amount", 0))
            totals[reward_type] = totals.get(reward_type, 0) + int(reward_amount or 0)
        return totals

    def _build_pokedex_reward_status(self, user_id: str, user) -> Dict[str, Any]:
        stats = self.log_repo.get_user_fish_stats(user_id)
        claims = self.log_repo.get_user_pokedex_reward_claims(user_id)
        claim_map = {claim.milestone_percent: claim for claim in claims}

        total_fish_count = len(self.item_template_repo.get_all_fish())
        unlocked_fish_count = len(stats)
        unlocked_ratio = (unlocked_fish_count / total_fish_count) if total_fish_count > 0 else 0.0

        milestones = []
        for milestone_percent, reward_type, reward_amount in POKEDEX_REWARD_MILESTONES:
            required_fish_count = (
                math.ceil(total_fish_count * milestone_percent / 100.0)
                if total_fish_count > 0
                else 0
            )
            claim_record = claim_map.get(milestone_percent)
            claimed = claim_record is not None
            claimed_reward_type = getattr(claim_record, "reward_type", None) if claim_record else None
            claimed_reward_amount = getattr(claim_record, "reward_amount", None) if claim_record else None
            claimable = (
                not claimed
                and total_fish_count > 0
                and unlocked_fish_count >= required_fish_count
            )
            remaining_fish_count = 0 if claimed or claimable else max(required_fish_count - unlocked_fish_count, 0)

            milestones.append(
                {
                    "milestone_percent": milestone_percent,
                    "reward_type": reward_type,
                    "reward_amount": reward_amount,
                    "reward_text": _format_reward_text(reward_type, reward_amount),
                    "reward_premium": reward_amount if reward_type == "premium" else 0,
                    "claimed_reward_type": claimed_reward_type,
                    "claimed_reward_amount": claimed_reward_amount,
                    "required_fish_count": required_fish_count,
                    "claimed": claimed,
                    "claimable": claimable,
                    "remaining_fish_count": remaining_fish_count,
                    "claimed_at": getattr(claim_record, "claimed_at", None),
                }
            )

        claimable_rewards = [item for item in milestones if item["claimable"]]
        next_milestone = next((item for item in milestones if not item["claimed"]), None)
        total_rewards_by_type = self._sum_rewards_by_type(milestones)
        claimed_rewards_by_type = self._sum_claimed_rewards_by_type([item for item in milestones if item["claimed"]])
        claimable_rewards_by_type = self._sum_rewards_by_type(claimable_rewards)

        return {
            "success": True,
            "user_id": user_id,
            "current_coins": user.coins,
            "current_premium_currency": user.premium_currency,
            "unlocked_fish_count": unlocked_fish_count,
            "total_fish_count": total_fish_count,
            "unlocked_percentage": unlocked_ratio,
            "unlocked_percentage_text": f"{unlocked_ratio * 100:.1f}%",
            "milestones": milestones,
            "claimable_rewards": claimable_rewards,
            "claimable_rewards_by_type": claimable_rewards_by_type,
            "claimable_reward_total": claimable_rewards_by_type.get("premium", 0),
            "claimable_coins_total": claimable_rewards_by_type.get("coins", 0),
            "claimable_premium_total": claimable_rewards_by_type.get("premium", 0),
            "claimed_count": sum(1 for item in milestones if item["claimed"]),
            "total_milestones": len(milestones),
            "total_claimed_coins": claimed_rewards_by_type.get("coins", 0),
            "total_claimed_premium": claimed_rewards_by_type.get("premium", 0),
            "total_reward_coins": total_rewards_by_type.get("coins", 0),
            "total_reward_premium": total_rewards_by_type.get("premium", 0),
            "total_reward_amount": total_rewards_by_type.get("premium", 0),
            "next_milestone": next_milestone,
            "all_claimed": all(item["claimed"] for item in milestones) if milestones else True,
        }

    def get_pokedex_reward_status(self, user_id: str) -> Dict[str, Any]:
        """获取用户图鉴奖励状态。"""
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}
        return self._build_pokedex_reward_status(user_id, user)

    def claim_pokedex_rewards(self, user_id: str) -> Dict[str, Any]:
        """领取当前所有可领取的图鉴奖励。"""
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        with self.pokedex_reward_claim_lock:
            status = self._build_pokedex_reward_status(user_id, user)
            claimable_rewards = status.get("claimable_rewards", [])
            newly_claimed_rewards = []
            newly_claimed_premium = 0

            for reward in claimable_rewards:
                claimed = self.log_repo.claim_pokedex_reward(
                    user_id,
                    reward["milestone_percent"],
                    reward["reward_type"],
                    reward["reward_amount"],
                    status["unlocked_fish_count"],
                    status["total_fish_count"],
                )
                if claimed:
                    newly_claimed_rewards.append(
                        {
                            "milestone_percent": reward["milestone_percent"],
                            "reward_type": reward["reward_type"],
                            "reward_amount": reward["reward_amount"],
                            "reward_text": reward["reward_text"],
                            "reward_premium": reward["reward_premium"],
                            "required_fish_count": reward["required_fish_count"],
                        }
                    )
                    newly_claimed_premium += reward["reward_premium"]

            refreshed_user = self.user_repo.get_by_id(user_id)
            refreshed_status = self._build_pokedex_reward_status(user_id, refreshed_user or user)
            refreshed_status["newly_claimed_rewards"] = newly_claimed_rewards
            refreshed_status["newly_claimed_premium"] = newly_claimed_premium
            refreshed_status["newly_claimed_by_type"] = self._sum_rewards_by_type(newly_claimed_rewards)
            if newly_claimed_rewards:
                claimed_totals = refreshed_status["newly_claimed_by_type"]
                parts = []
                if claimed_totals.get("coins", 0) > 0:
                    parts.append(f"{claimed_totals['coins']} 金币")
                if claimed_totals.get("premium", 0) > 0:
                    parts.append(f"{claimed_totals['premium']} 钻石")
                refreshed_status["message"] = "成功领取 " + "、".join(parts)
            else:
                refreshed_status["message"] = "当前没有可领取的图鉴奖励"
            return refreshed_status

    def _get_all_equipment_templates(self) -> List[Dict[str, Any]]:
        equipment = []
        for rod in self.item_template_repo.get_all_rods():
            equipment.append(
                {
                    "equipment_type": "rod",
                    "equipment_type_label": EQUIPMENT_TYPE_META["rod"]["label"],
                    "id": rod.rod_id,
                    "name": rod.name,
                    "rarity": rod.rarity,
                    "description": rod.description,
                }
            )
        for accessory in self.item_template_repo.get_all_accessories():
            equipment.append(
                {
                    "equipment_type": "accessory",
                    "equipment_type_label": EQUIPMENT_TYPE_META["accessory"]["label"],
                    "id": accessory.accessory_id,
                    "name": accessory.name,
                    "rarity": accessory.rarity,
                    "description": accessory.description,
                }
            )
        for bait in self.item_template_repo.get_all_baits():
            equipment.append(
                {
                    "equipment_type": "bait",
                    "equipment_type_label": EQUIPMENT_TYPE_META["bait"]["label"],
                    "id": bait.bait_id,
                    "name": bait.name,
                    "rarity": bait.rarity,
                    "description": bait.description,
                    "effect_description": bait.effect_description,
                }
            )
        equipment.sort(
            key=lambda item: (
                EQUIPMENT_TYPE_META[item["equipment_type"]]["order"],
                -int(item.get("rarity", 0) or 0),
                int(item["id"]),
            )
        )
        return equipment

    def _sync_user_equipment_stats(self, user_id: str) -> None:
        self.inventory_repo.sync_user_equipment_stats_from_inventory(user_id)

    def get_user_equipment_pokedex(self, user_id: str, page: int = 1, page_size: int = 20, equipment_type: str | None = None, rarity: int | None = None, owned_only: bool = False) -> Dict[str, Any]:
        """获取用户装备图鉴。"""
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        self._sync_user_equipment_stats(user_id)
        stats = self.inventory_repo.get_user_equipment_stats(user_id)
        stat_map = {
            (item["equipment_type"], item["equipment_id"]): item
            for item in stats
        }
        all_equipment = self._get_all_equipment_templates()
        items = []
        categories = {
            key: {
                "equipment_type": key,
                "label": meta["label"],
                "total_count": 0,
                "unlocked_count": 0,
                "unlocked_percentage": 0.0,
            }
            for key, meta in EQUIPMENT_TYPE_META.items()
        }

        for template in all_equipment:
            key = (template["equipment_type"], template["id"])
            stat = stat_map.get(key)
            is_collected = stat is not None
            category = categories[template["equipment_type"]]
            category["total_count"] += 1
            if is_collected:
                category["unlocked_count"] += 1

            item = dict(template)
            item.update(
                {
                    "is_collected": is_collected,
                    "total_obtained": int(stat.get("total_obtained", 0)) if stat else 0,
                    "first_obtained_at": stat.get("first_obtained_at") if stat else None,
                    "last_obtained_at": stat.get("last_obtained_at") if stat else None,
                }
            )
            items.append(item)

        for category in categories.values():
            total = category["total_count"]
            category["unlocked_percentage"] = (category["unlocked_count"] / total) if total > 0 else 0.0
            category["unlocked_percentage_text"] = f"{category['unlocked_percentage'] * 100:.1f}%"

        total_equipment_count = len(all_equipment)
        unlocked_equipment_count = len(stat_map)
        valid_equipment_types = set(EQUIPMENT_TYPE_META.keys())
        normalized_equipment_type = equipment_type if equipment_type in valid_equipment_types else "all"
        filtered_items = items
        if normalized_equipment_type != "all":
            filtered_items = [item for item in filtered_items if item.get("equipment_type") == normalized_equipment_type]
        if rarity is not None:
            filtered_items = [item for item in filtered_items if int(item.get("rarity", 0) or 0) == rarity]
        if owned_only:
            filtered_items = [item for item in filtered_items if item.get("is_collected")]

        total_pages = max(1, math.ceil(len(filtered_items) / page_size)) if page_size > 0 else 1
        page = min(max(page, 1), total_pages)
        start = (page - 1) * page_size
        paged_items = filtered_items[start:start + page_size]

        return {
            "success": True,
            "user_id": user_id,
            "pokedex": items,
            "page_items": paged_items,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "filtered_count": len(filtered_items),
            "filters": {
                "equipment_type": normalized_equipment_type,
                "rarity": rarity if rarity is not None else "all",
                "owned": "1" if owned_only else "0",
            },
            "categories": categories,
            "total_equipment_count": total_equipment_count,
            "unlocked_equipment_count": unlocked_equipment_count,
            "unlocked_percentage": (unlocked_equipment_count / total_equipment_count) if total_equipment_count > 0 else 0.0,
            "unlocked_percentage_text": (
                f"{(unlocked_equipment_count / total_equipment_count * 100) if total_equipment_count > 0 else 0:.1f}%"
            ),
        }

    def _build_equipment_pokedex_reward_status(self, user_id: str, user) -> Dict[str, Any]:
        self._sync_user_equipment_stats(user_id)
        stats = self.inventory_repo.get_user_equipment_stats(user_id)
        claims = self.inventory_repo.get_user_equipment_pokedex_reward_claims(user_id)
        claim_map = {claim["milestone_percent"]: claim for claim in claims}

        total_equipment_count = len(self._get_all_equipment_templates())
        unlocked_equipment_count = len(stats)
        unlocked_ratio = (unlocked_equipment_count / total_equipment_count) if total_equipment_count > 0 else 0.0

        milestones = []
        for milestone_percent, reward_type, reward_amount in EQUIPMENT_POKEDEX_REWARD_MILESTONES:
            required_equipment_count = (
                math.ceil(total_equipment_count * milestone_percent / 100.0)
                if total_equipment_count > 0
                else 0
            )
            claim_record = claim_map.get(milestone_percent)
            claimed = claim_record is not None
            claimed_reward_type = claim_record.get("reward_type") if claim_record else None
            claimed_reward_amount = claim_record.get("reward_amount") if claim_record else None
            claimable = (
                not claimed
                and total_equipment_count > 0
                and unlocked_equipment_count >= required_equipment_count
            )
            remaining_equipment_count = (
                0 if claimed or claimable else max(required_equipment_count - unlocked_equipment_count, 0)
            )

            milestones.append(
                {
                    "milestone_percent": milestone_percent,
                    "reward_type": reward_type,
                    "reward_amount": reward_amount,
                    "reward_text": _format_reward_text(reward_type, reward_amount),
                    "reward_premium": reward_amount if reward_type == "premium" else 0,
                    "claimed_reward_type": claimed_reward_type,
                    "claimed_reward_amount": claimed_reward_amount,
                    "required_equipment_count": required_equipment_count,
                    "claimed": claimed,
                    "claimable": claimable,
                    "remaining_equipment_count": remaining_equipment_count,
                    "claimed_at": claim_record.get("claimed_at") if claim_record else None,
                }
            )

        claimable_rewards = [item for item in milestones if item["claimable"]]
        next_milestone = next((item for item in milestones if not item["claimed"]), None)
        total_rewards_by_type = self._sum_rewards_by_type(milestones)
        claimed_rewards_by_type = self._sum_claimed_rewards_by_type([item for item in milestones if item["claimed"]])
        claimable_rewards_by_type = self._sum_rewards_by_type(claimable_rewards)

        return {
            "success": True,
            "user_id": user_id,
            "current_coins": user.coins,
            "current_premium_currency": user.premium_currency,
            "unlocked_equipment_count": unlocked_equipment_count,
            "total_equipment_count": total_equipment_count,
            "unlocked_percentage": unlocked_ratio,
            "unlocked_percentage_text": f"{unlocked_ratio * 100:.1f}%",
            "milestones": milestones,
            "claimable_rewards": claimable_rewards,
            "claimable_rewards_by_type": claimable_rewards_by_type,
            "claimable_coins_total": claimable_rewards_by_type.get("coins", 0),
            "claimable_premium_total": claimable_rewards_by_type.get("premium", 0),
            "claimed_count": sum(1 for item in milestones if item["claimed"]),
            "total_milestones": len(milestones),
            "total_claimed_coins": claimed_rewards_by_type.get("coins", 0),
            "total_claimed_premium": claimed_rewards_by_type.get("premium", 0),
            "total_reward_coins": total_rewards_by_type.get("coins", 0),
            "total_reward_premium": total_rewards_by_type.get("premium", 0),
            "next_milestone": next_milestone,
            "all_claimed": all(item["claimed"] for item in milestones) if milestones else True,
        }

    def get_equipment_pokedex_reward_status(self, user_id: str) -> Dict[str, Any]:
        """获取用户装备图鉴奖励状态。"""
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}
        return self._build_equipment_pokedex_reward_status(user_id, user)

    def claim_equipment_pokedex_rewards(self, user_id: str) -> Dict[str, Any]:
        """领取当前所有可领取的装备图鉴奖励。"""
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        with self.pokedex_reward_claim_lock:
            status = self._build_equipment_pokedex_reward_status(user_id, user)
            claimable_rewards = status.get("claimable_rewards", [])
            newly_claimed_rewards = []

            for reward in claimable_rewards:
                claimed = self.inventory_repo.claim_equipment_pokedex_reward(
                    user_id,
                    reward["milestone_percent"],
                    reward["reward_type"],
                    reward["reward_amount"],
                    status["unlocked_equipment_count"],
                    status["total_equipment_count"],
                )
                if claimed:
                    newly_claimed_rewards.append(
                        {
                            "milestone_percent": reward["milestone_percent"],
                            "reward_type": reward["reward_type"],
                            "reward_amount": reward["reward_amount"],
                            "reward_text": reward["reward_text"],
                            "reward_premium": reward["reward_premium"],
                            "required_equipment_count": reward["required_equipment_count"],
                        }
                    )

            refreshed_user = self.user_repo.get_by_id(user_id)
            refreshed_status = self._build_equipment_pokedex_reward_status(user_id, refreshed_user or user)
            refreshed_status["newly_claimed_rewards"] = newly_claimed_rewards
            refreshed_status["newly_claimed_by_type"] = self._sum_rewards_by_type(newly_claimed_rewards)
            refreshed_status["newly_claimed_premium"] = refreshed_status["newly_claimed_by_type"].get("premium", 0)
            if newly_claimed_rewards:
                claimed_totals = refreshed_status["newly_claimed_by_type"]
                parts = []
                if claimed_totals.get("coins", 0) > 0:
                    parts.append(f"{claimed_totals['coins']} 金币")
                if claimed_totals.get("premium", 0) > 0:
                    parts.append(f"{claimed_totals['premium']} 钻石")
                refreshed_status["message"] = "成功领取 " + "、".join(parts)
            else:
                refreshed_status["message"] = "当前没有可领取的装备图鉴奖励"
            return refreshed_status

    def get_user_fishing_zones(self, user_id: str) -> Dict[str, Any]:
        """
        获取用户的钓鱼区域信息。

        Args:
            user_id: 用户ID。

        Returns:
            包含钓鱼区域信息的字典。
        """
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        fishing_zones = self.inventory_repo.get_all_zones()
        zones_info = []
        
        for zone in fishing_zones:
            # 获取通行证道具名称
            required_item_name = None
            if zone.requires_pass and zone.required_item_id:
                item_template = self.item_template_repo.get_item_by_id(zone.required_item_id)
                required_item_name = item_template.name if item_template else f"道具ID{zone.required_item_id}"
            
            zones_info.append({
                "zone_id": zone.id,
                "name": zone.name,
                "description": zone.description,
                "daily_rare_fish_quota": zone.daily_rare_fish_quota,
                "rare_fish_caught_today": zone.rare_fish_caught_today,
                "whether_in_use": zone.id == user.fishing_zone_id,
                "is_active": zone.is_active,
                "requires_pass": zone.requires_pass,
                "required_item_id": zone.required_item_id,
                "required_item_name": required_item_name,
                "fishing_cost": zone.fishing_cost,
                "available_from": zone.available_from,
                "available_until": zone.available_until,
            })

        return {
            "success": True,
            "zones": zones_info
        }

    def _apply_rare_chance_to_distribution(self, distribution: list, rare_chance: float) -> list:
        """
        应用稀有度加成，调整鱼类稀有度分布权重。
        
        设计理念：
        - 装备/Buff/鱼饵的稀有度加成影响 4-5 星鱼（稀有鱼）的概率
        - 6+ 星鱼（超稀有/传说鱼）保持纯运气机制，不受装备影响
        - 通过从低星转移权重到中高星，确保概率总和始终为 1
        
        实现原理：
        1. 从 1-3 星的总权重中，按稀有度加成转移一部分权重。
        2. 对 rare_chance 做温和压缩，避免多来源叠加后 5 星膨胀。
        3. 将转移权重分配给 4-5 星，并轻微偏向 4 星。
        4. 6+ 星的概率保持不变，保证超稀有鱼的珍贵性。
        
        示例效果（rare_chance = 0.46）：
        - 原始: 1-3星为主要来源，4-5星为提升目标，6+星保持独立
        - 调整后: 1-3星下降，4星提升更明显，5星温和提升，6+星维持不变
        
        Args:
            distribution: 原始稀有度分布列表 [1星, 2星, 3星, 4星, 5星, 6+星]
            rare_chance: 稀有度加成值，通常在 0.0-0.8 之间
        
        Returns:
            调整后的稀有度分布列表，概率总和为 1
        """
        if len(distribution) < 6:
            return distribution.copy()

        new_distribution = distribution.copy()
        low_star_total = sum(new_distribution[:3])
        mid_high_star_total = sum(new_distribution[3:5])

        if mid_high_star_total <= 0 or low_star_total <= 0:
            return new_distribution

        effective_rare_chance = max(0.0, rare_chance)
        transfer_ratio = min((effective_rare_chance / (1.0 + effective_rare_chance)) * 0.6, 0.35)
        transfer_amount = low_star_total * transfer_ratio

        for i in range(3):
            if low_star_total > 0:
                ratio = new_distribution[i] / low_star_total
                new_distribution[i] = max(0, new_distribution[i] - transfer_amount * ratio)

        four_star_weight = new_distribution[3]
        five_star_weight = new_distribution[4] * 0.6
        target_total = four_star_weight + five_star_weight
        if target_total <= 0:
            return distribution.copy()

        new_distribution[3] += transfer_amount * (four_star_weight / target_total)
        new_distribution[4] += transfer_amount * (five_star_weight / target_total)

        total = sum(new_distribution)
        if total <= 0:
            return distribution.copy()
        new_distribution = [x / total for x in new_distribution]
        return new_distribution

    def _get_fish_template(self, rarity: int, zone: FishingZone, value_bonus: float):
        """根据稀有度和区域配置获取鱼类模板"""
        
        # 检查 FishingZone 对象是否有 'specific_fish_ids' 属性
        specific_fish_ids = getattr(zone, 'specific_fish_ids', [])

        if specific_fish_ids:
            # 如果是区域限定鱼，那么就在限定的鱼里面抽
            fish_list = [self.item_template_repo.get_fish_by_id(fish_id) for fish_id in specific_fish_ids]
            fish_list = [fish for fish in fish_list if fish and fish.rarity == rarity]
        else:
            # 否则就在全局鱼里面抽
            fish_list = self.item_template_repo.get_fishes_by_rarity(rarity)

        if not fish_list:
            # 如果限定鱼或全局鱼列表为空，则从所有鱼中随机抽取一条
            return self.item_template_repo.get_random_fish(rarity)

        return get_fish_template(fish_list, value_bonus)

    @staticmethod
    def _get_additive_modifier_bonus(modifier: float) -> float:
        """将以 1.0 为基线的模板倍率转换为加法语义下的增量。"""
        if modifier is None:
            return 0.0
        return modifier - 1.0

    @staticmethod
    def _get_quality_decay_factor(rarity: int) -> float:
        if rarity <= 3:
            return 1.0
        if rarity == 4:
            return 0.6
        if rarity == 5:
            return 0.3
        return 0.1

    @staticmethod
    def _get_quantity_decay_factor(rarity: int) -> float:
        if rarity <= 3:
            return 1.0
        if rarity == 4:
            return 0.7
        if rarity == 5:
            return 0.4
        return 0.0

    def _get_random_high_rarity(self, zone: FishingZone = None) -> int:
        """从6星及以上鱼类中随机选择一个稀有度，兼容区域限定鱼"""
        # 检查是否有区域限定鱼
        specific_fish_ids = getattr(zone, 'specific_fish_ids', []) if zone else []
        
        if specific_fish_ids:
            # 如果是区域限定鱼，只在限定鱼中查找高星级
            fish_list = [self.item_template_repo.get_fish_by_id(fish_id) for fish_id in specific_fish_ids]
            fish_list = [fish for fish in fish_list if fish]
        else:
            # 否则在全局鱼池中查找
            fish_list = self.item_template_repo.get_all_fish()
        
        # 找出所有6星及以上的稀有度
        high_rarities = set()
        for fish in fish_list:
            if fish.rarity >= 6:
                high_rarities.add(fish.rarity)
        
        if not high_rarities:
            # 如果没有6星及以上的鱼，返回5星
            return 5
        
        # 将稀有度排序
        sorted_rarities = sorted(high_rarities)
        
        # 使用指数递减权重：每高一星，概率降低到前一星的58%
        # 权重计算公式：0.58^(rarity-6)
        # 
        # 权重分布（相对值）：
        #   6星 = 0.58^0 = 1.0000
        #   7星 = 0.58^1 = 0.5800
        #   8星 = 0.58^2 = 0.3364
        #   9星 = 0.58^3 = 0.1951
        #  10星 = 0.58^4 = 0.1131
        #
        # 如果存在6-10星鱼，实际概率（归一化后）：
        #   6星: ~44.9% (1.0000/2.2246)
        #   7星: ~26.1% (0.5800/2.2246)
        #   8星: ~15.1% (0.3364/2.2246)
        #   9星: ~8.8%  (0.1951/2.2246)
        #  10星: ~5.1%  (0.1131/2.2246)
        #
        # 假设6+星整体概率为1%，则10星整体概率约为 1% × 5.1% = 0.051%
        weights = []
        for rarity in sorted_rarities:
            weight = 0.58 ** (rarity - 6)
            weights.append(weight)
        
        # 使用加权随机选择
        return random.choices(sorted_rarities, weights=weights, k=1)[0]

    def _find_matching_pass_for_zone(self, user_id: str, zone: FishingZone):
        """
        在用户背包中查找与区域通行证要求模糊匹配的道具。

        返回 (matched_item_id, item_template, quantity) 或 (None, None, 0)
        匹配规则：
        - 优先尝试 exact id 匹配（如果 zone.required_item_id 在库存中且数量>0）
        - 否则基于道具名称进行大小写不敏感的子串匹配（双向包含）
        """
        try:
            if not getattr(zone, "requires_pass", False) or not getattr(zone, "required_item_id", None):
                return (None, None, 0)

            # 需要的通行证模板（用于名称匹配）
            required_template = self.item_template_repo.get_item_by_id(zone.required_item_id)
            required_name = required_template.name if required_template and getattr(required_template, "name", None) else None

            user_items = self.inventory_repo.get_user_item_inventory(user_id) or {}

            # 优先 exact id
            qty = user_items.get(zone.required_item_id, 0)
            if qty > 0:
                return (zone.required_item_id, required_template, qty)

            # 然后基于名称模糊匹配
            if required_name:
                req_lower = required_name.lower()
                for item_id, item_qty in user_items.items():
                    if item_qty <= 0:
                        continue
                    try:
                        tpl = self.item_template_repo.get_item_by_id(item_id)
                        if not tpl or not getattr(tpl, "name", None):
                            continue
                        name_lower = tpl.name.lower()
                        # 双向包含判断
                        if req_lower in name_lower or name_lower in req_lower:
                            return (item_id, tpl, item_qty)
                    except Exception:
                        continue

            return (None, None, 0)
        except Exception:
            return (None, None, 0)

    @staticmethod
    def _is_stay_active(stay: Optional[Dict[str, Any]], now) -> bool:
        if not stay:
            return False
        expires_at = stay.get("expires_at")
        return bool(expires_at and expires_at > now)

    def _get_safe_zone_after_pass_expiry(self) -> Optional[FishingZone]:
        for zone_id in (2, 1):
            try:
                zone = self.inventory_repo.get_zone_by_id(zone_id)
            except Exception:
                continue
            if zone_id == 2 and (not zone.is_active or getattr(zone, "requires_pass", False)):
                continue
            return zone
        return None

    def _relocate_user_for_missing_zone_pass(self, user, zone: FishingZone, item_name: str) -> Dict[str, Any]:
        target_zone = self._get_safe_zone_after_pass_expiry()
        target_zone_id = target_zone.id if target_zone else 1
        target_zone_name = target_zone.name if target_zone else "区域一"

        self.inventory_repo.delete_user_zone_stay(user.user_id, zone.id)
        user.fishing_zone_id = target_zone_id
        self.user_repo.update(user)
        self.log_repo.add_log(
            user.user_id,
            "zone_relocation",
            f"缺少 {item_name}，已被传送至 {target_zone_name}",
        )
        return {
            "success": False,
            "message": f"区域通行证已失效且缺少 {item_name}，已传送至{target_zone_name}",
        }

    def _consume_pass_and_extend_stay(self, user_id: str, zone: FishingZone, item_id: int, item_name: str):
        self.inventory_repo.decrease_item_quantity(user_id, item_id, 1)
        expires_at = get_last_reset_time(self.daily_reset_hour) + timedelta(days=1)
        self.inventory_repo.upsert_user_zone_stay(user_id, zone.id, item_id, expires_at)
        self.log_repo.add_log(user_id, "zone_stay_renewal", f"消耗 1 个 {item_name}，{zone.name} 授权延长至 {expires_at.strftime('%Y-%m-%d %H:%M')}")
        return expires_at

    def _ensure_current_zone_pass_or_relocate(self, user, zone: FishingZone) -> Dict[str, Any]:
        if not getattr(zone, "requires_pass", False) or not getattr(zone, "required_item_id", None):
            self.inventory_repo.delete_user_zone_stay(user.user_id, zone.id)
            return {"success": True}

        matched_item_id, matched_tpl, matched_qty = self._find_matching_pass_for_zone(user.user_id, zone)
        item_template = self.item_template_repo.get_item_by_id(zone.required_item_id)
        required_item_name = item_template.name if item_template else f"道具ID{zone.required_item_id}"

        if matched_item_id:
            matched_item_name = matched_tpl.name if matched_tpl and getattr(matched_tpl, "name", None) else f"道具ID{matched_item_id}"
            is_consumable = getattr(matched_tpl, "is_consumable", True) if matched_tpl else True
            if not is_consumable:
                self.inventory_repo.delete_user_zone_stay(user.user_id, zone.id)
                return {"success": True}
        else:
            matched_item_name = required_item_name
            is_consumable = True

        now = get_now()
        stay = self.inventory_repo.get_user_zone_stay(user.user_id, zone.id)
        if self._is_stay_active(stay, now):
            return {"success": True}

        if matched_item_id and matched_qty > 0 and is_consumable:
            expires_at = self._consume_pass_and_extend_stay(user.user_id, zone, matched_item_id, matched_item_name)
            return {"success": True, "renewed_until": expires_at}

        return self._relocate_user_for_missing_zone_pass(user, zone, matched_item_name)

    def _enter_zone_with_pass(self, user, zone: FishingZone) -> Dict[str, Any]:
        if not getattr(zone, "requires_pass", False) or not getattr(zone, "required_item_id", None):
            return {"success": True, "message_suffix": ""}

        matched_item_id, matched_tpl, matched_qty = self._find_matching_pass_for_zone(user.user_id, zone)
        if not matched_item_id:
            item_template = self.item_template_repo.get_item_by_id(zone.required_item_id)
            item_name = item_template.name if item_template else f"道具ID{zone.required_item_id}"
            return {
                "success": False,
                "message": f"❌ 进入该区域需要 {item_name}，您当前拥有 0 个",
            }

        item_name = matched_tpl.name if matched_tpl and getattr(matched_tpl, "name", None) else f"道具ID{matched_item_id}"
        is_consumable = getattr(matched_tpl, "is_consumable", True) if matched_tpl else True
        if not is_consumable:
            self.inventory_repo.delete_user_zone_stay(user.user_id, zone.id)
            self.log_repo.add_log(user.user_id, "zone_entry", f"使用不消耗通行证({item_name})进入 {zone.name}")
            return {
                "success": True,
                "message_suffix": f"\nℹ️ 提示：欢迎入驻！您使用的 {item_name} 为该区域永久通行证！",
            }

        if matched_qty <= 0:
            return {
                "success": False,
                "message": f"❌ 进入该区域需要 {item_name}，您当前拥有 0 个",
            }

        expires_at = self._consume_pass_and_extend_stay(user.user_id, zone, matched_item_id, item_name)
        self.log_repo.add_log(user.user_id, "zone_entry", f"使用通行证({item_name})进入 {zone.name}")
        return {
            "success": True,
            "message_suffix": f"\n🔑 已消耗 1 个 {item_name}\n⏳ 本次驻留有效至 {expires_at.strftime('%Y-%m-%d %H:%M')}",
        }

    def set_user_fishing_zone(self, user_id: str, zone_id: int) -> Dict[str, Any]:
        """
        设置用户的钓鱼区域。

        Args:
            user_id: 用户ID。
            zone_id: 要设置的钓鱼区域ID。

        Returns:
            包含操作结果的字典。
        """
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        zone = self.inventory_repo.get_zone_by_id(zone_id)
        if not zone:
            return {"success": False, "message": "钓鱼区域不存在"}

        # 检查区域是否激活
        if not zone.is_active:
            return {"success": False, "message": "该钓鱼区域暂未开放"}

        # 检查时间限制
        now = get_now()
        if zone.available_from and now < zone.available_from:
            return {"success": False, "message": f"该钓鱼区域将在 {zone.available_from.strftime('%Y-%m-%d %H:%M')} 开放"}
        
        if zone.available_until and now > zone.available_until:
            return {"success": False, "message": f"该钓鱼区域已于 {zone.available_until.strftime('%Y-%m-%d %H:%M')} 关闭"}

        old_zone_id = user.fishing_zone_id
        if old_zone_id == zone.id:
            access_result = self._ensure_current_zone_pass_or_relocate(user, zone)
            if not access_result.get("success", False):
                return access_result
            message_suffix = ""
        else:
            access_result = self._enter_zone_with_pass(user, zone)
            if not access_result.get("success", False):
                return access_result
            message_suffix = access_result.get("message_suffix", "")

        user.fishing_zone_id = zone.id
        self.user_repo.update(user)
        if old_zone_id != zone.id:
            self.inventory_repo.delete_user_zone_stay(user_id, old_zone_id)

        # 构建成功消息
        success_message = f"✅已将钓鱼区域设置为 {zone.name}{message_suffix}"

        return {"success": True, "message": success_message}

    def apply_daily_taxes(self) -> None:
        """对所有高价值用户征收每日税收。逐用户检查，确保不遗漏也不重复征收。"""
        import uuid
        
        # 生成执行ID用于追踪和调试
        execution_id = uuid.uuid4().hex[:8]
        
        tax_config = self.config.get("tax", {})
        if tax_config.get("is_tax", False) is False:
            logger.info(f"[税收-{execution_id}] 税收功能未启用，跳过")
            return
        
        logger.info(f"[税收-{execution_id}] 开始检查每日资产税（执行ID: {execution_id}）")
        
        threshold = tax_config.get("threshold", 1000000)
        step_coins = tax_config.get("step_coins", 1000000)
        step_rate = tax_config.get("step_rate", 0.01)
        min_rate = tax_config.get("min_rate", 0.001)
        max_rate = tax_config.get("max_rate", 0.2)
        
        logger.info(f"[税收-{execution_id}] 税收配置：起征点={threshold}, 步长={step_coins}, 步长税率={step_rate*100}%, 最小税率={min_rate*100}%, 最大税率={max_rate*100}%")

        high_value_users = self.user_repo.get_high_value_users(threshold)
        logger.info(f"[税收-{execution_id}] 检测到 {len(high_value_users)} 个达到税收阈值的用户，开始逐个检查")
        
        total_tax_collected = 0
        taxed_user_count = 0
        skipped_user_count = 0

        for user in high_value_users:
            # 检查该用户今天是否已经被征收过税
            if self.log_repo.has_user_daily_tax_today(user.user_id, self.daily_reset_hour):
                logger.debug(f"[税收-{execution_id}] 用户 {user.user_id} 今日已缴税，跳过")
                skipped_user_count += 1
                continue
            
            tax_rate = 0.0
            # 根据资产确定税率
            if user.coins >= threshold:
                steps = (user.coins - threshold) // step_coins
                tax_rate = min_rate + steps * step_rate
                if tax_rate > max_rate:
                    tax_rate = max_rate
            min_tax_amount = 1
            if tax_rate > 0:
                tax_amount = max(int(user.coins * tax_rate), min_tax_amount)
                original_coins = user.coins
                user.coins -= tax_amount

                self.user_repo.update(user)

                tax_log = TaxRecord(
                    tax_id=0, # DB会自增
                    user_id=user.user_id,
                    tax_amount=tax_amount,
                    tax_rate=tax_rate,
                    original_amount=original_coins,
                    balance_after=user.coins,
                    timestamp=get_now(),
                    tax_type="每日资产税"
                )
                self.log_repo.add_tax_record(tax_log)
                
                add_wipe_bomb_jackpot(tax_amount, self.daily_reset_hour)
                total_tax_collected += tax_amount
                taxed_user_count += 1
        
        logger.info(f"[税收-{execution_id}] 每日资产税执行完成，征税 {taxed_user_count} 人，跳过 {skipped_user_count} 人（已缴税），总计 {total_tax_collected} 金币")

    def enforce_zone_pass_requirements_for_all_users(self) -> None:
        """
        每日刷新检查：消耗型通行证到达新周期后自动续票，无法续票则回传安全区域。
        永久通行证不走日周期授权，只校验背包持有状态。
        """
        logger.info("开始执行区域通行证驻留检查...")
        try:
            all_users = self.user_repo.get_all_users(limit=1000000, offset=0)
            logger.info(f"找到 {len(all_users)} 个用户需要检查")
        except Exception as e:
            logger.error(f"获取用户列表失败: {e}")
            return

        relocated_users = []
        renewed_count = 0

        for user in all_users:
            try:
                user_id = user.user_id

                zone = self.inventory_repo.get_zone_by_id(user.fishing_zone_id)
                if not zone or not getattr(zone, "requires_pass", False) or not getattr(zone, "required_item_id", None):
                    continue

                before_zone_id = user.fishing_zone_id
                result = self._ensure_current_zone_pass_or_relocate(user, zone)
                if not result.get("success", False):
                    item_template = self.item_template_repo.get_item_by_id(zone.required_item_id)
                    item_name = item_template.name if item_template else f"道具ID{zone.required_item_id}"
                    relocated_users.append({
                        "user_id": user_id,
                        "nickname": user.nickname,
                        "zone_name": zone.name,
                        "item_name": item_name
                    })
                elif result.get("renewed_until") and before_zone_id == zone.id:
                    renewed_count += 1
            except Exception as e:
                # 单个用户异常不影响其他用户
                logger.error(f"区域通行证驻留检查失败: {user_id}, {e}")
                continue

        logger.info(f"区域通行证驻留检查完成：续票 {renewed_count} 人，回传 {len(relocated_users)} 人")
        
        if relocated_users:
            logger.info(f"被传送用户详情：{relocated_users}")

    def _enforce_zone_pass_requirements_if_needed(self, current_reset_time=None) -> bool:
        """按每日刷新周期统一执行一次区域通行证检查。"""
        if current_reset_time is None:
            current_reset_time = get_last_reset_time(self.daily_reset_hour)
        if current_reset_time == self.last_zone_pass_check_reset_time:
            return False

        with self.zone_pass_reset_lock:
            current_reset_time = get_last_reset_time(self.daily_reset_hour)
            if current_reset_time == self.last_zone_pass_check_reset_time:
                return False

            self.enforce_zone_pass_requirements_for_all_users()
            self.last_zone_pass_check_reset_time = current_reset_time
            return True

    def _reset_rare_fish_daily_quota(self) -> bool:
        """
        检查并重置所有区域的稀有鱼每日配额计数。
        
        使用快速路径检查模式优化性能：
        1. 快速路径：无锁检查时间，如果不需要重置直接返回（99.9%的情况）
        2. 慢速路径：加锁后再次确认（double-check），避免并发问题
        
        Returns:
            bool: 如果执行了重置返回 True，否则返回 False
        """
        # 快速路径：无锁检查，避免大多数情况下的锁竞争
        current_reset_time = get_last_reset_time(self.daily_reset_hour)
        if current_reset_time == self.last_reset_time:
            # 不需要重置，直接返回（99.9%的情况）
            return False
        
        # 慢速路径：可能需要重置，获取锁后再次确认（double-check pattern）
        with self.rare_fish_reset_lock:
            # 再次检查，防止在获取锁的过程中其他线程已经执行了重置
            current_reset_time = get_last_reset_time(self.daily_reset_hour)
            if current_reset_time != self.last_reset_time:
                # 如果刷新时间点变了，执行每日重置任务
                logger.info(f"检测到刷新时间点变更（每日{self.daily_reset_hour}点刷新），从 {self.last_reset_time} 到 {current_reset_time}，开始执行稀有鱼配额重置...")
                self.last_reset_time = current_reset_time
                
                # 重置所有受配额限制区域的稀有鱼计数（4星及以上）
                all_zones = self.inventory_repo.get_all_zones()
                reset_count = 0
                for zone in all_zones:
                    if zone.daily_rare_fish_quota > 0:  # 只重置有配额的区域
                        zone.rare_fish_caught_today = 0
                        self.inventory_repo.update_fishing_zone(zone)
                        reset_count += 1
                
                logger.info(f"稀有鱼配额重置完成，共重置 {reset_count} 个区域的计数")
                return True
        
        return False

    def _cleanup_logs_if_needed(self, current_reset_time=None) -> bool:
        """按每日刷新周期集中清理需要保留上限和过期时间的日志。"""
        if current_reset_time is None:
            current_reset_time = get_last_reset_time(self.daily_reset_hour)
        if current_reset_time == self.last_log_cleanup_reset_time:
            return False

        with self.log_cleanup_lock:
            current_reset_time = get_last_reset_time(self.daily_reset_hour)
            if current_reset_time == self.last_log_cleanup_reset_time:
                return False

            cleanup_result = self.log_repo.cleanup_expired_records()
            self.last_log_cleanup_reset_time = current_reset_time
            logger.info(
                f"日志每日清理完成，刷新周期起点：{current_reset_time}，结果：{cleanup_result}"
            )
            return True

    def run_daily_maintenance_if_needed(self) -> bool:
        """统一执行每日刷新点对齐的后台维护。"""
        current_reset_time = get_last_reset_time(self.daily_reset_hour)
        maintenance_ran = False

        if self._enforce_zone_pass_requirements_if_needed(current_reset_time):
            maintenance_ran = True

        if current_reset_time != self.last_reset_time and self._reset_rare_fish_daily_quota():
            maintenance_ran = True

        if self._cleanup_logs_if_needed(current_reset_time):
            maintenance_ran = True

        return maintenance_ran

    def start_auto_fishing_task(self):
        """启动自动钓鱼的后台线程。"""
        if self.auto_fishing_thread and self.auto_fishing_thread.is_alive():
            logger.info("自动钓鱼线程已在运行中")
            return

        self.auto_fishing_running = True
        self.auto_fishing_thread = threading.Thread(target=self._auto_fishing_loop, daemon=True)
        self.auto_fishing_thread.start()
        logger.info("自动钓鱼线程已启动")

    def stop_auto_fishing_task(self):
        """停止自动钓鱼的后台线程。"""
        self.auto_fishing_running = False
        if self.auto_fishing_thread:
            self.auto_fishing_thread.join(timeout=1.0)
            logger.info("自动钓鱼线程已停止")

    def start_daily_tax_task(self):
        """启动每日税收的独立后台线程。"""
        # 使用锁确保线程创建检查和创建操作的原子性，防止重复创建线程
        with self.tax_start_lock:
            if self.tax_thread and self.tax_thread.is_alive():
                logger.info("税收线程已在运行中")
                return

            logger.info("正在启动每日税收线程...")
            self.tax_running = True
            self.tax_thread = threading.Thread(target=self._daily_tax_loop, daemon=True)
            self.tax_thread.start()
            logger.info(f"税收线程已启动，每日重置时间点：{self.daily_reset_hour}点")

    def stop_daily_tax_task(self):
        """停止每日税收的后台线程。"""
        self.tax_running = False
        if self.tax_thread:
            self.tax_thread.join(timeout=1.0)
            logger.info("税收线程已停止")

    def _daily_tax_loop(self):
        """每日税收独立循环任务，由后台线程执行。"""
        try:
            logger.info(f"[税收线程] 线程已进入运行循环，每日重置时间点：{self.daily_reset_hour}点")
            logger.info(f"[税收线程] 上次税收重置时间：{self.last_tax_reset_time}")
        except Exception as e:
            logger.error(f"[税收线程] 初始化日志输出失败: {e}")
        
        # 立即执行第一次检查，避免在重置时间点后重启时错过当天的税收
        first_check = True
        
        while self.tax_running:
            try:
                # 第一次检查不sleep，之后每小时检查一次
                if not first_check:
                    time.sleep(3600)
                
                # 检查是否到达每日重置时间点
                current_reset_time = get_last_reset_time(self.daily_reset_hour)
                
                # 判断是否需要执行税收检查：
                # 1. 时间点变更（跨天了）- 新的一天开始，需要检查所有用户
                # 2. 或者首次启动 - 检查是否有遗漏的用户（逐用户检查会自动跳过已缴税的用户）
                should_execute = False
                
                if current_reset_time != self.last_tax_reset_time:
                    # 时间点变更，新的一天开始
                    logger.info(f"[税收线程] 检测到刷新时间点变更（每日{self.daily_reset_hour}点刷新），从 {self.last_tax_reset_time} 到 {current_reset_time}")
                    should_execute = True
                    self.last_tax_reset_time = current_reset_time
                elif first_check:
                    # 首次检查，检查是否有遗漏的用户（逐用户检查会自动避免重复扣税）
                    logger.info(f"[税收线程] 首次检查，将检查所有高资产用户的缴税情况（已缴税用户会自动跳过）")
                    should_execute = True
                
                # 首次检查完成后，标记为非首次
                first_check = False
                
                if should_execute:
                    # 使用锁来防止并发执行税收（多层防护的第一层）
                    with self.tax_execution_lock:
                        logger.info("[税收线程] 已获取税收执行锁，开始执行税收")
                        self.apply_daily_taxes()
                        logger.info("[税收线程] 每日税收执行完成，释放锁")
                
            except Exception as e:
                logger.error(f"[税收线程] 出错: {e}")
                import traceback
                logger.error(traceback.format_exc())
                time.sleep(600)  # 出错后等待10分钟再重试
        
        logger.info("[税收线程] 线程循环已退出")

    def _auto_fishing_loop(self):
        """自动钓鱼循环任务，由后台线程执行。"""
        fishing_config = self.config.get("fishing", {})
        cooldown = fishing_config.get("cooldown_seconds", 180)
        base_sleep = 40

        while self.auto_fishing_running:
            try:
                # 每轮重新读取桶数，便于热更新配置
                bucket_count = max(1, int(fishing_config.get("auto_bucket_count", 4)))
                current_bucket = self._auto_bucket_tick % bucket_count

                self.run_daily_maintenance_if_needed()

                now = get_now()
                now_ts = now.timestamp()
                bait_template_cache = {}
                zone_cache = {}

                # 直接批量获取开启自动钓鱼的用户，避免 N+1 查询。
                auto_users = self.user_repo.get_auto_fishing_users()

                # 分桶：用 user_id 稳定哈希过滤出本轮值班的桶
                if bucket_count > 1:
                    auto_users = [
                        u for u in auto_users
                        if zlib.crc32(u.user_id.encode("utf-8")) % bucket_count == current_bucket
                    ]

                for user in auto_users:
                    user_id = user.user_id

                    # 检查CD
                    last_ts = 0
                    if user.last_fishing_time and user.last_fishing_time.year > 1:
                        last_ts = user.last_fishing_time.timestamp()
                    elif user.last_fishing_time and user.last_fishing_time.year <= 1:
                        # 若 last_fishing_time 被重置为极早时间，将时间设为当前时间减去冷却时间，
                        # 这样下一轮自动钓鱼就能正常工作了
                        user.last_fishing_time = now - timedelta(seconds=cooldown)
                        self.user_repo.update(user)
                        last_ts = user.last_fishing_time.timestamp()
                    # 计算基于鱼饵星级的CD减少
                    _cooldown = cooldown
                    if user.current_bait_id:
                        if user.current_bait_id not in bait_template_cache:
                            bait_template_cache[user.current_bait_id] = self.item_template_repo.get_bait_by_id(user.current_bait_id)
                        bait_template = bait_template_cache[user.current_bait_id]
                        if bait_template and bait_template.rarity >= 5:
                            # 5星开始，每星减少10%，上限60%（10星）
                            reduction_percent = min((bait_template.rarity - 4) * 0.1, 0.6)
                            _cooldown = cooldown * (1.0 - reduction_percent)
                    if now_ts - last_ts < _cooldown:
                        continue # CD中，跳过

                    # 检查成本（从区域配置中读取）
                    if user.fishing_zone_id not in zone_cache:
                        zone_cache[user.fishing_zone_id] = self.inventory_repo.get_zone_by_id(user.fishing_zone_id)
                    zone = zone_cache[user.fishing_zone_id]
                    if not zone:
                        continue
                    access_result = self._ensure_current_zone_pass_or_relocate(user, zone)
                    if not access_result.get("success", False):
                        try:
                            if self._notifier:
                                self._notifier(user_id, f"🔑 {access_result.get('message', '区域通行证已失效')}")
                        except Exception:
                            pass
                        continue
                    fishing_cost = zone.fishing_cost
                    if not user.can_afford(fishing_cost):
                        # 金币不足，关闭其自动钓鱼
                        user.auto_fishing_enabled = False
                        self.user_repo.update(user)
                        logger.warning(f"用户 {user_id} 金币不足（需要 {fishing_cost} 金币），已关闭自动钓鱼")
                        continue

                    # 执行钓鱼
                    result = self._go_fish_with_user(user, skip_daily_maintenance=True, is_auto=True)
                    
                    # 检查是否因为区域关闭被传送
                    if result and not result.get("success") and "已自动传送回" in result.get("message", ""):
                        # 区域关闭，给用户发送通知
                        try:
                            if self._notifier:
                                self._notifier(user_id, f"🌅 {result['message']}")
                        except Exception:
                            # 通知失败不影响主流程
                            pass
                    
                    # 自动钓鱼时，如装备损坏，尝试进行消息推送
                    if result and result.get("equipment_broken_messages"):
                        for msg in result["equipment_broken_messages"]:
                            try:
                                if self._notifier:
                                    self._notifier(user_id, msg)
                            except Exception:
                                # 通知失败不影响主流程
                                pass
                    # if result['success']:
                    #     fish = result["fish"]
                    #     logger.info(f"用户 {user_id} 自动钓鱼成功: {fish['name']}")
                    # else:
                    #      logger.info(f"用户 {user_id} 自动钓鱼失败: {result['message']}")

                # 每轮检查间隔：分桶时缩短为 base_sleep / bucket_count，
                # 让每个玩家被检查的总频率与不分桶时一致
                self._auto_bucket_tick += 1
                time.sleep(max(1.0, base_sleep / bucket_count))

            except Exception as e:
                logger.error(f"自动钓鱼任务出错: {e}")
                # 打印堆栈信息
                import traceback
                logger.error(traceback.format_exc())
                time.sleep(60)
