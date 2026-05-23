from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from ..database.connection_manager import DatabaseConnectionManager


def _row_to_dict(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _load_json_field(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class SqliteCthulhuRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cm = DatabaseConnectionManager(db_path)

    def ensure_state(self, user_id: str) -> Dict[str, Any]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO user_cthulhu_state(user_id, current_san, max_san) VALUES (?, 50, 50)",
                (user_id,),
            )
            conn.commit()
        return self.get_state(user_id)

    def bootstrap_states_for_all_users(self) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO user_cthulhu_state(user_id, current_san, max_san)
                SELECT user_id, 50, 50 FROM users
                """
            )
            conn.commit()
            return cur.rowcount

    def get_state(self, user_id: str) -> Dict[str, Any]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM user_cthulhu_state WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"cthulhu state not found for {user_id}")
            data = _row_to_dict(row) or {}
            data["pending_predict_candidates"] = _load_json_field(
                data.get("pending_predict_candidates"),
                [],
            )
            data["pending_event_snapshot"] = _load_json_field(
                data.get("pending_event_snapshot"),
                None,
            )
            return data

    def update_state_fields(self, user_id: str, **fields: Any) -> None:
        if not fields:
            return
        normalized = dict(fields)
        if "pending_predict_candidates" in normalized:
            normalized["pending_predict_candidates"] = json.dumps(
                normalized["pending_predict_candidates"] or [],
                ensure_ascii=False,
            )
        if "pending_event_snapshot" in normalized:
            snapshot = normalized["pending_event_snapshot"]
            normalized["pending_event_snapshot"] = (
                json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None
            )
        assignments = ", ".join(f"{key} = ?" for key in normalized.keys())
        params = list(normalized.values()) + [user_id]
        with self._cm.get_connection() as conn:
            conn.execute(
                f"UPDATE user_cthulhu_state SET {assignments} WHERE user_id = ?",
                params,
            )
            conn.commit()

    def get_pending_event_states(self) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM user_cthulhu_state WHERE pending_event_id IS NOT NULL"
            )
            rows = cur.fetchall()
            result = []
            for row in rows:
                data = _row_to_dict(row) or {}
                data["pending_predict_candidates"] = _load_json_field(
                    data.get("pending_predict_candidates"),
                    [],
                )
                data["pending_event_snapshot"] = _load_json_field(
                    data.get("pending_event_snapshot"),
                    None,
                )
                result.append(data)
            return result

    def reset_daily_flags(self, reset_at: str) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                UPDATE user_cthulhu_state
                SET is_in_deepdive_today = 0,
                    pending_event_id = NULL,
                    pending_event_tier = NULL,
                    pending_event_force_pollute = 0,
                    pending_event_choice = NULL,
                    last_daily_reset_at = ?,
                    pending_event_snapshot = NULL,
                    pending_predict_candidates = NULL,
                    pending_predict_expires_at = NULL
                """,
                (reset_at,),
            )
            conn.commit()

    def recover_all_users_san(self, amount: int) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                UPDATE user_cthulhu_state
                SET current_san = MIN(max_san, current_san + ?)
                """,
                (int(amount),),
            )
            conn.commit()

    def clear_expired_forced_pollution(self, now_text: str) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                UPDATE user_cthulhu_state
                SET forced_pollution_until = NULL
                WHERE forced_pollution_until IS NOT NULL
                  AND forced_pollution_until <= ?
                """,
                (now_text,),
            )
            conn.commit()

    def insert_true_name(
        self,
        name_string: str,
        god_type: str,
        tier: str,
        threshold: int,
        owner_user_id: str,
        created_at: str,
        status: str = "in_inventory",
    ) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO true_names
                (name_string, god_type, tier, threshold, progress, status, owner_user_id, created_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (name_string, god_type, tier, int(threshold), status, owner_user_id, created_at),
            )
            conn.commit()
            return int(cur.lastrowid)

    def true_name_exists(self, name_string: str) -> bool:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM true_names WHERE name_string = ?", (name_string,))
            return cur.fetchone() is not None

    def count_inventory_true_names(self, user_id: str) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM true_names WHERE owner_user_id = ? AND status = 'in_inventory'",
                (user_id,),
            )
            return int(cur.fetchone()[0])

    def delete_oldest_inventory_true_name(self, user_id: str) -> None:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM true_names
                WHERE name_id = (
                    SELECT name_id FROM true_names
                    WHERE owner_user_id = ? AND status = 'in_inventory'
                    ORDER BY created_at ASC, name_id ASC
                    LIMIT 1
                )
                """,
                (user_id,),
            )
            conn.commit()

    def delete_inventory_true_name_by_id(self, name_id: int) -> None:
        """删除指定 name_id 的真名（呼唤完成后从背包移除）。"""
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM true_names WHERE name_id = ?", (name_id,))
            conn.commit()

    def get_true_name(self, name_id: int) -> Optional[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM true_names WHERE name_id = ?", (int(name_id),))
            return _row_to_dict(cur.fetchone())

    def get_calling_name_by_string(self, name_string: str) -> Optional[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM true_names WHERE name_string = ? AND status = 'calling'",
                (name_string,),
            )
            return _row_to_dict(cur.fetchone())

    def list_true_names(self, user_id: str) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM true_names
                WHERE owner_user_id = ?
                ORDER BY created_at DESC, name_id DESC
                """,
                (user_id,),
            )
            return [_row_to_dict(row) for row in cur.fetchall()]

    def list_active_calls(self) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT tn.*, u.nickname AS owner_nickname
                FROM true_names tn
                LEFT JOIN users u ON u.user_id = tn.owner_user_id
                WHERE tn.status = 'calling'
                ORDER BY tn.called_at ASC, tn.name_id ASC
                """
            )
            return [_row_to_dict(row) for row in cur.fetchall()]

    def start_calling(self, name_id: int, owner_user_id: str, called_at: str) -> bool:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE true_names
                SET status = 'calling', called_at = ?
                WHERE name_id = ? AND owner_user_id = ? AND status = 'in_inventory'
                """,
                (called_at, int(name_id), owner_user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def consume_true_name(self, name_id: int, consumed_at: str) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                UPDATE true_names
                SET status = 'consumed', consumed_at = ?
                WHERE name_id = ?
                """,
                (consumed_at, int(name_id)),
            )
            conn.commit()

    def record_vote(self, name_id: int, voter_user_id: str, voted_at: str) -> Dict[str, Any]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "SELECT name_id, progress, threshold, status FROM true_names WHERE name_id = ?",
                (int(name_id),),
            )
            row = cur.fetchone()
            if row is None or row["status"] != "calling":
                conn.rollback()
                raise ValueError("name_not_calling")
            cur.execute(
                """
                INSERT INTO true_name_votes(name_id, voter_user_id, voted_at)
                VALUES (?, ?, ?)
                """,
                (int(name_id), voter_user_id, voted_at),
            )
            cur.execute(
                "UPDATE true_names SET progress = progress + 1 WHERE name_id = ?",
                (int(name_id),),
            )
            cur.execute(
                "SELECT name_id, progress, threshold, status FROM true_names WHERE name_id = ?",
                (int(name_id),),
            )
            updated = cur.fetchone()
            conn.commit()
            return _row_to_dict(updated) or {}

    def get_top_voters(self, name_id: int, limit: int) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT voter_user_id AS user_id, COUNT(*) AS vote_count, MIN(voted_at) AS first_vote_at
                FROM true_name_votes
                WHERE name_id = ?
                GROUP BY voter_user_id
                ORDER BY vote_count DESC, first_vote_at ASC, user_id ASC
                LIMIT ?
                """,
                (int(name_id), int(limit)),
            )
            return [_row_to_dict(row) for row in cur.fetchall()]

    def get_authority(self, authority_id: str) -> Optional[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ca.*, cu.nickname AS current_holder_nickname, pu.nickname AS previous_holder_nickname
                FROM cthulhu_authority ca
                LEFT JOIN users cu ON cu.user_id = ca.current_holder
                LEFT JOIN users pu ON pu.user_id = ca.previous_holder
                WHERE authority_id = ?
                """,
                (authority_id,),
            )
            return _row_to_dict(cur.fetchone())

    def list_authorities(self) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ca.*, cu.nickname AS current_holder_nickname, pu.nickname AS previous_holder_nickname
                FROM cthulhu_authority ca
                LEFT JOIN users cu ON cu.user_id = ca.current_holder
                LEFT JOIN users pu ON pu.user_id = ca.previous_holder
                ORDER BY ca.god_type, ca.tier
                """
            )
            return [_row_to_dict(row) for row in cur.fetchall()]

    def list_authorities_for_holder(self, user_id: str) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM cthulhu_authority
                WHERE current_holder = ?
                ORDER BY god_type, tier
                """,
                (user_id,),
            )
            return [_row_to_dict(row) for row in cur.fetchall()]

    def transfer_authority(self, authority_id: str, new_holder: str, acquired_at: str) -> None:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE cthulhu_authority
                SET previous_holder = current_holder,
                    previous_acquired_at = acquired_at,
                    current_holder = ?,
                    acquired_at = ?
                WHERE authority_id = ?
                """,
                (new_holder, acquired_at, authority_id),
            )
            conn.commit()

    def list_active_pollution_ids(self) -> List[str]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT pollution_id FROM cthulhu_global_pollution WHERE activated_at IS NOT NULL ORDER BY pollution_id"
            )
            return [row[0] for row in cur.fetchall()]

    def activate_random_inactive_pollution(self, triggered_by_name_id: int, activated_at: str) -> Optional[str]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT pollution_id FROM cthulhu_global_pollution WHERE activated_at IS NULL ORDER BY RANDOM() LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                return None
            pollution_id = row["pollution_id"]
            cur.execute(
                """
                UPDATE cthulhu_global_pollution
                SET activated_at = ?, triggered_by_name_id = ?
                WHERE pollution_id = ?
                """,
                (activated_at, int(triggered_by_name_id), pollution_id),
            )
            conn.commit()
            return pollution_id

    def get_signed_in_user_ids(self, marker_text: str) -> List[str]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id FROM check_ins WHERE check_in_date = ?",
                (marker_text,),
            )
            return [row[0] for row in cur.fetchall()]

    def get_all_user_ids(self) -> List[str]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM users")
            return [row[0] for row in cur.fetchall()]

    def insert_event_log(
        self,
        user_id: str,
        event_id: Optional[str],
        choice_id: Optional[str],
        is_main_roll: bool,
        d100_roll: int,
        result: str,
        san_delta: int,
        granted_name_id: Optional[int],
        occurred_at: str,
    ) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO cthulhu_event_log
                (user_id, event_id, choice_id, is_main_roll, d100_roll, result, san_delta, granted_name_id, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    event_id,
                    choice_id,
                    1 if is_main_roll else 0,
                    int(d100_roll),
                    result,
                    int(san_delta),
                    granted_name_id,
                    occurred_at,
                ),
            )
            conn.commit()

    def list_event_logs(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM cthulhu_event_log
                WHERE user_id = ?
                ORDER BY occurred_at DESC, log_id DESC
                LIMIT ?
                """,
                (user_id, int(limit)),
            )
            return [_row_to_dict(row) for row in cur.fetchall()]
