"""魔幻团战 V2：聊天指令 handlers。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..main import FishingPlugin

from ..core.services import team_battle_constants as C


async def team_battle(self: "FishingPlugin", event: AstrMessageEvent):
    """/团战：查看战报图 + 自动领取本次结算产出的所有未领奖励。"""
    user_id = self._get_effective_user_id(event)
    if not self.user_repo.check_exists(user_id):
        yield event.plain_result("❌ 你还没有注册，请先使用“注册”。")
        return

    # 1) 领取所有未领奖励（在渲染前完成；领取后 unclaimed=0）
    granted_now = self.team_battle_service.claim_all_unclaimed(user_id)
    # 2) 取玩家视图（不含 unclaimed，因为刚刚已 claim）
    view = self.team_battle_service.get_player_view(user_id)

    # 3) 渲染 PIL 战报图
    from ..draw.team_battle import draw_team_battle_image
    image = await draw_team_battle_image(
        view=view,
        granted_now=granted_now,
        viewer_user_id=user_id,
        data_dir=self.data_dir,
        item_template_repo=self.item_template_repo,
    )
    image_path = os.path.join(self.tmp_dir, f"team_battle_{_sanitize_filename(user_id)}.png")
    image.save(image_path)
    yield event.image_result(image_path)


async def team_battle_history(self: "FishingPlugin", event: AstrMessageEvent):
    """/团战历史：列出最近 10 星 Boss 击杀名册。"""
    user_id = self._get_effective_user_id(event)
    if not self.user_repo.check_exists(user_id):
        yield event.plain_result("❌ 你还没有注册，请先使用“注册”。")
        return

    history = self.team_battle_service.get_history_kills(limit=20)
    if not history:
        yield event.plain_result("📜 还没有任何 10 星 Boss 被击杀的记录。")
        return

    lines = ["📜【10 星击杀史册】（最近 20 条）", ""]
    for idx, h in enumerate(history, start=1):
        region_label = C.REGION_DISPLAY_NAME.get(h.get("region_key", ""), "未知")
        finisher = h.get("finisher_user_id") or "未知"
        try:
            finisher_user = self.user_repo.get_by_id(finisher) if finisher else None
            finisher_name = getattr(finisher_user, "nickname", None) or finisher
        except Exception:
            finisher_name = finisher
        killed_at = (h.get("killed_at") or "").replace("T", " ")
        lines.append(
            f"{idx}. [{region_label}] {h.get('boss_name', '?')}  ⭐10  最后一击：{finisher_name}  {killed_at}"
        )
    yield event.plain_result("\n".join(lines))


# ---------------------------------------------------------------------------
# 管理员调试命令
# ---------------------------------------------------------------------------

async def admin_team_battle_refresh(self: "FishingPlugin", event: AstrMessageEvent):
    """/管理团战刷新 [region] [star]：强制刷新一只新 Boss。"""
    args = (event.message_str or "").strip().split()
    region = None
    star = None
    if len(args) >= 2:
        candidate = args[1].strip()
        if candidate in C.REGIONS:
            region = candidate
        else:
            # 中文 → region_key
            for key, name in C.REGION_DISPLAY_NAME.items():
                if candidate == name:
                    region = key
                    break
    if len(args) >= 3:
        try:
            star = int(args[2])
            if star not in (7, 8, 9, 10):
                star = None
        except ValueError:
            star = None

    try:
        boss = self.team_battle_service.admin_force_refresh_boss(region_key=region, star=star)
    except Exception as exc:
        yield event.plain_result(f"❌ 刷新失败：{exc}")
        return

    region_label = C.REGION_DISPLAY_NAME.get(boss.get("region_key", ""), "未知")
    yield event.plain_result(
        f"✅ 新 Boss 已刷新：\n"
        f"  名字：{boss.get('boss_name')}\n"
        f"  风格：{region_label}  ⭐{boss.get('boss_star')}\n"
        f"  血量：{int(boss.get('max_hp', 0)):,}\n"
        f"  图片目录已清空。使用“管理团战图片提示词”生成手动生图提示。"
    )


async def admin_team_battle_image_prompt(self: "FishingPlugin", event: AstrMessageEvent):
    """/管理团战图片提示词：输出当前 Boss 的手动生图提示。"""
    info = self.team_battle_service.get_active_boss_manual_image_prompt_info()
    if info is None:
        yield event.plain_result("⚠️ 当前没有活跃 Boss，请先使用“管理团战刷新”。")
        return

    yield event.plain_result(
        "🖼️ 当前 Boss 手动生图提示\n"
        f"名字：{info['boss_name']}\n"
        f"原型：{info['fish_name']}\n"
        f"详细介绍：{info['fish_description'] or '无'}\n"
        f"风格：{info['region_label']}\n"
        f"风格提示词：{info['style_keywords']}\n"
        f"分辨率要求：{info['resolution']}\n"
        f"上传路径：{info['upload_path']}\n\n"
        "完整提示词：\n"
        f"{info['prompt']}"
    )


async def admin_team_battle_settle(self: "FishingPlugin", event: AstrMessageEvent):
    """/管理团战结算：立刻触发当日结算（绕过 daily_reset_hour）。"""
    try:
        result = self.team_battle_service.admin_force_settle()
    except Exception as exc:
        yield event.plain_result(f"❌ 结算失败：{exc}")
        return

    if not result.get("settled"):
        reason = result.get("reason", "未知")
        yield event.plain_result(f"⚠️ 本次结算未生效：{reason}")
        return

    stages = result.get("triggered_stages") or []
    stages_label = "、".join(stages) if stages else "无"
    yield event.plain_result(
        f"✅ 结算完成：\n"
        f"  参战玩家：{result.get('participants_count', 0)}\n"
        f"  有效伤害玩家：{result.get('effective_damage_count', 0)}\n"
        f"  当日总伤害：{int(result.get('total_today_damage', 0)):,}\n"
        f"  Boss HP：{int(result.get('before_hp', 0)):,} → {int(result.get('after_hp', 0)):,}\n"
        f"  触发阶段：{stages_label}"
    )


async def admin_team_battle_reset(self: "FishingPlugin", event: AstrMessageEvent):
    """/管理团战重置：清空当前 Boss + damage，过期所有未领奖励（保留历史）。"""
    try:
        result = self.team_battle_service.admin_reset()
    except Exception as exc:
        yield event.plain_result(f"❌ 重置失败：{exc}")
        return

    if not result.get("success"):
        yield event.plain_result(f"⚠️ 重置未生效：{result.get('reason', '未知')}")
        return

    yield event.plain_result(
        f"✅ 已重置：已停用 Boss id={result.get('deactivated_boss_id')}，"
        f"清空伤害排行，过期所有未领奖励。\n"
        f"  历史 10 星击杀记录保留。"
    )


def _sanitize_filename(value: str) -> str:
    import re
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", value or "")
    return safe.strip("_") or "user"
