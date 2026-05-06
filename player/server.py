import functools
import asyncio
import os
from typing import Dict, Any
from datetime import datetime, timedelta, timezone
import json
import secrets
from urllib.parse import urlencode

import requests

from quart import (
    Quart, render_template, request, redirect, url_for, session, flash,
    Blueprint, current_app, jsonify
)
from astrbot.api import logger
from ..manager.user_api import user_api_bp
from ..manager.unity_api import (
    install_unity_cors,
    normalize_public_base_url,
    register_unity_user_api_routes,
    unity_api_bp,
)


player_bp = Blueprint(
    "player_bp",
    __name__,
    template_folder="templates",
    static_folder="static",
)

LINUXDO_AUTHORIZE_URL = "https://connect.linux.do/oauth2/authorize"
LINUXDO_TOKEN_URL = "https://connect.linux.do/oauth2/token"
LINUXDO_USER_API_URL = "https://connect.linux.do/api/user"
UNITY_OAUTH_PENDING_TTL = timedelta(minutes=10)
UNITY_OAUTH_TICKET_TTL = timedelta(minutes=3)
UNITY_LINUXDO_OAUTH_PENDING = {}
DEFAULT_GITHUB_URL = "https://github.com/TheEyeoftheUniverse/astrbot_plugin_fishing"
DEFAULT_APK_DOWNLOAD_URL = f"{DEFAULT_GITHUB_URL}/releases"
DEFAULT_TAVERN_ADMIN_USER_ID = "2645956495"

# 用户凭证持久化辅助函数
def _get_credentials_file():
    """获取凭证文件路径"""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "user_credentials.json")

def _load_credentials():
    """从文件加载用户凭证"""
    credentials_file = _get_credentials_file()
    if os.path.exists(credentials_file):
        try:
            with open(credentials_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载用户凭证失败: {e}")
            return {}
    return {}

def _save_credentials(credentials):
    """保存用户凭证到文件"""
    credentials_file = _get_credentials_file()
    try:
        with open(credentials_file, "w", encoding="utf-8") as f:
            json.dump(credentials, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存用户凭证失败: {e}")

# 在启动时加载凭证
USER_CREDENTIALS = _load_credentials()

INITIAL_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _generate_initial_password() -> str:
    return "".join(secrets.choice(INITIAL_PASSWORD_ALPHABET) for _ in range(5))


def _get_credential_entry(user_id: str) -> Dict[str, str]:
    user_id = str(user_id or "").strip()
    raw_entry = USER_CREDENTIALS.get(user_id)
    changed = False

    if isinstance(raw_entry, dict):
        entry = {
            "password": str(raw_entry.get("password", "") or ""),
            "initial_password": str(raw_entry.get("initial_password", "") or ""),
        }
    elif raw_entry:
        entry = {"password": str(raw_entry), "initial_password": ""}
        changed = True
    else:
        entry = {"password": "", "initial_password": ""}
        changed = True

    if not entry["initial_password"]:
        entry["initial_password"] = _generate_initial_password()
        changed = True

    if changed:
        USER_CREDENTIALS[user_id] = entry
        _save_credentials(USER_CREDENTIALS)

    return entry


def ensure_initial_password(user_id: str) -> str:
    """为玩家懒生成并返回 5 位初始密码。"""
    if not str(user_id or "").strip():
        return ""
    return _get_credential_entry(user_id).get("initial_password", "")


def _normalize_external_url(value: Any, default: str = "") -> str:
    url = str(value or "").strip()
    return url or default


def _get_player_link_config() -> Dict[str, str]:
    return {
        "github_url": _normalize_external_url(current_app.config.get("PLAYER_GITHUB_URL"), DEFAULT_GITHUB_URL),
        "apk_download_url": _normalize_external_url(current_app.config.get("PLAYER_APK_DOWNLOAD_URL"), DEFAULT_APK_DOWNLOAD_URL),
    }


def _get_tavern_admin_user_id() -> str:
    configured = str(current_app.config.get("TAVERN_ADMIN_USER_ID", DEFAULT_TAVERN_ADMIN_USER_ID) or "").strip()
    return configured or DEFAULT_TAVERN_ADMIN_USER_ID


def verify_user_password(user_id: str, password: str) -> bool:
    password = str(password or "")
    if not password:
        return False
    entry = _get_credential_entry(user_id)
    return password in {
        entry.get("password", ""),
        entry.get("initial_password", ""),
    }


def set_user_password(user_id: str, new_password: str) -> None:
    entry = _get_credential_entry(user_id)
    entry["password"] = str(new_password or "")
    USER_CREDENTIALS[str(user_id)] = entry
    _save_credentials(USER_CREDENTIALS)


def reset_user_password_to_new_initial(user_id: str) -> str:
    """重置玩家登录密钥为新的初始密码，并废弃旧自定义密码。"""
    entry = _get_credential_entry(user_id)
    new_initial_password = _generate_initial_password()
    entry["password"] = ""
    entry["initial_password"] = new_initial_password
    USER_CREDENTIALS[str(user_id)] = entry
    _save_credentials(USER_CREDENTIALS)
    return new_initial_password


def _get_next_pond_upgrade(inventory_service, current_capacity: int) -> Dict[str, Any] | None:
    """根据当前鱼塘容量查找下一档升级信息。"""
    pond_upgrades = getattr(inventory_service, "config", {}).get("pond_upgrades", [])
    for upgrade in pond_upgrades:
        if int(upgrade.get("from", 0) or 0) == current_capacity:
            return {
                "from": int(upgrade.get("from", current_capacity) or current_capacity),
                "to": int(upgrade.get("to", current_capacity) or current_capacity),
                "cost": int(upgrade.get("cost", 0) or 0),
            }
    return None


async def _read_request_payload() -> Dict[str, Any]:
    """兼容 JSON 与表单提交，避免空请求体触发 500。"""
    payload = await request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload

    form = await request.form
    return dict(form)


def _get_linuxdo_links_file():
    """获取 Linux.do OAuth 绑定文件路径"""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "linuxdo_oauth_links.json")


def _load_linuxdo_links():
    """从文件加载 Linux.do OAuth 绑定关系"""
    links_file = _get_linuxdo_links_file()
    if os.path.exists(links_file):
        try:
            with open(links_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.error(f"加载 Linux.do OAuth 绑定失败: {e}")
    return {}


def _save_linuxdo_links(links):
    """保存 Linux.do OAuth 绑定关系"""
    links_file = _get_linuxdo_links_file()
    try:
        with open(links_file, "w", encoding="utf-8") as f:
            json.dump(links, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存 Linux.do OAuth 绑定失败: {e}")


LINUXDO_OAUTH_LINKS = _load_linuxdo_links()


def _normalize_linuxdo_oauth_config(config: Dict[str, Any] | None) -> Dict[str, Any]:
    config = config or {}
    enabled = bool(config.get("enabled", False))
    client_id = str(config.get("client_id", "") or "").strip()
    client_secret = str(config.get("client_secret", "") or "").strip()
    redirect_uri = str(config.get("redirect_uri", "") or "").strip()
    normalized = {
        "enabled": enabled,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scope": str(config.get("scope", "read") or "read").strip(),
        "user_id_field": str(config.get("user_id_field", "id") or "id").strip(),
        "nickname_field": str(config.get("nickname_field", "username") or "username").strip(),
        "auto_register": bool(config.get("auto_register", True)),
        "allow_password_fallback": bool(config.get("allow_password_fallback", True)),
        "authorize_url": str(config.get("authorize_url", LINUXDO_AUTHORIZE_URL) or LINUXDO_AUTHORIZE_URL).strip(),
        "token_url": str(config.get("token_url", LINUXDO_TOKEN_URL) or LINUXDO_TOKEN_URL).strip(),
        "user_api_url": str(config.get("user_api_url", LINUXDO_USER_API_URL) or LINUXDO_USER_API_URL).strip(),
        "proxy_url": str(config.get("proxy_url", "") or "").strip(),
    }
    normalized["configured"] = bool(
        enabled and client_id and client_secret and redirect_uri
    )
    normalized["login_entry_enabled"] = normalized["configured"]
    return normalized


def _get_linuxdo_oauth_config() -> Dict[str, Any]:
    raw_config = current_app.config.get("LINUXDO_OAUTH_CONFIG", {})
    return _normalize_linuxdo_oauth_config(raw_config)


def _normalize_profile_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_linuxdo_profile_field(profile: Dict[str, Any], field_name: str) -> str:
    if not isinstance(profile, dict):
        return ""
    return _normalize_profile_value(profile.get(field_name))


def _get_linuxdo_provider_key(profile: Dict[str, Any]) -> str:
    linuxdo_id = _extract_linuxdo_profile_field(profile, "id")
    if linuxdo_id:
        return f"id:{linuxdo_id}"

    username = _extract_linuxdo_profile_field(profile, "username").lower()
    if username:
        return f"username:{username}"

    return ""


def _get_linuxdo_candidate_user_id(profile: Dict[str, Any], oauth_config: Dict[str, Any]) -> str:
    field_name = oauth_config.get("user_id_field", "id")
    return _extract_linuxdo_profile_field(profile, field_name)


def _get_linuxdo_display_name(profile: Dict[str, Any], oauth_config: Dict[str, Any]) -> str:
    preferred_fields = [
        oauth_config.get("nickname_field", "username"),
        "name",
        "username",
        "id",
    ]

    for field_name in preferred_fields:
        value = _extract_linuxdo_profile_field(profile, field_name)
        if value:
            return value

    return "Linux.do玩家"


def _get_linked_game_user_id(provider_key: str) -> str:
    link = LINUXDO_OAUTH_LINKS.get(provider_key)
    if isinstance(link, dict):
        return _normalize_profile_value(link.get("game_user_id"))
    return _normalize_profile_value(link)


def _bind_linuxdo_account(profile: Dict[str, Any], game_user_id: str):
    provider_key = _get_linuxdo_provider_key(profile)
    if not provider_key or not game_user_id:
        return

    LINUXDO_OAUTH_LINKS[provider_key] = {
        "game_user_id": game_user_id,
        "linuxdo_id": _extract_linuxdo_profile_field(profile, "id"),
        "linuxdo_username": _extract_linuxdo_profile_field(profile, "username"),
        "linked_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    _save_linuxdo_links(LINUXDO_OAUTH_LINKS)


def _login_player_session(user, auth_provider: str = "password"):
    session["user_id"] = user.user_id
    session["nickname"] = user.nickname or user.user_id
    session["auth_provider"] = auth_provider


def _normalize_linuxdo_login_flow(value: Any) -> str:
    flow = str(value or "").strip().lower()
    if flow == "game":
        return "game"
    return "webui"


def _sanitize_post_login_path(value: Any) -> str:
    target = str(value or "").strip()
    if not target:
        return ""
    if not target.startswith("/") or target.startswith("//"):
        return ""
    return target


def _sanitize_unity_device_code(value: Any) -> str:
    device_code = str(value or "").strip()
    if not device_code or len(device_code) > 128:
        return ""
    allowed_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not all(char in allowed_chars for char in device_code):
        return ""
    return device_code


def _resolve_linuxdo_login_flow_from_request() -> str:
    if "flow" in request.args:
        return _normalize_linuxdo_login_flow(request.args.get("flow"))
    return "webui"


def _resolve_linuxdo_redirect_uri(oauth_config: Dict[str, Any]) -> str:
    return str(oauth_config.get("redirect_uri", "") or "").strip()


def _cleanup_pending_unity_linuxdo_oauth():
    now = datetime.utcnow()
    expired_codes = [
        device_code
        for device_code, payload in UNITY_LINUXDO_OAUTH_PENDING.items()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("expires_at"), datetime)
            or payload.get("expires_at") <= now
        )
    ]
    for device_code in expired_codes:
        UNITY_LINUXDO_OAUTH_PENDING.pop(device_code, None)


def _register_pending_unity_linuxdo_oauth(device_code: str):
    sanitized_device_code = _sanitize_unity_device_code(device_code)
    if not sanitized_device_code:
        return ""

    _cleanup_pending_unity_linuxdo_oauth()
    UNITY_LINUXDO_OAUTH_PENDING[sanitized_device_code] = {
        "status": "pending",
        "message": "等待浏览器完成 Linux.do 授权",
        "ticket": "",
        "user_id": "",
        "nickname": "",
        "expires_at": datetime.utcnow() + UNITY_OAUTH_PENDING_TTL,
    }
    return sanitized_device_code


def _mark_unity_linuxdo_oauth_error(device_code: str, message: str):
    sanitized_device_code = _sanitize_unity_device_code(device_code)
    if not sanitized_device_code:
        return

    _cleanup_pending_unity_linuxdo_oauth()
    payload = UNITY_LINUXDO_OAUTH_PENDING.get(sanitized_device_code)
    if not payload:
        return

    payload["status"] = "error"
    payload["message"] = str(message or "Linux.do 登录失败").strip()
    payload["ticket"] = ""
    payload["expires_at"] = datetime.utcnow() + UNITY_OAUTH_TICKET_TTL


def _complete_pending_unity_linuxdo_oauth(device_code: str, user) -> str:
    sanitized_device_code = _sanitize_unity_device_code(device_code)
    if not sanitized_device_code:
        return ""

    _cleanup_pending_unity_linuxdo_oauth()
    payload = UNITY_LINUXDO_OAUTH_PENDING.get(sanitized_device_code)
    if not payload:
        return ""

    ticket = secrets.token_urlsafe(24)
    payload["status"] = "authorized"
    payload["message"] = "授权成功，正在同步到游戏"
    payload["ticket"] = ticket
    payload["user_id"] = user.user_id
    payload["nickname"] = user.nickname or user.user_id
    payload["expires_at"] = datetime.utcnow() + UNITY_OAUTH_TICKET_TTL
    return ticket


def _get_pending_unity_linuxdo_oauth(device_code: str) -> Dict[str, Any] | None:
    sanitized_device_code = _sanitize_unity_device_code(device_code)
    if not sanitized_device_code:
        return None

    _cleanup_pending_unity_linuxdo_oauth()
    payload = UNITY_LINUXDO_OAUTH_PENDING.get(sanitized_device_code)
    if not isinstance(payload, dict):
        return None
    return payload


def _consume_pending_unity_linuxdo_oauth(device_code: str, ticket: str) -> Dict[str, Any] | None:
    sanitized_device_code = _sanitize_unity_device_code(device_code)
    normalized_ticket = str(ticket or "").strip()
    if not sanitized_device_code or not normalized_ticket:
        return None

    _cleanup_pending_unity_linuxdo_oauth()
    payload = UNITY_LINUXDO_OAUTH_PENDING.get(sanitized_device_code)
    if not isinstance(payload, dict):
        return None

    if payload.get("status") != "authorized" or payload.get("ticket") != normalized_ticket:
        return None

    UNITY_LINUXDO_OAUTH_PENDING.pop(sanitized_device_code, None)
    return payload


def _store_linuxdo_oauth_intent(flow: Any, next_path: Any, device_code: Any, redirect_uri: str):
    session["linuxdo_oauth_flow"] = _normalize_linuxdo_login_flow(flow)

    sanitized_next = _sanitize_post_login_path(next_path)
    if sanitized_next:
        session["linuxdo_oauth_next"] = sanitized_next
    else:
        session.pop("linuxdo_oauth_next", None)

    sanitized_device_code = _sanitize_unity_device_code(device_code)
    if session["linuxdo_oauth_flow"] == "game" and sanitized_device_code:
        session["linuxdo_oauth_device_code"] = sanitized_device_code
    else:
        session.pop("linuxdo_oauth_device_code", None)

    if redirect_uri:
        session["linuxdo_oauth_redirect_uri"] = redirect_uri
    else:
        session.pop("linuxdo_oauth_redirect_uri", None)


def _pop_linuxdo_oauth_intent() -> Dict[str, str]:
    return {
        "flow": _normalize_linuxdo_login_flow(session.pop("linuxdo_oauth_flow", "webui")),
        "next_path": _sanitize_post_login_path(session.pop("linuxdo_oauth_next", "")),
        "device_code": _sanitize_unity_device_code(session.pop("linuxdo_oauth_device_code", "")),
        "redirect_uri": str(session.pop("linuxdo_oauth_redirect_uri", "") or "").strip(),
    }


async def _complete_linuxdo_login(user):
    intent = _pop_linuxdo_oauth_intent()
    if intent["flow"] == "game":
        ticket = _complete_pending_unity_linuxdo_oauth(intent["device_code"], user)
        return await render_template(
            "oauth_complete.html",
            linked_user_id=user.user_id,
            nickname=user.nickname or user.user_id,
            unity_ticket_ready=bool(ticket),
        )

    if intent["next_path"]:
        return redirect(intent["next_path"])

    return redirect(url_for("player_bp.index"))


def _get_login_template_context(**kwargs):
    oauth_config = _get_linuxdo_oauth_config()
    context = {
        "first_login": False,
        "oauth_enabled": oauth_config.get("enabled", False),
        "oauth_configured": oauth_config.get("login_entry_enabled", False),
        "oauth_misconfigured": oauth_config.get("enabled", False) and not oauth_config.get("configured", False),
        "allow_password_login": oauth_config.get("allow_password_fallback", True) or not oauth_config.get("login_entry_enabled", False),
        "oauth_login_url": url_for("player_bp.login_with_linuxdo", flow="webui") if oauth_config.get("login_entry_enabled", False) else None,
    }
    context.update(_get_player_link_config())
    context.update(kwargs)
    return context


async def _render_login_page(**kwargs):
    return await render_template("login.html", **_get_login_template_context(**kwargs))


async def _exchange_linuxdo_access_token(code: str, oauth_config: Dict[str, Any]) -> Dict[str, Any]:
    proxy_url = oauth_config.get("proxy_url")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    def _request_token():
        response = requests.post(
            oauth_config["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": oauth_config["redirect_uri"],
            },
            headers={"Accept": "application/json"},
            auth=(oauth_config["client_id"], oauth_config["client_secret"]),
            timeout=15,
            proxies=proxies,
        )
        response.raise_for_status()
        return response.json()

    return await asyncio.to_thread(_request_token)


async def _fetch_linuxdo_user_profile(access_token: str, oauth_config: Dict[str, Any]) -> Dict[str, Any]:
    proxy_url = oauth_config.get("proxy_url")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    def _request_profile():
        response = requests.get(
            oauth_config["user_api_url"],
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=15,
            proxies=proxies,
        )
        response.raise_for_status()
        return response.json()

    payload = await asyncio.to_thread(_request_profile)
    if isinstance(payload, dict) and isinstance(payload.get("user"), dict):
        return payload["user"]
    return payload


def _resolve_linuxdo_user(profile: Dict[str, Any], user_repo, user_service, oauth_config: Dict[str, Any]):
    provider_key = _get_linuxdo_provider_key(profile)
    if not provider_key:
        return None, "Linux.do 用户资料中缺少可用于绑定的唯一标识"

    linked_user_id = _get_linked_game_user_id(provider_key)
    if linked_user_id:
        linked_user = user_repo.get_by_id(linked_user_id)
        if linked_user:
            return linked_user, None
        logger.warning(f"Linux.do 绑定存在失效用户: {provider_key} -> {linked_user_id}")

    candidate_user_id = _get_linuxdo_candidate_user_id(profile, oauth_config)
    if not candidate_user_id:
        field_name = oauth_config.get("user_id_field", "id")
        return None, f"Linux.do 用户资料缺少字段 {field_name}，无法映射到游戏账号"

    user = user_repo.get_by_id(candidate_user_id)
    if user:
        _bind_linuxdo_account(profile, candidate_user_id)
        return user, None

    if not oauth_config.get("auto_register", True):
        return None, (
            f"论坛账号已验证，但未找到游戏账号 {candidate_user_id}。"
            "请先在游戏内注册，或调整 user_id_field/绑定表配置。"
        )

    nickname = _get_linuxdo_display_name(profile, oauth_config)
    register_result = user_service.register(candidate_user_id, nickname)
    if not register_result.get("success"):
        return None, register_result.get("message", "自动注册失败")

    user = user_repo.get_by_id(candidate_user_id)
    if not user:
        return None, "自动注册完成后未能读取到玩家账号"

    _bind_linuxdo_account(profile, candidate_user_id)
    return user, None


def _build_unity_linuxdo_status_response(payload: Dict[str, Any]):
    return {
        "success": True,
        "status": str(payload.get("status", "pending") or "pending"),
        "message": str(payload.get("message", "") or ""),
        "ticket": str(payload.get("ticket", "") or ""),
        "data": {
            "user_id": str(payload.get("user_id", "") or ""),
            "nickname": str(payload.get("nickname", "") or ""),
            "first_login": False,
        },
    }

def _get_user_title(current_title_id, item_template_repo):
    """获取用户称号名称"""
    if not current_title_id:
        return "无称号"
    
    # 尝试从模板仓储获取称号
    if hasattr(item_template_repo, 'get_title_by_id'):
        title_info = item_template_repo.get_title_by_id(current_title_id)
        if title_info:
            return title_info.name
    
    # 简单映射
    title_names = {
        1: "新手渔夫",
        2: "钓鱾爱好者",
        3: "渔业专家",
        4: "传奇渔夫"
    }
    return title_names.get(current_title_id, f"称号#{current_title_id}")

def _get_leaderboard_data(user_repo, item_template_repo, top_n=10):
    """获取排行榜数据，包含用户称号显示"""
    try:
        # 获取所有用户
        all_users = user_repo.get_all_users()

        def _build_rank_list(users, key_fn, extra_field_name=None):
            ranking = sorted(users, key=key_fn, reverse=True)[:top_n]
            result = []
            for idx, u in enumerate(ranking):
                title = _get_user_title(getattr(u, 'current_title_id', None), item_template_repo)
                entry = {
                    "rank": idx + 1,
                    "user_id": u.user_id,
                    "nickname": u.nickname,
                    "current_title_id": getattr(u, 'current_title_id', None),
                    "title": title
                }
                if extra_field_name:
                    entry[extra_field_name] = getattr(u, extra_field_name, 0)
                result.append(entry)
            return result

        coins_leaderboard = _build_rank_list(all_users, lambda u: u.coins, 'coins')
        fishing_leaderboard = _build_rank_list(all_users, lambda u: u.total_fishing_count, 'total_fishing_count')
        earned_leaderboard = _build_rank_list(all_users, lambda u: u.total_coins_earned, 'total_coins_earned')

        return {
            "coins": coins_leaderboard,
            "fishing": fishing_leaderboard,
            "earned": earned_leaderboard
        }
    except Exception as e:
        logger.error(f"获取排行榜数据失败: {e}")
        return {
            "coins": [],
            "fishing": [],
            "earned": []
        }

def _get_or_create_daily_exhibition(exhibition_file, user_repo, aquarium_service, inventory_repo, item_template_repo):
    """获取或创建今日展览数据"""
    from datetime import datetime, date
    import random
    
    today = date.today().isoformat()
    
    # 读取展览数据
    if os.path.exists(exhibition_file):
        with open(exhibition_file, "r", encoding="utf-8") as f:
            exhibition_data = json.load(f)
    else:
        exhibition_data = {"date": "", "featured_user": None, "comments": {}}

    # 如果文件中已经有今日的展览数据，确保其中的鱼类条目包含 description/actual_value 等字段。
    if exhibition_data.get("featured_user") and exhibition_data.get("date"):
        try:
            featured = exhibition_data.get("featured_user")
            fishes = featured.get("aquarium", []) if isinstance(featured.get("aquarium", []), list) else []
            for idx, fish in enumerate(fishes):
                if not isinstance(fish, dict):
                    continue
                # 如果缺少描述信息，从模板仓储补充
                try:
                    fish_template = item_template_repo.get_fish_by_id(fish.get("fish_id"))
                except Exception:
                    fish_template = None

                if fish_template:
                    if not fish.get("description"):
                        fish["description"] = fish_template.description or "暂无描述"
                    if not fish.get("actual_value"):
                        fish["actual_value"] = fish_template.base_value * (1 + fish.get("quality_level", 0))

            # 将补充后的数据写回内存对象（不强制覆盖文件）
            exhibition_data["featured_user"]["aquarium"] = fishes
        except Exception:
            # 在补充展览数据时忽略错误，避免影响页面渲染
            pass
    
    # 检查是否需要更新展览
    if exhibition_data.get("date") != today:
        # 随机选择一个有水族箱内容的用户
        all_users = user_repo.get_all_users()
        eligible_users = []
        
        for user in all_users:
            aquarium_result = aquarium_service.get_user_aquarium(user.user_id)
            if aquarium_result.get("fishes") and len(aquarium_result["fishes"]) > 0:
                eligible_users.append(user)
        
        if eligible_users:
            featured_user = random.choice(eligible_users)
            
            # 获取用户装备信息
            equipped_rod = None
            rod_instance = inventory_repo.get_user_equipped_rod(featured_user.user_id)
            if rod_instance:
                rod_template = item_template_repo.get_rod_by_id(rod_instance.rod_id)
                if rod_template:
                    equipped_rod = {
                        "name": rod_template.name,
                        "rarity": rod_template.rarity,
                        "refine_level": rod_instance.refine_level
                    }
            
            equipped_accessory = None
            acc_instance = inventory_repo.get_user_equipped_accessory(featured_user.user_id)
            if acc_instance:
                acc_template = item_template_repo.get_accessory_by_id(acc_instance.accessory_id)
                if acc_template:
                    equipped_accessory = {
                        "name": acc_template.name,
                        "rarity": acc_template.rarity,
                        "refine_level": acc_instance.refine_level
                    }
            
            current_bait = None
            if featured_user.current_bait_id:
                bait_template = item_template_repo.get_bait_by_id(featured_user.current_bait_id)
                if bait_template:
                    bait_inventory = inventory_repo.get_user_bait_inventory(featured_user.user_id)
                    current_bait = {
                        "name": bait_template.name,
                        "rarity": bait_template.rarity,
                        "quantity": bait_inventory.get(featured_user.current_bait_id, 0)
                    }
            
            # 获取用户称号
            current_title = "无称号"
            if featured_user.current_title_id:
                # 尝试从模板仓储获取称号
                if hasattr(item_template_repo, 'get_title_by_id'):
                    title_info = item_template_repo.get_title_by_id(featured_user.current_title_id)
                    if title_info:
                        current_title = title_info.name
                    else:
                        current_title = f"称号#{featured_user.current_title_id}"
                else:
                    # 简单映射
                    title_names = {
                        1: "新手渔夫",
                        2: "钓鱼爱好者",
                        3: "渔业专家",
                        4: "传奇渔夫"
                    }
                    current_title = title_names.get(featured_user.current_title_id, f"称号#{featured_user.current_title_id}")
            
            # 获取水族箱内容
            aquarium_result = aquarium_service.get_user_aquarium(featured_user.user_id)
            
            # 为每条鱼添加完整的模板信息（参考pokedex图鉴页格式）
            enhanced_fishes = []
            for fish in aquarium_result.get("fishes", []):
                # aquarium_service已经返回了enriched的数据，直接使用
                enhanced_fish = fish.copy()
                
                # 获取完整的鱼类模板信息
                fish_template = item_template_repo.get_fish_by_id(fish["fish_id"])
                if fish_template:
                    # 确保有actual_value
                    if 'actual_value' not in enhanced_fish:
                        enhanced_fish["actual_value"] = fish_template.base_value * (1 + fish.get("quality_level", 0))
                    
                    # 描述信息
                    enhanced_fish["description"] = fish_template.description or "一条神秘的鱼"
                    enhanced_fish["base_value"] = fish_template.base_value
                    
                enhanced_fishes.append(enhanced_fish)
            
            exhibition_data = {
                "date": today,
                "featured_user": {
                    "user_id": featured_user.user_id,
                    "nickname": featured_user.nickname or f"渔夫{featured_user.user_id[-4:]}",
                    "current_title": current_title,
                    "equipped_rod": equipped_rod,
                    "equipped_accessory": equipped_accessory,
                    "current_bait": current_bait,
                    "aquarium": enhanced_fishes,
                    "stats": aquarium_result.get("stats", {})
                },
                "comments": {}  # 新的一天清空留言
            }
            
            # 保存展览数据
            with open(exhibition_file, "w", encoding="utf-8") as f:
                json.dump(exhibition_data, f, ensure_ascii=False, indent=2)
        else:
            exhibition_data = {"date": today, "featured_user": None, "comments": {}}
    
    return exhibition_data

def create_player_app(services: Dict[str, Any], webui_options: Dict[str, Any] | None = None):
    """
    创建并配置玩家WebUI的Quart应用实例。

    Args:
        services: 包含所有需要注入的服务实例的字典。
    """
    app = Quart(__name__)
    app.secret_key = os.urandom(24)
    webui_options = webui_options or {}

    # 将服务实例存入app配置
    for service_name, service_instance in services.items():
        app.config[service_name.upper()] = service_instance

    public_base_url = normalize_public_base_url(webui_options.get("public_base_url"))
    app.config["PUBLIC_BASE_URL"] = public_base_url
    app.config["PLAYER_GITHUB_URL"] = _normalize_external_url(webui_options.get("github_url"), DEFAULT_GITHUB_URL)
    app.config["PLAYER_APK_DOWNLOAD_URL"] = _normalize_external_url(webui_options.get("apk_download_url"), DEFAULT_APK_DOWNLOAD_URL)
    app.config["TAVERN_ADMIN_USER_ID"] = str(
        webui_options.get("tavern_admin_user_id", "") or DEFAULT_TAVERN_ADMIN_USER_ID
    ).strip()
    app.config["UNITY_ALLOWED_ORIGINS"] = webui_options.get("unity_allowed_origins", [])
    app.config["LINUXDO_OAUTH_CONFIG"] = webui_options.get("linuxdo_oauth", {})
    if public_base_url.startswith("https://"):
        app.config.setdefault("SESSION_COOKIE_SECURE", True)
        app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")

    app.register_blueprint(player_bp, url_prefix="/player")
    app.register_blueprint(user_api_bp)
    app.register_blueprint(unity_api_bp)
    register_unity_user_api_routes(app)
    install_unity_cors(app)

    @app.route("/")
    def root():
        return redirect(url_for("player_bp.index"))
    
    @app.route("/favicon.ico")
    def favicon():
        from quart import abort
        abort(404)
    
    @app.errorhandler(404)
    async def handle_404_error(error):
        if not request.path.startswith('/player/static/') and request.path != '/favicon.ico':
            logger.error(f"404 Not Found: {request.url} - {request.method}")
        return "Not Found", 404
    
    @app.errorhandler(500)
    async def handle_500_error(error):
        logger.error(f"Internal Server Error: {error}")
        import traceback
        logger.error(traceback.format_exc())
        return "Internal Server Error", 500
    
    return app

def login_required(f):
    """装饰器：要求用户已登录"""
    @functools.wraps(f)
    async def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("player_bp.login"))
        return await f(*args, **kwargs)
    return decorated_function

# ==================== 认证路由 ====================

@player_bp.route("/login", methods=["GET", "POST"])
async def login():
    """用户登录页面"""
    if session.get("user_id"):
        return redirect(url_for("player_bp.index"))

    oauth_config = _get_linuxdo_oauth_config()
    if request.method == "POST":
        if oauth_config.get("login_entry_enabled") and not oauth_config.get("allow_password_fallback", True):
            await flash("当前站点仅允许使用 Linux.do 登录", "warning")
            return await _render_login_page()

        form = await request.form
        user_id = form.get("user_id", "").strip()
        password = form.get("password", "").strip()

        if not user_id:
            await flash("请输入用户ID", "danger")
            return await _render_login_page()
        if len(password) < 5 or len(password) > 20:
            await flash("登录密钥长度需要为 5~20 字", "danger")
            return await _render_login_page()

        # 检查用户是否存在
        user_repo = current_app.config.get("USER_REPO")
        user = user_repo.get_by_id(user_id)
        
        if not user:
            await flash("🎣 你不是我们的钓鱼佬，去别处钓鱼吧！", "warning")
            logger.warning(f"未注册用户 {user_id} 尝试登录")
            return await _render_login_page()

        ensure_initial_password(user_id)

        # 验证密钥，兼容旧登录密钥与自动生成的初始密码
        if not verify_user_password(user_id, password):
            await flash("密钥错误", "danger")
            return await _render_login_page()
        
        # 登录成功
        _login_player_session(user)
        await flash(f"欢迎回来，{user.nickname or user_id}！", "success")
        logger.info(f"用户 {user_id} 登录成功")
        return redirect(url_for("player_bp.index"))
    
    # GET请求，显示登录页面
    return await _render_login_page()


@player_bp.route("/login/linuxdo")
async def login_with_linuxdo():
    """跳转到 Linux.do OAuth 授权页"""
    oauth_config = _get_linuxdo_oauth_config()
    if not oauth_config.get("login_entry_enabled"):
        await flash("Linux.do 登录未启用或配置不完整", "warning")
        return redirect(url_for("player_bp.login"))

    flow = _resolve_linuxdo_login_flow_from_request()
    device_code = request.args.get("device_code", "")
    if flow == "game":
        device_code = _register_pending_unity_linuxdo_oauth(device_code)
        if not device_code:
            await flash("Unity 登录缺少有效的设备标识，请返回游戏重新发起授权", "danger")
            return redirect(url_for("player_bp.login"))

    redirect_uri = _resolve_linuxdo_redirect_uri(oauth_config)
    if not redirect_uri:
        if device_code:
            _mark_unity_linuxdo_oauth_error(device_code, "Linux.do 回调地址未配置")
        await flash("Linux.do 回调地址未配置", "danger")
        return redirect(url_for("player_bp.login"))

    _store_linuxdo_oauth_intent(flow, request.args.get("next", ""), device_code, redirect_uri)
    state = secrets.token_urlsafe(24)
    session["linuxdo_oauth_state"] = state
    query = urlencode(
        {
            "client_id": oauth_config["client_id"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": oauth_config["scope"],
            "state": state,
        }
    )
    return redirect(f"{oauth_config['authorize_url']}?{query}")


@player_bp.route("/oauth/linuxdo/callback")
async def linuxdo_oauth_callback():
    """处理 Linux.do OAuth 授权回调"""
    oauth_config = _get_linuxdo_oauth_config()
    pending_device_code = _sanitize_unity_device_code(session.get("linuxdo_oauth_device_code", ""))
    if not oauth_config.get("login_entry_enabled"):
        if pending_device_code:
            _mark_unity_linuxdo_oauth_error(pending_device_code, "Linux.do 登录未启用或配置不完整")
        await flash("Linux.do 登录未启用或配置不完整", "warning")
        return redirect(url_for("player_bp.login"))

    error = request.args.get("error", "").strip()
    if error:
        if pending_device_code:
            _mark_unity_linuxdo_oauth_error(pending_device_code, f"Linux.do 授权失败：{error}")
        await flash(f"Linux.do 授权失败：{error}", "danger")
        return redirect(url_for("player_bp.login"))

    state = request.args.get("state", "").strip()
    expected_state = session.pop("linuxdo_oauth_state", "")
    if not expected_state or state != expected_state:
        if pending_device_code:
            _mark_unity_linuxdo_oauth_error(pending_device_code, "Linux.do 登录状态校验失败，请重试")
        await flash("Linux.do 登录状态校验失败，请重试", "danger")
        return redirect(url_for("player_bp.login"))

    code = request.args.get("code", "").strip()
    if not code:
        if pending_device_code:
            _mark_unity_linuxdo_oauth_error(pending_device_code, "Linux.do 回调缺少授权码")
        await flash("Linux.do 回调缺少授权码", "danger")
        return redirect(url_for("player_bp.login"))

    user_repo = current_app.config.get("USER_REPO")
    user_service = current_app.config.get("USER_SERVICE")
    session_redirect_uri = str(session.get("linuxdo_oauth_redirect_uri", "") or "").strip()
    effective_oauth_config = dict(oauth_config)
    effective_oauth_config["redirect_uri"] = session_redirect_uri or _resolve_linuxdo_redirect_uri(oauth_config)

    try:
        token_payload = await _exchange_linuxdo_access_token(code, effective_oauth_config)
        access_token = _normalize_profile_value(token_payload.get("access_token"))
        if not access_token:
            raise ValueError("Linux.do 未返回 access_token")

        profile = await _fetch_linuxdo_user_profile(access_token, effective_oauth_config)
        user, error_message = _resolve_linuxdo_user(profile, user_repo, user_service, effective_oauth_config)
        if error_message:
            if pending_device_code:
                _mark_unity_linuxdo_oauth_error(pending_device_code, error_message)
            await flash(error_message, "warning")
            return redirect(url_for("player_bp.login"))

        ensure_initial_password(user.user_id)
        _login_player_session(user, auth_provider="linuxdo")
        await flash(f"欢迎来到钓鱼世界，{user.nickname or user.user_id}！", "success")
        logger.info(f"Linux.do 用户登录成功: {user.user_id}")
        return await _complete_linuxdo_login(user)
    except requests.RequestException as e:
        if pending_device_code:
            _mark_unity_linuxdo_oauth_error(pending_device_code, "连接 Linux.do OAuth 服务失败，请稍后重试")
        logger.error(f"Linux.do OAuth 请求失败: {e}")
        await flash("连接 Linux.do OAuth 服务失败，请稍后重试", "danger")
    except Exception as e:
        if pending_device_code:
            _mark_unity_linuxdo_oauth_error(pending_device_code, f"Linux.do 登录失败：{e}")
        logger.error(f"Linux.do OAuth 登录失败: {e}", exc_info=True)
        await flash(f"Linux.do 登录失败：{e}", "danger")

    return redirect(url_for("player_bp.login"))


async def linuxdo_oauth_status():
    """给 Unity 轮询 Linux.do OAuth 状态"""
    device_code = _sanitize_unity_device_code(request.args.get("device_code", ""))
    if not device_code:
        return jsonify({"success": False, "status": "invalid", "message": "缺少有效的设备标识"}), 400

    payload = _get_pending_unity_linuxdo_oauth(device_code)
    if not payload:
        return jsonify({"success": False, "status": "expired", "message": "登录请求不存在或已过期，请重新发起授权"}), 404

    return jsonify(_build_unity_linuxdo_status_response(payload))


async def consume_linuxdo_oauth_for_unity():
    """给 Unity 消费一次性 Linux.do 登录票据并建立自己的 session cookie"""
    form = await request.form
    device_code = _sanitize_unity_device_code(form.get("device_code", ""))
    ticket = str(form.get("ticket", "") or "").strip()

    if not device_code or not ticket:
        return jsonify({"success": False, "message": "缺少必要的登录票据"}), 400

    payload = _consume_pending_unity_linuxdo_oauth(device_code, ticket)
    if not payload:
        return jsonify({"success": False, "message": "登录票据无效或已过期，请重新发起授权"}), 404

    user_repo = current_app.config.get("USER_REPO")
    user = user_repo.get_by_id(payload.get("user_id", ""))
    if not user:
        return jsonify({"success": False, "message": "对应的游戏账号不存在"}), 404

    session.clear()
    _login_player_session(user, auth_provider="linuxdo")
    logger.info(f"Unity 通过 Linux.do 票据登录成功: {user.user_id}")
    return jsonify({
        "success": True,
        "message": f"欢迎来到钓鱼世界，{user.nickname or user.user_id}！",
        "data": {
            "user_id": user.user_id,
            "nickname": user.nickname or user.user_id,
            "first_login": False,
        }
    })

# ==================== API路由 ====================

@player_bp.route("/api/toggle_auto_fishing", methods=["POST"])
@login_required
async def toggle_auto_fishing():
    """切换自动钓鱼状态"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    
    user = user_repo.get_by_id(user_id)
    if not user:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    
    # 切换状态
    new_state = not user.auto_fishing_enabled
    user.auto_fishing_enabled = new_state
    user_repo.update(user)
    
    return jsonify({
        "success": True,
        "auto_fishing_enabled": new_state,
        "message": f"自动钓鱼已{'开启' if new_state else '关闭'}"
    })

@player_bp.route("/api/change_zone", methods=["POST"])
@login_required
async def change_zone():
    """切换钓鱼区域"""
    user_id = session.get("user_id")
    form = await request.form
    zone_id = form.get("zone_id")
    
    if not zone_id:
        return jsonify({"success": False, "message": "未指定区域"}), 400
    
    try:
        zone_id = int(zone_id)
    except ValueError:
        return jsonify({"success": False, "message": "无效的区域ID"}), 400
    
    fishing_service = current_app.config.get("FISHING_SERVICE")
    if not fishing_service:
        return jsonify({"success": False, "message": "服务不可用"}), 500
    
    # 调用fishing_service切换区域
    result = fishing_service.set_user_fishing_zone(user_id, zone_id)
    
    if result.get("success"):
        return jsonify({
            "success": True,
            "message": result.get("message", "切换成功")
        })
    else:
        return jsonify({
            "success": False,
            "message": result.get("message", "切换失败")
        }), 400

@player_bp.route("/api/sell_fish", methods=["POST"])
@login_required
async def api_sell_fish():
    """出售鱼类API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json() or {}
        fish_id = data.get("fish_id")
        quality_level = data.get("quality_level", 0)
        quantity = data.get("quantity", 1)
        
        if not fish_id or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = inventory_service.sell_fish(user_id, fish_id, quantity, quality_level)
        return jsonify(result)
    except Exception as e:
        logger.error(f"出售鱼类失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/daily_checkin", methods=["POST"])
@login_required
async def api_daily_checkin():
    """每日签到API"""
    user_id = session.get("user_id")
    user_service = current_app.config.get("USER_SERVICE")
    
    try:
        result = user_service.daily_sign_in(user_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"签到失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/sell_all_fish", methods=["POST"])
@login_required
async def api_sell_all_fish():
    """全部卖出鱼类API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json()
        keep_one = data.get("keep_one", False)
        
        result = inventory_service.sell_all_fish(user_id, keep_one)
        return jsonify(result)
    except Exception as e:
        logger.error(f"全部卖出鱼类失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/add_to_aquarium", methods=["POST"])
@login_required
async def api_add_to_aquarium():
    """添加鱼到水族箱API"""
    user_id = session.get("user_id")
    aquarium_service = current_app.config.get("AQUARIUM_SERVICE")
    
    try:
        data = await request.get_json()
        fish_id = data.get("fish_id")
        quality_level = data.get("quality_level", 0)
        quantity = data.get("quantity", 1)
        
        if not fish_id or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = aquarium_service.add_fish_to_aquarium(user_id, fish_id, quantity, quality_level)
        return jsonify(result)
    except Exception as e:
        logger.error(f"添加到水族箱失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/buy_shop_item", methods=["POST"])
@login_required
async def api_buy_shop_item():
    """购买商店商品API"""
    user_id = session.get("user_id")
    shop_service = current_app.config.get("SHOP_SERVICE")
    if shop_service is None:
        return jsonify({"success": False, "message": "商店系统未初始化"}), 500
    
    try:
        data = await _read_request_payload()
        item_id = int(data.get("item_id") or 0)
        quantity = int(data.get("quantity", 1) or 1)
        
        if item_id <= 0 or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = shop_service.purchase_item(user_id, item_id, quantity)
        status_code = 200 if result.get("success", False) else 400
        return jsonify(result), status_code
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "参数无效"}), 400
    except Exception as e:
        logger.error(f"购买商店商品失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/list_item", methods=["POST"])
@login_required
async def api_list_item():
    """上架物品到市场API"""
    user_id = session.get("user_id")
    market_service = current_app.config.get("MARKET_SERVICE")
    
    try:
        data = await request.get_json()
        item_type = data.get("item_type")
        item_instance_id = data.get("item_instance_id")
        price = data.get("price")
        is_anonymous = data.get("is_anonymous", False)
        quantity = data.get("quantity", 1)
        quality_level = data.get("quality_level", 0)
        
        if not item_type or not item_instance_id or not price:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = market_service.put_item_on_sale(
            user_id, item_type, item_instance_id, price, 
            is_anonymous, quantity, quality_level
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"上架物品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/buy_market_item", methods=["POST"])
@login_required
async def api_buy_market_item():
    """购买市场商品API"""
    user_id = session.get("user_id")
    market_service = current_app.config.get("MARKET_SERVICE")
    
    try:
        data = await request.get_json()
        market_id = data.get("market_id")
        
        if not market_id:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = market_service.buy_market_item(user_id, market_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"购买市场商品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/delist_item", methods=["POST"])
@login_required
async def api_delist_item():
    """下架市场商品API"""
    user_id = session.get("user_id")
    market_service = current_app.config.get("MARKET_SERVICE")
    
    try:
        data = await request.get_json()
        market_id = data.get("market_id")
        
        if not market_id:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = market_service.delist_item(user_id, market_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"下架物品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/open_exchange_account", methods=["POST"])
@login_required
async def api_open_exchange_account():
    """开通期货账户API"""
    user_id = session.get("user_id")
    exchange_service = current_app.config.get("EXCHANGE_SERVICE")
    
    try:
        result = exchange_service.open_exchange_account(user_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"开通交易所账户失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@player_bp.route("/api/upgrade_exchange_capacity", methods=["POST"])
@login_required
async def api_upgrade_exchange_capacity():
    """升级期货容量API"""
    user_id = session.get("user_id")
    exchange_service = current_app.config.get("EXCHANGE_SERVICE")

    try:
        result = exchange_service.upgrade_exchange_capacity(user_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"升级期货容量失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@player_bp.route("/api/upgrade_fishpond_capacity", methods=["POST"])
@login_required
async def api_upgrade_fishpond_capacity():
    """升级鱼塘容量API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        result = inventory_service.upgrade_fish_pond(user_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"升级鱼塘容量失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@player_bp.route("/api/upgrade_aquarium_capacity", methods=["POST"])
@login_required
async def api_upgrade_aquarium_capacity():
    """升级水族箱容量API"""
    user_id = session.get("user_id")
    aquarium_service = current_app.config.get("AQUARIUM_SERVICE")

    try:
        result = aquarium_service.upgrade_aquarium(user_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"升级水族箱容量失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/buy_commodity", methods=["POST"])
@login_required
async def api_buy_commodity():
    """购买大宗商品API"""
    user_id = session.get("user_id")
    exchange_service = current_app.config.get("EXCHANGE_SERVICE")
    
    try:
        data = await request.get_json()
        commodity_id = data.get("commodity_id")
        quantity = data.get("quantity")
        current_price = data.get("current_price")
        
        if not commodity_id or not quantity or not current_price:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = exchange_service.purchase_commodity(user_id, commodity_id, quantity, current_price)
        return jsonify(result)
    except Exception as e:
        logger.error(f"购买商品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/sell_commodity", methods=["POST"])
@login_required
async def api_sell_commodity():
    """卖出大宗商品API"""
    user_id = session.get("user_id")
    exchange_service = current_app.config.get("EXCHANGE_SERVICE")
    
    try:
        data = await request.get_json()
        commodity_id = data.get("commodity_id")
        quantity = data.get("quantity")
        current_price = data.get("current_price")
        
        if not commodity_id or not quantity or not current_price:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = exchange_service.sell_commodity(user_id, commodity_id, quantity, current_price)
        return jsonify(result)
    except Exception as e:
        logger.error(f"卖出商品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/remove_from_aquarium", methods=["POST"])
@login_required
async def api_remove_from_aquarium():
    """从水族箱移除鱼API"""
    user_id = session.get("user_id")
    aquarium_service = current_app.config.get("AQUARIUM_SERVICE")
    
    try:
        data = await request.get_json()
        fish_id = data.get("fish_id")
        quality_level = data.get("quality_level", 0)
        quantity = data.get("quantity", 1)
        
        if not fish_id or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = aquarium_service.remove_fish_from_aquarium(user_id, fish_id, quantity, quality_level)
        return jsonify(result)
    except Exception as e:
        logger.error(f"从水族箱移除失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/batch_move_to_aquarium", methods=["POST"])
@login_required
async def api_batch_move_to_aquarium():
    """批量按稀有度放入水族箱API"""
    user_id = session.get("user_id")
    aquarium_service = current_app.config.get("AQUARIUM_SERVICE")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json()
        rarities = data.get("rarities", [])
        
        if not rarities or not isinstance(rarities, list):
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        # 获取鱼塘信息
        inventory_result = inventory_service.get_user_fish_pond(user_id)
        if not inventory_result.get("success"):
            return jsonify({"success": False, "message": "获取鱼塘信息失败"}), 500
        
        fishes = inventory_result.get("fishes", [])
        total_moved = 0
        high_quality_count = 0
        success_count = 0
        failed_items = []
        
        # 对每个选中的稀有度进行处理
        for rarity in rarities:
            target_fishes = [f for f in fishes if f.get("rarity") == rarity]
            
            for fish in target_fishes:
                fish_id = fish.get("fish_id")
                quantity = fish.get("quantity", 0)
                quality_level = fish.get("quality_level", 0)
                
                if quantity > 0:
                    result = aquarium_service.add_fish_to_aquarium(user_id, fish_id, quantity, quality_level)
                    if result.get("success"):
                        total_moved += quantity
                        if quality_level == 1:
                            high_quality_count += quantity
                        success_count += 1
                    else:
                        failed_items.append(f"{fish.get('name')}({result.get('message')})")
        
        # 构建结果消息
        if total_moved == 0:
            return jsonify({"success": False, "message": "没有可移动的鱼"})
        
        message = f"✅ 成功将 {success_count} 种鱼（共{total_moved}条）放入水族箱"
        if high_quality_count > 0:
            message += f"\n✨ 其中包含 {high_quality_count} 条高品质鱼"
        if failed_items:
            message += f"\n\n⚠️ 部分鱼类移动失败：" + "、".join(failed_items[:3])
            if len(failed_items) > 3:
                message += f" 等{len(failed_items)}项"
        
        return jsonify({"success": True, "message": message})
    except Exception as e:
        logger.error(f"批量放入水族箱失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/batch_remove_from_aquarium", methods=["POST"])
@login_required
async def api_batch_remove_from_aquarium():
    """批量按稀有度移回鱼塘API"""
    user_id = session.get("user_id")
    aquarium_service = current_app.config.get("AQUARIUM_SERVICE")
    
    try:
        data = await request.get_json()
        rarities = data.get("rarities", [])
        
        if not rarities or not isinstance(rarities, list):
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        # 获取水族箱信息
        aquarium_result = aquarium_service.get_user_aquarium(user_id)
        if not aquarium_result.get("success"):
            return jsonify({"success": False, "message": "获取水族箱信息失败"}), 500
        
        fishes = aquarium_result.get("fishes", [])
        total_moved = 0
        high_quality_count = 0
        success_count = 0
        failed_items = []
        
        # 对每个选中的稀有度进行处理
        for rarity in rarities:
            target_fishes = [f for f in fishes if f.get("rarity") == rarity]
            
            for fish in target_fishes:
                fish_id = fish.get("fish_id")
                quantity = fish.get("quantity", 0)
                quality_level = fish.get("quality_level", 0)
                
                if quantity > 0:
                    result = aquarium_service.remove_fish_from_aquarium(user_id, fish_id, quantity, quality_level)
                    if result.get("success"):
                        total_moved += quantity
                        if quality_level == 1:
                            high_quality_count += quantity
                        success_count += 1
                    else:
                        failed_items.append(f"{fish.get('name')}({result.get('message')})")
        
        # 构建结果消息
        if total_moved == 0:
            return jsonify({"success": False, "message": "没有可移动的鱼"})
        
        message = f"✅ 成功将 {success_count} 种鱼（共{total_moved}条）移回鱼塘"
        if high_quality_count > 0:
            message += f"\n✨ 其中包含 {high_quality_count} 条高品质鱼"
        if failed_items:
            message += f"\n\n⚠️ 部分鱼类移动失败：" + "、".join(failed_items[:3])
            if len(failed_items) > 3:
                message += f" 等{len(failed_items)}项"
        
        return jsonify({"success": True, "message": message})
    except Exception as e:
        logger.error(f"批量移回鱼塘失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/equip_rod", methods=["POST"])
@login_required
async def api_equip_rod():
    """装备鱼竿API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json()
        rod_code = data.get("rod_code")
        
        if not rod_code:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        # 解析短码为实例ID
        instance_id = inventory_service.resolve_rod_instance_id(user_id, rod_code)
        if not instance_id:
            return jsonify({"success": False, "message": "无效的鱼竿编号"}), 400
        
        result = inventory_service.equip_item(user_id, instance_id, "rod")
        return jsonify(result)
    except Exception as e:
        logger.error(f"装备鱼竿失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/equip_accessory", methods=["POST"])
@login_required
async def api_equip_accessory():
    """装备饰品API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json()
        accessory_code = data.get("accessory_code")
        
        if not accessory_code:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        # 解析短码为实例ID
        instance_id = inventory_service.resolve_accessory_instance_id(user_id, accessory_code)
        if not instance_id:
            return jsonify({"success": False, "message": "无效的饰品编号"}), 400
        
        result = inventory_service.equip_item(user_id, instance_id, "accessory")
        return jsonify(result)
    except Exception as e:
        logger.error(f"装备饰品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@player_bp.route("/api/unequip_rod", methods=["POST"])
@login_required
async def api_unequip_rod():
    """取消装备鱼竿API"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    inventory_repo = current_app.config.get("INVENTORY_REPO")

    try:
        user = user_repo.get_by_id(user_id)
        if not user:
            return jsonify({"success": False, "message": "用户不存在"}), 404
        user.equipped_rod_instance_id = None
        inventory_repo.set_equipment_status(
            user_id,
            rod_instance_id=None,
            accessory_instance_id=user.equipped_accessory_instance_id,
        )
        user_repo.update(user)
        return jsonify({"success": True, "message": "已取消装备鱼竿"})
    except Exception as e:
        logger.error(f"取消装备鱼竿失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@player_bp.route("/api/unequip_accessory", methods=["POST"])
@login_required
async def api_unequip_accessory():
    """取消装备饰品API"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    inventory_repo = current_app.config.get("INVENTORY_REPO")

    try:
        user = user_repo.get_by_id(user_id)
        if not user:
            return jsonify({"success": False, "message": "用户不存在"}), 404
        user.equipped_accessory_instance_id = None
        inventory_repo.set_equipment_status(
            user_id,
            rod_instance_id=user.equipped_rod_instance_id,
            accessory_instance_id=None,
        )
        user_repo.update(user)
        return jsonify({"success": True, "message": "已取消装备饰品"})
    except Exception as e:
        logger.error(f"取消装备饰品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/refine_rod", methods=["POST"])
@login_required
async def api_refine_rod():
    """精炼鱼竿API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json()
        rod_code = data.get("rod_code")
        
        if not rod_code:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        # 解析短码为实例ID
        instance_id = inventory_service.resolve_rod_instance_id(user_id, rod_code)
        if not instance_id:
            return jsonify({"success": False, "message": "无效的鱼竿编号"}), 400
        
        result = inventory_service.refine(user_id, instance_id, "rod")
        return jsonify(result)
    except Exception as e:
        logger.error(f"精炼鱼竿失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/refine_accessory", methods=["POST"])
@login_required
async def api_refine_accessory():
    """精炼饰品API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json()
        accessory_code = data.get("accessory_code")
        
        if not accessory_code:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        # 解析短码为实例ID
        instance_id = inventory_service.resolve_accessory_instance_id(user_id, accessory_code)
        if not instance_id:
            return jsonify({"success": False, "message": "无效的饰品编号"}), 400
        
        result = inventory_service.refine(user_id, instance_id, "accessory")
        return jsonify(result)
    except Exception as e:
        logger.error(f"精炼饰品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/delete_rod", methods=["POST"])
@login_required
async def api_delete_rod():
    """兼容旧前端：鱼竿删除请求默认改为出售。"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        data = await request.get_json()
        rod_code = data.get("rod_code")
        if not rod_code:
            return jsonify({"success": False, "message": "参数无效"}), 400
        instance_id = inventory_service.resolve_rod_instance_id(user_id, rod_code)
        if not instance_id:
            return jsonify({"success": False, "message": "无效的鱼竿编号"}), 400
        rods = inventory_service.inventory_repo.get_user_rod_instances(user_id)
        rod = next((item for item in rods if item.rod_instance_id == instance_id), None)
        if not rod:
            return jsonify({"success": False, "message": "鱼竿不存在或不属于你"}), 400
        if rod.is_equipped:
            return jsonify({"success": False, "message": "装备中的鱼竿不能删除"}), 400
        if rod.is_locked:
            return jsonify({"success": False, "message": "锁定的鱼竿不能删除"}), 400
        return jsonify(inventory_service.sell_rod(user_id, instance_id))
    except Exception as e:
        logger.error(f"删除鱼竿失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/sell_rod", methods=["POST"])
@login_required
async def api_sell_rod():
    """出售鱼竿API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        data = await request.get_json()
        rod_code = data.get("rod_code")
        if not rod_code:
            return jsonify({"success": False, "message": "参数无效"}), 400
        instance_id = inventory_service.resolve_rod_instance_id(user_id, rod_code)
        if not instance_id:
            return jsonify({"success": False, "message": "无效的鱼竿编号"}), 400
        return jsonify(inventory_service.sell_rod(user_id, instance_id))
    except Exception as e:
        logger.error(f"出售鱼竿失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/delete_accessory", methods=["POST"])
@login_required
async def api_delete_accessory():
    """兼容旧前端：饰品删除请求默认改为出售。"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        data = await request.get_json()
        accessory_code = data.get("accessory_code")
        if not accessory_code:
            return jsonify({"success": False, "message": "参数无效"}), 400
        instance_id = inventory_service.resolve_accessory_instance_id(user_id, accessory_code)
        if not instance_id:
            return jsonify({"success": False, "message": "无效的饰品编号"}), 400
        accessories = inventory_service.inventory_repo.get_user_accessory_instances(user_id)
        accessory = next((item for item in accessories if item.accessory_instance_id == instance_id), None)
        if not accessory:
            return jsonify({"success": False, "message": "饰品不存在或不属于你"}), 400
        if accessory.is_equipped:
            return jsonify({"success": False, "message": "装备中的饰品不能删除"}), 400
        if accessory.is_locked:
            return jsonify({"success": False, "message": "锁定的饰品不能删除"}), 400
        return jsonify(inventory_service.sell_accessory(user_id, instance_id))
    except Exception as e:
        logger.error(f"删除饰品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/sell_accessory", methods=["POST"])
@login_required
async def api_sell_accessory():
    """出售饰品API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        data = await request.get_json()
        accessory_code = data.get("accessory_code")
        if not accessory_code:
            return jsonify({"success": False, "message": "参数无效"}), 400
        instance_id = inventory_service.resolve_accessory_instance_id(user_id, accessory_code)
        if not instance_id:
            return jsonify({"success": False, "message": "无效的饰品编号"}), 400
        return jsonify(inventory_service.sell_accessory(user_id, instance_id))
    except Exception as e:
        logger.error(f"出售饰品失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/delete_item", methods=["POST"])
@login_required
async def api_delete_item():
    """兼容旧前端：道具删除请求默认改为出售。"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        data = await request.get_json()
        item_id = int(data.get("item_id") or 0)
        quantity = int(data.get("quantity", 1) or 1)
        if item_id <= 0 or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        return jsonify(inventory_service.sell_item(user_id, item_id, quantity))
    except Exception as e:
        logger.error(f"删除道具失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/sell_item", methods=["POST"])
@login_required
async def api_sell_item():
    """出售道具API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        data = await request.get_json()
        item_id = int(data.get("item_id") or 0)
        quantity = int(data.get("quantity", 1) or 1)
        if item_id <= 0 or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        return jsonify(inventory_service.sell_item(user_id, item_id, quantity))
    except Exception as e:
        logger.error(f"出售道具失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/delete_bait", methods=["POST"])
@login_required
async def api_delete_bait():
    """兼容旧前端：鱼饵删除请求默认改为出售。"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        data = await request.get_json()
        bait_id = int(data.get("bait_id") or 0)
        quantity = int(data.get("quantity", 1) or 1)
        if bait_id <= 0 or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        return jsonify(inventory_service.sell_bait(user_id, bait_id, quantity))
    except Exception as e:
        logger.error(f"删除鱼饵失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/sell_bait", methods=["POST"])
@login_required
async def api_sell_bait():
    """出售鱼饵API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")

    try:
        data = await request.get_json()
        bait_id = int(data.get("bait_id") or 0)
        quantity = int(data.get("quantity", 1) or 1)
        if bait_id <= 0 or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        return jsonify(inventory_service.sell_bait(user_id, bait_id, quantity))
    except Exception as e:
        logger.error(f"出售鱼饵失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/use_item", methods=["POST"])
@login_required
async def api_use_item():
    """使用道具API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json()
        item_id = data.get("item_id")
        quantity = data.get("quantity", 1)
        
        if not item_id or quantity <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = inventory_service.use_item(user_id, item_id, quantity)
        return jsonify(result)
    except Exception as e:
        logger.error(f"使用道具失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/use_bait", methods=["POST"])
@login_required
async def api_use_bait():
    """使用鱼饵API"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    try:
        data = await request.get_json()
        bait_id = data.get("bait_id")
        
        if not bait_id:
            return jsonify({"success": False, "message": "参数无效"}), 400

        bait_id = int(bait_id)
        if data.get("deactivate"):
            user_repo = current_app.config.get("USER_REPO")
            item_template_repo = current_app.config.get("ITEM_TEMPLATE_REPO")
            user = user_repo.get_by_id(user_id)
            if not user:
                return jsonify({"success": False, "message": "用户不存在"}), 404
            if user.current_bait_id == bait_id:
                user.current_bait_id = None
                user.bait_start_time = None
                user_repo.update(user)
            bait_template = item_template_repo.get_bait_by_id(bait_id)
            if bait_template and inventory_service._supports_bait_armed_state(bait_template):
                inventory_service.set_template_armed_state(user_id, "bait", bait_id, False)
            return jsonify({"success": True, "message": "已停用鱼饵"})
        
        # use_bait方法只使用一个鱼饵并设置为当前使用的鱼饵
        result = inventory_service.use_bait(user_id, bait_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"使用鱼饵失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/get_pool_details")
@login_required
async def api_get_pool_details():
    """获取卡池详情API"""
    gacha_service = current_app.config.get("GACHA_SERVICE")
    
    try:
        pool_id = request.args.get("pool_id", type=int)
        if not pool_id:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = gacha_service.get_pool_details(pool_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"获取卡池详情失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/perform_draw", methods=["POST"])
@login_required
async def api_perform_draw():
    """执行抽卡API"""
    user_id = session.get("user_id")
    gacha_service = current_app.config.get("GACHA_SERVICE")
    
    try:
        data = await request.get_json()
        pool_id = data.get("pool_id")
        num_draws = data.get("num_draws", 1)
        
        if not pool_id or num_draws <= 0:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        result = gacha_service.perform_draw(user_id, pool_id, num_draws)
        return jsonify(result)
    except Exception as e:
        logger.error(f"抽卡失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/perform_multi_draw", methods=["POST"])
@login_required
async def api_perform_multi_draw():
    """执行多次十连抽卡API"""
    user_id = session.get("user_id")
    gacha_service = current_app.config.get("GACHA_SERVICE")
    
    try:
        data = await request.get_json()
        pool_id = data.get("pool_id")
        times = data.get("times", 1)
        
        if not pool_id or times <= 0 or times > 100:
            return jsonify({"success": False, "message": "参数无效，次数必须在1-100之间"}), 400
        
        # 获取卡池信息
        pool = gacha_service.gacha_repo.get_pool_by_id(pool_id)
        if not pool:
            return jsonify({"success": False, "message": "卡池不存在"}), 400
        
        # 计算总消耗
        use_premium_currency = (getattr(pool, "cost_premium_currency", 0) or 0) > 0
        total_draws = times * 10
        if use_premium_currency:
            total_cost = (pool.cost_premium_currency or 0) * total_draws
            cost_type = "高级货币"
        else:
            total_cost = (pool.cost_coins or 0) * total_draws
            cost_type = "金币"
        
        # 统计信息
        total_items = 0
        item_counts = {}
        rarity_counts = {i: 0 for i in range(1, 11)}
        coin_total = 0
        
        # 执行多次十连
        for i in range(times):
            result = gacha_service.perform_draw(user_id, pool_id, num_draws=10)
            if not result.get("success"):
                return jsonify({
                    "success": False,
                    "message": f"第{i+1}次十连失败: {result.get('message')}"
                })
            
            items = result.get("results", [])
            total_items += len(items)
            
            for item in items:
                if item.get("type") == "coins":
                    coin_total += item["quantity"]
                else:
                    item_name = item["name"]
                    rarity = item.get("rarity", 1)
                    
                    item_counts[item_name] = item_counts.get(item_name, 0) + 1
                    
                    if rarity <= 10:
                        rarity_counts[rarity] += 1
                    else:
                        rarity_counts[10] += 1
        
        return jsonify({
            "success": True,
            "times": times,
            "total_items": total_items,
            "total_cost": total_cost,
            "cost_type": cost_type,
            "rarity_counts": rarity_counts,
            "item_counts": item_counts,
            "coin_total": coin_total
        })
    except Exception as e:
        logger.error(f"多次十连失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@player_bp.route("/api/claim_expedition_reward", methods=["POST"])
@login_required
async def api_claim_expedition_reward():
    """领取指定科考的个人奖励。"""
    user_id = session.get("user_id")
    expedition_service = current_app.config.get("EXPEDITION_SERVICE")
    if expedition_service is None:
        return jsonify({"success": False, "message": "科考服务未初始化"}), 500

    try:
        data = await request.get_json()
        expedition_id = str((data or {}).get("expedition_id", "") or "").strip()
        result = expedition_service.claim_expedition_reward(user_id, expedition_id)
        return jsonify(result), (200 if result.get("success") else 400)
    except Exception as e:
        logger.error(f"领取科考奖励失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"领取失败: {str(e)}"}), 500

@player_bp.route("/api/post_message", methods=["POST"])
@login_required
async def api_post_message():
    """发表留言API"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    
    try:
        data = await request.get_json()
        content = data.get("content", "").strip()
        
        if not content:
            return jsonify({"success": False, "message": "留言内容不能为空"}), 400
        
        if len(content) > 500:
            return jsonify({"success": False, "message": "留言内容不能超过500字"}), 400
        
        # 获取用户信息
        user = user_repo.get_by_id(user_id)
        if not user:
            return jsonify({"success": False, "message": "用户不存在"}), 400
        
        # 读取留言数据
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        messages_file = os.path.join(data_dir, "tavern_messages.json")
        
        if os.path.exists(messages_file):
            with open(messages_file, "r", encoding="utf-8") as f:
                tavern_data = json.load(f)
        else:
            tavern_data = {"announcement": "", "messages": []}
        
        # 添加新留言
        import uuid
        new_message = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "username": user.nickname or f"渔夫{user_id[-4:]}",
            "content": content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # 插入到列表开头（最新的在前面）
        tavern_data.setdefault("messages", []).insert(0, new_message)
        
        # 限制最多保存1000条留言
        if len(tavern_data["messages"]) > 1000:
            tavern_data["messages"] = tavern_data["messages"][:1000]
        
        # 保存到文件
        with open(messages_file, "w", encoding="utf-8") as f:
            json.dump(tavern_data, f, ensure_ascii=False, indent=2)
        
        return jsonify({"success": True, "message": "留言发表成功！"})
    except Exception as e:
        logger.error(f"发表留言失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/delete_message", methods=["POST"])
@login_required
async def api_delete_message():
    """删除留言API"""
    user_id = session.get("user_id")
    admin_user_id = _get_tavern_admin_user_id()
    
    try:
        data = await request.get_json()
        message_id = data.get("message_id")
        
        if not message_id:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        # 读取留言数据
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        messages_file = os.path.join(data_dir, "tavern_messages.json")
        
        if not os.path.exists(messages_file):
            return jsonify({"success": False, "message": "留言不存在"}), 404
        
        with open(messages_file, "r", encoding="utf-8") as f:
            tavern_data = json.load(f)
        
        # 查找并删除留言
        messages = tavern_data.get("messages", [])
        message_to_delete = None
        
        for msg in messages:
            if msg.get("id") == message_id:
                message_to_delete = msg
                break
        
        if not message_to_delete:
            return jsonify({"success": False, "message": "留言不存在"}), 404
        
        # 检查权限（只能删除自己的留言或管理员可以删除所有）
        if message_to_delete.get("user_id") != user_id and user_id != admin_user_id:
            return jsonify({"success": False, "message": "无权删除此留言"}), 403
        
        # 删除留言
        tavern_data["messages"] = [msg for msg in messages if msg.get("id") != message_id]
        
        # 保存到文件
        with open(messages_file, "w", encoding="utf-8") as f:
            json.dump(tavern_data, f, ensure_ascii=False, indent=2)
        
        return jsonify({"success": True, "message": "留言已删除"})
    except Exception as e:
        logger.error(f"删除留言失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/update_announcement", methods=["POST"])
@login_required
async def api_update_announcement():
    """更新公告API（仅管理员）"""
    user_id = session.get("user_id")
    admin_user_id = _get_tavern_admin_user_id()

    if user_id != admin_user_id:
        return jsonify({"success": False, "message": "无权限操作"}), 403
    
    try:
        data = await request.get_json()
        content = data.get("content", "")
        
        # 读取留言数据
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        messages_file = os.path.join(data_dir, "tavern_messages.json")
        
        if os.path.exists(messages_file):
            with open(messages_file, "r", encoding="utf-8") as f:
                tavern_data = json.load(f)
        else:
            tavern_data = {"announcement": "", "messages": []}
        
        # 更新公告
        tavern_data["announcement"] = content
        
        # 保存到文件
        with open(messages_file, "w", encoding="utf-8") as f:
            json.dump(tavern_data, f, ensure_ascii=False, indent=2)
        
        return jsonify({"success": True, "message": "公告更新成功！"})
    except Exception as e:
        logger.error(f"更新公告失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/add_exhibition_comment", methods=["POST"])
@login_required
async def api_add_exhibition_comment():
    """添加展览鱼类评论API"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    
    try:
        data = await request.get_json()
        fish_key = data.get("fish_key")  # "fish_id-quality_level" 格式
        content = data.get("content", "").strip()
        
        if not fish_key or not content:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        if len(content) > 200:
            return jsonify({"success": False, "message": "评论内容不能超过200字"}), 400
        
        # 获取用户信息
        user = user_repo.get_by_id(user_id)
        if not user:
            return jsonify({"success": False, "message": "用户不存在"}), 400
        
        # 读取展览数据
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        exhibition_file = os.path.join(data_dir, "aquarium_exhibition.json")
        
        if not os.path.exists(exhibition_file):
            return jsonify({"success": False, "message": "展览数据不存在"}), 404
        
        with open(exhibition_file, "r", encoding="utf-8") as f:
            exhibition_data = json.load(f)
        
        if not exhibition_data.get("featured_user"):
            return jsonify({"success": False, "message": "当前没有展览"}), 404
        
        # 添加评论
        import uuid
        if "comments" not in exhibition_data:
            exhibition_data["comments"] = {}
        
        if fish_key not in exhibition_data["comments"]:
            exhibition_data["comments"][fish_key] = []
        
        new_comment = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "username": user.nickname or f"渔夫{user_id[-4:]}",
            "content": content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        exhibition_data["comments"][fish_key].append(new_comment)
        
        # 保存到文件
        with open(exhibition_file, "w", encoding="utf-8") as f:
            json.dump(exhibition_data, f, ensure_ascii=False, indent=2)
        
        return jsonify({"success": True, "message": "评论发表成功！"})
    except Exception as e:
        logger.error(f"添加展览评论失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/api/delete_exhibition_comment", methods=["POST"])
@login_required
async def api_delete_exhibition_comment():
    """删除展览评论API"""
    user_id = session.get("user_id")
    
    try:
        data = await request.get_json()
        fish_key = data.get("fish_key")
        comment_id = data.get("comment_id")
        
        if not fish_key or not comment_id:
            return jsonify({"success": False, "message": "参数无效"}), 400
        
        # 读取展览数据
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        exhibition_file = os.path.join(data_dir, "aquarium_exhibition.json")
        
        if not os.path.exists(exhibition_file):
            return jsonify({"success": False, "message": "展览数据不存在"}), 404
        
        with open(exhibition_file, "r", encoding="utf-8") as f:
            exhibition_data = json.load(f)
        
        # 检查评论是否存在
        if fish_key not in exhibition_data.get("comments", {}):
            return jsonify({"success": False, "message": "评论不存在"}), 404
        
        comments = exhibition_data["comments"][fish_key]
        comment_to_delete = None
        
        for comment in comments:
            if comment.get("id") == comment_id:
                comment_to_delete = comment
                break
        
        if not comment_to_delete:
            return jsonify({"success": False, "message": "评论不存在"}), 404
        
        # 检查权限（只能删除自己的评论或展览者可以删除所有评论）
        exhibition_owner_id = exhibition_data.get("featured_user", {}).get("user_id")
        if comment_to_delete.get("user_id") != user_id and user_id != exhibition_owner_id:
            return jsonify({"success": False, "message": "无权删除此评论"}), 403
        
        # 删除评论
        exhibition_data["comments"][fish_key] = [
            c for c in comments if c.get("id") != comment_id
        ]
        
        # 如果该鱼没有评论了，删除这个key
        if not exhibition_data["comments"][fish_key]:
            del exhibition_data["comments"][fish_key]
        
        # 保存到文件
        with open(exhibition_file, "w", encoding="utf-8") as f:
            json.dump(exhibition_data, f, ensure_ascii=False, indent=2)
        
        return jsonify({"success": True, "message": "评论已删除"})
    except Exception as e:
        logger.error(f"删除展览评论失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@player_bp.route("/logout")
async def logout():
    """用户登出"""
    user_id = session.get("user_id")
    session.clear()
    if user_id:
        logger.info(f"用户 {user_id} 登出")
    await flash("已成功登出", "info")
    return redirect(url_for("player_bp.login"))

# ==================== 主页面 ====================

@player_bp.route("/")
@player_bp.route("/index")
@login_required
async def index():
    """玩家主页 - 仪表板"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    inventory_repo = current_app.config.get("INVENTORY_REPO")
    item_template_repo = current_app.config.get("ITEM_TEMPLATE_REPO")
    log_repo = current_app.config.get("LOG_REPO")
    buff_repo = current_app.config.get("BUFF_REPO")
    fishing_service = current_app.config.get("FISHING_SERVICE")
    
    user = user_repo.get_by_id(user_id)
    if not user:
        await flash("用户数据异常", "danger")
        return redirect(url_for("player_bp.logout"))
    
    # 使用与游戏中状态显示相同的数据获取函数
    from ..draw.state import get_user_state_data
    from ..core.utils import get_current_daily_marker, get_now
    
    game_config = current_app.config.get("FISHING_SERVICE").config if fishing_service else {}
    user_state = get_user_state_data(
        user_repo, inventory_repo, item_template_repo, 
        log_repo, buff_repo, game_config, user_id
    )
    
    if not user_state:
        await flash("无法获取用户状态", "danger")
        return redirect(url_for("player_bp.logout"))
    
    # 获取基本统计信息
    fish_inventory = inventory_repo.get_fish_inventory(user_id)
    fish_count = sum(item.quantity for item in fish_inventory)
    
    # 计算鱼塘总价值
    fish_pond_value = inventory_repo.get_fish_inventory_value(user_id)
    
    # 计算钓鱼CD剩余时间（考虑鱼饵星级）
    fishing_cooldown_remaining = 0
    if user.last_fishing_time:
        base_cooldown = game_config.get("fishing", {}).get("cooldown_seconds", 180)
        
        # 获取当前鱼饵的星级来计算CD减少
        cooldown_seconds = base_cooldown
        if user.current_bait_id:
            bait_template = item_template_repo.get_bait_by_id(user.current_bait_id)
            if bait_template and bait_template.rarity >= 5:
                # 5星开始，每星减少10%，上限60%（10星）
                reduction_percent = min((bait_template.rarity - 4) * 0.1, 0.6)
                cooldown_seconds = base_cooldown * (1.0 - reduction_percent)
        
        now = get_now()
        if user.last_fishing_time.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        elif user.last_fishing_time.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=user.last_fishing_time.tzinfo)
        
        elapsed = (now - user.last_fishing_time).total_seconds()
        if elapsed < cooldown_seconds:
            fishing_cooldown_remaining = int(cooldown_seconds - elapsed)
    
    user_state['fishing_cooldown_remaining'] = fishing_cooldown_remaining
    
    # 检查当前刷新周期是否已签到
    reset_hour = int(game_config.get("daily_reset_hour", 0) or 0)
    has_checked_in_today = log_repo.has_checked_in(user_id, get_current_daily_marker(reset_hour))
    
    stats = {
        "coins": user.coins,
        "premium_currency": user.premium_currency,
        "total_fishing_count": user.total_fishing_count,
        "fish_count": fish_count,
        "fish_pond_capacity": user.fish_pond_capacity,
        "fish_pond_value": fish_pond_value,
        "consecutive_login_days": user.consecutive_login_days,
        "has_checked_in_today": has_checked_in_today,
    }
    
    return await render_template(
        "index.html",
        user=user,
        stats=stats,
        user_state=user_state,
    )

# ==================== 功能页面（占位符） ====================

@player_bp.route("/profile")
@login_required
async def profile():
    """个人信息页面"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    user_service = current_app.config.get("USER_SERVICE")

    user = user_repo.get_by_id(user_id)
    if not user:
        await flash("用户数据异常", "danger")
        return redirect(url_for("player_bp.logout"))

    initial_password = ensure_initial_password(user_id)
    titles_result = user_service.get_user_titles(user_id) if user_service else {"success": False, "titles": []}
    titles = titles_result.get("titles", []) if titles_result.get("success") else []

    return await render_template(
        "profile.html",
        user=user,
        initial_password=initial_password,
        titles=titles,
    )


@player_bp.route("/profile/nickname", methods=["POST"])
@login_required
async def update_profile_nickname():
    """更新玩家昵称"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    form = await request.form
    nickname = str(form.get("nickname", "") or "").strip()
    if not nickname:
        await flash("昵称不能为空", "danger")
        return redirect(url_for("player_bp.profile"))
    if len(nickname) > 24:
        await flash("昵称最多 24 个字符", "danger")
        return redirect(url_for("player_bp.profile"))

    user = user_repo.get_by_id(user_id)
    if not user:
        await flash("用户数据异常", "danger")
        return redirect(url_for("player_bp.logout"))
    user.nickname = nickname
    user_repo.update(user)
    session["nickname"] = nickname
    await flash("昵称已更新", "success")
    return redirect(url_for("player_bp.profile"))


@player_bp.route("/profile/title", methods=["POST"])
@login_required
async def update_profile_title():
    """佩戴称号"""
    user_id = session.get("user_id")
    user_service = current_app.config.get("USER_SERVICE")
    form = await request.form
    title_text = str(form.get("title_id", "") or "").strip()
    if not title_text.isdigit():
        await flash("请选择有效称号", "danger")
        return redirect(url_for("player_bp.profile"))

    result = user_service.use_title(user_id, int(title_text)) if user_service else {"success": False, "message": "称号服务不可用"}
    await flash(result.get("message", "称号操作完成"), "success" if result.get("success") else "danger")
    return redirect(url_for("player_bp.profile"))


@player_bp.route("/profile/password", methods=["POST"])
@login_required
async def update_profile_password():
    """修改 WebUI 登录密钥"""
    user_id = session.get("user_id")
    form = await request.form
    old_password = str(form.get("old_password", "") or "")
    new_password = str(form.get("new_password", "") or "")

    if not verify_user_password(user_id, old_password):
        await flash("旧密码不正确", "danger")
        return redirect(url_for("player_bp.profile"))
    new_password = new_password.strip()
    if len(new_password) < 5 or len(new_password) > 20:
        await flash("新密码长度需要为 5~20 字", "danger")
        return redirect(url_for("player_bp.profile"))

    set_user_password(user_id, new_password)
    await flash("登录密钥已修改", "success")
    return redirect(url_for("player_bp.profile"))


@player_bp.route("/profile/password/reset", methods=["POST"])
@login_required
async def reset_profile_password():
    """重置 WebUI/App 登录密钥为新的初始密码"""
    user_id = session.get("user_id")
    new_initial_password = reset_user_password_to_new_initial(user_id)
    await flash(f"登录密钥已重置为新的初始密码：{new_initial_password}", "success")
    return redirect(url_for("player_bp.profile"))

@player_bp.route("/pokedex")
@login_required
async def pokedex():
    """鱼类图鉴页面"""
    user_id = session.get("user_id")
    item_template_repo = current_app.config.get("ITEM_TEMPLATE_REPO")
    log_repo = current_app.config.get("LOG_REPO")
    fishing_service = current_app.config.get("FISHING_SERVICE")
    
    # 获取所有鱼类模板
    all_fish = item_template_repo.get_all_fish()
    
    # 从日志中获取用户历史钓到过的鱼类统计
    fish_stats = log_repo.get_user_fish_stats(user_id)
    
    # 创建已钓到的鱼类ID到统计数据的映射
    caught_fish_map = {}
    for stat in fish_stats:
        caught_fish_map[stat.fish_id] = {
            "total_caught": stat.total_caught,
            "first_caught_at": stat.first_caught_at,
            "last_caught_at": stat.last_caught_at
        }
    
    # 按稀有度分组
    fish_by_rarity = {}
    for fish in all_fish:
        rarity = fish.rarity
        if rarity not in fish_by_rarity:
            fish_by_rarity[rarity] = []
        
        is_caught = fish.fish_id in caught_fish_map
        fish_data = {
            "id": fish.fish_id,
            "name": fish.name,
            "rarity": fish.rarity,
            "base_value": fish.base_value,
            "description": fish.description,
            "is_caught": is_caught
        }
        
        # 如果已钓到，添加统计数据
        if is_caught:
            fish_data.update(caught_fish_map[fish.fish_id])
        
        fish_by_rarity[rarity].append(fish_data)
    
    # 排序
    for rarity in fish_by_rarity:
        fish_by_rarity[rarity].sort(key=lambda x: x["id"])

    rarity_progress = []
    for rarity in range(1, 11):
        rarity_fishes = fish_by_rarity.get(rarity, [])
        total_for_rarity = len(rarity_fishes)
        caught_for_rarity = sum(1 for fish in rarity_fishes if fish.get("is_caught"))
        rarity_progress.append({
            "rarity": rarity,
            "caught": caught_for_rarity,
            "total": total_for_rarity,
            "percent": (caught_for_rarity / total_for_rarity * 100) if total_for_rarity > 0 else 0,
        })

    pokedex_reward_status = (
        fishing_service.get_pokedex_reward_status(user_id)
        if fishing_service
        else {"success": False, "message": "服务不可用"}
    )

    return await render_template("pokedex.html", 
                                  fish_by_rarity=fish_by_rarity,
                                  total_fish=len(all_fish),
                                  caught_count=len(caught_fish_map),
                                  rarity_progress=rarity_progress,
                                  pokedex_reward_status=pokedex_reward_status)


@player_bp.route("/pokedex/reward/claim", methods=["POST"])
@login_required
async def claim_pokedex_reward():
    """领取图鉴奖励"""
    user_id = session.get("user_id")
    fishing_service = current_app.config.get("FISHING_SERVICE")
    if not fishing_service:
        await flash("图鉴奖励服务不可用", "danger")
        return redirect(url_for("player_bp.pokedex"))

    result = fishing_service.claim_pokedex_rewards(user_id)
    if not result.get("success"):
        await flash(result.get("message", "图鉴奖励领取失败"), "danger")
        return redirect(url_for("player_bp.pokedex"))

    claimed_totals = result.get("newly_claimed_by_type", {}) or {}
    parts = []
    if int(claimed_totals.get("coins", 0) or 0) > 0:
        parts.append(f"{claimed_totals['coins']} 金币")
    if int(claimed_totals.get("premium", 0) or 0) > 0:
        parts.append(f"{claimed_totals['premium']} 钻石")
    if parts:
        await flash(f"成功领取图鉴奖励：{'、'.join(parts)}", "success")
    else:
        await flash(result.get("message", "当前没有可领取的图鉴奖励"), "info")
    return redirect(url_for("player_bp.pokedex"))


@player_bp.route("/equipment_pokedex")
@login_required
async def equipment_pokedex():
    """装备图鉴页面"""
    user_id = session.get("user_id")
    fishing_service = current_app.config.get("FISHING_SERVICE")
    if not fishing_service:
        await flash("装备图鉴服务不可用", "danger")
        return redirect(url_for("player_bp.index"))

    page_text = request.args.get("page", "1")
    page = int(page_text) if str(page_text).isdigit() else 1
    equipment_type = request.args.get("equipment_type", "all")
    rarity_text = request.args.get("rarity", "all")
    rarity = int(rarity_text) if str(rarity_text).isdigit() else None
    owned_only = request.args.get("owned", "0") == "1"
    pokedex_data = fishing_service.get_user_equipment_pokedex(
        user_id,
        page=page,
        page_size=24,
        equipment_type=equipment_type,
        rarity=rarity,
        owned_only=owned_only,
    )
    reward_status = fishing_service.get_equipment_pokedex_reward_status(user_id)

    return await render_template(
        "equipment_pokedex.html",
        pokedex_data=pokedex_data,
        reward_status=reward_status,
    )


@player_bp.route("/equipment_pokedex/reward/claim", methods=["POST"])
@login_required
async def claim_equipment_pokedex_reward():
    """领取装备图鉴奖励"""
    user_id = session.get("user_id")
    fishing_service = current_app.config.get("FISHING_SERVICE")
    if not fishing_service:
        await flash("装备图鉴奖励服务不可用", "danger")
        return redirect(url_for("player_bp.equipment_pokedex"))

    result = fishing_service.claim_equipment_pokedex_rewards(user_id)
    if not result.get("success"):
        await flash(result.get("message", "装备图鉴奖励领取失败"), "danger")
        return redirect(url_for("player_bp.equipment_pokedex"))

    claimed_totals = result.get("newly_claimed_by_type", {}) or {}
    parts = []
    if int(claimed_totals.get("coins", 0) or 0) > 0:
        parts.append(f"{claimed_totals['coins']} 金币")
    if int(claimed_totals.get("premium", 0) or 0) > 0:
        parts.append(f"{claimed_totals['premium']} 钻石")
    if parts:
        await flash(f"成功领取装备图鉴奖励：{'、'.join(parts)}", "success")
    else:
        await flash(result.get("message", "当前没有可领取的装备图鉴奖励"), "info")
    return redirect(url_for("player_bp.equipment_pokedex"))

@player_bp.route("/inventory")
@login_required
async def inventory():
    """背包页面"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    user_repo = current_app.config.get("USER_REPO")
    user = user_repo.get_by_id(user_id) if user_repo else None
    
    # 获取鱼竿、饰品、道具、鱼饵
    rods_result = inventory_service.get_user_rod_inventory(user_id)
    accessories_result = inventory_service.get_user_accessory_inventory(user_id)
    items_result = inventory_service.get_user_item_inventory(user_id)
    baits_result = inventory_service.get_user_bait_inventory(user_id)
    
    return await render_template("inventory.html",
                                  rods=rods_result.get("rods", []),
                                  accessories=accessories_result.get("accessories", []),
                                  items=items_result.get("items", []),
                                  baits=baits_result.get("baits", []),
                                  current_bait_id=user.current_bait_id if user else None)

@player_bp.route("/fishpond")
@login_required
async def fishpond():
    """鱼塘页面"""
    user_id = session.get("user_id")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    # 获取鱼塘信息
    pond_result = inventory_service.get_user_fish_pond(user_id)
    
    # 按稀有度分组
    fish_by_rarity = {}
    for fish in pond_result.get("fishes", []):
        rarity = fish["rarity"]
        if rarity not in fish_by_rarity:
            fish_by_rarity[rarity] = []
        fish_by_rarity[rarity].append(fish)

    pond_capacity_result = inventory_service.get_user_fish_pond_capacity(user_id)
    current_capacity = int(pond_capacity_result.get("fish_pond_capacity", 0) or 0)
    current_count = int(
        pond_capacity_result.get(
            "current_fish_count",
            pond_result.get("stats", {}).get("total_count", 0),
        )
        or 0
    )
    capacity_info = {
        "current_quantity": current_count,
        "current_capacity": current_capacity,
        "next_upgrade": _get_next_pond_upgrade(inventory_service, current_capacity),
    }

    return await render_template("fishpond.html",
                                  fish_by_rarity=fish_by_rarity,
                                  stats=pond_result.get("stats", {}),
                                  capacity_info=capacity_info)

@player_bp.route("/aquarium")
@login_required
async def aquarium():
    """水族箱页面"""
    user_id = session.get("user_id")
    aquarium_service = current_app.config.get("AQUARIUM_SERVICE")
    
    # 获取水族箱信息
    aquarium_result = aquarium_service.get_user_aquarium(user_id)
    
    # 按稀有度分组
    fish_by_rarity = {}
    for fish in aquarium_result.get("fishes", []):
        rarity = fish["rarity"]
        if rarity not in fish_by_rarity:
            fish_by_rarity[rarity] = []
        fish_by_rarity[rarity].append(fish)

    aquarium_upgrade_info = aquarium_service.get_aquarium_upgrade_info(user_id)
    aquarium_stats = aquarium_result.get("stats", {})
    capacity_info = {
        "current_quantity": int(aquarium_stats.get("total_count", 0) or 0),
        "current_capacity": int(
            aquarium_upgrade_info.get("current_capacity", aquarium_stats.get("capacity", 0))
            if aquarium_upgrade_info.get("success")
            else aquarium_stats.get("capacity", 0)
            or 0
        ),
        "next_upgrade": aquarium_upgrade_info.get("next_upgrade") if aquarium_upgrade_info.get("success") else None,
    }
    
    # 读取展览评论数据（如果用户是展览者）
    exhibition_comments = {}
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    exhibition_file = os.path.join(data_dir, "aquarium_exhibition.json")
    
    if os.path.exists(exhibition_file):
        with open(exhibition_file, "r", encoding="utf-8") as f:
            exhibition_data = json.load(f)
        
        # 如果当前用户是展览者，获取评论
        if exhibition_data.get("featured_user", {}).get("user_id") == user_id:
            exhibition_comments = exhibition_data.get("comments", {})
    
    return await render_template("aquarium.html",
                                  fish_by_rarity=fish_by_rarity,
                                  stats=aquarium_stats,
                                  capacity_info=capacity_info,
                                  exhibition_comments=exhibition_comments,
                                  current_user_id=user_id)

@player_bp.route("/market")
@login_required
async def market():
    """交易市场页面"""
    user_id = session.get("user_id")
    market_service = current_app.config.get("MARKET_SERVICE")
    inventory_service = current_app.config.get("INVENTORY_SERVICE")
    
    # 获取市场商品列表
    market_result = market_service.get_market_listings()
    
    # 获取用户的上架列表
    my_listings_result = market_service.get_user_listings(user_id)
    
    # 获取用户库存用于上架
    user_inventory = {
        "rod": [],
        "accessory": [],
        "fish": [],
        "item": []
    }
    
    # 获取鱼竿
    rods_result = inventory_service.get_user_rod_inventory(user_id)
    for rod in rods_result.get("rods", []):
        if not rod.get("is_equipped"):  # 只显示未装备的
            user_inventory["rod"].append({
                "instance_id": rod["instance_id"],
                "name": rod["name"],
                "rarity": rod["rarity"],
                "refine_level": rod.get("refine_level", 0),
                "display_code": rod.get("display_code", "")
            })
    
    # 获取饰品
    accessories_result = inventory_service.get_user_accessory_inventory(user_id)
    for accessory in accessories_result.get("accessories", []):
        if not accessory.get("is_equipped"):  # 只显示未装备的
            user_inventory["accessory"].append({
                "instance_id": accessory["instance_id"],
                "name": accessory["name"],
                "rarity": accessory["rarity"],
                "refine_level": accessory.get("refine_level", 0),
                "display_code": accessory.get("display_code", "")
            })
    
    # 获取鱼类（从鱼塘）
    pond_result = inventory_service.get_user_fish_pond(user_id)
    for fish in pond_result.get("fishes", []):
        user_inventory["fish"].append({
            "fish_id": fish["fish_id"],
            "name": fish["name"],
            "rarity": fish["rarity"],
            "quality_level": fish["quality_level"],
            "quantity": fish["quantity"]
        })
    
    # 获取道具
    items_result = inventory_service.get_user_item_inventory(user_id)
    for item in items_result.get("items", []):
        user_inventory["item"].append({
            "item_id": item["item_id"],
            "name": item["name"],
            "rarity": item["rarity"],
            "quantity": item["quantity"]
        })
    
    import json
    user_inventory_json = json.dumps(user_inventory)
    
    return await render_template("market.html",
                                  rods=market_result.get("rods", []),
                                  accessories=market_result.get("accessories", []),
                                  fish=market_result.get("fish", []),
                                  items=market_result.get("items", []),
                                  my_listings=my_listings_result.get("listings", []),
                                  user_inventory_json=user_inventory_json,
                                  user_id=user_id)

@player_bp.route("/shop")
@login_required
async def shop():
    """商店页面"""
    user_id = session.get("user_id")
    shop_service = current_app.config.get("SHOP_SERVICE")
    user_repo = current_app.config.get("USER_REPO")
    inventory_repo = current_app.config.get("INVENTORY_REPO")
    
    # 获取用户信息
    user = user_repo.get_by_id(user_id)
    
    # 获取用户库存用于检查购买条件
    user_inventory = {
        "coins": user.coins,
        "premium": user.premium_currency,
        "items": {},
        "fish": {},
        "rods": {},
        "accessories": {},
        "baits": {}
    }
    
    # 获取道具库存（inventory_repo返回的是字典 {item_id: quantity}）
    user_inventory["items"] = inventory_repo.get_user_item_inventory(user_id)
    
    # 获取鱼类库存（鱼塘 + 水族箱）
    for fish in inventory_repo.get_fish_inventory(user_id):
        key = (fish.fish_id, fish.quality_level)
        user_inventory["fish"][key] = user_inventory["fish"].get(key, 0) + fish.quantity
    
    from ..core.services.aquarium_service import AquariumService
    aquarium_service = current_app.config.get("AQUARIUM_SERVICE")
    if aquarium_service:
        aquarium_result = aquarium_service.get_user_aquarium(user_id)
        for fish in aquarium_result.get("fishes", []):
            key = (fish["fish_id"], fish["quality_level"])
            user_inventory["fish"][key] = user_inventory["fish"].get(key, 0) + fish["quantity"]
    
    # 获取鱼竿库存
    for rod in inventory_repo.get_user_rod_instances(user_id):
        user_inventory["rods"][rod.rod_id] = user_inventory["rods"].get(rod.rod_id, 0) + 1
    
    # 获取饰品库存
    for accessory in inventory_repo.get_user_accessory_instances(user_id):
        user_inventory["accessories"][accessory.accessory_id] = user_inventory["accessories"].get(accessory.accessory_id, 0) + 1
    
    # 获取鱼饵库存（inventory_repo返回的是字典 {bait_id: quantity}）
    user_inventory["baits"] = inventory_repo.get_user_bait_inventory(user_id)
    
    # 获取所有商店
    shops_result = shop_service.get_shops()
    shops_list = shops_result.get("shops", [])
    
    # 为每个商店获取详细信息
    shops_with_items = []
    for shop in shops_list:
        shop_details = shop_service.get_shop_details(shop["shop_id"])
        if shop_details.get("success"):
            # 为每个商品的成本检查是否满足
            for item_data in shop_details.get("items", []):
                item = item_data.get("item", {})
                for cost in item_data.get("costs", []):
                    cost_type = cost.get("cost_type")
                    cost_item_id = cost.get("cost_item_id")
                    cost_amount = cost.get("cost_amount", 0)
                    quality_level = cost.get("quality_level", 0)
                    
                    # 检查是否满足
                    satisfied = False
                    if cost_type == "coins":
                        satisfied = user_inventory["coins"] >= cost_amount
                    elif cost_type == "premium":
                        satisfied = user_inventory["premium"] >= cost_amount
                    elif cost_type == "item":
                        satisfied = user_inventory["items"].get(cost_item_id, 0) >= cost_amount
                    elif cost_type == "fish":
                        key = (cost_item_id, quality_level)
                        satisfied = user_inventory["fish"].get(key, 0) >= cost_amount
                    elif cost_type == "rod":
                        satisfied = user_inventory["rods"].get(cost_item_id, 0) >= cost_amount
                    elif cost_type == "accessory":
                        satisfied = user_inventory["accessories"].get(cost_item_id, 0) >= cost_amount
                    elif cost_type == "bait":
                        satisfied = user_inventory["baits"].get(cost_item_id, 0) >= cost_amount
                    
                    cost["satisfied"] = satisfied

                valid_costs = [
                    cost for cost in item_data.get("costs", [])
                    if cost.get("cost_type") and int(cost.get("cost_amount", 0) or 0) > 0
                ]
                cost_groups = {}
                for cost in valid_costs:
                    group_id = cost.get("group_id") or 0
                    cost_groups.setdefault(group_id, []).append(cost)

                can_pay = True
                for group_costs in cost_groups.values():
                    relation = str(group_costs[0].get("cost_relation", "and") or "and").lower()
                    if relation == "or" and len(group_costs) > 1:
                        group_ok = any(cost.get("satisfied") for cost in group_costs)
                    else:
                        group_ok = all(cost.get("satisfied") for cost in group_costs)
                    if not group_ok:
                        can_pay = False
                        break

                can_purchase = can_pay
                disabled_reason = "" if can_pay else "资源不足"
                stock_total = item.get("stock_total")
                if stock_total is not None and int(item.get("stock_sold", 0) or 0) >= int(stock_total or 0):
                    can_purchase = False
                    disabled_reason = "库存不足"

                per_user_limit = item.get("per_user_limit")
                if can_purchase and per_user_limit is not None:
                    purchased_total = shop_service.shop_repo.get_user_purchased_count(user_id, item.get("item_id"))
                    if purchased_total >= int(per_user_limit or 0):
                        can_purchase = False
                        disabled_reason = "已达限购"

                per_user_daily_limit = item.get("per_user_daily_limit")
                if can_purchase and per_user_daily_limit is not None and int(per_user_daily_limit or 0) > 0:
                    now_utc = datetime.now(timezone.utc)
                    now_local = now_utc.astimezone(timezone(timedelta(hours=8)))
                    local_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                    start_of_day_utc = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)
                    purchased_today = shop_service.shop_repo.get_user_purchased_count(
                        user_id,
                        item.get("item_id"),
                        since=start_of_day_utc,
                    )
                    if purchased_today >= int(per_user_daily_limit or 0):
                        can_purchase = False
                        disabled_reason = "今日已达限购"

                item_data["can_purchase"] = can_purchase
                item_data["purchase_disabled_reason"] = disabled_reason
            
            shops_with_items.append({
                "shop_id": shop["shop_id"],
                "name": shop["name"],
                "description": shop.get("description"),
                "item_list": shop_details.get("items", [])
            })
    
    return await render_template("shop.html", 
                                  user=user,
                                  shops=shops_with_items)

@player_bp.route("/exchange")
@login_required
async def exchange():
    """期货页面"""
    user_id = session.get("user_id")
    exchange_service = current_app.config.get("EXCHANGE_SERVICE")
    user_repo = current_app.config.get("USER_REPO")
    
    # 检查是否开通账户
    account_check = exchange_service.check_exchange_account(user_id)
    has_account = account_check.get("success", False)
    
    # 获取用户信息用于显示金币
    user = user_repo.get_by_id(user_id)
    
    if not has_account:
        return await render_template("exchange.html",
                                      has_account=False,
                                      user=user,
                                      market_status={"commodities": []},
                                      user_inventory={},
                                      user_costs={},
                                      user_inventory_lots={},
                                      capacity_info={},
                                      price_history={},
                                      history_data={},
                                      labels=[],
                                      price_changes={},
                                      auto_sell_message="")
    
    # 获取市场状态
    market_status = exchange_service.get_market_status()
    
    # 获取用户库存
    user_inventory_result = exchange_service.get_user_inventory(user_id)
    inventory_data = user_inventory_result.get("inventory", {})
    auto_sell_message = user_inventory_result.get("auto_sell_message", "")
    
    # 构建用户库存字典和成本字典
    user_inventory = {}
    user_costs = {}
    user_inventory_lots = {}
    now = datetime.now()
    for commodity_id, data in inventory_data.items():
        user_inventory[commodity_id] = data.get("total_quantity", 0)
        user_costs[commodity_id] = data.get("total_cost", 0)
        lots = []
        for item in data.get("items", []):
            expires_at = item.get("expires_at")
            expires_at_text = "未知"
            time_left_text = "未知"
            is_expired = False
            is_expiring_soon = False
            if isinstance(expires_at, datetime):
                expires_at_text = expires_at.strftime("%Y-%m-%d %H:%M")
                seconds_left = int((expires_at - now).total_seconds())
                if seconds_left <= 0:
                    is_expired = True
                    time_left_text = "已到期"
                elif seconds_left < 86400:
                    hours = max(1, seconds_left // 3600)
                    is_expiring_soon = True
                    time_left_text = f"剩 {hours} 小时"
                else:
                    days = seconds_left // 86400
                    hours = (seconds_left % 86400) // 3600
                    if hours > 0:
                        time_left_text = f"剩 {days} 天 {hours} 小时"
                    else:
                        time_left_text = f"剩 {days} 天"
            lots.append({
                "instance_id": item.get("instance_id"),
                "quantity": item.get("quantity", 0),
                "purchase_price": item.get("purchase_price", 0),
                "expires_at_text": expires_at_text,
                "time_left_text": time_left_text,
                "is_expired": is_expired,
                "is_expiring_soon": is_expiring_soon,
            })
        user_inventory_lots[commodity_id] = lots
    
    # 获取价格历史
    price_history_result = exchange_service.get_price_history(days=7)
    raw_history_data = price_history_result.get("history", {}) if price_history_result.get("success", False) else {}
    labels = price_history_result.get("labels", []) if price_history_result.get("success", False) else []
    current_prices = market_status.get("prices", {})

    if not labels:
        labels = [market_status.get("date") or datetime.now().strftime("%Y-%m-%d")]

    history_data = {}
    for commodity_id in market_status.get("commodities", {}).keys():
        raw_series = list(raw_history_data.get(commodity_id, []) or [])
        if not raw_series:
            raw_series = [current_prices.get(commodity_id, 0)]

        while len(raw_series) < len(labels):
            raw_series.append(raw_series[-1] if raw_series else current_prices.get(commodity_id, 0))

        normalized_series = []
        last_known = None
        for value in raw_series[:len(labels)]:
            if value is None:
                value = last_known if last_known is not None else current_prices.get(commodity_id, 0)
            if value is not None:
                last_known = value
            normalized_series.append(value)
        history_data[commodity_id] = normalized_series

    price_changes = {}
    for commodity_id, current_price in current_prices.items():
        series = [price for price in history_data.get(commodity_id, []) if price is not None]
        previous_price = series[-2] if len(series) >= 2 else (series[-1] if series else current_price)
        change_amount = int(current_price or 0) - int(previous_price or 0)
        change_percent = (change_amount / previous_price * 100) if previous_price else 0
        price_changes[commodity_id] = {
            "previous_price": int(previous_price or 0),
            "amount": change_amount,
            "percent": change_percent,
            "direction": "up" if change_amount > 0 else ("down" if change_amount < 0 else "flat"),
            "has_previous": bool(previous_price),
        }
    capacity_info = exchange_service.get_exchange_capacity_info(user_id)
    
    # 转换数据结构：从 {commodity_id: [prices]} 转换为 {date: {commodity_id: price}}
    price_history = {}
    for i, date in enumerate(labels):
        price_history[date] = {}
        for commodity_id, prices in history_data.items():
            if i < len(prices):
                price_history[date][commodity_id] = prices[i]
    
    return await render_template("exchange.html",
                                  has_account=True,
                                  user=user,
                                  market_status=market_status,
                                  user_inventory=user_inventory,
                                  user_costs=user_costs,
                                  user_inventory_lots=user_inventory_lots,
                                  capacity_info=capacity_info,
                                  price_history=price_history,
                                  history_data=history_data,
                                  labels=labels,
                                  price_changes=price_changes,
                                  auto_sell_message=auto_sell_message)

@player_bp.route("/gacha")
@login_required
async def gacha():
    """抽卡页面"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    gacha_service = current_app.config.get("GACHA_SERVICE")
    
    user = user_repo.get_by_id(user_id)
    if not user:
        await flash("用户数据异常", "danger")
        return redirect(url_for("player_bp.logout"))
    
    # 获取所有卡池
    pools_result = gacha_service.get_all_pools()
    all_pools_raw = pools_result.get("pools", [])
    
    # 将卡池对象转换为字典并添加额外信息
    all_pools = []
    for pool in all_pools_raw:
        # 如果是字典直接用，否则转换为字典
        if isinstance(pool, dict):
            pool_dict = pool.copy()
        else:
            pool_dict = {
                "gacha_pool_id": pool.gacha_pool_id,
                "name": pool.name,
                "description": pool.description,
                "cost_coins": pool.cost_coins,
                "cost_premium_currency": pool.cost_premium_currency,
                "is_limited_time": bool(pool.is_limited_time),
                "open_until": pool.open_until
            }
        
        all_pools.append(pool_dict)
    
    return await render_template("gacha.html",
                                  user=user,
                                  pools=all_pools)

@player_bp.route("/tavern")
@login_required
async def tavern():
    """酒馆页面"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    aquarium_service = current_app.config.get("AQUARIUM_SERVICE")
    inventory_repo = current_app.config.get("INVENTORY_REPO")
    item_template_repo = current_app.config.get("ITEM_TEMPLATE_REPO")
    expedition_service = current_app.config.get("EXPEDITION_SERVICE")
    
    user = user_repo.get_by_id(user_id)
    if not user:
        await flash("用户数据异常", "danger")
        return redirect(url_for("player_bp.logout"))
    
    tavern_admin_user_id = _get_tavern_admin_user_id()
    is_admin = user_id == tavern_admin_user_id
    
    # 获取留言数据文件路径
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    messages_file = os.path.join(data_dir, "tavern_messages.json")
    exhibition_file = os.path.join(data_dir, "aquarium_exhibition.json")
    
    # 读取留言数据
    if os.path.exists(messages_file):
        with open(messages_file, "r", encoding="utf-8") as f:
            tavern_data = json.load(f)
    else:
        tavern_data = {"announcement": "", "messages": []}
    
    # 分页
    page = request.args.get("page", 1, type=int)
    per_page = 20
    messages = tavern_data.get("messages", [])
    total_messages = len(messages)
    total_pages = (total_messages + per_page - 1) // per_page
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_messages = messages[start_idx:end_idx]
    
    # 获取排行榜数据
    leaderboard = _get_leaderboard_data(user_repo, item_template_repo)
    
    # 获取今日展览数据
    exhibition_data = _get_or_create_daily_exhibition(
        exhibition_file, user_repo, aquarium_service, 
        inventory_repo, item_template_repo
    )
    
    # 获取进行中的科考
    active_expeditions = []
    if expedition_service:
        try:
            active_expeditions = expedition_service.get_all_active_expeditions(user_id)
            logger.info(f"成功获取科考数据，共{len(active_expeditions)}个进行中的科考")
            if active_expeditions:
                logger.info(f"科考数据示例: {active_expeditions[0]}")
        except Exception as e:
            logger.error(f"获取科考数据失败: {e}", exc_info=True)
    else:
        logger.warning("expedition_service未初始化")
    
    return await render_template("tavern.html",
                                  user=user,
                                  announcement=tavern_data.get("announcement", ""),
                                  messages=page_messages,
                                  is_admin=is_admin,
                                  tavern_admin_user_id=tavern_admin_user_id,
                                  current_user_id=user_id,
                                  page=page,
                                  total_pages=total_pages,
                                  leaderboard=leaderboard,
                                  exhibition=exhibition_data,
                                  expeditions=active_expeditions)

@player_bp.route("/fishing")
@login_required
async def fishing():
    """钓鱼区域管理页面"""
    user_id = session.get("user_id")
    user_repo = current_app.config.get("USER_REPO")
    fishing_service = current_app.config.get("FISHING_SERVICE")
    inventory_repo = current_app.config.get("INVENTORY_REPO")
    item_template_repo = current_app.config.get("ITEM_TEMPLATE_REPO")
    
    user = user_repo.get_by_id(user_id)
    if not user:
        await flash("用户数据异常", "danger")
        return redirect(url_for("player_bp.logout"))
    
    # 从数据库获取所有钓鱼区域
    fishing_zones = inventory_repo.get_all_zones()
    
    # 获取用户当前区域
    current_zone_id = user.fishing_zone_id
    current_zone = None
    
    # 构建所有区域列表
    all_zones = []
    for zone in fishing_zones:
        # 获取通行证道具名称
        required_pass_name = None
        if zone.requires_pass and zone.required_item_id:
            item_template = item_template_repo.get_item_by_id(zone.required_item_id)
            required_pass_name = item_template.name if item_template else f"道具ID{zone.required_item_id}"
        
        zone_info = {
            "id": zone.id,
            "name": zone.name,
            "description": zone.description,
            "required_pass": required_pass_name,
            "is_current": zone.id == current_zone_id,
            "is_active": zone.is_active,
            "fishing_cost": zone.fishing_cost,
        }
        
        all_zones.append(zone_info)
        
        # 设置当前区域信息
        if zone.id == current_zone_id:
            current_zone = zone_info
    
    # 按ID排序
    all_zones.sort(key=lambda z: z["id"])
    
    return await render_template("fishing_zones.html",
                                  current_zone=current_zone,
                                  all_zones=all_zones)
