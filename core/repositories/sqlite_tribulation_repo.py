"""玄幻渡劫玩法 V2：SQLite 仓储实现。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..database.connection_manager import DatabaseConnectionManager
from ..domain.tribulation_models import (
    CultivationProfile,
    TribulationEvent,
    TribulationParticipant,
    STATUS_PENDING,
    STATUS_ANNOUNCED,
    STATUS_FINISHED,
)


def _dump_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _load_json(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _row_to_profile(row) -> CultivationProfile:
    return CultivationProfile(
        user_id=row["user_id"],
        current_realm=row["current_realm"],
        current_realm_quality=row["current_realm_quality"],
        accumulated_xiuwei=int(row["accumulated_xiuwei"] or 0),
        consecutive_failures=int(row["consecutive_failures"] or 0),
        realm_history=_load_json(row["realm_history"], {}),
        tiancheng_protection=_load_json(row["tiancheng_protection"], {}),
        daily_observer_reward_count=int(row["daily_observer_reward_count"] or 0),
        daily_guard_reward_count=int(row["daily_guard_reward_count"] or 0),
        daily_count_reset_at=row["daily_count_reset_at"],
        sci_fi_intervention_level=int(row["sci_fi_intervention_level"] or 0),
        sci_fi_apex_fate_solitude=bool(row["sci_fi_apex_fate_solitude"]),
        updated_at=row["updated_at"],
    )


def _row_to_event(row) -> TribulationEvent:
    return TribulationEvent(
        event_id=int(row["event_id"]),
        user_id=row["user_id"],
        target_realm=row["target_realm"],
        mode=row["mode"],
        status=row["status"],
        equipment_snapshot=_load_json(row["equipment_snapshot"], {}),
        items_invested=_load_json(row["items_invested"], []),
        accumulated_xiuwei=int(row["accumulated_xiuwei"] or 0),
        created_at=row["created_at"],
        announce_at=row["announce_at"],
        scheduled_at=row["scheduled_at"],
        resolved_at=row["resolved_at"],
        result=row["result"],
        quality=row["quality"],
        final_success_rate=row["final_success_rate"],
        final_total_weight=row["final_total_weight"],
        daowang_collected=int(row["daowang_collected"] or 0),
    )


def _row_to_participant(row) -> TribulationParticipant:
    return TribulationParticipant(
        participant_id=int(row["participant_id"]),
        event_id=int(row["event_id"]),
        user_id=row["user_id"],
        type=row["type"],
        joined_at=row["joined_at"],
        reward_paid=bool(row["reward_paid"]),
        reward_amount=_load_json(row["reward_amount"], None),
        is_effective=bool(row["is_effective"]),
        xiuwei_granted=int(row["xiuwei_granted"] or 0),
    )


class SqliteTribulationRepository:
    """玄幻渡劫数据访问。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cm = DatabaseConnectionManager(db_path)

    # ------------------------------------------------------------------
    # CultivationProfile
    # ------------------------------------------------------------------
    def get_profile(self, user_id: str) -> Optional[CultivationProfile]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM user_cultivation WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
            return _row_to_profile(row) if row else None

    def upsert_profile(self, profile: CultivationProfile, now_iso: str) -> None:
        profile.updated_at = now_iso
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO user_cultivation (
                    user_id, current_realm, current_realm_quality,
                    accumulated_xiuwei, consecutive_failures,
                    realm_history, tiancheng_protection,
                    daily_observer_reward_count, daily_guard_reward_count,
                    daily_count_reset_at, sci_fi_intervention_level,
                    sci_fi_apex_fate_solitude, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    current_realm = excluded.current_realm,
                    current_realm_quality = excluded.current_realm_quality,
                    accumulated_xiuwei = excluded.accumulated_xiuwei,
                    consecutive_failures = excluded.consecutive_failures,
                    realm_history = excluded.realm_history,
                    tiancheng_protection = excluded.tiancheng_protection,
                    daily_observer_reward_count = excluded.daily_observer_reward_count,
                    daily_guard_reward_count = excluded.daily_guard_reward_count,
                    daily_count_reset_at = excluded.daily_count_reset_at,
                    sci_fi_intervention_level = excluded.sci_fi_intervention_level,
                    sci_fi_apex_fate_solitude = excluded.sci_fi_apex_fate_solitude,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.user_id,
                    profile.current_realm,
                    profile.current_realm_quality,
                    int(profile.accumulated_xiuwei),
                    int(profile.consecutive_failures),
                    _dump_json(profile.realm_history or {}),
                    _dump_json(profile.tiancheng_protection or {}),
                    int(profile.daily_observer_reward_count),
                    int(profile.daily_guard_reward_count),
                    profile.daily_count_reset_at,
                    int(profile.sci_fi_intervention_level),
                    1 if profile.sci_fi_apex_fate_solitude else 0,
                    profile.updated_at,
                ),
            )
            conn.commit()

    def add_xiuwei(self, user_id: str, delta: int, cap: int, now_iso: str) -> int:
        """原子化追加修为，返回实际增加值（不超过 cap）。"""
        if delta <= 0:
            return 0
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT accumulated_xiuwei FROM user_cultivation WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
            if row is None:
                return 0
            current = int(row["accumulated_xiuwei"] or 0)
            target = min(current + delta, cap)
            actual = max(0, target - current)
            if actual <= 0:
                return 0
            cur.execute(
                "UPDATE user_cultivation SET accumulated_xiuwei = ?, updated_at = ? WHERE user_id = ?",
                (target, now_iso, user_id),
            )
            conn.commit()
            return actual

    # ------------------------------------------------------------------
    # TribulationEvent
    # ------------------------------------------------------------------
    def create_event(
        self,
        user_id: str,
        target_realm: str,
        mode: str,
        status: str,
        equipment_snapshot: Dict[str, Any],
        items_invested: List[Dict[str, Any]],
        accumulated_xiuwei: int,
        created_at: str,
        announce_at: Optional[str],
        scheduled_at: str,
    ) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO tribulation_events (
                    user_id, target_realm, mode, status,
                    equipment_snapshot, items_invested, accumulated_xiuwei,
                    created_at, announce_at, scheduled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    target_realm,
                    mode,
                    status,
                    _dump_json(equipment_snapshot),
                    _dump_json(items_invested),
                    int(accumulated_xiuwei),
                    created_at,
                    announce_at,
                    scheduled_at,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_event(self, event_id: int) -> Optional[TribulationEvent]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM tribulation_events WHERE event_id = ?",
                (event_id,),
            )
            row = cur.fetchone()
            return _row_to_event(row) if row else None

    def get_active_event_for_user(self, user_id: str) -> Optional[TribulationEvent]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM tribulation_events
                WHERE user_id = ? AND status IN (?, ?)
                ORDER BY event_id DESC LIMIT 1
                """,
                (user_id, STATUS_PENDING, STATUS_ANNOUNCED),
            )
            row = cur.fetchone()
            return _row_to_event(row) if row else None

    def list_announced(self, limit: int = 50) -> List[TribulationEvent]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM tribulation_events
                WHERE status = ?
                ORDER BY scheduled_at ASC
                LIMIT ?
                """,
                (STATUS_ANNOUNCED, int(limit)),
            )
            return [_row_to_event(r) for r in cur.fetchall()]

    def list_pending_ready_to_announce(self, now_iso: str) -> List[TribulationEvent]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM tribulation_events
                WHERE status = ? AND announce_at IS NOT NULL AND announce_at <= ?
                ORDER BY announce_at ASC
                """,
                (STATUS_PENDING, now_iso),
            )
            return [_row_to_event(r) for r in cur.fetchall()]

    def list_announced_due_for_resolution(self, now_iso: str) -> List[TribulationEvent]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM tribulation_events
                WHERE status = ? AND scheduled_at <= ?
                ORDER BY scheduled_at ASC
                """,
                (STATUS_ANNOUNCED, now_iso),
            )
            return [_row_to_event(r) for r in cur.fetchall()]

    def list_user_history(self, user_id: str, limit: int = 20) -> List[TribulationEvent]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM tribulation_events
                WHERE user_id = ? AND status = ?
                ORDER BY event_id DESC LIMIT ?
                """,
                (user_id, STATUS_FINISHED, int(limit)),
            )
            return [_row_to_event(r) for r in cur.fetchall()]

    def update_event_status(self, event_id: int, status: str) -> None:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE tribulation_events SET status = ? WHERE event_id = ?",
                (status, event_id),
            )
            conn.commit()

    def finalize_event(
        self,
        event_id: int,
        resolved_at: str,
        result: str,
        quality: Optional[str],
        final_success_rate: float,
        final_total_weight: int,
        daowang_collected: int,
    ) -> None:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE tribulation_events SET
                    status = ?, resolved_at = ?, result = ?, quality = ?,
                    final_success_rate = ?, final_total_weight = ?,
                    daowang_collected = ?
                WHERE event_id = ?
                """,
                (
                    STATUS_FINISHED,
                    resolved_at,
                    result,
                    quality,
                    float(final_success_rate),
                    int(final_total_weight),
                    int(daowang_collected),
                    int(event_id),
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # TribulationParticipant
    # ------------------------------------------------------------------
    def add_participant(
        self,
        event_id: int,
        user_id: str,
        type_: str,
        joined_at: str,
        is_effective: bool = True,
    ) -> bool:
        """加入；若已加入返回 False。"""
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO tribulation_participants
                (event_id, user_id, type, joined_at, reward_paid, is_effective, xiuwei_granted)
                VALUES (?, ?, ?, ?, 0, ?, 0)
                """,
                (int(event_id), user_id, type_, joined_at, 1 if is_effective else 0),
            )
            inserted = cur.rowcount > 0
            conn.commit()
            return inserted

    def get_participant(self, event_id: int, user_id: str) -> Optional[TribulationParticipant]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM tribulation_participants WHERE event_id = ? AND user_id = ?",
                (int(event_id), user_id),
            )
            row = cur.fetchone()
            return _row_to_participant(row) if row else None

    def list_participants(self, event_id: int, type_: Optional[str] = None) -> List[TribulationParticipant]:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            if type_:
                cur.execute(
                    "SELECT * FROM tribulation_participants WHERE event_id = ? AND type = ? ORDER BY joined_at ASC",
                    (int(event_id), type_),
                )
            else:
                cur.execute(
                    "SELECT * FROM tribulation_participants WHERE event_id = ? ORDER BY joined_at ASC",
                    (int(event_id),),
                )
            return [_row_to_participant(r) for r in cur.fetchall()]

    def count_participants(self, event_id: int, type_: Optional[str] = None) -> int:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            if type_:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM tribulation_participants WHERE event_id = ? AND type = ?",
                    (int(event_id), type_),
                )
            else:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM tribulation_participants WHERE event_id = ?",
                    (int(event_id),),
                )
            row = cur.fetchone()
            return int(row["c"] if row else 0)

    def mark_participant_reward(
        self,
        participant_id: int,
        reward_amount: Optional[Dict[str, Any]],
        xiuwei_granted: int = 0,
    ) -> None:
        with self._cm.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE tribulation_participants SET
                    reward_paid = 1, reward_amount = ?, xiuwei_granted = ?
                WHERE participant_id = ?
                """,
                (_dump_json(reward_amount), int(xiuwei_granted), int(participant_id)),
            )
            conn.commit()
