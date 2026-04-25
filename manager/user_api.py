"""
用户WebUI API路由

提供用户端WebUI所需的所有API端点
"""

import json
import os
from quart import Blueprint, jsonify, current_app, request, session
import functools
from astrbot.api import logger
from ..core.utils import get_last_reset_time, get_today


user_api_bp = Blueprint(
    "user_api",
    __name__,
    url_prefix="/api/user"
)


def api_login_required(f):
    """API登录验证装饰器"""
    @functools.wraps(f)
    async def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "message": "未登录"}), 401
        return await f(*args, **kwargs)
    return decorated_function


def _get_player_credentials_state():
    """复用 player.server 的凭证存储，避免引入第二套登录体系"""
    from ..player import server as player_server
    return player_server.USER_CREDENTIALS, player_server._save_credentials


def _get_tavern_messages_file() -> str:
    plugin_root = os.path.dirname(os.path.dirname(__file__))
    data_dir = os.path.join(plugin_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "tavern_messages.json")


def _load_tavern_data():
    tavern_file = _get_tavern_messages_file()
    if not os.path.exists(tavern_file):
        return {"announcement": "", "messages": []}

    try:
        with open(tavern_file, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as exception:
        logger.error(f"读取酒馆数据失败: {exception}", exc_info=True)
        return {"announcement": "", "messages": []}

    if not isinstance(data, dict):
        return {"announcement": "", "messages": []}

    return {
        "announcement": data.get("announcement", "") or "",
        "messages": data.get("messages", []) if isinstance(data.get("messages", []), list) else [],
    }


def _get_all_users_for_tavern(user_repo, batch_size: int = 500):
    users = []
    offset = 0

    while True:
        batch = user_repo.get_all_users(limit=batch_size, offset=offset)
        if not batch:
            break

        users.extend(batch)
        if len(batch) < batch_size:
            break

        offset += len(batch)

    return users


def _get_equipped_item_names(inventory_repo, item_template_provider, user_id: str):
    rod_name = ""
    accessory_name = ""

    try:
        rod_instance = inventory_repo.get_user_equipped_rod(user_id)
        if rod_instance:
            rod_template = item_template_provider.get_rod_by_id(rod_instance.rod_id)
            if rod_template:
                rod_name = getattr(rod_template, "name", "") or ""
    except Exception as exception:
        logger.warning(f"读取用户 {user_id} 鱼竿名失败: {exception}")

    try:
        accessory_instance = inventory_repo.get_user_equipped_accessory(user_id)
        if accessory_instance:
            accessory_template = item_template_provider.get_accessory_by_id(accessory_instance.accessory_id)
            if accessory_template:
                accessory_name = getattr(accessory_template, "name", "") or ""
    except Exception as exception:
        logger.warning(f"读取用户 {user_id} 饰品名失败: {exception}")

    return rod_name, accessory_name


def _build_tavern_rankings(users, inventory_repo, item_template_provider, limit: int = 10):
    def _nickname(user):
        nickname = getattr(user, "nickname", "") or ""
        return nickname if nickname else f"渔夫{str(getattr(user, 'user_id', '0000'))[-4:]}"

    def _build_entries(sorted_users):
        entries = []
        for index, user in enumerate(sorted_users[:limit]):
            user_id = getattr(user, "user_id", "") or ""
            rod_name, accessory_name = _get_equipped_item_names(inventory_repo, item_template_provider, user_id)
            entries.append({
                "rank": index + 1,
                "user_id": user_id,
                "nickname": _nickname(user),
                "coins": int(getattr(user, "coins", 0) or 0),
                "diamonds": int(getattr(user, "premium_currency", 0) or 0),
                "equipped_rod_name": rod_name,
                "equipped_accessory_name": accessory_name,
            })

        return entries

    users = users or []
    coins_sorted = sorted(users, key=lambda user: int(getattr(user, "coins", 0) or 0), reverse=True)
    diamonds_sorted = sorted(users, key=lambda user: int(getattr(user, "premium_currency", 0) or 0), reverse=True)

    return {
        "coins_rankings": _build_entries(coins_sorted),
        "diamonds_rankings": _build_entries(diamonds_sorted),
    }


def _get_home_status_payload(user_id: str):
    user_repo = current_app.config["USER_REPO"]
    inventory_repo = current_app.config["INVENTORY_REPO"]
    item_template_repo = current_app.config["ITEM_TEMPLATE_REPO"]
    log_repo = current_app.config["LOG_REPO"]

    user = user_repo.get_by_id(user_id)
    if not user:
        return None

    zone_id = int(getattr(user, "fishing_zone_id", 1) or 1)
    zone_name = ""
    try:
        zone = inventory_repo.get_zone_by_id(zone_id)
        if zone:
            zone_name = getattr(zone, "name", "") or ""
    except Exception as exception:
        logger.warning(f"读取用户 {user_id} 区域名失败: {exception}")

    equipped_rod_name = ""
    try:
        rod_instance = inventory_repo.get_user_equipped_rod(user_id)
        if rod_instance:
            rod_template = item_template_repo.get_rod_by_id(rod_instance.rod_id)
            if rod_template:
                equipped_rod_name = getattr(rod_template, "name", "") or ""
    except Exception as exception:
        logger.warning(f"读取用户 {user_id} 当前鱼竿名失败: {exception}")

    equipped_accessory_name = ""
    try:
        accessory_instance = inventory_repo.get_user_equipped_accessory(user_id)
        if accessory_instance:
            accessory_template = item_template_repo.get_accessory_by_id(accessory_instance.accessory_id)
            if accessory_template:
                equipped_accessory_name = getattr(accessory_template, "name", "") or ""
    except Exception as exception:
        logger.warning(f"读取用户 {user_id} 当前饰品名失败: {exception}")

    current_bait_name = ""
    current_bait_id = int(getattr(user, "current_bait_id", 0) or 0)
    if current_bait_id > 0:
        try:
            bait_template = item_template_repo.get_bait_by_id(current_bait_id)
            if bait_template:
                current_bait_name = getattr(bait_template, "name", "") or ""
        except Exception as exception:
            logger.warning(f"读取用户 {user_id} 当前鱼饵名失败: {exception}")

    current_fish_count = 0
    try:
        fish_inventory = inventory_repo.get_fish_inventory(user_id) or []
        current_fish_count = sum(int(getattr(item, "quantity", 0) or 0) for item in fish_inventory)
    except Exception as exception:
        logger.warning(f"读取用户 {user_id} 鱼塘数量失败: {exception}")

    fish_pond_capacity = int(getattr(user, "fish_pond_capacity", 0) or 0)
    fish_pond_ratio = 0.0
    if fish_pond_capacity > 0:
        fish_pond_ratio = max(0.0, min(float(current_fish_count) / float(fish_pond_capacity), 1.0))

    has_checked_in_today = False
    try:
        has_checked_in_today = bool(log_repo.has_checked_in(user_id, get_today()))
    except Exception as exception:
        logger.warning(f"读取用户 {user_id} 签到状态失败: {exception}")

    return {
        "zone_id": zone_id,
        "zone_name": zone_name,
        "equipped_rod_name": equipped_rod_name,
        "equipped_accessory_name": equipped_accessory_name,
        "current_bait_name": current_bait_name,
        "fish_pond_current_count": current_fish_count,
        "fish_pond_capacity": fish_pond_capacity,
        "fish_pond_ratio": round(fish_pond_ratio, 4),
        "auto_fishing_enabled": bool(getattr(user, "auto_fishing_enabled", False)),
        "has_checked_in_today": has_checked_in_today,
    }


def _get_home_upgrade_payload(user_id: str):
    user_repo = current_app.config["USER_REPO"]
    inventory_service = current_app.config["INVENTORY_SERVICE"]
    aquarium_service = current_app.config["AQUARIUM_SERVICE"]
    exchange_service = current_app.config["EXCHANGE_SERVICE"]

    user = user_repo.get_by_id(user_id)
    if not user:
        return None

    pond_capacity_result = inventory_service.get_user_fish_pond_capacity(user_id)
    fish_pond_capacity = int(pond_capacity_result.get("fish_pond_capacity", getattr(user, "fish_pond_capacity", 0)) or 0)
    pond_upgrades = getattr(inventory_service, "config", {}).get("pond_upgrades", [])
    next_pond_upgrade = None
    for upgrade in pond_upgrades:
        if int(upgrade.get("from", 0) or 0) == fish_pond_capacity:
            next_pond_upgrade = upgrade
            break

    aquarium_upgrade_info = aquarium_service.get_aquarium_upgrade_info(user_id)
    next_aquarium_upgrade = aquarium_upgrade_info.get("next_upgrade") if aquarium_upgrade_info.get("success") else None
    aquarium_capacity = int(
        aquarium_upgrade_info.get("current_capacity", getattr(user, "aquarium_capacity", 0))
        if aquarium_upgrade_info.get("success")
        else getattr(user, "aquarium_capacity", 0)
    )

    futures_upgrade_info = exchange_service.get_exchange_capacity_info(user_id)
    next_futures_upgrade = futures_upgrade_info.get("next_upgrade") if futures_upgrade_info.get("success") else None
    futures_capacity = int(
        futures_upgrade_info.get("current_capacity", getattr(user, "exchange_capacity", 0))
        if futures_upgrade_info.get("success")
        else getattr(user, "exchange_capacity", 0)
    )

    return {
        "fish_pond": {
            "current_capacity": fish_pond_capacity,
            "next_capacity": int(next_pond_upgrade.get("to", fish_pond_capacity) or fish_pond_capacity) if next_pond_upgrade else fish_pond_capacity,
            "upgrade_cost_coins": int(next_pond_upgrade.get("cost", 0) or 0) if next_pond_upgrade else 0,
            "is_max_level": next_pond_upgrade is None,
        },
        "aquarium": {
            "current_capacity": aquarium_capacity,
            "next_capacity": int(getattr(next_aquarium_upgrade, "capacity", aquarium_capacity) or aquarium_capacity),
            "upgrade_cost_coins": int(getattr(next_aquarium_upgrade, "cost_coins", 0) or 0),
            "is_max_level": next_aquarium_upgrade is None,
        },
        "futures": {
            "current_capacity": futures_capacity,
            "next_capacity": int((next_futures_upgrade or {}).get("to", futures_capacity) or futures_capacity),
            "upgrade_cost_coins": int((next_futures_upgrade or {}).get("cost", 0) or 0),
            "is_max_level": next_futures_upgrade is None,
        }
    }


def _serialize_tavern_expedition(expedition):
    if not isinstance(expedition, dict):
        return None

    total_progress = float(expedition.get("total_progress", 0.0) or 0.0)
    total_progress = max(0.0, min(1.0, total_progress))
    contribution_board = _build_tavern_expedition_contribution_board(expedition)

    return {
        "expedition_id": expedition.get("expedition_id", "") or "",
        "type": expedition.get("type", "") or "",
        "creator_name": expedition.get("creator_name", "") or "",
        "member_count": int(expedition.get("member_count", 0) or 0),
        "total_progress": total_progress,
        "progress_percent": round(total_progress * 100.0, 1),
        "targets_text": _build_tavern_expedition_targets_text(expedition),
        "targets": _build_tavern_expedition_targets(expedition),
        "contribution_board": contribution_board,
    }


def _build_tavern_expedition_targets_text(expedition):
    if not isinstance(expedition, dict):
        return ""

    targets = expedition.get("targets", {}) if isinstance(expedition.get("targets", {}), dict) else {}
    if not targets:
        return ""

    lines = []
    for target in targets.values():
        if not isinstance(target, dict):
            continue

        fish_name = target.get("fish_name", "") or "未知鱼类"
        caught = int(target.get("caught", 0) or 0)
        required = int(target.get("required", 0) or 0)
        rarity = int(target.get("rarity", 0) or 0)
        prefix = "★" * max(0, min(rarity, 10))
        line = f"{prefix} {fish_name} {caught}/{required}".strip()
        lines.append(line)

    return "\n".join(lines)


def _build_tavern_expedition_targets(expedition):
    if not isinstance(expedition, dict):
        return []

    targets = expedition.get("targets", {}) if isinstance(expedition.get("targets", {}), dict) else {}
    rows = []
    for target in targets.values():
        if not isinstance(target, dict):
            continue

        caught = int(target.get("caught", 0) or 0)
        required = int(target.get("required", 0) or 0)
        rows.append({
            "fish_name": target.get("fish_name", "") or "未知鱼类",
            "rarity": int(target.get("rarity", 0) or 0),
            "caught": caught,
            "required": required,
            "progress_text": f"{caught}/{required}",
        })

    rows.sort(key=lambda item: (item["rarity"], item["fish_name"]))
    return rows


def _build_tavern_expedition_contribution_board(expedition):
    if not isinstance(expedition, dict):
        return {
            "top_contributors": [],
            "current_user": None,
        }

    participants = expedition.get("participants", {}) if isinstance(expedition.get("participants", {}), dict) else {}
    total_progress = float(expedition.get("total_progress", 0.0) or 0.0)
    total_progress = max(0.0, min(1.0, total_progress))
    creator_id = expedition.get("creator_id", "") or ""
    current_user_id = session.get("user_id", "") or ""

    total_contribution = 0
    participant_rows = []
    for user_id, participant in participants.items():
        if not isinstance(participant, dict):
            continue

        contribution_map = participant.get("contribution", {}) if isinstance(participant.get("contribution", {}), dict) else {}
        contribution_value = 0
        for amount in contribution_map.values():
            contribution_value += int(amount or 0)

        total_contribution += contribution_value
        participant_rows.append({
            "user_id": user_id,
            "nickname": participant.get("nickname", "") or "",
            "contribution": contribution_value,
            "is_creator": user_id == creator_id,
        })

    type_premium_base = {"short": 1000, "medium": 5000, "long": 10000}
    base_premium = type_premium_base.get(expedition.get("type", ""), 1000)
    total_premium = int(base_premium * total_progress)
    for row in participant_rows:
        if total_contribution > 0 and row["contribution"] > 0:
            contribution_percent = (row["contribution"] / total_contribution) * 100.0
            estimated_premium = max(1, int(total_premium * (row["contribution"] / total_contribution)))
        else:
            contribution_percent = 0.0
            estimated_premium = 0

        row["contribution_percent"] = contribution_percent
        row["estimated_reward"] = ""

    participant_rows.sort(
        key=lambda item: (
            -item["contribution"],
            0 if item["is_creator"] else 1,
            item["nickname"],
            item["user_id"]
        )
    )

    top_contributors = [
        {
            "user_id": row["user_id"],
            "nickname": row["nickname"],
            "contribution_percent": round(row["contribution_percent"], 1),
            "estimated_reward": row["estimated_reward"],
        }
        for row in participant_rows[:3]
    ]

    current_user_entry = None
    for row in participant_rows:
        if row["user_id"] == current_user_id:
            current_user_entry = {
                "user_id": row["user_id"],
                "nickname": row["nickname"],
                "contribution_percent": round(row["contribution_percent"], 1),
                "estimated_reward": row["estimated_reward"],
            }
            break

    return {
        "top_contributors": top_contributors,
        "current_user": current_user_entry,
    }


def _get_tavern_user_display_name(user) -> str:
    nickname = getattr(user, "nickname", "") or ""
    return nickname if nickname else f"渔夫{str(getattr(user, 'user_id', '0000'))[-4:]}"


def _get_daily_reset_hour() -> int:
    service = current_app.config.get("GAME_MECHANICS_SERVICE") or current_app.config.get("FISHING_SERVICE")
    config = getattr(service, "config", {}) if service else {}
    return int(config.get("daily_reset_hour", 0) or 0)


def _get_wipe_bomb_config() -> dict:
    service = current_app.config.get("GAME_MECHANICS_SERVICE") or current_app.config.get("FISHING_SERVICE")
    return getattr(service, "config", {}) if service else {}


def _get_item_template_repo_for_wipe_bomb():
    item_template_repo = current_app.config.get("ITEM_TEMPLATE_REPO")
    if item_template_repo is not None:
        return item_template_repo

    item_template_service = current_app.config.get("ITEM_TEMPLATE_SERVICE")
    if item_template_service is not None:
        return getattr(item_template_service, "item_template_repo", None)

    return None


def _get_game_mechanics_service_for_wipe_bomb():
    service = current_app.config.get("GAME_MECHANICS_SERVICE")
    if service is not None:
        return service

    try:
        from ..core.services.game_mechanics_service import GameMechanicsService

        user_repo = current_app.config["USER_REPO"]
        log_repo = current_app.config["LOG_REPO"]
        inventory_repo = current_app.config["INVENTORY_REPO"]
        buff_repo = current_app.config["BUFF_REPO"]
        item_template_repo = _get_item_template_repo_for_wipe_bomb()
        if item_template_repo is None:
            raise KeyError("ITEM_TEMPLATE_REPO/ITEM_TEMPLATE_SERVICE")

        config = _get_wipe_bomb_config()
        return GameMechanicsService(
            user_repo,
            log_repo,
            inventory_repo,
            item_template_repo,
            buff_repo,
            config
        )
    except KeyError:
        raise


def _get_wipe_bomb_publicity_file() -> str:
    plugin_root = os.path.dirname(os.path.dirname(__file__))
    data_dir = os.path.join(plugin_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "tavern_wipe_bomb_daily.json")


def _get_current_reset_marker(reset_hour: int) -> str:
    return get_last_reset_time(reset_hour).strftime("%Y-%m-%d %H:%M:%S")


def _create_empty_notice():
    return {
        "has_record": False,
        "user_id": "",
        "title_name": "",
        "nickname": "",
        "multiplier": 0.0,
        "profit": 0,
    }


def _create_default_wipe_bomb_publicity(reset_marker: str):
    return {
        "reset_marker": reset_marker,
        "king": _create_empty_notice(),
        "ghost": _create_empty_notice(),
    }


def _normalize_wipe_bomb_notice(entry):
    if not isinstance(entry, dict):
        return _create_empty_notice()

    return {
        "has_record": bool(entry.get("has_record", False)),
        "user_id": entry.get("user_id", "") or "",
        "title_name": entry.get("title_name", "") or "",
        "nickname": entry.get("nickname", "") or "",
        "multiplier": float(entry.get("multiplier", 0.0) or 0.0),
        "profit": int(entry.get("profit", 0) or 0),
    }


def _save_wipe_bomb_publicity(data):
    publicity_file = _get_wipe_bomb_publicity_file()
    with open(publicity_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def _load_wipe_bomb_publicity(reset_hour: int):
    publicity_file = _get_wipe_bomb_publicity_file()
    reset_marker = _get_current_reset_marker(reset_hour)
    default_data = _create_default_wipe_bomb_publicity(reset_marker)

    if not os.path.exists(publicity_file):
        _save_wipe_bomb_publicity(default_data)
        return default_data

    try:
        with open(publicity_file, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as exception:
        logger.error(f"读取每日擦弹公示失败: {exception}", exc_info=True)
        _save_wipe_bomb_publicity(default_data)
        return default_data

    if not isinstance(data, dict) or data.get("reset_marker") != reset_marker:
        _save_wipe_bomb_publicity(default_data)
        return default_data

    data["king"] = _normalize_wipe_bomb_notice(data.get("king"))
    data["ghost"] = _normalize_wipe_bomb_notice(data.get("ghost"))
    return data


def _get_current_title_name(item_template_provider, user) -> str:
    title_id = getattr(user, "current_title_id", None)
    if not title_id:
        return ""

    try:
        title = item_template_provider.get_title_by_id(title_id)
        return getattr(title, "name", "") or ""
    except Exception as exception:
        logger.warning(f"读取用户 {getattr(user, 'user_id', '')} 当前称号失败: {exception}")
        return ""


def _build_wipe_bomb_notice_entry(user, title_name: str, multiplier: float, profit: int):
    return {
        "has_record": True,
        "user_id": getattr(user, "user_id", "") or "",
        "title_name": title_name or "",
        "nickname": _get_tavern_user_display_name(user),
        "multiplier": float(multiplier or 0.0),
        "profit": int(profit or 0),
    }


def _update_wipe_bomb_publicity(publicity_data, user, title_name: str, multiplier: float, profit: int):
    king_entry = _normalize_wipe_bomb_notice(publicity_data.get("king"))
    ghost_entry = _normalize_wipe_bomb_notice(publicity_data.get("ghost"))

    if (not king_entry["has_record"]) or multiplier > king_entry["multiplier"]:
        publicity_data["king"] = _build_wipe_bomb_notice_entry(user, title_name, multiplier, profit)

    if (not ghost_entry["has_record"]) or multiplier < ghost_entry["multiplier"]:
        publicity_data["ghost"] = _build_wipe_bomb_notice_entry(user, title_name, multiplier, profit)

    return publicity_data


def _get_wipe_bomb_remaining_attempts(user_id: str):
    user_repo = current_app.config["USER_REPO"]
    buff_repo = current_app.config.get("BUFF_REPO")
    game_config = _get_wipe_bomb_config()
    user = user_repo.get_by_id(user_id)
    if not user:
        return 0

    if hasattr(user, "last_wipe_bomb_date") and hasattr(user, "wipe_bomb_attempts_today"):
        base_max_attempts = game_config.get("wipe_bomb", {}).get("max_attempts_per_day", 3)
        extra_attempts = 0

        if buff_repo is not None:
            boost_buff = buff_repo.get_active_by_user_and_type(user_id, "WIPE_BOMB_ATTEMPTS_BOOST")
            if boost_buff and getattr(boost_buff, "payload", None):
                try:
                    extra_attempts = json.loads(boost_buff.payload).get("amount", 0)
                except json.JSONDecodeError:
                    extra_attempts = 0

        total_max_attempts = base_max_attempts + extra_attempts
        today_str = get_today().strftime("%Y-%m-%d")
        used_attempts = user.wipe_bomb_attempts_today if user.last_wipe_bomb_date == today_str else 0
        return max(0, int(total_max_attempts) - int(used_attempts))

    return int(game_config.get("wipe_bomb", {}).get("max_attempts_per_day", 3) or 3)


def _serialize_wipe_bomb_notice(entry):
    notice = _normalize_wipe_bomb_notice(entry)
    return {
        "has_record": notice["has_record"],
        "title_name": notice["title_name"],
        "nickname": notice["nickname"],
        "multiplier": notice["multiplier"],
        "profit": notice["profit"],
    }


def _format_wipe_bomb_result_text(result: dict) -> str:
    if not isinstance(result, dict) or not result.get("success", False):
        message = result.get("message", "擦弹失败") if isinstance(result, dict) else "擦弹失败"
        return f"⚠️ 擦弹失败：{message}"

    contribution = int(result.get("contribution", 0) or 0)
    multiplier = float(result.get("multiplier", 0.0) or 0.0)
    reward = int(result.get("reward", 0) or 0)
    profit = int(result.get("profit", 0) or 0)
    remaining_today = int(result.get("remaining_today", 0) or 0)
    multiplier_formatted = f"{multiplier:.4f}" if multiplier < 0.01 else f"{multiplier:.2f}"

    if multiplier >= 3:
        message = (
            f"🎰 大成功！你投入 {contribution} 金币，获得了 {multiplier_formatted} 倍奖励！\n"
            f" 💰 奖励金额：{reward} 金币（盈利：+ {profit}）\n"
        )
    elif multiplier >= 1:
        message = (
            f"🎲 你投入 {contribution} 金币，获得了 {multiplier_formatted} 倍奖励！\n"
            f" 💰 奖励金额：{reward} 金币（盈利：+ {profit}）\n"
        )
    else:
        message = (
            f"💥 你投入 {contribution} 金币，获得了 {multiplier_formatted} 倍奖励！\n"
            f" 💰 奖励金额：{reward} 金币（亏损：- {abs(profit)})\n"
        )

    message += f"剩余擦弹次数：{remaining_today} 次\n"
    if result.get("suppression_notice"):
        message += f"\n{result['suppression_notice']}"

    return message


def _serialize_market_listing(listing):
    if listing is None:
        return None

    return {
        "market_id": getattr(listing, "market_id", 0),
        "user_id": getattr(listing, "user_id", ""),
        "seller_nickname": getattr(listing, "seller_nickname", ""),
        "item_type": getattr(listing, "item_type", ""),
        "item_id": getattr(listing, "item_id", 0),
        "item_instance_id": getattr(listing, "item_instance_id", 0) or 0,
        "item_name": getattr(listing, "item_name", ""),
        "item_description": getattr(listing, "item_description", "") or "",
        "quantity": getattr(listing, "quantity", 1),
        "price": getattr(listing, "price", 0),
        "refine_level": getattr(listing, "refine_level", 0),
        "quality_level": getattr(listing, "quality_level", 0),
        "listed_at": getattr(getattr(listing, "listed_at", None), "isoformat", lambda: None)(),
    }


def _serialize_exchange_inventory_item(item):
    return {
        "instance_id": item.get("instance_id", 0),
        "quantity": item.get("quantity", 0),
        "purchase_price": item.get("purchase_price", 0),
        "purchased_at": item.get("purchased_at").isoformat() if hasattr(item.get("purchased_at"), "isoformat") else item.get("purchased_at"),
        "expires_at": item.get("expires_at").isoformat() if hasattr(item.get("expires_at"), "isoformat") else item.get("expires_at"),
    }


def _serialize_exchange_inventory(inventory_result):
    inventory = inventory_result.get("inventory", {}) if isinstance(inventory_result, dict) else {}
    commodities = []

    for commodity_id, summary in inventory.items():
        items = summary.get("items", []) if isinstance(summary, dict) else []
        commodities.append({
            "commodity_id": commodity_id,
            "name": summary.get("name", ""),
            "total_quantity": summary.get("total_quantity", 0),
            "total_cost": summary.get("total_cost", 0),
            "items": [_serialize_exchange_inventory_item(item) for item in items if isinstance(item, dict)],
        })

    commodities.sort(key=lambda item: item.get("commodity_id", ""))
    return {
        "commodities": commodities,
        "total_items": inventory_result.get("total_items", 0) if isinstance(inventory_result, dict) else 0,
    }


def _serialize_exchange_status(status_result, tax_rate):
    prices = status_result.get("prices", {}) if isinstance(status_result, dict) else {}
    commodities = status_result.get("commodities", {}) if isinstance(status_result, dict) else {}
    commodity_list = []

    for commodity_id, info in commodities.items():
        if not isinstance(info, dict):
            continue

        commodity_list.append({
            "commodity_id": commodity_id,
            "name": info.get("name", ""),
            "description": info.get("description", ""),
            "current_price": prices.get(commodity_id, 0),
        })

    commodity_list.sort(key=lambda item: item.get("commodity_id", ""))
    return {
        "market_sentiment": status_result.get("market_sentiment", ""),
        "price_trend": status_result.get("price_trend", ""),
        "supply_demand": status_result.get("supply_demand", ""),
        "date": status_result.get("date", ""),
        "tax_rate": tax_rate,
        "commodities": commodity_list,
    }


def _serialize_exchange_history(history_result):
    labels = history_result.get("labels", []) if isinstance(history_result, dict) else []
    history = history_result.get("history", {}) if isinstance(history_result, dict) else {}
    commodities = []

    for commodity_id, prices in history.items():
        commodities.append({
            "commodity_id": commodity_id,
            "prices": prices if isinstance(prices, list) else [],
        })

    commodities.sort(key=lambda item: item.get("commodity_id", ""))
    return {
        "labels": labels if isinstance(labels, list) else [],
        "commodities": commodities,
        "days": history_result.get("days", 7) if isinstance(history_result, dict) else 7,
    }


def _serialize_gacha_pool(pool):
    if pool is None:
        return None

    return {
        "pool_id": getattr(pool, "gacha_pool_id", 0),
        "name": getattr(pool, "name", "") or "",
        "description": getattr(pool, "description", "") or "",
        "cost_coins": getattr(pool, "cost_coins", 0) or 0,
        "cost_premium_currency": getattr(pool, "cost_premium_currency", 0) or 0,
        "is_limited_time": bool(getattr(pool, "is_limited_time", 0)),
        "open_until": getattr(pool, "open_until", None),
    }


def _serialize_gacha_probability_item(item):
    if not isinstance(item, dict):
        return None

    return {
        "item_type": item.get("item_type", "") or "",
        "item_id": item.get("item_id", 0) or 0,
        "item_name": item.get("item_name", "") or "",
        "item_rarity": item.get("item_rarity", 0) or 0,
        "weight": item.get("weight", 0) or 0,
        "probability": item.get("probability", 0.0) or 0.0,
    }


def _serialize_gacha_reward_item(item):
    if not isinstance(item, dict):
        return None

    return {
        "type": item.get("type", "") or "",
        "id": item.get("id", 0) or 0,
        "name": item.get("name", "") or "",
        "rarity": item.get("rarity", 0) or 0,
        "quantity": item.get("quantity", 1) or 1,
    }


async def _read_request_payload():
    """兼容 JSON 与表单提交"""
    payload = await request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload

    form = await request.form
    return dict(form)


@user_api_bp.route("/debug/status", methods=["GET"])
async def debug_status():
    """调试端点：检查WebUI初始化状态和数据库连接"""
    try:
        status = {
            "webui_initialized": True,
            "services": {},
            "database_status": "unknown"
        }
        
        # 检查services是否存在
        try:
            user_repo = current_app.config.get("USER_REPO")
            if user_repo:
                status["services"]["user_repo"] = type(user_repo).__name__
                # 尝试查询数据库
                try:
                    test_result = user_repo.get_by_id("test_query")
                    status["database_status"] = "connected"
                    status["database_query_works"] = True
                except Exception as e:
                    status["database_status"] = "error"
                    status["database_error"] = str(e)
            else:
                status["services"]["user_repo"] = "NOT FOUND"
        except Exception as e:
            status["services"]["error"] = str(e)
        
        try:
            inventory_repo = current_app.config.get("INVENTORY_REPO")
            if inventory_repo:
                status["services"]["inventory_repo"] = type(inventory_repo).__name__
            else:
                status["services"]["inventory_repo"] = "NOT FOUND"
        except:
            pass
        
        try:
            user_service = current_app.config.get("USER_SERVICE")
            if user_service:
                status["services"]["user_service"] = type(user_service).__name__
        except:
            pass
        
        return jsonify(status)
    except Exception as e:
        logger.error(f"[WebUI] 调试端点错误: {e}")
        return jsonify({"error": str(e)}), 500


@user_api_bp.route("/login", methods=["POST"])
async def api_login():
    """JSON 登录接口，沿用 player.server 的 session cookie 方案"""
    try:
        payload = await _read_request_payload()
        user_id = str(payload.get("user_id", "")).strip()
        password = str(payload.get("password", "")).strip()

        if not user_id:
            return jsonify({"success": False, "message": "请输入用户ID"}), 400

        if not password:
            return jsonify({"success": False, "message": "请输入登录密钥"}), 400

        user_repo = current_app.config["USER_REPO"]
        user = user_repo.get_by_id(user_id)
        if not user:
            logger.warning(f"[WebUI] 未注册用户 {user_id} 尝试通过 API 登录")
            return jsonify({"success": False, "message": "该用户不存在"}), 404

        credentials, save_credentials = _get_player_credentials_state()
        first_login = user_id not in credentials

        if first_login:
            credentials[user_id] = password
            save_credentials(credentials)
            login_message = f"欢迎，{user.nickname or user_id}！密钥已设置"
        else:
            if credentials.get(user_id) != password:
                return jsonify({"success": False, "message": "密钥错误"}), 401
            login_message = f"欢迎回来，{user.nickname or user_id}！"

        session.clear()
        session["user_id"] = user_id
        session["nickname"] = user.nickname or user_id

        logger.info(f"[WebUI] 用户 {user_id} 通过 API 登录成功")
        return jsonify({
            "success": True,
            "message": login_message,
            "data": {
                "user_id": user_id,
                "nickname": user.nickname or user_id,
                "first_login": first_login
            }
        })
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: 登录所需服务未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    except Exception as e:
        logger.error(f"API登录失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"登录失败: {str(e)}"}), 500


@user_api_bp.route("/logout", methods=["POST"])
@api_login_required
async def api_logout():
    """JSON 登出接口，清理当前 session cookie"""
    user_id = session.get("user_id")
    session.clear()
    if user_id:
        logger.info(f"[WebUI] 用户 {user_id} 通过 API 登出")
    return jsonify({"success": True, "message": "已成功登出"})


@user_api_bp.route("/info", methods=["GET"])
@api_login_required
async def get_user_info():
    """获取当前登录用户信息"""
    user_id = session.get("user_id")
    
    try:
        user_repo = current_app.config["USER_REPO"]
        logger.info(f"[WebUI] /info获取USER_REPO: {type(user_repo).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: USER_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        user = user_repo.get_by_id(user_id)
        
        if not user:
            return jsonify({"success": False, "message": "用户不存在"}), 404
        
        logger.info(f"[WebUI] 用户信息查询成功: {user_id}")
        
        return jsonify({
            "success": True,
            "data": {
                "user_id": user.user_id,
                "nickname": user.nickname,
                "coins": user.coins,
                "premium_currency": user.premium_currency,
                "total_fishing_count": user.total_fishing_count,
                "total_weight_caught": user.total_weight_caught,
                "consecutive_login_days": user.consecutive_login_days,
                "fish_pond_capacity": user.fish_pond_capacity,
                "fishing_zone_id": int(getattr(user, "fishing_zone_id", 1) or 1),
                "max_coins": user.max_coins,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            }
        })
    except Exception as e:
        logger.error(f"获取用户信息失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/home/status", methods=["GET"])
@api_login_required
async def get_home_status():
    """获取 Unity 主界面聚合状态。"""
    user_id = session.get("user_id")

    try:
        payload = _get_home_status_payload(user_id)
        if not payload:
            return jsonify({"success": False, "message": "用户不存在"}), 404

        return jsonify({
            "success": True,
            "data": payload,
        })
    except KeyError as e:
        logger.error(f"[WebUI] 主页状态配置缺失: {e}", exc_info=True)
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    except Exception as e:
        logger.error(f"获取主页状态失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/home/upgrade_info", methods=["GET"])
@api_login_required
async def get_home_upgrade_info():
    """获取主页升级面板所需的鱼塘/水族箱升级信息。"""
    user_id = session.get("user_id")

    try:
        payload = _get_home_upgrade_payload(user_id)
        if not payload:
            return jsonify({"success": False, "message": "用户不存在"}), 404

        return jsonify({
            "success": True,
            "data": payload,
        })
    except KeyError as e:
        logger.error(f"[WebUI] 升级面板配置缺失: {e}", exc_info=True)
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    except Exception as e:
        logger.error(f"获取主页升级信息失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/home/upgrade_fish_pond", methods=["POST"])
@api_login_required
async def home_upgrade_fish_pond():
    """主页升级鱼塘容量。"""
    user_id = session.get("user_id")

    try:
        inventory_service = current_app.config["INVENTORY_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_SERVICE未找到 - {e}", exc_info=True)
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = inventory_service.upgrade_fish_pond(user_id)
        payload = _get_home_upgrade_payload(user_id)
        status_code = 200 if result.get("success") else 400
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "升级失败"),
            "new_capacity": int(result.get("new_capacity", 0) or 0),
            "cost": int(result.get("cost", 0) or 0),
            "data": payload,
        }), status_code
    except Exception as e:
        logger.error(f"主页升级鱼塘失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"升级失败: {str(e)}"}), 500


@user_api_bp.route("/home/upgrade_aquarium", methods=["POST"])
@api_login_required
async def home_upgrade_aquarium():
    """主页升级水族箱容量。"""
    user_id = session.get("user_id")

    try:
        aquarium_service = current_app.config["AQUARIUM_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: AQUARIUM_SERVICE未找到 - {e}", exc_info=True)
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = aquarium_service.upgrade_aquarium(user_id)
        payload = _get_home_upgrade_payload(user_id)
        status_code = 200 if result.get("success") else 400
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "升级失败"),
            "data": payload,
        }), status_code
    except Exception as e:
        logger.error(f"主页升级水族箱失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"升级失败: {str(e)}"}), 500


@user_api_bp.route("/home/upgrade_futures", methods=["POST"])
@api_login_required
async def home_upgrade_futures():
    """主页升级期货容量。"""
    user_id = session.get("user_id")

    try:
        exchange_service = current_app.config["EXCHANGE_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: EXCHANGE_SERVICE未找到 - {e}", exc_info=True)
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = exchange_service.upgrade_exchange_capacity(user_id)
        payload = _get_home_upgrade_payload(user_id)
        status_code = 200 if result.get("success") else 400
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "升级失败"),
            "new_capacity": int(result.get("new_capacity", 0) or 0),
            "cost": int(result.get("cost", 0) or 0),
            "data": payload,
        }), status_code
    except Exception as e:
        logger.error(f"主页升级期货失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"升级失败: {str(e)}"}), 500


@user_api_bp.route("/home/toggle_auto_fishing", methods=["POST"])
@api_login_required
async def home_toggle_auto_fishing():
    """切换主页自动钓鱼状态。"""
    user_id = session.get("user_id")

    try:
        user_repo = current_app.config["USER_REPO"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: USER_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        user = user_repo.get_by_id(user_id)
        if not user:
            return jsonify({"success": False, "message": "用户不存在"}), 404

        new_state = not bool(getattr(user, "auto_fishing_enabled", False))
        user.auto_fishing_enabled = new_state
        user_repo.update(user)

        return jsonify({
            "success": True,
            "auto_fishing_enabled": new_state,
            "message": f"自动钓鱼已{'开启' if new_state else '关闭'}",
        })
    except Exception as e:
        logger.error(f"切换自动钓鱼失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"切换失败: {str(e)}"}), 500


@user_api_bp.route("/backpack", methods=["GET"])
@api_login_required
async def get_backpack():
    """获取完整背包信息（供 Unity / WebUI 使用）"""
    user_id = session.get("user_id")
    
    try:
        inventory_service = current_app.config["INVENTORY_SERVICE"]
        logger.info(f"[WebUI] /backpack获取INVENTORY_SERVICE: {type(inventory_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        user_repo = current_app.config["USER_REPO"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: USER_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        user = user_repo.get_by_id(user_id)
        if not user:
            return jsonify({"success": False, "message": "用户不存在"}), 404

        rods_result = inventory_service.get_user_rod_inventory(user_id)
        accessories_result = inventory_service.get_user_accessory_inventory(user_id)
        items_result = inventory_service.get_user_item_inventory(user_id)
        baits_result = inventory_service.get_user_bait_inventory(user_id)

        rods = rods_result.get("rods", []) if isinstance(rods_result, dict) else []
        accessories = accessories_result.get("accessories", []) if isinstance(accessories_result, dict) else []
        items = items_result.get("items", []) if isinstance(items_result, dict) else []
        baits = baits_result.get("baits", []) if isinstance(baits_result, dict) else []
        
        logger.info(f"[WebUI] 背包查询成功 - 竿:{len(rods)}, 饰品:{len(accessories)}, 道具:{len(items)}, 诱饵:{len(baits)}")
        
        return jsonify({
            "success": True,
            "data": {
                "rod_count": len(rods) if rods else 0,
                "accessory_count": len(accessories) if accessories else 0,
                "item_count": len(items) if items else 0,
                "bait_count": len(baits) if baits else 0,
                "rods": rods,
                "accessories": accessories,
                "items": items,
                "baits": baits,
                "equipped": {
                    "current_bait_id": getattr(user, "current_bait_id", None),
                }
            }
        })
    except Exception as e:
        logger.error(f"获取背包信息失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/inventory/sell", methods=["POST"])
@api_login_required
async def sell_inventory_item():
    """出售背包中的单个物品或装备。"""
    user_id = session.get("user_id")

    try:
        inventory_service = current_app.config["INVENTORY_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        payload = await _read_request_payload()
        item_type = str(payload.get("item_type", "")).strip().lower()
        quantity = int(payload.get("quantity", 1) or 1)

        if item_type in ("rod", "accessory"):
            instance_id = int(payload.get("instance_id", 0) or 0)
            if instance_id <= 0:
                return jsonify({"success": False, "message": "缺少实例ID"}), 400
            result = inventory_service.sell_equipment(user_id, instance_id, item_type)
            return jsonify(result), (200 if result.get("success") else 400)

        if item_type == "item":
            item_id = int(payload.get("item_id", 0) or 0)
            if item_id <= 0:
                return jsonify({"success": False, "message": "缺少道具ID"}), 400
            result = inventory_service.sell_item(user_id, item_id, quantity)
            return jsonify(result), (200 if result.get("success") else 400)

        return jsonify({"success": False, "message": "该类型暂不支持卖出"}), 400
    except Exception as e:
        logger.error(f"出售背包物品失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"出售失败: {str(e)}"}), 500


@user_api_bp.route("/fish", methods=["GET"])
@api_login_required
async def get_fish():
    """获取用户的鱼塘中的鱼"""
    user_id = session.get("user_id")
    
    try:
        inventory_repo = current_app.config["INVENTORY_REPO"]
        logger.info(f"[WebUI] /fish获取INVENTORY_REPO: {type(inventory_repo).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        fish = inventory_repo.get_fish_inventory(user_id)
        
        fish_list = []
        if fish:
            for f in fish:
                fish_list.append({
                    "fish_id": f.fish_id,
                    "quality_level": f.quality_level,
                    "quantity": f.quantity,
                })
        
        logger.info(f"[WebUI] 鱼列表查询成功: {len(fish_list)}条鱼")
        
        return jsonify({
            "success": True,
            "data": fish_list
        })
    except Exception as e:
        logger.error(f"获取鱼列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/fishpond", methods=["GET"])
@api_login_required
async def get_fishpond():
    """获取用户鱼塘富数据，供 Unity/UI 直接渲染。"""
    user_id = session.get("user_id")

    try:
        inventory_service = current_app.config["INVENTORY_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = inventory_service.get_user_fish_pond(user_id)
        if not result.get("success"):
            return jsonify({
                "success": False,
                "message": result.get("message", "获取鱼塘失败")
            }), 400

        return jsonify({
            "success": True,
            "fishes": result.get("fishes", []),
            "stats": result.get("stats", {}),
            "data": {
                "fishes": result.get("fishes", []),
                "stats": result.get("stats", {})
            }
        })
    except Exception as e:
        logger.error(f"获取鱼塘富数据失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/aquarium", methods=["GET"])
@api_login_required
async def get_aquarium():
    """获取用户水族箱富数据，供 Unity/UI 直接渲染。"""
    user_id = session.get("user_id")

    try:
        aquarium_service = current_app.config["AQUARIUM_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: AQUARIUM_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = aquarium_service.get_user_aquarium(user_id)
        if not result.get("success"):
            return jsonify({
                "success": False,
                "message": result.get("message", "获取水族箱失败")
            }), 400

        return jsonify({
            "success": True,
            "fishes": result.get("fishes", []),
            "stats": result.get("stats", {}),
            "data": {
                "fishes": result.get("fishes", []),
                "stats": result.get("stats", {})
            }
        })
    except Exception as e:
        logger.error(f"获取水族箱富数据失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/rods", methods=["GET"])
@api_login_required
async def get_rods():
    """获取用户的鱼竿"""
    user_id = session.get("user_id")
    
    try:
        inventory_repo = current_app.config["INVENTORY_REPO"]
        logger.info(f"[WebUI] /rods获取INVENTORY_REPO: {type(inventory_repo).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        rods = inventory_repo.get_user_rod_instances(user_id)
        
        rod_list = []
        if rods:
            for r in rods:
                rod_list.append({
                    "rod_id": r.rod_id,
                    "durability": r.durability if hasattr(r, 'durability') else 0,
                })
        
        logger.info(f"[WebUI] 鱼竿列表查询成功: {len(rod_list)}根鱼竿")
        
        return jsonify({
            "success": True,
            "data": rod_list
        })
    except Exception as e:
        logger.error(f"获取鱼竿列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/baits", methods=["GET"])
@api_login_required
async def get_baits():
    """获取用户的鱼饵"""
    user_id = session.get("user_id")
    
    try:
        inventory_repo = current_app.config["INVENTORY_REPO"]
        logger.info(f"[WebUI] /baits获取INVENTORY_REPO: {type(inventory_repo).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        baits = inventory_repo.get_user_bait_inventory(user_id)
        
        bait_list = []
        if baits:
            for b in baits:
                bait_list.append({
                    "bait_id": b.bait_id,
                    "quantity": b.quantity,
                })
        
        logger.info(f"[WebUI] 鱼饵列表查询成功: {len(bait_list)}种鱼饵")
        
        return jsonify({
            "success": True,
            "data": bait_list
        })
    except Exception as e:
        logger.error(f"获取鱼饵列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/accessories", methods=["GET"])
@api_login_required
async def get_accessories():
    """获取用户的饰品"""
    user_id = session.get("user_id")
    
    try:
        inventory_repo = current_app.config["INVENTORY_REPO"]
        logger.info(f"[WebUI] /accessories获取INVENTORY_REPO: {type(inventory_repo).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        accessories = inventory_repo.get_user_accessory_instances(user_id)
        
        acc_list = []
        if accessories:
            for a in accessories:
                acc_list.append({
                    "accessory_id": a.accessory_id,
                    "durability": a.durability if hasattr(a, 'durability') else 0,
                })
        
        logger.info(f"[WebUI] 饰品列表查询成功: {len(acc_list)}个饰品")
        
        return jsonify({
            "success": True,
            "data": acc_list
        })
    except Exception as e:
        logger.error(f"获取饰品列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/items", methods=["GET"])
@api_login_required
async def get_items():
    """获取用户的道具"""
    user_id = session.get("user_id")
    
    try:
        inventory_repo = current_app.config["INVENTORY_REPO"]
        logger.info(f"[WebUI] /items获取INVENTORY_REPO: {type(inventory_repo).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: INVENTORY_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        items = inventory_repo.get_user_item_inventory(user_id)
        
        item_list = []
        if items:
            for i in items:
                item_list.append({
                    "item_id": i.item_id,
                    "quantity": i.quantity,
                })
        
        logger.info(f"[WebUI] 道具列表查询成功: {len(item_list)}种道具")
        
        return jsonify({
            "success": True,
            "data": item_list
        })
    except Exception as e:
        logger.error(f"获取道具列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500



@user_api_bp.route("/fishing/do", methods=["POST"])
@api_login_required
async def do_fishing():
    """执行钓鱼操作"""
    user_id = session.get("user_id")
    
    try:
        user_repo = current_app.config["USER_REPO"]
        logger.info(f"[WebUI] /fishing/do获取USER_REPO: {type(user_repo).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: USER_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        fishing_service = current_app.config["FISHING_SERVICE"]
        logger.info(f"[WebUI] /fishing/do获取FISHING_SERVICE: {type(fishing_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: FISHING_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        user = user_repo.get_by_id(user_id)
        if not user:
            return jsonify({"success": False, "message": "用户不存在"}), 404
        
        # 执行钓鱼
        result = fishing_service.fish(user_id, None)  # None表示使用当前区域
        
        logger.info(f"[WebUI] 钓鱼执行成功: {user_id}")
        
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "钓鱼失败"),
            "data": result.get("data")
        })
    except Exception as e:
        logger.error(f"钓鱼失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"钓鱼失败: {str(e)}"}), 500


@user_api_bp.route("/sign-in", methods=["POST"])
@api_login_required
async def sign_in():
    """用户签到"""
    user_id = session.get("user_id")
    
    try:
        user_service = current_app.config["USER_SERVICE"]
        logger.info(f"[WebUI] /sign-in获取USER_SERVICE: {type(user_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: USER_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        result = user_service.daily_sign_in(user_id)
        has_checked_in_today = bool(current_app.config["LOG_REPO"].has_checked_in(user_id, get_today()))
        
        logger.info(f"[WebUI] 签到成功: {user_id}")
        
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "签到失败"),
            "has_checked_in_today": has_checked_in_today,
        })
    except Exception as e:
        logger.error(f"签到失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"签到失败: {str(e)}"}), 500


@user_api_bp.route("/home/sign_in", methods=["POST"])
@api_login_required
async def home_sign_in():
    """主页签到接口。"""
    user_id = session.get("user_id")

    try:
        user_service = current_app.config["USER_SERVICE"]
        log_repo = current_app.config["LOG_REPO"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: {e}", exc_info=True)
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = user_service.daily_sign_in(user_id)
        has_checked_in_today = bool(log_repo.has_checked_in(user_id, get_today()))

        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "签到失败"),
            "has_checked_in_today": has_checked_in_today,
        })
    except Exception as e:
        logger.error(f"主页签到失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"签到失败: {str(e)}"}), 500


@user_api_bp.route("/fishing/zones", methods=["GET"])
@api_login_required
async def get_fishing_zones():
    """获取当前用户可见的钓鱼区域列表"""
    user_id = session.get("user_id")

    try:
        fishing_service = current_app.config["FISHING_SERVICE"]
        logger.info(f"[WebUI] /fishing/zones获取FISHING_SERVICE: {type(fishing_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: FISHING_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = fishing_service.get_user_fishing_zones(user_id)

        if result.get("success"):
            logger.info(f"[WebUI] 钓鱼区域查询成功: {user_id}")
            return jsonify(result)

        logger.warning(f"[WebUI] 钓鱼区域查询失败: {user_id} - {result.get('message', '未知错误')}")
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"获取钓鱼区域失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/fishing/change_zone", methods=["POST"])
@api_login_required
async def change_fishing_zone():
    """切换当前用户的钓鱼区域"""
    user_id = session.get("user_id")

    try:
        fishing_service = current_app.config["FISHING_SERVICE"]
        logger.info(f"[WebUI] /fishing/change_zone获取FISHING_SERVICE: {type(fishing_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: FISHING_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        payload = await request.get_json(silent=True)
        zone_id_raw = None

        if isinstance(payload, dict):
            zone_id_raw = payload.get("zone_id")

        if zone_id_raw is None:
            form = await request.form
            zone_id_raw = form.get("zone_id")

        if zone_id_raw is None:
            zone_id_raw = request.args.get("zone_id")

        try:
            zone_id = int(zone_id_raw)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "缺少有效的 zone_id"}), 400

        result = fishing_service.set_user_fishing_zone(user_id, zone_id)

        if result.get("success"):
            logger.info(f"[WebUI] 切换钓鱼区域成功: {user_id} -> {zone_id}")
            return jsonify(result)

        logger.warning(f"[WebUI] 切换钓鱼区域失败: {user_id} -> {zone_id}, {result.get('message', '未知错误')}")
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"切换钓鱼区域失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"切换失败: {str(e)}"}), 500


@user_api_bp.route("/market/list", methods=["GET"])
@api_login_required
async def get_market_listings():
    """获取市场列表"""
    try:
        market_service = current_app.config["MARKET_SERVICE"]
        logger.info(f"[WebUI] /market/list获取MARKET_SERVICE: {type(market_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: MARKET_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        item_type = request.args.get("item_type")
        min_price = request.args.get("min_price")
        max_price = request.args.get("max_price")
        
        min_price = int(min_price) if min_price else None
        max_price = int(max_price) if max_price else None
        
        result = market_service.get_all_market_listings_for_admin(
            page=page,
            per_page=per_page,
            item_type=item_type,
            min_price=min_price,
            max_price=max_price
        )
        
        serialized = []
        listings = result.get("listings", [])
        if listings:
            for listing in listings:
                normalized = _serialize_market_listing(listing)
                if normalized is not None:
                    serialized.append(normalized)

        logger.info(f"[WebUI] 市场列表查询成功")
        
        return jsonify({
            "success": result.get("success", False),
            "data": serialized,
            "pagination": result.get("pagination", {})
        })
    except Exception as e:
        logger.error(f"获取市场列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/market/list/<int:listing_id>", methods=["POST"])
@api_login_required
async def purchase_listing(listing_id):
    """购买市场商品"""
    user_id = session.get("user_id")
    
    try:
        market_service = current_app.config["MARKET_SERVICE"]
        logger.info(f"[WebUI] /market/list/<id>获取MARKET_SERVICE: {type(market_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: MARKET_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        result = market_service.buy_market_item(user_id, listing_id)
        
        logger.info(f"[WebUI] 购买成功: {user_id} -> {listing_id}")
        
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "购买失败")
        })
    except Exception as e:
        logger.error(f"购买失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"购买失败: {str(e)}"}), 500


@user_api_bp.route("/market/list-item", methods=["POST"])
@api_login_required
async def list_market_item():
    """上架背包物品到市场"""
    user_id = session.get("user_id")

    try:
        market_service = current_app.config["MARKET_SERVICE"]
        inventory_service = current_app.config["INVENTORY_SERVICE"]
        logger.info(f"[WebUI] /market/list-item获取MARKET_SERVICE: {type(market_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: 市场上架所需服务未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        payload = await _read_request_payload()
        item_type = str(payload.get("item_type", "")).strip().lower()
        item_code = str(payload.get("item_code", "")).strip()
        quantity = int(payload.get("quantity", 1) or 1)
        price = int(payload.get("price", 1) or 1)
        quality_level = int(payload.get("quality_level", 0) or 0)
        item_instance_id = payload.get("item_instance_id")
        item_id = payload.get("item_id")

        if not item_type:
            return jsonify({"success": False, "message": "缺少物品类型"}), 400

        resolved_identifier = None
        if item_type == "rod":
            resolved_identifier = inventory_service.resolve_rod_instance_id(user_id, item_code) if item_code else None
        elif item_type == "accessory":
            resolved_identifier = inventory_service.resolve_accessory_instance_id(user_id, item_code) if item_code else None
        elif item_type == "item":
            resolved_identifier = int(item_id or item_instance_id or 0)
        else:
            return jsonify({"success": False, "message": "该类型暂不支持上架"}), 400

        if resolved_identifier is None or int(resolved_identifier) <= 0:
            return jsonify({"success": False, "message": "缺少有效的物品编码"}), 400

        result = market_service.put_item_on_sale(
            user_id=user_id,
            item_type=item_type,
            item_instance_id=int(resolved_identifier),
            price=price,
            quantity=quantity,
            quality_level=quality_level,
        )

        status_code = 200 if result.get("success", False) else 400
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "上架失败")
        }), status_code
    except Exception as e:
        logger.error(f"上架市场物品失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"上架失败: {str(e)}"}), 500


@user_api_bp.route("/market/delist", methods=["POST"])
@api_login_required
async def delist_market_item():
    """回收自己在市场上的挂单"""
    user_id = session.get("user_id")

    try:
        market_service = current_app.config["MARKET_SERVICE"]
        logger.info(f"[WebUI] /market/delist获取MARKET_SERVICE: {type(market_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: MARKET_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        payload = await _read_request_payload()
        market_id = int(payload.get("market_id", 0) or 0)
        if market_id <= 0:
            return jsonify({"success": False, "message": "缺少有效的挂单ID"}), 400

        result = market_service.delist_item(user_id, market_id)
        status_code = 200 if result.get("success", False) else 400
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "回收失败")
        }), status_code
    except Exception as e:
        logger.error(f"回收市场挂单失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"回收失败: {str(e)}"}), 500


@user_api_bp.route("/exchange/status", methods=["GET"])
@api_login_required
async def get_exchange_status():
    """获取交易行当前状态与期货列表"""
    try:
        exchange_service = current_app.config["EXCHANGE_SERVICE"]
        logger.info(f"[WebUI] /exchange/status获取EXCHANGE_SERVICE: {type(exchange_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: EXCHANGE_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = exchange_service.get_market_status()
        if not result.get("success", False):
            return jsonify({
                "success": False,
                "message": result.get("message", "获取交易行状态失败")
            }), 400

        tax_rate = exchange_service.config.get("exchange", {}).get("tax_rate", 0.05)
        return jsonify({
            "success": True,
            "data": _serialize_exchange_status(result, tax_rate)
        })
    except Exception as e:
        logger.error(f"获取交易行状态失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/exchange/history", methods=["GET"])
@api_login_required
async def get_exchange_history():
    """获取交易行价格历史"""
    try:
        exchange_service = current_app.config["EXCHANGE_SERVICE"]
        logger.info(f"[WebUI] /exchange/history获取EXCHANGE_SERVICE: {type(exchange_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: EXCHANGE_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        days = int(request.args.get("days", 7) or 7)
        days = 7 if days <= 0 else min(days, 30)
        result = exchange_service.get_price_history(days=days)
        if not result.get("success", False):
            return jsonify({
                "success": False,
                "message": result.get("message", "获取交易行历史失败")
            }), 400

        return jsonify({
            "success": True,
            "data": _serialize_exchange_history(result)
        })
    except Exception as e:
        logger.error(f"获取交易行历史失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/exchange/inventory", methods=["GET"])
@api_login_required
async def get_exchange_inventory():
    """获取当前用户的交易行持仓"""
    user_id = session.get("user_id")

    try:
        exchange_service = current_app.config["EXCHANGE_SERVICE"]
        logger.info(f"[WebUI] /exchange/inventory获取EXCHANGE_SERVICE: {type(exchange_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: EXCHANGE_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = exchange_service.get_user_inventory(user_id)
        if not result.get("success", False):
            return jsonify({
                "success": False,
                "message": result.get("message", "获取持仓失败")
            }), 400

        return jsonify({
            "success": True,
            "data": _serialize_exchange_inventory(result)
        })
    except Exception as e:
        logger.error(f"获取交易行持仓失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/exchange/buy", methods=["POST"])
@api_login_required
async def buy_exchange_commodity():
    """购买交易行商品"""
    user_id = session.get("user_id")

    try:
        exchange_service = current_app.config["EXCHANGE_SERVICE"]
        logger.info(f"[WebUI] /exchange/buy获取EXCHANGE_SERVICE: {type(exchange_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: EXCHANGE_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        payload = await _read_request_payload()
        commodity_id = str(payload.get("commodity_id", "")).strip()
        quantity = int(payload.get("quantity", 0) or 0)

        if not commodity_id:
            return jsonify({"success": False, "message": "缺少商品ID"}), 400

        if quantity <= 0:
            return jsonify({"success": False, "message": "购买数量必须大于0"}), 400

        market_status = exchange_service.get_market_status()
        prices = market_status.get("prices", {}) if isinstance(market_status, dict) else {}
        current_price = int(prices.get(commodity_id, 0) or 0)
        if current_price <= 0:
            return jsonify({"success": False, "message": "当前价格不可用"}), 400

        result = exchange_service.purchase_commodity(user_id, commodity_id, quantity, current_price)
        status_code = 200 if result.get("success", False) else 400
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "购买失败"),
            "total_cost": result.get("total_cost", 0),
            "current_price": result.get("current_price", current_price),
        }), status_code
    except Exception as e:
        logger.error(f"购买交易行商品失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"购买失败: {str(e)}"}), 500


@user_api_bp.route("/exchange/sell", methods=["POST"])
@api_login_required
async def sell_exchange_commodity():
    """卖出交易行商品"""
    user_id = session.get("user_id")

    try:
        exchange_service = current_app.config["EXCHANGE_SERVICE"]
        logger.info(f"[WebUI] /exchange/sell获取EXCHANGE_SERVICE: {type(exchange_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: EXCHANGE_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        payload = await _read_request_payload()
        commodity_id = str(payload.get("commodity_id", "")).strip()
        quantity = int(payload.get("quantity", 0) or 0)

        if not commodity_id:
            return jsonify({"success": False, "message": "缺少商品ID"}), 400

        if quantity <= 0:
            return jsonify({"success": False, "message": "卖出数量必须大于0"}), 400

        market_status = exchange_service.get_market_status()
        prices = market_status.get("prices", {}) if isinstance(market_status, dict) else {}
        current_price = int(prices.get(commodity_id, 0) or 0)
        if current_price <= 0:
            return jsonify({"success": False, "message": "当前价格不可用"}), 400

        result = exchange_service.sell_commodity(user_id, commodity_id, quantity, current_price)
        status_code = 200 if result.get("success", False) else 400
        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "卖出失败"),
            "total_income": result.get("total_income", 0),
            "net_income": result.get("net_income", 0),
            "tax_amount": result.get("tax_amount", 0),
            "current_price": result.get("current_price", current_price),
            "profit_loss": result.get("profit_loss", {}),
            "expired_sold": result.get("expired_sold", 0),
            "valid_sold": result.get("valid_sold", 0),
        }), status_code
    except Exception as e:
        logger.error(f"卖出交易行商品失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"卖出失败: {str(e)}"}), 500


@user_api_bp.route("/shop/list", methods=["GET"])
@api_login_required
async def get_shops():
    """获取商店列表"""
    try:
        shop_service = current_app.config["SHOP_SERVICE"]
        logger.info(f"[WebUI] /shop/list获取SHOP_SERVICE: {type(shop_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: SHOP_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        result = shop_service.get_shops()
        shops = result.get("shops", []) if isinstance(result, dict) else []
        
        shop_list = []
        if shops:
            for shop in shops:
                shop_list.append({
                    "id": shop.get("shop_id", shop.get("id", 0)),
                    "name": shop.get("name", ""),
                    "description": shop.get("description", ""),
                    "shop_type": shop.get("shop_type", ""),
                })
        
        logger.info(f"[WebUI] 商店列表查询成功: {len(shop_list)}个商店")
        
        return jsonify({
            "success": True,
            "data": shop_list
        })
    except Exception as e:
        logger.error(f"获取商店列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/shop/<int:shop_id>", methods=["GET"])
@api_login_required
async def get_shop_details(shop_id):
    """获取单个商店详情和商品列表"""
    try:
        shop_service = current_app.config["SHOP_SERVICE"]
        logger.info(f"[WebUI] /shop/<shop_id>获取SHOP_SERVICE: {type(shop_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: SHOP_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        if shop_id <= 0:
            return jsonify({"success": False, "message": "缺少有效的商店ID"}), 400

        result = shop_service.get_shop_details(shop_id)
        if not result.get("success", False):
            return jsonify({
                "success": False,
                "message": result.get("message", "获取商店详情失败")
            }), 400

        logger.info(f"[WebUI] 商店详情查询成功: {shop_id}")

        return jsonify({
            "success": True,
            "message": result.get("message", ""),
            "data": {
                "shop": result.get("shop"),
                "items": result.get("items", [])
            }
        })
    except Exception as e:
        logger.error(f"获取商店详情失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/shop/buy", methods=["POST"])
@api_login_required
async def buy_shop_item():
    """购买商店商品"""
    user_id = session.get("user_id")

    try:
        shop_service = current_app.config["SHOP_SERVICE"]
        logger.info(f"[WebUI] /shop/buy获取SHOP_SERVICE: {type(shop_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: SHOP_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        payload = await _read_request_payload()
        item_id = int(payload.get("item_id", 0) or 0)
        quantity = int(payload.get("quantity", 1) or 1)

        if item_id <= 0:
            return jsonify({"success": False, "message": "缺少有效的商品ID"}), 400

        if quantity <= 0:
            return jsonify({"success": False, "message": "购买数量必须大于0"}), 400

        result = shop_service.purchase_item(user_id, item_id, quantity)
        status_code = 200 if result.get("success", False) else 400

        logger.info(f"[WebUI] 商店购买完成: user={user_id}, item={item_id}, quantity={quantity}, success={result.get('success', False)}")

        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "购买失败")
        }), status_code
    except Exception as e:
        logger.error(f"购买商店商品失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"购买失败: {str(e)}"}), 500


@user_api_bp.route("/gacha/pools", methods=["GET"])
@api_login_required
async def get_gacha_pools():
    """获取抽卡卡池列表"""
    try:
        gacha_service = current_app.config["GACHA_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: GACHA_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = gacha_service.get_all_pools()
        if not result.get("success", False):
            return jsonify({"success": False, "message": result.get("message", "获取卡池失败")}), 400

        pools = result.get("pools", []) or []
        return jsonify({
            "success": True,
            "data": [_serialize_gacha_pool(pool) for pool in pools if pool is not None]
        })
    except Exception as e:
        logger.error(f"获取抽卡卡池列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取卡池失败: {str(e)}"}), 500


@user_api_bp.route("/gacha/pools/<int:pool_id>", methods=["GET"])
@api_login_required
async def get_gacha_pool_details(pool_id: int):
    """获取单个卡池的概率详情"""
    try:
        gacha_service = current_app.config["GACHA_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: GACHA_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = gacha_service.get_pool_details(pool_id)
        if not result.get("success", False):
            return jsonify({"success": False, "message": result.get("message", "获取卡池详情失败")}), 404

        pool = _serialize_gacha_pool(result.get("pool"))
        probabilities = result.get("probabilities", []) or []
        return jsonify({
            "success": True,
            "data": {
                "pool": pool,
                "items": [
                    serialized for serialized in
                    (_serialize_gacha_probability_item(item) for item in probabilities)
                    if serialized is not None
                ]
            }
        })
    except Exception as e:
        logger.error(f"获取抽卡卡池详情失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取卡池详情失败: {str(e)}"}), 500


@user_api_bp.route("/gacha/do", methods=["POST"])
@api_login_required
async def do_gacha():
    """执行抽卡"""
    user_id = session.get("user_id")
    
    try:
        gacha_service = current_app.config["GACHA_SERVICE"]
        logger.info(f"[WebUI] /gacha/do获取GACHA_SERVICE: {type(gacha_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: GACHA_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        payload = await _read_request_payload()
        pool_id = int(payload.get("pool_id", 0) or 0)
        num_draws = int(payload.get("num_draws", 1) or 1)

        if pool_id <= 0:
            return jsonify({"success": False, "message": "缺少有效的卡池ID"}), 400

        if num_draws <= 0:
            return jsonify({"success": False, "message": "抽卡次数必须大于0"}), 400

        result = gacha_service.perform_draw(user_id, pool_id, num_draws)
        status_code = 200 if result.get("success", False) else 400

        logger.info(f"[WebUI] 抽卡完成: user={user_id}, pool={pool_id}, draws={num_draws}, success={result.get('success', False)}")

        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "抽卡失败"),
            "data": {
                "results": [
                    serialized for serialized in
                    (_serialize_gacha_reward_item(item) for item in (result.get("results", []) or []))
                    if serialized is not None
                ]
            }
        }), status_code
    except Exception as e:
        logger.error(f"抽卡失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"抽卡失败: {str(e)}"}), 500


@user_api_bp.route("/leaderboard", methods=["GET"])
@api_login_required
async def get_leaderboard():
    """获取排行榜"""
    try:
        user_service = current_app.config["USER_SERVICE"]
        logger.info(f"[WebUI] /leaderboard获取USER_SERVICE: {type(user_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: USER_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        sort_by = request.args.get("sort_by", "coins")
        limit = int(request.args.get("limit", 10))
        
        result = user_service.get_leaderboard_data(sort_by=sort_by, limit=limit)
        
        logger.info(f"[WebUI] 排行榜查询成功: {sort_by}")
        
        return jsonify({
            "success": result.get("success", False),
            "data": result.get("leaderboard", [])
        })
    except Exception as e:
        logger.error(f"获取排行榜失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/tavern/announcement", methods=["GET"])
@api_login_required
async def get_tavern_announcement():
    """获取酒馆公告内容"""
    try:
        tavern_data = _load_tavern_data()
        return jsonify({
            "success": True,
            "data": {
                "content": tavern_data.get("announcement", "") or ""
            }
        })
    except Exception as e:
        logger.error(f"获取酒馆公告失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/tavern/leaderboards", methods=["GET"])
@api_login_required
async def get_tavern_leaderboards():
    """获取 Unity 酒馆公告板专用排行榜"""
    try:
        user_repo = current_app.config["USER_REPO"]
        inventory_repo = current_app.config["INVENTORY_REPO"]
        item_template_provider = current_app.config.get("ITEM_TEMPLATE_SERVICE") or current_app.config.get("ITEM_TEMPLATE_REPO")
        if item_template_provider is None:
            raise KeyError("ITEM_TEMPLATE_SERVICE/ITEM_TEMPLATE_REPO")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: 酒馆排行榜依赖缺失 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        limit = int(request.args.get("limit", 10))
        limit = 10 if limit <= 0 else min(limit, 50)

        users = _get_all_users_for_tavern(user_repo)
        data = _build_tavern_rankings(users, inventory_repo, item_template_provider, limit)

        return jsonify({
            "success": True,
            "data": data
        })
    except Exception as e:
        logger.error(f"获取酒馆排行榜失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/tavern/wipe-bomb", methods=["GET"])
@api_login_required
async def get_tavern_wipe_bomb_status():
    """获取不要赌博界面的每日擦弹状态与公示"""
    user_id = session.get("user_id")

    try:
        user_repo = current_app.config["USER_REPO"]
        user = user_repo.get_by_id(user_id)
        if user is None:
            return jsonify({"success": False, "message": "用户不存在"}), 404

        reset_hour = _get_daily_reset_hour()
        publicity_data = _load_wipe_bomb_publicity(reset_hour)
        remaining_today = _get_wipe_bomb_remaining_attempts(user_id)

        return jsonify({
            "success": True,
            "data": {
                "remaining_today": remaining_today,
                "result_text": "",
                "king_notice": _serialize_wipe_bomb_notice(publicity_data.get("king")),
                "ghost_notice": _serialize_wipe_bomb_notice(publicity_data.get("ghost")),
            }
        })
    except Exception as e:
        logger.error(f"获取每日擦弹状态失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/tavern/wipe-bomb/do", methods=["POST"])
@api_login_required
async def do_tavern_wipe_bomb():
    """执行每日擦弹，并返回结果文本与更新后的公示"""
    user_id = session.get("user_id")

    try:
        game_mechanics_service = _get_game_mechanics_service_for_wipe_bomb()
        user_repo = current_app.config["USER_REPO"]
        item_template_provider = current_app.config.get("ITEM_TEMPLATE_SERVICE") or current_app.config.get("ITEM_TEMPLATE_REPO")
        if item_template_provider is None:
            raise KeyError("ITEM_TEMPLATE_SERVICE/ITEM_TEMPLATE_REPO")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: 每日擦弹依赖缺失 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        payload = await _read_request_payload()
        user = user_repo.get_by_id(user_id)
        if user is None:
            return jsonify({"success": False, "message": "用户不存在"}), 404

        all_in = bool(payload.get("all_in", False))
        if isinstance(payload.get("all_in"), str):
            all_in = str(payload.get("all_in", "")).strip().lower() in ("1", "true", "yes", "on")

        if all_in:
            amount = int(getattr(user, "coins", 0) or 0)
        else:
            try:
                amount = int(payload.get("amount", 0) or 0)
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "擦弹数量必须是数字"}), 400

        result = game_mechanics_service.perform_wipe_bomb(user_id, amount)
        reset_hour = _get_daily_reset_hour()
        publicity_data = _load_wipe_bomb_publicity(reset_hour)

        status_code = 200 if result.get("success", False) else 400
        if result.get("success", False):
            user = user_repo.get_by_id(user_id)
            title_name = _get_current_title_name(item_template_provider, user)
            publicity_data = _update_wipe_bomb_publicity(
                publicity_data,
                user,
                title_name,
                float(result.get("multiplier", 0.0) or 0.0),
                int(result.get("profit", 0) or 0)
            )
            _save_wipe_bomb_publicity(publicity_data)

        response_data = {
            "remaining_today": int(result.get("remaining_today", _get_wipe_bomb_remaining_attempts(user_id)) or 0),
            "result_text": _format_wipe_bomb_result_text(result),
            "king_notice": _serialize_wipe_bomb_notice(publicity_data.get("king")),
            "ghost_notice": _serialize_wipe_bomb_notice(publicity_data.get("ghost")),
        }

        return jsonify({
            "success": result.get("success", False),
            "message": result.get("message", "擦弹失败" if not result.get("success", False) else "擦弹成功"),
            "data": response_data
        }), status_code
    except Exception as e:
        logger.error(f"执行每日擦弹失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"擦弹失败: {str(e)}"}), 500


@user_api_bp.route("/tavern/expeditions", methods=["GET"])
@api_login_required
async def get_tavern_expeditions():
    """获取科学考察界面的进行中科考卡片数据"""
    try:
        expedition_service = current_app.config["EXPEDITION_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: 科考依赖缺失 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        expeditions = expedition_service.get_all_active_expeditions() or []
        serialized = [
            item for item in
            (_serialize_tavern_expedition(expedition) for expedition in expeditions)
            if item is not None
        ]

        return jsonify({
            "success": True,
            "data": {
                "expeditions": serialized
            }
        })
    except Exception as e:
        logger.error(f"获取科学考察列表失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/profile/update", methods=["POST"])
@api_login_required
async def update_profile():
    """更新用户信息"""
    user_id = session.get("user_id")
    
    try:
        user_repo = current_app.config["USER_REPO"]
        logger.info(f"[WebUI] /profile/update获取USER_REPO: {type(user_repo).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: USER_REPO未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        form = await request.json
        nickname = form.get("nickname", "").strip()
        
        if not nickname or len(nickname) < 2 or len(nickname) > 20:
            return jsonify({"success": False, "message": "昵称长度必须为2-20个字符"})
        
        user = user_repo.get_by_id(user_id)
        if user:
            user.nickname = nickname
            user_repo.update(user)
            
            logger.info(f"[WebUI] 用户信息更新成功: {user_id}")
            
            return jsonify({"success": True, "message": "昵称更新成功"})
        
        return jsonify({"success": False, "message": "用户不存在"})
    except Exception as e:
        logger.error(f"更新用户信息失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"更新失败: {str(e)}"}), 500


@user_api_bp.route("/fish-templates", methods=["GET"])
@api_login_required
async def get_fish_templates():
    """获取所有鱼类模板（用于图鉴）"""
    try:
        item_template_service = current_app.config["ITEM_TEMPLATE_SERVICE"]
        logger.info(f"[WebUI] /fish-templates获取ITEM_TEMPLATE_SERVICE: {type(item_template_service).__name__}")
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: ITEM_TEMPLATE_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500
    
    try:
        fish_templates = item_template_service.get_all_fish_templates()
        
        fish_list = []
        if fish_templates:
            for fish in fish_templates:
                fish_list.append({
                    "id": fish.id,
                    "name": fish.name,
                    "description": fish.description or "",
                    "quality_level": fish.quality_level or 1,
                    "weight": fish.weight or 0,
                    "drop_rate": fish.drop_rate or 0,
                    "zones": fish.zones or "",
                })
        
        logger.info(f"[WebUI] 鱼类模板查询成功: {len(fish_list)}条")
        
        return jsonify({
            "success": True,
            "data": fish_list
        })
    except Exception as e:
        logger.error(f"获取鱼类模板失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500


@user_api_bp.route("/pokedex", methods=["GET"])
@api_login_required
async def get_pokedex():
    """获取用户图鉴数据"""
    user_id = session.get("user_id")

    try:
        fishing_service = current_app.config["FISHING_SERVICE"]
    except KeyError as e:
        logger.error(f"[WebUI] 配置错误: FISHING_SERVICE未找到 - {e}")
        return jsonify({"success": False, "message": "系统配置错误"}), 500

    try:
        result = fishing_service.get_user_pokedex(user_id)

        if result.get("success"):
            logger.info(f"[WebUI] 图鉴查询成功: {user_id}, 解锁 {result.get('unlocked_fish_count', 0)}/{result.get('total_fish_count', 0)}")
            return jsonify(result)

        logger.warning(f"[WebUI] 图鉴查询失败: {user_id} - {result.get('message', '未知错误')}")
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"获取图鉴失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"}), 500
