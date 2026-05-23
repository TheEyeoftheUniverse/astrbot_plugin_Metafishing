import asyncio

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.permission import PermissionType
from astrbot.api.message_components import At, Node, Plain

from ..utils import parse_target_user_id, _is_port_available, parse_amount
from ..manager.server import create_app
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..main import FishingPlugin


async def modify_coins(plugin: "FishingPlugin", event: AstrMessageEvent):
    """修改用户金币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查金币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定金币数量，例如：/修改金币 @用户 1000 或 /修改金币 123456789 1000"
        )
        return

    coins = args[2]
    if not coins.isdigit():
        yield event.plain_result("❌ 金币数量必须是数字，请检查后重试。")
        return

    if result := plugin.user_service.modify_user_coins(target_user_id, int(coins)):
        yield event.plain_result(
            f"✅ 成功修改用户 {target_user_id} 的金币数量为 {coins} 金币"
        )
    else:
        yield event.plain_result("❌ 出错啦！请稍后再试。")


async def modify_premium(plugin: "FishingPlugin", event: AstrMessageEvent):
    """修改用户高级货币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查高级货币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定高级货币数量，例如：/修改高级货币 @用户 100 或 /修改高级货币 123456789 100"
        )
        return

    premium = args[2]
    if not premium.isdigit():
        yield event.plain_result("❌ 高级货币数量必须是数字，请检查后重试。")
        return

    user = plugin.user_repo.get_by_id(target_user_id)
    if not user:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    user.premium_currency = int(premium)
    plugin.user_repo.update(user)
    yield event.plain_result(f"✅ 成功修改用户 {target_user_id} 的高级货币为 {premium}")


async def reward_premium(plugin: "FishingPlugin", event: AstrMessageEvent):
    """奖励用户高级货币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查高级货币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定高级货币数量，例如：/奖励高级货币 @用户 100 或 /奖励高级货币 123456789 100"
        )
        return

    premium = args[2]
    if not premium.isdigit():
        yield event.plain_result("❌ 高级货币数量必须是数字，请检查后重试。")
        return

    user = plugin.user_repo.get_by_id(target_user_id)
    if not user:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    user.premium_currency += int(premium)
    plugin.user_repo.update(user)
    yield event.plain_result(f"✅ 成功给用户 {target_user_id} 奖励 {premium} 高级货币")


async def deduct_premium(plugin: "FishingPlugin", event: AstrMessageEvent):
    """扣除用户高级货币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查高级货币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定高级货币数量，例如：/扣除高级货币 @用户 100 或 /扣除高级货币 123456789 100"
        )
        return

    premium = args[2]
    if not premium.isdigit():
        yield event.plain_result("❌ 高级货币数量必须是数字，请检查后重试。")
        return

    user = plugin.user_repo.get_by_id(target_user_id)
    if not user:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    if int(premium) > user.premium_currency:
        yield event.plain_result("❌ 扣除的高级货币不能超过用户当前拥有数量")
        return
    user.premium_currency -= int(premium)
    plugin.user_repo.update(user)
    yield event.plain_result(f"✅ 成功扣除用户 {target_user_id} 的 {premium} 高级货币")


async def reward_all_coins(plugin: "FishingPlugin", event: AstrMessageEvent):
    """给所有注册用户发放金币"""
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result("❌ 请指定奖励的金币数量，例如：/全体奖励金币 1000 或 /全体奖励金币 一万")
        return
    
    try:
        amount_int = parse_amount(args[1])
        if amount_int <= 0:
            yield event.plain_result("❌ 奖励数量必须是正整数，请检查后重试。")
            return
    except ValueError as e:
        yield event.plain_result(f"❌ 数量格式错误：{str(e)}")
        return
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return
    updated = 0
    for uid in user_ids:
        user = plugin.user_repo.get_by_id(uid)
        if not user:
            continue
        user.coins += amount_int
        plugin.user_repo.update(user)
        updated += 1
    yield event.plain_result(f"✅ 已向 {updated} 位用户每人发放 {amount_int} 金币")


async def reward_all_premium(plugin: "FishingPlugin", event: AstrMessageEvent):
    """给所有注册用户发放高级货币"""
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result(
            "❌ 请指定奖励的高级货币数量，例如：/全体奖励高级货币 100"
        )
        return
    amount = args[1]
    if not amount.isdigit() or int(amount) <= 0:
        yield event.plain_result("❌ 奖励数量必须是正整数，请检查后重试。")
        return
    amount_int = int(amount)
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return
    updated = 0
    for uid in user_ids:
        user = plugin.user_repo.get_by_id(uid)
        if not user:
            continue
        user.premium_currency += amount_int
        plugin.user_repo.update(user)
        updated += 1
    yield event.plain_result(f"✅ 已向 {updated} 位用户每人发放 {amount_int} 高级货币")


async def deduct_all_coins(plugin: "FishingPlugin", event: AstrMessageEvent):
    """从所有注册用户扣除金币（不低于0）"""
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result("❌ 请指定扣除的金币数量，例如：/全体扣除金币 1000")
        return
    amount = args[1]
    if not amount.isdigit() or int(amount) <= 0:
        yield event.plain_result("❌ 扣除数量必须是正整数，请检查后重试。")
        return
    amount_int = int(amount)
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return
    affected = 0
    total_deducted = 0
    for uid in user_ids:
        user = plugin.user_repo.get_by_id(uid)
        if not user:
            continue
        if user.coins <= 0:
            continue
        deduct = min(user.coins, amount_int)
        if deduct <= 0:
            continue
        user.coins -= deduct
        plugin.user_repo.update(user)
        affected += 1
        total_deducted += deduct
    yield event.plain_result(
        f"✅ 已从 {affected} 位用户总计扣除 {total_deducted} 金币（每人至多 {amount_int}）"
    )


async def deduct_all_premium(plugin: "FishingPlugin", event: AstrMessageEvent):
    """从所有注册用户扣除高级货币（不低于0）"""
    args = event.message_str.split(" ")
    if len(args) < 2:
        yield event.plain_result(
            "❌ 请指定扣除的高级货币数量，例如：/全体扣除高级货币 100"
        )
        return
    amount = args[1]
    if not amount.isdigit() or int(amount) <= 0:
        yield event.plain_result("❌ 扣除数量必须是正整数，请检查后重试。")
        return
    amount_int = int(amount)
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return
    affected = 0
    total_deducted = 0
    for uid in user_ids:
        user = plugin.user_repo.get_by_id(uid)
        if not user:
            continue
        if user.premium_currency <= 0:
            continue
        deduct = min(user.premium_currency, amount_int)
        if deduct <= 0:
            continue
        user.premium_currency -= deduct
        plugin.user_repo.update(user)
        affected += 1
        total_deducted += deduct
    yield event.plain_result(
        f"✅ 已从 {affected} 位用户总计扣除 {total_deducted} 高级货币（每人至多 {amount_int}）"
    )


async def reward_coins(plugin: "FishingPlugin", event: AstrMessageEvent):
    """奖励用户金币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查金币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定金币数量，例如：/奖励金币 @用户 1000 或 /奖励金币 @用户 一万"
        )
        return

    try:
        coins = parse_amount(args[2])
        if coins <= 0:
            yield event.plain_result("❌ 金币数量必须是正整数，请检查后重试。")
            return
    except ValueError as e:
        yield event.plain_result(f"❌ 数量格式错误：{str(e)}")
        return

    if (current_coins := plugin.user_service.get_user_currency(target_user_id)) is None:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    if result := plugin.user_service.modify_user_coins(
        target_user_id, int(current_coins.get("coins") + coins)
    ):
        yield event.plain_result(f"✅ 成功给用户 {target_user_id} 奖励 {coins} 金币")
    else:
        yield event.plain_result("❌ 出错啦！请稍后再试。")


async def deduct_coins(plugin: "FishingPlugin", event: AstrMessageEvent):
    """扣除用户金币"""
    args = event.message_str.split(" ")

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return

    # 检查金币数量参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定金币数量，例如：/扣除金币 @用户 1000 或 /扣除金币 123456789 1000"
        )
        return

    coins = args[2]
    if not coins.isdigit():
        yield event.plain_result("❌ 金币数量必须是数字，请检查后重试。")
        return

    if (current_coins := plugin.user_service.get_user_currency(target_user_id)) is None:
        yield event.plain_result("❌ 用户不存在或未注册，请检查后重试。")
        return
    if int(coins) > current_coins.get("coins"):
        yield event.plain_result("❌ 扣除的金币数量不能超过用户当前拥有的金币数量")
        return
    if result := plugin.user_service.modify_user_coins(
        target_user_id, int(current_coins.get("coins") - int(coins))
    ):
        yield event.plain_result(f"✅ 成功扣除用户 {target_user_id} 的 {coins} 金币")
    else:
        yield event.plain_result("❌ 出错啦！请稍后再试。")


async def start_admin(plugin: "FishingPlugin", event: AstrMessageEvent):
    if plugin.web_admin_task and not plugin.web_admin_task.done():
        yield event.plain_result("❌ 钓鱼后台管理已经在运行中")
        return
    yield event.plain_result("🔄 正在启动钓鱼插件Web管理后台...")

    await plugin._cancel_stale_admin_webui_tasks()

    if not await _is_port_available(plugin.port):
        yield event.plain_result(f"❌ 端口 {plugin.port} 已被占用，请更换端口后重试")
        return

    try:
        services_to_inject = {
            "item_template_service": plugin.item_template_service,
            "user_service": plugin.user_service,
            "market_service": plugin.market_service,
            "fishing_zone_service": plugin.fishing_zone_service,
            "shop_service": plugin.shop_service,
            "exchange_service": plugin.exchange_service,
        }
        app = create_app(secret_key=plugin.secret_key, services=services_to_inject)
        plugin._web_admin_shutdown_event = asyncio.Event()
        plugin.web_admin_task = asyncio.create_task(plugin._run_admin_webui(app))

        # 等待服务启动
        for i in range(10):
            if await plugin._check_port_active():
                break
            await asyncio.sleep(1)
        else:
            raise TimeoutError("⌛ 启动超时，请检查防火墙设置")

        await asyncio.sleep(1)  # 等待服务启动

        yield event.plain_result(
            f"✅ 钓鱼后台已启动！\n🔗请访问 http://localhost:{plugin.port}/admin\n🔑 密钥请到配置文件中查看\n\n⚠️ 重要提示：\n• 如需公网访问，请自行配置端口转发和防火墙规则\n• 确保端口 {plugin.port} 已开放并映射到公网IP\n• 建议使用反向代理（如Nginx）增强安全性"
        )
    except Exception as e:
        await plugin._shutdown_server_task(
            "web_admin_task",
            "_web_admin_shutdown_event",
            "钓鱼后台管理",
        )
        logger.error(f"启动后台失败: {e}", exc_info=True)
        yield event.plain_result(f"❌ 启动后台失败: {e}")


async def stop_admin(plugin: "FishingPlugin", event: AstrMessageEvent):
    """关闭钓鱼后台管理"""
    if (
        not hasattr(plugin, "web_admin_task")
        or not plugin.web_admin_task
        or plugin.web_admin_task.done()
    ):
        yield event.plain_result("❌ 钓鱼后台管理没有在运行中")
        return

    try:
        await plugin._shutdown_server_task(
            "web_admin_task",
            "_web_admin_shutdown_event",
            "钓鱼后台管理",
        )
        logger.info("钓鱼插件Web管理后台已成功关闭")
        yield event.plain_result("✅ 钓鱼后台已关闭")
    except Exception as e:
        logger.error(f"关闭钓鱼后台管理时发生意外错误: {e}", exc_info=True)
        yield event.plain_result(f"❌ 关闭钓鱼后台管理失败: {e}")


async def sync_initial_data(plugin: "FishingPlugin", event: AstrMessageEvent):
    """同步所有内置种子数据。"""
    try:
        plugin.data_setup_service.sync_all_initial_data()
        yield event.plain_result("✅ 所有内置种子数据同步成功！")
    except Exception as e:
        logger.error(f"同步内置种子数据时出错: {e}")
        yield event.plain_result(f"❌ 同步内置种子数据失败: {e}")


async def impersonate_start(plugin: "FishingPlugin", event: AstrMessageEvent):
    """管理员开始扮演一名用户。"""
    admin_id = event.get_sender_id()
    args = event.message_str.split(" ")

    # 如果已经在线，则显示当前状态
    if admin_id in plugin.impersonation_map:
        target_user_id = plugin.impersonation_map[admin_id]
        target_user = plugin.user_repo.get_by_id(target_user_id)
        nickname = target_user.nickname if target_user else "未知用户"
        yield event.plain_result(f"您当前正在代理用户: {nickname} ({target_user_id})")
        return

    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(
            f"用法: /代理上线 <目标用户ID> 或 /代理上线 @用户\n{error_msg}"
        )
        return

    target_user = plugin.user_repo.get_by_id(target_user_id)
    if not target_user:
        yield event.plain_result("❌ 目标用户不存在。")
        return

    plugin.impersonation_map[admin_id] = target_user_id
    nickname = target_user.nickname
    yield event.plain_result(
        f"✅ 您已成功代理用户: {nickname} ({target_user_id})。\n现在您发送的所有游戏指令都将以该用户的身份执行。\n使用 /代理下线 结束代理。"
    )


async def impersonate_stop(plugin: "FishingPlugin", event: AstrMessageEvent):
    """管理员结束扮演用户。"""
    admin_id = event.get_sender_id()
    if admin_id in plugin.impersonation_map:
        del plugin.impersonation_map[admin_id]
        yield event.plain_result("✅ 您已成功结束代理。")
    else:
        yield event.plain_result("❌ 您当前没有在代理任何用户。")


async def reward_all_items(plugin: "FishingPlugin", event: AstrMessageEvent):
    """给所有注册用户发放道具"""
    args = event.message_str.split(" ")
    if len(args) < 4:
        yield event.plain_result(
            "❌ 请指定道具类型、道具ID和数量，例如：/全体发放道具 item 1 5"
        )
        return

    item_type = args[1]
    item_id_str = args[2]
    quantity_str = args[3]

    # 验证道具ID
    if not item_id_str.isdigit():
        yield event.plain_result("❌ 道具ID必须是数字，请检查后重试。")
        return
    item_id = int(item_id_str)

    # 验证数量
    if not quantity_str.isdigit() or int(quantity_str) <= 0:
        yield event.plain_result("❌ 数量必须是正整数，请检查后重试。")
        return
    quantity = int(quantity_str)

    # 验证道具类型
    valid_types = ["item", "bait", "rod", "accessory"]
    if item_type not in valid_types:
        yield event.plain_result(
            f"❌ 不支持的道具类型。支持的类型：{', '.join(valid_types)}"
        )
        return

    # 验证道具是否存在
    item_template = None
    if item_type == "item":
        item_template = plugin.item_template_repo.get_item_by_id(item_id)
    elif item_type == "bait":
        item_template = plugin.item_template_repo.get_bait_by_id(item_id)
    elif item_type == "rod":
        item_template = plugin.item_template_repo.get_rod_by_id(item_id)
    elif item_type == "accessory":
        item_template = plugin.item_template_repo.get_accessory_by_id(item_id)

    if not item_template:
        yield event.plain_result(f"❌ 道具不存在，请检查道具ID和类型。")
        return

    # 获取所有用户ID
    user_ids = plugin.user_repo.get_all_user_ids()
    if not user_ids:
        yield event.plain_result("❌ 当前没有注册用户。")
        return

    # 给所有用户发放道具
    success_count = 0
    failed_count = 0

    for user_id in user_ids:
        try:
            result = plugin.user_service.add_item_to_user_inventory(
                user_id, item_type, item_id, quantity
            )
            if result.get("success", False):
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"给用户 {user_id} 发放道具失败: {e}")

    item_name = getattr(item_template, "name", f"ID:{item_id}")
    yield event.plain_result(
        f"✅ 全体发放道具完成！\n📦 道具：{item_name} x{quantity}\n✅ 成功：{success_count} 位用户\n❌ 失败：{failed_count} 位用户"
    )


async def replenish_fish_pools(plugin: "FishingPlugin", event: AstrMessageEvent):
    """补充鱼池 - 重置所有钓鱼区域的稀有鱼剩余数量"""
    try:
        # 获取所有钓鱼区域
        all_zones = plugin.inventory_repo.get_all_zones()
        
        if not all_zones:
            yield event.plain_result("❌ 没有找到任何钓鱼区域。")
            return
        
        # 重置所有有配额的区域的稀有鱼计数
        reset_count = 0
        zone_details = []
        
        for zone in all_zones:
            if zone.daily_rare_fish_quota > 0:  # 只重置有配额的区域
                zone.rare_fish_caught_today = 0
                plugin.inventory_repo.update_fishing_zone(zone)
                reset_count += 1
                zone_details.append(f"🎣 {zone.name}：配额 {zone.daily_rare_fish_quota} 条")
        
        if reset_count == 0:
            yield event.plain_result("❌ 没有找到任何有稀有鱼配额的钓鱼区域。")
            return
        
        # 构建结果消息
        result_msg = f"✅ 鱼池补充完成！已重置 {reset_count} 个钓鱼区域的稀有鱼剩余数量。\n\n"
        result_msg += "📋 重置详情：\n"
        result_msg += "\n".join(zone_details)
        result_msg += f"\n\n🔄 所有区域的稀有鱼(4星及以上)剩余数量已重置为满配额状态。"
        
        yield event.plain_result(result_msg)
        
        logger.info(f"管理员 {event.get_sender_id()} 执行了鱼池补充操作，重置了 {reset_count} 个钓鱼区域")
        
    except Exception as e:
        logger.error(f"补充鱼池时发生错误: {e}")
        yield event.plain_result(f"❌ 补充鱼池时发生错误：{str(e)}")
        return


async def manual_daily_refresh(plugin: "FishingPlugin", event: AstrMessageEvent):
    """[管理员] 手动触发项目内可安全重跑的每日刷新逻辑。"""
    try:
        maintenance_result = plugin.fishing_service.force_daily_maintenance()
        title_refresh_result = plugin.gameplay_title_service.force_daily_refresh()
        tax_result = plugin.fishing_service.apply_daily_taxes()
        exchange_result = plugin.exchange_service.manual_update_prices()
        if not exchange_result.get("success", False):
            raise RuntimeError(exchange_result.get("message", "交易所价格刷新失败"))
        if maintenance_result.get("tribulation_error"):
            raise RuntimeError(f"渡劫 tick 失败：{maintenance_result.get('tribulation_error')}")

        quips_result = None
        aquarium_quips_service = getattr(plugin, "aquarium_quips_service", None)
        if aquarium_quips_service is not None:
            quips_result = await aquarium_quips_service.refresh_today_quips()

        zone_pass = maintenance_result.get("zone_pass") or {}
        lines = [
            "✅ 手动每日刷新已执行",
            f"🕒 刷新基准周期：{maintenance_result.get('reset_time', 'unknown')}",
            "",
            "🎣 钓鱼维护：",
            f"  · 通行证检查：续票 {zone_pass.get('renewed_count', 0)} 人，回传 {zone_pass.get('relocated_count', 0)} 人",
            f"  · 稀有鱼配额重置区域：{maintenance_result.get('rare_fish_reset_count', 0)} 个",
            f"  · 日志清理结果：{maintenance_result.get('log_cleanup_result')}",
            "",
            "💰 每日资产税：",
            f"  · 已征税：{tax_result.get('taxed_user_count', 0)} 人",
            f"  · 已跳过：{tax_result.get('skipped_user_count', 0)} 人",
            f"  · 累计税额：{tax_result.get('total_tax_collected', 0)} 金币",
            "",
            "📈 交易所：",
            f"  · 本次刷新商品数：{len(exchange_result.get('prices') or {})}",
            "",
            "🐠 水族箱短评：",
        ]
        if quips_result is None:
            lines.append("  · 未启用短评服务")
        else:
            lines.append(
                f"  · 来源：{quips_result.get('source', 'unknown')}，鱼种数：{quips_result.get('fish_count', 0)}，日期：{quips_result.get('date', 'unknown')}"
            )

        lines.extend(["", "🧿 渡劫："])
        tribulation_result = maintenance_result.get("tribulation_result")
        if tribulation_result is None:
            lines.append("  · 未启用渡劫服务")
        else:
            lines.append(f"  · tick 已执行：{tribulation_result}")

        lines.extend(
            [
                "",
                "🏅 玩法称号：",
                f"  · 扫描活跃玩家：{title_refresh_result.get('scanned_users', 0)}",
                f"  · 跳过僵尸号：{title_refresh_result.get('skipped_zombies', 0)}",
                f"  · 新授予：{title_refresh_result.get('granted_count', 0)}",
                f"  · 取消：{title_refresh_result.get('revoked_count', 0)}",
            ]
        )

        lines.extend([
            "",
            "ℹ️ 说明：该指令不会代替团战结算；如需团战测试，请继续使用现有团战管理指令。",
        ])
        yield event.plain_result("\n".join(lines))
    except Exception as exc:
        logger.error(f"管理员手动触发每日刷新失败: {exc}", exc_info=True)
        yield event.plain_result(f"❌ 手动触发每日刷新失败：{exc}")
        return


async def grant_title(plugin: "FishingPlugin", event: AstrMessageEvent):
    """授予用户称号"""
    args = event.message_str.split(" ")
    
    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return
    
    # 检查称号名称参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定称号名称，例如：/授予称号 @用户 钓鱼大师 或 /授予称号 123456789 钓鱼大师"
        )
        return
    
    title_name = " ".join(args[2:])  # 支持称号名称中包含空格
    
    result = plugin.user_service.grant_title_to_user_by_name(target_user_id, title_name)
    yield event.plain_result(result["message"])


async def revoke_title(plugin: "FishingPlugin", event: AstrMessageEvent):
    """移除用户称号"""
    args = event.message_str.split(" ")
    
    # 解析目标用户ID（支持@和用户ID两种方式）
    target_user_id, error_msg = parse_target_user_id(event, args, 1)
    if error_msg:
        yield event.plain_result(error_msg)
        return
    
    # 检查称号名称参数
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定称号名称，例如：/移除称号 @用户 钓鱼大师 或 /移除称号 123456789 钓鱼大师"
        )
        return
    
    title_name = " ".join(args[2:])  # 支持称号名称中包含空格
    
    result = plugin.user_service.revoke_title_from_user_by_name(target_user_id, title_name)
    yield event.plain_result(result["message"])


async def create_title(plugin: "FishingPlugin", event: AstrMessageEvent):
    """创建自定义称号"""
    args = event.message_str.split(" ")
    
    if len(args) < 3:
        yield event.plain_result(
            "❌ 请指定称号名称和描述，例如：/创建称号 称号名称 描述 [显示格式]\n"
            "显示格式可选，默认为 {name}，可以使用 {name} 和 {username} 占位符"
        )
        return
    
    title_name = args[1]
    description = " ".join(args[2:-1]) if len(args) > 3 and args[-1].startswith("{") else " ".join(args[2:])
    display_format = args[-1] if len(args) > 3 and args[-1].startswith("{") else "{name}"

    # 如果描述为空，使用默认值
    if not description:
        description = f"自定义称号：{title_name}"

    result = plugin.user_service.create_custom_title(title_name, description, display_format)
    yield event.plain_result(result["message"])


async def simulate_aquarium_income(plugin: "FishingPlugin", event: AstrMessageEvent):
    """[管理员] 模拟计算水族箱展览收益。

    用法：
        /计算水族箱奖励              对自己当前阵容做一次模拟（不写 DB / 不发钱袋）
        /计算水族箱奖励 @用户        对指定玩家
        /计算水族箱奖励 用户ID       对指定玩家
    """
    income_service = getattr(plugin, "aquarium_income_service", None)
    if income_service is None:
        yield event.plain_result("❌ 水族箱展览收益服务未启用")
        return

    args = event.message_str.split(" ")
    if len(args) >= 2 and args[1].strip():
        target_user_id, error_msg = parse_target_user_id(event, args, 1)
        if error_msg:
            yield event.plain_result(error_msg)
            return
    else:
        target_user_id = event.get_sender_id()

    if not plugin.user_repo.check_exists(target_user_id):
        yield event.plain_result(f"❌ 玩家 {target_user_id} 未注册")
        return

    try:
        result = income_service.compute_window_income(target_user_id)
    except Exception as exc:
        yield event.plain_result(f"❌ 模拟计算失败：{exc}")
        return

    raw = int(result.get("raw_score", 0) or 0)
    mult = float(result.get("equipment_multiplier", 1.0) or 1.0)
    randomness = float(result.get("randomness", 1.0) or 1.0)
    computed = int(result.get("computed_amount", 0) or 0)
    capped = int(result.get("capped_amount", 0) or 0)
    snapshot = result.get("fish_snapshot", []) or []

    rarity_summary = {}
    high_quality_count = 0
    for entry in snapshot:
        r = int(entry.get("rarity", 0) or 0)
        q = int(entry.get("quantity", 0) or 0)
        rarity_summary[r] = rarity_summary.get(r, 0) + q
        if int(entry.get("quality_level", 0) or 0) == 1:
            high_quality_count += q

    accessory_factor = 1.0
    rod_factor = 1.0
    try:
        equipped_acc = plugin.inventory_repo.get_user_equipped_accessory(target_user_id)
        if equipped_acc:
            acc_template = plugin.item_template_repo.get_accessory_by_id(equipped_acc.accessory_id)
            if acc_template and acc_template.bonus_coin_modifier:
                from ..core.utils import calculate_after_refine
                refined = calculate_after_refine(
                    float(acc_template.bonus_coin_modifier),
                    refine_level=int(equipped_acc.refine_level or 1),
                    rarity=int(acc_template.rarity or 0),
                )
                accessory_factor = 1.0 + max(0.0, refined - 1.0)
    except Exception:
        pass
    try:
        equipped_rod = plugin.inventory_repo.get_user_equipped_rod(target_user_id)
        if equipped_rod:
            rod_template = plugin.item_template_repo.get_rod_by_id(equipped_rod.rod_id)
            if rod_template and rod_template.bonus_rare_fish_chance:
                from ..core.utils import calculate_after_refine
                refined = calculate_after_refine(
                    float(rod_template.bonus_rare_fish_chance),
                    refine_level=int(equipped_rod.refine_level or 1),
                    rarity=int(rod_template.rarity or 0),
                )
                rod_factor = 1.0 + max(0.0, refined) * 0.5
    except Exception:
        pass

    pouch = income_service._pick_pouch(capped, has_fish=bool(snapshot))

    from ..core.utils import get_current_daily_marker
    today_marker = get_current_daily_marker(income_service.daily_reset_hour)
    today_str = today_marker.isoformat()
    today_claimed = income_service.income_repo.get_daily_claimed_total(target_user_id, today_str)
    DAILY_SOFT_CAP = 15_000_000

    lines = [
        f"🧪 【水族箱展览收益模拟】 user_id={target_user_id}",
        "",
        "📦 参与判定阵容（4★ 及以上）：",
    ]
    if rarity_summary:
        for r in sorted(rarity_summary.keys(), reverse=True):
            lines.append(f"  · {r}★ × {rarity_summary[r]} 条")
        if high_quality_count:
            lines.append(f"  · 其中 ✨高品质 共 {high_quality_count} 条（系数 ×2）")
    else:
        lines.append("  · 无 4★ 及以上鱼，原始积分=0")

    lines.extend([
        "",
        "🧮 公式分解：",
        f"  原始积分        = {raw}",
        f"  装备倍率        = {mult:.4f}（饰品 ×{accessory_factor:.3f} × 鱼竿 ×{rod_factor:.3f}）",
        f"  随机扰动        = {randomness:.4f}（本次模拟值，每窗口独立 [0.8, 1.2]）",
        f"  K 闸值          = 0.30",
        f"  原始 × 倍率 × 扰动 × K = {computed}",
        f"  单次硬上限 200 万 → 截顶 = {capped}",
    ])

    lines.append("")
    if pouch is None:
        lines.append("📉 钱袋预测：水族箱无 4★ 及以上鱼，不会触发结算")
    elif capped < 500:
        lines.append(f"💰 钱袋预测：保底 {pouch.item_name}（item_id={pouch.item_id}） × {pouch.quantity}（金额低于 500 兜底档位）")
    else:
        lines.append(f"💰 钱袋预测：{pouch.item_name}（item_id={pouch.item_id}） × {pouch.quantity}")

    lines.extend([
        "",
        f"📊 今日已领取吸引力累计：{today_claimed} / 软上限 {DAILY_SOFT_CAP}",
        f"  · 软上限剩余空间：{max(0, DAILY_SOFT_CAP - today_claimed)}",
        "",
        "💡 本指令仅模拟；不写入 pending、不发钱袋、不消耗任何窗口配额。",
    ])

    yield event.plain_result("\n".join(lines))
