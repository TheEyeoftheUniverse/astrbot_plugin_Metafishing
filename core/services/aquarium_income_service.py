"""水族箱稀有鱼展览收益服务。

核心职责：
- 按 K=0.30 公式计算每个时间窗口的「吸引力」（金币内部值）。
- 将吸引力映射为对应档位的"钱袋"道具发到玩家背包。
- 与交易所价格刷新窗口（ExchangePriceService.get_update_schedule）保持一致。
- 提供 evaluate_pending（懒补发）和 claim_all（玩家主动领取）两个核心入口。

需求文档：docs/requirements/2026-05-08-aquarium-rare-fish-income.md
执行计划：docs/plans/2026-05-08-aquarium-rare-fish-income-execution-plan.md
"""

from __future__ import annotations

import json
import math
import random
import threading
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger

from ..repositories.abstract_repository import (
    AbstractAquariumIncomeRepository,
    AbstractInventoryRepository,
    AbstractItemTemplateRepository,
    AbstractUserRepository,
)
from ..utils import get_now, get_current_daily_marker, calculate_after_refine, DATETIME_FORMAT


# ---------------------------------------------------------------------------
# 常量（与需求文档第 1 节锁定一致）
# ---------------------------------------------------------------------------

RARITY_THRESHOLD = 4  # 4★ 及以上参与判定

RARITY_WEIGHTS: Dict[int, int] = {
    4: 200,
    5: 600,
    6: 2000,
    7: 6000,
    8: 20000,
    9: 80000,
    10: 300000,
}

K_FACTOR = 0.30
SINGLE_WINDOW_HARD_CAP = 2_000_000
DAILY_SOFT_CAP = 15_000_000
DAILY_OVERFLOW_DECAY = 0.30
RANDOM_BAND = (0.8, 1.2)
QUALITY_COEFFICIENT = {0: 1.0, 1: 2.0}
ROD_RARE_CHANCE_SCALE = 0.5

# 钱袋档位（item_id 来自 initial_seed.sql, 1-6）
# 区间下限 (>=) → (item_id, qty 公式 amount/divisor)
POUCH_TIERS: List[Tuple[int, int, str, int]] = [
    # (lower_bound, item_id, item_name, divisor)
    (5_000_000, 6, "龙之钱袋", 2_000_000),
    (500_000, 5, "巨型钱袋", 250_000),
    (200_000, 4, "神秘钱袋", 120_000),
    (50_000, 3, "大号钱袋", 50_000),
    (10_000, 2, "中号钱袋", 10_000),
    (500, 1, "小钱袋", 1_000),
]

# 当玩家水族箱有 4★+ 鱼但收益数值未达成最低档位（小钱袋下限 500）时，
# 仍发放 1 个"小钱袋"作为保底，保证每次结算都有钱袋落袋（用户体感不受随机扰动惩罚）。
FALLBACK_MIN_POUCH = ("小钱袋", 1, 1)  # (item_name, item_id, quantity)


@dataclass
class PouchPayout:
    item_id: int
    item_name: str
    quantity: int


# ---------------------------------------------------------------------------
# 服务实现
# ---------------------------------------------------------------------------


class AquariumIncomeService:
    """水族箱展览收益服务。"""

    def __init__(
        self,
        income_repo: AbstractAquariumIncomeRepository,
        inventory_repo: AbstractInventoryRepository,
        user_repo: AbstractUserRepository,
        item_template_repo: AbstractItemTemplateRepository,
        game_config: Dict[str, Any],
        exchange_price_service=None,
        aquarium_quips_service=None,
    ):
        self.income_repo = income_repo
        self.inventory_repo = inventory_repo
        self.user_repo = user_repo
        self.item_template_repo = item_template_repo
        self.game_config = game_config or {}
        self.exchange_price_service = exchange_price_service
        self.aquarium_quips_service = aquarium_quips_service
        self._evaluation_lock = threading.Lock()

    # ---- 配置 ----------------------------------------------------------

    @property
    def daily_reset_hour(self) -> int:
        return int(self.game_config.get("daily_reset_hour", 0) or 0)

    def _get_window_times(self) -> List[dt_time]:
        if self.exchange_price_service is not None:
            try:
                schedule = self.exchange_price_service.get_update_schedule()
                if schedule:
                    return list(schedule)
            except Exception as exc:
                logger.warning(f"取交易所窗口列表失败，回退默认: {exc}")
        # 默认 09:00 / 15:00 / 21:00
        return [dt_time(hour=9), dt_time(hour=15), dt_time(hour=21)]

    # ---- 核心：单次窗口收益计算 ---------------------------------------

    def compute_window_income(
        self,
        user_id: str,
        window_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """纯计算（不写库）。返回收益详情。"""
        if window_dt is None:
            window_dt = get_now()

        aquarium_items = self.inventory_repo.get_aquarium_inventory(user_id)
        # 加载稀有度（fish_template.rarity）— 一次性批量查询
        raw_score = 0
        snapshot: List[Dict[str, Any]] = []
        for item in aquarium_items:
            if item.quantity <= 0:
                continue
            fish = self.item_template_repo.get_fish_by_id(item.fish_id)
            if not fish or fish.rarity < RARITY_THRESHOLD:
                continue
            weight = RARITY_WEIGHTS.get(int(fish.rarity), 0)
            if weight <= 0:
                continue
            quality_coef = QUALITY_COEFFICIENT.get(int(item.quality_level or 0), 1.0)
            contribution = int(weight * item.quantity * quality_coef)
            raw_score += contribution
            snapshot.append({
                "fish_id": int(fish.fish_id),
                "name": fish.name,
                "rarity": int(fish.rarity),
                "quality_level": int(item.quality_level or 0),
                "quantity": int(item.quantity),
                "weight": int(weight),
                "contribution": contribution,
            })

        equipment_multiplier = self._compute_equipment_multiplier(user_id)
        randomness = random.uniform(RANDOM_BAND[0], RANDOM_BAND[1])
        computed = int(raw_score * equipment_multiplier * randomness * K_FACTOR)
        capped = min(computed, SINGLE_WINDOW_HARD_CAP)

        return {
            "raw_score": raw_score,
            "equipment_multiplier": equipment_multiplier,
            "randomness": randomness,
            "computed_amount": computed,
            "capped_amount": capped,
            "fish_snapshot": snapshot,
        }

    def _compute_equipment_multiplier(self, user_id: str) -> float:
        """`(1 + accessory.bonus_coin_modifier_delta) × (1 + 0.5 × rod.bonus_rare_fish_chance)`，
        都经过精炼修正；玩家未装备相应装备时对应因子取 1.0。
        """
        accessory_factor = 1.0
        rod_factor = 1.0

        try:
            equipped_acc = self.inventory_repo.get_user_equipped_accessory(user_id)
            if equipped_acc:
                acc_template = self.item_template_repo.get_accessory_by_id(equipped_acc.accessory_id)
                if acc_template and acc_template.bonus_coin_modifier:
                    refined = calculate_after_refine(
                        float(acc_template.bonus_coin_modifier),
                        refine_level=int(equipped_acc.refine_level or 1),
                        rarity=int(acc_template.rarity or 0),
                    )
                    delta = max(0.0, refined - 1.0)
                    accessory_factor = 1.0 + delta
        except Exception as exc:
            logger.warning(f"读取装备饰品收益加成失败 user={user_id}: {exc}")

        try:
            equipped_rod = self.inventory_repo.get_user_equipped_rod(user_id)
            if equipped_rod:
                rod_template = self.item_template_repo.get_rod_by_id(equipped_rod.rod_id)
                if rod_template and rod_template.bonus_rare_fish_chance:
                    refined = calculate_after_refine(
                        float(rod_template.bonus_rare_fish_chance),
                        refine_level=int(equipped_rod.refine_level or 1),
                        rarity=int(rod_template.rarity or 0),
                    )
                    rod_factor = 1.0 + max(0.0, refined) * ROD_RARE_CHANCE_SCALE
        except Exception as exc:
            logger.warning(f"读取装备鱼竿稀有率加成失败 user={user_id}: {exc}")

        return float(accessory_factor * rod_factor)

    # ---- 待领取补发 ----------------------------------------------------

    def evaluate_pending(self, user_id: str) -> List[Dict[str, Any]]:
        """补齐当日所有过去窗口的待领取记录到 DB；返回所有未领取列表。"""
        with self._evaluation_lock:
            now = get_now()
            today_marker = get_current_daily_marker(self.daily_reset_hour)
            window_date = today_marker.isoformat()

            window_times = self._get_window_times()
            now_time = now.time()
            for w in window_times:
                if w <= now_time:
                    self._ensure_window_pending(user_id, window_date, w, now)

            return self.income_repo.get_pending(user_id)

    def _ensure_window_pending(
        self,
        user_id: str,
        window_date: str,
        window_time_obj: dt_time,
        now: datetime,
    ) -> None:
        window_time_str = window_time_obj.strftime("%H:%M:%S")
        if self.income_repo.has_window(user_id, window_date, window_time_str):
            return

        result = self.compute_window_income(user_id, now)
        # 即使 raw_score == 0（无稀有鱼）也记录一条占位，避免反复计算；
        # 领取阶段会按收益区间选择不发钱袋的处理路径。
        snapshot_json = json.dumps(result["fish_snapshot"], ensure_ascii=False)
        self.income_repo.upsert_pending(
            user_id=user_id,
            window_date=window_date,
            window_time=window_time_str,
            raw_score=int(result["raw_score"]),
            equipment_multiplier=float(result["equipment_multiplier"]),
            randomness=float(result["randomness"]),
            computed_amount=int(result["computed_amount"]),
            capped_amount=int(result["capped_amount"]),
            fish_snapshot_json=snapshot_json,
            created_at=now.strftime(DATETIME_FORMAT),
        )

    # ---- 领取流程 ------------------------------------------------------

    def claim_all(self, user_id: str) -> Dict[str, Any]:
        """领取该用户所有待领取记录，发放钱袋并返回叙事文本。"""
        pendings = self.evaluate_pending(user_id)

        if not pendings:
            return {
                "success": True,
                "claimed_count": 0,
                "total_amount": 0,
                "pouches": [],
                "narrations": [],
                "message": "暂无可领取的水族箱展览收益",
            }

        today_marker = get_current_daily_marker(self.daily_reset_hour)
        today_str = today_marker.isoformat()
        today_already_claimed = self.income_repo.get_daily_claimed_total(user_id, today_str)

        pouches_summary: Dict[int, PouchPayout] = {}
        narrations: List[str] = []
        record_keys: List[Tuple[str, str, str]] = []
        total_amount = 0

        # 准备 narration 共享上下文（一次查询活跃旁人）
        narration_ctx = self._prepare_narration_context(user_id) if self.aquarium_quips_service else None

        for pending in pendings:
            amount = int(pending["capped_amount"])

            # 每日软上限（仅对 window_date == 今日 的 pending 生效）
            if pending["window_date"] == today_str and amount > 0:
                amount = self._apply_daily_soft_cap(amount, today_already_claimed)
                today_already_claimed += amount

            total_amount += amount
            # 判定是否有鱼参与（来源：写入 pending 时的 fish_snapshot）
            try:
                snapshot_for_check = json.loads(pending.get("fish_snapshot") or "[]")
            except (TypeError, ValueError):
                snapshot_for_check = []
            has_fish = bool(snapshot_for_check)

            pouch = self._pick_pouch(amount, has_fish=has_fish)
            if pouch:
                self.inventory_repo.add_item_to_user(user_id, pouch.item_id, pouch.quantity)
                merged = pouches_summary.get(pouch.item_id)
                if merged:
                    merged.quantity += pouch.quantity
                else:
                    pouches_summary[pouch.item_id] = PouchPayout(
                        item_id=pouch.item_id,
                        item_name=pouch.item_name,
                        quantity=pouch.quantity,
                    )

            if narration_ctx is not None:
                snapshot_json = pending.get("fish_snapshot") or "[]"
                narration = self._compose_narration(snapshot_json, pouch, narration_ctx)
                if narration:
                    narrations.append(narration)

            record_keys.append((pending["user_id"], pending["window_date"], pending["window_time"]))

        claimed_at = get_now().strftime(DATETIME_FORMAT)
        self.income_repo.mark_claimed(record_keys, claimed_at)

        return {
            "success": True,
            "claimed_count": len(pendings),
            "total_amount": total_amount,
            "pouches": [
                {"item_id": p.item_id, "item_name": p.item_name, "quantity": p.quantity}
                for p in pouches_summary.values()
            ],
            "narrations": narrations,
            "message": f"成功领取 {len(pendings)} 次展览收益",
        }

    @staticmethod
    def _apply_daily_soft_cap(amount: int, already_claimed_today: int) -> int:
        """按每日软上限处理：超出部分 ×0.3 衰减。"""
        if already_claimed_today >= DAILY_SOFT_CAP:
            return int(amount * DAILY_OVERFLOW_DECAY)
        if already_claimed_today + amount <= DAILY_SOFT_CAP:
            return amount
        # 跨边界：内部部分按原值，超出部分衰减
        within = DAILY_SOFT_CAP - already_claimed_today
        overflow = amount - within
        return int(within + overflow * DAILY_OVERFLOW_DECAY)

    @staticmethod
    def _pick_pouch(amount: int, has_fish: bool = False) -> Optional[PouchPayout]:
        """根据收益金额映射钱袋档位与数量。

        amount 命中任一区间档 → 返回对应档位 ×N
        amount 低于最低档（500）但 has_fish=True → 保底返回 小钱袋 ×1
        amount <= 0 且无鱼参与判定 → 返回 None
        """
        if amount <= 0 and not has_fish:
            return None
        for lower_bound, item_id, item_name, divisor in POUCH_TIERS:
            if amount >= lower_bound:
                qty = max(1, math.ceil(amount / divisor))
                return PouchPayout(item_id=item_id, item_name=item_name, quantity=qty)
        if has_fish:
            return PouchPayout(item_id=1, item_name="小钱袋", quantity=1)
        return None

    # ---- Narration（叙事拼装） -----------------------------------------

    def _prepare_narration_context(self, user_id: str) -> Dict[str, Any]:
        """一次性准备好旁人池与动词池，避免 narration 内部重复查询。"""
        try:
            neighbors = self._collect_neighbor_pool(user_id)
        except Exception as exc:
            logger.warning(f"获取活跃旁人列表失败: {exc}")
            neighbors = []

        return {
            "neighbors": neighbors,
            "view_action_pool": [
                "凝视着", "端详着", "驻足看着", "凝神望着",
                "隔着玻璃看着", "认真打量着", "静静欣赏着",
            ],
            "thought_pool": ["觉得", "感觉", "寻思"],
            "npc_pool": ["路过的渔夫", "迷途的旅人", "某位老钓友", "邻塘的小孩"],
            "used_neighbors": set(),
        }

    def _collect_neighbor_pool(self, current_user_id: str) -> List[str]:
        """7 天内活跃且有昵称的玩家昵称列表（不含自己）。"""
        from datetime import timedelta
        since = get_now() - timedelta(days=7)
        # 通过 user_repo 全量遍历可能开销大；用一个原生 SQL 在 inventory_repo 的连接里查
        try:
            users = self.user_repo.get_all_users(limit=200)
        except Exception:
            return []

        names: List[str] = []
        for u in users:
            if not u or not getattr(u, "user_id", None):
                continue
            if str(u.user_id) == str(current_user_id):
                continue
            nickname = (getattr(u, "nickname", None) or "").strip()
            if not nickname:
                continue
            last_login = getattr(u, "last_login_time", None)
            if last_login is None:
                # 没有 last_login 字段也作为活跃候选（兼容老数据）
                names.append(nickname)
                continue
            try:
                if isinstance(last_login, str):
                    parsed = datetime.strptime(last_login[:19], DATETIME_FORMAT)
                else:
                    parsed = last_login
                if parsed >= since:
                    names.append(nickname)
            except Exception:
                names.append(nickname)
        return names

    def _pick_neighbor_name(self, ctx: Dict[str, Any]) -> str:
        neighbors = ctx.get("neighbors", []) or []
        used = ctx.get("used_neighbors")
        candidates = [n for n in neighbors if n not in used] if used is not None else list(neighbors)
        if candidates:
            choice = random.choice(candidates)
            if used is not None:
                used.add(choice)
            return choice
        if neighbors:
            return random.choice(neighbors)
        return random.choice(ctx.get("npc_pool") or ["路过的渔夫"])

    def _compose_narration(
        self,
        snapshot_json: str,
        pouch: Optional[PouchPayout],
        ctx: Dict[str, Any],
    ) -> Optional[str]:
        """根据当前 pending 的水族箱快照拼装一条叙事文本（填空式）。"""
        try:
            snapshot = json.loads(snapshot_json) if snapshot_json else []
        except (TypeError, ValueError):
            snapshot = []

        if not snapshot or pouch is None:
            return None

        fish = self._weighted_pick_fish(snapshot)
        if not fish:
            return None

        quip = ""
        if self.aquarium_quips_service is not None:
            try:
                quip = self.aquarium_quips_service.get_quip_for_fish(int(fish.get("fish_id"))) or ""
            except Exception as exc:
                logger.warning(f"获取鱼短评失败 fish_id={fish.get('fish_id')}: {exc}")
                quip = ""
        if not quip:
            quip = "在缸里慢慢氧化的样子"
        # 防御：再剥一次末尾标点，避免拼接时出现"…，"双逗号
        quip = quip.rstrip("。！？.!?…~～，,、 \t")
        quip = self._normalize_quip_clause(quip)

        neighbor = self._pick_neighbor_name(ctx)
        view_action = random.choice(ctx.get("view_action_pool") or ["凝视着"])
        thought = random.choice(ctx.get("thought_pool") or ["觉得"])
        fish_name = str(fish.get("name") or "这条鱼")

        return f"{neighbor}{view_action}{fish_name}，{thought}{quip}，留下了{pouch.item_name} ×{pouch.quantity}！"

    @staticmethod
    def _normalize_quip_clause(quip: str) -> str:
        """把旧式短评修正为能接在“觉得/感觉/寻思”后面的判断从句。"""
        quip = (quip or "").strip()
        if not quip:
            return "它在缸里慢慢氧化的样子"

        pronoun_prefixes = ("它", "这鱼", "这条鱼", "那鱼", "那条鱼")
        judgment_prefixes = ("像", "仿佛", "好像", "似乎")
        if quip.startswith(pronoun_prefixes) or quip.startswith(judgment_prefixes):
            return quip

        return f"它{quip}"

    @staticmethod
    def _weighted_pick_fish(snapshot: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not snapshot:
            return None
        weights = []
        for entry in snapshot:
            w = max(0, int(entry.get("contribution", 0) or 0))
            weights.append(w if w > 0 else 1)
        if sum(weights) <= 0:
            return random.choice(snapshot)
        return random.choices(snapshot, weights=weights, k=1)[0]

    # ---- 状态查询（用于 /水族箱 主命令展示） --------------------------

    def get_pending_summary(self, user_id: str) -> Dict[str, Any]:
        """返回待领取条目数与预估总收益（不结算）。"""
        pendings = self.evaluate_pending(user_id)
        total = sum(int(p.get("capped_amount", 0) or 0) for p in pendings)
        return {
            "pending_count": len(pendings),
            "estimated_amount": total,
        }
