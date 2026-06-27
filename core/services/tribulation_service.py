"""玄幻渡劫玩法 V2：渡劫服务。

职责：
- 发起立即/预约渡劫，校验、扣除渡劫品、装备快照、锁定修为
- 渡劫预览：实时计算总权重、可冲击档位、命中率、最终成功率
- 调度：promote PENDING→ANNOUNCED；resolve 到期事件
- 单事件结算：品级判定（含天成执念）+ 成功率判定 + 修为返还/上升
- 启动时扫描历史漏结算事件
- 护法/观道 join 与奖励分发（V3 wave 接入，本 wave 仅留接口骨架）
"""

from __future__ import annotations

import json
import random
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger

from ..domain.tribulation_models import (
    CultivationProfile,
    TribulationEvent,
    REALMS,
    REALM_DISPLAY,
    QUALITY_DISPLAY,
    MODE_IMMEDIATE,
    MODE_RESERVED,
    STATUS_PENDING,
    STATUS_ANNOUNCED,
    STATUS_FINISHED,
    RESULT_SUCCESS,
    RESULT_FAILURE,
    PARTICIPANT_GUARD,
    PARTICIPANT_OBSERVER,
    next_realm,
    is_realm_higher_or_equal,
)
from ..repositories.sqlite_tribulation_repo import SqliteTribulationRepository
from . import tribulation_constants as C
from . import scifi_constants as SCIFI
from ..utils import get_now, get_last_reset_time


def _now_iso() -> str:
    return get_now().isoformat(timespec="seconds")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


class TribulationService:
    """渡劫核心服务。"""

    def __init__(
        self,
        tribulation_repo: SqliteTribulationRepository,
        cultivation_service,
        inventory_repo,
        item_template_repo,
        user_repo,
        game_config: Dict[str, Any],
    ):
        self.repo = tribulation_repo
        self.cultivation_service = cultivation_service
        self.inventory_repo = inventory_repo
        self.item_template_repo = item_template_repo
        self.user_repo = user_repo
        self.game_config = game_config
        self._tick_lock = threading.Lock()
        self._last_tick_at: Optional[datetime] = None
        # 通知回调（与 fishing_service 模式一致）
        self._notifier = None
        self._notification_target = "group"

    # ------------------------------------------------------------------
    # 通知器
    # ------------------------------------------------------------------
    def register_notifier(self, notifier, default_target: Optional[str] = None) -> None:
        self._notifier = notifier
        if default_target:
            self._notification_target = default_target

    def _notify(self, message: str) -> None:
        if self._notifier is None:
            return
        try:
            self._notifier(self._notification_target, message)
        except Exception as exc:
            logger.warning(f"[tribulation] notifier 调用失败: {exc}")

    # ------------------------------------------------------------------
    # 渡劫品识别
    # ------------------------------------------------------------------
    def list_eligible_items(self, user_id: str) -> List[Dict[str, Any]]:
        """返回玩家可作为渡劫品的鱼饵库存（rarity >= 5）。"""
        try:
            inv = self.inventory_repo.get_user_bait_inventory(user_id) or {}
            results = []
            for bait_id, qty in inv.items():
                if qty <= 0:
                    continue
                tmpl = self.item_template_repo.get_bait_by_id(int(bait_id))
                if tmpl is None:
                    continue
                if tmpl.rarity < 5:
                    continue
                results.append({
                    "bait_id": int(bait_id),
                    "name": tmpl.name,
                    "rarity": int(tmpl.rarity),
                    "quantity": int(qty),
                    "base_weight": C.get_item_base_weight(int(tmpl.rarity)),
                })
            results.sort(key=lambda x: (-x["rarity"], x["name"]))
            return results
        except Exception as exc:
            logger.warning(f"[tribulation] list_eligible_items 失败: {exc}")
            return []

    # ------------------------------------------------------------------
    # 装备快照
    # ------------------------------------------------------------------
    def _build_equipment_snapshot(self, user_id: str) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {"rod": None, "accessory": None}
        try:
            rod_inst = self.inventory_repo.get_user_equipped_rod(user_id)
            if rod_inst is not None:
                rod_tmpl = self.item_template_repo.get_rod_by_id(rod_inst.rod_id)
                if rod_tmpl is not None:
                    snapshot["rod"] = {
                        "rod_id": int(rod_inst.rod_id),
                        "name": rod_tmpl.name,
                        "rarity": int(rod_tmpl.rarity),
                        "refine_level": int(getattr(rod_inst, "refine_level", 1) or 1),
                    }
        except Exception as exc:
            logger.warning(f"[tribulation] snapshot rod 失败: {exc}")

        try:
            acc_inst = self.inventory_repo.get_user_equipped_accessory(user_id)
            if acc_inst is not None:
                acc_tmpl = self.item_template_repo.get_accessory_by_id(acc_inst.accessory_id)
                if acc_tmpl is not None:
                    snapshot["accessory"] = {
                        "accessory_id": int(acc_inst.accessory_id),
                        "name": acc_tmpl.name,
                        "rarity": int(acc_tmpl.rarity),
                        "refine_level": int(getattr(acc_inst, "refine_level", 1) or 1),
                    }
        except Exception as exc:
            logger.warning(f"[tribulation] snapshot acc 失败: {exc}")

        return snapshot

    @staticmethod
    def _calc_equipment_bonus(snapshot: Dict[str, Any]) -> Tuple[float, int]:
        """根据装备快照计算 (成功率加成%, 品级权重加成)。

        来自策划案 §8.2。
        """
        rod = snapshot.get("rod") if snapshot else None
        acc = snapshot.get("accessory") if snapshot else None
        rod_score = 0.0
        acc_score = 0.0
        if rod:
            rod_score = rod["rarity"] * (1 + 0.05 * (rod["refine_level"] - 1))
        if acc:
            acc_score = acc["rarity"] * (1 + 0.05 * (acc["refine_level"] - 1))
        total = rod_score + acc_score
        success_bonus = min(round(total * 0.3, 1), 8.0)
        weight_bonus = min(int(total // 6), 4)
        return float(success_bonus), int(weight_bonus)

    # ------------------------------------------------------------------
    # 权重 / 成功率
    # ------------------------------------------------------------------
    def _enrich_items(self, items_invested: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """给定 [{bait_id, count}]，回填 rarity / weight_per_unit / star_bucket。"""
        out = []
        for raw in items_invested or []:
            bait_id = int(raw.get("bait_id"))
            count = int(raw.get("count") or 0)
            if count <= 0:
                continue
            tmpl = self.item_template_repo.get_bait_by_id(bait_id)
            if tmpl is None or tmpl.rarity < 5:
                continue
            rarity = int(tmpl.rarity)
            out.append({
                "bait_id": bait_id,
                "name": tmpl.name,
                "rarity": rarity,
                "count": count,
                "weight_per_unit": C.get_item_base_weight(rarity),
                "star_bucket": C.star_bucket(rarity),
            })
        return out

    def _total_quality_weight(
        self,
        items: List[Dict[str, Any]],
        target_realm: str,
        equip_weight_bonus: int,
    ) -> int:
        """渡劫品 + 装备的总品级权重（策划案 §18.1）。"""
        total = 0.0
        coef_table = C.QUALITY_COEFFICIENT.get(target_realm, {})
        for it in items:
            base = it["weight_per_unit"] * it["count"]
            coef = coef_table.get(it["star_bucket"], 1.0)
            total += base * coef
        total += equip_weight_bonus
        return int(total)

    def _items_success_bonus(
        self,
        items: List[Dict[str, Any]],
        target_realm: str,
    ) -> float:
        """根据 §15.5 表，按 bucket 命中上限累加成功率加成。

        策划案没有给出每件鱼饵单独的成功率公式，仅给出"上限"。
        我们采用直观线性映射：
            该 bucket 实际成功率 = 上限 × min(1, sum(count) / threshold_unit)
        threshold_unit 按 §15.9 的建议投入量近似（5~28 中位 ~ 12）。
        """
        cap_table = C.ITEM_SUCCESS_BONUS_CAP.get(target_realm, {})
        bucket_count: Dict[str, int] = {"low56": 0, "star7": 0, "star8": 0, "star910": 0}
        for it in items:
            bucket_count[it["star_bucket"]] = bucket_count.get(it["star_bucket"], 0) + it["count"]

        # 推荐总投入量（§15.9）
        rec_total = {"zhuji": 8, "jindan": 12, "yuanying": 18, "huashen": 28}.get(target_realm, 12)

        bonus = 0.0
        for bucket, cnt in bucket_count.items():
            if cnt <= 0:
                continue
            cap = cap_table.get(bucket, 0.0)
            if cap <= 0:
                continue
            # 单 bucket 达到推荐量即拉满本 bucket 的上限
            ratio = min(1.0, cnt / max(1, rec_total))
            bonus += cap * ratio
        return round(bonus, 2)

    def _quality_hit_rate(
        self,
        target_quality: str,
        base_rate: float,
        tiancheng_protection: int,
    ) -> float:
        if target_quality != "tiancheng":
            return float(base_rate)
        extra = min(
            tiancheng_protection * C.TIANCHENG_PROTECTION_PER_STACK,
            C.TIANCHENG_PROTECTION_CAP,
        )
        return float(base_rate) + extra

    def _determine_candidate_quality(
        self,
        total_weight: int,
        target_realm: str,
    ) -> Tuple[str, float]:
        """选出本次可冲击的最高档与其基础命中率。"""
        tiers = C.QUALITY_TIER_TABLE.get(target_realm, [])
        candidate_quality = "fanxue"
        candidate_rate = 0.0
        for min_weight, quality, hit_rate in tiers:
            if total_weight >= min_weight:
                candidate_quality = quality
                candidate_rate = hit_rate
        return candidate_quality, float(candidate_rate)

    def _final_success_rate(
        self,
        target_realm: str,
        items_bonus: float,
        equip_bonus: float,
        buff_bonus: float,
        effective_guard_count: int,
        self_intervention_level: int,
        guards_with_intervention: Optional[List[int]],
    ) -> float:
        """策划案 §11.3 完整公式（含科幻接入点，V1 不进入）。"""
        base_rate = C.BASE_SUCCESS_RATE.get(target_realm, 0.0)
        self_rate = base_rate + items_bonus + equip_bonus + buff_bonus

        # 接入点 #1：渡劫者削弱（科幻天命截断）
        self_rate *= SCIFI.TRIBULATION_SELF_RATE_MULTIPLIER.get(
            max(0, min(int(self_intervention_level or 0), SCIFI.MAX_BRANCH_LEVEL)),
            1.0,
        )

        self_rate_capped = min(self_rate, C.SELF_SUCCESS_RATE_CAP)

        # 累加护法加成（接入点 #2：护法者削弱，V1 不进入）
        guard_bonus = 0.0
        interventions = guards_with_intervention or []
        for idx in range(effective_guard_count):
            bonus = 1.0
            if idx < len(interventions) and interventions[idx] > 0:
                bonus = 0.0
            guard_bonus += bonus

        return float(min(self_rate_capped + guard_bonus, C.FINAL_SUCCESS_RATE_CAP))

    # ------------------------------------------------------------------
    # 公开 API：预览
    # ------------------------------------------------------------------
    def preview(
        self,
        user_id: str,
        items_invested: List[Dict[str, Any]],
        target_realm: Optional[str] = None,
    ) -> Dict[str, Any]:
        profile = self.cultivation_service.get_or_create_profile(user_id)
        target = target_realm or next_realm(profile.current_realm)
        if target is None:
            return {"success": False, "message": "已达化神巅峰，无更高境界可渡。"}

        cap = C.get_realm_cap(profile.current_realm)
        if profile.accumulated_xiuwei < cap:
            return {
                "success": False,
                "message": f"当前修为 {profile.accumulated_xiuwei}/{cap}，未圆满，无法渡劫。",
            }

        items = self._enrich_items(items_invested)
        snapshot = self._build_equipment_snapshot(user_id)
        equip_success_bonus, equip_weight_bonus = self._calc_equipment_bonus(snapshot)
        total_weight = self._total_quality_weight(items, target, equip_weight_bonus)
        items_bonus = self._items_success_bonus(items, target)
        buff_bonus = profile.consecutive_failures * C.FAILURE_BUFF_PER_STACK

        candidate_quality, candidate_rate_base = self._determine_candidate_quality(total_weight, target)
        candidate_rate = self._quality_hit_rate(
            candidate_quality,
            candidate_rate_base,
            profile.get_tiancheng_count(target),
        )

        success_rate = self._final_success_rate(
            target_realm=target,
            items_bonus=items_bonus,
            equip_bonus=equip_success_bonus,
            buff_bonus=buff_bonus,
            effective_guard_count=0,
            self_intervention_level=profile.sci_fi_intervention_level,
            guards_with_intervention=None,
        )
        # 科幻觉醒协议渡劫乘区惩罚
        if profile.sci_fi_apex_singularity:
            success_rate *= SCIFI.APEX_TRIBULATION_MULTIPLIER["singularity"]
        elif profile.sci_fi_apex_abyss_unity:
            success_rate *= SCIFI.APEX_TRIBULATION_MULTIPLIER["abyss_unity"]
        elif profile.sci_fi_apex_fate_solitude:
            success_rate *= SCIFI.APEX_TRIBULATION_MULTIPLIER["fate_solitude"]
        elif profile.sci_fi_apex_resonance_summit:
            success_rate *= SCIFI.APEX_TRIBULATION_MULTIPLIER["resonance_summit"]

        return {
            "success": True,
            "target_realm": target,
            "target_realm_display": REALM_DISPLAY.get(target, target),
            "current_realm": profile.current_realm,
            "total_weight": total_weight,
            "candidate_quality": candidate_quality,
            "candidate_quality_display": QUALITY_DISPLAY.get(candidate_quality, candidate_quality),
            "candidate_hit_rate": round(candidate_rate, 2),
            "tiancheng_protection": profile.get_tiancheng_count(target),
            "items_bonus": round(items_bonus, 2),
            "equip_success_bonus": equip_success_bonus,
            "equip_weight_bonus": equip_weight_bonus,
            "buff_bonus": buff_bonus,
            "consecutive_failures": profile.consecutive_failures,
            "final_success_rate": round(success_rate, 2),
            "enriched_items": items,
            "equipment": snapshot,
            "accumulated_xiuwei": profile.accumulated_xiuwei,
        }

    # ------------------------------------------------------------------
    # 公开 API：发起
    # ------------------------------------------------------------------
    def start(
        self,
        user_id: str,
        mode: str,
        items_invested: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """发起渡劫。

        Args:
            mode: "immediate" 或 "reserved"
            items_invested: [{bait_id, count}, ...]
        """
        if mode not in (MODE_IMMEDIATE, MODE_RESERVED):
            return {"success": False, "message": "未知渡劫模式。"}

        profile = self.cultivation_service.get_or_create_profile(user_id)
        target = next_realm(profile.current_realm)
        if target is None:
            return {"success": False, "message": "已达化神巅峰，无更高境界可渡。"}

        cap = C.get_realm_cap(profile.current_realm)
        if profile.accumulated_xiuwei < cap:
            return {
                "success": False,
                "message": f"当前修为 {profile.accumulated_xiuwei}/{cap}，未圆满，无法渡劫。",
            }

        # 校验当前无进行中事件
        active = self.repo.get_active_event_for_user(user_id)
        if active is not None:
            return {"success": False, "message": "您已有进行中的渡劫事件，无法重复发起。"}

        # 校验库存
        items = self._enrich_items(items_invested)
        inv = self.inventory_repo.get_user_bait_inventory(user_id) or {}
        # 合并相同 bait_id（防止前端重复传同 id）
        merged: Dict[int, int] = {}
        for it in items:
            merged[it["bait_id"]] = merged.get(it["bait_id"], 0) + it["count"]
        for bait_id, count in merged.items():
            if int(inv.get(bait_id, 0)) < count:
                tmpl = self.item_template_repo.get_bait_by_id(bait_id)
                name = tmpl.name if tmpl else f"#{bait_id}"
                return {"success": False, "message": f"渡劫品 {name} 库存不足。"}

        # 装备快照
        snapshot = self._build_equipment_snapshot(user_id)

        now = get_now()
        scheduled_at, announce_at, status = self._compute_schedule(now, mode)

        # 扣除渡劫品（立即扣除）
        for bait_id, count in merged.items():
            self.inventory_repo.update_bait_quantity(user_id, bait_id, -count)

        event_id = self.repo.create_event(
            user_id=user_id,
            target_realm=target,
            mode=mode,
            status=status,
            equipment_snapshot=snapshot,
            items_invested=items,
            accumulated_xiuwei=profile.accumulated_xiuwei,
            created_at=now.isoformat(timespec="seconds"),
            announce_at=announce_at.isoformat(timespec="seconds") if announce_at else None,
            scheduled_at=scheduled_at.isoformat(timespec="seconds"),
        )

        # 立即模式：直接公示
        if mode == MODE_IMMEDIATE:
            self._broadcast_announce(event_id, user_id, target)

        return {
            "success": True,
            "event_id": event_id,
            "mode": mode,
            "status": status,
            "scheduled_at": scheduled_at.isoformat(timespec="seconds"),
            "announce_at": announce_at.isoformat(timespec="seconds") if announce_at else None,
            "target_realm": target,
        }

    def _compute_schedule(self, now: datetime, mode: str) -> Tuple[datetime, Optional[datetime], str]:
        """计算 scheduled_at / announce_at / status。"""
        reset_hour = int(self.game_config.get("daily_reset_hour", 0) or 0)
        # 下一次 daily_reset_hour 时刻
        next_reset = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
        if next_reset <= now:
            next_reset += timedelta(days=1)

        if mode == MODE_IMMEDIATE:
            return next_reset, None, STATUS_ANNOUNCED

        # 预约：announce_at = 下一次 reset；scheduled_at = announce_at 的次日 reset
        announce_at = next_reset
        scheduled_at = announce_at + timedelta(days=1)
        return scheduled_at, announce_at, STATUS_PENDING

    # ------------------------------------------------------------------
    # 心跳：promote / resolve
    # ------------------------------------------------------------------
    def tick(self) -> Dict[str, int]:
        """每分钟级心跳：promote PENDING→ANNOUNCED；resolve 到期 ANNOUNCED。"""
        if not self._tick_lock.acquire(blocking=False):
            return {"promoted": 0, "resolved": 0}
        try:
            now = get_now()
            now_iso = now.isoformat(timespec="seconds")
            promoted = 0
            for ev in self.repo.list_pending_ready_to_announce(now_iso):
                self.repo.update_event_status(ev.event_id, STATUS_ANNOUNCED)
                self._broadcast_announce(ev.event_id, ev.user_id, ev.target_realm)
                promoted += 1
            resolved = 0
            for ev in self.repo.list_announced_due_for_resolution(now_iso):
                try:
                    self._resolve_event(ev)
                    resolved += 1
                except Exception as exc:
                    logger.error(f"[tribulation] 结算事件 {ev.event_id} 失败: {exc}")
            self._last_tick_at = now
            return {"promoted": promoted, "resolved": resolved}
        finally:
            self._tick_lock.release()

    def scan_overdue_on_startup(self) -> Dict[str, int]:
        """启动时扫描历史漏结算事件。"""
        logger.info("[tribulation] 启动扫描漏结算事件...")
        return self.tick()

    # ------------------------------------------------------------------
    # 结算
    # ------------------------------------------------------------------
    def _resolve_event(self, ev: TribulationEvent) -> None:
        """单事件结算（W2 阶段：暂时不发放护法/观道奖励，由 W3 接入）。"""
        profile = self.cultivation_service.get_or_create_profile(ev.user_id)

        snapshot = ev.equipment_snapshot or {}
        items = ev.items_invested or []
        equip_success_bonus, equip_weight_bonus = self._calc_equipment_bonus(snapshot)
        total_weight = self._total_quality_weight(items, ev.target_realm, equip_weight_bonus)
        items_bonus = self._items_success_bonus(items, ev.target_realm)
        buff_bonus = profile.consecutive_failures * C.FAILURE_BUFF_PER_STACK

        # 品级判定
        candidate_quality, candidate_rate_base = self._determine_candidate_quality(total_weight, ev.target_realm)
        candidate_rate = self._quality_hit_rate(
            candidate_quality,
            candidate_rate_base,
            profile.get_tiancheng_count(ev.target_realm),
        )

        final_quality = "fanxue"
        if candidate_quality == "fanxue":
            final_quality = "fanxue"
        else:
            if random.random() * 100 < candidate_rate:
                final_quality = candidate_quality
            else:
                final_quality = C.QUALITY_DOWNGRADE[candidate_quality]

        # 护法（W3 接入）
        guards = self.repo.list_participants(ev.event_id, PARTICIPANT_GUARD)
        effective_guards = [g for g in guards if g.is_effective]
        observers = self.repo.list_participants(ev.event_id, PARTICIPANT_OBSERVER)
        daowang = len(observers)

        success_rate = self._final_success_rate(
            target_realm=ev.target_realm,
            items_bonus=items_bonus,
            equip_bonus=equip_success_bonus,
            buff_bonus=buff_bonus,
            effective_guard_count=len(effective_guards),
            self_intervention_level=profile.sci_fi_intervention_level,
            guards_with_intervention=None,
        )
        # 科幻觉醒协议渡劫乘区惩罚
        if profile.sci_fi_apex_singularity:
            success_rate *= SCIFI.APEX_TRIBULATION_MULTIPLIER["singularity"]
        elif profile.sci_fi_apex_abyss_unity:
            success_rate *= SCIFI.APEX_TRIBULATION_MULTIPLIER["abyss_unity"]
        elif profile.sci_fi_apex_fate_solitude:
            success_rate *= SCIFI.APEX_TRIBULATION_MULTIPLIER["fate_solitude"]
        elif profile.sci_fi_apex_resonance_summit:
            success_rate *= SCIFI.APEX_TRIBULATION_MULTIPLIER["resonance_summit"]

        # §6.5.2 天成自动成功
        if final_quality == "tiancheng":
            succeeded = True
        else:
            succeeded = (random.random() * 100) < success_rate

        # 写入结果
        now_iso = _now_iso()
        result = RESULT_SUCCESS if succeeded else RESULT_FAILURE
        self.repo.finalize_event(
            event_id=ev.event_id,
            resolved_at=now_iso,
            result=result,
            quality=final_quality if succeeded else None,
            final_success_rate=success_rate,
            final_total_weight=total_weight,
            daowang_collected=daowang,
        )

        # 更新修行档案
        if succeeded:
            profile.current_realm = ev.target_realm
            profile.current_realm_quality = final_quality
            profile.accumulated_xiuwei = 0
            profile.consecutive_failures = 0
            profile.realm_history[ev.target_realm] = final_quality

            # 天成执念
            if candidate_quality == "tiancheng":
                if final_quality == "tiancheng":
                    profile.set_tiancheng_count(ev.target_realm, 0)
                else:
                    profile.set_tiancheng_count(
                        ev.target_realm,
                        profile.get_tiancheng_count(ev.target_realm) + 1,
                    )
        else:
            refund_ratio = C.REFUND_BASE + min(
                daowang * C.REFUND_PER_DAOWANG, C.REFUND_MAX - C.REFUND_BASE
            )
            refund = int(ev.accumulated_xiuwei * refund_ratio)
            profile.accumulated_xiuwei = refund
            profile.consecutive_failures += 1

        self.cultivation_service.repo.upsert_profile(profile, now_iso)

        # 通知
        self._broadcast_result(ev, profile, result, final_quality, success_rate, len(effective_guards), daowang)

        # 奖励分发（W3 接入；占位调用，目前 list 为空）
        self._distribute_rewards_stub(ev, succeeded, items, effective_guards, observers)

    def _distribute_rewards_stub(self, ev, succeeded, items, guards, observers):
        """委托到正式实现。保留旧名供老调用点平滑替换。"""
        return self._distribute_rewards(ev, succeeded, items, guards, observers)

    # ------------------------------------------------------------------
    # 护法 / 观道
    # ------------------------------------------------------------------
    def join(self, user_id: str, event_id: int) -> Dict[str, Any]:
        """参与他人渡劫；系统按境界关系自动分配身份。"""
        ev = self.repo.get_event(event_id)
        if ev is None:
            return {"success": False, "message": "渡劫事件不存在。"}
        if ev.status != STATUS_ANNOUNCED:
            return {"success": False, "message": "该渡劫已不在公示期。"}
        if ev.user_id == user_id:
            return {"success": False, "message": "不可参与自己的渡劫。"}

        # 报名截止
        deadline = _parse_iso(ev.scheduled_at) - timedelta(minutes=C.JOIN_DEADLINE_MINUTES_BEFORE)
        if get_now() >= deadline:
            return {"success": False, "message": "报名已截止。"}

        # 渡劫者本人若有进行中事件，不可参与他人
        my_active = self.repo.get_active_event_for_user(user_id)
        if my_active is not None:
            return {"success": False, "message": "您有进行中的渡劫事件，不可参与他人。"}

        # 重复参与
        existing = self.repo.get_participant(event_id, user_id)
        if existing is not None:
            return {"success": False, "message": "您已参与本次渡劫。"}

        # 按境界关系判定身份
        my_profile = self.cultivation_service.get_or_create_profile(user_id)
        host_profile = self.cultivation_service.get_or_create_profile(ev.user_id)
        if is_realm_higher_or_equal(my_profile.current_realm, host_profile.current_realm):
            ptype = PARTICIPANT_GUARD
        else:
            ptype = PARTICIPANT_OBSERVER

        joined_at = _now_iso()
        is_effective = True
        if ptype == PARTICIPANT_GUARD and int(my_profile.sci_fi_intervention_level or 0) >= 1:
            is_effective = False

        ok = self.repo.add_participant(event_id, user_id, ptype, joined_at, is_effective=is_effective)
        if not ok:
            return {"success": False, "message": "已存在参与记录。"}

        return {
            "success": True,
            "event_id": event_id,
            "type": ptype,
            "message": (
                f"已加入 #{event_id}（{'护法' if ptype == PARTICIPANT_GUARD else '观道'}）"
                if is_effective
                else f"已加入 #{event_id}（护法，受科幻干预影响，本次护法不会提供成功率加成）"
            ),
        }

    # ------------------------------------------------------------------
    # 日次数重置
    # ------------------------------------------------------------------
    def _ensure_daily_counts_reset(self, profile: CultivationProfile, now: datetime) -> None:
        """与 daily_reset_hour 对齐重置日参与奖励次数。"""
        reset_hour = int(self.game_config.get("daily_reset_hour", 0) or 0)
        last_reset = get_last_reset_time(reset_hour)
        if profile.daily_count_reset_at:
            try:
                prev = _parse_iso(profile.daily_count_reset_at)
            except Exception:
                prev = None
        else:
            prev = None
        if prev is None or prev < last_reset:
            profile.daily_guard_reward_count = 0
            profile.daily_observer_reward_count = 0
            profile.daily_count_reset_at = last_reset.isoformat(timespec="seconds")

    # ------------------------------------------------------------------
    # 奖励发放
    # ------------------------------------------------------------------
    def _calculate_guard_pool(self, items: List[Dict[str, Any]]) -> Tuple[float, float]:
        """计算护法基础池与追加池（按 §9.2.2 公式，单位 = 5 星玄幻鱼饵等值）。"""
        total_weight = 0.0
        for it in items:
            total_weight += it["weight_per_unit"] * it["count"]
        base_pool = total_weight * C.GUARD_BASE_POOL_RATIO
        extra_pool = total_weight * C.GUARD_EXTRA_POOL_RATIO
        return base_pool, extra_pool

    @staticmethod
    def _split_5star_units(units: int) -> Dict[str, int]:
        """按 §9.2.4 换算规则把单位拆为 9/7/5 星玄幻鱼饵的"概念发放表"。

        Returns:
            {"unit_9star": x, "unit_7star": y, "unit_5star": z}
            注意：这里只产生数量结构供后续映射为实际 bait_id；不在此处真正进入库存。
        """
        units = max(0, int(units))
        nine = 0
        seven = 0
        five = 0
        if units >= 9:
            nine = units // 9
            units -= nine * 9
        if units >= 4:
            seven = units // 4
            units -= seven * 4
        five = units
        return {"unit_9star": nine, "unit_7star": seven, "unit_5star": five}

    def _resolve_reward_bait_ids(self) -> Dict[str, Optional[int]]:
        """选取代表性 5/7/9 星玄幻鱼饵 bait_id，按 rarity 取价格最低的一条。"""
        result: Dict[str, Optional[int]] = {"unit_5star": None, "unit_7star": None, "unit_9star": None}
        try:
            for rarity, key in ((5, "unit_5star"), (7, "unit_7star"), (9, "unit_9star")):
                candidates = []
                for b in self.item_template_repo.get_all_baits() or []:
                    if b.rarity == rarity:
                        candidates.append(b)
                if candidates:
                    candidates.sort(key=lambda x: (getattr(x, "cost", 0) or 0, x.bait_id))
                    result[key] = int(candidates[0].bait_id)
        except Exception as exc:
            logger.warning(f"[tribulation] 选取奖励 bait_id 失败: {exc}")
        return result

    def _distribute_rewards(
        self,
        ev: TribulationEvent,
        succeeded: bool,
        items: List[Dict[str, Any]],
        guards,
        observers,
    ) -> None:
        """正式奖励发放（策划案 §9.2.3 / §9.3 / §18.6）。"""
        if not items:
            items = ev.items_invested or []

        base_pool, extra_pool = self._calculate_guard_pool(items)
        bait_ids = self._resolve_reward_bait_ids()
        now = get_now()
        now_iso = now.isoformat(timespec="seconds")

        # ---- 护法 ----
        if guards:
            base_per = int(base_pool // len(guards))
            extra_per = 0
            if succeeded:
                eff = [g for g in guards if g.is_effective]
                if eff:
                    extra_per = int(extra_pool // len(eff))
            for g in guards:
                total_units = base_per + (extra_per if g.is_effective and succeeded else 0)
                if total_units <= 0:
                    self.repo.mark_participant_reward(g.participant_id, {"units": 0}, xiuwei_granted=0)
                    continue
                # 检查日上限
                gp = self.cultivation_service.get_or_create_profile(g.user_id)
                self._ensure_daily_counts_reset(gp, now)
                if gp.daily_guard_reward_count >= C.DAILY_GUARD_REWARD_CAP:
                    self.repo.mark_participant_reward(g.participant_id, {"units": 0, "reason": "daily_cap"}, xiuwei_granted=0)
                    continue
                split = self._split_5star_units(total_units)
                granted: Dict[str, int] = {}
                for key, qty in split.items():
                    bait_id = bait_ids.get(key)
                    if not bait_id or qty <= 0:
                        continue
                    try:
                        self.inventory_repo.update_bait_quantity(g.user_id, bait_id, qty)
                        granted[str(bait_id)] = qty
                    except Exception as exc:
                        logger.warning(f"[tribulation] 护法奖励发放失败 user={g.user_id} bait={bait_id}: {exc}")
                gp.daily_guard_reward_count += 1
                self.cultivation_service.repo.upsert_profile(gp, now_iso)
                self.repo.mark_participant_reward(g.participant_id, {"units": total_units, "baits": granted}, xiuwei_granted=0)

        # ---- 观道 ----
        target_xiuwei_cap = C.get_realm_cap(ev.target_realm)
        for o in observers:
            op = self.cultivation_service.get_or_create_profile(o.user_id)
            self._ensure_daily_counts_reset(op, now)

            # 日上限耗尽：不发修为
            if op.daily_observer_reward_count >= C.DAILY_OBSERVER_REWARD_CAP:
                self.repo.mark_participant_reward(o.participant_id, {"reason": "daily_cap"}, xiuwei_granted=0)
                continue

            # 自身阶段修为上限
            self_cap = C.get_realm_cap(op.current_realm)
            headroom = self_cap - op.accumulated_xiuwei
            if headroom <= 0:
                self.repo.mark_participant_reward(o.participant_id, {"reason": "self_cap_full"}, xiuwei_granted=0)
                continue

            base_grant = int(target_xiuwei_cap * C.OBSERVER_XIUWEI_RATIO)
            actual = max(0, min(base_grant, headroom))
            if actual <= 0:
                self.repo.mark_participant_reward(o.participant_id, {"reason": "no_grant"}, xiuwei_granted=0)
                continue
            op.accumulated_xiuwei += actual
            op.daily_observer_reward_count += 1
            self.cultivation_service.repo.upsert_profile(op, now_iso)
            self.repo.mark_participant_reward(o.participant_id, {"xiuwei": actual}, xiuwei_granted=actual)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def get_event_view(self, event_id: int) -> Dict[str, Any]:
        ev = self.repo.get_event(event_id)
        if ev is None:
            return {"success": False, "message": "事件不存在。"}
        guards = self.repo.list_participants(event_id, PARTICIPANT_GUARD)
        observers = self.repo.list_participants(event_id, PARTICIPANT_OBSERVER)
        host_user = self.user_repo.get_by_id(ev.user_id)
        return {
            "success": True,
            "event_id": ev.event_id,
            "host_user_id": ev.user_id,
            "host_nickname": getattr(host_user, "nickname", None) or ev.user_id,
            "target_realm": ev.target_realm,
            "target_realm_display": REALM_DISPLAY.get(ev.target_realm, ev.target_realm),
            "mode": ev.mode,
            "status": ev.status,
            "scheduled_at": ev.scheduled_at,
            "announce_at": ev.announce_at,
            "resolved_at": ev.resolved_at,
            "result": ev.result,
            "quality": ev.quality,
            "quality_display": QUALITY_DISPLAY.get(ev.quality or "", None) if ev.quality else None,
            "final_success_rate": ev.final_success_rate,
            "final_total_weight": ev.final_total_weight,
            "daowang_collected": ev.daowang_collected,
            "items_invested": ev.items_invested,
            "equipment_snapshot": ev.equipment_snapshot,
            "guard_count": len(guards),
            "observer_count": len(observers),
        }

    def list_active_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        events = self.repo.list_announced(limit=limit)
        out = []
        for ev in events:
            host_user = self.user_repo.get_by_id(ev.user_id)
            out.append({
                "event_id": ev.event_id,
                "host_user_id": ev.user_id,
                "host_nickname": getattr(host_user, "nickname", None) or ev.user_id,
                "target_realm": ev.target_realm,
                "target_realm_display": REALM_DISPLAY.get(ev.target_realm, ev.target_realm),
                "mode": ev.mode,
                "scheduled_at": ev.scheduled_at,
                "guard_count": self.repo.count_participants(ev.event_id, PARTICIPANT_GUARD),
                "observer_count": self.repo.count_participants(ev.event_id, PARTICIPANT_OBSERVER),
            })
        return out

    # ------------------------------------------------------------------
    # 境界重修
    # ------------------------------------------------------------------
    def reset_realm(self, user_id: str) -> Dict[str, Any]:
        """境界重修（策划案 §13）。"""
        profile = self.cultivation_service.get_or_create_profile(user_id)
        if not profile.realm_history:
            return {"success": False, "message": "尚未完成任何渡劫，无可重修。"}

        active = self.repo.get_active_event_for_user(user_id)
        if active is not None:
            return {"success": False, "message": "您有进行中的渡劫事件，不可重修。"}

        lowest = profile.lowest_quality()
        # 找到该 lowest 品级首次出现的境界
        target_reset_realm = None
        for r in REALMS:
            if profile.realm_history.get(r) == lowest:
                target_reset_realm = r
                break
        if target_reset_realm is None:
            return {"success": False, "message": "无法判定重修起点。"}

        # 清空该境界及之后的渡劫记录与品级历史
        start_idx = REALMS.index(target_reset_realm)
        for r in REALMS[start_idx:]:
            profile.realm_history.pop(r, None)
            profile.tiancheng_protection.pop(r, None)

        # 重修后玩家状态：当前境界回退到 reset 起点的前一个，修为按 90% 继承
        prev_idx = max(0, start_idx - 1)
        profile.current_realm = REALMS[prev_idx]
        profile.current_realm_quality = profile.realm_history.get(profile.current_realm)
        profile.accumulated_xiuwei = int(profile.accumulated_xiuwei * C.REPAIR_XIUWEI_INHERIT_RATIO)
        # 当前境界修为不能超过其上限
        cap_now = C.get_realm_cap(profile.current_realm)
        if profile.accumulated_xiuwei > cap_now:
            profile.accumulated_xiuwei = cap_now
        profile.consecutive_failures = 0

        self.cultivation_service.repo.upsert_profile(profile, _now_iso())
        return {
            "success": True,
            "reset_from_realm": target_reset_realm,
            "current_realm": profile.current_realm,
            "accumulated_xiuwei": profile.accumulated_xiuwei,
        }

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------
    def _broadcast_announce(self, event_id: int, user_id: str, target_realm: str) -> None:
        try:
            user = self.user_repo.get_by_id(user_id)
            nickname = getattr(user, "nickname", None) or user_id
            target_label = REALM_DISPLAY.get(target_realm, target_realm)
            msg = f"⚡【渡劫公示】{nickname} 即将冲击 {target_label}！(ID #{event_id})\n输入 /参与渡劫 {event_id} 加入护法或观道。"
            self._notify(msg)
        except Exception as exc:
            logger.warning(f"[tribulation] 公示通知失败: {exc}")

    def _broadcast_result(
        self,
        ev: TribulationEvent,
        profile: CultivationProfile,
        result: str,
        quality: Optional[str],
        success_rate: float,
        guard_count: int,
        daowang: int,
    ) -> None:
        try:
            user = self.user_repo.get_by_id(ev.user_id)
            nickname = getattr(user, "nickname", None) or ev.user_id
            target_label = REALM_DISPLAY.get(ev.target_realm, ev.target_realm)
            if result == RESULT_SUCCESS:
                qlabel = QUALITY_DISPLAY.get(quality or "fanxue", "凡蜕")
                msg = (
                    f"🌟【渡劫成功】{nickname} 以 {qlabel}{target_label} 完成突破！\n"
                    f"成功率 {success_rate:.1f}%，护法 {guard_count}，观道 {daowang}。"
                )
            else:
                msg = (
                    f"💥【渡劫失败】{nickname} 冲击 {target_label} 失败。\n"
                    f"成功率 {success_rate:.1f}%，护法 {guard_count}，观道 {daowang}；修为已按道望返还。"
                )
            self._notify(msg)
        except Exception as exc:
            logger.warning(f"[tribulation] 结算通知失败: {exc}")
