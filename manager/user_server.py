import functools
import os
import traceback
import hashlib
import json
from typing import Dict, Any
from datetime import datetime, timedelta
import asyncio

from quart import (
    Quart, render_template, request, redirect, url_for, session, flash,
    Blueprint, current_app, jsonify
)
from astrbot.api import logger
from ..core.utils import get_current_daily_marker

# 导入用户API蓝图
from .user_api import user_api_bp


user_bp = Blueprint(
    "user_bp",
    __name__,
    template_folder="templates",
    static_folder="static",
)


def create_user_app(services: Dict[str, Any]):
    """
    创建用户WebUI应用。
    """
    app = Quart(__name__)
    app.secret_key = os.urandom(24)

    # 将所有服务实例存入app的配置中
    logger.info(f"[WebUI] 初始化用户WebUI，传入的服务: {list(services.keys())}")
    for service_name, service_instance in services.items():
        app.config[service_name.upper()] = service_instance
        logger.info(f"[WebUI] 已配置 {service_name.upper()}: {type(service_instance).__name__}")

    app.register_blueprint(user_bp, url_prefix="")
    app.register_blueprint(user_api_bp)  # 注册API蓝图

    @app.route("/")
    def root():
        return redirect(url_for("user_bp.login"))
    
    @app.route("/favicon.ico")
    def favicon():
        from quart import abort
        abort(404)
    
    @app.errorhandler(404)
    async def handle_404_error(error):
        logger.error(f"404 Not Found: {request.url}")
        return "Not Found", 404
    
    @app.errorhandler(500)
    async def handle_500_error(error):
        logger.error(f"Internal Server Error: {error}")
        logger.error(traceback.format_exc())
        return "Internal Server Error", 500
    
    return app


def login_required(f):
    """装饰器：需要登录"""
    @functools.wraps(f)
    async def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("user_bp.login"))
        return await f(*args, **kwargs)
    return decorated_function


def user_context(f):
    """装饰器：为视图函数注入用户信息"""
    @functools.wraps(f)
    @login_required
    async def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        try:
            user_repo = current_app.config["USER_REPO"]
            logger.info(f"[WebUI] user_context获取USER_REPO: {type(user_repo).__name__}")
        except KeyError as e:
            logger.error(f"[WebUI] 配置错误: USER_REPO未找到 - {e}")
            await flash("系统配置错误，请联系管理员", "danger")
            return redirect(url_for("user_bp.login"))
        
        try:
            user = user_repo.get_by_id(user_id)
            logger.info(f"[WebUI] 查询用户成功: {user is not None}")
        except Exception as e:
            logger.error(f"[WebUI] 查询用户失败: {e}")
            await flash(f"数据库查询失败: {str(e)}", "danger")
            return redirect(url_for("user_bp.login"))
        
        if not user:
            await flash("用户不存在", "danger")
            return redirect(url_for("user_bp.login"))
        
        # 注入到kwargs中
        kwargs['user'] = user
        return await f(*args, **kwargs)
    return decorated_function


def hash_password(password: str) -> str:
    """哈希密码"""
    return hashlib.sha256(password.encode()).hexdigest()


@user_bp.route("/login", methods=["GET", "POST"])
async def login():
    """用户登录页面"""
    if request.method == "POST":
        form = await request.form
        qq = form.get("qq", "").strip()
        secret_key = form.get("secret_key", "").strip()
        
        logger.info(f"[WebUI] 用户尝试登录: QQ={qq}")
        
        # 验证QQ号格式
        if not qq or not qq.isdigit() or len(qq) < 5 or len(qq) > 11:
            await flash("❌ 请输入有效的QQ号（5-11位数字）", "danger")
            return await render_template("user_login.html")
        
        try:
            user_repo = current_app.config["USER_REPO"]
            logger.info(f"[WebUI] 已获取USER_REPO: {type(user_repo).__name__}")
            user = user_repo.get_by_id(qq)
            logger.info(f"[WebUI] 数据库查询结果: user={user is not None}")
        except KeyError as e:
            logger.error(f"[WebUI] 配置错误: {e}")
            await flash("❌ 系统配置错误，请稍后重试", "danger")
            return await render_template("user_login.html")
        except Exception as e:
            logger.error(f"[WebUI] 数据库查询失败: {e}")
            await flash(f"❌ 数据库查询失败: {e}", "danger")
            return await render_template("user_login.html")
        
        # 检查user_id是否存在于数据库
        if not user:
            # 用户不存在于数据库
            await flash(
                "❌ 该QQ号未注册。请先在游戏中注册账户。\n"
                "在QQ群中使用 /注册 命令来创建账户。",
                "danger"
            )
            return await render_template("user_login.html")
        
        # 检查该用户是否已设置密钥
        stored_hash = get_user_secret_hash(qq)
        
        if not stored_hash:
            # 首次登录：需要设置密钥
            if not secret_key:
                session["temp_qq"] = qq
                await flash("✅ 该账户首次登录，请设置密钥", "info")
                return redirect(url_for("user_bp.setup_key"))
            else:
                # 用户直接提供了密钥，帮他保存
                if len(secret_key) < 8:
                    await flash("❌ 密钥长度至少8个字符", "danger")
                    return await render_template("user_login.html")
                
                try:
                    hashed_key = hash_password(secret_key)
                    save_user_secret_hash(qq, hashed_key)
                    
                    session["user_id"] = qq
                    session["secret_hash"] = hashed_key
                    session["logged_in"] = True
                    
                    await flash("✅ 欢迎！密钥已设置，你已成功登录", "success")
                    return redirect(url_for("user_bp.dashboard"))
                except Exception as e:
                    logger.error(f"设置密钥失败: {e}")
                    await flash("❌ 设置密钥失败，请重试", "danger")
                    return await render_template("user_login.html")
        else:
            # 已有密钥：验证密钥
            if not secret_key:
                await flash("❌ 请输入密钥", "danger")
                return await render_template("user_login.html")
            
            try:
                input_hash = hash_password(secret_key)
                
                if stored_hash == input_hash:
                    session["user_id"] = qq
                    session["secret_hash"] = input_hash
                    session["logged_in"] = True
                    await flash("✅ 登录成功！", "success")
                    return redirect(url_for("user_bp.dashboard"))
                else:
                    await flash("❌ 密钥错误，请检查后重试", "danger")
                    return await render_template("user_login.html")
            except Exception as e:
                logger.error(f"登录失败: {e}")
                await flash("❌ 登录失败，请重试", "danger")
                return await render_template("user_login.html")
    
    return await render_template("user_login.html")


@user_bp.route("/setup_key", methods=["GET", "POST"])
async def setup_key():
    """首次登录设置密钥"""
    qq = session.get("temp_qq")
    
    if not qq:
        return redirect(url_for("user_bp.login"))
    
    user_repo = current_app.config["USER_REPO"]
    user = user_repo.get_by_id(qq)
    
    # 验证qq是否真的存在于数据库
    if not user:
        await flash(
            "❌ 该QQ号不存在于系统中。请先在游戏中使用 /注册 命令注册账户。",
            "danger"
        )
        session.pop("temp_qq", None)
        return redirect(url_for("user_bp.login"))
    
    if request.method == "POST":
        form = await request.form
        secret_key = form.get("secret_key", "").strip()
        secret_key_confirm = form.get("secret_key_confirm", "").strip()
        
        # 验证密钥
        if not secret_key or len(secret_key) < 8:
            await flash("❌ 密钥长度至少8个字符", "danger")
            return await render_template("user_setup_key.html", qq=qq)
        
        if secret_key != secret_key_confirm:
            await flash("❌ 两次输入的密钥不一致", "danger")
            return await render_template("user_setup_key.html", qq=qq)
        
        try:
            # 检查密钥是否已设置
            if get_user_secret_hash(qq):
                await flash("❌ 该账户已设置过密钥，请直接登录", "danger")
                session.pop("temp_qq", None)
                return redirect(url_for("user_bp.login"))
            
            # 保存密钥哈希
            hashed_key = hash_password(secret_key)
            save_user_secret_hash(qq, hashed_key)
            
            session["user_id"] = qq
            session["secret_hash"] = hashed_key
            session["logged_in"] = True
            session.pop("temp_qq", None)
            
            await flash("✅ 密钥设置成功！欢迎来到钓鱼游戏", "success")
            return redirect(url_for("user_bp.dashboard"))
        except Exception as e:
            logger.error(f"设置密钥失败: {e}")
            await flash(f"❌ 设置密钥失败：{str(e)}", "danger")
            return await render_template("user_setup_key.html", qq=qq)
    
    return await render_template("user_setup_key.html", qq=qq)


@user_bp.route("/logout")
async def logout():
    """登出"""
    session.clear()
    await flash("你已成功登出", "info")
    return redirect(url_for("user_bp.login"))


@user_bp.route("/dashboard")
@user_context
async def dashboard(user):
    """用户仪表板"""
    inventory_repo = current_app.config["INVENTORY_REPO"]
    
    # 获取鱼塘中的鱼数量
    pond_fish_count = len(inventory_repo.get_fish_inventory(user.user_id))
    
    # 获取当前称号
    current_title = "未设置"
    if user.current_title_id:
        item_template_service = current_app.config["ITEM_TEMPLATE_SERVICE"]
        title = item_template_service.get_title_by_id(user.current_title_id)
        if title:
            current_title = title.name
    
    # 检查当前刷新周期是否签到
    log_repo = current_app.config["LOG_REPO"]
    game_config = current_app.config.get("FISHING_SERVICE").config if current_app.config.get("FISHING_SERVICE") else {}
    reset_hour = int(game_config.get("daily_reset_hour", 0) or 0)
    today_signed = log_repo.has_checked_in(user.user_id, get_current_daily_marker(reset_hour))
    
    return await render_template(
        "user_dashboard.html",
        user=user,
        pond_fish_count=pond_fish_count,
        current_title=current_title,
        today_signed=today_signed
    )


@user_bp.route("/profile")
@user_context
async def profile(user):
    """用户个人资料页面"""
    return await render_template("user_profile.html", user=user)


@user_bp.route("/settings")
@user_context
async def settings(user):
    """用户设置页面"""
    return await render_template("user_settings.html", user=user)


@user_bp.route("/backpack")
@user_context
async def backpack(user):
    """背包页面"""
    return await render_template("user_backpack.html", user=user)


@user_bp.route("/pokedex")
@user_context
async def pokedex(user):
    """鱼类图鉴页面"""
    return await render_template("user_pokedex.html", user=user)


@user_bp.route("/fishing")
@user_context
async def fishing(user):
    """钓鱼页面"""
    return await render_template("user_fishing.html", user=user)


@user_bp.route("/market")
@user_context
async def market(user):
    """市场页面"""
    return await render_template("user_market.html", user=user)


@user_bp.route("/shop")
@user_context
async def shop(user):
    """商店页面"""
    return await render_template("user_shop.html", user=user)


@user_bp.route("/gacha")
@user_context
async def gacha(user):
    """抽卡页面"""
    return await render_template("user_gacha.html", user=user)


@user_bp.route("/leaderboard")
@user_context
async def leaderboard(user):
    """排行榜页面"""
    return await render_template("user_leaderboard.html", user=user)


@user_bp.route("/exchange")
@user_context
async def exchange(user):
    """交易所页面"""
    return await render_template("user_exchange.html", user=user)


@user_bp.route("/sign_in", methods=["GET", "POST"])
@user_context
async def sign_in(user):
    """签到"""
    user_service = current_app.config["USER_SERVICE"]
    result = user_service.daily_sign_in(user.user_id)
    await flash(result.get("message", "签到失败"), "success" if result.get("success") else "warning")
    return redirect(url_for("user_bp.dashboard"))


# 密钥存储方案（使用JSON文件持久化 + 内存缓存）
_user_secrets = {}
_secrets_file_path = None

def _get_secrets_file_path() -> str:
    """获取密钥文件路径"""
    global _secrets_file_path
    if _secrets_file_path is None:
        # 在数据目录下创建user_secrets.json
        from pathlib import Path
        data_dir = Path(__file__).parent.parent.parent / "data" / "astrbot_plugin_fishing"
        data_dir.mkdir(parents=True, exist_ok=True)
        _secrets_file_path = str(data_dir / "user_secrets.json")
    return _secrets_file_path


def _load_secrets_from_file():
    """从文件加载密钥"""
    global _user_secrets
    file_path = _get_secrets_file_path()
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                _user_secrets = json.load(f)
                logger.info(f"已加载 {len(_user_secrets)} 个用户密钥")
        except Exception as e:
            logger.error(f"加载密钥文件失败: {e}")
            _user_secrets = {}
    else:
        _user_secrets = {}


def _save_secrets_to_file():
    """将密钥保存到文件"""
    file_path = _get_secrets_file_path()
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(_user_secrets, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存密钥文件失败: {e}")


def save_user_secret_hash(qq: str, hash_value: str):
    """
    保存用户密钥哈希（持久化到文件）
    
    Args:
        qq: 用户QQ号
        hash_value: 密钥的SHA256哈希值
    """
    global _user_secrets
    _user_secrets[qq] = hash_value
    _save_secrets_to_file()
    logger.info(f"已保存用户 {qq} 的密钥哈希")


def get_user_secret_hash(qq: str) -> str:
    """
    获取用户密钥哈希
    
    Args:
        qq: 用户QQ号
        
    Returns:
        密钥哈希值，如果不存在返回None
    """
    global _user_secrets
    return _user_secrets.get(qq)


def delete_user_secret_hash(qq: str):
    """
    删除用户密钥哈希（管理员操作）
    
    Args:
        qq: 用户QQ号
    """
    global _user_secrets
    if qq in _user_secrets:
        del _user_secrets[qq]
        _save_secrets_to_file()
        logger.info(f"已删除用户 {qq} 的密钥哈希")


# 初始化时加载密钥
_load_secrets_from_file()
