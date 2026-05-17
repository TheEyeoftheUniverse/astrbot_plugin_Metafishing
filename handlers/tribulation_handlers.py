"""玄幻渡劫 V2：聊天指令 handlers。"""

from astrbot.api.event import AstrMessageEvent
from typing import TYPE_CHECKING, List, Dict

if TYPE_CHECKING:
    from ..main import FishingPlugin


from ..core.domain.tribulation_models import (
    REALM_DISPLAY,
    QUALITY_DISPLAY,
    MODE_IMMEDIATE,
    MODE_RESERVED,
)
from ..core.services import tribulation_constants as C


def _parse_items_args(raw_tokens: List[str], eligible: List[Dict]) -> List[Dict]:
    """解析 '名称:数量' 或 '#bait_id:数量' 形式的多个 token。

    支持空白分隔与中文分隔。
    """
    items: List[Dict] = []
    name_to_id = {item["name"]: item["bait_id"] for item in eligible}
    id_set = {item["bait_id"] for item in eligible}
    for raw in raw_tokens:
        if not raw:
            continue
        sep = None
        for s in (":", "：", "*", "×"):
            if s in raw:
                sep = s
                break
        if sep is None:
            continue
        name, qty_str = raw.split(sep, 1)
        name = name.strip()
        qty_str = qty_str.strip()
        try:
            count = int(qty_str)
        except ValueError:
            continue
        if count <= 0:
            continue
        bait_id = None
        if name.startswith("#"):
            try:
                bait_id = int(name[1:])
            except ValueError:
                bait_id = None
        if bait_id is None:
            bait_id = name_to_id.get(name)
        if bait_id is None or bait_id not in id_set:
            continue
        items.append({"bait_id": int(bait_id), "count": count})
    return items


async def show_cultivation(self: "FishingPlugin", event: AstrMessageEvent):
    """/修行：展示个人修行状态。"""
    user_id = self._get_effective_user_id(event)
    summary = self.cultivation_service.get_status_summary(user_id)
    realm = REALM_DISPLAY.get(summary["current_realm"], summary["current_realm"])
    quality = summary.get("current_realm_quality")
    quality_label = QUALITY_DISPLAY.get(quality or "", "") if quality else "—"
    cap = summary["xiuwei_cap"]
    cur = summary["accumulated_xiuwei"]
    msg = (
        "【🪷 修行状态】\n"
        f"当前境界：{quality_label}{realm}\n"
        f"修为：{cur}/{cap}" + ("（已圆满，可渡劫）" if summary["is_full"] else "") + "\n"
        f"劫痕未消：{summary['consecutive_failures']} 层（每层 +{C.FAILURE_BUFF_PER_STACK:.0f}%）\n"
        f"今日护法奖励次数：{summary['daily_guard_reward_count']}/{C.DAILY_GUARD_REWARD_CAP}\n"
        f"今日观道奖励次数：{summary['daily_observer_reward_count']}/{C.DAILY_OBSERVER_REWARD_CAP}\n"
    )
    if summary.get("tiancheng_protection"):
        protection_lines = []
        for r, n in summary["tiancheng_protection"].items():
            if n <= 0:
                continue
            protection_lines.append(f"  {REALM_DISPLAY.get(r, r)}：{n} 层（+{n * C.TIANCHENG_PROTECTION_PER_STACK:.0f}% 天成命中）")
        if protection_lines:
            msg += "天成执念：\n" + "\n".join(protection_lines) + "\n"
    if summary.get("realm_history"):
        history_lines = []
        for r, q in summary["realm_history"].items():
            history_lines.append(f"  {QUALITY_DISPLAY.get(q, q)}{REALM_DISPLAY.get(r, r)}")
        msg += "已成就境界：\n" + "\n".join(history_lines) + "\n"
        msg += f"对外显示：{QUALITY_DISPLAY.get(summary['lowest_quality'] or '', '')}{realm}\n"
    yield event.plain_result(msg)


async def list_eligible_items(self: "FishingPlugin", event: AstrMessageEvent):
    """/渡劫品：列出可投入的渡劫品（rarity >= 5 的鱼饵）。"""
    user_id = self._get_effective_user_id(event)
    items = self.tribulation_service.list_eligible_items(user_id)
    if not items:
        yield event.plain_result("背包中尚无可作为渡劫品的鱼饵（需 rarity ≥ 5）。")
        return
    lines = ["【⚗️ 可用渡劫品】"]
    for it in items:
        lines.append(
            f"  {it['name']}（#{it['bait_id']}） {it['rarity']}星  ×{it['quantity']}  基础权重 {it['base_weight']}"
        )
    lines.append("用法：/渡劫预览 名称:数量 名称:数量 …")
    yield event.plain_result("\n".join(lines))


async def preview_tribulation(self: "FishingPlugin", event: AstrMessageEvent):
    """/渡劫预览 名称:数量 …：实时预览本次渡劫的总权重、命中率、成功率。"""
    user_id = self._get_effective_user_id(event)
    tokens = event.message_str.strip().split()[1:]
    eligible = self.tribulation_service.list_eligible_items(user_id)
    items_invested = _parse_items_args(tokens, eligible)
    if not items_invested:
        yield event.plain_result("未识别到投入。用法：/渡劫预览 灵石:5 …")
        return
    result = self.tribulation_service.preview(user_id, items_invested)
    if not result.get("success"):
        yield event.plain_result(result.get("message", "无法预览。"))
        return
    msg = (
        f"【🌌 渡劫预览】目标境界：{result['target_realm_display']}\n"
        f"总品级权重：{result['total_weight']}（装备 +{result['equip_weight_bonus']}）\n"
        f"可冲击：{result['candidate_quality_display']}，命中率 {result['candidate_hit_rate']}%\n"
        f"天成执念：{result['tiancheng_protection']} 层\n"
        f"渡劫品成功率加成：+{result['items_bonus']}%\n"
        f"装备成功率加成：+{result['equip_success_bonus']}%\n"
        f"劫痕未消加成：+{result['buff_bonus']:.0f}%\n"
        f"——————————\n"
        f"预估最终成功率：{result['final_success_rate']}%（未含护法）\n"
        "请使用 /立即渡劫 或 /预约渡劫 同样的投入参数发起。"
    )
    yield event.plain_result(msg)


async def start_immediate(self: "FishingPlugin", event: AstrMessageEvent):
    """/立即渡劫 名称:数量 …"""
    async for r in _start_with_mode(self, event, MODE_IMMEDIATE):
        yield r


async def start_reserved(self: "FishingPlugin", event: AstrMessageEvent):
    """/预约渡劫 名称:数量 …"""
    async for r in _start_with_mode(self, event, MODE_RESERVED):
        yield r


async def _start_with_mode(self: "FishingPlugin", event: AstrMessageEvent, mode: str):
    user_id = self._get_effective_user_id(event)
    tokens = event.message_str.strip().split()[1:]
    eligible = self.tribulation_service.list_eligible_items(user_id)
    items_invested = _parse_items_args(tokens, eligible)
    if not items_invested:
        yield event.plain_result("未识别到投入。用法：/立即渡劫 灵石:5 …")
        return
    result = self.tribulation_service.start(user_id, mode, items_invested)
    if not result.get("success"):
        yield event.plain_result(result.get("message", "发起失败。"))
        return
    label = "立即渡劫" if mode == MODE_IMMEDIATE else "预约渡劫"
    msg = (
        f"⚡ 已发起{label} #{result['event_id']} → {REALM_DISPLAY.get(result['target_realm'], result['target_realm'])}\n"
        f"结算时间：{result['scheduled_at']}\n"
    )
    if result.get("announce_at"):
        msg += f"公示时间：{result['announce_at']}\n"
    msg += "等待护法 / 观道加入。"
    yield event.plain_result(msg)


async def join_tribulation(self: "FishingPlugin", event: AstrMessageEvent):
    """/参与渡劫 <event_id>"""
    user_id = self._get_effective_user_id(event)
    tokens = event.message_str.strip().split()
    if len(tokens) < 2:
        yield event.plain_result("用法：/参与渡劫 <event_id>")
        return
    try:
        event_id = int(tokens[1].lstrip("#"))
    except ValueError:
        yield event.plain_result("event_id 必须为整数。")
        return
    result = self.tribulation_service.join(user_id, event_id)
    if not result.get("success"):
        yield event.plain_result(result.get("message", "参与失败。"))
        return
    yield event.plain_result(result.get("message", "✅ 已加入。"))


async def list_active(self: "FishingPlugin", event: AstrMessageEvent):
    """/渡劫列表 ：当前公示中的渡劫事件。"""
    events = self.tribulation_service.list_active_events(limit=20)
    if not events:
        yield event.plain_result("当前无公示中的渡劫。")
        return
    lines = ["【🌌 公示中的渡劫】"]
    for e in events:
        lines.append(
            f"#{e['event_id']}  {e['host_nickname']} → {e['target_realm_display']} | "
            f"护法 {e['guard_count']} 观道 {e['observer_count']} | 结算 {e['scheduled_at']}"
        )
    lines.append("使用 /参与渡劫 <id> 加入。")
    yield event.plain_result("\n".join(lines))


async def show_event(self: "FishingPlugin", event: AstrMessageEvent):
    """/渡劫详情 <event_id>"""
    tokens = event.message_str.strip().split()
    if len(tokens) < 2:
        yield event.plain_result("用法：/渡劫详情 <event_id>")
        return
    try:
        event_id = int(tokens[1].lstrip("#"))
    except ValueError:
        yield event.plain_result("event_id 必须为整数。")
        return
    view = self.tribulation_service.get_event_view(event_id)
    if not view.get("success"):
        yield event.plain_result(view.get("message", "事件不存在。"))
        return
    msg = (
        f"【⚡ 渡劫 #{view['event_id']}】\n"
        f"渡劫者：{view['host_nickname']}\n"
        f"目标境界：{view['target_realm_display']}\n"
        f"模式：{'立即' if view['mode'] == MODE_IMMEDIATE else '预约'}\n"
        f"状态：{view['status']}\n"
        f"结算时间：{view['scheduled_at']}\n"
        f"护法：{view['guard_count']} 名 / 观道：{view['observer_count']} 名\n"
    )
    if view.get("resolved_at"):
        msg += (
            f"\n【结算结果】\n"
            f"结果：{'成功' if view['result'] == 'success' else '失败'}\n"
        )
        if view.get("quality_display"):
            msg += f"品级：{view['quality_display']}\n"
        msg += f"最终成功率：{view.get('final_success_rate', 0):.1f}%\n"
        msg += f"总权重：{view.get('final_total_weight', 0)}\n"
        msg += f"道望：{view.get('daowang_collected', 0)}\n"
    yield event.plain_result(msg)


async def reset_realm(self: "FishingPlugin", event: AstrMessageEvent):
    """/境界重修：从最低品级首次出现的境界开始重修。"""
    user_id = self._get_effective_user_id(event)
    result = self.tribulation_service.reset_realm(user_id)
    if not result.get("success"):
        yield event.plain_result(result.get("message", "重修失败。"))
        return
    msg = (
        "🔄 境界重修已完成\n"
        f"  重修起点：{REALM_DISPLAY.get(result['reset_from_realm'], result['reset_from_realm'])}\n"
        f"  当前境界：{REALM_DISPLAY.get(result['current_realm'], result['current_realm'])}\n"
        f"  继承修为：{result['accumulated_xiuwei']}\n"
        "请重新积累修为并发起渡劫。"
    )
    yield event.plain_result(msg)


async def tribulation_help(self: "FishingPlugin", event: AstrMessageEvent):
    """/渡劫帮助"""
    msg = (
        "【🌌 玄幻渡劫 V2】\n"
        "在 区域6 钓鱼累积修为；修满后投入渡劫品发起渡劫。\n"
        "\n"
        "/修行  查看修行状态\n"
        "/渡劫品  列出可用渡劫品\n"
        "/渡劫预览 名称:数量 …  预览成功率\n"
        "/立即渡劫 名称:数量 …  立即发起（下次刷新点结算）\n"
        "/预约渡劫 名称:数量 …  预约发起（次日刷新点结算）\n"
        "/渡劫列表  查看公示中的渡劫\n"
        "/渡劫详情 <id>  查看具体事件\n"
        "/参与渡劫 <id>  系统按境界关系自动安排护法/观道\n"
        "/境界重修  追求更高品级\n"
        "\n"
        "境界：炼气→筑基→金丹→元婴→化神\n"
        "品级：凡蜕＜灵蕴＜真一＜天成"
    )
    yield event.plain_result(msg)
