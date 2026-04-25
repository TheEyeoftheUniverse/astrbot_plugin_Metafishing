import sqlite3
from pathlib import Path

from astrbot.api import logger

from ..repositories.abstract_repository import (
    AbstractGachaRepository,
    AbstractItemTemplateRepository,
    AbstractShopRepository,
)


class DataSetupService:
    """负责在首次启动时初始化游戏基础数据。"""

    def __init__(
        self,
        item_template_repo: AbstractItemTemplateRepository,
        gacha_repo: AbstractGachaRepository,
        shop_repo: AbstractShopRepository,
        db_path: str,
    ):
        self.gacha_repo = gacha_repo
        self.item_template_repo = item_template_repo
        self.shop_repo = shop_repo
        self.db_path = db_path
        self.seed_sql_path = (
            Path(__file__).resolve().parent.parent / "database" / "seeds" / "initial_seed.sql"
        )

    def setup_initial_data(self):
        """
        检查核心种子表是否为空，如果为空则灌入内置基线数据。
        这是一个幂等操作，可以安全地重复执行。
        """
        try:
            if self._has_seeded_core_data():
                logger.info("数据库核心数据已存在，跳过初始化。")
                return
        except Exception as e:
            logger.error(f"检查核心种子数据时发生错误，将继续初始化: {e}")

        logger.info("检测到数据库为空或核心数据不完整，正在注入内置种子数据...")
        self._apply_seed_sql()
        logger.info("核心游戏数据初始化完成。")

    def sync_shops_from_initial_data(self):
        """兼容旧入口，改为同步内置种子数据。"""
        logger.info("正在同步内置种子数据（兼容旧商店同步入口）...")
        self._apply_seed_sql()
        logger.info("内置种子数据同步完成。")

    def sync_all_initial_data(self):
        """手动同步所有内置种子数据。"""
        logger.info("--- 开始同步所有内置种子数据 ---")
        self._apply_seed_sql()
        logger.info("--- 所有内置种子数据同步完成 ---")

    def create_initial_items(self):
        """兼容旧入口，改为执行统一种子同步。"""
        logger.info("正在通过统一种子数据补齐初始道具与配置...")
        self._apply_seed_sql()

    def _has_seeded_core_data(self) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM fish LIMIT 1")
            return cursor.fetchone() is not None

    def _apply_seed_sql(self) -> None:
        if not self.seed_sql_path.exists():
            raise FileNotFoundError(f"种子文件不存在: {self.seed_sql_path}")

        sql = self.seed_sql_path.read_text(encoding="utf-8")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(sql)
            conn.commit()
