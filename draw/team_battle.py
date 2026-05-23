"""魔幻团战 V2：PIL 战报图绘制。

输出图片结构：
  Header   : 风格条带 + 标题
  Boss卡   : 占位封面（按 region 主题色）+ 名字 + 星级 + region 标签
  开篇     : 召集者文案
  血条     : 当前 HP / 4 阶段刻度
  排行     : 前 10 名（观察者高亮）
  本次领取 : 列出本次自动领取奖励
  历史未领 : 列出尚未领取的（应为空，因为本次已 claim）
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from ..core.services import team_battle_constants as C


# ---------------------------------------------------------------------------
# 主题
# ---------------------------------------------------------------------------

CANVAS_WIDTH = 840
PADDING = 24

PAPER_BG = (250, 246, 238)
PAPER_DARK = (38, 33, 27)
PAPER_MUTED = (113, 104, 92)
PAPER_SOFT = (151, 139, 122)
HAIRLINE = (228, 216, 198)
CARD_BG = (255, 252, 245)
CARD_BORDER = (231, 220, 200)
TRACK_BG = (231, 221, 205)
GOLD = (217, 151, 36)
LEAF = (78, 155, 95)
CORAL = (255, 90, 61)
AQUA = (21, 166, 166)
DANGER = (207, 77, 69)

REGION_THEME = {
    C.REGION_XIANHUAN: {"primary": (78, 110, 86), "accent": (216, 180, 90), "label": "玄幻"},
    C.REGION_MAGIC:    {"primary": (90, 32, 38), "accent": (180, 60, 60), "label": "魔幻"},
    C.REGION_CTHULHU:  {"primary": (54, 80, 96), "accent": (140, 120, 200), "label": "克苏鲁"},
    C.REGION_SCI_FI:   {"primary": (30, 60, 110), "accent": (60, 200, 240), "label": "科幻"},
}

REWARD_TYPE_LABEL = {
    "rod": "🎣 鱼竿",
    "accessory": "💍 饰品",
    "bait": "🪱 鱼饵",
    "chip": "✨ 风格屑",
    "crystal": "💎 风格晶",
    "refine": "🔧 精炼道具",
}


def _load_font(size: int) -> ImageFont.ImageFont:
    resource_dir = os.path.join(os.path.dirname(__file__), "resource")
    for fname in ("DouyinSansBold.otf", "NotoSansTC-Bold.ttf", "NotoSansJP-Bold.ttf"):
        path = os.path.join(resource_dir, fname)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_rounded_card(draw: ImageDraw.ImageDraw, bbox, radius: int, fill, outline=None, outline_width: int = 0):
    draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=outline_width)


def _rounded_alpha_mask(width: int, height: int, radius: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    return mask


def _fit_cover_image(image: Image.Image, width: int, height: int) -> Image.Image:
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        raise ValueError("invalid image size")
    scale = max(width / src_w, height / src_h)
    resized = image.resize((max(1, int(src_w * scale)), max(1, int(src_h * scale))), Image.Resampling.LANCZOS)
    left = max(0, (resized.size[0] - width) // 2)
    top = max(0, (resized.size[1] - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _load_boss_cover(image_path: str, width: int, height: int, radius: int) -> Optional[Image.Image]:
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        with Image.open(image_path) as raw:
            cover = _fit_cover_image(raw.convert("RGBA"), width, height)
        cover.putalpha(_rounded_alpha_mask(width, height, radius))
        return cover
    except Exception:
        return None


def _text_width(font: ImageFont.ImageFont, text: str) -> int:
    if hasattr(font, "getlength"):
        try:
            return int(font.getlength(text))
        except Exception:
            pass
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def _shorten(text: str, max_width: int, font: ImageFont.ImageFont) -> str:
    if _text_width(font, text) <= max_width:
        return text
    ellipsis = "…"
    while text and _text_width(font, text + ellipsis) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ""


def _fmt_damage(value: int) -> str:
    value = int(value)
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿"
    if value >= 10_000:
        return f"{value / 10_000:.2f} 万"
    return str(value)


async def draw_team_battle_image(
    view: Dict[str, Any],
    granted_now: List[Dict[str, Any]],
    viewer_user_id: str,
    data_dir: str,
    item_template_repo=None,
) -> Image.Image:
    """渲染战报图。"""
    has_boss = bool(view.get("has_active_boss"))
    boss = view.get("boss") or {}
    region_key = boss.get("region_key", C.REGION_MAGIC)
    theme = REGION_THEME.get(region_key, REGION_THEME[C.REGION_MAGIC])

    # 字体
    title_font = _load_font(28)
    big_font = _load_font(22)
    section_font = _load_font(18)
    body_font = _load_font(15)
    small_font = _load_font(13)

    # 测量高度
    sections: List[Dict[str, Any]] = []

    sections.append({"kind": "header"})
    if has_boss:
        sections.append({"kind": "boss_card"})
        sections.append({"kind": "opening"})
        sections.append({"kind": "hp_bar"})
        sections.append({"kind": "rank"})
    else:
        sections.append({"kind": "no_boss"})
    sections.append({"kind": "granted_now"})
    if view.get("unclaimed"):
        sections.append({"kind": "unclaimed"})
    sections.append({"kind": "history"})

    # 第一遍粗略计算总高度（用估算）
    rank_count = min(10, len(view.get("rank_top10") or []))
    granted_count = len(granted_now)
    unclaimed_count = len(view.get("unclaimed") or [])
    history_count = min(5, len(view.get("history_kills") or []))

    h = PADDING
    h += 64  # header
    if has_boss:
        h += 220  # boss card
        h += 90   # opening
        h += 90   # hp bar
        h += 50 + rank_count * 28  # rank
    else:
        h += 120  # no_boss
    h += 50 + max(granted_count, 1) * 22 + 20
    if unclaimed_count:
        h += 50 + unclaimed_count * 22 + 20
    h += 50 + history_count * 24 + 20
    h += PADDING
    height = max(h, 600)

    img = Image.new("RGB", (CANVAS_WIDTH, height), color=PAPER_BG)
    draw = ImageDraw.Draw(img)
    y = PADDING

    # Header
    draw.rectangle([0, 0, CANVAS_WIDTH, 6], fill=theme["primary"])
    draw.text((PADDING, y), "魔幻团战 · 战报", font=title_font, fill=PAPER_DARK)
    subtitle = f"{theme['label']} 风暴日" if has_boss else "今日无 Boss 在线"
    sub_w = _text_width(big_font, subtitle)
    draw.text((CANVAS_WIDTH - PADDING - sub_w, y + 4), subtitle, font=big_font, fill=theme["primary"])
    y += 64

    # Boss card
    if has_boss:
        y = _draw_boss_card(draw, img, y, boss, theme, title_font, big_font, body_font)
        y = _draw_opening(draw, y, view.get("opening_text") or "", body_font)
        y = _draw_hp_bar(draw, y, boss, big_font, body_font, small_font, theme)
        y = _draw_rank(draw, y, view, viewer_user_id, section_font, body_font, small_font, theme)
    else:
        y = _draw_no_boss(draw, y, body_font)

    # Granted now
    y = _draw_section_title(draw, y, "📦 本次自动领取", section_font, theme)
    y = _draw_reward_list(draw, y, granted_now, item_template_repo, body_font, small_font, empty_text="（本次无奖励产出）")

    # Lingering unclaimed (should normally be empty after claim)
    if view.get("unclaimed"):
        y = _draw_section_title(draw, y, "📌 历史未领（异常）", section_font, theme)
        y = _draw_reward_list(draw, y, view.get("unclaimed") or [], item_template_repo, body_font, small_font)

    # History kills
    y = _draw_section_title(draw, y, "📜 近期 10 星击杀", section_font, theme)
    y = _draw_history_list(draw, y, view.get("history_kills") or [], body_font, small_font)

    return img


# ---------------------------------------------------------------------------
# 分块绘制
# ---------------------------------------------------------------------------

def _draw_boss_card(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    y: int,
    boss: Dict[str, Any],
    theme: Dict[str, Any],
    title_font, big_font, body_font,
) -> int:
    x0, x1 = PADDING, CANVAS_WIDTH - PADDING
    card_h = 200
    _draw_rounded_card(draw, [x0, y, x1, y + card_h], 12, CARD_BG, CARD_BORDER, 1)

    # 占位封面（区域主题色 + 文字）
    cover_w, cover_h = 180, 180
    cover_x = x0 + 14
    cover_y = y + 10
    cover_radius = 10
    cover_image = _load_boss_cover(boss.get("image_path", ""), cover_w, cover_h, cover_radius)
    if cover_image is not None:
        img.paste(cover_image, (cover_x, cover_y), cover_image)
        _draw_rounded_card(
            draw, [cover_x, cover_y, cover_x + cover_w, cover_y + cover_h],
            cover_radius, None, theme["accent"], 2,
        )
    else:
        _draw_rounded_card(
            draw, [cover_x, cover_y, cover_x + cover_w, cover_y + cover_h],
            cover_radius, theme["primary"], theme["accent"], 2,
        )
        star_text = f"⭐{boss.get('boss_star', '?')}"
        sw = _text_width(title_font, star_text)
        draw.text(
            (cover_x + (cover_w - sw) // 2, cover_y + cover_h // 2 - 18),
            star_text, font=title_font, fill=(255, 255, 255),
        )
        label = theme.get("label", "")
        lw = _text_width(big_font, label)
        draw.text(
            (cover_x + (cover_w - lw) // 2, cover_y + cover_h // 2 + 16),
            label, font=big_font, fill=(255, 255, 255),
        )

    # 右侧：Boss 名字 + 信息
    text_x = cover_x + cover_w + 16
    draw.text((text_x, y + 16), boss.get("boss_name", "?"), font=title_font, fill=PAPER_DARK)
    info_lines = [
        f"风格区：{theme.get('label', '?')}",
        f"星级：{boss.get('boss_star', '?')}",
        f"血量：{_fmt_damage(int(boss.get('current_hp', 0)))} / {_fmt_damage(int(boss.get('max_hp', 0)))}",
        f"已达阶段：{'、'.join(boss.get('stages_triggered') or []) or '尚未跨过任何阶段'}",
    ]
    for i, line in enumerate(info_lines):
        draw.text((text_x, y + 58 + i * 24), line, font=body_font, fill=PAPER_MUTED)

    if boss.get("intro_quote"):
        quote = _shorten(boss["intro_quote"], x1 - text_x - 12, body_font)
        draw.text((text_x, y + card_h - 28), f"“{quote}”", font=body_font, fill=theme["primary"])

    return y + card_h + 12


def _draw_opening(draw: ImageDraw.ImageDraw, y: int, text: str, body_font) -> int:
    x0, x1 = PADDING, CANVAS_WIDTH - PADDING
    h = 70
    _draw_rounded_card(draw, [x0, y, x1, y + h], 10, (250, 244, 232), HAIRLINE, 1)
    if text:
        # 简单换行：按宽度切分
        wrapped = _wrap_text(text, body_font, x1 - x0 - 28)
        for i, line in enumerate(wrapped[:2]):
            draw.text((x0 + 14, y + 12 + i * 22), line, font=body_font, fill=PAPER_DARK)
    return y + h + 12


def _draw_hp_bar(
    draw: ImageDraw.ImageDraw, y: int, boss: Dict[str, Any],
    big_font, body_font, small_font, theme,
) -> int:
    x0, x1 = PADDING, CANVAS_WIDTH - PADDING
    h = 80
    _draw_rounded_card(draw, [x0, y, x1, y + h], 10, CARD_BG, CARD_BORDER, 1)

    current = max(0, int(boss.get("current_hp", 0)))
    max_hp = max(1, int(boss.get("max_hp", 1)))
    ratio = current / max_hp

    label = f"当前血量：{ratio * 100:.2f}%"
    draw.text((x0 + 14, y + 10), label, font=body_font, fill=PAPER_DARK)

    bar_x0 = x0 + 14
    bar_x1 = x1 - 14
    bar_y0 = y + 38
    bar_y1 = y + 58
    # track
    _draw_rounded_card(draw, [bar_x0, bar_y0, bar_x1, bar_y1], 8, TRACK_BG)
    # filled
    if ratio > 0:
        bar_filled_x = bar_x0 + int((bar_x1 - bar_x0) * ratio)
        _draw_rounded_card(draw, [bar_x0, bar_y0, bar_filled_x, bar_y1], 8, theme["primary"])

    # 4 阶段刻度（25/50/75）
    stages_triggered = set(boss.get("stages_triggered") or [])
    for r, stage in ((0.25, "25"), (0.50, "50"), (0.75, "75")):
        x = bar_x0 + int((bar_x1 - bar_x0) * r)
        color = LEAF if stage in stages_triggered else PAPER_SOFT
        draw.line([x, bar_y0 - 2, x, bar_y1 + 2], fill=color, width=2)
        tag = f"{stage}%"
        tw = _text_width(small_font, tag)
        draw.text((x - tw // 2, bar_y1 + 4), tag, font=small_font, fill=color)

    return y + h + 12


def _draw_rank(
    draw: ImageDraw.ImageDraw, y: int, view: Dict[str, Any], viewer_user_id: str,
    section_font, body_font, small_font, theme,
) -> int:
    x0, x1 = PADDING, CANVAS_WIDTH - PADDING
    rank = view.get("rank_top10") or []
    h = 40 + max(1, len(rank)) * 28 + 14
    _draw_rounded_card(draw, [x0, y, x1, y + h], 10, CARD_BG, CARD_BORDER, 1)

    draw.text((x0 + 14, y + 10), "🏆 当前 Boss 伤害排行（前 10）", font=section_font, fill=theme["primary"])
    if not rank:
        draw.text((x0 + 14, y + 44), "（暂无有效伤害记录）", font=body_font, fill=PAPER_MUTED)
        return y + h + 12

    my_rank = view.get("my_rank")
    my_damage = view.get("my_damage", 0)
    for i, row in enumerate(rank, start=1):
        line_y = y + 40 + (i - 1) * 28
        is_me = row.get("user_id") == viewer_user_id
        prefix_color = GOLD if i <= 3 else (CORAL if i <= 10 else PAPER_MUTED)
        text_color = theme["primary"] if is_me else PAPER_DARK
        draw.text((x0 + 14, line_y), f"#{i}", font=body_font, fill=prefix_color)
        nickname = row.get("user_id")  # 视图未带 nickname，直接展示 user_id；如需 nickname 可在 view 中预加载
        name_show = _shorten(str(nickname), 280, body_font)
        draw.text((x0 + 58, line_y), name_show, font=body_font, fill=text_color)
        leader_tag = "👑" if row.get("is_leader_at_last_settle") else "  "
        draw.text((x0 + 360, line_y), leader_tag, font=body_font, fill=GOLD)
        dmg = _fmt_damage(int(row.get("total_damage", 0)))
        dw = _text_width(body_font, dmg)
        draw.text((x1 - 14 - dw, line_y), dmg, font=body_font, fill=text_color)

    # 自己排名条
    if my_rank and my_rank > 10:
        draw.text(
            (x0 + 14, y + h - 22),
            f"我的排名：第 {my_rank} 名  伤害：{_fmt_damage(int(my_damage))}",
            font=small_font, fill=theme["primary"],
        )
    elif my_rank is None:
        draw.text(
            (x0 + 14, y + h - 22),
            "我尚未对当前 Boss 造成有效伤害",
            font=small_font, fill=PAPER_MUTED,
        )

    return y + h + 12


def _draw_no_boss(draw: ImageDraw.ImageDraw, y: int, body_font) -> int:
    x0, x1 = PADDING, CANVAS_WIDTH - PADDING
    h = 100
    _draw_rounded_card(draw, [x0, y, x1, y + h], 10, CARD_BG, CARD_BORDER, 1)
    draw.text((x0 + 14, y + 16), "🌫️ 当前没有活跃 Boss", font=body_font, fill=PAPER_DARK)
    draw.text(
        (x0 + 14, y + 44),
        "下次每日刷新点会自动诞生新 Boss。期间可继续钓鱼囤积 6+ 星水族箱。",
        font=body_font, fill=PAPER_MUTED,
    )
    return y + h + 12


def _draw_section_title(draw, y: int, text: str, section_font, theme) -> int:
    draw.text((PADDING, y), text, font=section_font, fill=theme["primary"])
    return y + 30


def _draw_reward_list(
    draw, y: int, rewards: List[Dict[str, Any]],
    item_template_repo, body_font, small_font, empty_text: str = "（暂无）",
) -> int:
    x0, x1 = PADDING, CANVAS_WIDTH - PADDING
    if not rewards:
        h = 36
        _draw_rounded_card(draw, [x0, y, x1, y + h], 8, CARD_BG, CARD_BORDER, 1)
        draw.text((x0 + 14, y + 10), empty_text, font=small_font, fill=PAPER_MUTED)
        return y + h + 12

    h = len(rewards) * 24 + 18
    _draw_rounded_card(draw, [x0, y, x1, y + h], 8, CARD_BG, CARD_BORDER, 1)
    for i, r in enumerate(rewards):
        rtype = r.get("reward_type", "")
        type_label = REWARD_TYPE_LABEL.get(rtype, f"📦 {rtype}")
        item_id = r.get("item_id")
        item_name = _lookup_item_name(item_template_repo, rtype, item_id) or f"#{item_id}"
        qty = r.get("quantity", 1)
        stage = r.get("source_stage", "?")
        label = r.get("source_label", "")
        line = f"{type_label}  {item_name}  ×{qty}    [{stage}]"
        if label:
            line += f"  {label}"
        line = _shorten(line, x1 - x0 - 28, body_font)
        draw.text((x0 + 14, y + 10 + i * 24), line, font=body_font, fill=PAPER_DARK)
    return y + h + 12


def _draw_history_list(
    draw, y: int, history: List[Dict[str, Any]],
    body_font, small_font,
) -> int:
    x0, x1 = PADDING, CANVAS_WIDTH - PADDING
    history = history[:5]
    if not history:
        h = 36
        _draw_rounded_card(draw, [x0, y, x1, y + h], 8, CARD_BG, CARD_BORDER, 1)
        draw.text((x0 + 14, y + 10), "（暂无 10 星击杀记录）", font=small_font, fill=PAPER_MUTED)
        return y + h + 12

    h = len(history) * 26 + 16
    _draw_rounded_card(draw, [x0, y, x1, y + h], 8, CARD_BG, CARD_BORDER, 1)
    for i, hk in enumerate(history):
        region = C.REGION_DISPLAY_NAME.get(hk.get("region_key", ""), "?")
        name = hk.get("boss_name", "?")
        killed = (hk.get("killed_at", "") or "").replace("T", " ")[:16]
        finisher = hk.get("finisher_user_id") or "—"
        line = f"[{region}]  {name}  ⭐10  最后一击：{finisher}  {killed}"
        line = _shorten(line, x1 - x0 - 28, body_font)
        draw.text((x0 + 14, y + 8 + i * 26), line, font=body_font, fill=PAPER_DARK)
    return y + h + 12


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _lookup_item_name(item_template_repo, rtype: str, item_id: Optional[int]) -> Optional[str]:
    if item_id is None or item_template_repo is None:
        return None
    try:
        if rtype == "rod":
            r = item_template_repo.get_rod_by_id(int(item_id))
            return getattr(r, "name", None)
        if rtype == "accessory":
            a = item_template_repo.get_accessory_by_id(int(item_id))
            return getattr(a, "name", None)
        if rtype == "bait":
            b = item_template_repo.get_bait_by_id(int(item_id))
            return getattr(b, "name", None)
        if rtype in ("chip", "crystal", "refine"):
            it = item_template_repo.get_item_by_id(int(item_id))
            return getattr(it, "name", None)
    except Exception:
        return None
    return None


def _wrap_text(text: str, font, max_width: int) -> List[str]:
    """简单中文 / 英文混排自动换行。"""
    lines: List[str] = []
    cur = ""
    for ch in text:
        candidate = cur + ch
        if _text_width(font, candidate) > max_width:
            if cur:
                lines.append(cur)
                cur = ch
            else:
                lines.append(ch)
                cur = ""
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines
