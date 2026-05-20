from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from typing import TYPE_CHECKING

from ..core.services import scifi_constants as C

if TYPE_CHECKING:
    from ..main import FishingPlugin


BRANCH_ALIASES = {
    "深渊": C.BRANCH_ABYSS,
    "深渊压缩": C.BRANCH_ABYSS,
    "深渊压缩协议": C.BRANCH_ABYSS,
    "abyss": C.BRANCH_ABYSS,
    "天命": C.BRANCH_FATE,
    "天命截断": C.BRANCH_FATE,
    "天命截断协议": C.BRANCH_FATE,
    "fate": C.BRANCH_FATE,
    "共振": C.BRANCH_RESONANCE,
    "共振拑鋭": C.BRANCH_RESONANCE,
    "共振拑鋭协议": C.BRANCH_RESONANCE,
    "resonance": C.BRANCH_RESONANCE,
}

APEX_ALIASES = {
    "奇点": C.APEX_SINGULARITY,
    "奇点协议": C.APEX_SINGULARITY,
    "深渊归一": C.APEX_ABYSS_UNITY,
    "天命独行": C.APEX_FATE_SOLITUDE,
    "共振之巅": C.APEX_RESONANCE_SUMMIT,
}


async def show_state(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    view = self.scifi_service.get_state_view(user_id)
    state = view["state"]
    append_rate = view["append_rate"]
    lines = [
        "【科技状态】",
        f"科研点：{state['research_points']}",
        f"深渊压缩：Lv.{state['abyss_compression_level']}",
        f"天命截断：Lv.{state['fate_severance_level']}",
        f"共振拑鋭：Lv.{state['resonance_dampening_level']}",
        f"觉醒协议：{C.APEX_DISPLAY.get(state.get('apex_protocol'), '未选择')}",
        f"总追加率：{append_rate['total_append_rate_bp']} bp ({append_rate['total_append_rate_percent']:.2f}%)",
        f"协议重写芯：{view['chip_count']}",
    ]
    yield event.plain_result("\n".join(lines))


async def level_up(self: "FishingPlugin", event: AstrMessageEvent):
    parts = event.message_str.strip().split()
    if len(parts) < 2:
        yield event.plain_result("用法：/加点 <深渊|天命|共振> [次数]")
        return
    branch = BRANCH_ALIASES.get(parts[1], parts[1])
    count = 1
    if len(parts) >= 3:
        try:
            count = int(parts[2])
        except ValueError:
            yield event.plain_result("加点次数必须是整数。")
            return
    result = self.scifi_service.level_up_branch(self._get_effective_user_id(event), branch, count)
    if not result["success"]:
        yield event.plain_result(result.get("message", "加点失败。"))
        return
    yield event.plain_result(
        f"✅ {result['branch_name']} 提升到 Lv.{result['new_level']}，本次提升 {result['levels_gained']} 级，消耗 {result['spent_points']} 点科研点，剩余 {result['remaining_points']} 点。"
    )


async def select_apex(self: "FishingPlugin", event: AstrMessageEvent):
    parts = event.message_str.strip().split(maxsplit=1)
    if len(parts) < 2:
        yield event.plain_result("用法：/觉醒 <奇点|深渊归一|天命独行|共振之巅>")
        return
    apex = APEX_ALIASES.get(parts[1].strip(), parts[1].strip())
    result = self.scifi_service.select_apex(self._get_effective_user_id(event), apex)
    if not result["success"]:
        yield event.plain_result(result.get("message", "觉醒失败。"))
        return
    yield event.plain_result(f"✅ 已选择觉醒协议：{result['apex_name']}。")


async def reset_apex(self: "FishingPlugin", event: AstrMessageEvent):
    result = self.scifi_service.reset_apex(self._get_effective_user_id(event))
    if not result["success"]:
        yield event.plain_result(result.get("message", "重写失败。"))
        return
    yield event.plain_result(f"✅ 已重写协议，原协议：{C.APEX_DISPLAY.get(result['previous'], result['previous'])}。")


async def show_append_rate(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    view = self.scifi_service.get_append_rate_breakdown(user_id)
    state = self.scifi_service.get_state(user_id)
    lines = [
        "【追加率】",
        f"深渊压缩：{view['branch_rates_bp'][C.BRANCH_ABYSS]} bp",
        f"天命截断：{view['branch_rates_bp'][C.BRANCH_FATE]} bp",
        f"共振拑鋭：{view['branch_rates_bp'][C.BRANCH_RESONANCE]} bp",
        f"觉醒协议：{C.APEX_DISPLAY.get(state.get('apex_protocol'), '未选择')} (+{view['apex_rate_bp']} bp)",
        f"总计：{view['total_append_rate_bp']} bp ({view['total_append_rate_percent']:.2f}%)",
    ]
    yield event.plain_result("\n".join(lines))


async def show_leaderboard(self: "FishingPlugin", event: AstrMessageEvent):
    leaderboard = self.scifi_service.get_leaderboard(limit=10)["leaderboard"]
    if not leaderboard:
        yield event.plain_result("当前还没有科技数据。")
        return
    lines = ["【科技榜】"]
    for row in leaderboard:
        lines.append(
            f"{row['rank']}. {row['nickname']} 总等级 {row['total_level']} · 追加率 {row['append_rate_bp']} bp"
        )
    yield event.plain_result("\n".join(lines))
