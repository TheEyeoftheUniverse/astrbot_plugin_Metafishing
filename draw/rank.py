import os
from datetime import datetime
from typing import List, Dict

from PIL import Image, ImageDraw, ImageFont
from astrbot.api import logger

from .gradient_utils import create_vertical_gradient
from .styles import load_font


IMG_WIDTH = 900
PADDING = 28
HEADER_HEIGHT = 78
ROW_HEIGHT = 96
ROW_GAP = 10

# ===== 配色（沿用纸面 paper / editorial 风格，与背包一致）=====
HAIRLINE = (228, 216, 198)
BG_TOP = (255, 248, 236)
BG_BOTTOM = (246, 237, 220)
TEXT_PRIMARY = (38, 33, 27)
TEXT_SECONDARY = (113, 104, 92)
TEXT_MUTED = (151, 139, 122)
AQUA = (18, 127, 130)
GOLD = (217, 151, 36)
CORAL = (255, 90, 61)
VIOLET = (121, 103, 217)
SILVER = (148, 154, 168)
BRONZE = (184, 120, 72)
CARD_BG = (255, 252, 245)
CARD_BORDER = (231, 220, 200)
TOP_CARD_TINT = (255, 246, 226)


def _text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _ellipsize(draw, text: str, font, max_width: int) -> str:
    text = text or ""
    if _text_size(draw, text, font)[0] <= max_width:
        return text
    trimmed = text
    while trimmed:
        candidate = trimmed + "..."
        if _text_size(draw, candidate, font)[0] <= max_width:
            return candidate
        trimmed = trimmed[:-1]
    return "..."


def _rank_color(index: int):
    if index == 0:
        return GOLD
    if index == 1:
        return SILVER
    if index == 2:
        return BRONZE
    return TEXT_SECONDARY


def format_large_number(number):
    value = int(number or 0)
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1000:.1f}K".replace(".0K", "K")
    if value < 1_000_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    return f"{value / 1_000_000_000:.1f}B".replace(".0B", "B")


def draw_fishing_ranking(user_data: List[Dict], output_path: str, ranking_type: str = "coins"):
    try:
        title_font = load_font(30)
        kicker_font = load_font(12)
        subtitle_font = load_font(15)
        rank_font = load_font(28)
        name_font = load_font(21)
        body_font = load_font(15)
        small_font = load_font(13)
        metric_font = load_font(26)
    except IOError:
        logger.warning("加载排行榜字体失败，回退默认字体。")
        title_font = ImageFont.load_default()
        kicker_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        rank_font = ImageFont.load_default()
        name_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
        metric_font = ImageFont.load_default()

    top_users = user_data[:10]
    rows = max(1, len(top_users))
    total_height = PADDING * 2 + HEADER_HEIGHT + rows * ROW_HEIGHT + (rows - 1) * ROW_GAP + 32

    image = create_vertical_gradient(IMG_WIDTH, total_height, BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(image)

    left = PADDING
    right = IMG_WIDTH - PADDING

    if ranking_type in {"premium", "premium_currency", "diamond", "diamonds", "gem", "gems"}:
        title_text = "钻石榜 TOP 10"
        subtitle_text = "按当前钻石持有量排序"
        metric_key = "premium_currency"
        metric_color = CORAL
        metric_label = "钻石"
    else:
        title_text = "金币榜 TOP 10"
        subtitle_text = "按当前金币持有量排序"
        metric_key = "coins"
        metric_color = GOLD
        metric_label = "金币"

    # ---- 顶部标题：editorial-kicker + 大标题（与状态页一致）----
    kicker_text = "FISHING  RANKING  ·  METAFISHING"
    draw.text((left, PADDING), kicker_text, font=kicker_font, fill=CORAL)
    draw.text((left, PADDING + 18), title_text, font=title_font, fill=TEXT_PRIMARY)
    sub_w, _ = _text_size(draw, subtitle_text, subtitle_font)
    draw.text((right - sub_w, PADDING + 26), subtitle_text, font=subtitle_font, fill=TEXT_SECONDARY)

    current_y = PADDING + HEADER_HEIGHT

    # ---- 排行榜白底卡片（每行一张）----
    for idx, user in enumerate(top_users):
        row_top = current_y + idx * (ROW_HEIGHT + ROW_GAP)
        row_bottom = row_top + ROW_HEIGHT

        # 前三名使用淡金色卡底，强化视觉
        bg_fill = TOP_CARD_TINT if idx < 3 else CARD_BG
        border_color = _rank_color(idx) if idx < 3 else CARD_BORDER
        border_w = 2 if idx < 3 else 1

        draw.rounded_rectangle(
            (left, row_top, right, row_bottom),
            radius=14,
            fill=bg_fill,
            outline=border_color,
            width=border_w,
        )

        # 内部布局
        inner_left = left + 22
        rank_color = _rank_color(idx)
        rank_text = f"#{idx + 1}"
        rank_w, _ = _text_size(draw, rank_text, rank_font)
        # 排名居中显示在左侧
        rank_y = row_top + (ROW_HEIGHT - 28) // 2 - 2
        draw.text((inner_left, rank_y), rank_text, font=rank_font, fill=rank_color)

        # 排名右侧装饰小圆点（前三名突出）
        if idx < 3:
            dot_x = inner_left + rank_w + 12
            dot_y = row_top + ROW_HEIGHT // 2 - 3
            draw.ellipse((dot_x, dot_y, dot_x + 6, dot_y + 6), fill=rank_color)
            name_col_x = dot_x + 22
        else:
            name_col_x = inner_left + max(rank_w, 56) + 18

        gear_col_x = left + 460
        metric_col_x = right - 180

        nickname = _ellipsize(
            draw,
            user.get("nickname") or f"渔夫{str(user.get('user_id', ''))[-4:]}",
            name_font,
            gear_col_x - name_col_x - 16,
        )
        title = _ellipsize(draw, user.get("title") or "无称号", small_font, gear_col_x - name_col_x - 16)
        rod = _ellipsize(draw, user.get("fishing_rod") or "无鱼竿", body_font, metric_col_x - gear_col_x - 16)
        accessory = _ellipsize(draw, user.get("accessory") or "无饰品", body_font, metric_col_x - gear_col_x - 16)
        metric_value = format_large_number(user.get(metric_key, 0))

        name_y = row_top + 18
        draw.text((name_col_x, name_y), nickname, font=name_font, fill=TEXT_PRIMARY)
        draw.text((name_col_x, name_y + 32), f"〔{title}〕", font=small_font, fill=VIOLET)

        # 装备列
        gear_y = row_top + 22
        draw.text((gear_col_x, gear_y), f"鱼竿  {rod}", font=body_font, fill=TEXT_SECONDARY)
        draw.text((gear_col_x, gear_y + 26), f"饰品  {accessory}", font=body_font, fill=TEXT_SECONDARY)

        # 指标列：标签 + 大数值
        metric_y = row_top + 18
        draw.text((metric_col_x, metric_y), metric_label, font=small_font, fill=TEXT_MUTED)
        draw.text((metric_col_x, metric_y + 18), metric_value, font=metric_font, fill=metric_color)

    # ---- 页脚 ----
    footer_text = f"Generated · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    fw, _ = _text_size(draw, footer_text, small_font)
    draw.text(((IMG_WIDTH - fw) // 2, total_height - 24), footer_text, font=small_font, fill=TEXT_MUTED)

    try:
        image.save(output_path)
        logger.info(f"排行榜图片已保存到 {output_path}")
    except Exception as e:
        logger.error(f"保存排行榜图片失败: {e}")
        raise
