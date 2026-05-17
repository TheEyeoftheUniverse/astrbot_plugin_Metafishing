"""玄幻渡劫玩法 V2：领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


# ---------------------------------
# 枚举常量
# ---------------------------------

REALMS = ("lianqi", "zhuji", "jindan", "yuanying", "huashen")
REALM_DISPLAY = {
    "lianqi": "炼气",
    "zhuji": "筑基",
    "jindan": "金丹",
    "yuanying": "元婴",
    "huashen": "化神",
}

QUALITIES = ("fanxue", "lingyun", "zhenyi", "tiancheng")
QUALITY_DISPLAY = {
    "fanxue": "凡蜕",
    "lingyun": "灵蕴",
    "zhenyi": "真一",
    "tiancheng": "天成",
}

MODE_IMMEDIATE = "immediate"
MODE_RESERVED = "reserved"

STATUS_PENDING = "pending_announcement"
STATUS_ANNOUNCED = "announced"
STATUS_FINISHED = "finished"

RESULT_SUCCESS = "success"
RESULT_FAILURE = "failure"

PARTICIPANT_GUARD = "guard"
PARTICIPANT_OBSERVER = "observer"


def next_realm(realm: str) -> Optional[str]:
    """返回当前境界的下一级，化神之上无。"""
    if realm not in REALMS:
        return None
    idx = REALMS.index(realm)
    if idx + 1 >= len(REALMS):
        return None
    return REALMS[idx + 1]


def is_realm_higher_or_equal(a: str, b: str) -> bool:
    """a 的境界是否 ≥ b。"""
    if a not in REALMS or b not in REALMS:
        return False
    return REALMS.index(a) >= REALMS.index(b)


# ---------------------------------
# 实体
# ---------------------------------


@dataclass
class CultivationProfile:
    """玩家修行档案。"""

    user_id: str
    current_realm: str = "lianqi"
    current_realm_quality: Optional[str] = None
    accumulated_xiuwei: int = 0
    consecutive_failures: int = 0
    realm_history: Dict[str, str] = field(default_factory=dict)
    tiancheng_protection: Dict[str, int] = field(default_factory=dict)
    daily_observer_reward_count: int = 0
    daily_guard_reward_count: int = 0
    daily_count_reset_at: Optional[str] = None
    sci_fi_intervention_level: int = 0
    updated_at: Optional[str] = None

    def lowest_quality(self) -> Optional[str]:
        """返回玩家已达成境界中最低品级。用于对外展示。"""
        if not self.realm_history:
            return None
        order = {q: i for i, q in enumerate(QUALITIES)}
        return min(self.realm_history.values(), key=lambda q: order.get(q, 0))

    def get_tiancheng_count(self, realm: str) -> int:
        return int(self.tiancheng_protection.get(realm, 0))

    def set_tiancheng_count(self, realm: str, value: int) -> None:
        self.tiancheng_protection[realm] = max(0, int(value))


@dataclass
class TribulationEvent:
    """渡劫事件。"""

    event_id: int
    user_id: str
    target_realm: str
    mode: str
    status: str
    equipment_snapshot: Dict[str, Any]
    items_invested: List[Dict[str, Any]]
    accumulated_xiuwei: int
    created_at: str
    scheduled_at: str
    announce_at: Optional[str] = None
    resolved_at: Optional[str] = None
    result: Optional[str] = None
    quality: Optional[str] = None
    final_success_rate: Optional[float] = None
    final_total_weight: Optional[int] = None
    daowang_collected: int = 0


@dataclass
class TribulationParticipant:
    """护法 / 观道记录。"""

    participant_id: int
    event_id: int
    user_id: str
    type: str
    joined_at: str
    reward_paid: bool = False
    reward_amount: Optional[Dict[str, Any]] = None
    is_effective: bool = True
    xiuwei_granted: int = 0
