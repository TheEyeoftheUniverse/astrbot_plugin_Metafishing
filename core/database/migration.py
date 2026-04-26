import sqlite3
import os
import re
import importlib

from astrbot.api import logger


def get_current_version(cursor: sqlite3.Cursor) -> int:
    """获取当前数据库的版本号。"""
    try:
        cursor.execute("SELECT version FROM schema_version")
        result = cursor.fetchone()
        return result[0] if result else 0
    except sqlite3.OperationalError:
        return 0


def set_version(cursor: sqlite3.Cursor, version: int):
    """设置数据库的版本号。"""
    cursor.execute("UPDATE schema_version SET version = ?", (version,))


def _list_migration_files(migrations_dir: str) -> list[str]:
    return sorted(
        [f for f in os.listdir(migrations_dir) if f.endswith(".py") and re.match(r"^\d{3}_", f)],
        key=lambda f: int(f.split("_")[0]),
    )


def _apply_initial_setup(db_path: str, initial_migration: str, target_version: int) -> None:
    """
    新项目首装只执行 001 基线迁移，然后直接写入当前最新版本号。
    后续历史迁移文件保留为开发记录，但不再参与新库初始化。
    """
    module_name = f"data.plugins.astrbot_plugin_fishing.core.database.migrations.{initial_migration[:-3]}"
    migration_module = importlib.import_module(module_name)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN TRANSACTION")
            migration_module.up(cursor)
            set_version(cursor, target_version)
            conn.commit()
            logger.info(
                f"已通过 {initial_migration} 初始化最新基线 schema，并将版本号直接设置为 {target_version}。"
            )
        except Exception:
            conn.rollback()
            raise


def run_migrations(db_path: str, migrations_dir: str):
    """
    运行所有待处理的数据库迁移脚本。
    """
    try:
        migration_files = _list_migration_files(migrations_dir)
    except FileNotFoundError:
        logger.warning(f"迁移目录 '{migrations_dir}' 不存在，跳过迁移。")
        return

    latest_version = max((int(f.split("_")[0]) for f in migration_files), default=0)

    # 确保版本表存在
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL PRIMARY KEY)")
        cursor.execute("SELECT COUNT(*) FROM schema_version")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO schema_version (version) VALUES (0)")
            logger.info("schema_version 表已初始化。")
        current_version = get_current_version(cursor)
        logger.info(f"当前数据库版本: {current_version}")
        conn.commit()

    if current_version == 0 and migration_files:
        initial_migration = migration_files[0]
        logger.info(
            f"检测到新库初始化流程，执行 {initial_migration} 并跳过其余历史迁移回放（目标版本 {latest_version}）。"
        )
        _apply_initial_setup(db_path, initial_migration, latest_version)
        return

    for filename in migration_files:
        version = int(filename.split("_")[0])
        if version > current_version:
            logger.info(f"正在应用迁移脚本: {filename}...")
            try:
                module_name = f"data.plugins.astrbot_plugin_fishing.core.database.migrations.{filename[:-3]}"
                migration_module = importlib.import_module(module_name)

                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()

                    try:
                        cursor.execute("BEGIN TRANSACTION")
                        migration_module.up(cursor)
                        # 在同一个事务中更新版本号
                        set_version(cursor, version)
                        conn.commit()
                        logger.info(f"成功应用迁移: {filename}")
                    except Exception as e:
                        conn.rollback()
                        logger.error(f"应用迁移失败: {filename}。错误: {e}")
                        raise
            except Exception as e:
                logger.error(f"加载迁移模块失败: {module_name}。错误: {e}")
                raise
