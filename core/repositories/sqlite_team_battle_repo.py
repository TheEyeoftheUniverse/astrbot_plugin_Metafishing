"""魔幻团战玩法 V2：SQLite 仓储实现。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from ..database.connection_manager import DatabaseConnectionManager


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load_json(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


class SqliteTeamBattleRepository:
    """魔幻团战数据访问。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cm = DatabaseConnectionManager(db_path)

    # ------------------------------------------------------------------
    # Boss
    # ------------------------------------------------------------------
    def get_active_boss(self) -> Optional[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM team_battle_boss WHERE is_active = 1 "
                "ORDER BY spawned_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return None
            data = _row_to_dict(row)
            data["stages_triggered"] = _load_json(data.get("stages_triggered"), [])
            return data

    def get_boss_by_id(self, boss_id: int) -> Optional[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM team_battle_boss WHERE id = ?", (boss_id,))
            row = cur.fetchone()
            if not row:
                return None
            data = _row_to_dict(row)
            data["stages_triggered"] = _load_json(data.get("stages_triggered"), [])
            return data

    def insert_boss(
        self,
        region_key: str,
        boss_name: str,
        fish_id: int,
        boss_star: int,
        max_hp: int,
        spawned_at: str,
        image_path: Optional[str] = None,
        intro_story: Optional[str] = None,
        intro_quote: Optional[str] = None,
    ) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO team_battle_boss
                  (region_key, boss_name, fish_id, boss_star, max_hp, current_hp,
                   image_path, intro_story, intro_quote, stages_triggered,
                   spawned_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, 1)
                """,
                (
                    region_key, boss_name, fish_id, boss_star, max_hp, max_hp,
                    image_path, intro_story, intro_quote, spawned_at,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_boss_hp(self, boss_id: int, new_hp: int) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                "UPDATE team_battle_boss SET current_hp = ? WHERE id = ?",
                (new_hp, boss_id),
            )
            conn.commit()

    def update_boss_stages(self, boss_id: int, stages: List[str]) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                "UPDATE team_battle_boss SET stages_triggered = ? WHERE id = ?",
                (_dump_json(stages), boss_id),
            )
            conn.commit()

    def update_boss_image(self, boss_id: int, image_path: Optional[str]) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                "UPDATE team_battle_boss SET image_path = ? WHERE id = ?",
                (image_path, boss_id),
            )
            conn.commit()

    def mark_boss_killed(self, boss_id: int, killed_at: str) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                "UPDATE team_battle_boss SET current_hp = 0, killed_at = ?, "
                "is_active = 0 WHERE id = ?",
                (killed_at, boss_id),
            )
            conn.commit()

    def deactivate_boss(self, boss_id: int) -> None:
        """管理员重置：把当前 Boss 标记为失效（不写 killed_at）。"""
        with self._cm.get_connection() as conn:
            conn.execute(
                "UPDATE team_battle_boss SET is_active = 0 WHERE id = ?",
                (boss_id,),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Damage
    # ------------------------------------------------------------------
    def add_damage(
        self,
        boss_id: int,
        user_id: str,
        delta: int,
        is_leader: bool,
        settled_at: str,
    ) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO team_battle_damage
                  (boss_id, user_id, total_damage, last_settled_at, is_leader_at_last_settle)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(boss_id, user_id) DO UPDATE SET
                  total_damage = total_damage + excluded.total_damage,
                  last_settled_at = excluded.last_settled_at,
                  is_leader_at_last_settle = excluded.is_leader_at_last_settle
                """,
                (boss_id, user_id, int(delta), settled_at, 1 if is_leader else 0),
            )
            conn.commit()

    def get_damage_rank(
        self, boss_id: int, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        sql = (
            "SELECT user_id, total_damage, last_settled_at, is_leader_at_last_settle "
            "FROM team_battle_damage WHERE boss_id = ? "
            "ORDER BY total_damage DESC, user_id ASC"
        )
        params: List[Any] = [boss_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(sql, params)
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get_player_damage(
        self, boss_id: int, user_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM team_battle_damage WHERE boss_id = ? AND user_id = ?",
                (boss_id, user_id),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None

    def clear_damage(self, boss_id: int) -> None:
        with self._cm.get_connection() as conn:
            conn.execute("DELETE FROM team_battle_damage WHERE boss_id = ?", (boss_id,))
            conn.commit()

    # ------------------------------------------------------------------
    # Reward inventory
    # ------------------------------------------------------------------
    def insert_reward(
        self,
        user_id: str,
        boss_id: int,
        reward_type: str,
        quantity: int,
        source_stage: str,
        granted_at: str,
        item_id: Optional[int] = None,
        source_label: Optional[str] = None,
    ) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO team_battle_reward_inventory
                  (user_id, boss_id, reward_type, item_id, quantity,
                   source_stage, source_label, granted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, boss_id, reward_type, item_id, int(quantity),
                    source_stage, source_label, granted_at,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_unclaimed_rewards(self, user_id: str) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM team_battle_reward_inventory
                WHERE user_id = ? AND claimed_at IS NULL AND expired_at IS NULL
                ORDER BY granted_at ASC, id ASC
                """,
                (user_id,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

    def mark_rewards_claimed(self, reward_ids: List[int], claimed_at: str) -> int:
        if not reward_ids:
            return 0
        placeholders = ",".join("?" for _ in reward_ids)
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE team_battle_reward_inventory SET claimed_at = ? "
                f"WHERE id IN ({placeholders}) AND claimed_at IS NULL",
                [claimed_at, *reward_ids],
            )
            conn.commit()
            return cur.rowcount

    def expire_all_unclaimed(self, expired_at: str) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE team_battle_reward_inventory SET expired_at = ? "
                "WHERE claimed_at IS NULL AND expired_at IS NULL",
                (expired_at,),
            )
            conn.commit()
            return cur.rowcount

    def get_rewards_history(
        self, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM team_battle_reward_inventory
                WHERE user_id = ?
                ORDER BY granted_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, int(limit)),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # History kills (10 星)
    # ------------------------------------------------------------------
    def insert_history_kill(
        self,
        boss_id: int,
        region_key: str,
        boss_name: str,
        boss_star: int,
        finisher_user_id: Optional[str],
        final_rank_snapshot: List[Dict[str, Any]],
        killed_at: str,
    ) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO team_battle_history_kills
                  (boss_id, region_key, boss_name, boss_star,
                   finisher_user_id, final_rank_snapshot, killed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    boss_id, region_key, boss_name, int(boss_star),
                    finisher_user_id, _dump_json(final_rank_snapshot), killed_at,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_history_kills(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM team_battle_history_kills "
                "ORDER BY killed_at DESC, id DESC LIMIT ?",
                (int(limit),),
            )
            rows = cur.fetchall()
            results = []
            for row in rows:
                data = _row_to_dict(row)
                data["final_rank_snapshot"] = _load_json(
                    data.get("final_rank_snapshot"), []
                )
                results.append(data)
            return results

    # ------------------------------------------------------------------
    # Daily settle (幂等标记)
    # ------------------------------------------------------------------
    def is_daily_settled(self, daily_marker: str) -> bool:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM team_battle_daily_settle WHERE daily_marker = ?",
                (daily_marker,),
            )
            return cur.fetchone() is not None

    def mark_daily_settled(
        self,
        daily_marker: str,
        boss_id: Optional[int],
        settled_at: str,
        summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._cm.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO team_battle_daily_settle
                  (daily_marker, boss_id, settled_at, settle_summary)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(daily_marker) DO UPDATE SET
                  boss_id = excluded.boss_id,
                  settled_at = excluded.settled_at,
                  settle_summary = excluded.settle_summary
                """,
                (
                    daily_marker, boss_id, settled_at,
                    _dump_json(summary) if summary is not None else None,
                ),
            )
            conn.commit()

    def list_recent_settles(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._cm.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM team_battle_daily_settle "
                "ORDER BY daily_marker DESC LIMIT ?",
                (int(limit),),
            )
            results = []
            for row in cur.fetchall():
                data = _row_to_dict(row)
                data["settle_summary"] = _load_json(data.get("settle_summary"), {})
                results.append(data)
            return results

    # ------------------------------------------------------------------
    # 跨表查询便利方法
    # ------------------------------------------------------------------
    def get_signed_in_user_ids(self, daily_marker) -> List[str]:
        """读取在 daily_marker 当日完成签到的玩家 ID 列表。

        Args:
            daily_marker: ``datetime.date`` 对象，需与 ``log_repo.has_checked_in``
                所写入的 ``check_ins.check_in_date`` 字段格式一致。
        """
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT user_id FROM check_ins WHERE check_in_date = ?",
                (daily_marker,),
            )
            return [row[0] for row in cur.fetchall()]

    def get_users_in_zone(self, zone_id: int) -> List[str]:
        """读取当前位于指定钓鱼区域的玩家 ID 列表。"""
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id FROM users WHERE fishing_zone_id = ?",
                (int(zone_id),),
            )
            return [row[0] for row in cur.fetchall()]
