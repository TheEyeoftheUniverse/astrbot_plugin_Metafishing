"""玄幻渡劫玩法 V2：修行服务。

职责：
- 玩家在区域 6 钓鱼时的修为产出
- 玩家修行档案的初始化、读取
- 钓鱼挂钩（fishing_service 调用）

修为加成：
- 仅当玩家当前钓鱼区域 == CULTIVATION_ZONE_ID 时产出
- 按钓到的鱼的星级（rarity）查表
- 受当前境界修为上限封顶
"""

from __future__ import annotations

from typing import Optional

from astrbot.api import logger

from ..domain.tribulation_models import CultivationProfile
from ..repositories.sqlite_tribulation_repo import SqliteTribulationRepository
from . import tribulation_constants as C
from ..utils import get_now


class CultivationService:
    """修行积累服务。"""

    def __init__(self, tribulation_repo: SqliteTribulationRepository):
        self.repo = tribulation_repo

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------
    def get_or_create_profile(self, user_id: str) -> CultivationProfile:
        profile = self.repo.get_profile(user_id)
        if profile is not None:
            return profile
        profile = CultivationProfile(user_id=user_id)
        self.repo.upsert_profile(profile, _now_iso())
        return profile

    def get_profile(self, user_id: str) -> Optional[CultivationProfile]:
        return self.repo.get_profile(user_id)

    # ------------------------------------------------------------------
    # 钓鱼产出
    # ------------------------------------------------------------------
    def award_xiuwei_for_fishing(
        self,
        user_id: str,
        fish_rarity: int,
        fish_count: int,
        zone_id: int,
    ) -> int:
        """钓鱼结算后调用。

        Returns:
            实际增加的修为（受境界上限封顶；非区域 6 / 上限已满 / 失败返 0）。
        """
        try:
            if zone_id != C.CULTIVATION_ZONE_ID:
                return 0
            if fish_count <= 0:
                return 0
            per_unit = C.get_xiuwei_for_fish_rarity(int(fish_rarity))
            if per_unit <= 0:
                return 0
            delta = per_unit * int(fish_count)

            profile = self.get_or_create_profile(user_id)
            cap = C.get_realm_cap(profile.current_realm)
            if cap <= 0:
                return 0
            if profile.accumulated_xiuwei >= cap:
                return 0

            actual = self.repo.add_xiuwei(user_id, delta, cap, _now_iso())
            return actual
        except Exception as exc:
            # 修行积累绝不能阻断钓鱼主流程
            logger.warning(f"[cultivation] award_xiuwei_for_fishing 失败: {exc}")
            return 0

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    def get_status_summary(self, user_id: str) -> dict:
        profile = self.get_or_create_profile(user_id)
        cap = C.get_realm_cap(profile.current_realm)
        return {
            "user_id": user_id,
            "current_realm": profile.current_realm,
            "current_realm_quality": profile.current_realm_quality,
            "accumulated_xiuwei": profile.accumulated_xiuwei,
            "xiuwei_cap": cap,
            "is_full": profile.accumulated_xiuwei >= cap if cap > 0 else False,
            "consecutive_failures": profile.consecutive_failures,
            "tiancheng_protection": dict(profile.tiancheng_protection or {}),
            "realm_history": dict(profile.realm_history or {}),
            "daily_observer_reward_count": profile.daily_observer_reward_count,
            "daily_guard_reward_count": profile.daily_guard_reward_count,
            "lowest_quality": profile.lowest_quality(),
            "sci_fi_intervention_level": profile.sci_fi_intervention_level,
        }


def _now_iso() -> str:
    return get_now().isoformat(timespec="seconds")
