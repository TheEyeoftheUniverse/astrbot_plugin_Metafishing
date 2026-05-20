"""Sci-Fi Intervention V2 shared constants."""

from __future__ import annotations


SCIFI_ZONE_ID = 5
REWRITE_CHIP_ITEM_ID = 58
MAX_BRANCH_LEVEL = 5
APPEND_RATE_CAP_BP = 9999

BRANCH_ABYSS = "abyss_compression"
BRANCH_FATE = "fate_severance"
BRANCH_RESONANCE = "resonance_dampening"
BRANCHES = (BRANCH_ABYSS, BRANCH_FATE, BRANCH_RESONANCE)

BRANCH_LEVEL_FIELD = {
    BRANCH_ABYSS: "abyss_compression_level",
    BRANCH_FATE: "fate_severance_level",
    BRANCH_RESONANCE: "resonance_dampening_level",
}

BRANCH_DISPLAY = {
    BRANCH_ABYSS: "深渊压缩协议",
    BRANCH_FATE: "天命截断协议",
    BRANCH_RESONANCE: "共振拑鋭协议",
}

BRANCH_TARGET_DISPLAY = {
    BRANCH_ABYSS: "克苏鲁",
    BRANCH_FATE: "玄幻",
    BRANCH_RESONANCE: "魔幻",
}

LEVEL_UP_COST = {1: 30, 2: 60, 3: 100, 4: 150, 5: 200}
LEVEL_APPEND_RATE_BP = {0: 0, 1: 2, 2: 5, 3: 8, 4: 12, 5: 17}
CTHULHU_GREAT_FAILURE_OFFSET = {0: 0, 1: 1, 2: 2, 3: 4, 4: 7, 5: 10}
TRIBULATION_SELF_RATE_MULTIPLIER = {0: 1.0, 1: 0.99, 2: 0.98, 3: 0.95, 4: 0.90, 5: 0.86}
TEAM_BATTLE_D20_PENALTY = {0: 0, 1: 0, 2: 0, 3: 1, 4: 3, 5: 5}

APEX_SINGULARITY = "singularity"
APEX_ABYSS_UNITY = "abyss_unity"
APEX_FATE_SOLITUDE = "fate_solitude"
APEX_RESONANCE_SUMMIT = "resonance_summit"
APEX_PROTOCOLS = (
    APEX_SINGULARITY,
    APEX_ABYSS_UNITY,
    APEX_FATE_SOLITUDE,
    APEX_RESONANCE_SUMMIT,
)

APEX_DISPLAY = {
    APEX_SINGULARITY: "奇点协议",
    APEX_ABYSS_UNITY: "深渊归一",
    APEX_FATE_SOLITUDE: "天命独行",
    APEX_RESONANCE_SUMMIT: "共振之巅",
}

APEX_APPEND_RATE_BP = {
    None: 0,
    APEX_SINGULARITY: 6,
    APEX_ABYSS_UNITY: 23,
    APEX_FATE_SOLITUDE: 23,
    APEX_RESONANCE_SUMMIT: 23,
}

APEX_UNLOCK_REQUIREMENTS = {
    APEX_SINGULARITY: {
        BRANCH_ABYSS: 3,
        BRANCH_FATE: 3,
        BRANCH_RESONANCE: 3,
    },
    APEX_ABYSS_UNITY: {BRANCH_ABYSS: 5},
    APEX_FATE_SOLITUDE: {BRANCH_FATE: 5},
    APEX_RESONANCE_SUMMIT: {BRANCH_RESONANCE: 5},
}

RESET_REASON_CODE = "no_apex_to_reset"

EVENT_RESEARCH_EARNED = "research_earned"
EVENT_LEVEL_UP = "level_up"
EVENT_APEX_SELECT = "apex_select"
EVENT_APEX_RESET = "apex_reset"
EVENT_APPEND_TRIGGERED = "append_triggered"
EVENT_APPEND_MISSED = "append_missed"
EVENT_CHIP_GRANTED = "chip_granted"

ERROR_INVALID_BRANCH = "invalid_branch"
ERROR_MAX_LEVEL = "max_level"
ERROR_INSUFFICIENT_POINTS = "insufficient_research_points"
ERROR_INVALID_APEX = "invalid_apex"
ERROR_APEX_ALREADY_SELECTED = "apex_already_selected"
ERROR_NO_CHIP = "no_chip"

APEX_UNLOCK_ERROR = {
    APEX_SINGULARITY: "singularity_requires_all_three_at_3",
    APEX_ABYSS_UNITY: "abyss_unity_requires_abyss_compression_5",
    APEX_FATE_SOLITUDE: "fate_solitude_requires_fate_severance_5",
    APEX_RESONANCE_SUMMIT: "resonance_summit_requires_resonance_dampening_5",
}


def get_branch_level(state: dict, branch: str) -> int:
    return int(state.get(BRANCH_LEVEL_FIELD[branch], 0) or 0)
