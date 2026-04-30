from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from ..core.utils import get_now
from ..utils import safe_datetime_handler, to_percentage, safe_get_file_path
from ..draw.pokedex import draw_pokedex
from astrbot.api.message_components import Image as AstrImage
from typing import TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from ..main import FishingPlugin


def _normalize_now_for(lst_time):
    """根据 lst_time 的时区信息，规范化当前时间的 tzinfo。"""
    now = get_now()
    if lst_time and lst_time.tzinfo is None and now.tzinfo is not None:
        return now.replace(tzinfo=None)
    if lst_time and lst_time.tzinfo is not None and now.tzinfo is None:
        return now.replace(tzinfo=lst_time.tzinfo)
    return now


def _compute_cooldown_seconds(base_seconds, equipped_accessory, current_bait=None):
    """根据装备的鱼饵星级动态计算冷却时间。
    
    CD减少规则：
    - 5星鱼饵：减少10%
    - 6星鱼饵：减少20%
    - 7星鱼饵：减少30%
    - 8星鱼饵：减少40%
    - 9星鱼饵：减少50%
    - 10星鱼饵：减少60%
    """
    cooldown = base_seconds
    
    # 基于鱼饵星级减少CD
    if current_bait and current_bait.get("rarity"):
        rarity = current_bait["rarity"]
        if rarity >= 5:
            # 5星开始，每星减少10%，上限60%（10星）
            reduction_percent = min((rarity - 4) * 0.1, 0.6)
            cooldown = base_seconds * (1.0 - reduction_percent)
    
    return cooldown


def _build_fish_message(result, fishing_cost):
    if result["success"]:
        fish = result['fish']
        # 构建品质显示
        quality_display = ""
        if fish.get('quality_level') == 1:
            quality_display = " ✨高品质"
        
        message = (
            f"🎣 恭喜你钓到了：{fish['name']}{quality_display}\n"
            f"✨稀有度：{'★' * fish['rarity']} \n"
            f"💰价值：{fish['value']} 金币\n"
            f"💸消耗：{fishing_cost} 金币/次"
        )
        if "equipment_broken_messages" in result:
            for broken_msg in result["equipment_broken_messages"]:
                message += f"\n{broken_msg}"
        
        return message
    return f"{result['message']}\n💸消耗：{fishing_cost} 金币/次"


def _build_pokedex_reward_message(result: Dict[str, Any]) -> str:
    lines = [
        "【图鉴奖励】",
        f"当前进度：{result.get('unlocked_fish_count', 0)}/{result.get('total_fish_count', 0)}（{result.get('unlocked_percentage_text', '0.0%')}）",
    ]

    newly_claimed_rewards = result.get("newly_claimed_rewards", [])
    if newly_claimed_rewards:
        lines.append("")
        lines.append("本次领取：")
        for reward in newly_claimed_rewards:
            lines.append(
                f" - {reward['milestone_percent']}% 节点：{reward['reward_premium']} 高级货币"
            )
        lines.append(f"合计获得：{result.get('newly_claimed_premium', 0)} 高级货币")
    else:
        lines.append("")
        lines.append("当前没有可领取奖励")

    lines.append("")
    lines.append(f"已领取节点：{result.get('claimed_count', 0)}/{result.get('total_milestones', 0)}")
    lines.append(f"累计已领取：{result.get('total_claimed_premium', 0)} 高级货币")

    next_milestone = result.get("next_milestone")
    if next_milestone:
        lines.append(f"下一奖励：{next_milestone['milestone_percent']}% 节点")
        lines.append(
            f"还需数量：{next_milestone['remaining_fish_count']} 种（目标 {next_milestone['required_fish_count']}/{result.get('total_fish_count', 0)}）"
        )
    else:
        lines.append("下一奖励：已全部领取完毕")

    lines.append(f"当前高级货币：{result.get('current_premium_currency', 0)}")
    return "\n".join(lines)


class FishingHandlers:
    def __init__(self, plugin: "FishingPlugin"):
        self.plugin = plugin
        self.user_service = plugin.user_service
        self.fishing_service = plugin.fishing_service
        self.inventory_service = plugin.inventory_service
        self.gacha_service = plugin.gacha_service
        self.market_service = plugin.market_service
        self.shop_service = plugin.shop_service
        self.item_template_repo = plugin.item_template_repo
        self.achievement_service = plugin.achievement_service
        self.aquarium_service = plugin.aquarium_service
        self.exchange_service = plugin.exchange_service

    def _get_fishing_cost(self, user):
        zone = self.plugin.inventory_repo.get_zone_by_id(user.fishing_zone_id)
        return zone.fishing_cost if zone else 10

    async def fish(self, event: AstrMessageEvent):
        """钓鱼"""
        user_id = self.plugin._get_effective_user_id(event)
        user = self.plugin.user_repo.get_by_id(user_id)
        if not user:
            yield event.plain_result("❌ 您还没有注册，请先使用 /注册 命令注册。")
            return
        # 检查用户钓鱼CD
        lst_time = user.last_fishing_time
        info = self.user_service.get_user_current_accessory(user_id)
        if info["success"] is False:
            yield event.plain_result(f"❌ 获取用户饰品信息失败：{info['message']}")
            return
        equipped_accessory = info.get("accessory")
        
        # 获取装备的鱼饵信息
        current_bait = None
        if user.current_bait_id:
            bait_template = self.plugin.item_template_repo.get_bait_by_id(user.current_bait_id)
            if bait_template:
                current_bait = {
                    "bait_id": bait_template.bait_id,
                    "name": bait_template.name,
                    "rarity": bait_template.rarity
                }
        
        base_cooldown = self.plugin.game_config["fishing"]["cooldown_seconds"]
        cooldown_seconds = _compute_cooldown_seconds(base_cooldown, equipped_accessory, current_bait)
        # 修复时区问题
        now = _normalize_now_for(lst_time)
        if lst_time and (now - lst_time).total_seconds() < cooldown_seconds:
            wait_time = cooldown_seconds - (now - lst_time).total_seconds()
            yield event.plain_result(f"⏳ 您还需要等待 {int(wait_time)} 秒才能再次钓鱼。")
            return
        fishing_cost = self._get_fishing_cost(user)
        result = self.fishing_service.go_fish(user_id)
        if not result:
            yield event.plain_result("❌ 出错啦！请稍后再试。")
            return
        yield event.plain_result(_build_fish_message(result, fishing_cost))

    async def auto_fish(self, event: AstrMessageEvent):
        """自动钓鱼"""
        user_id = self.plugin._get_effective_user_id(event)
        result = self.fishing_service.toggle_auto_fishing(user_id)
        yield event.plain_result(result["message"])

    async def fishing_area(self, event: AstrMessageEvent):
        """查看当前钓鱼区域"""
        user_id = self.plugin._get_effective_user_id(event)
        args = event.message_str.split(" ")
        if len(args) < 2:
            result = self.fishing_service.get_user_fishing_zones(user_id)
            if not result:
                yield event.plain_result("❌ 出错啦！请稍后再试。")
                return
            if not result.get("success"):
                yield event.plain_result(f"❌ 查看钓鱼区域失败：{result['message']}")
                return
            zones = result.get("zones", [])
            message = "【🌊 钓鱼区域】\n"
            for zone in zones:
                status_icons = []
                if zone["whether_in_use"]:
                    status_icons.append("✅")
                if not zone["is_active"]:
                    status_icons.append("🚫")
                if zone.get("requires_pass"):
                    status_icons.append("🔑")
                status_text = " ".join(status_icons) if status_icons else ""
                message += (
                    f"区域名称: {zone['name']} (ID: {zone['zone_id']}) {status_text}\n"
                )
                message += f"描述: {zone['description']}\n"
                message += f"💰 钓鱼消耗: {zone.get('fishing_cost', 10)} 金币/次\n"
                if zone.get("requires_pass"):
                    required_item_name = zone.get("required_item_name", "通行证")
                    message += f"🔑 需要 {required_item_name} 才能进入\n"
                if zone.get("available_from") or zone.get("available_until"):
                    message += "⏰ 开放时间: "
                    if zone.get("available_from") and zone.get("available_until"):
                        from_time = zone["available_from"].strftime("%Y-%m-%d %H:%M")
                        until_time = zone["available_until"].strftime("%Y-%m-%d %H:%M")
                        message += f"{from_time} 至 {until_time}\n"
                    elif zone.get("available_from"):
                        from_time = zone["available_from"].strftime("%Y-%m-%d %H:%M")
                        message += f"{from_time} 开始\n"
                    elif zone.get("available_until"):
                        until_time = zone["available_until"].strftime("%Y-%m-%d %H:%M")
                        message += f"至 {until_time} 结束\n"
                remaining_rare = max(
                    0, zone["daily_rare_fish_quota"] - zone["rare_fish_caught_today"]
                )
                if zone.get("daily_rare_fish_quota", 0) > 0:
                    message += f"剩余稀有鱼类数量: {remaining_rare}\n"
                message += "\n"
            message += "使用「/钓鱼区域 ID」命令切换钓鱼区域。\n"
            yield event.plain_result(message)
            return
        zone_id = args[1]
        if not zone_id.isdigit():
            yield event.plain_result("❌ 钓鱼区域 ID 必须是数字，请检查后重试。")
            return
        zone_id = int(zone_id)

        # 动态获取所有有效的区域ID
        all_zones = self.plugin.fishing_zone_service.get_all_zones()
        valid_zone_ids = [zone["id"] for zone in all_zones]

        if zone_id not in valid_zone_ids:
            yield event.plain_result(
                f"❌ 无效的钓鱼区域 ID。有效ID为: {', '.join(map(str, valid_zone_ids))}"
            )
            yield event.plain_result("💡 请使用「/钓鱼区域 <ID>」命令指定区域ID")
            return

        # 切换用户的钓鱼区域
        result = self.fishing_service.set_user_fishing_zone(user_id, zone_id)
        yield event.plain_result(result["message"] if result else "❌ 出错啦！请稍后再试。")

    async def fish_pokedex(self, event: AstrMessageEvent):
        """查看鱼类图鉴"""
        user_id = self.plugin._get_effective_user_id(event)
        args = event.message_str.split()
        page = 1
        if len(args) > 1 and args[1].isdigit():
            page = int(args[1])

        pokedex_data = self.fishing_service.get_user_pokedex(user_id)
        if not pokedex_data or not pokedex_data.get("success"):
            yield event.plain_result(
                f"❌ 查看图鉴失败: {pokedex_data.get('message', '未知错误')}"
            )
            return

        pokedex_list = pokedex_data.get("pokedex", [])
        if not pokedex_list:
            yield event.plain_result("❌ 您还没有捕捉到任何鱼类，快去钓鱼吧！")
            return

        user_info = self.plugin.user_repo.get_by_id(user_id)

        # 绘制图片
        output_path = safe_get_file_path(self.plugin, f"pokedex_{user_id}_page_{page}.png")

        try:
            await draw_pokedex(
                pokedex_data,
                {"nickname": user_info.nickname, "user_id": user_id},
                output_path,
                page=page,
                data_dir=self.plugin.data_dir,
            )
            yield event.image_result(output_path)
        except Exception as e:
            logger.error(f"绘制图鉴图片失败: {e}", exc_info=e)
            yield event.plain_result("❌ 绘制图鉴时发生错误，请稍后再试或联系管理员。")

    async def pokedex_reward(self, event: AstrMessageEvent):
        """领取或查看图鉴奖励进度"""
        user_id = self.plugin._get_effective_user_id(event)
        result = self.fishing_service.claim_pokedex_rewards(user_id)
        if not result or not result.get("success"):
            yield event.plain_result(
                f"❌ 图鉴奖励处理失败：{(result or {}).get('message', '未知错误')}"
            )
            return

        yield event.plain_result(_build_pokedex_reward_message(result))
