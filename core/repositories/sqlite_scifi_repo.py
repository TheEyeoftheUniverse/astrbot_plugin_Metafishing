from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from ..database.connection_manager import DatabaseConnectionManager


def _row_to_dict(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class SqliteSciFiRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cm = DatabaseConnectionManager(db_path)

    def ensure_state(self, user_id: str, now_iso: str) -> Dict[str, Any]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO user_scifi_state (
                    user_id, created_at, updated_at
                ) VALUES (?, ?, ?)
                """,
                (user_id, now_iso, now_iso),
            )
            conn.commit()
        return self.get_state(user_id)

    def get_state(self, user_id: str) -> Dict[str, Any]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM user_scifi_state WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"scifi state not found for {user_id}")
            return _row_to_dict(row) or {}

    def update_state_fields(self, user_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields.keys())
        params = list(fields.values()) + [user_id]
        with self._cm.get_connection() as conn:
            conn.execute(
                f"UPDATE user_scifi_state SET {assignments} WHERE user_id = ?",
                params,
            )
            conn.commit()

    def add_research_points(
        self,
        user_id: str,
        amount: int,
        now_iso: str,
    ) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                UPDATE user_scifi_state
                SET research_points = research_points + ?,
                    total_research_points_earned = total_research_points_earned + ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (int(amount), int(amount), now_iso, user_id),
            )
            conn.commit()

    def increment_append_triggered(self, user_id: str, delta: int, now_iso: str) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                UPDATE user_scifi_state
                SET total_append_triggered = total_append_triggered + ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (int(delta), now_iso, user_id),
            )
            conn.commit()

    def try_level_up(
        self,
        user_id: str,
        field_name: str,
        current_level: int,
        next_level: int,
        cost: int,
        now_iso: str,
    ) -> bool:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE user_scifi_state
                SET research_points = research_points - ?,
                    {field_name} = ?,
                    updated_at = ?
                WHERE user_id = ?
                  AND {field_name} = ?
                  AND research_points >= ?
                """,
                (int(cost), int(next_level), now_iso, user_id, int(current_level), int(cost)),
            )
            conn.commit()
            return cur.rowcount > 0

    def insert_event_log(
        self,
        user_id: str,
        event_type: str,
        event_detail: Optional[Dict[str, Any]],
        occurred_at: str,
    ) -> None:
        payload = json.dumps(event_detail or {}, ensure_ascii=False)
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO scifi_event_log (user_id, event_type, event_detail, occurred_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, event_type, payload, occurred_at),
            )
            conn.commit()

    def list_event_logs(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM scifi_event_log
                WHERE user_id = ?
                ORDER BY occurred_at DESC, log_id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = cur.fetchall()

        result: List[Dict[str, Any]] = []
        for row in rows:
            item = _row_to_dict(row) or {}
            try:
                item["event_detail"] = json.loads(item.get("event_detail") or "{}")
            except Exception:
                item["event_detail"] = {}
            result.append(item)
        return result

    def get_leaderboard(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    s.user_id,
                    COALESCE(u.nickname, s.user_id) AS nickname,
                    s.research_points,
                    s.total_research_points_earned,
                    s.total_append_triggered,
                    s.apex_protocol,
                    s.abyss_compression_level,
                    s.fate_severance_level,
                    s.resonance_dampening_level,
                    (
                        s.abyss_compression_level
                        + s.fate_severance_level
                        + s.resonance_dampening_level
                    ) AS total_level
                FROM user_scifi_state s
                LEFT JOIN users u ON u.user_id = s.user_id
                ORDER BY total_level DESC, s.total_research_points_earned DESC, s.updated_at ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [_row_to_dict(row) or {} for row in cur.fetchall()]
