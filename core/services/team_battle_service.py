"""魔幻团战玩法 V2：核心业务服务。

参见 docs/requirements/2026-05-17-team-battle-v2.md 与 docs/plans/2026-05-17-team-battle-v2-execution-plan.md。
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger

from ..utils import get_now, get_current_daily_marker, get_last_reset_time
from . import team_battle_constants as C
from .boss_image_provider import BossImageProvider, NullBossImageProvider


_DATETIME_FMT = "%Y-%m-%dT%H:%M:%S"


def _now_iso() -> str:
    return get_now().strftime(_DATETIME_FMT)


def _marker_str(d: date) -> str:
    return d.isoformat()


class TeamBattleService:
    """魔幻团战 V2 服务。

    职责：
    - 当前 Boss 生命周期（刷新 / 进度 / 击杀 / 历史）
    - 每日结算：签到玩家 → 战斗 → 阶段触发 → 奖励发放
    - 奖励背包：发放 / 领取 / 自然过期
    - 后台日次循环 + 启动漏结算扫描
    - 管理员调试入口
    """

    # ---------------------------------------------------------------------
    # 初始化
    # ---------------------------------------------------------------------
    def __init__(
        self,
        team_battle_repo,
        user_repo,
        inventory_repo,
        item_template_repo,
        log_repo,
        game_config: Dict[str, Any],
        context=None,
        image_provider: Optional[BossImageProvider] = None,
    ):
        self.repo = team_battle_repo
        self.user_repo = user_repo
        self.inventory_repo = inventory_repo
        self.item_template_repo = item_template_repo
        self.log_repo = log_repo
        self.game_config = game_config
        self.context = context
        self.image_provider = image_provider or NullBossImageProvider()

        tb_cfg = self.game_config.get("team_battle", {}) if isinstance(self.game_config, dict) else {}
        self._settle_offset_minutes = int(tb_cfg.get("settle_offset_minutes", 2))
        self._image_retry = int(tb_cfg.get("image_retry", C.IMAGE_RETRY_DEFAULT))
        self._enable_llm_text = bool(tb_cfg.get("enable_llm_text", True))

        self._stop_event = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
        self._settle_lock = threading.Lock()

    # ---------------------------------------------------------------------
    # 配置 / 工具
    # ---------------------------------------------------------------------
    def _daily_reset_hour(self) -> int:
        return int(self.game_config.get("daily_reset_hour", 0) or 0)

    def _current_marker(self, now: Optional[datetime] = None) -> date:
        # get_current_daily_marker 只考虑当前时间，传入 now 时需手工计算
        if now is None:
            return get_current_daily_marker(self._daily_reset_hour())
        # 简化：直接用 (now - reset_hour) 的 date
        reset_hour = self._daily_reset_hour()
        adjusted = now - timedelta(hours=reset_hour)
        return adjusted.date()

    # ---------------------------------------------------------------------
    # 启动 / 关闭后台任务
    # ---------------------------------------------------------------------
    def start_daily_settle_task(self) -> None:
        if self._loop_thread and self._loop_thread.is_alive():
            logger.info("[team_battle] daily settle thread 已在运行")
            return
        self._stop_event.clear()
        self._loop_thread = threading.Thread(
            target=self._settle_loop, daemon=True, name="team_battle_settle_loop"
        )
        self._loop_thread.start()
        logger.info(
            f"[team_battle] daily settle thread 已启动，daily_reset_hour={self._daily_reset_hour()}, "
            f"offset={self._settle_offset_minutes}m"
        )

    def stop_daily_settle_task(self) -> None:
        self._stop_event.set()
        if self._loop_thread:
            self._loop_thread.join(timeout=2.0)
            self._loop_thread = None
            logger.info("[team_battle] daily settle thread 已停止")

    def _settle_loop(self) -> None:
        """每 10 分钟检查一次：当前是否已跨过 daily_reset_hour + offset 且当日未结算。"""
        last_processed_marker: Optional[date] = None
        while not self._stop_event.is_set():
            try:
                now = get_now()
                reset_hour = self._daily_reset_hour()
                reset_time = get_last_reset_time(reset_hour)
                target_time = reset_time + timedelta(minutes=self._settle_offset_minutes)
                today_marker = self._current_marker(now)

                if now >= target_time and last_processed_marker != today_marker:
                    if not self.repo.is_daily_settled(_marker_str(today_marker)):
                        logger.info(f"[team_battle] 触发当日结算 marker={today_marker}")
                        try:
                            self.settle_daily()
                        except Exception as exc:
                            logger.error(f"[team_battle] 当日结算异常: {exc}")
                    last_processed_marker = today_marker
            except Exception as exc:
                logger.error(f"[team_battle] settle loop 异常: {exc}")
            # 10 分钟轮询，足够及时
            self._stop_event.wait(timeout=600)

    def scan_overdue_on_startup(self) -> Dict[str, Any]:
        """启动时若当日未结算且早已过 daily_reset_hour+offset，补结算一次。"""
        try:
            now = get_now()
            today_marker = self._current_marker(now)
            reset_time = get_last_reset_time(self._daily_reset_hour())
            target_time = reset_time + timedelta(minutes=self._settle_offset_minutes)
            if now >= target_time and not self.repo.is_daily_settled(_marker_str(today_marker)):
                logger.info(f"[team_battle] 启动扫描发现漏结算 marker={today_marker}，立即补结算")
                return self.settle_daily()
        except Exception as exc:
            logger.warning(f"[team_battle] scan_overdue_on_startup 异常: {exc}")
        return {"settled": False, "reason": "no overdue"}

    # =====================================================================
    # Boss 刷新
    # =====================================================================
    def get_or_spawn_today_boss(
        self,
        force_region: Optional[str] = None,
        force_star: Optional[int] = None,
    ) -> Dict[str, Any]:
        """如果当前没有活跃 Boss，刷新一个新的。"""
        active = self.repo.get_active_boss()
        if active is not None:
            return active
        region = force_region or self._pick_region()
        star = force_star or self._pick_boss_star()
        return self._spawn_new_boss(region, star)

    def _spawn_new_boss(self, region_key: str, boss_star: int) -> Dict[str, Any]:
        fish = self._pick_boss_fish(region_key, boss_star)
        boss_name = self._build_boss_name(fish, region_key, boss_star)
        max_hp = C.BOSS_HP_BY_STAR[boss_star]

        # 图片：本期 NullProvider → None
        image_path: Optional[str] = None
        try:
            prompt = self._build_image_prompt(fish, region_key, boss_star)
            image_path = self.image_provider.generate(
                prompt=prompt,
                region_key=region_key,
                boss_name=boss_name,
                boss_star=boss_star,
                max_retries=self._image_retry,
            )
        except Exception as exc:
            logger.warning(f"[team_battle] 图片生成异常: {exc}")
            image_path = None

        # 登场 LLM 文本：失败兜底静态模板
        intro_story, intro_quote = self._request_intro_llm_text(fish, region_key, boss_name, boss_star)

        boss_id = self.repo.insert_boss(
            region_key=region_key,
            boss_name=boss_name,
            fish_id=int(fish.get("fish_id")),
            boss_star=boss_star,
            max_hp=max_hp,
            spawned_at=_now_iso(),
            image_path=image_path,
            intro_story=intro_story,
            intro_quote=intro_quote,
        )
        logger.info(
            f"[team_battle] Boss spawned id={boss_id} region={region_key} star={boss_star} name={boss_name}"
        )
        return self.repo.get_boss_by_id(boss_id)

    @staticmethod
    def _pick_region() -> str:
        return random.choice(C.REGIONS)

    @staticmethod
    def _pick_boss_star() -> int:
        roll = random.random()
        acc = 0.0
        for star, prob in C.BOSS_STAR_WEIGHTS:
            acc += prob
            if roll < acc:
                return star
        return C.BOSS_STAR_WEIGHTS[-1][0]

    def _pick_boss_fish(self, region_key: str, boss_star: int) -> Dict[str, Any]:
        """从该 region 对应 zone 的鱼池抽一条 boss_star 星鱼。"""
        zone_id = C.REGION_TO_ZONE[region_key]
        zone_fish_ids = set(self.inventory_repo.get_specific_fish_ids_for_zone(zone_id))

        # 优先：交集 zone 专属 + 同星级
        candidates: List[Any] = []
        try:
            same_star = self.item_template_repo.get_fishes_by_rarity(boss_star) or []
            for f in same_star:
                fid = getattr(f, "fish_id", None) or getattr(f, "id", None)
                if fid is not None and int(fid) in zone_fish_ids:
                    candidates.append(f)
        except Exception:
            candidates = []

        # 兜底 1：zone 内任意 7+ 星
        if not candidates:
            for fid in zone_fish_ids:
                try:
                    f = self.item_template_repo.get_fish_by_id(int(fid))
                except Exception:
                    f = None
                if f and int(getattr(f, "rarity", 0)) >= 7:
                    candidates.append(f)

        # 兜底 2：全库同星级
        if not candidates:
            try:
                candidates = list(self.item_template_repo.get_fishes_by_rarity(boss_star) or [])
            except Exception:
                candidates = []

        # 终极兜底：构造一个虚拟鱼
        if not candidates:
            return {"fish_id": 0, "name": "深渊未知体", "description": "数据库找不到匹配鱼", "rarity": boss_star}

        chosen = random.choice(candidates)
        return {
            "fish_id": int(getattr(chosen, "fish_id", 0) or getattr(chosen, "id", 0) or 0),
            "name": getattr(chosen, "name", "未知"),
            "description": getattr(chosen, "description", ""),
            "rarity": int(getattr(chosen, "rarity", boss_star) or boss_star),
            "base_value": int(getattr(chosen, "base_value", 0) or 0),
        }

    @staticmethod
    def _build_boss_name(fish: Dict[str, Any], region_key: str, boss_star: int) -> str:
        if boss_star >= 10 and C.MODIFIERS_TEN_STAR.get(region_key):
            modifier = random.choice(C.MODIFIERS_TEN_STAR[region_key])
        else:
            modifier = random.choice(C.MODIFIERS_COMMON[region_key])
        return f"{modifier}·{fish.get('name', '未知鱼')}"

    @staticmethod
    def _build_image_prompt(fish: Dict[str, Any], region_key: str, boss_star: int) -> str:
        style_block = C.IMAGE_PROMPT_REGION_STYLE.get(region_key, "")
        star_block = C.IMAGE_PROMPT_STAR_FEATURES.get(boss_star, "")
        return (
            f"{style_block}\n\n"
            f"character: {fish.get('name', '')} described as {fish.get('description', '')}\n"
            f"{star_block}\n"
            f"high quality, detailed, single subject"
        )

    # =====================================================================
    # 当日结算
    # =====================================================================
    def settle_daily(self, force: bool = False) -> Dict[str, Any]:
        with self._settle_lock:
            now = get_now()
            marker = self._current_marker(now)
            marker_s = _marker_str(marker)
            if not force and self.repo.is_daily_settled(marker_s):
                logger.info(f"[team_battle] marker={marker_s} 已结算，跳过")
                return {"settled": False, "reason": "already_settled", "marker": marker_s}

            # 1) 保证当前有 Boss
            boss = self.repo.get_active_boss()
            if boss is None:
                boss = self.get_or_spawn_today_boss()

            # 2) 检查区域 8 是否有人 → 决定是否结算
            leaders = self.repo.get_users_in_zone(C.LEADER_ZONE_ID)
            leaders_set = set(leaders)
            if not leaders_set:
                self.repo.mark_daily_settled(
                    marker_s, boss["id"], _now_iso(),
                    summary={"reason": "no_leader_in_zone_8", "leaders": []},
                )
                logger.info(f"[team_battle] marker={marker_s} 区域 8 无人，跳过结算")
                return {"settled": False, "reason": "no_leader", "marker": marker_s, "boss_id": boss["id"]}

            # 3) 收集签到玩家
            signed_in = self.repo.get_signed_in_user_ids(marker)
            participants = list(dict.fromkeys(signed_in))  # 去重保序
            if not participants:
                self.repo.mark_daily_settled(
                    marker_s, boss["id"], _now_iso(),
                    summary={"reason": "no_signed_in_players", "leaders": list(leaders_set)},
                )
                logger.info(f"[team_battle] marker={marker_s} 无签到玩家，跳过结算")
                return {"settled": False, "reason": "no_participants", "marker": marker_s, "boss_id": boss["id"]}

            # 4) 战斗
            before_hp = int(boss["current_hp"])
            settle_at = _now_iso()
            damage_log: List[Dict[str, Any]] = []
            highlights: List[str] = []
            total_today_damage = 0

            for user_id in participants:
                fish_team = self._pick_player_fish_for_battle(user_id)
                if not fish_team:
                    continue
                dmg, fish_highlights = self._compute_player_damage(
                    user_id, boss, fish_team, leaders_set,
                )
                if dmg <= 0:
                    continue
                self.repo.add_damage(
                    boss["id"], user_id, dmg, is_leader=(user_id in leaders_set), settled_at=settle_at,
                )
                total_today_damage += dmg
                damage_log.append({"user_id": user_id, "damage": dmg})
                highlights.extend(fish_highlights)

            # 5) 更新 Boss HP
            after_hp = max(0, before_hp - total_today_damage)
            self.repo.update_boss_hp(boss["id"], after_hp)
            boss["current_hp"] = after_hp

            # 6) 阶段触发（含跨阶段合并）
            triggered_stages = self._process_stage_triggers(boss, before_hp, after_hp, leaders_set)

            # 7) 写入幂等标记
            summary = {
                "marker": marker_s,
                "boss_id": boss["id"],
                "boss_name": boss["boss_name"],
                "leaders": list(leaders_set),
                "participants_count": len(participants),
                "effective_damage_count": len(damage_log),
                "total_today_damage": total_today_damage,
                "before_hp": before_hp,
                "after_hp": after_hp,
                "triggered_stages": triggered_stages,
                "highlights": highlights[:20],
            }
            self.repo.mark_daily_settled(marker_s, boss["id"], _now_iso(), summary=summary)

            logger.info(
                f"[team_battle] settled marker={marker_s} boss={boss['boss_name']} "
                f"participants={len(participants)} damage={total_today_damage} stages={triggered_stages}"
            )
            return {"settled": True, **summary}

    # ---------------------------------------------------------------------
    # 参战鱼选取
    # ---------------------------------------------------------------------
    def _pick_player_fish_for_battle(self, user_id: str) -> List[Dict[str, Any]]:
        """选玩家水族箱内最高价值的 8 条不同种类的 6+ 星鱼。"""
        try:
            aqua_items = self.inventory_repo.get_aquarium_inventory(user_id) or []
        except Exception:
            return []

        # 按 fish_id 聚合，同 fish_id 取 (1 + quality_level) 最大那条作为代表
        per_fish: Dict[int, Tuple[int, int]] = {}  # fish_id -> (effective_quality_level, quantity)
        for item in aqua_items:
            fid = int(getattr(item, "fish_id", 0) or 0)
            q = int(getattr(item, "quality_level", 0) or 0)
            qty = int(getattr(item, "quantity", 0) or 0)
            if fid <= 0 or qty <= 0:
                continue
            cur = per_fish.get(fid)
            if cur is None or q > cur[0]:
                per_fish[fid] = (q, qty)

        # 查 fish 模板，过滤 rarity >= 6，计算 effective_value
        candidates: List[Dict[str, Any]] = []
        for fid, (q, qty) in per_fish.items():
            try:
                f = self.item_template_repo.get_fish_by_id(int(fid))
            except Exception:
                f = None
            if not f:
                continue
            rarity = int(getattr(f, "rarity", 0) or 0)
            if rarity < C.PARTICIPATING_FISH_MIN_RARITY:
                continue
            base_value = int(getattr(f, "base_value", 0) or 0)
            effective_value = base_value * (1 + q)
            candidates.append({
                "fish_id": fid,
                "name": getattr(f, "name", "未知"),
                "rarity": rarity,
                "value": effective_value,
                "quality_level": q,
            })

        candidates.sort(key=lambda c: c["value"], reverse=True)
        return candidates[: C.PARTICIPATING_FISH_MAX_COUNT]

    # ---------------------------------------------------------------------
    # 单玩家战斗结算
    # ---------------------------------------------------------------------
    def _compute_player_damage(
        self,
        user_id: str,
        boss: Dict[str, Any],
        fish_team: List[Dict[str, Any]],
        leaders_set: set,
    ) -> Tuple[int, List[str]]:
        ac = C.boss_ac(int(boss["boss_star"]))
        total = 0
        highlights: List[str] = []
        nickname = self._lookup_nickname(user_id)
        for fish in fish_team:
            roll = random.randint(1, 20)
            hit = (roll + int(fish["rarity"])) >= ac
            if not hit:
                total += 1
                continue
            damage = int(fish["value"])
            is_crit = False
            if roll == 20:
                second_roll = random.randint(1, 20)
                if (second_roll + int(fish["rarity"])) >= ac:
                    damage *= 2
                    is_crit = True
            total += damage
            if is_crit:
                highlights.append(
                    f"{nickname} 的 {fish['name']} 暴击斩出 {self._fmt_damage(damage)} 伤"
                )
        return total, highlights

    @staticmethod
    def _fmt_damage(value: int) -> str:
        if value >= 100_000_000:
            return f"{value / 100_000_000:.2f} 亿"
        if value >= 10_000:
            return f"{value / 10_000:.2f} 万"
        return str(value)

    def _lookup_nickname(self, user_id: str) -> str:
        try:
            user = self.user_repo.get_by_id(user_id)
        except Exception:
            user = None
        if user is None:
            return user_id
        return getattr(user, "nickname", None) or user_id

    # ---------------------------------------------------------------------
    # 阶段触发与跨阶段合并
    # ---------------------------------------------------------------------
    def _process_stage_triggers(
        self,
        boss: Dict[str, Any],
        before_hp: int,
        after_hp: int,
        leaders_set: set,
    ) -> List[str]:
        max_hp = int(boss["max_hp"])
        already = set(boss.get("stages_triggered") or [])
        triggered_now: List[str] = []

        for stage in C.STAGES_ORDER:
            if stage in already:
                continue
            ratio = C.STAGE_HP_RATIO[stage]
            threshold_hp = int(max_hp * ratio)
            if before_hp > threshold_hp and after_hp <= threshold_hp:
                triggered_now.append(stage)

        if not triggered_now:
            return []

        rank_snapshot = self.repo.get_damage_rank(boss["id"])

        for stage in triggered_now:
            if stage == C.STAGE_KILL:
                self._trigger_kill_stage(boss, rank_snapshot, leaders_set, triggered_now)
            else:
                self._trigger_partial_stage(boss, stage, rank_snapshot, leaders_set, triggered_now)

        already.update(triggered_now)
        self.repo.update_boss_stages(boss["id"], list(already))

        return triggered_now

    # ---------------------------------------------------------------------
    # 75 / 50 / 25 阶段
    # ---------------------------------------------------------------------
    def _trigger_partial_stage(
        self,
        boss: Dict[str, Any],
        stage: str,
        rank_snapshot: List[Dict[str, Any]],
        leaders_set: set,
        all_stages_this_settle: List[str],
    ) -> None:
        # 计算"本次结算中已经在更早阶段拿过固定装备"的玩家集合
        already_won_users = self._collect_users_with_fixed_in_settle(boss["id"], stage, all_stages_this_settle)

        # 1) 固定装备 roll（仅前 10）
        top10 = [r for r in rank_snapshot[:10]]
        winner = self._roll_for_fixed_equipment(boss, stage, top10, already_won_users, leaders_set)
        if winner is not None:
            already_won_users.add(winner)

        # 2) 非装备阶段奖励（所有有效伤害玩家按身份倍数发）
        self._grant_stage_non_equipment(boss, stage, rank_snapshot)

    def _collect_users_with_fixed_in_settle(
        self,
        boss_id: int,
        current_stage: str,
        all_stages_this_settle: List[str],
    ) -> set:
        """同 Boss 单人最多 1 件固定装备：扫描 reward_inventory 已发放的 fixed 装备。"""
        already: set = set()
        try:
            # 用 db 查所有 reward_inventory 中 source_stage in (75/50/25) 且 reward_type in (rod/accessory) 且 source_label 包含 '固定'
            stages_done = [s for s in all_stages_this_settle if s != C.STAGE_KILL]
            # 简化：读取全部历史，service 不直接查 SQL，跨表用 repo 方法实现更干净。
            # 但这里需要的查询 repo 没现成接口；走个轻量自查：
            for row in self._all_fixed_rewards_for_boss(boss_id):
                already.add(row["user_id"])
        except Exception:
            pass
        return already

    def _all_fixed_rewards_for_boss(self, boss_id: int) -> List[Dict[str, Any]]:
        """读取该 Boss 已发放的固定装备 reward 行。"""
        # 直接借助 repo._cm 走 SQL
        import sqlite3 as _sql
        with self.repo._cm.get_connection() as conn:  # noqa: SLF001
            conn.row_factory = _sql.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id FROM team_battle_reward_inventory
                WHERE boss_id = ? AND source_stage IN ('75','50','25')
                  AND reward_type IN ('rod','accessory')
                  AND source_label LIKE '%固定%'
                """,
                (boss_id,),
            )
            return [{"user_id": r[0]} for r in cur.fetchall()]

    def _roll_for_fixed_equipment(
        self,
        boss: Dict[str, Any],
        stage: str,
        top10_rank: List[Dict[str, Any]],
        already_won_users: set,
        leaders_set: set,
    ) -> Optional[str]:
        eligible = [r for r in top10_rank if r["user_id"] not in already_won_users]
        if not eligible:
            return None

        rank_index_map: Dict[str, int] = {}
        damage_map: Dict[str, int] = {}
        for idx, r in enumerate(top10_rank, start=1):
            rank_index_map[r["user_id"]] = idx
            damage_map[r["user_id"]] = int(r["total_damage"])

        candidates: List[Tuple[str, int, int, int]] = []  # (user, roll, modifier, damage)
        for r in eligible:
            uid = r["user_id"]
            modifier = C.rank_modifier(rank_index_map.get(uid, 99))
            if uid in leaders_set:
                modifier += C.LEADER_MODIFIER_BONUS
            roll = random.randint(1, 20) + modifier
            candidates.append((uid, roll, modifier, damage_map.get(uid, 0)))

        candidates.sort(key=lambda x: (x[1], x[2], x[3], random.random()), reverse=True)
        winner_uid = candidates[0][0]

        # 抽装备：50% rod, 50% accessory；从对应风格池抽
        equip_type = random.choice([C.EQUIP_TYPE_ROD, C.EQUIP_TYPE_ACCESSORY])
        item_id = self._pick_equipment_from_pool(
            boss["region_key"], int(boss["boss_star"]), equip_type
        )
        if item_id is None:
            logger.warning(
                f"[team_battle] stage={stage} 装备池为空 region={boss['region_key']} "
                f"star={boss['boss_star']} type={equip_type}"
            )
            return None

        # 写入背包
        self.repo.insert_reward(
            user_id=winner_uid,
            boss_id=boss["id"],
            reward_type=equip_type,
            item_id=item_id,
            quantity=1,
            source_stage=stage,
            source_label=f"阶段{stage}固定装备",
            granted_at=_now_iso(),
        )
        logger.info(
            f"[team_battle] stage={stage} 固定装备已分配 winner={winner_uid} "
            f"type={equip_type} item={item_id}"
        )
        return winner_uid

    def _grant_stage_non_equipment(
        self,
        boss: Dict[str, Any],
        stage: str,
        rank_snapshot: List[Dict[str, Any]],
    ) -> None:
        region_key = boss["region_key"]
        boss_star = int(boss["boss_star"])
        rank_index_map = {r["user_id"]: idx for idx, r in enumerate(rank_snapshot, start=1)}

        # 风格鱼饵
        bait_id = C.BAIT_ITEM_ID.get((region_key, boss_star))
        # 屑或晶
        if boss_star <= 8:
            chip_item = C.CHIP_ITEM_ID.get(region_key)
            chip_base = C.STAGE_CHIP_BASE_COUNT_LOW_STAR
            chip_type = "chip"
        else:
            chip_item = C.CRYSTAL_ITEM_ID.get(region_key)
            chip_base = C.STAGE_CRYSTAL_BASE_COUNT_HIGH_STAR
            chip_type = "crystal"

        for r in rank_snapshot:
            uid = r["user_id"]
            idx = rank_index_map.get(uid, 99)
            multiplier = 3 if idx <= 10 else 1

            if bait_id is not None:
                self.repo.insert_reward(
                    user_id=uid, boss_id=boss["id"], reward_type="bait",
                    item_id=bait_id, quantity=C.STAGE_BAIT_BASE_COUNT * multiplier,
                    source_stage=stage, source_label=f"阶段{stage}风格鱼饵",
                    granted_at=_now_iso(),
                )
            if chip_item is not None:
                self.repo.insert_reward(
                    user_id=uid, boss_id=boss["id"], reward_type=chip_type,
                    item_id=chip_item, quantity=chip_base * multiplier,
                    source_stage=stage, source_label=f"阶段{stage}{'风格屑' if chip_type=='chip' else '风格晶'}",
                    granted_at=_now_iso(),
                )

    # ---------------------------------------------------------------------
    # 击杀阶段
    # ---------------------------------------------------------------------
    def _trigger_kill_stage(
        self,
        boss: Dict[str, Any],
        rank_snapshot: List[Dict[str, Any]],
        leaders_set: set,
        all_stages_this_settle: List[str],
    ) -> None:
        # 策划案 §12：奖励保留到"下次 Boss 击杀结算前"。
        # 因此在发放本次击杀奖励之前，先把过往所有未领奖励标记过期，
        # 然后再发本 Boss 的固定 / 阶段非装备 / 随机池奖励。
        self.repo.expire_all_unclaimed(_now_iso())

        # 1) 第 4 件固定装备
        already_won = self._collect_users_with_fixed_in_settle(boss["id"], C.STAGE_KILL, all_stages_this_settle)
        top10 = rank_snapshot[:10]
        self._roll_for_fixed_equipment_kill(boss, top10, already_won, leaders_set)

        # 2) 非装备阶段奖励
        self._grant_stage_non_equipment(boss, C.STAGE_KILL, rank_snapshot)

        # 3) 随机奖励池
        self._allocate_kill_random_pool(boss, rank_snapshot, leaders_set)

        # 4) 退场 LLM
        finisher_uid = rank_snapshot[0]["user_id"] if rank_snapshot else None
        finisher_name = self._lookup_nickname(finisher_uid) if finisher_uid else "未知"
        outro_text = self._request_outro_llm_text(boss, finisher_name) or ""

        # 5) 写入历史 / mark killed / 10 星永久保留
        killed_at = _now_iso()
        self.repo.mark_boss_killed(boss["id"], killed_at)
        if int(boss["boss_star"]) >= 10:
            self.repo.insert_history_kill(
                boss_id=boss["id"],
                region_key=boss["region_key"],
                boss_name=boss["boss_name"],
                boss_star=int(boss["boss_star"]),
                finisher_user_id=finisher_uid,
                final_rank_snapshot=[
                    {"user_id": r["user_id"], "damage": int(r["total_damage"])}
                    for r in rank_snapshot
                ],
                killed_at=killed_at,
            )
        else:
            # 7/8/9 星：排行立即清空，准备下一只 Boss
            self.repo.clear_damage(boss["id"])

        # 6) 触发"下一次 Boss 击杀结算前"过期：策划案 §12 由本方法开头处理。
        boss["killed_at"] = killed_at
        boss["outro_story"] = outro_text

    def _roll_for_fixed_equipment_kill(
        self,
        boss: Dict[str, Any],
        top10_rank: List[Dict[str, Any]],
        already_won_users: set,
        leaders_set: set,
    ) -> Optional[str]:
        # 与 partial 阶段同逻辑
        return self._roll_for_fixed_equipment(boss, C.STAGE_KILL, top10_rank, already_won_users, leaders_set)

    def _allocate_kill_random_pool(
        self,
        boss: Dict[str, Any],
        rank_snapshot: List[Dict[str, Any]],
        leaders_set: set,
    ) -> None:
        boss_star = int(boss["boss_star"])
        region_key = boss["region_key"]
        share_count = C.KILL_RANDOM_POOL_COUNT.get(boss_star, 5)
        if not rank_snapshot:
            return

        rank_index_map = {r["user_id"]: idx for idx, r in enumerate(rank_snapshot, start=1)}
        damage_map = {r["user_id"]: int(r["total_damage"]) for r in rank_snapshot}

        for share_idx in range(1, share_count + 1):
            # 决定本份类型
            roll = random.random()
            acc = 0.0
            chosen_type = "bait"
            for t, p in C.KILL_RANDOM_TYPE_WEIGHTS:
                acc += p
                if roll < acc:
                    chosen_type = t
                    break

            # roll 出获奖玩家
            candidates: List[Tuple[str, int, int, int]] = []
            for uid in rank_index_map:
                modifier = C.rank_modifier(rank_index_map[uid])
                if uid in leaders_set:
                    modifier += C.LEADER_MODIFIER_BONUS
                roll_total = random.randint(1, 20) + modifier
                candidates.append((uid, roll_total, modifier, damage_map[uid]))
            candidates.sort(key=lambda x: (x[1], x[2], x[3], random.random()), reverse=True)
            winner_uid = candidates[0][0]

            # 按类型发放
            self._grant_kill_random_share(boss, winner_uid, chosen_type, share_idx)

    def _grant_kill_random_share(
        self,
        boss: Dict[str, Any],
        user_id: str,
        share_type: str,
        share_idx: int,
    ) -> None:
        region_key = boss["region_key"]
        boss_star = int(boss["boss_star"])
        stage = C.STAGE_KILL
        label = f"击杀随机池第{share_idx}份"

        if share_type == "equipment":
            equip_type = random.choice([C.EQUIP_TYPE_ROD, C.EQUIP_TYPE_ACCESSORY])
            item_id = self._pick_equipment_from_pool(region_key, boss_star, equip_type)
            if item_id is None:
                return
            self.repo.insert_reward(
                user_id=user_id, boss_id=boss["id"], reward_type=equip_type,
                item_id=item_id, quantity=1, source_stage=stage,
                source_label=label + "·装备", granted_at=_now_iso(),
            )
        elif share_type == "refine":
            refine_id = C.REFINE_ITEM_FOR_BOSS_STAR.get(boss_star)
            if refine_id is None:
                return
            self.repo.insert_reward(
                user_id=user_id, boss_id=boss["id"], reward_type="refine",
                item_id=refine_id, quantity=C.KILL_RANDOM_REFINE_PER_SHARE,
                source_stage=stage, source_label=label + "·精炼", granted_at=_now_iso(),
            )
        elif share_type == "chip_or_crystal":
            if boss_star <= 8:
                cid = C.CHIP_ITEM_ID.get(region_key)
                cqty = C.KILL_RANDOM_CHIP_PER_SHARE
                rtype = "chip"
                sub = "屑"
            else:
                cid = C.CRYSTAL_ITEM_ID.get(region_key)
                cqty = C.KILL_RANDOM_CRYSTAL_PER_SHARE
                rtype = "crystal"
                sub = "晶"
            if cid is None:
                return
            self.repo.insert_reward(
                user_id=user_id, boss_id=boss["id"], reward_type=rtype,
                item_id=cid, quantity=cqty,
                source_stage=stage, source_label=label + f"·{sub}", granted_at=_now_iso(),
            )
        elif share_type == "bait":
            bait_id = C.BAIT_ITEM_ID.get((region_key, boss_star))
            if bait_id is None:
                return
            self.repo.insert_reward(
                user_id=user_id, boss_id=boss["id"], reward_type="bait",
                item_id=bait_id, quantity=C.KILL_RANDOM_BAIT_PER_SHARE,
                source_stage=stage, source_label=label + "·鱼饵", granted_at=_now_iso(),
            )

    def _pick_equipment_from_pool(
        self,
        region_key: str,
        boss_star: int,
        equip_type: str,
    ) -> Optional[int]:
        pool = C.EQUIPMENT_POOL.get((region_key, boss_star, equip_type)) or ()
        if not pool:
            return None
        return random.choice(pool)

    # =====================================================================
    # 玩家视图与领取
    # =====================================================================
    def get_player_view(self, user_id: str) -> Dict[str, Any]:
        boss = self.repo.get_active_boss()
        history = self.repo.list_history_kills(limit=5)
        if boss is None:
            return {
                "has_active_boss": False,
                "history_kills": history,
                "rank_top10": [],
                "my_damage": 0,
                "my_rank": None,
                "unclaimed": self.repo.get_unclaimed_rewards(user_id),
            }
        rank_all = self.repo.get_damage_rank(boss["id"])
        rank_index_map = {r["user_id"]: idx for idx, r in enumerate(rank_all, start=1)}
        my = self.repo.get_player_damage(boss["id"], user_id)
        unclaimed = self.repo.get_unclaimed_rewards(user_id)
        opening = self._render_opening(boss, rank_all)
        return {
            "has_active_boss": True,
            "boss": boss,
            "opening_text": opening,
            "rank_top10": rank_all[:10],
            "rank_all_count": len(rank_all),
            "my_damage": int(my["total_damage"]) if my else 0,
            "my_rank": rank_index_map.get(user_id),
            "unclaimed": unclaimed,
            "history_kills": history,
        }

    def claim_all_unclaimed(self, user_id: str) -> List[Dict[str, Any]]:
        rewards = self.repo.get_unclaimed_rewards(user_id)
        if not rewards:
            return []

        granted_details: List[Dict[str, Any]] = []
        ids_to_mark: List[int] = []
        for r in rewards:
            try:
                self._dispatch_reward_to_inventory(user_id, r)
                ids_to_mark.append(int(r["id"]))
                granted_details.append({
                    "reward_id": r["id"],
                    "reward_type": r["reward_type"],
                    "item_id": r["item_id"],
                    "quantity": r["quantity"],
                    "source_label": r.get("source_label") or "",
                })
            except Exception as exc:
                logger.error(f"[team_battle] claim 失败 reward_id={r['id']} user={user_id}: {exc}")
                continue

        if ids_to_mark:
            self.repo.mark_rewards_claimed(ids_to_mark, _now_iso())
        return granted_details

    def _dispatch_reward_to_inventory(self, user_id: str, reward: Dict[str, Any]) -> None:
        rtype = reward["reward_type"]
        item_id = reward.get("item_id")
        qty = int(reward.get("quantity", 1))
        if rtype == "rod" and item_id:
            self.inventory_repo.add_rod_instance(user_id, int(item_id), durability=None, refine_level=1)
        elif rtype == "accessory" and item_id:
            self.inventory_repo.add_accessory_instance(user_id, int(item_id), refine_level=1)
        elif rtype == "bait" and item_id:
            self.inventory_repo.update_bait_quantity(user_id, int(item_id), qty)
        elif rtype in ("chip", "crystal", "refine") and item_id:
            self.inventory_repo.add_item_to_user(user_id, int(item_id), qty)
        else:
            logger.warning(f"[team_battle] 未识别的 reward_type={rtype} item_id={item_id}")

    def get_history_kills(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self.repo.list_history_kills(limit)

    # =====================================================================
    # LLM 文本
    # =====================================================================
    def _get_llm_provider(self):
        if not self._enable_llm_text or self.context is None:
            return None
        getter = getattr(self.context, "get_using_provider", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _request_intro_llm_text(
        self,
        fish: Dict[str, Any],
        region_key: str,
        boss_name: str,
        boss_star: int,
    ) -> Tuple[Optional[str], Optional[str]]:
        provider = self._get_llm_provider()
        if provider is None:
            return None, None
        prompt = C.LLM_PROMPT_INTRO.format(
            region_name=C.REGION_DISPLAY_NAME[region_key],
            style_keywords=C.STYLE_KEYWORDS[region_key],
            boss_name=boss_name,
            boss_star=boss_star,
            fish_description=fish.get("description", ""),
        )
        try:
            completion = self._sync_call_provider(provider, prompt)
            return self._parse_intro_completion(completion)
        except Exception as exc:
            logger.warning(f"[team_battle] intro LLM 调用失败: {exc}")
            return None, None

    def _request_outro_llm_text(
        self,
        boss: Dict[str, Any],
        finisher_name: str,
    ) -> Optional[str]:
        provider = self._get_llm_provider()
        if provider is None:
            return None
        prompt = C.LLM_PROMPT_OUTRO.format(
            region_name=C.REGION_DISPLAY_NAME[boss["region_key"]],
            style_keywords=C.STYLE_KEYWORDS[boss["region_key"]],
            boss_name=boss["boss_name"],
            boss_star=boss["boss_star"],
            finisher_name=finisher_name,
        )
        try:
            return self._sync_call_provider(provider, prompt)
        except Exception as exc:
            logger.warning(f"[team_battle] outro LLM 调用失败: {exc}")
            return None

    def _sync_call_provider(self, provider, prompt: str) -> str:
        """跨 AstrBot 版本适配的同步调用（线程中安全使用 asyncio.run）。"""
        async def _call():
            text_chat = getattr(provider, "text_chat", None)
            if not callable(text_chat):
                raise RuntimeError("provider 未提供 text_chat 接口")
            attempts = [
                lambda: text_chat(prompt=prompt, session_id=None, contexts=[], image_urls=[], system_prompt=""),
                lambda: text_chat(prompt=prompt),
                lambda: text_chat(prompt),
            ]
            last_error: Optional[Exception] = None
            for invoker in attempts:
                try:
                    response = invoker()
                except TypeError as exc:
                    last_error = exc
                    continue
                except Exception as exc:
                    last_error = exc
                    break
                if asyncio.iscoroutine(response):
                    response = await response
                text = self._extract_completion_text(response)
                if text:
                    return text
            raise RuntimeError(f"provider.text_chat 全部尝试失败: {last_error}")

        try:
            return asyncio.run(_call())
        except RuntimeError:
            # 主线程已有 loop 时退而求其次：直接调，丢失协程支持
            return ""

    @staticmethod
    def _extract_completion_text(response: Any) -> str:
        if response is None:
            return ""
        for attr in ("completion_text", "text", "content"):
            value = getattr(response, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        if isinstance(response, str):
            return response
        return str(response or "")

    @staticmethod
    def _parse_intro_completion(text: str) -> Tuple[Optional[str], Optional[str]]:
        if not text:
            return None, None
        story = None
        quote = None
        cur_section = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("[登场]"):
                cur_section = "story"
                continue
            if line.startswith("[台词]"):
                cur_section = "quote"
                continue
            if cur_section == "story":
                story = (story + " " + line) if story else line
            elif cur_section == "quote":
                quote = (quote + " " + line) if quote else line
        return story, quote

    # ---------------------------------------------------------------------
    # 战报开篇文案
    # ---------------------------------------------------------------------
    def _render_opening(self, boss: Dict[str, Any], rank: List[Dict[str, Any]]) -> str:
        region_key = boss["region_key"]
        # 当前在 8 区的用户即为团长（不依赖结算时刻历史）
        leaders = self.repo.get_users_in_zone(C.LEADER_ZONE_ID)
        leader_names = [self._lookup_nickname(uid) for uid in leaders[:5]]
        if leader_names:
            template = C.REGION_OPENING_TEMPLATE.get(region_key, "")
            return template.format(leaders="、".join(leader_names), boss_name=boss["boss_name"])
        template = C.REGION_OPENING_NO_LEADER.get(region_key, "")
        return template.format(boss_name=boss["boss_name"])

    # =====================================================================
    # 管理员命令
    # =====================================================================
    def admin_force_refresh_boss(
        self,
        region_key: Optional[str] = None,
        star: Optional[int] = None,
    ) -> Dict[str, Any]:
        """强制刷新 Boss：先把当前 active 标记失效，奖励过期，再 spawn 新的。"""
        active = self.repo.get_active_boss()
        if active:
            self.repo.expire_all_unclaimed(_now_iso())
            self.repo.deactivate_boss(active["id"])
        return self._spawn_new_boss(
            region_key or self._pick_region(),
            star or self._pick_boss_star(),
        )

    def admin_force_settle(self) -> Dict[str, Any]:
        return self.settle_daily(force=True)

    def admin_reset(self) -> Dict[str, Any]:
        active = self.repo.get_active_boss()
        if active is None:
            return {"success": False, "reason": "no_active_boss"}
        self.repo.clear_damage(active["id"])
        self.repo.expire_all_unclaimed(_now_iso())
        self.repo.deactivate_boss(active["id"])
        return {"success": True, "deactivated_boss_id": active["id"]}
