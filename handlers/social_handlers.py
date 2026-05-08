import os
import time
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.message.components import At
from astrbot.api import logger
from ..draw.rank import draw_fishing_ranking
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..main import FishingPlugin


async def ranking(plugin: "FishingPlugin", event: AstrMessageEvent):
    """
    查看排行榜。
    默认按金币排名，支持钻石榜。
    默认按金币排名。
    """
    args = event.message_str.split()
    ranking_type = "coins"

    if len(args) > 1:
        sort_key = args[1]
        if sort_key in ["钻石", "宝石", "高级货币", "premium", "diamond", "diamonds", "gem", "gems"]:
            ranking_type = "premium_currency"

    # 1. 从服务层获取排行榜数据（已包含称号和装备名称）
    user_data = plugin.user_service.get_leaderboard_data(sort_by=ranking_type).get(
        "leaderboard", []
    )

    if not user_data:
        yield event.plain_result("❌ 当前没有排行榜数据。")
        return

    # 2. 向后兼容默认字段
    for user_dict in user_data:
        user_dict.setdefault("title", "无称号")
        user_dict.setdefault("fishing_rod", "无鱼竿")
        user_dict.setdefault("accessory", "无饰品")

    # 3. 绘制并发送图片
    user_id_for_filename = plugin._get_effective_user_id(event)
    unique_id = getattr(
        event, "message_id", f"{user_id_for_filename}_{int(time.time())}"
    )
    # 安全化文件名，移除特殊字符
    from ..utils import sanitize_filename
    safe_unique_id = sanitize_filename(str(unique_id))
    output_path = os.path.join(plugin.tmp_dir, f"fishing_ranking_{safe_unique_id}.png")

    draw_fishing_ranking(user_data, output_path=output_path, ranking_type=ranking_type)
    yield event.image_result(output_path)


async def steal_fish(plugin: "FishingPlugin", event: AstrMessageEvent):
    """偷鱼功能"""
    user_id = plugin._get_effective_user_id(event)
    message_obj = event.message_obj
    target_id = None
    if hasattr(message_obj, "message"):
        for comp in message_obj.message:
            if isinstance(comp, At):
                if comp.qq != message_obj.self_id:
                    target_id = str(comp.qq)
                    break

    if target_id is None:
        parts = event.message_str.strip().split()
        if len(parts) >= 2:
            target_id = parts[1].strip()

    if not target_id:
        yield event.plain_result(
            "❌ 请指定偷鱼的用户！\n用法：/偷鱼 @用户 或 /偷鱼 用户ID"
        )
        return
    target_id = plugin._resolve_external_user_id(target_id)
    if str(target_id) == str(user_id):
        yield event.plain_result("不能偷自己的鱼哦！")
        return

    result = plugin.game_mechanics_service.steal_fish(user_id, target_id)
    if result:
        yield event.plain_result(result["message"])
    else:
        yield event.plain_result("❌ 出错啦！请稍后再试。")


async def electric_fish(plugin: "FishingPlugin", event: AstrMessageEvent):
    """电鱼功能"""
    # 检查电鱼功能是否启用
    electric_fish_config = plugin.game_config.get("electric_fish", {})
    if not electric_fish_config.get("enabled", True):
        yield event.plain_result("❌ 电鱼功能已被管理员禁用！")
        return
    
    user_id = plugin._get_effective_user_id(event)
    message_obj = event.message_obj
    target_id = None
    if hasattr(message_obj, "message"):
        for comp in message_obj.message:
            if isinstance(comp, At):
                # 排除机器人本身的id
                if comp.qq != message_obj.self_id:
                    target_id = str(comp.qq)
                    break

    if target_id is None:
        parts = event.message_str.strip().split()
        if len(parts) >= 2:
            target_id = parts[1].strip()

    if not target_id:
        yield event.plain_result("❌ 请指定电鱼的用户！\n用法：/电鱼 @用户 或 /电鱼 用户ID")
        return
    target_id = plugin._resolve_external_user_id(target_id)
    if str(target_id) == str(user_id):
        yield event.plain_result("不能电自己的鱼哦！")
        return

    result = plugin.game_mechanics_service.electric_fish(user_id, target_id)
    if result:
        yield event.plain_result(result["message"])
    else:
        yield event.plain_result("❌ 出错啦！请稍后再试。")


async def view_titles(plugin: "FishingPlugin", event: AstrMessageEvent):
    """查看用户称号"""
    user_id = plugin._get_effective_user_id(event)
    titles = plugin.user_service.get_user_titles(user_id).get("titles", [])
    if titles:
        message = "【🏅 您的称号】\n"
        for title in titles:
            status = " (当前装备)" if title["is_current"] else ""
            message += f"- {title['name']} (ID: {title['title_id']}){status}\n- 描述: {title['description']}\n\n"
        yield event.plain_result(message)
    else:
        yield event.plain_result("❌ 您还没有任何称号，快去完成成就或参与活动获取吧！")


async def use_title(plugin: "FishingPlugin", event: AstrMessageEvent):
    """使用称号"""
    user_id = plugin._get_effective_user_id(event)
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result("❌ 请指定要使用的称号 ID，例如：/使用称号 1")
        return
    title_id_str = args[1]
    if not title_id_str.isdigit():
        yield event.plain_result("❌ 称号 ID 必须是数字，请检查后重试。")
        return
    result = plugin.user_service.use_title(user_id, int(title_id_str))
    yield event.plain_result(result["message"])


async def view_achievements(plugin: "FishingPlugin", event: AstrMessageEvent):
    """查看用户成就"""
    from ..utils import safe_datetime_handler

    user_id = plugin._get_effective_user_id(event)
    achievements = plugin.achievement_service.get_user_achievements(user_id).get(
        "achievements", []
    )
    if achievements:
        message = "【🏆 您的成就】\n"
        for ach in achievements:
            message += f"- {ach['name']} (ID: {ach['id']})\n"
            message += f"  描述: {ach['description']}\n"
            if ach.get("completed_at"):
                message += f"  完成时间: {safe_datetime_handler(ach['completed_at'])}\n"
            else:
                message += "  进度: {}/{}\n".format(
                    ach.get("progress", 0), ach.get("target", 1)
                )
        message += "请继续努力完成更多成就！"
        yield event.plain_result(message)
    else:
        yield event.plain_result("❌ 您还没有任何成就，快去完成任务或参与活动获取吧！")


async def tax_record(plugin: "FishingPlugin", event: AstrMessageEvent):
    """查看税收记录"""
    from ..utils import safe_datetime_handler

    user_id = plugin._get_effective_user_id(event)
    result = plugin.user_service.get_tax_record(user_id)
    if result and result["success"]:
        records = result.get("records", [])
        if not records:
            yield event.plain_result("📜 您还没有税收记录。")
            return
        message = "【📜 税收记录】\n\n"
        for record in records:
            message += f"⏱️ 时间: {safe_datetime_handler(record['timestamp'])}\n"
            message += f"💰 金额: {record['amount']} 金币\n"
            message += f"📊 描述: {record['tax_type']}\n\n"
        yield event.plain_result(message)
    else:
        yield event.plain_result(f"❌ 查看税收记录失败：{result.get('message', '未知错误')}")
