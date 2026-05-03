"""
Unity API aggregation layer.

This module owns Unity-facing route registration so Unity integration does not
need to chase endpoints across player/server.py and manager/user_api.py.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from quart import Blueprint, current_app, jsonify, request


DEFAULT_PUBLIC_BASE_URL = "https://fish.eyeoftheuniverse.top"
UNITY_API_PREFIX = "/api/unity"
INTERNAL_USER_API_PREFIX = "/api/user"


unity_api_bp = Blueprint(
    "unity_api",
    __name__,
    url_prefix=UNITY_API_PREFIX,
)


def normalize_public_base_url(value: str | None) -> str:
    base_url = str(value or DEFAULT_PUBLIC_BASE_URL).strip()
    return base_url.rstrip("/") or DEFAULT_PUBLIC_BASE_URL


def build_public_url(path: str) -> str:
    public_base_url = normalize_public_base_url(current_app.config.get("PUBLIC_BASE_URL"))
    normalized_path = "/" + str(path or "").lstrip("/")
    return f"{public_base_url}{normalized_path}"


def _get_unity_allowed_origins():
    configured = current_app.config.get("UNITY_ALLOWED_ORIGINS", [])
    if isinstance(configured, str):
        configured = [configured]

    origins = {
        normalize_public_base_url(current_app.config.get("PUBLIC_BASE_URL")),
        DEFAULT_PUBLIC_BASE_URL,
        "http://localhost:8888",
        "http://127.0.0.1:8888",
    }
    origins.update(str(origin).rstrip("/") for origin in configured if str(origin).strip())
    return origins


def install_unity_cors(app):
    @app.after_request
    async def add_unity_cors_headers(response):
        origin = request.headers.get("Origin", "").rstrip("/")
        allowed_origins = _get_unity_allowed_origins()

        if origin and origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Credentials"] = "true"

        response.headers.setdefault(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Requested-With",
        )
        response.headers.setdefault(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, DELETE, OPTIONS",
        )
        return response


@unity_api_bp.route("/config", methods=["GET"])
async def unity_config():
    """Return public URLs Unity should use for the current deployment."""
    device_code = str(request.args.get("device_code", "") or "").strip()
    if not device_code:
        device_code = secrets.token_urlsafe(16)

    login_query = urlencode({"flow": "game", "device_code": device_code})
    return jsonify({
        "success": True,
        "data": {
            "public_base_url": normalize_public_base_url(current_app.config.get("PUBLIC_BASE_URL")),
            "api_base_url": build_public_url(UNITY_API_PREFIX),
            "oauth": {
                "device_code": device_code,
                "login_url": build_public_url(f"/player/login/linuxdo?{login_query}"),
                "status_url": build_public_url(f"{UNITY_API_PREFIX}/oauth/linuxdo/status"),
                "consume_url": build_public_url(f"{UNITY_API_PREFIX}/oauth/linuxdo/consume"),
            },
        },
    })


@unity_api_bp.route("/login", methods=["POST"])
async def unity_login():
    """Unity credential login endpoint."""
    from . import user_api

    return await user_api.api_login()


@unity_api_bp.route("/logout", methods=["POST"])
async def unity_logout():
    """Unity logout endpoint."""
    from . import user_api

    return await user_api.api_logout()


@unity_api_bp.route("/info", methods=["GET"])
async def unity_user_info():
    """Unity current-user info endpoint."""
    from . import user_api

    return await user_api.get_user_info()


@unity_api_bp.route("/oauth/linuxdo/start", methods=["GET", "POST"])
async def unity_linuxdo_oauth_start():
    """Create a Unity OAuth request and return URLs the client can use."""
    from ..player import server as player_server

    payload = await request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}

    form = await request.form
    device_code = (
        payload.get("device_code")
        or form.get("device_code")
        or request.args.get("device_code")
        or secrets.token_urlsafe(16)
    )
    device_code = player_server._register_pending_unity_linuxdo_oauth(device_code)
    if not device_code:
        return jsonify({"success": False, "message": "缺少有效的设备标识"}), 400

    login_query = urlencode({"flow": "game", "device_code": device_code})
    return jsonify({
        "success": True,
        "message": "Unity OAuth 请求已创建",
        "data": {
            "device_code": device_code,
            "login_url": build_public_url(f"/player/login/linuxdo?{login_query}"),
            "status_url": build_public_url(f"{UNITY_API_PREFIX}/oauth/linuxdo/status"),
            "consume_url": build_public_url(f"{UNITY_API_PREFIX}/oauth/linuxdo/consume"),
        },
    })


@unity_api_bp.route("/oauth/linuxdo/status", methods=["GET"])
async def unity_linuxdo_oauth_status():
    """Unity namespace wrapper for Linux.do OAuth polling."""
    from ..player import server as player_server

    return await player_server.linuxdo_oauth_status()


@unity_api_bp.route("/oauth/linuxdo/consume", methods=["POST"])
async def unity_linuxdo_oauth_consume():
    """Unity namespace wrapper for consuming one-time OAuth login tickets."""
    from ..player import server as player_server

    return await player_server.consume_linuxdo_oauth_for_unity()


def register_unity_user_api_routes(app):
    """
    Register Unity-facing user API routes under /api/unity.

    The handler implementation is shared with manager/user_api.py, but Unity
    only receives the dedicated /api/unity namespace.
    """
    existing_rules = list(app.url_map.iter_rules())
    existing_paths = {rule.rule for rule in existing_rules}

    for rule in existing_rules:
        if not rule.rule.startswith(f"{INTERNAL_USER_API_PREFIX}/"):
            continue
        if rule.endpoint == "static":
            continue

        unity_rule = UNITY_API_PREFIX + rule.rule[len(INTERNAL_USER_API_PREFIX):]
        if unity_rule in existing_paths:
            continue

        view_func = app.view_functions.get(rule.endpoint)
        if view_func is None:
            continue

        methods = sorted(
            method for method in rule.methods
            if method not in {"HEAD", "OPTIONS"}
        )
        app.add_url_rule(
            unity_rule,
            endpoint=f"unity_api_routes_{rule.endpoint.replace('.', '_')}",
            view_func=view_func,
            methods=methods,
            defaults=rule.defaults,
        )
        existing_paths.add(unity_rule)
