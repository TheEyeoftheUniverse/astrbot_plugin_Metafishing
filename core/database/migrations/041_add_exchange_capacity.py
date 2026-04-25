import sqlite3


def up(cursor: sqlite3.Cursor):
    """为用户表添加交易所容量字段。"""
    try:
        cursor.execute("""
            ALTER TABLE users ADD COLUMN exchange_capacity INTEGER DEFAULT 1000
        """)
        print("  - 已添加 users.exchange_capacity 字段")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            print("  - users.exchange_capacity 字段已存在，跳过")
        else:
            raise

    cursor.execute("""
        UPDATE users
        SET exchange_capacity = 1000
        WHERE exchange_capacity IS NULL OR exchange_capacity <= 0
    """)


def down(cursor: sqlite3.Cursor):
    """回滚交易所容量字段。"""
    # 注意：SQLite 不支持直接删除列，这里仅保留兼容注释。
    pass
