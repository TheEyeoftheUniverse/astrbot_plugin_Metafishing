import os
import asyncio

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.permission import PermissionType

# ==========================================================
# 导入所有仓储层 & 服务层（与旧版保持一致的精确导入）
# ==========================================================
from .core.repositories.sqlite_user_repo import SqliteUserRepository
from .core.repositories.sqlite_item_template_repo import SqliteItemTemplateRepository
from .core.repositories.sqlite_inventory_repo import SqliteInventoryRepository
from .core.repositories.sqlite_gacha_repo import SqliteGachaRepository
from .core.repositories.sqlite_market_repo import SqliteMarketRepository
from .core.repositories.sqlite_shop_repo import SqliteShopRepository
from .core.repositories.sqlite_log_repo import SqliteLogRepository
from .core.repositories.sqlite_achievement_repo import SqliteAchievementRepository
from .core.repositories.sqlite_user_buff_repo import SqliteUserBuffRepository
from .core.repositories.sqlite_exchange_repo import SqliteExchangeRepository # 新增交易所Repo

from .core.services.data_setup_service import DataSetupService
from .core.services.item_template_service import ItemTemplateService
from .core.services.user_service import UserService
from .core.services.fishing_service import FishingService
from .core.services.inventory_service import InventoryService
from .core.services.shop_service import ShopService
from .core.services.market_service import MarketService
from .core.services.gacha_service import GachaService
from .core.services.achievement_service import AchievementService
from .core.services.game_mechanics_service import GameMechanicsService
from .core.services.effect_manager import EffectManager
from .core.services.fishing_zone_service import FishingZoneService
from .core.services.exchange_service import ExchangeService # 新增交易所Service

from .core.database.migration import run_migrations
from .utils import _is_port_available, kill_processes_on_port

# ==========================================================
# 导入所有指令函数
# ==========================================================
from .handlers import (
    admin_handlers, 
    common_handlers, 
    inventory_handlers, 
    fishing_handlers, 
    market_handlers, 
    social_handlers, 
    gacha_handlers, 
    aquarium_handlers, 
)
from .handlers.fishing_handlers import FishingHandlers
from .handlers.exchange_handlers import ExchangeHandlers


class FishingPlugin(Star):
    _shared_player_webui_task = None
    _shared_player_webui_shutdown_event = None
    _shared_player_webui_owner = None

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)

        # --- 1. 加载配置 ---
        # 从新的嵌套结构中读取配置
        tax_config = config.get("tax", {})
        self.is_tax = tax_config.get("is_tax", True)  # 是否开启税收
        self.threshold = tax_config.get("threshold", 100000)  # 起征点
        self.step_coins = tax_config.get("step_coins", 100000)
        self.step_rate = tax_config.get("step_rate", 0.01)
        self.max_rate = tax_config.get("max_rate", 0.2)  # 最大税率
        self.min_rate = tax_config.get("min_rate", 0.001)  # 最小税率
        self.area2num = config.get("area2num", 2000)
        self.area3num = config.get("area3num", 500)
        
        # 插件ID，与 metadata.yaml 的 name 保持一致
        self.plugin_id = "astrbot_plugin_metafishing"

        # --- 1.1. 数据与临时文件路径管理 ---
        try:
            # 优先使用框架提供的 get_data_dir 方法
            self.data_dir = self.context.get_data_dir(self.plugin_id)
        except (AttributeError, TypeError):
            # 如果方法不存在或调用失败，则回退到旧的硬编码路径
            logger.warning(f"无法使用 self.context.get_data_dir('{self.plugin_id}'), 将回退到旧的 'data/' 目录。")
            self.data_dir = "data"
        
        self.tmp_dir = os.path.join(self.data_dir, "tmp")
        os.makedirs(self.tmp_dir, exist_ok=True)

        db_path = os.path.join(self.data_dir, "fish.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        # --- 1.2. 配置数据完整性检查注释 ---
        # 以下配置项必须在此处从 AstrBotConfig 中提取并放入 game_config，
        # 以确保所有服务在接收 game_config 时能够正确读取配置值
        # 
        # 配置数据流：_conf_schema.json → AstrBotConfig (config) → game_config → 各个服务
        # 
        # 从框架读取嵌套配置
        # 注意：框架会自动解析 _conf_schema.json 中的嵌套对象
        fishing_config = config.get("fishing", {})
        steal_config = config.get("steal", {})
        electric_fish_config = config.get("electric_fish", {})
        game_global_config = config.get("game", {})
        user_config = config.get("user", {})
        market_config = config.get("market", {})
        sell_prices_config = config.get("sell_prices", {})
        armed_state_config = config.get("armed_state", {}) or {}
        gacha_guarantee_config = config.get("gacha_guarantee", {}) or {}
        
        # 直接从框架获取 exchange 配置（不重建）
        exchange_config = config.get("exchange", {})
        if not exchange_config:
            # 如果框架返回空字典，说明嵌套配置不被支持，手动构建默认值
            logger.warning("[CONFIG] Exchange config is empty, using defaults")
            exchange_config = {
                "account_fee": 0,
                "capacity": 1000,
                "upgrades": [
                    {"from": 1000, "to": 2000, "cost": 100000},
                    {"from": 2000, "to": 5000, "cost": 500000},
                    {"from": 5000, "to": 10000, "cost": 2000000},
                    {"from": 10000, "to": 20000, "cost": 10000000},
                ],
                "tax_rate": 0.05,
                "volatility": {"dried_fish": 0.08, "fish_roe": 0.12, "fish_oil": 0.10},
                "event_chance": 0.1,
                "max_change_rate": 0.2,
                "min_price": 1,
                "max_price": 1000000,
                "sentiment_weights": {"panic": 0.1, "pessimistic": 0.2, "neutral": 0.4, "optimistic": 0.2, "euphoric": 0.1},
                "merge_window_minutes": 30,
            }
        else:
            logger.info(f"[CONFIG] Exchange capacity loaded: {exchange_config.get('capacity', 'NOT SET')}")

        exchange_config.setdefault("capacity", 1000)
        exchange_config.setdefault(
            "upgrades",
            [
                {"from": exchange_config["capacity"], "to": exchange_config["capacity"] * 2, "cost": 100000},
                {"from": exchange_config["capacity"] * 2, "to": exchange_config["capacity"] * 5, "cost": 500000},
                {"from": exchange_config["capacity"] * 5, "to": exchange_config["capacity"] * 10, "cost": 2000000},
                {"from": exchange_config["capacity"] * 10, "to": exchange_config["capacity"] * 20, "cost": 10000000},
            ],
        )
        
        self.game_config = {
            "fishing": {
                "cost": config.get("fish_cost", 10),
                "cooldown_seconds": fishing_config.get("cooldown_seconds", 180),
                "auto_bucket_count": fishing_config.get("auto_bucket_count", 4)
            },
            "quality_bonus_max_chance": fishing_config.get("quality_bonus_max_chance", 0.35),
            "steal": {
                "cooldown_seconds": steal_config.get("cooldown_seconds", 14400)
            },
            "electric_fish": {
                "enabled": electric_fish_config.get("enabled", True),
                "cooldown_seconds": electric_fish_config.get("cooldown_seconds", 7200),
                "base_success_rate": electric_fish_config.get("base_success_rate", 0.6),
                "failure_penalty_max_rate": electric_fish_config.get("failure_penalty_max_rate", 0.5)
            },
            "wipe_bomb": {
                "max_attempts_per_day": game_global_config.get("wipe_bomb_attempts", 3),
                "record_upload": {
                    "enabled": game_global_config.get("wipe_bomb_record_upload_enabled", False),
                    "url": game_global_config.get("wipe_bomb_record_upload_url", "http://veyu.me/api/record"),
                    "timeout_seconds": game_global_config.get("wipe_bomb_record_upload_timeout_seconds", 3),
                },
            },
            "daily_reset_hour": game_global_config.get("daily_reset_hour", 0),
            "user": {
                "initial_coins": user_config.get("initial_coins", 200)
            },
            "market": {
                "listing_tax_rate": market_config.get("listing_tax_rate", 0.05)
            },
            "tax": {
                "is_tax": self.is_tax,
                "threshold": self.threshold,
                "step_coins": self.step_coins,
                "step_rate": self.step_rate,
                "min_rate": self.min_rate,
                "max_rate": self.max_rate
            },
            "pond_upgrades": [
                { "from": 480, "to": 999, "cost": 50000 },
                { "from": 999, "to": 9999, "cost": 500000 },
                { "from": 9999, "to": 99999, "cost": 50000000 },
                { "from": 99999, "to": 999999, "cost": 5000000000 },
            ],
            "sell_prices": {
                "rod": { 
                    "1": sell_prices_config.get("by_rarity_1", 100),
                    "2": sell_prices_config.get("by_rarity_2", 500),
                    "3": sell_prices_config.get("by_rarity_3", 2000),
                    "4": sell_prices_config.get("by_rarity_4", 5000),
                    "5": sell_prices_config.get("by_rarity_5", 10000)
                },
                "accessory": { 
                    "1": sell_prices_config.get("by_rarity_1", 100),
                    "2": sell_prices_config.get("by_rarity_2", 500),
                    "3": sell_prices_config.get("by_rarity_3", 2000),
                    "4": sell_prices_config.get("by_rarity_4", 5000),
                    "5": sell_prices_config.get("by_rarity_5", 10000)
                },
                "refine_multiplier": {
                    "1": 1.0, "2": 1.6, "3": 3.0, "4": 6.0, "5": 12.0,
                    "6": 25.0, "7": 55.0, "8": 125.0, "9": 280.0, "10": 660.0
                }
            },
            "exchange": exchange_config,  # 直接使用框架的配置
            "armed_state": {
                "item_ids": armed_state_config.get("item_ids", [8, 9, 10, 11, 12, 13]),
                # 默认覆盖当前高星鱼饵区间，后续可通过配置精细覆盖
                "bait_ids": armed_state_config.get(
                    "bait_ids",
                    list(range(7, 23)),
                ),
                # 鱼饵默认保持当前“可自动使用”的旧行为，直到玩家主动关闭
                "default_armed_for_baits": armed_state_config.get(
                    "default_armed_for_baits", True
                ),
            },
            "gacha_guarantee": gacha_guarantee_config,
        }
        
        # 初始化数据库模式
        plugin_root_dir = os.path.dirname(__file__)
        migrations_path = os.path.join(plugin_root_dir, "core", "database", "migrations")
        run_migrations(db_path, migrations_path)

        # --- 2. 组合根：实例化所有仓储层 ---
        self.user_repo = SqliteUserRepository(db_path)
        self.item_template_repo = SqliteItemTemplateRepository(db_path)
        self.inventory_repo = SqliteInventoryRepository(db_path)
        self.gacha_repo = SqliteGachaRepository(db_path)
        self.market_repo = SqliteMarketRepository(db_path)
        self.shop_repo = SqliteShopRepository(db_path)
        self.log_repo = SqliteLogRepository(db_path)
        self.achievement_repo = SqliteAchievementRepository(db_path)
        self.buff_repo = SqliteUserBuffRepository(db_path)
        self.exchange_repo = SqliteExchangeRepository(db_path)

        # --- 3. 组合根：实例化所有服务层，并注入依赖 ---
        # 3.1 核心服务必须在效果管理器之前实例化，以解决依赖问题
        self.fishing_zone_service = FishingZoneService(self.item_template_repo, self.inventory_repo, self.game_config)
        self.game_mechanics_service = GameMechanicsService(self.user_repo, self.log_repo, self.inventory_repo,
                                                          self.item_template_repo, self.buff_repo, self.game_config)

        # 3.3 实例化其他核心服务
        self.gacha_service = GachaService(
            self.gacha_repo,
            self.user_repo,
            self.inventory_repo,
            self.item_template_repo,
            self.log_repo,
            self.achievement_repo,
            self.game_config,
        )
        # UserService 依赖 GachaService，因此在 GachaService 之后实例化
        self.user_service = UserService(self.user_repo, self.log_repo, self.inventory_repo, self.item_template_repo, self.gacha_service, self.game_config, self.achievement_repo)
        self.inventory_service = InventoryService(
            self.inventory_repo,
            self.user_repo,
            self.item_template_repo,
            None,  # 先设为None，稍后设置
            self.game_mechanics_service,
            self.game_config,
        )
        self.shop_service = ShopService(self.item_template_repo, self.inventory_repo, self.user_repo, self.shop_repo, self.game_config)
        # MarketService 依赖 exchange_repo
        self.market_service = MarketService(self.market_repo, self.inventory_repo, self.user_repo, self.log_repo,
                                           self.item_template_repo, self.exchange_repo, self.game_config)
        self.achievement_service = AchievementService(self.achievement_repo, self.user_repo, self.inventory_repo,
                                                     self.item_template_repo, self.log_repo, self.game_config)
        
        # 初始化科考服务
        from .core.services.expedition_service import ExpeditionService
        self.expedition_service = ExpeditionService(
            self.user_repo,
            self.inventory_repo,
            self.item_template_repo,
            self.log_repo,
            self.game_config,
        )
        
        # 将科考服务注入到库存服务
        self.inventory_service.expedition_service = self.expedition_service
        
        self.fishing_service = FishingService(
            self.user_repo,
            self.inventory_repo,
            self.item_template_repo,
            self.log_repo,
            self.buff_repo,
            self.fishing_zone_service,
            self.game_config,
            self.expedition_service,  # 传递科考服务
        )
        
        # 导入并初始化水族箱服务
        from .core.services.aquarium_service import AquariumService
        self.aquarium_service = AquariumService(
            self.inventory_repo,
            self.user_repo,
            self.item_template_repo
        )
        
        # 初始化交易所服务
        self.exchange_service = ExchangeService(self.user_repo, self.exchange_repo, self.game_config, self.log_repo, self.market_service)
        
        # 初始化交易所处理器
        self.exchange_handlers = ExchangeHandlers(self)
        
        #初始化钓鱼处理器
        self.fishing_handlers = FishingHandlers(self)


        # 3.2 实例化效果管理器并自动注册所有效果（需要在fishing_service之后）
        self.effect_manager = EffectManager()
        plugin_package = __package__ or __name__.rsplit(".", 1)[0]
        self.effect_manager.discover_and_register(
            effects_package_path=f"{plugin_package}.core.services.item_effects",
            dependencies={
                "user_repo": self.user_repo, 
                "buff_repo": self.buff_repo,
                "game_mechanics_service": self.game_mechanics_service,
                "fishing_service": self.fishing_service,
                "log_repo": self.log_repo,
                "game_config": self.game_config,
                "achievement_repo": self.achievement_repo,
                "item_template_repo": self.item_template_repo,
                "inventory_repo": self.inventory_repo,
            },
        )
        
        # 设置inventory_service的effect_manager
        self.inventory_service.effect_manager = self.effect_manager

        self.item_template_service = ItemTemplateService(self.item_template_repo, self.gacha_repo)

        # --- 4. 启动后台任务 ---
        self.fishing_service.start_auto_fishing_task()
        if self.is_tax:
            self.fishing_service.start_daily_tax_task()  # 启动独立的税收线程
        self.achievement_service.start_achievement_check_task()
        self.exchange_service.start_daily_price_update_task() # 启动交易所后台任务
        
        # --- 5. 初始化核心游戏数据 ---
        data_setup_service = DataSetupService(
            self.item_template_repo, self.gacha_repo, self.shop_repo, db_path
        )
        data_setup_service.setup_initial_data()

        # 商店完全由后台管控，不再自动种子化

        # --- 6. (临时) 实例化数据服务，供调试命令使用 ---
        self.data_setup_service = data_setup_service

        # --- Web后台配置 ---
        self.web_admin_task = None
        self._web_admin_shutdown_event = None
        webui_config = config.get("webui", {})
        self.secret_key = webui_config.get("secret_key")
        if not self.secret_key:
            logger.error("安全警告：Web后台管理的'secret_key'未在配置中设置！强烈建议您设置一个长且随机的字符串以保证安全。")
            self.secret_key = None
        self.port = webui_config.get("port", 7777)
        
        # --- 玩家WebUI配置 ---
        self.web_player_task = None
        self._web_player_shutdown_event = asyncio.Event()
        self.player_port = webui_config.get("player_port", 8888)
        public_base_url_config_value = str(webui_config.get("public_base_url", "") or "").strip().rstrip("/")
        self.public_base_url_configured = bool(public_base_url_config_value)
        self.public_base_url = str(
            public_base_url_config_value
            or "https://fish.eyeoftheuniverse.top"
        ).rstrip("/")
        oauth_config = webui_config.get("oauth", {})
        linuxdo_oauth_config = oauth_config.get("linuxdo", {})
        configured_redirect_uri = str(linuxdo_oauth_config.get("redirect_uri", "") or "").strip()
        default_redirect_uri = f"{self.public_base_url}/player/oauth/linuxdo/callback"
        if not configured_redirect_uri:
            configured_redirect_uri = default_redirect_uri
        self.player_linuxdo_oauth_config = {
            "enabled": linuxdo_oauth_config.get("enabled", False),
            "client_id": linuxdo_oauth_config.get("client_id", ""),
            "client_secret": linuxdo_oauth_config.get("client_secret", ""),
            "redirect_uri": configured_redirect_uri,
            "scope": linuxdo_oauth_config.get("scope", "read"),
            "user_id_field": linuxdo_oauth_config.get("user_id_field", "id"),
            "nickname_field": linuxdo_oauth_config.get("nickname_field", "username"),
            "auto_register": linuxdo_oauth_config.get("auto_register", True),
            "allow_password_fallback": linuxdo_oauth_config.get("allow_password_fallback", True),
            "proxy_url": linuxdo_oauth_config.get("proxy_url", ""),
        }
        
        # 启动玩家WebUI
        self.web_player_task = asyncio.create_task(self._start_player_webui())

        # 管理员扮演功能
        self.impersonation_map = {}

    def _get_effective_user_id(self, event: AstrMessageEvent):
        """获取在当前上下文中应当作为指令执行者的用户ID。
        - 默认返回消息发送者ID
        - 若发送者是管理员且已开启代理，则返回被代理用户ID
        注意：仅在非管理员指令中调用该方法；管理员指令应使用真实管理员ID。
        """
        admin_id = event.get_sender_id()
        return self.impersonation_map.get(admin_id, admin_id)

    def _is_group_message_event(self, event: AstrMessageEvent) -> bool:
        """尽量兼容不同平台适配器的群聊判断。"""
        get_group_id = getattr(event, "get_group_id", None)
        if callable(get_group_id):
            try:
                if get_group_id():
                    return True
            except Exception:
                pass

        message_obj = getattr(event, "message_obj", None)
        for attr in ("group_id", "group", "room_id"):
            value = getattr(message_obj, attr, None)
            if value:
                return True

        message_type = str(getattr(message_obj, "message_type", "") or "").lower()
        return message_type in {"group", "guild", "channel"}

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        logger.info("""
    _____ _     _     _
    |  ___(_)___| |__ (_)_ __   __ _
    | |_  | / __| '_ \\| | '_ \\ / _` |
    |  _| | \\__ \\ | | | | | | | (_| |
    |_|   |_|___/_| |_|_|_| |_|\\__, |
                               |___/
                               """)
    # =========== 基础与核心 ==========

    @filter.command("注册")
    async def register_user(self, event: AstrMessageEvent):
        """注册成为 MetaFishing！玩家，开始你的钓鱼之旅"""
        async for r in common_handlers.register_user(self, event):
            yield r

    @filter.command("钓鱼")
    async def fish(self, event: AstrMessageEvent):
        """进行一次钓鱼，消耗金币并获得鱼类或物品"""
        async for r in self.fishing_handlers.fish(event):
            yield r

    @filter.command("签到")
    async def sign_in(self, event: AstrMessageEvent):
        """每日签到领取奖励，连续签到奖励更丰厚"""
        async for r in common_handlers.sign_in(self, event):
            yield r

    @filter.command("自动钓鱼")
    async def auto_fish(self, event: AstrMessageEvent):
        """开启或关闭自动钓鱼功能，自动钓鱼会定期帮你钓鱼"""
        async for r in self.fishing_handlers.auto_fish(event): 
            yield r

    @filter.command("状态", alias={"我的状态"})
    async def state(self, event: AstrMessageEvent):
        """查看你的游戏状态，包括金币、等级、装备等信息"""
        async for r in common_handlers.state(self, event):
            yield r

    @filter.command("钓鱼帮助", alias={"钓鱼菜单", "菜单"})
    async def fishing_help(self, event: AstrMessageEvent):
        """查看 MetaFishing！的帮助信息和所有可用命令"""
        async for r in common_handlers.fishing_help(self, event):
            yield r

    # =========== 背包与资产 ==========

    @filter.command("背包", alias={"查看背包", "我的背包"})
    async def user_backpack(self, event: AstrMessageEvent):
        """查看你的背包，包含所有物品和装备"""
        async for r in inventory_handlers.user_backpack(self, event):
            yield r

    @filter.command("鱼塘")
    async def pond(self, event: AstrMessageEvent):
        """查看你的鱼塘，查看所有已钓到的鱼"""
        async for r in inventory_handlers.pond(self, event):
            yield r

    @filter.command("偷看鱼塘", alias={"查看鱼塘", "偷看"})
    async def peek_pond(self, event: AstrMessageEvent):
        """偷看别人的鱼塘，查看其他玩家的鱼。用法：偷看鱼塘 @用户"""
        async for r in inventory_handlers.peek_pond(self, event):
            yield r

    @filter.command("鱼塘容量")
    async def pond_capacity(self, event: AstrMessageEvent):
        """查看当前鱼塘容量和升级信息"""
        async for r in inventory_handlers.pond_capacity(self, event):
            yield r

    @filter.command("升级鱼塘", alias={"鱼塘升级"})
    async def upgrade_pond(self, event: AstrMessageEvent):
        """升级鱼塘容量，可以存放更多的鱼"""
        async for r in inventory_handlers.upgrade_pond(self, event):
            yield r

    # 水族箱相关命令
    @filter.command("水族箱")
    async def aquarium(self, event: AstrMessageEvent):
        """查看你的水族箱，欣赏展示的珍贵鱼类"""
        async for r in aquarium_handlers.aquarium(self, event):
            yield r

    @filter.command("放入水族箱", alias={"移入水族箱"})
    async def add_to_aquarium(self, event: AstrMessageEvent):
        """将鱼从鱼塘放入水族箱展示。用法：放入水族箱 鱼的编号"""
        async for r in aquarium_handlers.add_to_aquarium(self, event):
            yield r

    @filter.command("移出水族箱", alias={"移回鱼塘"})
    async def remove_from_aquarium(self, event: AstrMessageEvent):
        """将鱼从水族箱移回鱼塘。用法：移出水族箱 鱼的编号"""
        async for r in aquarium_handlers.remove_from_aquarium(self, event):
            yield r

    @filter.command("升级水族箱", alias={"水族箱升级"})
    async def upgrade_aquarium(self, event: AstrMessageEvent):
        """升级水族箱容量，可以展示更多珍贵鱼类"""
        async for r in aquarium_handlers.upgrade_aquarium(self, event):
            yield r

    @filter.command("放入稀有度")
    async def add_rarity_to_aquarium(self, event: AstrMessageEvent):
        """按稀有度批量放入水族箱。用法：放入稀有度 3 (将所有3星鱼放入水族箱)"""
        async for r in aquarium_handlers.add_rarity_to_aquarium(self, event):
            yield r

    @filter.command("移出稀有度")
    async def remove_rarity_from_aquarium(self, event: AstrMessageEvent):
        """按稀有度批量移出水族箱。用法：移出稀有度 1 (将所有1星鱼移回鱼塘)"""
        async for r in aquarium_handlers.remove_rarity_from_aquarium(self, event):
            yield r

    @filter.command("鱼竿")
    async def rod(self, event: AstrMessageEvent):
        """查看你拥有的所有鱼竿"""
        async for r in inventory_handlers.rod(self, event):
            yield r

    @filter.command("精炼", alias={"强化"})
    async def refine_equipment(self, event: AstrMessageEvent):
        """精炼装备提升属性。用法：精炼 装备编号"""
        async for r in inventory_handlers.refine_equipment(self, event):
            yield r

    @filter.command("出售", alias={"卖出"})
    async def sell_equipment(self, event: AstrMessageEvent):
        """出售装备换取金币。用法：出售 装备编号"""
        async for r in inventory_handlers.sell_equipment(self, event):
            yield r

    @filter.command("鱼饵")
    async def bait(self, event: AstrMessageEvent):
        """查看你拥有的所有鱼饵"""
        async for r in inventory_handlers.bait(self, event):
            yield r

    @filter.command("启用自动鱼饵")
    async def enable_auto_bait(self, event: AstrMessageEvent):
        """允许指定鱼饵参与自动使用。用法：启用自动鱼饵 13"""
        async for r in inventory_handlers.set_bait_auto_use(self, event, True):
            yield r

    @filter.command("停用自动鱼饵")
    async def disable_auto_bait(self, event: AstrMessageEvent):
        """禁止指定鱼饵参与自动使用。用法：停用自动鱼饵 13"""
        async for r in inventory_handlers.set_bait_auto_use(self, event, False):
            yield r

    @filter.command("道具", alias={"我的道具", "查看道具"})
    async def items(self, event: AstrMessageEvent):
        """查看你拥有的所有道具"""
        async for r in inventory_handlers.items(self, event):
            yield r

    @filter.command("开启全部钱袋", alias={"打开全部钱袋", "打开所有钱袋"})
    async def open_all_money_bags(self, event: AstrMessageEvent):
        """一次性打开所有钱袋，获得金币"""
        async for r in inventory_handlers.open_all_money_bags(self, event):
            yield r

    @filter.command("饰品")
    async def accessories(self, event: AstrMessageEvent):
        """查看你拥有的所有饰品"""
        async for r in inventory_handlers.accessories(self, event):
            yield r

    @filter.command("锁定", alias={"上锁"})
    async def lock_equipment(self, event: AstrMessageEvent):
        """锁定装备防止误操作。用法：锁定 装备编号"""
        async for r in inventory_handlers.lock_equipment(self, event):
            yield r

    @filter.command("解锁", alias={"开锁"})
    async def unlock_equipment(self, event: AstrMessageEvent):
        """解锁已锁定的装备。用法：解锁 装备编号"""
        async for r in inventory_handlers.unlock_equipment(self, event):
            yield r

    @filter.command("使用", alias={"装备"})
    async def use_equipment(self, event: AstrMessageEvent):
        """使用或装备物品。用法：使用 物品编号"""
        async for r in inventory_handlers.use_equipment(self, event):
            yield r

    @filter.command("金币", alias={"钱包", "余额"})
    async def coins(self, event: AstrMessageEvent):
        """查看你当前拥有的金币数量"""
        async for r in inventory_handlers.coins(self, event):
            yield r

    @filter.command("转账", alias={"赠送"})
    async def transfer_coins(self, event: AstrMessageEvent):
        """转账金币给其他玩家。用法：转账 @用户 金额"""
        async for r in common_handlers.transfer_coins(self, event):
            yield r

    @filter.command("更新昵称", alias={"修改昵称", "改昵称", "昵称"})
    async def update_nickname(self, event: AstrMessageEvent):
        """更新你的游戏昵称。用法：更新昵称 新昵称"""
        async for r in common_handlers.update_nickname(self, event):
            yield r

    @filter.command("初始密码获取", alias={"初始密码", "获取初始密码", "登录密钥", "获取密钥"})
    async def get_initial_password(self, event: AstrMessageEvent):
        """私聊获取 WebUI/App 初始登录密码"""
        if self._is_group_message_event(event):
            yield event.plain_result("❌ 为保护账号安全，请在私聊中使用“初始密码获取”。")
            return
        user_id = self._get_effective_user_id(event)
        if not self.user_repo.check_exists(user_id):
            yield event.plain_result("❌ 你还没有注册，请先使用“注册”。")
            return
        from .player.server import ensure_initial_password
        initial_password = ensure_initial_password(user_id)
        yield event.plain_result(f"你的 WebUI/App 初始密码是：{initial_password}")

    @filter.command("获取网页端地址", alias={"网页端地址", "获取WebUI地址", "WebUI地址", "获取网页地址"})
    async def get_player_webui_url(self, event: AstrMessageEvent):
        """获取玩家 WebUI 公网访问地址"""
        if not self.public_base_url_configured:
            yield event.plain_result("未配置网页端地址")
            return
        yield event.plain_result(f"玩家 WebUI 网页端地址：{self.public_base_url}")

    @filter.command("高级货币", alias={"钻石", "星石"})
    async def premium(self, event: AstrMessageEvent):
        """查看你当前拥有的高级货币（钻石/星石）数量"""
        async for r in inventory_handlers.premium(self, event):
            yield r

    # =========== 钓鱼与图鉴 ==========

    @filter.command("钓鱼区域", alias={"区域"})
    async def fishing_area(self, event: AstrMessageEvent):
        """查看所有钓鱼区域和切换钓鱼区域。用法：钓鱼区域 [区域编号]"""
        async for r in self.fishing_handlers.fishing_area(event):
            yield r

    @filter.command("鱼类图鉴", alias={"图鉴"})
    async def fish_pokedex(self, event: AstrMessageEvent):
        """查看鱼类图鉴，了解所有可钓到的鱼"""
        async for r in self.fishing_handlers.fish_pokedex(event): 
            yield r

    @filter.command("图鉴奖励")
    async def pokedex_reward(self, event: AstrMessageEvent):
        """领取图鉴奖励或查看当前图鉴奖励进度"""
        async for r in self.fishing_handlers.pokedex_reward(event):
            yield r

    @filter.command("装备图鉴", alias={"装备收集"})
    async def equipment_pokedex(self, event: AstrMessageEvent):
        """查看装备图鉴，了解已收集的鱼竿、饰品和鱼饵"""
        async for r in self.fishing_handlers.equipment_pokedex(event):
            yield r

    @filter.command("装备图鉴奖励")
    async def equipment_pokedex_reward(self, event: AstrMessageEvent):
        """领取装备图鉴奖励或查看当前装备图鉴奖励进度"""
        async for r in self.fishing_handlers.equipment_pokedex_reward(event):
            yield r

    # =========== 市场与商店 ==========

    @filter.command("全部卖出", alias={"全部出售", "卖出全部", "出售全部", "清空鱼"})
    async def sell_all(self, event: AstrMessageEvent):
        """卖出鱼塘中所有的鱼，换取金币"""
        async for r in market_handlers.sell_all(self, event):
            yield r

    @filter.command("保留卖出", alias={"保留出售", "卖出保留", "出售保留"})
    async def sell_keep(self, event: AstrMessageEvent):
        """卖出鱼塘中的鱼，但保留指定数量。用法：保留卖出 保留数量"""
        async for r in market_handlers.sell_keep(self, event):
            yield r

    @filter.command("砸锅卖铁", alias={"破产", "清空"})
    async def sell_everything(self, event: AstrMessageEvent):
        """卖掉所有可以出售的物品，包括鱼、装备等"""
        async for r in market_handlers.sell_everything(self, event):
            yield r

    @filter.command("出售稀有度", alias={"稀有度出售", "出售星级"})
    async def sell_by_rarity(self, event: AstrMessageEvent):
        """按稀有度出售鱼。用法：出售稀有度 星级"""
        async for r in market_handlers.sell_by_rarity(self, event):
            yield r

    @filter.command("出售所有鱼竿", alias={"出售全部鱼竿", "卖出所有鱼竿", "卖出全部鱼竿", "清空鱼竿"})
    async def sell_all_rods(self, event: AstrMessageEvent):
        """出售所有未装备且未锁定的鱼竿"""
        async for r in market_handlers.sell_all_rods(self, event):
            yield r

    @filter.command("出售所有饰品", alias={"出售全部饰品", "卖出所有饰品", "卖出全部饰品", "清空饰品"})
    async def sell_all_accessories(self, event: AstrMessageEvent):
        """出售所有未装备且未锁定的饰品"""
        async for r in market_handlers.sell_all_accessories(self, event):
            yield r

    @filter.command("商店")
    async def shop(self, event: AstrMessageEvent):
        """查看所有可用的商店"""
        async for r in market_handlers.shop(self, event):
            yield r

    @filter.command("商店购买", alias={"购买商店商品", "购买商店"})
    async def buy_in_shop(self, event: AstrMessageEvent):
        """从商店购买商品。用法：商店购买 商品编号 [数量]"""
        async for r in market_handlers.buy_in_shop(self, event):
            yield r

    @filter.command("市场")
    async def market(self, event: AstrMessageEvent):
        """查看玩家市场中的所有上架商品"""
        async for r in market_handlers.market(self, event):
            yield r

    @filter.command("上架")
    async def list_any(self, event: AstrMessageEvent):
        """将物品上架到市场出售。用法：上架 物品编号 价格"""
        async for r in market_handlers.list_any(self, event):
            yield r

    @filter.command("购买")
    async def buy_item(self, event: AstrMessageEvent):
        """从市场购买玩家上架的商品。用法：购买 订单编号"""
        async for r in market_handlers.buy_item(self, event):
            yield r

    @filter.command("我的上架", alias={"上架列表", "我的商品", "我的挂单"})
    async def my_listings(self, event: AstrMessageEvent):
        """查看你在市场上架的所有商品"""
        async for r in market_handlers.my_listings(self, event):
            yield r

    @filter.command("下架")
    async def delist_item(self, event: AstrMessageEvent):
        """从市场下架你上架的商品。用法：下架 订单编号"""
        async for r in market_handlers.delist_item(self, event):
            yield r

    # =========== 抽卡 ==========

    @filter.command("抽卡", alias={"抽奖"})
    async def gacha(self, event: AstrMessageEvent):
        """进行一次抽卡，有机会获得稀有装备和道具"""
        async for r in gacha_handlers.gacha(self, event):
            yield r

    @filter.command("十连")
    async def ten_gacha(self, event: AstrMessageEvent):
        """进行十次连续抽卡，有保底机制"""
        async for r in gacha_handlers.ten_gacha(self, event):
            yield r

    @filter.command("查看卡池", alias={"卡池"})
    async def view_gacha_pool(self, event: AstrMessageEvent):
        """查看当前卡池中的所有物品及其概率"""
        async for r in gacha_handlers.view_gacha_pool(self, event):
            yield r

    @filter.command("擦弹")
    async def wipe_bomb(self, event: AstrMessageEvent):
        """使用擦弹道具，有机会重置保底计数"""
        async for r in gacha_handlers.wipe_bomb(self, event):
            yield r

    # =========== 科考系统 ==========

    @filter.command("发起科考")
    async def start_expedition(self, event: AstrMessageEvent):
        """发起科考队伍。用法：/发起科考 探险|征服|圣域 [@用户1 @用户2 ...]"""
        from .handlers.expedition_handlers import ExpeditionHandlers
        handlers = ExpeditionHandlers(self.expedition_service)
        result = await handlers.start_expedition(self, event)
        yield event.plain_result(result["message"])

    @filter.command("加入科考")
    async def join_expedition(self, event: AstrMessageEvent):
        """加入科考队伍。用法：/加入科考 <邀请码>"""
        from .handlers.expedition_handlers import ExpeditionHandlers
        handlers = ExpeditionHandlers(self.expedition_service)
        result = await handlers.join_expedition(self, event)
        yield event.plain_result(result["message"])

    @filter.command("退出科考")
    async def leave_expedition(self, event: AstrMessageEvent):
        """退出当前科考队伍"""
        from .handlers.expedition_handlers import ExpeditionHandlers
        handlers = ExpeditionHandlers(self.expedition_service)
        result = await handlers.leave_expedition(self, event)
        yield event.plain_result(result["message"])

    @filter.command("科考状态")
    async def expedition_status(self, event: AstrMessageEvent):
        """查看当前科考进度"""
        from .handlers.expedition_handlers import ExpeditionHandlers
        handlers = ExpeditionHandlers(self.expedition_service)
        result = await handlers.expedition_status(self, event)
        yield event.plain_result(result["message"])

    @filter.command("结束科考")
    async def end_expedition(self, event: AstrMessageEvent):
        """查看科考结束/领奖规则提示"""
        from .handlers.expedition_handlers import ExpeditionHandlers
        handlers = ExpeditionHandlers(self.expedition_service)
        result = await handlers.end_expedition(self, event)
        yield event.plain_result(result["message"])

    @filter.command("科考帮助")
    async def expedition_help(self, event: AstrMessageEvent):
        """查看科考系统帮助"""
        from .handlers.expedition_handlers import ExpeditionHandlers
        handlers = ExpeditionHandlers(self.expedition_service)
        result = await handlers.expedition_help(self, event)
        yield event.plain_result(result["message"])

    @filter.command("测试科考")
    async def test_expedition(self, event: AstrMessageEvent):
        """【管理员】强制将当前科考设置为100%完成"""
        from .handlers.expedition_handlers import ExpeditionHandlers
        handlers = ExpeditionHandlers(self.expedition_service)
        result = await handlers.test_expedition(self, event)
        yield event.plain_result(result["message"])

    # =========== 社交 ==========

    @filter.command("排行榜", alias={"phb"})
    async def ranking(self, event: AstrMessageEvent):
        """查看金币、鱼类等各种排行榜"""
        async for r in social_handlers.ranking(self, event):
            yield r

    @filter.command("偷鱼")
    async def steal_fish(self, event: AstrMessageEvent):
        """偷取其他玩家的鱼，但有失败风险。用法：偷鱼 @用户"""
        async for r in social_handlers.steal_fish(self, event):
            yield r

    @filter.command("电鱼")
    async def electric_fish(self, event: AstrMessageEvent):
        """对其他玩家使用电鱼，成功可获得金币。用法：电鱼 @用户"""
        async for r in social_handlers.electric_fish(self, event):
            yield r

    @filter.command("查看称号", alias={"称号"})
    async def view_titles(self, event: AstrMessageEvent):
        """查看你拥有的所有称号"""
        async for r in social_handlers.view_titles(self, event):
            yield r

    @filter.command("使用称号")
    async def use_title(self, event: AstrMessageEvent):
        """装备或卸下称号。用法：使用称号 称号编号"""
        async for r in social_handlers.use_title(self, event):
            yield r

    @filter.command("查看成就", alias={"成就"})
    async def view_achievements(self, event: AstrMessageEvent):
        """查看你的成就完成情况"""
        async for r in social_handlers.view_achievements(self, event):
            yield r

    @filter.command("税收记录")
    async def tax_record(self, event: AstrMessageEvent):
        """查看你的税收缴纳记录"""
        async for r in social_handlers.tax_record(self, event):
            yield r
            
    # =========== 期货 ==========

    @filter.command("期货", alias={"交易所"})
    async def exchange_main(self, event: AstrMessageEvent):
        """查看期货信息和进行交易。用法：期货 [买入/卖出] [商品] [数量]"""
        async for r in self.exchange_handlers.exchange_main(event):
            yield r

    @filter.command("持仓")
    async def view_inventory(self, event: AstrMessageEvent):
        """查看你在期货中的持仓情况"""
        async for r in self.exchange_handlers.view_inventory(event):
            yield r

    @filter.command("清仓")
    async def clear_inventory(self, event: AstrMessageEvent):
        """清空期货持仓，将所有商品按当前价格卖出"""
        async for r in self.exchange_handlers.clear_inventory(event):
            yield r

    @filter.command("期货容量", alias={"交易所容量"})
    async def exchange_capacity(self, event: AstrMessageEvent):
        """查看当前期货容量和升级信息"""
        async for r in self.exchange_handlers.exchange_capacity(event):
            yield r

    @filter.command("升级期货", alias={"期货升级", "升级交易所", "交易所升级"})
    async def upgrade_exchange_capacity(self, event: AstrMessageEvent):
        """升级期货容量，可以持有更多商品"""
        async for r in self.exchange_handlers.upgrade_exchange_capacity(event):
            yield r

    # =========== 管理后台 ==========

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("修改金币")
    async def modify_coins(self, event: AstrMessageEvent):
        """[管理员] 修改指定玩家的金币数量。用法：修改金币 @用户 数量"""
        async for r in admin_handlers.modify_coins(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("修改高级货币")
    async def modify_premium(self, event: AstrMessageEvent):
        """[管理员] 修改指定玩家的高级货币数量。用法：修改高级货币 @用户 数量"""
        async for r in admin_handlers.modify_premium(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("奖励高级货币")
    async def reward_premium(self, event: AstrMessageEvent):
        """[管理员] 奖励指定玩家高级货币。用法：奖励高级货币 @用户 数量"""
        async for r in admin_handlers.reward_premium(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("扣除高级货币")
    async def deduct_premium(self, event: AstrMessageEvent):
        """[管理员] 扣除指定玩家的高级货币。用法：扣除高级货币 @用户 数量"""
        async for r in admin_handlers.deduct_premium(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("全体奖励金币")
    async def reward_all_coins(self, event: AstrMessageEvent):
        """[管理员] 给所有玩家奖励金币。用法：全体奖励金币 数量"""
        async for r in admin_handlers.reward_all_coins(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("全体奖励高级货币")
    async def reward_all_premium(self, event: AstrMessageEvent):
        """[管理员] 给所有玩家奖励高级货币。用法：全体奖励高级货币 数量"""
        async for r in admin_handlers.reward_all_premium(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("全体扣除金币")
    async def deduct_all_coins(self, event: AstrMessageEvent):
        """[管理员] 扣除所有玩家的金币。用法：全体扣除金币 数量"""
        async for r in admin_handlers.deduct_all_coins(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("全体扣除高级货币")
    async def deduct_all_premium(self, event: AstrMessageEvent):
        """[管理员] 扣除所有玩家的高级货币。用法：全体扣除高级货币 数量"""
        async for r in admin_handlers.deduct_all_premium(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("奖励金币")
    async def reward_coins(self, event: AstrMessageEvent):
        """[管理员] 奖励指定玩家金币。用法：奖励金币 @用户 数量"""
        async for r in admin_handlers.reward_coins(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("扣除金币")
    async def deduct_coins(self, event: AstrMessageEvent):
        """[管理员] 扣除指定玩家的金币。用法：扣除金币 @用户 数量"""
        async for r in admin_handlers.deduct_coins(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("开启钓鱼后台管理")
    async def start_admin(self, event: AstrMessageEvent):
        """[管理员] 启动Web后台管理服务器"""
        async for r in admin_handlers.start_admin(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("关闭钓鱼后台管理")
    async def stop_admin(self, event: AstrMessageEvent):
        """[管理员] 关闭Web后台管理服务器"""
        async for r in admin_handlers.stop_admin(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("同步初始设定", alias={"同步设定", "同步数据", "同步"})
    async def sync_initial_data(self, event: AstrMessageEvent):
        """[管理员] 同步游戏初始设定数据到数据库"""
        async for r in admin_handlers.sync_initial_data(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("授予称号")
    async def grant_title(self, event: AstrMessageEvent):
        """[管理员] 授予用户称号。用法：授予称号 @用户 称号名称"""
        async for r in admin_handlers.grant_title(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("移除称号")
    async def revoke_title(self, event: AstrMessageEvent):
        """[管理员] 移除用户称号。用法：移除称号 @用户 称号名称"""
        async for r in admin_handlers.revoke_title(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("创建称号")
    async def create_title(self, event: AstrMessageEvent):
        """[管理员] 创建自定义称号。用法：创建称号 称号名称 描述 [显示格式]"""
        async for r in admin_handlers.create_title(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("代理上线", alias={"login"})
    async def impersonate_start(self, event: AstrMessageEvent):
        """[管理员] 代理其他玩家进行操作。用法：代理上线 @用户"""
        async for r in admin_handlers.impersonate_start(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("代理下线", alias={"logout"})
    async def impersonate_stop(self, event: AstrMessageEvent):
        """[管理员] 结束代理模式，恢复为管理员身份"""
        async for r in admin_handlers.impersonate_stop(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("全体发放道具")
    async def reward_all_items(self, event: AstrMessageEvent):
        """[管理员] 给所有玩家发放道具。用法：全体发放道具 道具ID 数量"""
        async for r in admin_handlers.reward_all_items(self, event):
            yield r

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("补充鱼池")
    async def replenish_fish_pools(self, event: AstrMessageEvent):
        """[管理员] 重置所有钓鱼区域的稀有鱼剩余数量"""
        async for r in admin_handlers.replenish_fish_pools(self, event):
            yield r

    async def _check_port_active(self):
        """验证端口是否实际已激活"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.port),
                timeout=1
            )
            writer.close()
            return True
        except:
            return False

    async def terminate(self):
        """插件被卸载/停用时调用"""
        logger.info("钓鱼插件正在终止...")
        self.fishing_service.stop_auto_fishing_task()
        self.fishing_service.stop_daily_tax_task()  # 终止独立的税收线程
        self.achievement_service.stop_achievement_check_task()
        self.exchange_service.stop_daily_price_update_task() # 终止交易所后台任务

        await self._shutdown_server_task(
            "web_player_task",
            "_web_player_shutdown_event",
            "玩家WebUI",
        )
        await self._shutdown_server_task(
            "web_admin_task",
            "_web_admin_shutdown_event",
            "钓鱼后台管理",
        )

        logger.info("钓鱼插件已成功终止。")

    async def _shutdown_server_task(
        self,
        task_attr: str,
        event_attr: str,
        task_name: str,
        timeout: int = 5,
    ):
        """优雅关闭 Hypercorn 任务，超时后再回退到 cancel。"""
        task = getattr(self, task_attr, None)
        if not task:
            return

        shutdown_event = getattr(self, event_attr, None)
        if shutdown_event and not shutdown_event.is_set():
            shutdown_event.set()

        if not task.done():
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"{task_name} 在 {timeout} 秒内未优雅退出，回退到取消任务")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                logger.info(f"{task_name}任务已取消")
            except Exception as e:
                logger.warning(f"等待{task_name}结束时发生错误: {e}")
        else:
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"{task_name}任务已取消")
            except Exception as e:
                logger.warning(f"等待{task_name}结束时发生错误: {e}")

        setattr(self, task_attr, None)
        setattr(self, event_attr, None)

    async def _cancel_stale_server_tasks(self, qualname_suffix: str, event_attr: str, task_name: str):
        """取消事件循环中遗留的旧服务器任务，并优先触发其优雅关闭事件。"""
        current_task = asyncio.current_task()
        cancelled_count = 0

        for task in list(asyncio.all_tasks()):
            if task is current_task or task.done():
                continue

            coro = task.get_coro()
            qualname = getattr(coro, "__qualname__", "")
            if qualname.endswith(qualname_suffix):
                owner = None
                frame = getattr(coro, "cr_frame", None)
                if frame is not None:
                    owner = frame.f_locals.get("self")

                shutdown_event = getattr(owner, event_attr, None) if owner else None
                if shutdown_event and not shutdown_event.is_set():
                    shutdown_event.set()

                try:
                    await asyncio.wait_for(task, timeout=5)
                except asyncio.TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"等待旧{task_name}任务结束时发生错误: {e}")
                cancelled_count += 1

        if cancelled_count > 0:
            logger.warning(f"已清理 {cancelled_count} 个旧{task_name}任务")

    async def _cancel_stale_player_webui_tasks(self):
        """取消事件循环中遗留的旧玩家WebUI任务。"""
        await self._cancel_stale_server_tasks(
            "FishingPlugin._start_player_webui",
            "_web_player_shutdown_event",
            "玩家WebUI",
        )

    async def _claim_stale_shared_player_webui(self):
        """优先接管并关闭跨插件实例残留的玩家WebUI任务。"""
        current_task = asyncio.current_task()
        shared_task = type(self)._shared_player_webui_task
        shared_event = type(self)._shared_player_webui_shutdown_event

        if (
            not shared_task
            or shared_task is current_task
            or shared_task.done()
        ):
            if shared_task and shared_task.done():
                type(self)._shared_player_webui_task = None
                type(self)._shared_player_webui_shutdown_event = None
                type(self)._shared_player_webui_owner = None
            return

        logger.warning("检测到跨实例残留的玩家WebUI任务，尝试先优雅关闭旧实例")

        if shared_event and not shared_event.is_set():
            shared_event.set()

        try:
            await asyncio.wait_for(shared_task, timeout=8)
        except asyncio.TimeoutError:
            logger.warning("旧玩家WebUI任务未在超时内退出，回退到取消任务")
            shared_task.cancel()
            try:
                await shared_task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"等待旧玩家WebUI任务结束时发生错误: {e}")
        finally:
            type(self)._shared_player_webui_task = None
            type(self)._shared_player_webui_shutdown_event = None
            type(self)._shared_player_webui_owner = None

    async def _cancel_stale_admin_webui_tasks(self):
        """取消事件循环中遗留的旧后台管理任务。"""
        await self._cancel_stale_server_tasks(
            "FishingPlugin._run_admin_webui",
            "_web_admin_shutdown_event",
            "钓鱼后台管理",
        )

    async def _run_admin_webui(self, app):
        """启动管理员WebUI服务器，并支持优雅关闭。"""
        from hypercorn.asyncio import serve
        from hypercorn.config import Config

        config = Config()
        config.bind = [f"0.0.0.0:{self.port}"]
        config.use_reloader = False
        config.accesslog = None

        if self._web_admin_shutdown_event is None:
            self._web_admin_shutdown_event = asyncio.Event()

        try:
            logger.info(f"钓鱼后台管理启动中，端口: {self.port}")
            await serve(app, config, shutdown_trigger=self._web_admin_shutdown_event.wait)
            logger.info("钓鱼后台管理服务已优雅停止")
        except asyncio.CancelledError:
            logger.info("钓鱼后台管理任务已取消")
            raise

    async def _start_player_webui(self):
        """启动玩家WebUI服务器"""
        try:
            from .player.server import create_player_app

            fixed_port = int(self.player_port)

            # 1) 先接管跨实例残留任务，再清理同进程旧任务
            await self._claim_stale_shared_player_webui()
            await self._cancel_stale_player_webui_tasks()

            # 2) 如端口仍被占用，尝试强制释放
            if not await _is_port_available(fixed_port):
                logger.warning(f"玩家WebUI端口 {fixed_port} 被占用，尝试自动释放...")
                released, killed_pids = await kill_processes_on_port(fixed_port)
                if killed_pids:
                    logger.warning(f"已终止占用端口 {fixed_port} 的进程: {killed_pids}")
                if not released:
                    logger.warning(f"自动释放端口 {fixed_port} 未完全成功，继续等待端口回收")

            # 3) 固定端口重试等待
            max_wait_seconds = 15
            for waited in range(max_wait_seconds + 1):
                if await _is_port_available(fixed_port):
                    break
                await asyncio.sleep(1)
            else:
                logger.error(f"玩家WebUI启动失败：端口 {fixed_port} 持续被占用，请确认旧实例是否已退出")
                return

            # 准备服务注入
            services = {
                "user_repo": self.user_repo,
                "inventory_repo": self.inventory_repo,
                "item_template_repo": self.item_template_repo,
                "log_repo": self.log_repo,
                "buff_repo": self.buff_repo,
                "user_service": self.user_service,
                "fishing_service": self.fishing_service,
                "game_mechanics_service": self.game_mechanics_service,
                "inventory_service": self.inventory_service,
                "shop_service": self.shop_service,
                "market_service": self.market_service,
                "gacha_service": self.gacha_service,
                "exchange_service": self.exchange_service,
                "aquarium_service": self.aquarium_service,
                "expedition_service": self.expedition_service,
            }

            app = create_player_app(
                services,
                {
                    "public_base_url": self.public_base_url,
                    "unity_allowed_origins": [
                        self.public_base_url,
                        "https://fish.eyeoftheuniverse.top",
                    ],
                    "linuxdo_oauth": self.player_linuxdo_oauth_config,
                },
            )

            # 使用hypercorn启动服务器
            from hypercorn.config import Config
            from hypercorn.asyncio import serve

            config = Config()
            config.bind = [f"0.0.0.0:{fixed_port}"]
            config.use_reloader = False
            config.accesslog = None

            if self._web_player_shutdown_event is None:
                self._web_player_shutdown_event = asyncio.Event()

            type(self)._shared_player_webui_task = asyncio.current_task()
            type(self)._shared_player_webui_shutdown_event = self._web_player_shutdown_event
            type(self)._shared_player_webui_owner = self

            logger.info(f"玩家WebUI启动中，端口: {fixed_port}")
            logger.info(f"访问地址: {self.public_base_url}/player/login")

            await serve(app, config, shutdown_trigger=self._web_player_shutdown_event.wait)
            logger.info("玩家WebUI服务已优雅停止")

        except asyncio.CancelledError:
            logger.info("玩家WebUI服务已停止")
            raise
        except ImportError as e:
            logger.error(f"玩家WebUI依赖缺失: {e}")
        except OSError as e:
            if getattr(e, "errno", None) == 98:
                logger.error(f"玩家WebUI启动失败：端口 {self.player_port} 已被占用")
            else:
                logger.error(f"玩家WebUI启动失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        except Exception as e:
            logger.error(f"玩家WebUI启动失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            current_task = asyncio.current_task()
            if type(self)._shared_player_webui_task is current_task:
                type(self)._shared_player_webui_task = None
                type(self)._shared_player_webui_shutdown_event = None
                type(self)._shared_player_webui_owner = None
