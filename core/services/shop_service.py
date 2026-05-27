from typing import Dict, Any, Optional, List, Tuple, Set
from datetime import datetime, timedelta, timezone
import copy
import math
import random

# 导入仓储接口
from ..repositories.abstract_repository import (
    AbstractItemTemplateRepository,
    AbstractInventoryRepository,
    AbstractUserRepository,
    AbstractShopRepository,
)
from ..domain.models import Shop, ShopItem, ShopItemCost, ShopItemReward
from ..utils import calculate_after_refine


class ShopService:
    """封装与系统商店相关的业务逻辑（新设计：shops + shop_items）"""
    

    def __init__(
        self,
        item_template_repo: AbstractItemTemplateRepository,
        inventory_repo: AbstractInventoryRepository,
        user_repo: AbstractUserRepository,
        shop_repo: Optional[AbstractShopRepository] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.item_template_repo = item_template_repo
        self.inventory_repo = inventory_repo
        self.user_repo = user_repo
        self.shop_repo = shop_repo
        self.config = config or {}

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """解析时间字符串为 datetime 对象"""
        if not dt_str or not isinstance(dt_str, str):
            return None
        try:
            return datetime.fromisoformat(dt_str)
        except Exception:
            return None

    def _check_shop_availability(self, shop: Dict[str, Any]) -> Optional[str]:
        """检查商店是否可用，返回错误消息或None"""
        if not shop.get("is_active", True):
            return "商店未启用"
        
        now = datetime.now()
        
        # 检查日期范围
        start_time = self._parse_datetime(shop.get("start_time"))
        end_time = self._parse_datetime(shop.get("end_time"))
        if start_time and now < start_time:
            return "商店尚未开放"
        if end_time and now > end_time:
            return "商店已关闭"
        
        # 检查每日时段
        daily_start = shop.get("daily_start_time")
        daily_end = shop.get("daily_end_time")
        if daily_start and daily_end:
            current_time = now.time()
            try:
                start_hour, start_min = map(int, daily_start.split(":"))
                end_hour, end_min = map(int, daily_end.split(":"))
                start_time_today = datetime.combine(now.date(), datetime.min.time().replace(hour=start_hour, minute=start_min))
                end_time_today = datetime.combine(now.date(), datetime.min.time().replace(hour=end_hour, minute=end_min))
                
                # 处理跨日情况（如21:00-04:00）
                if start_time_today <= end_time_today:
                    # 同一天内的时间段
                    if not (start_time_today <= now <= end_time_today):
                        return f"商店营业时间：{daily_start}-{daily_end}"
                else:
                    # 跨日的时间段
                    if not (now >= start_time_today or now <= end_time_today):
                        return f"商店营业时间：{daily_start}-{daily_end}"
            except (ValueError, IndexError):
                pass  # 忽略时间格式错误
        
        return None

    def _check_item_availability(self, item: Dict[str, Any]) -> Optional[str]:
        """检查商品是否可用，返回错误消息或None"""
        if not item.get("is_active", True):
            return "商品未启用"
        
        now = datetime.now()
        
        # 检查时间范围
        start_time = self._parse_datetime(item.get("start_time"))
        end_time = self._parse_datetime(item.get("end_time"))
        if start_time and now < start_time:
            return "商品尚未开售"
        if end_time and now > end_time:
            return "商品已过期"
        
        return None

    @staticmethod
    def _format_bonus_percent(value: float) -> str:
        return f"{value * 100:+.1f}%"

    @staticmethod
    def _format_multiplier_bonus(value: float) -> str:
        return f"{(value - 1.0) * 100:+.1f}%"

    @staticmethod
    def _format_reduction_percent(value: float) -> str:
        percent = max(0.0, value) * 100
        if percent == 0:
            return "0.0%"
        return f"-{percent:.1f}%"

    @staticmethod
    def _bait_cooldown_reduction(rarity: int) -> float:
        if rarity < 5:
            return 0.0
        return min((rarity - 4) * 0.1, 0.6)

    @staticmethod
    def _attribute_chip(label: str, value: str) -> Dict[str, str]:
        return {"label": label, "value": value}

    @staticmethod
    def _refined_value(item_template: Any, field_name: str, default: float, refine_level: int, rarity: int) -> float:
        return calculate_after_refine(
            getattr(item_template, field_name, default),
            refine_level=refine_level,
            rarity=rarity,
        )

    def _build_reward_attribute_chips(self, reward_type: str, item_template: Any, reward: Dict[str, Any]) -> List[Dict[str, str]]:
        refine_level = self._coerce_positive_quantity(reward.get("reward_refine_level"), 1)
        rarity = int(getattr(item_template, "rarity", 0) or 0)

        if reward_type == "rod":
            return [
                self._attribute_chip(
                    "成功率",
                    self._format_bonus_percent(
                        self._refined_value(item_template, "success_rate_modifier", 0.0, refine_level, rarity)
                    ),
                ),
                self._attribute_chip(
                    "稀有",
                    self._format_bonus_percent(
                        self._refined_value(item_template, "bonus_rare_fish_chance", 0.0, refine_level, rarity)
                    ),
                ),
                self._attribute_chip(
                    "品质",
                    self._format_multiplier_bonus(
                        self._refined_value(item_template, "bonus_fish_quality_modifier", 1.0, refine_level, rarity)
                    ),
                ),
                self._attribute_chip(
                    "数量",
                    self._format_multiplier_bonus(
                        self._refined_value(item_template, "bonus_fish_quantity_modifier", 1.0, refine_level, rarity)
                    ),
                ),
            ]

        if reward_type == "accessory":
            return [
                self._attribute_chip(
                    "价值",
                    self._format_multiplier_bonus(
                        self._refined_value(item_template, "bonus_coin_modifier", 1.0, refine_level, rarity)
                    ),
                ),
                self._attribute_chip(
                    "稀有",
                    self._format_bonus_percent(
                        self._refined_value(item_template, "bonus_rare_fish_chance", 0.0, refine_level, rarity)
                    ),
                ),
                self._attribute_chip(
                    "品质",
                    self._format_multiplier_bonus(
                        self._refined_value(item_template, "bonus_fish_quality_modifier", 1.0, refine_level, rarity)
                    ),
                ),
                self._attribute_chip(
                    "数量",
                    self._format_multiplier_bonus(
                        self._refined_value(item_template, "bonus_fish_quantity_modifier", 1.0, refine_level, rarity)
                    ),
                ),
            ]

        if reward_type == "bait":
            return [
                self._attribute_chip("成功率", self._format_bonus_percent(getattr(item_template, "success_rate_modifier", 0.0))),
                self._attribute_chip("垃圾减少", self._format_reduction_percent(getattr(item_template, "garbage_reduction_modifier", 0.0))),
                self._attribute_chip("稀有度", self._format_bonus_percent(getattr(item_template, "rare_chance_modifier", 0.0))),
                self._attribute_chip("CD减少", self._format_reduction_percent(self._bait_cooldown_reduction(rarity))),
            ]

        if reward_type == "item":
            effect_description = getattr(item_template, "effect_description", None)
            if effect_description:
                return [self._attribute_chip("用途", str(effect_description))]

        return []

    # ---- 商店管理 ----
    def get_shops(self) -> Dict[str, Any]:
        """获取所有活跃商店"""
        if not self.shop_repo:
            return {"success": True, "shops": []}
        
        shops = self.shop_repo.get_active_shops()
        return {"success": True, "shops": shops}

    def get_shop_details(self, shop_id: int) -> Dict[str, Any]:
        """获取商店详情和商品列表"""
        if not self.shop_repo:
            return {"success": False, "message": "商店系统未初始化"}
        
        shop = self.shop_repo.get_shop_by_id(shop_id)
        if not shop:
            return {"success": False, "message": "商店不存在"}
        
        # 检查商店可用性
        availability_error = self._check_shop_availability(shop)
        if availability_error:
            return {"success": False, "message": availability_error}
        
        # 获取商店商品
        items = self.shop_repo.get_shop_items(shop_id)
        items_with_details = []
        
        for item in items:
            # 检查商品可用性
            item_error = self._check_item_availability(item)
            if item_error:
                continue  # 跳过不可用的商品
            
            # 获取成本和奖励
            costs = self.shop_repo.get_item_costs(item["item_id"])
            rewards = self.shop_repo.get_item_rewards(item["item_id"])
            self._apply_reward_quantity_defaults(rewards, costs)
            
            # 为costs补充物品名称
            for cost in costs:
                cost_type = cost.get("cost_type")
                cost_item_id = cost.get("cost_item_id")
                if cost_type and cost_item_id:
                    item_template = None
                    if cost_type == "fish":
                        item_template = self.item_template_repo.get_fish_by_id(cost_item_id)
                        if item_template:
                            # 为鱼类添加品质标记（0=普通, 1=高品质）
                            quality_level = cost.get("quality_level", 0)
                            if quality_level == 1:
                                cost["cost_item_name"] = f"{item_template.name}(✨高品质)"
                            else:
                                cost["cost_item_name"] = item_template.name
                    elif cost_type == "rod":
                        item_template = self.item_template_repo.get_rod_by_id(cost_item_id)
                        if item_template:
                            cost["cost_item_name"] = item_template.name
                    elif cost_type == "accessory":
                        item_template = self.item_template_repo.get_accessory_by_id(cost_item_id)
                        if item_template:
                            cost["cost_item_name"] = item_template.name
                    elif cost_type == "item":
                        item_template = self.item_template_repo.get_by_id(cost_item_id)
                        if item_template:
                            cost["cost_item_name"] = item_template.name
                    elif cost_type == "bait":
                        item_template = self.item_template_repo.get_bait_by_id(cost_item_id)
                        if item_template:
                            cost["cost_item_name"] = item_template.name
            
            # 为rewards补充物品名称和属性
            for reward in rewards:
                reward_type = reward.get("reward_type")
                reward_item_id = reward.get("reward_item_id")
                if reward_type and reward_item_id:
                    item_template = None
                    if reward_type == "fish":
                        item_template = self.item_template_repo.get_fish_by_id(reward_item_id)
                        if item_template:
                            # 为鱼类添加品质标记（0=普通, 1=高品质）
                            quality_level = reward.get("quality_level", 0)
                            if quality_level == 1:
                                reward["reward_item_name"] = f"{item_template.name}(✨高品质)"
                            else:
                                reward["reward_item_name"] = item_template.name
                    elif reward_type == "rod":
                        item_template = self.item_template_repo.get_rod_by_id(reward_item_id)
                        if item_template:
                            reward["reward_item_name"] = item_template.name
                    elif reward_type == "accessory":
                        item_template = self.item_template_repo.get_accessory_by_id(reward_item_id)
                        if item_template:
                            reward["reward_item_name"] = item_template.name
                    elif reward_type == "item":
                        item_template = self.item_template_repo.get_by_id(reward_item_id)
                        if item_template:
                            reward["reward_item_name"] = item_template.name
                    elif reward_type == "bait":
                        item_template = self.item_template_repo.get_bait_by_id(reward_item_id)
                        if item_template:
                            reward["reward_item_name"] = item_template.name
                    
                    if item_template:
                        reward["attribute_chips"] = self._build_reward_attribute_chips(reward_type, item_template, reward)
            
            items_with_details.append({
                "item": item,
                "costs": costs,
                "rewards": rewards,
            })
        
        return {
            "success": True,
            "shop": shop,
            "items": items_with_details
        }

    @staticmethod
    def _coerce_positive_quantity(value: Any, default: int = 1) -> int:
        try:
            quantity = int(value or default)
        except (TypeError, ValueError):
            quantity = default
        return quantity if quantity > 0 else default

    def _apply_reward_quantity_defaults(self, rewards: List[Dict[str, Any]], costs: List[Dict[str, Any]]) -> None:
        """补齐旧商店数据缺失的奖励数量，保证展示和发放一致。"""
        has_premium_cost = any(
            cost.get("cost_type") == "premium"
            and self._coerce_positive_quantity(cost.get("cost_amount"), 0) > 0
            for cost in costs
        )

        for reward in rewards:
            quantity = self._coerce_positive_quantity(
                reward.get("reward_quantity") or reward.get("reward_amount"),
                1,
            )
            if reward.get("reward_type") == "bait" and has_premium_cost and quantity <= 1:
                quantity = 500
            reward["reward_quantity"] = quantity

    # ---- 商品购买 ----
    def purchase_item(self, user_id: str, item_id: int, quantity: int = 1) -> Dict[str, Any]:
        """购买商店商品（已使用递归回溯算法优化OR逻辑）"""
        if not self.shop_repo:
            return {"success": False, "message": "商店系统未初始化"}
        
        if quantity <= 0:
            return {"success": False, "message": "数量必须大于0"}
        
        # 获取商品信息
        item = self.shop_repo.get_shop_item_by_id(item_id)
        if not item:
            return {"success": False, "message": "商品不存在"}
        
        # 检查商品可用性
        item_error = self._check_item_availability(item)
        if item_error:
            return {"success": False, "message": item_error}
        
        # 获取商店信息并检查可用性
        shop = self.shop_repo.get_shop_by_id(item["shop_id"])
        if not shop:
            return {"success": False, "message": "商店不存在"}
        
        shop_error = self._check_shop_availability(shop)
        if shop_error:
            return {"success": False, "message": shop_error}
        
        # 获取用户信息
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在"}
        
        # 库存检查
        if item.get("stock_total") is not None:
            available_stock = item["stock_total"] - item.get("stock_sold", 0)
            if available_stock < quantity:
                return {"success": False, "message": f"库存不足，剩余 {available_stock} 个"}
        
        # 限购检查
        if item.get("per_user_limit") is not None:
            purchased_total = self.shop_repo.get_user_purchased_count(user_id, item_id)
            if purchased_total + quantity > item["per_user_limit"]:
                remaining = item["per_user_limit"] - purchased_total
                if remaining <= 0:
                    return {"success": False, "message": f"该商品限购 {item['per_user_limit']} 个，您已购买完毕"}
                else:
                    return {"success": False, "message": f"超过个人限购，还可购买 {remaining} 个"}
        
        if item.get("per_user_daily_limit") is not None and item["per_user_daily_limit"] > 0:
            # 处理每日限购：数据库记录时间为UTC（SQLite CURRENT_TIMESTAMP），需将本地零点换算为UTC
            now_utc = datetime.now(timezone.utc)
            now_local = now_utc.astimezone(timezone(timedelta(hours=8)))
            local_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            start_of_day_utc = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)
            purchased_today = self.shop_repo.get_user_purchased_count(user_id, item_id, since=start_of_day_utc)
            if purchased_today + quantity > item["per_user_daily_limit"]:
                remaining = item["per_user_daily_limit"] - purchased_today
                if remaining <= 0:
                    return {"success": False, "message": f"该商品每日限购 {item['per_user_daily_limit']} 个，您今日已购买完毕"}
                else:
                    return {"success": False, "message": f"超过今日限购，今日还可购买 {remaining} 个"}
        
        # --- 核心购买逻辑重构 ---
        
        # 1. 解析成本结构
        costs_db = self.shop_repo.get_item_costs(item_id)
        cost_structure = self._get_cost_structure(costs_db, quantity)
        and_costs = cost_structure["and_costs"]
        or_choices = cost_structure["or_choices"]

        # 2. 获取用户可用资源的快照
        relevant_fish_ids: Set[int] = set()
        for cost in costs_db:
            if cost.get("cost_type") == "fish" and cost.get("cost_item_id"):
                relevant_fish_ids.add(cost["cost_item_id"])

        user_resources_copy = self._get_user_resources_copy(user)
        
        # 3. 检查并从快照中扣除必须的 AND 成本
        can_pay_and, resources_after_and = self._check_and_get_remaining_resources(user_resources_copy, and_costs)
        if not can_pay_and:
            # 使用旧的检查方法来生成具体的错误消息
            final_check = self._check_user_resources(user, and_costs)
            return {"success": False, "message": f"核心资源不足: {final_check.get('message', '未知错误')}"}

        # 4. 使用回溯算法为 OR 部分寻找一个可行的支付组合
        or_solution = self._find_payable_combination(or_choices, resources_after_and)

        if or_solution is None:
            return {"success": False, "message": "支付条件不足，无法找到满足所有选项的物品组合"}

        # 5. 合并最终成本
        final_total_costs = and_costs.copy()
        for cost_part in or_solution:
            self._merge_costs(final_total_costs, cost_part)
        
        # 6. 执行真实的交易
        self._deduct_costs(user, final_total_costs)
        rewards = self.shop_repo.get_item_rewards(item_id)
        self._apply_reward_quantity_defaults(rewards, costs_db)
        obtained_items = self._give_rewards(user_id, rewards, quantity)
        
        self.shop_repo.increase_item_sold(item_id, quantity)
        self.shop_repo.add_purchase_record(user_id, item_id, quantity)
        
        success_message = f"✅ 购买成功：{item['name']} x{quantity}"
        if obtained_items:
            unique_items = list(set(obtained_items))
            success_message += f"\n📦 获得物品：\n" + "\n".join([f"  • {item}" for item in unique_items])
        
        return {"success": True, "message": success_message}

    def _merge_costs(self, base_costs: Dict, new_costs: Dict) -> None:
        """辅助函数：将 new_costs 合并到 base_costs 中，能处理带品质的鱼类成本。"""
        base_costs["coins"] = base_costs.get("coins", 0) + new_costs.get("coins", 0)
        base_costs["premium"] = base_costs.get("premium", 0) + new_costs.get("premium", 0)

        for category in ["items", "fish", "rods", "accessories", "baits"]:
            if category not in new_costs:
                continue

            if category not in base_costs:
                base_costs[category] = {}

            for item_id, new_value in new_costs[category].items():
                # 如果是带品质的鱼类成本（值为字典）
                if isinstance(new_value, dict) and 'quantity' in new_value:
                    # 检查 base_costs 中是否已存在该鱼的记录
                    if item_id not in base_costs[category]:
                        # 首次添加，直接复制
                        base_costs[category][item_id] = new_value.copy()
                    else:
                        # 已存在，需要合并数量
                        existing_value = base_costs[category][item_id]
                        # 健壮性检查：确保品质等级一致
                        if existing_value.get('quality_level') != new_value.get('quality_level'):
                            raise ValueError(f"无法合并不同品质等级的鱼类成本: ID {item_id}")
                        
                        existing_value['quantity'] += new_value['quantity']
                # 否则，认为是普通的数量成本（值为整数）
                else:
                    base_costs[category][item_id] = base_costs[category].get(item_id, 0) + new_value

    def _get_cost_structure(self, costs: List[Dict[str, Any]], quantity: int) -> Dict[str, Any]:
        """解析数据库成本，返回包含必须成本(and_costs)和可选成本组(or_choices)的结构"""
        if not costs:
            return {"and_costs": {}, "or_choices": []}

        groups = {}
        for cost in costs:
            group_id = cost.get("group_id", 0)
            if group_id not in groups:
                groups[group_id] = []
            groups[group_id].append(cost)

        and_costs = {"coins": 0, "premium": 0, "items": {}, "fish": {}, "rods": {}, "accessories": {}, "baits": {}}
        or_choices = []

        def _get_cost_dict(cost_item: Dict, qty: int) -> Dict:
            cost_type = cost_item["cost_type"]
            amount = cost_item["cost_amount"] * qty
            item_id = cost_item.get("cost_item_id")
            quality_level = cost_item.get("quality_level", 0)  # 获取品质等级
            plural_map = {"item": "items", "fish": "fish", "rod": "rods", "accessory": "accessories", "bait": "baits"}
            if cost_type in ["coins", "premium"]:
                return {cost_type: amount}
            elif cost_type in plural_map and item_id:
                if cost_type == "fish" and quality_level > 0:
                    # 鱼类成本需要包含品质信息
                    return {plural_map[cost_type]: {item_id: {"quantity": amount, "quality_level": quality_level}}}
                else:
                    # 其他类型保持原有格式
                    return {plural_map[cost_type]: {item_id: amount}}
            return {}

        # 确保OR选项按某种稳定顺序处理，例如group_id
        sorted_groups = sorted(groups.items(), key=lambda item: item[0])

        for group_id, group_costs in sorted_groups:
            relation = group_costs[0].get("cost_relation", "and") if len(group_costs) > 1 else "and"

            if relation == "and":
                for cost in group_costs:
                    self._merge_costs(and_costs, _get_cost_dict(cost, quantity))
            elif relation == "or":
                current_or_group = [_get_cost_dict(c, quantity) for c in group_costs if _get_cost_dict(c, quantity)]
                if current_or_group:
                    or_choices.append(current_or_group)

        return {"and_costs": and_costs, "or_choices": or_choices}
    
    def _get_user_resources_copy(self, user: Any) -> Dict[str, Any]:
        """获取用户当前可用资源的快照字典，支持鱼类品质"""
        resources = {
            "coins": user.coins,
            "premium": user.premium_currency,
            "items": self.inventory_repo.get_user_item_inventory(user.user_id),
            "fish": {}, # 结构: {fish_id: {quality_level: count}}
            "rods": {},
            "accessories": {},
            "baits": self.inventory_repo.get_user_bait_inventory(user.user_id),
        }
        
        # 合并鱼塘和水族箱的鱼，并按品质区分
        fish_inventory = self.inventory_repo.get_fish_inventory(user.user_id)
        aquarium_inventory = self.inventory_repo.get_aquarium_inventory(user.user_id)
        
        for fish_item in fish_inventory + aquarium_inventory:
            fish_id = fish_item.fish_id
            quality = fish_item.quality_level
            if fish_id not in resources["fish"]:
                resources["fish"][fish_id] = {}
            resources["fish"][fish_id][quality] = resources["fish"][fish_id].get(quality, 0) + fish_item.quantity

        # 鱼竿和饰品只计入未锁定且未装备的
        for rod in self.inventory_repo.get_user_rod_instances(user.user_id):
            if not rod.is_locked and not rod.is_equipped:
                resources["rods"][rod.rod_id] = resources["rods"].get(rod.rod_id, 0) + 1
        for acc in self.inventory_repo.get_user_accessory_instances(user.user_id):
            if not acc.is_locked and not acc.is_equipped:
                resources["accessories"][acc.accessory_id] = resources["accessories"].get(acc.accessory_id, 0) + 1
        
        return resources

    def _check_and_get_remaining_resources(self, resources: Dict, cost: Dict) -> Tuple[bool, Optional[Dict]]:
        """在资源副本上检查并模拟扣除，返回是否成功和扣除后的新副本（支持鱼类品质）"""
        res_copy = copy.deepcopy(resources)
        
        if res_copy.get("coins", 0) < cost.get("coins", 0): return (False, None)
        res_copy["coins"] -= cost.get("coins", 0)

        if res_copy.get("premium", 0) < cost.get("premium", 0): return (False, None)
        res_copy["premium"] -= cost.get("premium", 0)

        for category in ["items", "rods", "accessories", "baits"]:
            if category in cost:
                for item_id_str, need_qty in cost[category].items():
                    item_id = int(item_id_str)
                    if res_copy.get(category, {}).get(item_id, 0) < need_qty:
                        return (False, None)
                    res_copy[category][item_id] -= need_qty
        
        # 单独处理鱼类，因为其结构特殊
        if "fish" in cost:
            for fish_id_str, fish_cost in cost["fish"].items():
                fish_id = int(fish_id_str)
                
                if isinstance(fish_cost, dict): # 带品质的鱼
                    need_qty = fish_cost["quantity"]
                    quality = fish_cost["quality_level"]
                else: # 普通鱼 (quality_level = 0)
                    need_qty = fish_cost
                    quality = 0
                
                user_has = res_copy.get("fish", {}).get(fish_id, {}).get(quality, 0)
                if user_has < need_qty:
                    return (False, None)
                
                res_copy["fish"][fish_id][quality] -= need_qty

        return (True, res_copy)

    def _find_payable_combination(
        self, or_choices: List[List[Dict]], resources: Dict
    ) -> Optional[List[Dict]]:
        """使用递归回溯寻找一个可行的支付组合"""
        if not or_choices:
            return []

        first_group = or_choices[0]
        remaining_groups = or_choices[1:]

        for option_cost in first_group:
            can_pay, resources_after_pay = self._check_and_get_remaining_resources(resources, option_cost)
            if can_pay:
                result = self._find_payable_combination(remaining_groups, resources_after_pay)
                if result is not None:
                    return [option_cost] + result
        
        return None

    def _check_user_resources(self, user: Any, costs: Dict[str, Any]) -> Dict[str, Any]:
        """检查用户是否有足够资源"""
        # 检查金币
        if costs.get("coins", 0) > 0 and user.coins < costs["coins"]:
            return {"success": False, "message": f"金币不足，需要 {costs['coins']} 金币"}
        
        # 检查高级货币
        if costs.get("premium", 0) > 0 and user.premium_currency < costs["premium"]:
            return {"success": False, "message": f"高级货币不足，需要 {costs['premium']}"}
        
        # 检查道具
        if costs.get("items"):
            inv_items = self.inventory_repo.get_user_item_inventory(user.user_id)
            for item_id, need_qty in costs["items"].items():
                if inv_items.get(item_id, 0) < need_qty:
                    tpl = self.item_template_repo.get_item_by_id(item_id)
                    name = tpl.name if tpl else str(item_id)
                    return {"success": False, "message": f"道具不足：{name} x{need_qty}"}

        if costs.get("baits"):
            inv_baits = self.inventory_repo.get_user_bait_inventory(user.user_id)
            for bait_id, need_qty in costs["baits"].items():
                if inv_baits.get(bait_id, 0) < need_qty:
                    tpl = self.item_template_repo.get_bait_by_id(bait_id)
                    name = tpl.name if tpl else str(bait_id)
                    return {"success": False, "message": f"鱼饵不足：{name} x{need_qty}"}
        
        # 检查鱼类（包括鱼塘和水族箱）
        if costs.get("fish"):
            for fish_id, fish_cost in costs["fish"].items():
                # fish_cost 现在是一个字典，包含 quantity 和 quality_level
                if isinstance(fish_cost, dict):
                    need_qty = fish_cost.get("quantity", 0)
                    quality_level = fish_cost.get("quality_level", 0)
                else:
                    # 兼容旧格式（直接是数量）
                    need_qty = fish_cost
                    quality_level = 0
                
                if need_qty > 0:
                    # 检查指定品质的鱼类数量
                    fish_inventory = self.inventory_repo.get_fish_inventory(user.user_id)
                    fish_item = next((item for item in fish_inventory if item.fish_id == fish_id and item.quality_level == quality_level), None)
                    available_qty = fish_item.quantity if fish_item else 0
                    
                    if available_qty < need_qty:
                        fish_tpl = self.item_template_repo.get_fish_by_id(fish_id)
                        name = fish_tpl.name if fish_tpl else str(fish_id)
                        quality_label = "✨高品质" if quality_level == 1 else "普通"
                        return {"success": False, "message": f"{quality_label}鱼类不足：{name} x{need_qty}（当前有 {available_qty} 条）"}
        
        # 检查鱼竿（排除上锁和装备中的）
        if costs.get("rods"):
            user_rods = self.inventory_repo.get_user_rod_instances(user.user_id)
            available_rods = {}
            
            for rod in user_rods:
                if not rod.is_locked and not rod.is_equipped:  # 排除上锁和装备中的鱼竿
                    rod_id = rod.rod_id
                    available_rods[rod_id] = available_rods.get(rod_id, 0) + 1
            
            for rod_id, need_qty in costs["rods"].items():
                if available_rods.get(rod_id, 0) < need_qty:
                    rod_tpl = self.item_template_repo.get_rod_by_id(rod_id)
                    name = rod_tpl.name if rod_tpl else str(rod_id)
                    return {"success": False, "message": f"可用鱼竿不足：{name} x{need_qty}（已排除上锁和装备中的鱼竿）"}
        
        # 检查饰品（排除上锁和装备中的）
        if costs.get("accessories"):
            user_accessories = self.inventory_repo.get_user_accessory_instances(user.user_id)
            available_accessories = {}
            
            for accessory in user_accessories:
                if not accessory.is_locked and not accessory.is_equipped:  # 排除上锁和装备中的饰品
                    accessory_id = accessory.accessory_id
                    available_accessories[accessory_id] = available_accessories.get(accessory_id, 0) + 1
            
            for accessory_id, need_qty in costs["accessories"].items():
                if available_accessories.get(accessory_id, 0) < need_qty:
                    accessory_tpl = self.item_template_repo.get_accessory_by_id(accessory_id)
                    name = accessory_tpl.name if accessory_tpl else str(accessory_id)
                    return {"success": False, "message": f"可用饰品不足：{name} x{need_qty}（已排除上锁和装备中的饰品）"}
        
        return {"success": True}

    def _deduct_costs(self, user: Any, costs: Dict[str, Any]) -> None:
        """扣除用户资源"""
        # 扣除金币
        if costs.get("coins", 0) > 0:
            user.coins -= costs["coins"]
        
        # 扣除高级货币
        if costs.get("premium", 0) > 0:
            user.premium_currency -= costs["premium"]
        
        # 更新用户
        self.user_repo.update(user)
        
        # 扣除道具
        if costs.get("items"):
            for item_id, need_qty in costs["items"].items():
                self.inventory_repo.decrease_item_quantity(user.user_id, item_id, need_qty)

        if costs.get("baits"):
            for bait_id, need_qty in costs["baits"].items():
                self.inventory_repo.update_bait_quantity(user.user_id, bait_id, -need_qty)
        
        # 扣除鱼类（智能扣除：优先鱼塘，后水族箱）
        if costs.get("fish"):
            for fish_id_str, fish_cost in costs["fish"].items():
                fish_id = int(fish_id_str)
                
                if isinstance(fish_cost, dict):
                    need_qty = fish_cost.get("quantity", 0)
                    quality_level = fish_cost.get("quality_level", 0)
                else:
                    # 兼容旧格式（直接是数量）
                    need_qty = fish_cost
                    quality_level = 0
                
                if need_qty > 0:
                    self.inventory_repo.deduct_fish_smart(
                        user_id=user.user_id,
                        fish_id=fish_id,
                        quantity=need_qty,
                        quality_level=quality_level
                    )
        
        # 扣除鱼竿（排除上锁和装备中的）
        if costs.get("rods"):
            user_rods = self.inventory_repo.get_user_rod_instances(user.user_id)
            for rod_id_str, need_qty in costs["rods"].items():
                rod_id = int(rod_id_str) # 确保 rod_id 是整数
                remaining_qty = need_qty
                for rod in user_rods:
                    if remaining_qty <= 0:
                        break
                    if (rod.rod_id == rod_id and 
                        not rod.is_locked and 
                        not rod.is_equipped):
                        # 删除这个鱼竿实例
                        self.inventory_repo.delete_rod_instance(rod.rod_instance_id)
                        remaining_qty -= 1
        
        # 扣除饰品（排除上锁和装备中的）
        if costs.get("accessories"):
            user_accessories = self.inventory_repo.get_user_accessory_instances(user.user_id)
            for accessory_id_str, need_qty in costs["accessories"].items():
                accessory_id = int(accessory_id_str) # 确保 accessory_id 是整数
                remaining_qty = need_qty
                for accessory in user_accessories:
                    if remaining_qty <= 0:
                        break
                    if (accessory.accessory_id == accessory_id and 
                        not accessory.is_locked and 
                        not accessory.is_equipped):
                        # 删除这个饰品实例
                        self.inventory_repo.delete_accessory_instance(accessory.accessory_instance_id)
                        remaining_qty -= 1

    def _give_rewards(self, user_id: str, rewards: List[Dict[str, Any]], quantity: int) -> List[str]:
        """发放奖励并返回获得的物品列表"""
        obtained_items = []
        
        for _ in range(quantity):
            for reward in rewards:
                reward_type = reward["reward_type"]
                reward_item_id = reward.get("reward_item_id")
                reward_quantity = self._coerce_positive_quantity(
                    reward.get("reward_quantity") or reward.get("reward_amount"),
                    1,
                )
                reward_refine_level = reward.get("reward_refine_level")
                
                if reward_type == "rod" and reward_item_id:
                    rod_tpl = self.item_template_repo.get_rod_by_id(reward_item_id)
                    self.inventory_repo.add_rod_instance(
                        user_id=user_id,
                        rod_id=reward_item_id,
                        durability=rod_tpl.durability if rod_tpl else None,
                        refine_level=reward_refine_level or 1,
                    )
                    if rod_tpl:
                        obtained_items.append(f"🎣 {rod_tpl.name}")
                
                elif reward_type == "accessory" and reward_item_id:
                    accessory_tpl = self.item_template_repo.get_accessory_by_id(reward_item_id)
                    self.inventory_repo.add_accessory_instance(
                        user_id, reward_item_id, refine_level=reward_refine_level or 1
                    )
                    if accessory_tpl:
                        obtained_items.append(f"💍 {accessory_tpl.name}")
                
                elif reward_type == "bait" and reward_item_id:
                    bait_tpl = self.item_template_repo.get_bait_by_id(reward_item_id)
                    self.inventory_repo.update_bait_quantity(user_id, reward_item_id, reward_quantity)
                    if bait_tpl:
                        obtained_items.append(f"🪱 {bait_tpl.name} x{reward_quantity}")
                
                elif reward_type == "item" and reward_item_id:
                    item_tpl = self.item_template_repo.get_item_by_id(reward_item_id)
                    self.inventory_repo.update_item_quantity(user_id, reward_item_id, reward_quantity)
                    if item_tpl:
                        obtained_items.append(f"🎁 {item_tpl.name} x{reward_quantity}")
                
                elif reward_type == "fish" and reward_item_id:
                    fish_tpl = self.item_template_repo.get_fish_by_id(reward_item_id)
                    if fish_tpl:
                        # 从数据库获取奖励的品质等级设置
                        quality_level = reward.get("quality_level", 0)
                        # 调用水族箱的库存更新方法
                        self.inventory_repo.update_aquarium_fish_quantity(user_id, reward_item_id, reward_quantity, quality_level)
                        
                        quality_label = " ✨高品质" if quality_level == 1 else ""
                        obtained_items.append(f"🐠 {fish_tpl.name}{quality_label} x{reward_quantity} (放入水族箱)")
                
                elif reward_type == "coins":
                    # 直接给用户加金币
                    user = self.user_repo.get_by_id(user_id)
                    if user:
                        user.coins += reward_quantity
                        self.user_repo.update(user)
                        obtained_items.append(f"💰 金币 x{reward_quantity}")
        
        return obtained_items

    # ---- 兼容性方法（向后兼容旧系统） ----
    def get_shop_listings(self) -> Dict[str, Any]:
        """获取商店商品列表（兼容旧接口）"""
        result: Dict[str, Any] = {"success": True}
        if not self.shop_repo:
            result["offers"] = []
            return result
        
        offers = self.shop_repo.get_active_offers()
        offers_view: List[Dict[str, Any]] = []
        
        for offer in offers:
            costs = self.shop_repo.get_item_costs(offer["item_id"])
            rewards = self.shop_repo.get_item_rewards(offer["item_id"])
            offers_view.append({
                "offer": offer,
                "costs": costs,
                "rewards": rewards,
            })
        
        result["offers"] = offers_view
        return result

    def buy_item(self, user_id: str, item_type: str, item_template_id: int, quantity: int = 1) -> Dict[str, Any]:
        """旧商店购买接口（已废弃）"""
        return {"success": False, "message": "旧商店购买已废弃，请使用新的商店系统"}

    def purchase_offer(self, user_id: str, offer_id: int, quantity: int = 1) -> Dict[str, Any]:
        """购买商品（兼容旧接口）"""
        return self.purchase_item(user_id, offer_id, quantity)

    def purchase_in_shop(self, user_id: str, shop_id: int, offer_id: int, quantity: int = 1) -> Dict[str, Any]:
        """在指定商店购买商品（兼容旧接口）"""
        return self.purchase_item(user_id, offer_id, quantity)
