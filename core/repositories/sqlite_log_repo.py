import sqlite3
import threading
from typing import Optional, List, Dict
from datetime import date, datetime, timedelta, timezone
# 导入抽象基类和领域模型
from .abstract_repository import AbstractLogRepository
from ..domain.models import (
    FishingRecord,
    TaxRecord,
    UserFishStat,
    PokedexRewardClaim,
)
from ..database.sqlite_utils import connect_sqlite, clamp_sqlite_int

class SqliteLogRepository(AbstractLogRepository):
    """日志类数据仓储的SQLite实现"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        # 定义UTC+8时区
        self.UTC8 = timezone(timedelta(hours=8))

    def _get_connection(self) -> sqlite3.Connection:
        """获取一个线程安全的数据库连接。"""
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = connect_sqlite(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES,
                row_factory=sqlite3.Row,
            )
            self._local.connection = conn
        return conn

    # --- 私有映射辅助方法 ---
    def _row_to_user_fish_stat(self, row: sqlite3.Row) -> Optional[UserFishStat]:
        if not row:
            return None
        data = dict(row)
        return UserFishStat(
            user_id=data["user_id"],
            fish_id=data["fish_id"],
            first_caught_at=data.get("first_caught_at"),
            last_caught_at=data.get("last_caught_at"),
            total_caught=data["total_caught"],
        )

    def _row_to_pokedex_reward_claim(self, row: sqlite3.Row) -> Optional[PokedexRewardClaim]:
        if not row:
            return None
        data = dict(row)
        return PokedexRewardClaim(
            user_id=data["user_id"],
            milestone_percent=data["milestone_percent"],
            reward_premium=data["reward_premium"],
            claimed_unlocked_fish_count=data["claimed_unlocked_fish_count"],
            claimed_total_fish_count=data["claimed_total_fish_count"],
            claimed_at=data.get("claimed_at"),
            reward_type=data.get("reward_type", "premium"),
            reward_amount=data.get("reward_amount") or data["reward_premium"],
        )

    def _row_to_tax_record(self, row: sqlite3.Row) -> Optional[TaxRecord]:
        if not row:
            return None
        return TaxRecord(**row)

    # --- Fishing Log Methods ---
    def add_fishing_record(self, record: FishingRecord, log_to_records: bool = True) -> bool:
        # 仅更新图鉴聚合统计；详细钓鱼流水已移除。
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_ts = record.timestamp or datetime.now(self.UTC8)
            cursor.execute(
                """
                INSERT INTO user_fish_stats (
                    user_id, fish_id, first_caught_at, last_caught_at, total_caught
                ) VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(user_id, fish_id) DO UPDATE SET
                    last_caught_at = excluded.last_caught_at,
                    total_caught = total_caught + 1
                """,
                (
                    record.user_id,
                    record.fish_id,
                    now_ts,
                    now_ts,
                ),
            )

            conn.commit()
            return True


    def get_unlocked_fish_ids(self, user_id: str) -> Dict[int, datetime]:
        """
        获取指定用户所有钓到过的鱼类ID集合，以及对应的首次捕获时间。

        返回:
            Dict[int, datetime]: 键为鱼类ID，值为首次捕获时间
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT fish_id, first_caught_at as first_caught_time
                FROM user_fish_stats
                WHERE user_id = ?
            """, (user_id,))
            rows = cursor.fetchall()
            return {row["fish_id"]: row["first_caught_time"] for row in rows}

    def cleanup_expired_records(self, retention_days: int = 30, per_user_limit: int = 50) -> Dict[str, int]:
        cutoff_time = datetime.now(self.UTC8) - timedelta(days=retention_days)
        cutoff_date = cutoff_time.date()
        results: Dict[str, int] = {}

        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                DELETE FROM check_ins
                WHERE check_in_date < ?
                """,
                (cutoff_date,),
            )
            results["check_ins_expired"] = cursor.rowcount

            cursor.execute(
                f"""
                DELETE FROM check_ins
                WHERE rowid IN (
                    SELECT rowid FROM (
                        SELECT rowid,
                               ROW_NUMBER() OVER (
                                   PARTITION BY user_id
                                   ORDER BY check_in_date DESC
                               ) AS row_num
                        FROM check_ins
                    ) ranked
                    WHERE row_num > {per_user_limit}
                )
                """
            )
            results["check_ins_trimmed"] = cursor.rowcount

            conn.commit()

        return results

    # --- Check-in Log Methods ---
    def add_check_in(self, user_id: str, check_in_date: date) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1) 写入签到记录
            cursor.execute(
                "INSERT INTO check_ins (user_id, check_in_date) VALUES (?, ?)",
                (user_id, check_in_date),
            )

            conn.commit()

    def has_checked_in(self, user_id: str, check_in_date: date) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM check_ins WHERE user_id = ? AND check_in_date = ?",
                (user_id, check_in_date)
            )
            return cursor.fetchone() is not None

    def add_log(self, user_id: str, log_type: str, message: str) -> None:
        """兼容旧调用；当前不再持久化通用日志。"""
        return None

    # --- Tax Log Methods ---
    def add_tax_record(self, record: TaxRecord) -> None:
        tax_amount = clamp_sqlite_int(record.tax_amount)
        original_amount = clamp_sqlite_int(record.original_amount)
        balance_after = clamp_sqlite_int(record.balance_after)
        if (
            tax_amount != record.tax_amount
            or original_amount != record.original_amount
            or balance_after != record.balance_after
        ):
            logger.warning(
                "税收记录超出 SQLite INTEGER 范围，已截断 user_id=%s tax=%s original=%s balance=%s",
                record.user_id,
                tax_amount,
                original_amount,
                balance_after,
            )

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1) 写入税收记录
            cursor.execute(
                """
                INSERT INTO taxes
                    (user_id, tax_amount, tax_rate, original_amount, balance_after, tax_type, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.user_id,
                    tax_amount,
                    record.tax_rate,
                    original_amount,
                    balance_after,
                    record.tax_type,
                    record.timestamp or datetime.now(self.UTC8),
                ),
            )

            # 2) 仅保留当前用户最近税收记录
            # 策略：优先保留每日资产税记录（重要），剩余空间保留最近的其他税收记录
            cutoff_time = datetime.now(self.UTC8) - timedelta(days=30)
            cursor.execute(
                """
                DELETE FROM taxes
                WHERE user_id = ?
                  AND tax_id NOT IN (
                    -- 保留所有30天内的每日资产税（核心记录，必须保留）
                    SELECT tax_id FROM taxes
                    WHERE user_id = ?
                      AND tax_type = '每日资产税'
                      AND timestamp >= ?
                    UNION
                    -- 保留最近50条其他税收记录
                    SELECT tax_id FROM (
                        SELECT tax_id, timestamp
                        FROM taxes
                        WHERE user_id = ?
                          AND tax_type != '每日资产税'
                        ORDER BY timestamp DESC, tax_id DESC
                        LIMIT 50
                    )
                  )
                """,
                (record.user_id, record.user_id, cutoff_time, record.user_id),
            )

            # 3) 清理30天前的税收记录（全局）
            cursor.execute(
                """
                DELETE FROM taxes
                WHERE timestamp < ?
                """,
                (cutoff_time,),
            )

            conn.commit()

    def get_tax_records(self, user_id: str, limit: int = 10) -> List[TaxRecord]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM taxes
                WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
            """, (user_id, limit))
            return [self._row_to_tax_record(row) for row in cursor.fetchall()]
    
    def has_daily_tax_today(self, reset_hour: int = 0) -> bool:
        """检查今天是否已经执行过每日资产税"""
        from ..utils import get_last_reset_time
        last_reset = get_last_reset_time(reset_hour)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM taxes
                WHERE tax_type = '每日资产税'
                AND timestamp >= ?
            """, (last_reset,))
            result = cursor.fetchone()
            return result[0] > 0 if result else False
    
    def has_user_daily_tax_today(self, user_id: str, reset_hour: int = 0) -> bool:
        """检查某个用户今天是否已经被征收过每日资产税"""
        from ..utils import get_last_reset_time
        last_reset = get_last_reset_time(reset_hour)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM taxes
                WHERE user_id = ?
                AND tax_type = '每日资产税'
                AND timestamp >= ?
            """, (user_id, last_reset))
            result = cursor.fetchone()
            return result[0] > 0 if result else False

    # --- 用户鱼类统计（用于图鉴与个人纪录） ---
    def get_user_fish_stats(self, user_id: str) -> List[UserFishStat]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, fish_id, first_caught_at, last_caught_at,
                       total_caught
                FROM user_fish_stats
                WHERE user_id = ?
                ORDER BY last_caught_at DESC
                """,
                (user_id,),
            )
            return [self._row_to_user_fish_stat(row) for row in cursor.fetchall()]

    def get_user_fish_stat(self, user_id: str, fish_id: int) -> Optional[UserFishStat]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, fish_id, first_caught_at, last_caught_at,
                       total_caught
                FROM user_fish_stats
                WHERE user_id = ? AND fish_id = ?
                LIMIT 1
                """,
                (user_id, fish_id),
            )
            row = cursor.fetchone()
            return self._row_to_user_fish_stat(row) if row else None

    def get_user_pokedex_reward_claims(self, user_id: str) -> List[PokedexRewardClaim]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, milestone_percent, reward_premium, reward_type, reward_amount,
                       claimed_unlocked_fish_count, claimed_total_fish_count, claimed_at
                FROM user_pokedex_reward_claims
                WHERE user_id = ?
                ORDER BY milestone_percent ASC
                """,
                (user_id,),
            )
            return [self._row_to_pokedex_reward_claim(row) for row in cursor.fetchall()]

    def claim_pokedex_reward(
        self,
        user_id: str,
        milestone_percent: int,
        reward_type: str,
        reward_amount: int,
        unlocked_fish_count: int,
        total_fish_count: int,
    ) -> bool:
        claimed_at = datetime.now(self.UTC8)
        reward_premium = reward_amount if reward_type == "premium" else 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO user_pokedex_reward_claims (
                        user_id,
                        milestone_percent,
                        reward_premium,
                        reward_type,
                        reward_amount,
                        claimed_unlocked_fish_count,
                        claimed_total_fish_count,
                        claimed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        milestone_percent,
                        reward_premium,
                        reward_type,
                        reward_amount,
                        unlocked_fish_count,
                        total_fish_count,
                        claimed_at,
                    ),
                )
                if cursor.rowcount == 0:
                    conn.rollback()
                    return False

                if reward_type == "coins":
                    cursor.execute(
                        """
                        UPDATE users
                        SET coins = coins + ?,
                            max_coins = MAX(max_coins, coins + ?)
                        WHERE user_id = ?
                        """,
                        (reward_amount, reward_amount, user_id),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE users
                        SET premium_currency = premium_currency + ?
                        WHERE user_id = ?
                        """,
                        (reward_amount, user_id),
                    )
                if cursor.rowcount == 0:
                    conn.rollback()
                    return False

                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise
