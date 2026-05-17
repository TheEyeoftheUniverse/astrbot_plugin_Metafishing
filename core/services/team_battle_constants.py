"""
魔幻团战玩法 V2 常量定义。

仅持久化"配置 / 元数据"性质的常量；运营调优时直接改本文件。
参见 docs/requirements/2026-05-17-team-battle-v2.md。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 风格区 ↔ 钓鱼区映射
# ---------------------------------------------------------------------------

REGION_SCI_FI = "sci_fi"
REGION_XIANHUAN = "xianhuan"
REGION_CTHULHU = "cthulhu"
REGION_MAGIC = "magic"

REGIONS: tuple[str, ...] = (
    REGION_SCI_FI,
    REGION_XIANHUAN,
    REGION_CTHULHU,
    REGION_MAGIC,
)

REGION_DISPLAY_NAME: dict[str, str] = {
    REGION_SCI_FI: "科幻",
    REGION_XIANHUAN: "玄幻",
    REGION_CTHULHU: "克苏鲁",
    REGION_MAGIC: "魔幻",
}

# fishing_zones.id 5/6/7/8 ↔ region_key
ZONE_TO_REGION: dict[int, str] = {
    5: REGION_SCI_FI,
    6: REGION_XIANHUAN,
    7: REGION_CTHULHU,
    8: REGION_MAGIC,
}
REGION_TO_ZONE: dict[str, int] = {v: k for k, v in ZONE_TO_REGION.items()}

# 团长聚集区域 = fishing_zones.id = 8
LEADER_ZONE_ID = 8

# ---------------------------------------------------------------------------
# Boss 血量
# ---------------------------------------------------------------------------

V = 100_000_000  # 单条 10 星鱼基础价值

BOSS_HP_BY_STAR: dict[int, int] = {
    7: 32 * V,
    8: 48 * V,
    9: 72 * V,
    10: 108 * V,
}

# Boss 星级抽取概率
BOSS_STAR_WEIGHTS: list[tuple[int, float]] = [
    (7, 0.50),
    (8, 0.30),
    (9, 0.15),
    (10, 0.05),
]

# Boss AC = 10 + Boss 星级
def boss_ac(boss_star: int) -> int:
    return 10 + int(boss_star)


# ---------------------------------------------------------------------------
# 阶段
# ---------------------------------------------------------------------------

STAGE_75 = "75"
STAGE_50 = "50"
STAGE_25 = "25"
STAGE_KILL = "kill"
STAGES_ORDER: tuple[str, ...] = (STAGE_75, STAGE_50, STAGE_25, STAGE_KILL)

# 阶段 hp 阈值 = max_hp 的百分比；只有"低于该阈值"才视为跨过
STAGE_HP_RATIO: dict[str, float] = {
    STAGE_75: 0.75,
    STAGE_50: 0.50,
    STAGE_25: 0.25,
    STAGE_KILL: 0.0,
}


# ---------------------------------------------------------------------------
# 修饰词库（策划案 17.1）
# ---------------------------------------------------------------------------

MODIFIERS_COMMON: dict[str, tuple[str, ...]] = {
    REGION_XIANHUAN: (
        "渊海之主", "紫府仙君", "九霄龙王", "太虚道者", "玄元上人",
        "清虚祖师", "太阴仙姬", "北冥之灵", "蓬莱古帝", "紫霄真君",
        "长生鲛王", "苍渊圣主", "玉清古佛", "九幽冥王", "黄泉摆渡者",
        "太华仙长", "凌霄殿主", "沧澜剑灵", "太古鲲族", "紫府金鳞",
    ),
    REGION_MAGIC: (
        "深渊吞噬者", "黑鳞领主", "暗影长老", "血肉教皇", "巨颚之王",
        "死寂使徒", "颅骨歌者", "渊血暴君", "灰烬之父", "黑铁之爪",
        "腐血亡灵", "静默之喙", "焦土暴君", "龙裔遗孤", "颅骨城主",
        "漆黑领主", "残月之眼", "灾劫先锋", "暮霭执法者", "黑潮邪魔",
    ),
    REGION_CTHULHU: (
        "渊底低语者", "万眼凝视者", "触手编织者", "疯魔之主", "深海歌姬",
        "未明之相", "沉默吟唱者", "影息潜行者", "虚空回响", "黏液之父",
        "异色梦魇", "异界召唤者", "内壁低吟", "触尾游荡者", "不眠观察者",
        "海床之囚", "心智蚕食者", "域外之尾", "寂静纺织者", "深域呢喃",
    ),
    REGION_SCI_FI: (
        "超频原型", "量子之握", "数据嗜食者", "自迭代单元", "漂浮模因",
        "数字寄生体", "神经索·零号", "第三代生命体", "半机械支配者", "基因合成体",
        "失控 AI", "数据漩涡", "离线终端", "矩阵游走者", "全息执行单元",
        "异种合成体", "编码深渊", "等离子之触", "自由意志·废弃", "镜面协议者",
    ),
}

MODIFIERS_TEN_STAR: dict[str, tuple[str, ...]] = {
    REGION_XIANHUAN: ("万古帝尊", "玄黄道祖", "鸿蒙游者", "太初渊君", "太上忘情"),
    REGION_MAGIC: ("殒落之神", "远古沉睡者", "终焉之牙", "永夜咆哮者", "噬魂尊主"),
    REGION_CTHULHU: ("旧日支配者", "无名古神", "不可名状者", "远古觉者", "星之子嗣"),
    REGION_SCI_FI: ("协议-终末", "α-原种", "源代码·零", "反熵之心", "终态原型"),
}


# ---------------------------------------------------------------------------
# 屑 / 晶 物品 ID（参见 initial_seed.sql items 47-54）
# ---------------------------------------------------------------------------

CHIP_ITEM_ID: dict[str, int] = {
    REGION_SCI_FI: 47,    # 星屑 (6 星)
    REGION_XIANHUAN: 49,  # 道屑 (6 星)
    REGION_CTHULHU: 51,   # 梦屑 (6 星)
    REGION_MAGIC: 53,     # 龙屑 (6 星)
}

CRYSTAL_ITEM_ID: dict[str, int] = {
    REGION_SCI_FI: 48,    # 星核 (8 星)
    REGION_XIANHUAN: 50,  # 道晶 (8 星)
    REGION_CTHULHU: 52,   # 梦核 (8 星)
    REGION_MAGIC: 54,     # 龙晶 (8 星)
}


# ---------------------------------------------------------------------------
# 鱼饵 ID 矩阵（4 风格 × 7-10 星）
# 参见 initial_seed.sql baits 7-22
# ---------------------------------------------------------------------------

BAIT_ITEM_ID: dict[tuple[str, int], int] = {
    # 魔幻
    (REGION_MAGIC, 7): 7,    # 微型法阵
    (REGION_MAGIC, 8): 11,   # 永燃焰
    (REGION_MAGIC, 9): 15,   # 星陨鳞粉
    (REGION_MAGIC, 10): 19,  # 吐息碎片
    # 科幻
    (REGION_SCI_FI, 7): 8,   # 纳米钩爪
    (REGION_SCI_FI, 8): 12,  # 量子成像
    (REGION_SCI_FI, 9): 16,  # 轨道诱鱼信标
    (REGION_SCI_FI, 10): 20, # 微型黑洞
    # 玄幻
    (REGION_XIANHUAN, 7): 9,    # 灵石
    (REGION_XIANHUAN, 8): 13,   # 天道气息
    (REGION_XIANHUAN, 9): 17,   # 归墟道种
    (REGION_XIANHUAN, 10): 21,  # 龙蕴结晶
    # 克苏鲁
    (REGION_CTHULHU, 7): 10,    # 触须
    (REGION_CTHULHU, 8): 14,    # 梦潮腮瓣
    (REGION_CTHULHU, 9): 18,    # 搏动眼球
    (REGION_CTHULHU, 10): 22,   # 漆黑物质
}


# ---------------------------------------------------------------------------
# 精炼道具 ID 池（按 Boss 星级映射）
# items 8 归元护符 (7星) / 9 晨星祝福徽记 (8星) / 10 零点保险协议 (9星)
# 10 星 Boss 也只出 9 星精炼（策划案 10.1）
# ---------------------------------------------------------------------------

REFINE_ITEM_FOR_BOSS_STAR: dict[int, int] = {
    7: 8,
    8: 9,
    9: 10,
    10: 10,
}


# ---------------------------------------------------------------------------
# 装备池：(region, boss_star, equip_type) -> [rod_id 或 accessory_id, ...]
#
# 内容来源：initial_seed.sql rods / accessories 表 7-10 星条目，按描述风格人工分组。
# **首版分组待运营人审**：以下分组覆盖能拿到的现网装备，但具体风格归属可能略有偏差，
# 后续可通过修改本表调整，不需要数据库迁移。
# ---------------------------------------------------------------------------

EQUIP_TYPE_ROD = "rod"
EQUIP_TYPE_ACCESSORY = "accessory"

# 7-10 星鱼竿 ID（参见 initial_seed.sql rods 表 rarity>=7）
# 现网具体星级与 region 分布如下（人审建议在此完善）：

EQUIPMENT_POOL: dict[tuple[str, int, str], tuple[int, ...]] = {
    # ---- 玄幻 ----
    (REGION_XIANHUAN, 7, EQUIP_TYPE_ROD): (10, 11, 12),     # 桃木伏波竿 / 紫竹仙人钓 / 钓灵符
    (REGION_XIANHUAN, 8, EQUIP_TYPE_ROD): (22, 23, 25),     # 诛仙竿 / 百炼成仙竿 / 冰晶溯流竿
    (REGION_XIANHUAN, 7, EQUIP_TYPE_ACCESSORY): (10, 11, 12), # 镇魂铃 / 太极鱼坠 / 紫府符纸
    (REGION_XIANHUAN, 8, EQUIP_TYPE_ACCESSORY): (22, 24),     # 青鸾褪羽 / 通幽玉佩
    # 9-10 星暂用 8 星池兜底（现网尚无 9-10 星装备）
    (REGION_XIANHUAN, 9, EQUIP_TYPE_ROD): (22, 23, 25),
    (REGION_XIANHUAN, 9, EQUIP_TYPE_ACCESSORY): (22, 24),
    (REGION_XIANHUAN, 10, EQUIP_TYPE_ROD): (22, 23, 25),
    (REGION_XIANHUAN, 10, EQUIP_TYPE_ACCESSORY): (22, 24),

    # ---- 魔幻 ----
    (REGION_MAGIC, 7, EQUIP_TYPE_ROD): (14, 15),            # 雷云捕手 / 古龙鳞竿
    (REGION_MAGIC, 8, EQUIP_TYPE_ROD): (26,),                # 龙王骨竿
    (REGION_MAGIC, 7, EQUIP_TYPE_ACCESSORY): (14, 15),       # 海洋之心 / 古龙之息
    (REGION_MAGIC, 8, EQUIP_TYPE_ACCESSORY): (26, 27),       # 丰收号角 / 圣杯水滴
    (REGION_MAGIC, 9, EQUIP_TYPE_ROD): (26,),
    (REGION_MAGIC, 9, EQUIP_TYPE_ACCESSORY): (26, 27),
    (REGION_MAGIC, 10, EQUIP_TYPE_ROD): (26,),
    (REGION_MAGIC, 10, EQUIP_TYPE_ACCESSORY): (26, 27),

    # ---- 克苏鲁 ----
    (REGION_CTHULHU, 7, EQUIP_TYPE_ROD): (7, 8, 9),         # 触须钓线 / 黏液浮标杆 / 神经传导钓竿
    (REGION_CTHULHU, 8, EQUIP_TYPE_ROD): (19, 20, 21),       # 共生触腕 / 尖细的羊蹄竿 / 黏稠漆黑的羊蹄竿
    (REGION_CTHULHU, 7, EQUIP_TYPE_ACCESSORY): (7, 8, 9),    # 蠕动眼瞳护符 / 胎膜手套 / 哀鸣项链
    (REGION_CTHULHU, 8, EQUIP_TYPE_ACCESSORY): (19, 20, 21, 23, 25), # 黄印怀表 / 孕梦珊瑚戒指 / 同心眼网袋 / 心魔标 / 水妖发丝腕带
    (REGION_CTHULHU, 9, EQUIP_TYPE_ROD): (19, 20, 21),
    (REGION_CTHULHU, 9, EQUIP_TYPE_ACCESSORY): (19, 20, 21, 23, 25),
    (REGION_CTHULHU, 10, EQUIP_TYPE_ROD): (19, 20, 21),
    (REGION_CTHULHU, 10, EQUIP_TYPE_ACCESSORY): (19, 20, 21, 23, 25),

    # ---- 科幻 ----
    (REGION_SCI_FI, 7, EQUIP_TYPE_ROD): (16, 17, 18, 13),   # 虫洞投射钓竿 / 纳米修复钓竿 / 量子纠缠钓竿 / 溪语者
    (REGION_SCI_FI, 8, EQUIP_TYPE_ROD): (24,),               # 万竿归宗
    (REGION_SCI_FI, 7, EQUIP_TYPE_ACCESSORY): (16, 17, 18, 13), # 虚空虹吸戒指 / 全息渔获扫描仪 / AI 鱼饵决策器 / 藤蔓编织鱼篓
    (REGION_SCI_FI, 8, EQUIP_TYPE_ACCESSORY): (28, 29, 30),  # 时间沙漏钓坠 / 智械义眼 / 暗物质坠饰
    (REGION_SCI_FI, 9, EQUIP_TYPE_ROD): (24,),
    (REGION_SCI_FI, 9, EQUIP_TYPE_ACCESSORY): (28, 29, 30),
    (REGION_SCI_FI, 10, EQUIP_TYPE_ROD): (24,),
    (REGION_SCI_FI, 10, EQUIP_TYPE_ACCESSORY): (28, 29, 30),
}


# ---------------------------------------------------------------------------
# 召集者开篇模板（策划案 17.2）
# ---------------------------------------------------------------------------

REGION_OPENING_TEMPLATE: dict[str, str] = {
    REGION_XIANHUAN: "今日 {leaders} 立于灵渊之畔，鸣钟敬礼，遥引 {boss_name} 现身天地之间。",
    REGION_MAGIC:    "今日 {leaders} 镇守裂痕之地，挥剑为契，唤来 {boss_name} 撕开虚空。",
    REGION_CTHULHU:  "今日 {leaders} 凝视无底之渊，低吟禁忌之名，惊醒 {boss_name} 缓缓浮出。",
    REGION_SCI_FI:   "今日 {leaders} 接入战场坐标，启动锁定协议，{boss_name} 信号已经定位。",
}

# 无团长的兜底模板
REGION_OPENING_NO_LEADER: dict[str, str] = {
    REGION_XIANHUAN: "灵渊之畔无人鸣钟，{boss_name} 在天地间自行游弋。",
    REGION_MAGIC:    "裂痕之地无人镇守，{boss_name} 撕开虚空，无人理会。",
    REGION_CTHULHU:  "无底之渊无人凝视，{boss_name} 缓缓浮出，注视世间。",
    REGION_SCI_FI:   "战场坐标无人接入，{boss_name} 自行启动协议，无人锁定。",
}


# ---------------------------------------------------------------------------
# LLM 风格关键词 & prompt 模板（策划案 17.3）
# ---------------------------------------------------------------------------

STYLE_KEYWORDS: dict[str, str] = {
    REGION_XIANHUAN: "水墨仙气、古典中文、修行道法、洪荒苍古",
    REGION_MAGIC:    "西方暗黑奇幻、骑士史诗、远古邪恶、血与火的悲壮",
    REGION_CTHULHU:  "不可名状的恐怖、深渊低语、疯狂边缘、未知的存在",
    REGION_SCI_FI:   "赛博朋克、未来科技、协议与代码、量子与数据",
}

LLM_PROMPT_INTRO = """你正在为一款挂机钓鱼游戏的世界 Boss 玩法撰写战报。本次 Boss 来自 {region_name} 区。

风格要求：
- 严格匹配区域风格：{style_keywords}
- 简短、有画面感，整段不超过 120 字
- 不要 emoji，不要列表，纯叙事文本

请生成两段内容：

1. 登场故事（80-100 字）：描写 Boss 浮出 / 降临的情景
2. 登场台词（20-40 字）：让 Boss 用第一人称说一句威胁或宣告的话，用中文引号包裹

Boss 信息：
- 名字：{boss_name}
- 星级：{boss_star}
- 鱼类原始描述：{fish_description}

输出格式（严格按此格式输出，不要额外说明）：
[登场]
（故事文本）

[台词]
"（一句话）"
"""

LLM_PROMPT_HIGHLIGHT = """你正在为一款挂机钓鱼游戏的世界 Boss 玩法撰写当日战报。本次 Boss 来自 {region_name} 区。

风格要求：
- 严格匹配区域风格：{style_keywords}
- 紧凑、有戏剧性，整段不超过 150 字
- 不要 emoji，不要列表

请生成两部分：

1. 今日高光（约 80 字）：根据下方数据点改写出 2-3 个戏剧性瞬间
{highlight_data}

2. Boss 反应（约 40 字）：根据 Boss 当前血量百分比 {boss_hp_percent}% 描写它的状态
   - >75% 安然 / 嘲讽
   - 75-50% 受创但威严
   - 50-25% 怒吼 / 挣扎
   - <25% 喘息 / 即将倒下

Boss 信息：
- 名字：{boss_name}
- 星级：{boss_star}

输出格式：
[今日高光]
（高光文本，2-3 句）

[Boss 反应]
（反应文本，1-2 句）
"""

LLM_PROMPT_OUTRO = """你正在为一款挂机钓鱼游戏的世界 Boss 玩法撰写击杀战报。本次 Boss 来自 {region_name} 区。

风格要求：
- 严格匹配区域风格：{style_keywords}
- 庄严、有结束感，整段 80-120 字
- 不要 emoji，不要列表，纯叙事文本

请生成 Boss 退场故事：描写它在玩家全力之下最终倒下 / 消散 / 沉眠的情景。
可以提及最后一击的玩家 {finisher_name}，让故事有具体角色感。

Boss 信息：
- 名字：{boss_name}
- 星级：{boss_star}
- 击杀者：{finisher_name}

输出（纯文本，无格式标签）：
"""


# ---------------------------------------------------------------------------
# 图片 prompt 风格段（策划案 17.4）—— 本期 NullProvider 不会用到，但留作 V2.1 接入
# ---------------------------------------------------------------------------

IMAGE_PROMPT_REGION_STYLE: dict[str, str] = {
    REGION_XIANHUAN: (
        "A majestic mythical fish creature in traditional Chinese ink painting style, "
        "floating in ethereal mist, dragon-like elegance, soft brushwork, "
        "monochrome with hints of pale jade and gold, "
        "background: misty mountains, celestial clouds, ancient pavilion silhouette, "
        "mood: serene yet mighty, immortal aura, "
        "style_tag: traditional eastern aesthetic, sumi-e"
    ),
    REGION_MAGIC: (
        "A dark fantasy boss creature, dark fantasy illustration style, "
        "emerging from churning ocean, demonic features, blackened scales, glowing menacing eyes, "
        "background: stormy sea, jagged cliffs, gothic ruins, dark crimson sky, "
        "mood: dread, ancient evil, looming threat, "
        "style_tag: oil painting, dramatic chiaroscuro, deep blacks with crimson accents"
    ),
    REGION_CTHULHU: (
        "An eldritch unspeakable horror creature, cosmic horror illustration, "
        "unfathomable non-Euclidean form, multiple eyes, tentacles, nameless features, "
        "background: abyssal depths, sunken alien ruins, distant cold stars overhead, "
        "mood: existential dread, madness, beyond comprehension, "
        "style_tag: gothic dark, sickly green and deep purple palette, Lovecraftian aesthetic"
    ),
    REGION_SCI_FI: (
        "A cybernetic bio-engineered creature, sci-fi illustration, "
        "fusion of organic and synthetic, glowing circuits, plasma cores, neon implants, "
        "background: cyberpunk laboratory, quantum void, data stream landscape, "
        "mood: technological menace, futuristic predator, "
        "style_tag: digital painting, neon blue cyan and magenta color scheme, sharp geometric lines, cyberpunk aesthetic"
    ),
}

IMAGE_PROMPT_STAR_FEATURES: dict[int, str] = {
    7: "regular size, natural appearance",
    8: "oversized, glowing eyes, scarred body",
    9: "multiple appendages, ethereal aura, unnatural features",
    10: "colossal, ancient, otherworldly entity, surrounded by mystical runes / dark energy",
}


# ---------------------------------------------------------------------------
# Roll 加值
# ---------------------------------------------------------------------------

def rank_modifier(rank_position: int) -> int:
    """rank_position: 1-based 排名；0 / 负数视为无排名（陪跑）"""
    if rank_position is None or rank_position <= 0:
        return 1
    if rank_position <= 3:
        return 3
    if rank_position <= 10:
        return 2
    return 1


LEADER_MODIFIER_BONUS = 1


# ---------------------------------------------------------------------------
# 击杀随机奖励池
# ---------------------------------------------------------------------------

KILL_RANDOM_POOL_COUNT: dict[int, int] = {
    7: 5,
    8: 8,
    9: 12,
    10: 20,
}

# 每份独立判定类型的概率
KILL_RANDOM_TYPE_WEIGHTS: list[tuple[str, float]] = [
    ("equipment", 0.10),
    ("refine", 0.50),
    ("chip_or_crystal", 0.30),
    ("bait", 0.20),
]

# 阶段 75/50/25/kill 固定装备：1 件 / 阶段
STAGE_FIXED_EQUIPMENT_COUNT = 1

# 阶段非装备奖励基础数量（前十 ×3，陪跑 ×1）
STAGE_BAIT_BASE_COUNT = 100
STAGE_CHIP_BASE_COUNT_LOW_STAR = 3   # 7-8 星 Boss 出屑：基础 3 个
STAGE_CRYSTAL_BASE_COUNT_HIGH_STAR = 1  # 9-10 星 Boss 出晶：基础 1 个

# 击杀随机池中"风格鱼饵份"的数量
KILL_RANDOM_BAIT_PER_SHARE = 50

# 击杀随机池中"屑/晶份"的数量
KILL_RANDOM_CHIP_PER_SHARE = 1
KILL_RANDOM_CRYSTAL_PER_SHARE = 1

# 击杀随机池中"精炼份"的数量
KILL_RANDOM_REFINE_PER_SHARE = 1


# ---------------------------------------------------------------------------
# 玩家参战鱼选取
# ---------------------------------------------------------------------------

PARTICIPATING_FISH_MIN_RARITY = 6
PARTICIPATING_FISH_MAX_COUNT = 8


# ---------------------------------------------------------------------------
# 图片生成失败重试
# ---------------------------------------------------------------------------

IMAGE_RETRY_DEFAULT = 3
