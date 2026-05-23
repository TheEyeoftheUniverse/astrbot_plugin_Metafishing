from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable

from astrbot.api import logger

from ..utils import get_last_reset_time


QUALITY_ORDER = {
    None: 0,
    "fanxue": 0,
    "lingyun": 1,
    "zhenyi": 2,
    "tiancheng": 3,
}

SCIFI_TITLE_BY_APEX = {
    "singularity": 25,
    "abyss_unity": 26,
    "fate_solitude": 27,
    "resonance_summit": 28,
}

CTHULHU_UPPER_TITLE_BY_AUTHORITY = {
    "predict_upper": 33,
    "time_upper": 34,
    "pollute_upper": 35,
    "sacrifice_upper": 36,
}

REVOCABLE_TITLE_IDS = {22, 23, 24, 25, 26, 27, 28, 33, 34, 35, 36}


def _row_to_mapping(row: Any) -> Dict[str, Any]:
    if isinstance(row, dict):
        return row
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            return {key: row[key] for key in keys()}
        except Exception:
            pass
    try:
        return dict(row)
    except Exception:
        return {}


class GameplayTitleService:
    """仅在每日刷新时扫描活跃玩家，维护玩法专属称号。"""

    def __init__(
        self,
        user_repo,
        inventory_repo,
        scifi_service,
        cultivation_service,
        team_battle_repo,
        cthulhu_repo,
        game_config: Dict[str, Any],
    ):
        self.user_repo = user_repo
        self.inventory_repo = inventory_repo
        self.scifi_service = scifi_service
        self.cultivation_service = cultivation_service
        self.team_battle_repo = team_battle_repo
        self.cthulhu_repo = cthulhu_repo
        self.daily_reset_hour = int((game_config or {}).get("daily_reset_hour", 0) or 0)

        self._refresh_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_refresh_reset_time: datetime | None = None

    def start_daily_refresh_task(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._refresh_loop,
            daemon=True,
            name="gameplay_title_refresh_loop",
        )
        self._thread.start()

    def stop_daily_refresh_task(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def run_daily_refresh_if_needed(self) -> Dict[str, Any]:
        current_reset_time = get_last_reset_time(self.daily_reset_hour)
        if current_reset_time == self._last_refresh_reset_time:
            return {"success": True, "ran": False, "reset_time": current_reset_time.isoformat()}
        with self._refresh_lock:
            if current_reset_time == self._last_refresh_reset_time:
                return {"success": True, "ran": False, "reset_time": current_reset_time.isoformat()}
            result = self._refresh_once(current_reset_time)
            self._last_refresh_reset_time = current_reset_time
            return result

    def force_daily_refresh(self) -> Dict[str, Any]:
        with self._refresh_lock:
            current_reset_time = get_last_reset_time(self.daily_reset_hour)
            result = self._refresh_once(current_reset_time)
            self._last_refresh_reset_time = current_reset_time
            result["forced"] = True
            return result

    def _refresh_loop(self) -> None:
        while self._running:
            try:
                self.run_daily_refresh_if_needed()
            except Exception as exc:
                logger.warning(f"[gameplay_titles] refresh failed: {exc}")
            threading.Event().wait(60)

    def _refresh_once(self, reset_time: datetime) -> Dict[str, Any]:
        users = self.user_repo.get_all_users(limit=1_000_000, offset=0)
        settle_rows = [
            _row_to_mapping(row)
            for row in self.team_battle_repo.list_recent_settles(limit=1_000_000)
        ]
        history_kills = [
            _row_to_mapping(row)
            for row in self.team_battle_repo.list_history_kills(limit=1_000_000)
        ]
        ten_star_boss_ids = {
            int(row.get("boss_id"))
            for row in history_kills
            if int(row.get("boss_star", 0) or 0) == 10 and row.get("boss_id") is not None
        }

        leader_boss_ids_by_user: dict[str, set[int]] = {}
        for row in settle_rows:
            summary = row.get("settle_summary") or {}
            boss_id = summary.get("boss_id")
            if boss_id is None:
                continue
            for user_id in summary.get("leaders") or []:
                leader_boss_ids_by_user.setdefault(str(user_id), set()).add(int(boss_id))

        scanned = 0
        skipped_zombies = 0
        granted = 0
        revoked = 0

        for user in users:
            if self._is_zombie_user(user, leader_boss_ids_by_user):
                skipped_zombies += 1
                continue
            scanned += 1
            delta = self._refresh_user_titles(user.user_id, leader_boss_ids_by_user, ten_star_boss_ids)
            granted += delta["granted"]
            revoked += delta["revoked"]

        summary = {
            "success": True,
            "ran": True,
            "reset_time": reset_time.isoformat(),
            "scanned_users": scanned,
            "skipped_zombies": skipped_zombies,
            "granted_count": granted,
            "revoked_count": revoked,
        }
        logger.info(f"[gameplay_titles] refresh summary: {summary}")
        return summary

    def _is_zombie_user(self, user, leader_boss_ids_by_user: dict[str, set[int]]) -> bool:
        last_login = getattr(user, "last_login_time", None)
        if last_login is None:
            return False
        if isinstance(last_login, str):
            return False
        if last_login >= datetime.now(last_login.tzinfo) - timedelta(days=30):
            return False

        user_id = user.user_id

        try:
            scifi_state = _row_to_mapping(self.scifi_service.get_state(user_id))
            if any(int(scifi_state.get(field, 0) or 0) > 0 for field in (
                "abyss_compression_level",
                "fate_severance_level",
                "resonance_dampening_level",
            )):
                return False
            if scifi_state.get("apex_protocol"):
                return False
        except Exception:
            return False

        try:
            profile = self.cultivation_service.get_profile(user_id)
            if profile and (
                profile.current_realm != "lianqi"
                or profile.current_realm_quality
                or profile.realm_history
            ):
                return False
        except Exception:
            return False

        if leader_boss_ids_by_user.get(user_id):
            return False

        rewards = self.team_battle_repo.get_rewards_history(user_id, limit=1)
        if rewards:
            return False

        return True

    def _refresh_user_titles(
        self,
        user_id: str,
        leader_boss_ids_by_user: dict[str, set[int]],
        ten_star_boss_ids: set[int],
    ) -> Dict[str, int]:
        current_titles = set(self.inventory_repo.get_user_titles(user_id))
        target_titles = self._compute_target_titles(user_id, leader_boss_ids_by_user, ten_star_boss_ids)

        granted = 0
        revoked = 0

        for title_id in sorted(target_titles - current_titles):
            self.inventory_repo.grant_title_to_user(user_id, title_id)
            granted += 1

        for title_id in sorted((current_titles - target_titles) & REVOCABLE_TITLE_IDS):
            self.inventory_repo.revoke_title_from_user(user_id, title_id)
            revoked += 1

        return {"granted": granted, "revoked": revoked}

    def _compute_target_titles(
        self,
        user_id: str,
        leader_boss_ids_by_user: dict[str, set[int]],
        ten_star_boss_ids: set[int],
    ) -> set[int]:
        titles: set[int] = set()

        profile = self.cultivation_service.get_profile(user_id)
        quality_rank = QUALITY_ORDER.get(getattr(profile, "current_realm_quality", None), 0) if profile else 0
        if quality_rank >= QUALITY_ORDER["lingyun"]:
            titles.add(22)
        if quality_rank >= QUALITY_ORDER["zhenyi"]:
            titles.add(23)
        if quality_rank >= QUALITY_ORDER["tiancheng"]:
            titles.add(24)

        scifi_state = _row_to_mapping(self.scifi_service.get_state(user_id))
        apex_protocol = scifi_state.get("apex_protocol")
        title_id = SCIFI_TITLE_BY_APEX.get(apex_protocol)
        if title_id:
            titles.add(title_id)

        reward_history = [
            _row_to_mapping(row)
            for row in self.team_battle_repo.get_rewards_history(user_id, limit=1_000_000)
        ]
        reward_boss_ids = {
            int(row.get("boss_id"))
            for row in reward_history
            if row.get("boss_id") is not None
        }
        if reward_history:
            titles.add(29)
            settlement_count = len({row.get("granted_at") for row in reward_history if row.get("granted_at")})
            if settlement_count >= 10:
                titles.add(30)

        leader_boss_ids = leader_boss_ids_by_user.get(user_id, set())
        leader_reward_boss_ids = leader_boss_ids & reward_boss_ids
        if leader_reward_boss_ids:
            titles.add(31)
            if any(boss_id in ten_star_boss_ids for boss_id in leader_reward_boss_ids):
                titles.add(32)

        held_authorities = {
            row.get("authority_id")
            for row in (
                _row_to_mapping(item)
                for item in self.cthulhu_repo.list_authorities_for_holder(user_id)
            )
        }
        for authority_id, title_id in CTHULHU_UPPER_TITLE_BY_AUTHORITY.items():
            if authority_id in held_authorities:
                titles.add(title_id)

        return titles
