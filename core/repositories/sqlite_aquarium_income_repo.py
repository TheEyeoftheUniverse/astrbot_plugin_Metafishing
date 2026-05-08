"""水族箱展览收益（待领取记录）仓储 — SQLite 实现。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .abstract_repository import AbstractAquariumIncomeRepository
from ..database.connection_manager import DatabaseConnectionManager


class SqliteAquariumIncomeRepository(AbstractAquariumIncomeRepository):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._connection_manager = DatabaseConnectionManager(db_path)

    def upsert_pending(
        self,
        user_id: str,
        window_date: str,
        window_time: str,
        raw_score: int,
        equipment_multiplier: float,
        randomness: float,
        computed_amount: int,
        capped_amount: int,
        fish_snapshot_json: str,
        created_at: str,
    ) -> bool:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO aquarium_income_pending (
                    user_id, window_date, window_time,
                    raw_score, equipment_multiplier, randomness,
                    computed_amount, capped_amount,
                    fish_snapshot, created_at, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    user_id,
                    window_date,
                    window_time,
                    int(raw_score),
                    float(equipment_multiplier),
                    float(randomness),
                    int(computed_amount),
                    int(capped_amount),
                    fish_snapshot_json,
                    created_at,
                ),
            )
            inserted = cursor.rowcount > 0
            conn.commit()
            return inserted

    def get_pending(self, user_id: str) -> List[Dict[str, Any]]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, window_date, window_time, raw_score,
                       equipment_multiplier, randomness, computed_amount,
                       capped_amount, fish_snapshot, created_at
                FROM aquarium_income_pending
                WHERE user_id = ? AND claimed_at IS NULL
                ORDER BY window_date ASC, window_time ASC
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "user_id": row[0],
                    "window_date": row[1],
                    "window_time": row[2],
                    "raw_score": row[3],
                    "equipment_multiplier": row[4],
                    "randomness": row[5],
                    "computed_amount": row[6],
                    "capped_amount": row[7],
                    "fish_snapshot": row[8],
                    "created_at": row[9],
                }
                for row in rows
            ]

    def has_window(self, user_id: str, window_date: str, window_time: str) -> bool:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM aquarium_income_pending
                WHERE user_id = ? AND window_date = ? AND window_time = ?
                LIMIT 1
                """,
                (user_id, window_date, window_time),
            )
            return cursor.fetchone() is not None

    def get_daily_claimed_total(self, user_id: str, window_date: str) -> int:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COALESCE(SUM(capped_amount), 0)
                FROM aquarium_income_pending
                WHERE user_id = ? AND window_date = ? AND claimed_at IS NOT NULL
                """,
                (user_id, window_date),
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def mark_claimed(
        self,
        record_keys: List[Tuple[str, str, str]],
        claimed_at: str,
    ) -> int:
        if not record_keys:
            return 0
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            affected = 0
            for user_id, window_date, window_time in record_keys:
                cursor.execute(
                    """
                    UPDATE aquarium_income_pending
                    SET claimed_at = ?
                    WHERE user_id = ? AND window_date = ? AND window_time = ?
                      AND claimed_at IS NULL
                    """,
                    (claimed_at, user_id, window_date, window_time),
                )
                affected += cursor.rowcount
            conn.commit()
            return affected

    def cleanup_old(self, before_date: str) -> int:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM aquarium_income_pending
                WHERE window_date < ? AND claimed_at IS NOT NULL
                """,
                (before_date,),
            )
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    def get_distinct_active_aquarium_fish_ids(self, min_rarity: int) -> List[int]:
        with self._connection_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT ua.fish_id
                FROM user_aquarium ua
                JOIN fish f ON ua.fish_id = f.fish_id
                WHERE ua.quantity > 0 AND f.rarity >= ?
                ORDER BY ua.fish_id ASC
                """,
                (int(min_rarity),),
            )
            return [int(row[0]) for row in cursor.fetchall()]
