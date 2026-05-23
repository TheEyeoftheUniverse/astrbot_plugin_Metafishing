from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from . import scifi_constants as C
from ..utils import get_now


def _now_iso() -> str:
    return get_now().isoformat(timespec="seconds")


class SciFiService:
    def __init__(
        self,
        repo,
        inventory_repo,
        item_template_repo,
        cthulhu_repo,
        cultivation_service,
    ):
        self.repo = repo
        self.inventory_repo = inventory_repo
        self.item_template_repo = item_template_repo
        self.cthulhu_repo = cthulhu_repo
        self.cultivation_service = cultivation_service

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def get_or_create_state(self, user_id: str) -> Dict[str, Any]:
        return self.repo.ensure_state(user_id, _now_iso())

    def get_state(self, user_id: str) -> Dict[str, Any]:
        return self.get_or_create_state(user_id)

    def get_total_append_rate_bp(self, user_id: str) -> int:
        state = self.get_state(user_id)
        rate = (
            C.LEVEL_APPEND_RATE_BP.get(int(state.get("abyss_compression_level", 0) or 0), 0)
            + C.LEVEL_APPEND_RATE_BP.get(int(state.get("fate_severance_level", 0) or 0), 0)
            + C.LEVEL_APPEND_RATE_BP.get(int(state.get("resonance_dampening_level", 0) or 0), 0)
            + C.APEX_APPEND_RATE_BP.get(state.get("apex_protocol"), 0)
        )
        return min(int(rate), C.APPEND_RATE_CAP_BP)

    def get_append_rate_breakdown(self, user_id: str) -> Dict[str, Any]:
        state = self.get_state(user_id)
        branch_rates = {
            branch: C.LEVEL_APPEND_RATE_BP.get(C.get_branch_level(state, branch), 0)
            for branch in C.BRANCHES
        }
        apex = state.get("apex_protocol")
        total = min(sum(branch_rates.values()) + C.APEX_APPEND_RATE_BP.get(apex, 0), C.APPEND_RATE_CAP_BP)
        return {
            "success": True,
            "branch_rates_bp": branch_rates,
            "apex_protocol": apex,
            "apex_rate_bp": C.APEX_APPEND_RATE_BP.get(apex, 0),
            "total_append_rate_bp": total,
            "total_append_rate_percent": round(total / 100, 2),
        }

    def get_state_view(self, user_id: str) -> Dict[str, Any]:
        state = self.get_state(user_id)
        chips = self.inventory_repo.get_user_item_inventory(user_id).get(C.REWRITE_CHIP_ITEM_ID, 0)
        breakdown = self.get_append_rate_breakdown(user_id)
        apex_unlocks = []
        for apex, requirements in C.APEX_UNLOCK_REQUIREMENTS.items():
            unlocked = all(C.get_branch_level(state, branch) >= need for branch, need in requirements.items())
            apex_unlocks.append(
                {
                    "id": apex,
                    "name": C.APEX_DISPLAY[apex],
                    "unlocked": unlocked,
                    "selected": state.get("apex_protocol") == apex,
                    "append_rate_bp": C.APEX_APPEND_RATE_BP[apex],
                    "requirements": requirements,
                }
            )

        branches = []
        for branch in C.BRANCHES:
            level = C.get_branch_level(state, branch)
            next_level = min(level + 1, C.MAX_BRANCH_LEVEL)
            branches.append(
                {
                    "id": branch,
                    "name": C.BRANCH_DISPLAY[branch],
                    "target": C.BRANCH_TARGET_DISPLAY[branch],
                    "level": level,
                    "max_level": C.MAX_BRANCH_LEVEL,
                    "append_rate_bp": C.LEVEL_APPEND_RATE_BP[level],
                    "next_level_cost": C.LEVEL_UP_COST.get(next_level),
                    "cthulhu_offset": C.CTHULHU_GREAT_FAILURE_OFFSET.get(level, 0) if branch == C.BRANCH_ABYSS else None,
                    "tribulation_multiplier": C.TRIBULATION_SELF_RATE_MULTIPLIER.get(level, 1.0) if branch == C.BRANCH_FATE else None,
                    "team_battle_penalty": C.TEAM_BATTLE_D20_PENALTY.get(level, 0) if branch == C.BRANCH_RESONANCE else None,
                }
            )

        return {
            "success": True,
            "state": state,
            "branches": branches,
            "apex_unlocks": apex_unlocks,
            "chip_count": int(chips or 0),
            "append_rate": breakdown,
            "total_level": sum(C.get_branch_level(state, branch) for branch in C.BRANCHES),
        }

    def list_event_logs(self, user_id: str, limit: int = 50) -> Dict[str, Any]:
        return {"success": True, "logs": self.repo.list_event_logs(user_id, limit)}

    def get_leaderboard(self, limit: int = 50) -> Dict[str, Any]:
        rows = self.repo.get_leaderboard(limit)
        for idx, row in enumerate(rows, start=1):
            row["rank"] = idx
            row["append_rate_bp"] = min(
                C.LEVEL_APPEND_RATE_BP.get(int(row.get("abyss_compression_level", 0) or 0), 0)
                + C.LEVEL_APPEND_RATE_BP.get(int(row.get("fate_severance_level", 0) or 0), 0)
                + C.LEVEL_APPEND_RATE_BP.get(int(row.get("resonance_dampening_level", 0) or 0), 0)
                + C.APEX_APPEND_RATE_BP.get(row.get("apex_protocol"), 0),
                C.APPEND_RATE_CAP_BP,
            )
        return {"success": True, "leaderboard": rows}

    # ------------------------------------------------------------------
    # Research and append
    # ------------------------------------------------------------------
    def on_fish_caught(
        self,
        user_id: str,
        zone_id: int,
        final_rarity: int,
        original_rarity: Optional[int] = None,
        fish_count: int = 1,
    ) -> None:
        try:
            if int(zone_id) != C.SCIFI_ZONE_ID:
                return
            research_rarity = int(original_rarity if original_rarity is not None else final_rarity)
            fish_count = max(1, int(fish_count or 1))
            if research_rarity <= 0:
                return
            amount = research_rarity * fish_count
            now_iso = _now_iso()
            self.get_or_create_state(user_id)
            self.repo.add_research_points(user_id, amount, now_iso)
            self.repo.insert_event_log(
                user_id,
                C.EVENT_RESEARCH_EARNED,
                {
                    "amount": amount,
                    "fish_count": fish_count,
                    "fish_rarity": int(final_rarity),
                    "research_rarity": research_rarity,
                },
                now_iso,
            )
        except Exception as exc:
            logger.warning(f"[scifi] research award failed: {exc}")

    def roll_append(self, user_id: str, drawn_rarity: int) -> Dict[str, Any]:
        if int(drawn_rarity) >= 6:
            return {"rolled": False, "hit": False, "append_rate_bp": 0, "roll": None}
        append_rate_bp = self.get_total_append_rate_bp(user_id)
        if append_rate_bp <= 0:
            return {"rolled": False, "hit": False, "append_rate_bp": 0, "roll": None}
        roll = random.randint(1, 10000)
        return {
            "rolled": True,
            "hit": roll <= append_rate_bp,
            "append_rate_bp": append_rate_bp,
            "roll": roll,
        }

    def log_append_result(
        self,
        user_id: str,
        zone_id: int,
        original_rarity: int,
        append_result: Dict[str, Any],
        replaced_rarity: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        if not append_result.get("rolled"):
            return
        now_iso = _now_iso()
        event_type = C.EVENT_APPEND_TRIGGERED if replaced_rarity is not None else C.EVENT_APPEND_MISSED
        detail = {
            "zone_id": int(zone_id),
            "rarity_in": int(original_rarity),
            "append_rate_bp": int(append_result.get("append_rate_bp", 0) or 0),
            "roll": append_result.get("roll"),
            "reason": reason,
        }
        if replaced_rarity is not None:
            detail["rarity_out"] = int(replaced_rarity)
            self.repo.increment_append_triggered(user_id, 1, now_iso)
        self.repo.insert_event_log(user_id, event_type, detail, now_iso)

    # ------------------------------------------------------------------
    # Leveling and apex
    # ------------------------------------------------------------------
    def level_up_branch(self, user_id: str, branch: str, count: int = 1) -> Dict[str, Any]:
        if branch not in C.BRANCHES:
            return {"success": False, "reason": C.ERROR_INVALID_BRANCH, "message": "无效分支。"}

        count = max(1, min(int(count or 1), C.MAX_BRANCH_LEVEL))
        upgraded = 0
        spent = 0
        last_state = self.get_state(user_id)

        for _ in range(count):
            current_level = C.get_branch_level(last_state, branch)
            if current_level >= C.MAX_BRANCH_LEVEL:
                break
            next_level = current_level + 1
            cost = C.LEVEL_UP_COST[next_level]
            if int(last_state.get("research_points", 0) or 0) < cost:
                break
            ok = self.repo.try_level_up(
                user_id=user_id,
                field_name=C.BRANCH_LEVEL_FIELD[branch],
                current_level=current_level,
                next_level=next_level,
                cost=cost,
                now_iso=_now_iso(),
            )
            if not ok:
                break
            upgraded += 1
            spent += cost
            last_state = self.get_state(user_id)
            self.repo.insert_event_log(
                user_id,
                C.EVENT_LEVEL_UP,
                {"branch": branch, "new_level": next_level, "cost": cost},
                _now_iso(),
            )

        if upgraded <= 0:
            current_level = C.get_branch_level(last_state, branch)
            if current_level >= C.MAX_BRANCH_LEVEL:
                return {"success": False, "reason": C.ERROR_MAX_LEVEL, "message": "该分支已满级。"}
            return {
                "success": False,
                "reason": C.ERROR_INSUFFICIENT_POINTS,
                "needed": C.LEVEL_UP_COST[current_level + 1],
                "have": int(last_state.get("research_points", 0) or 0),
                "message": "科研点不足。",
            }

        self.sync_external_state(user_id, last_state)
        return {
            "success": True,
            "branch": branch,
            "branch_name": C.BRANCH_DISPLAY[branch],
            "levels_gained": upgraded,
            "spent_points": spent,
            "new_level": C.get_branch_level(last_state, branch),
            "remaining_points": int(last_state.get("research_points", 0) or 0),
            "state": last_state,
        }

    def select_apex(self, user_id: str, apex: str) -> Dict[str, Any]:
        if apex not in C.APEX_PROTOCOLS:
            return {"success": False, "reason": C.ERROR_INVALID_APEX, "message": "无效协议。"}
        state = self.get_state(user_id)
        current = state.get("apex_protocol")
        if current:
            return {
                "success": False,
                "reason": C.ERROR_APEX_ALREADY_SELECTED,
                "current": current,
                "message": "已持有觉醒协议，请先重写。",
            }
        requirements = C.APEX_UNLOCK_REQUIREMENTS[apex]
        if not all(C.get_branch_level(state, branch) >= need for branch, need in requirements.items()):
            return {
                "success": False,
                "reason": C.APEX_UNLOCK_ERROR[apex],
                "message": "当前不满足该协议的解锁条件。",
            }

        now_iso = _now_iso()
        self.repo.update_state_fields(
            user_id,
            apex_protocol=apex,
            apex_acquired_at=now_iso,
            updated_at=now_iso,
        )
        state = self.get_state(user_id)
        self.sync_external_state(user_id, state)
        self.repo.insert_event_log(user_id, C.EVENT_APEX_SELECT, {"apex": apex}, now_iso)
        return {"success": True, "apex": apex, "apex_name": C.APEX_DISPLAY[apex], "state": state}

    def reset_apex(self, user_id: str) -> Dict[str, Any]:
        state = self.get_state(user_id)
        current = state.get("apex_protocol")
        if not current:
            return {"success": False, "reason": C.RESET_REASON_CODE, "message": "当前没有可重写的觉醒协议。"}

        inventory = self.inventory_repo.get_user_item_inventory(user_id)
        chips = int(inventory.get(C.REWRITE_CHIP_ITEM_ID, 0) or 0)
        if chips <= 0:
            return {"success": False, "reason": C.ERROR_NO_CHIP, "message": "缺少协议重写芯。"}

        self.inventory_repo.decrease_item_quantity(user_id, C.REWRITE_CHIP_ITEM_ID, 1)
        now_iso = _now_iso()
        self.repo.update_state_fields(
            user_id,
            apex_protocol=None,
            apex_acquired_at=None,
            last_recompose_at=now_iso,
            updated_at=now_iso,
        )
        state = self.get_state(user_id)
        self.sync_external_state(user_id, state)
        self.repo.insert_event_log(user_id, C.EVENT_APEX_RESET, {"previous": current}, now_iso)
        return {"success": True, "previous": current, "state": state}

    def grant_chip(self, user_id: str, count: int = 1) -> Dict[str, Any]:
        amount = max(1, int(count or 1))
        self.inventory_repo.update_item_quantity(user_id, C.REWRITE_CHIP_ITEM_ID, amount)
        self.repo.insert_event_log(
            user_id,
            C.EVENT_CHIP_GRANTED,
            {"item_id": C.REWRITE_CHIP_ITEM_ID, "count": amount},
            _now_iso(),
        )
        return {"success": True, "item_id": C.REWRITE_CHIP_ITEM_ID, "count": amount}

    # ------------------------------------------------------------------
    # Penalty helpers
    # ------------------------------------------------------------------
    def get_resonance_d20_penalty(self, user_id: str) -> int:
        state = self.get_state(user_id)
        penalty = C.TEAM_BATTLE_D20_PENALTY.get(int(state.get("resonance_dampening_level", 0) or 0), 0)
        apex = state.get("apex_protocol")
        if apex:
            penalty += C.APEX_D20_PENALTY.get(apex, 0)
        return int(penalty)

    def has_apex_protocol(self, user_id: str, apex: str) -> bool:
        return self.get_state(user_id).get("apex_protocol") == apex

    # ------------------------------------------------------------------
    # External sync
    # ------------------------------------------------------------------
    def sync_external_state(self, user_id: str, state: Optional[Dict[str, Any]] = None) -> None:
        state = state or self.get_state(user_id)
        apex = state.get("apex_protocol")

        try:
            self.cthulhu_repo.ensure_state(user_id)
            self.cthulhu_repo.update_state_fields(
                user_id,
                sci_fi_intervention_level=int(state.get("abyss_compression_level", 0) or 0),
                sci_fi_apex_singularity=1 if apex == C.APEX_SINGULARITY else 0,
                sci_fi_apex_abyss_unity=1 if apex == C.APEX_ABYSS_UNITY else 0,
                sci_fi_apex_fate_solitude=1 if apex == C.APEX_FATE_SOLITUDE else 0,
            )
        except Exception as exc:
            logger.warning(f"[scifi] sync cthulhu failed for {user_id}: {exc}")

        try:
            profile = self.cultivation_service.get_or_create_profile(user_id)
            profile.sci_fi_intervention_level = int(state.get("fate_severance_level", 0) or 0)
            profile.sci_fi_apex_singularity = apex == C.APEX_SINGULARITY
            profile.sci_fi_apex_abyss_unity = apex == C.APEX_ABYSS_UNITY
            profile.sci_fi_apex_fate_solitude = apex == C.APEX_FATE_SOLITUDE
            profile.sci_fi_apex_resonance_summit = apex == C.APEX_RESONANCE_SUMMIT
            self.cultivation_service.repo.upsert_profile(profile, _now_iso())
        except Exception as exc:
            logger.warning(f"[scifi] sync cultivation failed for {user_id}: {exc}")
