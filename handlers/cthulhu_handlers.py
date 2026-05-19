from astrbot.api.event import AstrMessageEvent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..main import FishingPlugin


TIER_SHORT_LABEL = {"upper": "冠冕", "middle": "秘仪", "lower": "潮痕"}
GOD_LABEL = {"predict": "预知", "time": "时间", "pollute": "污染", "sacrifice": "献祭"}


def _format_authority(authority: dict) -> str:
    holder = authority.get("current_holder_nickname") or authority.get("current_holder") or "无"
    previous = authority.get("previous_holder_nickname") or authority.get("previous_holder") or "无"
    god = GOD_LABEL.get(authority["god_type"], authority["god_type"])
    tier = TIER_SHORT_LABEL.get(authority["tier"], authority["tier"])
    return f"{god}·{tier} 持有者：{holder} 上一任：{previous}"


async def show_state(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    view = self.cthulhu_service.get_state_view(user_id)
    state = view["state"]
    pending = view.get("pending_event")
    lines = [
        "【深潜状态】",
        f"SAN：{state['current_san']}/{state['max_san']}",
        f"今日深潜：{'已开启' if state['is_in_deepdive_today'] else '未开启'}",
        f"持有真名：{len(view.get('true_names', []))}",
        f"持有权柄：{len(view.get('owned_authorities', []))}",
    ]
    if pending:
        lines.append(f"待抉择事件：{pending['title']}")
        for choice in pending["choices"]:
            lines.append(f"  {choice['choice_id']}. {choice['label']}")
        if view.get("pending_choice"):
            lines.append(f"已选择：{view['pending_choice']}（等待 reset 结算）")
        else:
            lines.append("使用 /深潜选择 A 或 /深潜选择 B 做出抉择。")
    visible = view.get("visible_pollutions", {}).get("visible_pollutions", [])
    if visible:
        lines.append(f"可见污染：{', '.join(visible)}")
    yield event.plain_result("\n".join(lines))


async def choose_event(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    parts = event.message_str.strip().split()
    if len(parts) < 2:
        yield event.plain_result("用法：/深潜选择 A 或 /深潜选择 B")
        return
    result = self.cthulhu_service.stage_event_choice(user_id, parts[1])
    yield event.plain_result(result["message"])


async def list_true_names(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    names = self.cthulhu_service.list_true_names(user_id)["true_names"]
    if not names:
        yield event.plain_result("你当前没有持有真名。")
        return
        lines = ["【真名列表】"]
        for name in names:
            lines.append(
            f"#{name['name_id']} {name['name_string']} [{name['status']}] "
            f"{GOD_LABEL.get(name['god_type'], name['god_type'])}·{TIER_SHORT_LABEL.get(name['tier'], name['tier'])} "
            f"{name['progress']}/{name['threshold']}"
        )
    yield event.plain_result("\n".join(lines))


async def initiate_calling(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    parts = event.message_str.strip().split()
    if len(parts) < 2:
        yield event.plain_result("用法：/发起呼唤 <真名编号>")
        return
    try:
        name_id = int(parts[1].lstrip("#"))
    except ValueError:
        yield event.plain_result("真名编号必须是整数。")
        return
    result = self.cthulhu_service.initiate_calling(user_id, name_id)
    yield event.plain_result(result["message"])


async def vote_calling(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    raw = event.message_str.strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        yield event.plain_result("用法：/呼唤 <真名字符串>")
        return
    result = self.cthulhu_service.vote_on_call(user_id, parts[1].strip())
    yield event.plain_result(result["message"])


async def show_authorities(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    owned = self.cthulhu_service.get_state_view(user_id).get("owned_authorities", [])
    if not owned:
        yield event.plain_result("你当前未持有任何权柄。")
        return
    lines = ["【我的权柄】"]
    for authority in owned:
        lines.append(
            f"{GOD_LABEL.get(authority['god_type'], authority['god_type'])}"
            f"·{TIER_SHORT_LABEL.get(authority['tier'], authority['tier'])}"
        )
    lines.append("预知权柄先使用一次生成候选，再用相同指令加编号确认。")
    yield event.plain_result("\n".join(lines))


async def use_authority(self: "FishingPlugin", event: AstrMessageEvent):
    user_id = self._get_effective_user_id(event)
    parts = event.message_str.strip().split()
    if len(parts) < 2:
        yield event.plain_result("用法：/权柄使用 <authority_id> [参数]")
        return
    authority_id = parts[1]
    if authority_id.startswith("predict_"):
        if len(parts) >= 3 and parts[2].isdigit():
            result = self.cthulhu_service.confirm_predict(user_id, int(parts[2]) - 1)
            yield event.plain_result(result["message"])
            return
        result = self.cthulhu_service.prepare_predict(user_id, authority_id)
        if not result["success"]:
            yield event.plain_result(result["message"])
            return
        lines = ["【预知候选】"]
        for idx, candidate in enumerate(result["candidates"], start=1):
            lines.append(f"{idx}. {candidate['name']} {candidate['rarity']}星")
        lines.append("使用 /权柄使用 <authority_id> <编号> 确认。")
        yield event.plain_result("\n".join(lines))
        return
    if authority_id.startswith("time_"):
        result = self.cthulhu_service.use_time_authority(user_id, authority_id)
        yield event.plain_result(result["message"])
        return
    if authority_id.startswith("pollute_"):
        result = self.cthulhu_service.use_pollute_authority(user_id, authority_id)
        yield event.plain_result(result["message"])
        return
    if authority_id.startswith("sacrifice_"):
        if len(parts) < 4:
            yield event.plain_result("用法：/权柄使用 <authority_id> <rod|accessory|bait|item> <标识>")
            return
        result = self.cthulhu_service.use_sacrifice_authority(user_id, authority_id, parts[2], parts[3])
        yield event.plain_result(result["message"])
        return
    yield event.plain_result("未知权柄 ID。")


async def show_global_authorities(self: "FishingPlugin", event: AstrMessageEvent):
    authorities = self.cthulhu_service.list_authorities()["authorities"]
    lines = ["【全服权柄】"]
    for authority in authorities:
        lines.append(_format_authority(authority))
    yield event.plain_result("\n".join(lines))


async def show_active_calls(self: "FishingPlugin", event: AstrMessageEvent):
    calls = self.cthulhu_service.list_active_calls()["calls"]
    if not calls:
        yield event.plain_result("当前没有进行中的呼唤。")
        return
    lines = ["【呼唤进度】"]
    for call in calls:
        owner = call.get("owner_nickname") or call["owner_user_id"]
        lines.append(
            f"#{call['name_id']} {call['name_string']} {call['progress']}/{call['threshold']} "
            f"{call['god_type']}·{call['tier']} 发起人：{owner}"
        )
    yield event.plain_result("\n".join(lines))
