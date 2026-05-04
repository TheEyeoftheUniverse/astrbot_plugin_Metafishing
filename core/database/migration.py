import sqlite3
import os
import re
import importlib.util

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


def _load_migration_module(migrations_dir: str, filename: str):
    module_path = os.path.join(migrations_dir, filename)
    module_name = f"astrbot_plugin_fishing_migration_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载迁移文件: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema_latest_table_names(migrations_dir: str) -> set[str]:
    schema_path = os.path.join(os.path.dirname(migrations_dir), "schema_latest.sql")
    if not os.path.exists(schema_path):
        return set()

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema_sql = schema_file.read()

    return {
        match.group(1)
        for match in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?",
            schema_sql,
            flags=re.IGNORECASE,
        )
    }


def _database_matches_latest_schema(cursor: sqlite3.Cursor, migrations_dir: str) -> bool:
    required_tables = _schema_latest_table_names(migrations_dir)
    if not required_tables:
        return False

    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    existing_tables = {row[0] for row in cursor.fetchall()}
    missing_tables = sorted(required_tables - existing_tables)
    if missing_tables:
        logger.debug(f"数据库尚未满足最新基线结构，缺少表: {missing_tables}")
        return False

    return True


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

        if current_version != latest_version and _database_matches_latest_schema(cursor, migrations_dir):
            set_version(cursor, latest_version)
            conn.commit()
            logger.warning(
                "数据库结构已符合最新基线，已将 schema_version "
                f"从 {current_version} 对齐到 {latest_version}，跳过旧迁移。"
            )
            return

        conn.commit()

    if current_version > latest_version:
        raise RuntimeError(
            f"数据库版本 {current_version} 高于代码迁移版本 {latest_version}，请确认插件代码与数据库匹配。"
        )

    for filename in migration_files:
        version = int(filename.split("_")[0])
        if version > current_version:
            logger.info(f"正在应用迁移脚本: {filename}...")
            try:
                migration_module = _load_migration_module(migrations_dir, filename)

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
                logger.error(f"加载或执行迁移失败: {filename}。错误: {e}")
                raise
