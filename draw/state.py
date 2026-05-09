import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import requests
from io import BytesIO
import time
import json
from .utils import get_user_avatar
from ..core.utils import calculate_after_refine
from .styles import (
    COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR, COLOR_GOLD, COLOR_RARE,
    COLOR_REFINE_RED, COLOR_REFINE_ORANGE, COLOR_CORNER, load_font
)
from .text_utils import (
    load_font_with_cjk_fallback,
    draw_text_smart,
    wrap_text_by_width_optimized,
    create_text_cache,
)

BASE_FISHING_SUCCESS_RATE = 0.5


def _to_additive_bonus(modifier: float) -> float:
    if modifier is None:
        return 0.0
    return float(modifier) - 1.0


def _get_bait_cooldown_reduction(current_bait: Optional[Dict[str, Any]]) -> float:
    if not current_bait:
        return 0.0
    rarity = int(current_bait.get("rarity", 0) or 0)
    if rarity < 5:
        return 0.0
    return min((rarity - 4) * 0.1, 0.6)


def build_effective_fishing_stats(
    current_rod: Optional[Dict[str, Any]],
    current_accessory: Optional[Dict[str, Any]],
    current_bait: Optional[Dict[str, Any]],
    game_config: Dict[str, Any],
) -> Dict[str, Any]:
    success_rate = BASE_FISHING_SUCCESS_RATE
    quality_bonus = 0.0
    quantity_bonus = 0.0
    rare_bonus = 0.0
    value_bonus = 0.0

    if current_rod:
        success_rate += float(current_rod.get("success_rate_modifier", 0.0) or 0.0)
        quality_bonus += _to_additive_bonus(current_rod.get("bonus_fish_quality_modifier", 1.0))
        quantity_bonus += _to_additive_bonus(current_rod.get("bonus_fish_quantity_modifier", 1.0))
        rare_bonus += float(current_rod.get("bonus_rare_fish_chance", 0.0) or 0.0)

    if current_accessory:
        quality_bonus += _to_additive_bonus(current_accessory.get("bonus_fish_quality_modifier", 1.0))
        quantity_bonus += _to_additive_bonus(current_accessory.get("bonus_fish_quantity_modifier", 1.0))
        rare_bonus += float(current_accessory.get("bonus_rare_fish_chance", 0.0) or 0.0)
        value_bonus += _to_additive_bonus(current_accessory.get("bonus_coin_modifier", 1.0))

    if current_bait:
        success_rate += float(current_bait.get("success_rate_modifier", 0.0) or 0.0)
        quantity_bonus += _to_additive_bonus(current_bait.get("quantity_modifier", 1.0))
        rare_bonus += float(current_bait.get("rare_chance_modifier", 0.0) or 0.0)
        value_bonus += _to_additive_bonus(current_bait.get("value_modifier", 1.0))

    cooldown_reduction = _get_bait_cooldown_reduction(current_bait)
    base_cooldown_seconds = int(game_config.get("fishing", {}).get("cooldown_seconds", 180) or 180)
    effective_cooldown_seconds = max(1, int(round(base_cooldown_seconds * (1.0 - cooldown_reduction))))

    return {
        "success_rate": success_rate,
        "success_rate_bonus": success_rate - BASE_FISHING_SUCCESS_RATE,
        "quality_bonus": quality_bonus,
        "quantity_bonus": quantity_bonus,
        "rare_bonus": rare_bonus,
        "value_bonus": value_bonus,
        "cooldown_reduction": cooldown_reduction,
        "base_cooldown_seconds": base_cooldown_seconds,
        "effective_cooldown_seconds": effective_cooldown_seconds,
    }

def format_rarity_display(rarity: int) -> str:
    """格式化稀有度显示，支持显示到10星，10星以上显示为★★★★★★★★★★+"""
    if rarity <= 10:
        return '★' * rarity
    else:
        return '★★★★★★★★★★+'


def _draw_rounded_card(draw: ImageDraw.ImageDraw, bbox, radius: int, fill, outline=None, outline_width: int = 0):
    """绘制白底圆角卡片，与背包风格保持一致。"""
    x1, y1, x2, y2 = bbox
    draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill,
                           outline=outline, width=outline_width if outline else 0)


async def draw_state_image(user_data: Dict[str, Any], data_dir: str) -> Image.Image:
    """
    绘制玩家状态图像 - 卡片化排版，与背包视觉一致。

    每个分区都包裹在圆角白底卡片里，整体更紧凑且层次分明。
    """
    from .gradient_utils import create_vertical_gradient

    # ===== 布局常量 =====
    width = 760
    MARGIN_X = 24
    SECTION_GAP = 10
    CARD_RADIUS = 12
    CARD_PAD_X = 18
    CARD_PAD_Y = 14

    # ===== 颜色（沿用 paper / editorial 风格）=====
    bg_top = (255, 248, 236)        # --paper
    bg_bot = (246, 237, 220)        # --paper-soft
    paper_dark = (38, 33, 27)       # --ink
    paper_muted = (113, 104, 92)    # --muted
    paper_soft = (151, 139, 122)
    hairline = (228, 216, 198)      # 卡内细线
    track_bg = (231, 221, 205)
    aqua = (21, 166, 166)           # --aqua
    coral = (255, 90, 61)           # --cta
    gold = (217, 151, 36)           # --gold
    violet = (121, 103, 217)        # --violet
    leaf = (78, 155, 95)            # --leaf
    danger = (207, 77, 69)          # --danger
    card_bg = (255, 252, 245)       # 卡片白底（与背包一致的纸面卡底）
    card_border = (231, 220, 200)

    # ===== 字体 =====
    def load_local_font(name, size):
        path = os.path.join(os.path.dirname(__file__), 'resource', name)
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()

    font_path = os.path.join(os.path.dirname(__file__), 'resource', 'DouyinSansBold.otf')
    title_font = load_local_font('DouyinSansBold.otf', 26)
    kicker_font = load_local_font('DouyinSansBold.otf', 11)
    nickname_font = load_local_font('DouyinSansBold.otf', 22)
    section_font = load_local_font('DouyinSansBold.otf', 17)
    content_font = load_local_font('DouyinSansBold.otf', 17)
    body_font = load_font_with_cjk_fallback(font_path, 15)
    small_font = load_font_with_cjk_fallback(font_path, 13)
    tiny_font = load_local_font('DouyinSansBold.otf', 12)
    metric_value_font = load_local_font('DouyinSansBold.otf', 22)
    title_chip_font = load_local_font('DouyinSansBold.otf', 13)

    # ===== 测量 helper =====
    _measure_img = Image.new('RGB', (10, 10), bg_top)
    _measure_draw = ImageDraw.Draw(_measure_img)
    text_cache = create_text_cache()

    def measure(value, font):
        actual_font = font.primary_font if hasattr(font, 'primary_font') else font
        bbox = _measure_draw.textbbox((0, 0), value, font=actual_font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    def ellipsize(value: str, max_width: int, font) -> str:
        value = value or ''
        if measure(value, font)[0] <= max_width:
            return value
        trimmed = value
        while trimmed:
            candidate = trimmed + '...'
            if measure(candidate, font)[0] <= max_width:
                return candidate
            trimmed = trimmed[:-1]
        return '...'

    # ===== 解构 user_data =====
    coins = int(user_data.get('coins', 0) or 0)
    premium = int(user_data.get('premium_currency', 0) or 0)
    total_fishing = int(user_data.get('total_fishing_count', 0) or 0)
    nickname = str(user_data.get('nickname', '未知用户'))
    user_id_value = user_data.get('user_id')
    current_title = user_data.get('current_title')
    current_rod = user_data.get('current_rod') or {}
    current_accessory = user_data.get('current_accessory') or {}
    current_bait = user_data.get('current_bait') or {}
    fishing_zone = user_data.get('fishing_zone') or {}
    pond_info = user_data.get('pond_info') or {}
    total_count = int(pond_info.get('total_count', 0) or 0)
    total_value = int(pond_info.get('total_value', 0) or 0)
    effective = user_data.get('effective_fishing_stats', {}) or {}
    signed_today = bool(user_data.get('signed_in_today', False))
    auto_fishing = bool(user_data.get('auto_fishing_enabled', False))
    fishing_cd = int(user_data.get('fishing_cooldown_remaining', 0) or 0)
    steal_cd = int(user_data.get('steal_cooldown_remaining', 0) or 0)
    ef_cd = int(user_data.get('electric_fish_cooldown_remaining', 0) or 0)
    wipe_remaining = int(user_data.get('wipe_bomb_remaining', 0) or 0)

    # ===== 称号文本（无称号显示"无称号"）=====
    if current_title:
        title_name = current_title.get('name', '未知称号') if isinstance(current_title, dict) else str(current_title)
    else:
        title_name = '无称号'

    # ===== 区域描述自动换行（最多 2 行，更紧凑）=====
    zone_desc_raw = str(fishing_zone.get('description', '')).strip()
    desc_inner_w = width - MARGIN_X * 2 - CARD_PAD_X * 2
    if zone_desc_raw:
        all_lines = wrap_text_by_width_optimized(zone_desc_raw, body_font, desc_inner_w, text_cache)
        if len(all_lines) > 2:
            desc_lines = all_lines[:2]
            last = desc_lines[-1]
            while last and measure(last + '...', body_font)[0] > desc_inner_w:
                last = last[:-1]
            desc_lines[-1] = (last + '...') if last else '...'
        else:
            desc_lines = all_lines
    else:
        desc_lines = []

    # ===== 估算各卡片高度 =====
    HEADER_H = 56                             # 顶部标题区
    USER_CARD_H = CARD_PAD_Y * 2 + 60         # 头像 + 称号 + 昵称（紧凑）
    METRIC_CARD_H = CARD_PAD_Y * 2 + 64       # 4 列指标
    EQUIP_CARD_H = CARD_PAD_Y * 2 + 24 + 100  # title + 3 列装备
    DASH_CARD_H = CARD_PAD_Y * 2 + 24 + 56 * 2  # title + 2x3 网格
    ZONE_CARD_H = CARD_PAD_Y * 2 + 24 + 26 + max(len(desc_lines), 1) * 20 + 4
    POND_CARD_H = CARD_PAD_Y * 2 + 24 + 28
    CD_CARD_H = CARD_PAD_Y * 2 + 24 + 50
    FOOTER_H = 36

    height = (
        20
        + HEADER_H + SECTION_GAP
        + USER_CARD_H + SECTION_GAP
        + METRIC_CARD_H + SECTION_GAP
        + EQUIP_CARD_H + SECTION_GAP
        + DASH_CARD_H + SECTION_GAP
        + ZONE_CARD_H + SECTION_GAP
        + POND_CARD_H + SECTION_GAP
        + CD_CARD_H
        + FOOTER_H
    )

    image = create_vertical_gradient(width, height, bg_top, bg_bot)
    draw = ImageDraw.Draw(image)

    # ===== 绘制 helper =====
    def card(y, h):
        """画一张白底圆角卡片，返回内部内容起始 y。"""
        _draw_rounded_card(draw,
                           (MARGIN_X, y, width - MARGIN_X, y + h),
                           CARD_RADIUS,
                           fill=card_bg,
                           outline=card_border,
                           outline_width=1)
        return y + CARD_PAD_Y

    def card_section_title(x, y, text, accent=coral):
        """卡内 section 标题：左侧短色条 + 文字。"""
        bar_w = 3
        bar_h = 16
        draw.rounded_rectangle((x, y + 2, x + bar_w, y + 2 + bar_h), radius=2, fill=accent)
        draw.text((x + bar_w + 8, y), text, font=section_font, fill=paper_dark)
        _, th = measure(text, section_font)
        return y + max(th, bar_h) + 8

    def vline(x, y1, y2, color=hairline):
        draw.line((x, y1, x, y2), fill=color, width=1)

    cur_y = 20

    # ---- 1. 顶部标题区（无卡片，单纯文字）----
    kicker_text = 'PLAYER  STATUS  ·  METAFISHING'
    draw.text((MARGIN_X, cur_y), kicker_text, font=kicker_font, fill=coral)
    _, kicker_h = measure(kicker_text, kicker_font)
    draw.text((MARGIN_X, cur_y + kicker_h + 4), '状态手稿', font=title_font, fill=paper_dark)
    cur_y += HEADER_H + SECTION_GAP

    # ---- 2. 用户卡（白底）：头像 / 称号 / 昵称 ----
    user_inner_y = card(cur_y, USER_CARD_H)
    avatar_size = 56
    avatar_x = MARGIN_X + CARD_PAD_X
    avatar_y = user_inner_y - 4
    name_x = avatar_x
    if user_id_value:
        avatar = await get_user_avatar(user_id_value, data_dir, avatar_size)
        if avatar:
            image.paste(avatar, (avatar_x, avatar_y), avatar)
            name_x = avatar_x + avatar_size + 16

    # 称号（在昵称上方）
    title_chip_max = width - name_x - MARGIN_X - CARD_PAD_X
    title_display = ellipsize(f'〔{title_name}〕', title_chip_max, title_chip_font)
    draw_text_smart(draw, (name_x, avatar_y + 2), title_display, title_chip_font,
                    violet if current_title else paper_soft)

    # 昵称（在称号下方）
    nickname_clip = ellipsize(nickname, width - name_x - MARGIN_X - CARD_PAD_X, nickname_font)
    draw.text((name_x, avatar_y + 22), nickname_clip, font=nickname_font, fill=aqua)

    cur_y += USER_CARD_H + SECTION_GAP

    # ---- 3. 状态指标卡（白底，4 列）----
    metric_inner_y = card(cur_y, METRIC_CARD_H)
    inner_left = MARGIN_X + CARD_PAD_X
    inner_right = width - MARGIN_X - CARD_PAD_X
    cell_w = (inner_right - inner_left) // 4
    metrics = [
        ('COINS', '金币',     f'{coins:,}',                          gold),
        ('GEMS',  '钻石',     f'{premium:,}',                        coral),
        ('AUTO',  '自动钓鱼', '已开启' if auto_fishing else '已关闭', leaf if auto_fishing else danger),
        ('SIGN',  '今日签到', '已签到' if signed_today else '未签到', leaf if signed_today else danger),
    ]
    for i, (kicker, label, value, color) in enumerate(metrics):
        cx = inner_left + i * cell_w
        if i > 0:
            vline(cx - 4, metric_inner_y + 4, metric_inner_y + METRIC_CARD_H - CARD_PAD_Y * 2 - 4)
        draw.text((cx, metric_inner_y), kicker, font=kicker_font, fill=coral)
        draw_text_smart(draw, (cx, metric_inner_y + 14), label, small_font, paper_muted)
        value_clip = ellipsize(value, cell_w - 12, metric_value_font)
        draw.text((cx, metric_inner_y + 32), value_clip, font=metric_value_font, fill=color)
    cur_y += METRIC_CARD_H + SECTION_GAP

    # ---- 4. 装备信息卡 ----
    equip_inner_y = card(cur_y, EQUIP_CARD_H)
    after_title = card_section_title(MARGIN_X + CARD_PAD_X, equip_inner_y, '装备信息', accent=aqua)
    equip_y = after_title
    col_w = (inner_right - inner_left) // 3
    cols = [inner_left + i * col_w for i in range(3)]
    equip_block_h = EQUIP_CARD_H - (after_title - equip_inner_y) - CARD_PAD_Y
    for i in range(1, 3):
        vline(cols[i] - 4, equip_y, equip_y + equip_block_h - 6)

    def draw_equip_column(col_x, kind_label, item, on_extras=None):
        draw.text((col_x, equip_y), kind_label, font=small_font, fill=paper_soft)
        if not item:
            placeholder = '未使用' if kind_label == '鱼饵' else '未装备'
            draw.text((col_x, equip_y + 20), placeholder, font=content_font, fill=paper_soft)
            return
        name_clip = ellipsize(str(item.get('name', '?')), col_w - 14, content_font)
        draw.text((col_x, equip_y + 20), name_clip, font=content_font, fill=paper_dark)
        rarity = int(item.get('rarity', 1) or 1)
        meta_parts = [format_rarity_display(rarity)]
        refine = item.get('refine_level')
        if refine is not None and kind_label != '鱼饵':
            meta_parts.append(f'+{int(refine)}')
        meta_text = '  '.join(meta_parts)
        draw.text((col_x, equip_y + 44), meta_text, font=tiny_font, fill=violet)
        if on_extras:
            on_extras(col_x)

    def rod_extras(col_x):
        cur_d = current_rod.get('current_durability')
        max_d = current_rod.get('max_durability')
        if cur_d is None or not max_d:
            draw.text((col_x, equip_y + 64), '永久耐久', font=tiny_font, fill=aqua)
            return
        ratio = max(0.0, min(1.0, cur_d / max_d))
        bar_w = min(col_w - 18, 140)
        bar_y = equip_y + 66
        bar_color = COLOR_SUCCESS if ratio > 0.6 else COLOR_WARNING if ratio > 0.3 else COLOR_ERROR
        draw.rounded_rectangle((col_x, bar_y, col_x + bar_w, bar_y + 5), radius=2, fill=track_bg)
        draw.rounded_rectangle((col_x, bar_y, col_x + int(bar_w * ratio), bar_y + 5), radius=2, fill=bar_color)
        draw.text((col_x, bar_y + 9), f'耐久 {cur_d}/{max_d}', font=tiny_font, fill=bar_color)

    def bait_extras(col_x):
        qty = int(current_bait.get('quantity', 0) or 0)
        draw.text((col_x, equip_y + 64), f'剩余 {qty} 个', font=tiny_font, fill=gold)

    draw_equip_column(cols[0], '鱼竿', current_rod, rod_extras if current_rod else None)
    draw_equip_column(cols[1], '饰品', current_accessory)
    draw_equip_column(cols[2], '鱼饵', current_bait, bait_extras if current_bait else None)
    cur_y += EQUIP_CARD_H + SECTION_GAP

    # ---- 5. 钓鱼仪表盘卡（2x3 网格）----
    dash_inner_y = card(cur_y, DASH_CARD_H)
    after_title = card_section_title(MARGIN_X + CARD_PAD_X, dash_inner_y, '钓鱼仪表盘', accent=coral)
    dash_top = after_title
    dash_rows = [
        [('成功率', '绝不空军', f"{effective.get('success_rate', BASE_FISHING_SUCCESS_RATE) * 100:.1f}%", aqua),
         ('价值',  '值钱的鱼', f"+{effective.get('value_bonus', 0.0) * 100:.1f}%",                       gold),
         ('CD减少','更快钓鱼', f"-{effective.get('cooldown_reduction', 0.0) * 100:.1f}%",                coral)],
        [('数量',  '更多的鱼', f"+{effective.get('quantity_bonus', 0.0) * 100:.1f}%",                    aqua),
         ('品质',  '更好的鱼', f"+{effective.get('quality_bonus', 0.0) * 100:.1f}%",                     leaf),
         ('稀有度','少见的鱼', f"+{effective.get('rare_bonus', 0.0) * 100:.1f}%",                        violet)],
    ]
    dash_col_w = (inner_right - inner_left) // 3
    row_h = 56
    for ridx, row in enumerate(dash_rows):
        rt = dash_top + ridx * row_h
        if ridx > 0:
            draw.line((inner_left, rt - 2, inner_right, rt - 2), fill=hairline, width=1)
        for cidx, (label, note, value, color) in enumerate(row):
            cx = inner_left + cidx * dash_col_w
            if cidx > 0:
                vline(cx - 4, rt + 2, rt + row_h - 8)
            draw.text((cx, rt + 2), label, font=small_font, fill=paper_dark)
            draw.text((cx, rt + 18), note, font=tiny_font, fill=paper_soft)
            draw.text((cx, rt + 32), value, font=content_font, fill=color)
    cur_y += DASH_CARD_H + SECTION_GAP

    # ---- 6. 钓鱼区域卡 ----
    zone_inner_y = card(cur_y, ZONE_CARD_H)
    after_title = card_section_title(MARGIN_X + CARD_PAD_X, zone_inner_y, '钓鱼区域', accent=leaf)
    zone_y = after_title
    quota = int(fishing_zone.get('rare_fish_quota', 0) or 0)
    caught = int(fishing_zone.get('rare_fish_caught', 0) or 0)
    if quota <= 0:
        badge_text = '此区域无稀有鱼'
        badge_color = paper_soft
    else:
        remaining = max(0, quota - caught)
        badge_text = f'剩余稀有鱼 {remaining}/{quota}'
        badge_color = danger if remaining <= 0 else gold
    badge_w, _ = measure(badge_text, small_font)
    zone_name = ellipsize(str(fishing_zone.get('name', '未知区域')),
                          inner_right - inner_left - badge_w - 18, content_font)
    draw.text((inner_left, zone_y), zone_name, font=content_font, fill=paper_dark)
    draw_text_smart(draw, (inner_right - badge_w, zone_y + 2), badge_text, small_font, badge_color)
    zone_y += 24
    if desc_lines:
        for line in desc_lines:
            draw_text_smart(draw, (inner_left, zone_y), line, body_font, paper_muted)
            zone_y += 20
    else:
        draw_text_smart(draw, (inner_left, zone_y), '（无区域描述）', body_font, paper_soft)
    cur_y += ZONE_CARD_H + SECTION_GAP

    # ---- 7. 鱼塘概览卡 ----
    pond_inner_y = card(cur_y, POND_CARD_H)
    after_title = card_section_title(MARGIN_X + CARD_PAD_X, pond_inner_y, '鱼塘概览', accent=aqua)
    pond_y = after_title
    if total_count > 0 or total_value > 0:
        capacity_text = f'容量    {total_count:,} 条'
        value_text = f'总价值    {total_value:,} 金币'
        draw.text((inner_left, pond_y), capacity_text, font=content_font, fill=paper_dark)
        vw, _ = measure(value_text, content_font)
        draw.text((inner_right - vw, pond_y), value_text, font=content_font, fill=gold)
    else:
        draw_text_smart(draw, (inner_left, pond_y), '鱼塘里什么都没有...', content_font, paper_soft)
    cur_y += POND_CARD_H + SECTION_GAP

    # ---- 8. 冷却与道具卡（4 列）----
    cd_inner_y = card(cur_y, CD_CARD_H)
    after_title = card_section_title(MARGIN_X + CARD_PAD_X, cd_inner_y, '冷却与道具', accent=violet)
    cd_top = after_title

    def format_cd(seconds: int) -> str:
        if seconds <= 0:
            return '已就绪'
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            if minutes > 0:
                return f'{hours} 时 {minutes} 分'
            return f'{hours} 小时'
        if minutes > 0:
            seconds_part = seconds % 60
            if seconds_part > 0 and minutes < 5:
                return f'{minutes} 分 {seconds_part} 秒'
            return f'{minutes} 分钟'
        return f'{seconds} 秒'

    cd_cells = [
        ('钓鱼CD',   format_cd(fishing_cd),                                      leaf if fishing_cd <= 0 else paper_dark),
        ('偷鱼CD',   format_cd(steal_cd),                                        leaf if steal_cd <= 0 else paper_dark),
        ('电鱼CD',   format_cd(ef_cd),                                           leaf if ef_cd <= 0 else paper_dark),
        ('擦弹剩余', f'{wipe_remaining} 次' if wipe_remaining > 0 else '已用完', coral if wipe_remaining > 0 else paper_soft),
    ]
    cd_col_w = (inner_right - inner_left) // 4
    for i, (label, value, color) in enumerate(cd_cells):
        cx = inner_left + i * cd_col_w
        if i > 0:
            vline(cx - 4, cd_top + 2, cd_top + 44)
        draw_text_smart(draw, (cx, cd_top), label, small_font, paper_muted)
        value_clip = ellipsize(value, cd_col_w - 12, content_font)
        draw.text((cx, cd_top + 18), value_clip, font=content_font, fill=color)

    # ---- 9. 页脚 ----
    footer_text = f"Generated · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    fw, _ = measure(footer_text, tiny_font)
    draw.text(((width - fw) // 2, height - 22), footer_text, font=tiny_font, fill=paper_muted)

    return image


def get_user_state_data(user_repo, inventory_repo, item_template_repo, log_repo, buff_repo, game_config, user_id: str) -> Optional[Dict[str, Any]]:
    """
    获取用户状态数据

    Args:
        user_repo: 用户仓储
        inventory_repo: 库存仓储
        item_template_repo: 物品模板仓储
        log_repo: 日志仓储
        buff_repo: 用户增益仓储
        game_config: 游戏配置
        user_id: 用户ID

    Returns:
        包含用户状态信息的字典，如果用户不存在则返回None
    """
    from ..core.utils import get_current_daily_marker, get_now, get_today

    # 获取用户基本信息
    user = user_repo.get_by_id(user_id)
    if not user:
        return None

    # 获取当前装备的鱼竿
    current_rod = None
    rod_instance = inventory_repo.get_user_equipped_rod(user_id)
    if rod_instance:
        rod_template = item_template_repo.get_rod_by_id(rod_instance.rod_id)
        if rod_template:
            # 计算精炼后的最大耐久度，与背包一致：原始 * (1.5)^(精炼等级-1)
            if rod_template.durability is not None:
                refined_max_durability = int(rod_template.durability * (1.5 ** (max(rod_instance.refine_level, 1) - 1)))
            else:
                refined_max_durability = None

            # 如果实例是无限耐久，则上限也视为 None
            if rod_instance.current_durability is None:
                refined_max_durability = None

            current_rod = {
                'name': rod_template.name,
                'rarity': rod_template.rarity,
                'refine_level': rod_instance.refine_level,
                'current_durability': rod_instance.current_durability,
                'max_durability': refined_max_durability,
                'bonus_fish_quality_modifier': calculate_after_refine(
                    rod_template.bonus_fish_quality_modifier,
                    refine_level=rod_instance.refine_level,
                    rarity=rod_template.rarity,
                ),
                'bonus_fish_quantity_modifier': calculate_after_refine(
                    rod_template.bonus_fish_quantity_modifier,
                    refine_level=rod_instance.refine_level,
                    rarity=rod_template.rarity,
                ),
                'success_rate_modifier': calculate_after_refine(
                    getattr(rod_template, "success_rate_modifier", 0.0),
                    refine_level=rod_instance.refine_level,
                    rarity=rod_template.rarity,
                ),
                'bonus_rare_fish_chance': calculate_after_refine(
                    rod_template.bonus_rare_fish_chance,
                    refine_level=rod_instance.refine_level,
                    rarity=rod_template.rarity,
                ),
            }

    # 获取当前装备的饰品
    current_accessory = None
    accessory_instance = inventory_repo.get_user_equipped_accessory(user_id)
    if accessory_instance:
        accessory_template = item_template_repo.get_accessory_by_id(accessory_instance.accessory_id)
        if accessory_template:
            current_accessory = {
                'name': accessory_template.name,
                'rarity': accessory_template.rarity,
                'refine_level': accessory_instance.refine_level,
                'bonus_fish_quality_modifier': calculate_after_refine(
                    accessory_template.bonus_fish_quality_modifier,
                    refine_level=accessory_instance.refine_level,
                    rarity=accessory_template.rarity,
                ),
                'bonus_fish_quantity_modifier': calculate_after_refine(
                    accessory_template.bonus_fish_quantity_modifier,
                    refine_level=accessory_instance.refine_level,
                    rarity=accessory_template.rarity,
                ),
                'bonus_rare_fish_chance': calculate_after_refine(
                    accessory_template.bonus_rare_fish_chance,
                    refine_level=accessory_instance.refine_level,
                    rarity=accessory_template.rarity,
                ),
                'bonus_coin_modifier': calculate_after_refine(
                    accessory_template.bonus_coin_modifier,
                    refine_level=accessory_instance.refine_level,
                    rarity=accessory_template.rarity,
                ),
            }

    # 获取当前使用的鱼饵
    current_bait = None
    if user.current_bait_id:
        bait_template = item_template_repo.get_bait_by_id(user.current_bait_id)
        if bait_template:
            # 获取用户的鱼饵库存
            bait_inventory = inventory_repo.get_user_bait_inventory(user_id)
            bait_quantity = bait_inventory.get(user.current_bait_id, 0)
            current_bait = {
                'name': bait_template.name,
                'rarity': bait_template.rarity,
                'quantity': bait_quantity,
                'duration_minutes': getattr(bait_template, "duration_minutes", 0),
                'success_rate_modifier': getattr(bait_template, "success_rate_modifier", 0.0),
                'rare_chance_modifier': getattr(bait_template, "rare_chance_modifier", 0.0),
                'garbage_reduction_modifier': getattr(bait_template, "garbage_reduction_modifier", 0.0),
                'value_modifier': getattr(bait_template, "value_modifier", 1.0),
                'quantity_modifier': getattr(bait_template, "quantity_modifier", 1.0),
            }

    # 获取钓鱼区域信息
    fishing_zone = None
    if user.fishing_zone_id:
        zone = inventory_repo.get_zone_by_id(user.fishing_zone_id)
        if zone:
            fishing_zone = {
                'name': zone.name,
                'description': zone.description,
                'rare_fish_quota': zone.daily_rare_fish_quota if hasattr(zone, 'daily_rare_fish_quota') else 0,
                'rare_fish_caught': zone.rare_fish_caught_today if hasattr(zone, 'rare_fish_caught_today') else 0
            }

    # 计算偷鱼剩余CD时间
    steal_cooldown_remaining = 0
    if user.last_steal_time:
        cooldown_seconds = game_config.get("steal", {}).get("cooldown_seconds", 14400)
        now = get_now()
        # 处理时区问题
        if user.last_steal_time.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        elif user.last_steal_time.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=user.last_steal_time.tzinfo)

        elapsed = (now - user.last_steal_time).total_seconds()
        if elapsed < cooldown_seconds:
            steal_cooldown_remaining = int(cooldown_seconds - elapsed)

    # 计算电鱼CD时间
    electric_fish_cooldown_remaining = 0
    if hasattr(user, 'last_electric_fish_time') and user.last_electric_fish_time:
        cooldown_seconds = game_config.get("electric_fish", {}).get("cooldown_seconds", 7200)
        now = get_now()
        if user.last_electric_fish_time.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        elif user.last_electric_fish_time.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=user.last_electric_fish_time.tzinfo)

        elapsed = (now - user.last_electric_fish_time).total_seconds()
        if elapsed < cooldown_seconds:
            electric_fish_cooldown_remaining = int(cooldown_seconds - elapsed)

    # 获取当前称号
    current_title = None
    if hasattr(user, 'current_title_id') and user.current_title_id:
        try:
            # 尝试从各种可能的来源获取称号信息
            title_info = None
            if hasattr(item_template_repo, 'get_title_by_id'):
                title_info = item_template_repo.get_title_by_id(user.current_title_id)

            if title_info:
                current_title = {
                    'id': user.current_title_id,
                    'name': title_info.name if hasattr(title_info, 'name') else str(title_info)
                }
            else:
                # 如果无法获取详细信息，至少显示称号ID
                current_title = {
                    'id': user.current_title_id,
                    'name': f"称号#{user.current_title_id}"
                }
        except:
            # 如果获取称号失败，忽略
            current_title = None

    # 获取总钓鱼次数
    total_fishing_count = getattr(user, 'total_fishing_count', 0)

    effective_fishing_stats = build_effective_fishing_stats(
        current_rod,
        current_accessory,
        current_bait,
        game_config,
    )

    # 获取偷鱼总价值
    # steal_total_value = getattr(user, 'steal_total_value', 0)
    steal_total_value = '0' # 似乎没有偷鱼总价值字段？

    # 检查当前刷新周期是否签到
    signed_in_today = False
    try:
        reset_hour = int(game_config.get("daily_reset_hour", 0) or 0)
        signed_in_today = log_repo.has_checked_in(user_id, get_current_daily_marker(reset_hour))
    except Exception:
        signed_in_today = False

    # 计算擦弹剩余次数
    wipe_bomb_remaining = 0
    # 确保 user 对象有新添加的字段，做向后兼容
    if hasattr(user, 'last_wipe_bomb_date') and hasattr(user, 'wipe_bomb_attempts_today'):
        base_max_attempts = game_config.get("wipe_bomb", {}).get("max_attempts_per_day", 3)
        extra_attempts = 0
        boost_buff = buff_repo.get_active_by_user_and_type(user_id, "WIPE_BOMB_ATTEMPTS_BOOST")
        if boost_buff and boost_buff.payload:
            try:
                extra_attempts = json.loads(boost_buff.payload).get("amount", 0)
            except json.JSONDecodeError: pass

        total_max_attempts = base_max_attempts + extra_attempts

        today_str = get_today().strftime('%Y-%m-%d')
        used_attempts_today = 0
        # 如果记录的日期是今天，就使用记录的次数；否则次数为0
        if user.last_wipe_bomb_date == today_str:
            used_attempts_today = user.wipe_bomb_attempts_today

        wipe_bomb_remaining = max(0, total_max_attempts - used_attempts_today)
    else:
        # 如果数据库中的用户数据还没有新字段（例如，尚未迁移），提供一个默认值
        wipe_bomb_remaining = game_config.get("wipe_bomb", {}).get("max_attempts_per_day", 3)

    # 获取鱼塘信息
    pond_info = None
    try:
        # 使用与inventory_service.get_user_fish_pond相同的逻辑获取鱼塘信息
        inventory_items = inventory_repo.get_fish_inventory(user_id)
        total_value = inventory_repo.get_fish_inventory_value(user_id)

        # 计算总鱼数
        total_count = sum(item.quantity for item in inventory_items) if inventory_items else 0

        if total_count > 0 or total_value > 0:
            pond_info = {
                'total_count': total_count,
                'total_value': total_value
            }
        else:
            pond_info = {'total_count': 0, 'total_value': 0}

    except Exception as e:
        # 如果获取鱼塘信息失败，设置为默认值
        pond_info = {'total_count': 0, 'total_value': 0}

    fishing_cooldown_remaining = 0
    if user.last_fishing_time:
        cooldown_seconds = int(effective_fishing_stats.get("effective_cooldown_seconds", 180) or 180)
        now = get_now()
        if user.last_fishing_time.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        elif user.last_fishing_time.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=user.last_fishing_time.tzinfo)

        elapsed = (now - user.last_fishing_time).total_seconds()
        if elapsed < cooldown_seconds:
            fishing_cooldown_remaining = int(cooldown_seconds - elapsed)

    return {
        'user_id': user.user_id,
        'nickname': user.nickname or user.user_id,
        'coins': user.coins,
        'premium_currency': getattr(user, 'premium_currency', 0),
        'current_rod': current_rod,
        'current_accessory': current_accessory,
        'current_bait': current_bait,
        'auto_fishing_enabled': user.auto_fishing_enabled,
        'steal_cooldown_remaining': steal_cooldown_remaining,
        'electric_fish_cooldown_remaining': electric_fish_cooldown_remaining,
        'fishing_zone': fishing_zone,
        'current_title': current_title,
        'total_fishing_count': total_fishing_count,
        'steal_total_value': steal_total_value,
        'signed_in_today': signed_in_today,
        'wipe_bomb_remaining': wipe_bomb_remaining,
        'pond_info': pond_info,
        'effective_fishing_stats': effective_fishing_stats,
        'fishing_cooldown_remaining': fishing_cooldown_remaining,
    }
