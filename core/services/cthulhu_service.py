from __future__ import annotations

import json
import random
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from ..utils import get_now, get_current_daily_marker, get_last_reset_time


AUTHORITY_SAN_COST = {
    "predict": 5,
    "time": 10,
    "pollute": 5,
}

THRESHOLD_BY_TIER = {"upper": 200, "middle": 80, "lower": 30}
GLOBAL_SAN_COST = {"upper": 30, "middle": 15, "lower": 5}
DIFFICULTY_BY_TIER = {
    "upper": {"sacrifice": 35, "time": 40, "predict": 45, "pollute": 50},
    "middle": {"sacrifice": 50, "time": 55, "predict": 60, "pollute": 65},
    "lower": {"sacrifice": 65, "time": 70, "predict": 75, "pollute": 80},
}
AUTHORITY_DISPLAY = {
    "predict": "预知",
    "time": "时间",
    "pollute": "污染",
    "sacrifice": "献祭",
}
TIER_DISPLAY = {"upper": "冠冕位阶", "middle": "秘仪位阶", "lower": "潮痕位阶"}
TIER_SHORT_DISPLAY = {"upper": "冠冕", "middle": "秘仪", "lower": "潮痕"}
POLLUTION_NAME = {
    "U1": "群星错位",
    "U2": "众生失名",
    "U5": "谵妄文字",
    "U8": "染血之幕",
    "U10": "静电之雨",
    "U11": "深海之眼",
    "U14": "战栗",
}
POLLUTION_TEXT_PREFIXES = [
    "溺死的", "无颈的", "反折的", "空壳的", "潮湿的", "盲目的", "裂面的", "失言的", "逆生的", "离潮的",
    "下沉的", "反胃的", "无梦的", "潮红的", "失温的", "无骨的", "盐化的", "回响的", "失真的", "盯视的",
]
ZALGO_MARKS = ["\u0300", "\u0301", "\u0302", "\u0303", "\u0304", "\u0307", "\u0308", "\u0323", "\u0330", "\u0336"]
NUMERAL_GLYPHS = {"0": "𒐀", "1": "𒐁", "2": "𒐂", "3": "𒐃", "4": "𒐄", "5": "𒐅", "6": "𒐆", "7": "𒐇", "8": "𒐈", "9": "𒐉"}


class CthulhuService:
    def __init__(
        self,
        repo,
        user_repo,
        inventory_repo,
        item_template_repo,
        log_repo,
        fishing_service,
        game_config: Dict[str, Any],
    ):
        self.repo = repo
        self.user_repo = user_repo
        self.inventory_repo = inventory_repo
        self.item_template_repo = item_template_repo
        self.log_repo = log_repo
        self.fishing_service = fishing_service
        self.game_config = game_config
        self._stop_event = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
        self._settle_lock = threading.Lock()
        self._last_processed_reset: Optional[str] = None

        plugin_root = Path(__file__).resolve().parents[2]
        events_path = plugin_root / "data" / "cthulhu" / "events_v1.json"
        names_path = plugin_root / "data" / "cthulhu" / "name_pool_v1.json"
        self.events = json.loads(events_path.read_text(encoding="utf-8"))
        self.events_by_id = {event["event_id"]: event for event in self.events}
        self.events_by_tier: Dict[str, List[Dict[str, Any]]] = {"upper": [], "middle": [], "lower": []}
        for event in self.events:
            self.events_by_tier[event["tier"]].append(event)
        name_pools = json.loads(names_path.read_text(encoding="utf-8"))
        self.prefix_pool = list(name_pools["prefix"])
        self.root_pool = list(name_pools["root"])
        self.suffix_pool = list(name_pools["suffix"])
        self.daily_reset_hour = int(self.game_config.get("daily_reset_hour", 0) or 0)

    def _now_iso(self) -> str:
        return get_now().isoformat(timespec="seconds")

    def _current_marker(self) -> str:
        return get_current_daily_marker(self.daily_reset_hour).isoformat()

    def _sanitize_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(state)
        state["current_san"] = int(state.get("current_san", 50) or 50)
        state["max_san"] = int(state.get("max_san", 50) or 50)
        state["pending_san_cap_tokens"] = int(state.get("pending_san_cap_tokens", 0) or 0)
        state["sci_fi_intervention_level"] = int(state.get("sci_fi_intervention_level", 0) or 0)
        state["pending_predict_candidates"] = state.get("pending_predict_candidates") or []
        return state

    def _get_state(self, user_id: str) -> Dict[str, Any]:
        return self._sanitize_state(self.repo.ensure_state(user_id))

    def _save_state(self, user_id: str, **fields: Any) -> None:
        self.repo.update_state_fields(user_id, **fields)

    def _threshold_for_tier(self, tier: str) -> int:
        return THRESHOLD_BY_TIER[tier]

    def _tier_roll(self, intervention: int) -> tuple[str, bool]:
        roll = random.randint(1, 100)
        great_failure_threshold = 96 - intervention * 10
        middle_threshold = max(6, 20 - intervention * 2)
        if roll <= 5:
            return "upper", False
        if roll <= middle_threshold:
            return "middle", False
        if roll < great_failure_threshold:
            return "lower", False
        return "lower", True

    def _apply_san_delta(self, user_id: str, delta: int) -> Dict[str, int]:
        state = self._get_state(user_id)
        current_san = max(0, min(state["max_san"], state["current_san"] + int(delta)))
        self._save_state(user_id, current_san=current_san)
        state["current_san"] = current_san
        return state

    def _grant_item(self, user_id: str, item_id: int, quantity: int = 1) -> None:
        self.inventory_repo.update_item_quantity(user_id, int(item_id), int(quantity))

    def _generate_unique_name(self) -> str:
        for _ in range(100):
            candidate = (
                random.choice(self.prefix_pool)
                + random.choice(self.root_pool)
                + random.choice(self.suffix_pool)
            )
            if not self.repo.true_name_exists(candidate):
                return candidate
        raise RuntimeError("true_name_generation_exhausted")

    def _grant_true_name(self, user_id: str, tier: str, god_type: str) -> Dict[str, Any]:
        if self.repo.count_inventory_true_names(user_id) >= 10:
            self.repo.delete_oldest_inventory_true_name(user_id)
        name_string = self._generate_unique_name()
        name_id = self.repo.insert_true_name(
            name_string=name_string,
            god_type=god_type,
            tier=tier,
            threshold=self._threshold_for_tier(tier),
            owner_user_id=user_id,
            created_at=self._now_iso(),
        )
        return {
            "name_id": name_id,
            "name_string": name_string,
            "god_type": god_type,
            "tier": tier,
            "threshold": self._threshold_for_tier(tier),
        }

    def try_enter_deepdive(self, user_id: str) -> Dict[str, Any]:
        state = self._get_state(user_id)
        if state["is_in_deepdive_today"]:
            return {"success": True, "deepdive_started": False, "reason": "already_deepdived"}
        items = self.inventory_repo.get_user_item_inventory(user_id)
        ticket_consumed = False
        compatibility_mode = False
        if items.get(55, 0) > 0:
            self.inventory_repo.decrease_item_quantity(user_id, 55, 1)
            ticket_consumed = True
        else:
            # 兼容回退：当前版本尚未把深潜门票来源接入到外部经济系统，
            # 为保证区域 7 可实际测试，允许免票开启一次当日深潜。
            compatibility_mode = True
        tier, force_pollute = self._tier_roll(state["sci_fi_intervention_level"])
        event = random.choice(self.events_by_tier[tier])
        self._save_state(
            user_id,
            is_in_deepdive_today=1,
            pending_event_id=event["event_id"],
            pending_event_tier=tier,
            pending_event_force_pollute=1 if force_pollute else 0,
            pending_event_choice=None,
        )
        return {
            "success": True,
            "deepdive_started": True,
            "event": event,
            "great_failure_pending": force_pollute,
            "ticket_consumed": ticket_consumed,
            "compatibility_mode": compatibility_mode,
        }

    def stage_event_choice(self, user_id: str, choice_id: str) -> Dict[str, Any]:
        state = self._get_state(user_id)
        if not state.get("pending_event_id"):
            return {"success": False, "message": "当前没有待抉择的深潜事件。"}
        event = self.events_by_id.get(state["pending_event_id"])
        if event is None:
            return {"success": False, "message": "深潜事件数据缺失。"}
        choice_id = str(choice_id or "").strip().upper()
        if choice_id not in {"A", "B"}:
            return {"success": False, "message": "选项只能是 A 或 B。"}
        if not any(choice["choice_id"] == choice_id for choice in event["choices"]):
            return {"success": False, "message": "无效选项。"}
        self._save_state(user_id, pending_event_choice=choice_id)
        return {"success": True, "message": "你已下注，等潮汐归位时见分晓。"}

    def _resolve_pending_event(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        event = self.events_by_id.get(state["pending_event_id"])
        if event is None:
            return None
        choice_id = state.get("pending_event_choice")
        if choice_id:
            chosen = next(choice for choice in event["choices"] if choice["choice_id"] == choice_id)
        else:
            chosen = random.choice(event["choices"])
            choice_id = chosen["choice_id"]
        other = next(choice for choice in event["choices"] if choice["choice_id"] != choice_id)
        roll = random.randint(1, 100)
        landed = chosen if roll <= int(chosen["difficulty"]) else other
        granted = self._grant_true_name(state["user_id"], event["tier"], landed["god_type"])

        san_delta = 0
        result = "success" if landed["choice_id"] == choice_id else "failure"
        updates: Dict[str, Any] = {}
        if int(state.get("pending_event_force_pollute", 0) or 0):
            san_delta -= 25
            forced_until = (get_last_reset_time(self.daily_reset_hour) + timedelta(days=1)).isoformat(timespec="seconds")
            updates["forced_pollution_until"] = forced_until
            self._apply_san_delta(state["user_id"], -25)
            result = "great_failure"
        self._save_state(state["user_id"], **updates)
        self.repo.insert_event_log(
            user_id=state["user_id"],
            event_id=event["event_id"],
            choice_id=choice_id,
            is_main_roll=True,
            d100_roll=roll,
            result=result,
            san_delta=san_delta,
            granted_name_id=granted["name_id"],
            occurred_at=self._now_iso(),
        )
        return {
            "user_id": state["user_id"],
            "event": event,
            "choice_id": choice_id,
            "roll": roll,
            "landed": landed,
            "granted": granted,
            "san_delta": san_delta,
        }

    def _maybe_settle_daily(self, force: bool = False) -> Dict[str, Any]:
        with self._settle_lock:
            reset_at = get_last_reset_time(self.daily_reset_hour)
            marker = reset_at.isoformat(timespec="seconds")
            now = get_now()
            if not force:
                if now < reset_at + timedelta(minutes=1):
                    return {"settled": False, "reason": "not_due", "marker": marker}
                if self._last_processed_reset == marker:
                    return {"settled": False, "reason": "already_settled", "marker": marker}
            self.repo.bootstrap_states_for_all_users()
            resolved = []
            for state in self.repo.get_pending_event_states():
                data = self._resolve_pending_event(state)
                if data is not None:
                    resolved.append(data)
            self.repo.recover_all_users_san(5)
            self.repo.clear_expired_forced_pollution(self._now_iso())
            self.repo.reset_daily_flags(self._now_iso())
            self._last_processed_reset = marker
            return {"settled": True, "marker": marker, "resolved_count": len(resolved)}

    def tick(self) -> Dict[str, Any]:
        return self._maybe_settle_daily(force=False)

    def force_daily_reset(self) -> Dict[str, Any]:
        return self._maybe_settle_daily(force=True)

    def scan_overdue_on_startup(self) -> Dict[str, Any]:
        return self._maybe_settle_daily(force=False)

    def start_daily_settle_task(self) -> None:
        if self._loop_thread and self._loop_thread.is_alive():
            return
        self._stop_event.clear()
        self._loop_thread = threading.Thread(target=self._settle_loop, daemon=True, name="cthulhu_daily_settle_loop")
        self._loop_thread.start()

    def stop_daily_settle_task(self) -> None:
        self._stop_event.set()
        if self._loop_thread:
            self._loop_thread.join(timeout=2.0)
            self._loop_thread = None

    def _settle_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                logger.warning(f"[cthulhu] tick failed: {exc}")
            self._stop_event.wait(timeout=60)

    def initiate_calling(self, user_id: str, name_id: int) -> Dict[str, Any]:
        name = self.repo.get_true_name(name_id)
        if name is None or name["owner_user_id"] != user_id:
            return {"success": False, "message": "你不持有这个真名。"}
        if name["status"] != "in_inventory":
            return {"success": False, "message": "该真名当前不能发起呼唤。"}
        if not self.repo.start_calling(name_id, user_id, self._now_iso()):
            return {"success": False, "message": "发起呼唤失败。"}
        return {
            "success": True,
            "message": f"真名【{name['name_string']}】已公开呼唤，当前进度 0/{name['threshold']}。",
        }

    def vote_on_call(self, voter_user_id: str, name_string: str) -> Dict[str, Any]:
        name = self.repo.get_calling_name_by_string(name_string)
        if name is None:
            return {"success": False, "message": "没有找到正在呼唤的真名。"}
        state = self._get_state(voter_user_id)
        if state["current_san"] < 3:
            return {"success": False, "message": "SAN 不足，无法投票。"}
        self._apply_san_delta(voter_user_id, -3)
        updated = self.repo.record_vote(name["name_id"], voter_user_id, self._now_iso())
        if int(updated["progress"]) >= int(updated["threshold"]):
            self.resolve_calling(name["name_id"])
            return {
                "success": True,
                "message": f"你为【{name_string}】投下了呼唤。进度已达阈值，旧神开始回应。",
                "new_progress": int(updated["progress"]),
                "threshold": int(updated["threshold"]),
            }
        return {
            "success": True,
            "message": f"呼唤成功，当前进度 {updated['progress']}/{updated['threshold']}。",
            "new_progress": int(updated["progress"]),
            "threshold": int(updated["threshold"]),
        }

    def resolve_calling(self, name_id: int) -> Dict[str, Any]:
        name = self.repo.get_true_name(name_id)
        if name is None:
            return {"success": False, "message": "真名不存在。"}
        if name["status"] != "calling":
            return {"success": False, "message": "该真名未处于呼唤中。"}
        authority_id = f"{name['god_type']}_{name['tier']}"
        authority = self.repo.get_authority(authority_id)
        if authority is None:
            return {"success": False, "message": "权柄槽位不存在。"}
        old_holder = authority.get("current_holder")
        initiator = name["owner_user_id"]
        if old_holder and old_holder != initiator and self.user_repo.check_exists(old_holder):
            self._grant_item(old_holder, 57, 1)
        self.repo.transfer_authority(authority_id, initiator, self._now_iso())

        self.repo.bootstrap_states_for_all_users()
        for user_id in self.repo.get_all_user_ids():
            self._apply_san_delta(user_id, -GLOBAL_SAN_COST[name["tier"]])

        activated_pollution = self.repo.activate_random_inactive_pollution(name_id, self._now_iso())
        reward_summary = self._distribute_global_rewards(name_id, name["tier"], initiator)
        self.repo.consume_true_name(name_id, self._now_iso())
        message = (
            f"{AUTHORITY_DISPLAY[name['god_type']]}·{TIER_DISPLAY[name['tier']]} 权柄已易位。"
            if old_holder and old_holder != initiator
            else f"{AUTHORITY_DISPLAY[name['god_type']]}·{TIER_DISPLAY[name['tier']]} 权柄已回应。"
        )
        if activated_pollution:
            message += f" 永久污染【{POLLUTION_NAME.get(activated_pollution, activated_pollution)}】已显形。"
        return {
            "success": True,
            "message": message,
            "reward_summary": reward_summary,
        }

    def _distribute_global_rewards(self, name_id: int, tier: str, initiator: str) -> Dict[str, Any]:
        marker = self._current_marker()
        signed_users = self.repo.get_signed_in_user_ids(marker)
        base_amount = {"lower": 1, "middle": 2, "upper": 3}[tier]
        for user_id in signed_users:
            self._grant_item(user_id, 56, base_amount)
        extra_receivers: List[str] = []
        if tier in {"middle", "upper"}:
            top_n = 5 if tier == "middle" else 10
            for row in self.repo.get_top_voters(name_id, top_n):
                user_id = row["user_id"]
                if user_id in signed_users:
                    self._grant_item(user_id, 57, 1)
                    extra_receivers.append(user_id)
        if tier == "upper" and initiator in signed_users:
            self._grant_item(initiator, 57, 1)
            extra_receivers.append(initiator)
        return {
            "signed_user_count": len(signed_users),
            "base_amount": base_amount,
            "extra_receivers": extra_receivers,
        }

    def get_visible_pollutions(self, user_id: str) -> Dict[str, Any]:
        state = self._get_state(user_id)
        active = self.repo.list_active_pollution_ids()
        forced_until = state.get("forced_pollution_until")
        now_text = self._now_iso()
        if forced_until and forced_until > now_text:
            return {
                "visible_pollutions": active,
                "applied_reason": "forced",
                "current_san": state["current_san"],
                "max_san": state["max_san"],
            }
        ratio = 0 if state["max_san"] <= 0 else state["current_san"] / state["max_san"]
        return {
            "visible_pollutions": active if ratio < 0.30 else [],
            "applied_reason": "san_threshold" if ratio < 0.30 else "none",
            "current_san": state["current_san"],
            "max_san": state["max_san"],
        }

    def apply_number_pollution(self, text: str, visible_pollutions: List[str]) -> str:
        if "U1" not in visible_pollutions:
            return text
        return "".join(NUMERAL_GLYPHS.get(ch, ch) for ch in str(text))

    def apply_name_pollution(self, viewer_id: str, target_name: str, visible_pollutions: List[str]) -> str:
        if "U2" not in visible_pollutions:
            return target_name
        prefix = POLLUTION_TEXT_PREFIXES[hash(f"{viewer_id}:{target_name}") % len(POLLUTION_TEXT_PREFIXES)]
        return f"{prefix}{target_name}"

    def apply_text_pollution(self, text: str, visible_pollutions: List[str]) -> str:
        if "U5" not in visible_pollutions:
            return text
        polluted = []
        for idx, ch in enumerate(str(text)):
            polluted.append(ch)
            if idx % 2 == 0:
                polluted.append(random.choice(ZALGO_MARKS))
        return "".join(polluted)

    def get_state_view(self, user_id: str) -> Dict[str, Any]:
        state = self._get_state(user_id)
        if state["pending_san_cap_tokens"] > 0:
            self._grant_item(user_id, 57, state["pending_san_cap_tokens"])
            self._save_state(user_id, pending_san_cap_tokens=0)
            state["pending_san_cap_tokens"] = 0
        pending_event = self.events_by_id.get(state["pending_event_id"]) if state.get("pending_event_id") else None
        return {
            "success": True,
            "state": {
                "current_san": state["current_san"],
                "max_san": state["max_san"],
                "is_in_deepdive_today": bool(state.get("is_in_deepdive_today")),
                "forced_pollution_until": state.get("forced_pollution_until"),
            },
            "pending_event": pending_event,
            "pending_choice": state.get("pending_event_choice"),
            "true_names": self.list_true_names(user_id)["true_names"],
            "owned_authorities": self.repo.list_authorities_for_holder(user_id),
            "active_calls": self.list_active_calls()["calls"],
            "authority_board": self.repo.list_authorities(),
            "authority_board_sections": self._build_authority_board_sections(),
            "visible_pollutions": self.get_visible_pollutions(user_id),
        }

    def _build_authority_board_sections(self) -> List[Dict[str, Any]]:
        rows = self.repo.list_authorities()
        by_type: Dict[str, Dict[str, Any]] = {}
        for god_type, title, subtitle in (
            ("predict", "预知权柄", "先于潮声看见结果"),
            ("time", "时间权柄", "让一次夜潮多活几息"),
            ("pollute", "污染权柄", "把注视分给别的渔人"),
            ("sacrifice", "献祭权柄", "以自身理智换取暴烈改写"),
        ):
            by_type[god_type] = {
                "god_type": god_type,
                "title": title,
                "subtitle": subtitle,
                "tiers": [],
            }
        tier_order = ["upper", "middle", "lower"]
        for god_type in ("predict", "time", "pollute", "sacrifice"):
            god_rows = {row["tier"]: row for row in rows if row["god_type"] == god_type}
            for tier in tier_order:
                row = dict(god_rows.get(tier) or {})
                row["tier_label"] = TIER_DISPLAY[tier]
                row["tier_short_label"] = TIER_SHORT_DISPLAY[tier]
                row["holder_label"] = row.get("current_holder_nickname") or row.get("current_holder") or "暂无持有者"
                row["previous_holder_label"] = row.get("previous_holder_nickname") or row.get("previous_holder") or "无前任记录"
                by_type[god_type]["tiers"].append(row)
        return [by_type[key] for key in ("predict", "time", "pollute", "sacrifice")]

    def list_event_logs(self, user_id: str, limit: int = 50) -> Dict[str, Any]:
        return {"success": True, "logs": self.repo.list_event_logs(user_id, limit)}

    def list_true_names(self, user_id: str) -> Dict[str, Any]:
        rows = []
        for row in self.repo.list_true_names(user_id):
            row = dict(row)
            row["god_type_label"] = AUTHORITY_DISPLAY.get(row["god_type"], row["god_type"])
            row["tier_label"] = TIER_DISPLAY.get(row["tier"], row["tier"])
            rows.append(row)
        return {"success": True, "true_names": rows}

    def list_active_calls(self) -> Dict[str, Any]:
        rows = []
        for row in self.repo.list_active_calls():
            row = dict(row)
            row["god_type_label"] = AUTHORITY_DISPLAY.get(row["god_type"], row["god_type"])
            row["tier_label"] = TIER_DISPLAY.get(row["tier"], row["tier"])
            rows.append(row)
        return {"success": True, "calls": rows}

    def list_authorities(self) -> Dict[str, Any]:
        return {"success": True, "authorities": self.repo.list_authorities()}

    def _sample_fish_template(self, user) -> Any:
        strategy = self.fishing_service.fishing_zone_service.get_strategy(user.fishing_zone_id)
        rarity_distribution = strategy.get_fish_rarity_distribution(user)
        zone = self.inventory_repo.get_zone_by_id(user.fishing_zone_id)
        adjusted_distribution = rarity_distribution
        rarity_index = random.choices(range(len(adjusted_distribution)), weights=adjusted_distribution, k=1)[0]
        rarity = self.fishing_service._get_random_high_rarity(zone) if rarity_index == 5 else rarity_index + 1
        return self.fishing_service._get_fish_template(rarity, zone, 0.0)

    def _format_fish_preview(self, fish_template: Any) -> Dict[str, Any]:
        return {
            "fish_id": int(fish_template.fish_id),
            "name": fish_template.name,
            "rarity": int(fish_template.rarity),
            "base_value": int(fish_template.base_value),
        }

    def _grant_sampled_fish(self, user_id: str, fish_template: Any) -> None:
        self.inventory_repo.add_fish_to_inventory(user_id, int(fish_template.fish_id), quantity=1, quality_level=0)
        user = self.user_repo.get_by_id(user_id)
        if user:
            user.total_fishing_count += 1
            user.total_coins_earned += int(fish_template.base_value)
            self.user_repo.update(user)
        try:
            from ..domain.models import FishingRecord
            record = FishingRecord(
                record_id=0,
                user_id=user_id,
                fish_id=int(fish_template.fish_id),
                value=int(fish_template.base_value),
                timestamp=get_now(),
                rod_instance_id=None,
                accessory_instance_id=None,
                bait_id=None,
            )
            self.log_repo.add_fishing_record(record, log_to_records=False)
        except Exception:
            pass

    def prepare_predict(self, user_id: str, authority_id: str) -> Dict[str, Any]:
        authority = self.repo.get_authority(authority_id)
        if authority is None or authority.get("current_holder") != user_id:
            return {"success": False, "message": "你未持有该权柄。"}
        state = self._get_state(user_id)
        if state["current_san"] < AUTHORITY_SAN_COST["predict"]:
            return {"success": False, "message": "SAN 不足。"}
        self._apply_san_delta(user_id, -AUTHORITY_SAN_COST["predict"])
        sample_count = {"upper": 8, "middle": 5, "lower": 3}[authority["tier"]]
        user = self.user_repo.get_by_id(user_id)
        candidates = [self._format_fish_preview(self._sample_fish_template(user)) for _ in range(sample_count)]
        expires_at = (get_now() + timedelta(minutes=5)).isoformat(timespec="seconds")
        self._save_state(
            user_id,
            pending_predict_candidates=candidates,
            pending_predict_expires_at=expires_at,
        )
        return {"success": True, "candidates": candidates, "expires_at": expires_at}

    def confirm_predict(self, user_id: str, candidate_index: int) -> Dict[str, Any]:
        state = self._get_state(user_id)
        candidates = state.get("pending_predict_candidates") or []
        expires_at = state.get("pending_predict_expires_at")
        if not candidates or not expires_at or expires_at <= self._now_iso():
            self._save_state(user_id, pending_predict_candidates=[], pending_predict_expires_at=None)
            return {"success": False, "message": "预知候选已失效。"}
        if candidate_index < 0 or candidate_index >= len(candidates):
            return {"success": False, "message": "候选编号无效。"}
        candidate = candidates[candidate_index]
        fish_template = self.item_template_repo.get_fish_by_id(int(candidate["fish_id"]))
        if fish_template is None:
            return {"success": False, "message": "候选鱼数据不存在。"}
        self._grant_sampled_fish(user_id, fish_template)
        self._save_state(user_id, pending_predict_candidates=[], pending_predict_expires_at=None)
        return {"success": True, "message": f"你从预知中带回了【{fish_template.name}】。"}

    def use_time_authority(self, user_id: str, authority_id: str) -> Dict[str, Any]:
        authority = self.repo.get_authority(authority_id)
        if authority is None or authority.get("current_holder") != user_id:
            return {"success": False, "message": "你未持有该权柄。"}
        state = self._get_state(user_id)
        if state["current_san"] < AUTHORITY_SAN_COST["time"]:
            return {"success": False, "message": "SAN 不足。"}
        self._apply_san_delta(user_id, -AUTHORITY_SAN_COST["time"])
        casts = {"upper": 3, "middle": 2, "lower": 1}[authority["tier"]]
        user = self.user_repo.get_by_id(user_id)
        fishes = []
        for _ in range(casts):
            fish_template = self._sample_fish_template(user)
            self._grant_sampled_fish(user_id, fish_template)
            fishes.append(fish_template.name)
        return {"success": True, "message": f"时间向你倾斜，你额外获得了：{'、'.join(fishes)}。"}

    def use_pollute_authority(self, user_id: str, authority_id: str) -> Dict[str, Any]:
        authority = self.repo.get_authority(authority_id)
        if authority is None or authority.get("current_holder") != user_id:
            return {"success": False, "message": "你未持有该权柄。"}
        state = self._get_state(user_id)
        if state["current_san"] < AUTHORITY_SAN_COST["pollute"]:
            return {"success": False, "message": "SAN 不足。"}
        self._apply_san_delta(user_id, -AUTHORITY_SAN_COST["pollute"])
        sample_count = {"upper": 10, "middle": 5, "lower": 1}[authority["tier"]]
        marker = self._current_marker()
        candidates = [uid for uid in self.repo.get_signed_in_user_ids(marker) if uid != user_id]
        random.shuffle(candidates)
        chosen = candidates[:sample_count]
        until = (get_last_reset_time(self.daily_reset_hour) + timedelta(days=1)).isoformat(timespec="seconds")
        for target in chosen:
            self.repo.ensure_state(target)
            self._save_state(target, forced_pollution_until=until)
        return {
            "success": True,
            "message": f"你让 {len(chosen)} 名渔人感受到了被注视的目光。",
            "targets": chosen,
        }

    def use_sacrifice_authority(self, user_id: str, authority_id: str, item_type: str, token: str) -> Dict[str, Any]:
        authority = self.repo.get_authority(authority_id)
        if authority is None or authority.get("current_holder") != user_id:
            return {"success": False, "message": "你未持有该权柄。"}
        state = self._get_state(user_id)
        if state["current_san"] < state["max_san"]:
            return {"success": False, "message": "献祭需要以满额 SAN 为代价。"}
        max_rarity = {"upper": 10, "middle": 9, "lower": 8}[authority["tier"]]
        item_type = str(item_type or "").strip().lower()
        if item_type not in {"rod", "accessory", "bait", "item"}:
            return {"success": False, "message": "献祭类型必须是 rod/accessory/bait/item。"}
        replacement_name = ""
        if item_type == "rod":
            instance_id = self.fishing_service.inventory_service.resolve_rod_instance_id(user_id, token) if hasattr(self.fishing_service, "inventory_service") else None
            if instance_id is None:
                try:
                    instance_id = int(token)
                except Exception:
                    instance_id = None
            instance = self.inventory_repo.get_user_rod_instance_by_id(user_id, instance_id) if instance_id else None
            if instance is None:
                return {"success": False, "message": "找不到要献祭的鱼竿。"}
            template = self.item_template_repo.get_rod_by_id(instance.rod_id)
            if template is None or int(template.rarity) > max_rarity:
                return {"success": False, "message": "该鱼竿超出当前献祭档位允许的星级。"}
            candidates = [rod for rod in self.item_template_repo.get_all_rods() if int(rod.rarity) == int(template.rarity)]
            replacement = random.choice(candidates)
            self.inventory_repo.delete_rod_instance(instance.rod_instance_id)
            self.inventory_repo.add_rod_instance(user_id, replacement.rod_id, replacement.durability)
            replacement_name = replacement.name
        elif item_type == "accessory":
            instance_id = self.fishing_service.inventory_service.resolve_accessory_instance_id(user_id, token) if hasattr(self.fishing_service, "inventory_service") else None
            if instance_id is None:
                try:
                    instance_id = int(token)
                except Exception:
                    instance_id = None
            instance = self.inventory_repo.get_user_accessory_instance_by_id(user_id, instance_id) if instance_id else None
            if instance is None:
                return {"success": False, "message": "找不到要献祭的饰品。"}
            template = self.item_template_repo.get_accessory_by_id(instance.accessory_id)
            if template is None or int(template.rarity) > max_rarity:
                return {"success": False, "message": "该饰品超出当前献祭档位允许的星级。"}
            candidates = [acc for acc in self.item_template_repo.get_all_accessories() if int(acc.rarity) == int(template.rarity)]
            replacement = random.choice(candidates)
            self.inventory_repo.delete_accessory_instance(instance.accessory_instance_id)
            self.inventory_repo.add_accessory_instance(user_id, replacement.accessory_id)
            replacement_name = replacement.name
        elif item_type == "bait":
            bait_id = int(token)
            template = self.item_template_repo.get_bait_by_id(bait_id)
            inventory = self.inventory_repo.get_user_bait_inventory(user_id)
            if template is None or inventory.get(bait_id, 0) <= 0:
                return {"success": False, "message": "找不到要献祭的鱼饵。"}
            if int(template.rarity) > max_rarity:
                return {"success": False, "message": "该鱼饵超出当前献祭档位允许的星级。"}
            candidates = [bait for bait in self.item_template_repo.get_all_baits() if int(bait.rarity) == int(template.rarity)]
            replacement = random.choice(candidates)
            self.inventory_repo.decrease_item_quantity(user_id, bait_id, 0)  # noop for interface symmetry
            self.inventory_repo.update_bait_quantity(user_id, bait_id, -1)
            self.inventory_repo.update_bait_quantity(user_id, replacement.bait_id, 1)
            replacement_name = replacement.name
        else:
            item_id = int(token)
            template = self.item_template_repo.get_item_by_id(item_id)
            inventory = self.inventory_repo.get_user_item_inventory(user_id)
            if template is None or inventory.get(item_id, 0) <= 0:
                return {"success": False, "message": "找不到要献祭的道具。"}
            if int(template.rarity) > max_rarity:
                return {"success": False, "message": "该道具超出当前献祭档位允许的星级。"}
            candidates = [item for item in self.item_template_repo.get_all_items() if int(item.rarity) == int(template.rarity)]
            replacement = random.choice(candidates)
            self.inventory_repo.decrease_item_quantity(user_id, item_id, 1)
            self.inventory_repo.update_item_quantity(user_id, replacement.item_id, 1)
            replacement_name = replacement.name

        self._save_state(user_id, current_san=0)
        return {"success": True, "message": f"献祭完成，深渊回赠了【{replacement_name}】。你的 SAN 已归零。"}
