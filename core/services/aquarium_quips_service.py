"""水族箱鱼类 LLM 短评服务。

每日刷新（与 daily_reset_hour 同步）：
1. 收集当日全服水族箱中实际展示的稀有鱼（rarity>=4）的 fish_id。
2. 通过 AstrBot Context 的当前 provider 调 LLM，按批生成 3 条 15-28 字的中文短评。
3. 写入 data/aquarium_fish_quips.json；上一次成功结果镜像保留为 data/aquarium_fish_quips.last_ok.json。

兜底链：LLM 失败 → 复用 last_ok 镜像 → 预置通用文案池。

需求文档：docs/requirements/2026-05-08-aquarium-rare-fish-income.md (LLM 鱼类短评模块)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import threading
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from ..repositories.abstract_repository import (
    AbstractAquariumIncomeRepository,
    AbstractItemTemplateRepository,
)
from ..utils import get_now, get_current_daily_marker


_STATE_LOCK = threading.Lock()


# 预置兜底短评池（至少 12 条；填空式：可直接接在「{人}觉得/凝视着 {鱼名} ___」之后）
FALLBACK_QUIP_POOL: List[str] = [
    "在缸里晃来晃去活像周一早晨的我",
    "一脸不知道自己为啥还活着的样子",
    "鳞片闪得让人想起没结的工资",
    "看上去比甲方还难懂",
    "和缸里的水草达成了奇怪的默契",
    "用很哲学的方式发着呆",
    "摆出一副劝你别再加班的眼神",
    "游得像在敷衍工作",
    "活得比退休金还佛系",
    "看起来随时准备申请劳动仲裁",
    "打着哈欠跟现实拉扯",
    "在缸里慢慢氧化的样子",
    "明显比上礼拜的菜单还新鲜",
    "摆出一副已经看破红尘的造型",
    "用游泳的方式表达拒绝营业",
    "看着比 KPI 还让人心累",
]

VERB_POOL_DEFAULT: List[str] = [
    "觉得", "惊叹于", "震惊于", "流连于",
    "凝视着", "端详了一会儿", "驻足看了看", "凝神望着",
]

_BATCH_SIZE = 50
_BATCH_TIMEOUT_SECONDS = 60
_BATCH_RETRY = 1
_MIN_QUIP_LEN = 6
_MAX_QUIP_LEN = 30


class AquariumQuipsService:
    """每日 LLM 短评库管理。"""

    def __init__(
        self,
        income_repo: AbstractAquariumIncomeRepository,
        item_template_repo: AbstractItemTemplateRepository,
        data_dir: str,
        game_config: Optional[Dict[str, Any]] = None,
        context: Any = None,
    ):
        self.income_repo = income_repo
        self.item_template_repo = item_template_repo
        self.data_dir = data_dir
        self.game_config = game_config or {}
        self.context = context

        os.makedirs(self.data_dir, exist_ok=True)
        self._state_file = os.path.join(self.data_dir, "aquarium_fish_quips.json")
        self._last_ok_file = os.path.join(self.data_dir, "aquarium_fish_quips.last_ok.json")

        self._refresh_running = False
        self._refresh_thread: Optional[threading.Thread] = None

    @property
    def daily_reset_hour(self) -> int:
        return int(self.game_config.get("daily_reset_hour", 0) or 0)

    # ---------------------------------------------------------------
    # 公开：取一条短评
    # ---------------------------------------------------------------

    def get_quip_for_fish(self, fish_id: int) -> str:
        """供 AquariumIncomeService narration 调用。失败时也保证返回合法字符串。"""
        try:
            state = self._read_state()
            quips_map = state.get("quips") or {}
            quips = quips_map.get(str(fish_id)) or []
            if quips:
                return random.choice(quips)
        except Exception as exc:
            logger.warning(f"读取鱼短评库失败 fish_id={fish_id}: {exc}")
        return random.choice(FALLBACK_QUIP_POOL)

    # ---------------------------------------------------------------
    # 后台 daily refresh loop
    # ---------------------------------------------------------------

    def start_daily_refresh_task(self):
        if self._refresh_thread and self._refresh_thread.is_alive():
            logger.info("水族箱短评刷新线程已在运行")
            return
        self._refresh_running = True
        self._refresh_thread = threading.Thread(
            target=self._daily_refresh_loop,
            daemon=True,
            name="AquariumQuipsRefresh",
        )
        self._refresh_thread.start()
        logger.info("水族箱短评刷新线程已启动")

    def stop_daily_refresh_task(self):
        self._refresh_running = False

    def _daily_refresh_loop(self):
        # 启动时若今日尚未刷新，立即触发一次
        try:
            if not self._is_today_refreshed_with_llm():
                self._trigger_refresh_now()
        except Exception as exc:
            logger.error(f"启动时刷新水族箱短评失败: {exc}", exc_info=True)

        while self._refresh_running:
            try:
                wait_seconds = self._seconds_until_next_reset()
                while wait_seconds > 0 and self._refresh_running:
                    sleep_chunk = min(60, wait_seconds)
                    time.sleep(sleep_chunk)
                    wait_seconds -= sleep_chunk
                if not self._refresh_running:
                    break
                self._trigger_refresh_now()
            except Exception as exc:
                logger.error(f"水族箱短评刷新循环异常: {exc}", exc_info=True)
                time.sleep(60 * 30)

    def _seconds_until_next_reset(self) -> float:
        from datetime import datetime, timedelta
        now = get_now()
        target = now.replace(hour=self.daily_reset_hour, minute=2, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return max(60.0, (target - now).total_seconds())

    def _is_today_refreshed_with_llm(self) -> bool:
        state = self._read_state()
        if not state:
            return False
        if state.get("source") != "llm":
            return False
        today = get_current_daily_marker(self.daily_reset_hour).isoformat()
        return state.get("date") == today

    def _trigger_refresh_now(self):
        """同步触发一次刷新。把 async 调用桥接到本线程。"""
        try:
            asyncio.run(self.refresh_today_quips())
        except RuntimeError as exc:
            # 如果当前线程已有 event loop（极少见），换一种方式
            logger.warning(f"asyncio.run 失败，尝试新建 loop: {exc}")
            new_loop = asyncio.new_event_loop()
            try:
                new_loop.run_until_complete(self.refresh_today_quips())
            finally:
                new_loop.close()
        except Exception as exc:
            logger.error(f"刷新水族箱短评失败: {exc}", exc_info=True)

    # ---------------------------------------------------------------
    # 核心刷新
    # ---------------------------------------------------------------

    async def refresh_today_quips(self) -> Dict[str, Any]:
        """生成今日短评库。返回最终的 state。"""
        today = get_current_daily_marker(self.daily_reset_hour).isoformat()

        if self._is_today_refreshed_with_llm():
            logger.info(f"水族箱短评库今日已是 LLM 来源（{today}），跳过")
            return self._read_state()

        active_fish_ids = self.income_repo.get_distinct_active_aquarium_fish_ids(min_rarity=4)
        if not active_fish_ids:
            logger.info("当前全服无 4★+ 水族箱鱼，跳过 LLM 刷新；仍写入空 state 占位")
            self._write_state({
                "date": today,
                "generated_at": get_now().isoformat(),
                "source": "llm",
                "model": "n/a",
                "fish_count": 0,
                "quips": {},
                "failed_at": None,
            })
            return self._read_state()

        fishes = []
        for fid in active_fish_ids:
            fish = self.item_template_repo.get_fish_by_id(int(fid))
            if fish is not None:
                fishes.append(fish)

        try:
            quips_map = await self._generate_quips_via_llm(fishes)
        except Exception as exc:
            logger.warning(f"LLM 全量调用失败：{exc}；尝试兜底链")
            quips_map = None

        if quips_map and any(quips_map.values()):
            state = {
                "date": today,
                "generated_at": get_now().isoformat(),
                "source": "llm",
                "model": self._provider_name() or "unknown",
                "fish_count": len(fishes),
                "quips": {str(k): v for k, v in quips_map.items()},
                "failed_at": None,
            }
            self._write_state(state)
            self._mirror_last_ok(state)
            logger.info(f"水族箱短评 LLM 刷新成功，共 {len(fishes)} 种鱼")
            return state

        # fallback 1: 复用 last_ok 镜像
        last_ok = self._read_last_ok()
        if last_ok and last_ok.get("quips"):
            state = deepcopy(last_ok)
            state["date"] = today
            state["source"] = "fallback_previous"
            state["failed_at"] = get_now().isoformat()
            self._write_state(state)
            logger.info("水族箱短评 LLM 失败，已复用上次成功镜像")
            return state

        # fallback 2: 预置文案
        preset_map: Dict[str, List[str]] = {
            str(fish.fish_id): random.sample(FALLBACK_QUIP_POOL, k=min(3, len(FALLBACK_QUIP_POOL)))
            for fish in fishes
        }
        state = {
            "date": today,
            "generated_at": get_now().isoformat(),
            "source": "fallback_preset",
            "model": "n/a",
            "fish_count": len(fishes),
            "quips": preset_map,
            "failed_at": get_now().isoformat(),
        }
        self._write_state(state)
        logger.warning("水族箱短评 LLM 全部失败，使用预置文案池")
        return state

    # ---------------------------------------------------------------
    # LLM 调用
    # ---------------------------------------------------------------

    def _get_provider(self):
        if self.context is None:
            return None
        getter = getattr(self.context, "get_using_provider", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception as exc:
            logger.warning(f"取 LLM provider 失败: {exc}")
            return None

    def _provider_name(self) -> str:
        provider = self._get_provider()
        if provider is None:
            return ""
        return str(getattr(provider, "name", "") or getattr(provider, "model", "") or type(provider).__name__)

    async def _generate_quips_via_llm(self, fishes: List[Any]) -> Dict[int, List[str]]:
        provider = self._get_provider()
        if provider is None:
            raise RuntimeError("当前未启用任何 LLM provider")

        result: Dict[int, List[str]] = {}
        for batch_start in range(0, len(fishes), _BATCH_SIZE):
            batch = fishes[batch_start: batch_start + _BATCH_SIZE]
            batch_quips = await self._request_batch(provider, batch)
            for fish_id, quips in batch_quips.items():
                result[fish_id] = quips
        return result

    async def _request_batch(self, provider, batch: List[Any]) -> Dict[int, List[str]]:
        prompt = self._build_prompt(batch)

        for attempt in range(_BATCH_RETRY + 1):
            try:
                completion = await asyncio.wait_for(
                    self._call_provider_text_chat(provider, prompt),
                    timeout=_BATCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(f"LLM 短评批次请求超时（尝试 {attempt + 1}/{_BATCH_RETRY + 1}）")
                continue
            except Exception as exc:
                logger.warning(f"LLM 短评批次请求异常（尝试 {attempt + 1}）: {exc}")
                continue

            parsed = self._parse_completion(completion, expected_ids={int(f.fish_id) for f in batch})
            if parsed:
                return parsed
            logger.warning(f"LLM 响应无法解析为 JSON，尝试 {attempt + 1}/{_BATCH_RETRY + 1}")
        # 全部失败：本批返回空字典，调用方会兜底
        return {}

    @staticmethod
    def _build_prompt(batch: List[Any]) -> str:
        lines = [
            "你是一名为奇幻钓鱼世界水族箱写打卡留言的旅人。我会给你若干鱼的「名字」与「描述」，",
            "请为每条鱼生成 3 条 中文短句，每条 12-22 个汉字，作为别人游客留下的「填空式」评语。",
            "",
            "【格式硬要求】",
            "  · 每条短句必须能直接接在以下任一开头后面读起来通顺：",
            "      「{某人}觉得{鱼名}____」",
            "      「{某人}惊叹于{鱼名}____」",
            "      「{某人}凝视着{鱼名}____」",
            "      「{某人}端详了一会儿{鱼名}____」",
            "    可以是「在……的样子」「像……一样」「一脸……的表情」「在……着」「活脱脱就是……」等结构。",
            "  · 不要句号、感叹号、问号、省略号；不要 emoji；不要数值/价格/具体金额。",
            "  · 不要重复鱼的名字或描述里的原句；要原创、口语化的玩笑。",
            "",
            "【风格硬要求】",
            "  搞笑、轻松、调侃；像在吐槽周一上班、调侃菜单、拿大事开小玩笑那种感觉。",
            "  允许偶尔黑色幽默（房贷、KPI、退休金、劳动仲裁、甲方等都可以拿来调侃），",
            "  但避免严肃、抒情、过度文艺，避免空泛的「神秘」「珍贵」「价值不菲」之类陈词。",
            "",
            "【输入】每行格式：fish_id|name|description",
        ]
        for fish in batch:
            name = getattr(fish, "name", "") or ""
            desc = getattr(fish, "description", "") or ""
            desc = desc.replace("\n", " ").replace("|", "/")[:140]
            lines.append(f"{int(fish.fish_id)}|{name}|{desc}")
        lines.extend([
            "",
            "【输出】严格 JSON：键是 fish_id 字符串，值是 3 条短句字符串数组。例：",
            '{"103": ["在缸里晃来晃去活像周一早晨的我", "一脸不知道自己为啥还活着的样子", "和缸里的水草达成了奇怪的默契"]}',
            "只输出 JSON 对象本身，不要任何解释、前后缀或代码块标记。",
        ])
        return "\n".join(lines)

    @staticmethod
    async def _call_provider_text_chat(provider, prompt: str) -> str:
        """兼容 AstrBot 不同版本 provider.text_chat 形态，返回 completion 字符串。"""
        text_chat = getattr(provider, "text_chat", None)
        if not callable(text_chat):
            raise RuntimeError("provider 未提供 text_chat 接口")

        # 尝试常见调用形态。第一种最贴近主流文档。
        attempts = [
            lambda: text_chat(
                prompt=prompt,
                session_id=None,
                contexts=[],
                image_urls=[],
                system_prompt="",
            ),
            lambda: text_chat(prompt=prompt),
            lambda: text_chat(prompt),
        ]

        last_error: Optional[Exception] = None
        for invoker in attempts:
            try:
                response = invoker()
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                break

            if asyncio.iscoroutine(response):
                response = await response

            text = AquariumQuipsService._extract_completion_text(response)
            if text:
                return text
        raise RuntimeError(f"provider.text_chat 调用失败: {last_error}")

    @staticmethod
    def _extract_completion_text(response: Any) -> str:
        if response is None:
            return ""
        if isinstance(response, str):
            return response
        for attr in ("completion_text", "content", "text", "message"):
            value = getattr(response, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        # 字典形态
        if isinstance(response, dict):
            for key in ("completion_text", "content", "text", "message"):
                value = response.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return ""

    @staticmethod
    def _normalize_quip(raw: str) -> str:
        """剥去填空式短句尾部的标点（句号/感叹号/问号/省略号等），保证拼接通顺。"""
        if not raw:
            return ""
        s = raw.strip()
        # 反复剥末尾的中英文句末符号
        terminal_chars = {"。", "！", "？", ".", "!", "?", "…", "～", "~", "，", ",", "、"}
        while s and s[-1] in terminal_chars:
            s = s[:-1].rstrip()
        return s

    @staticmethod
    def _parse_completion(text: str, expected_ids: set) -> Dict[int, List[str]]:
        if not text:
            return {}
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        json_str = match.group(0)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return {}

        result: Dict[int, List[str]] = {}
        for raw_key, value in (data or {}).items():
            try:
                fish_id = int(raw_key)
            except (TypeError, ValueError):
                continue
            if fish_id not in expected_ids:
                continue
            if not isinstance(value, list):
                continue
            cleaned: List[str] = []
            for item in value:
                if not isinstance(item, str):
                    continue
                normalized = AquariumQuipsService._normalize_quip(item)
                if _MIN_QUIP_LEN <= len(normalized) <= _MAX_QUIP_LEN:
                    cleaned.append(normalized)
            if cleaned:
                result[fish_id] = cleaned[:3]
        return result

    # ---------------------------------------------------------------
    # 文件 IO（带锁）
    # ---------------------------------------------------------------

    def _read_state(self) -> Dict[str, Any]:
        with _STATE_LOCK:
            if not os.path.exists(self._state_file):
                return {}
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
            except Exception as exc:
                logger.warning(f"读取 quips state 失败: {exc}")
                return {}

    def _write_state(self, state: Dict[str, Any]):
        with _STATE_LOCK:
            tmp_path = self._state_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._state_file)

    def _mirror_last_ok(self, state: Dict[str, Any]):
        with _STATE_LOCK:
            tmp_path = self._last_ok_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._last_ok_file)

    def _read_last_ok(self) -> Optional[Dict[str, Any]]:
        with _STATE_LOCK:
            if not os.path.exists(self._last_ok_file):
                return None
            try:
                with open(self._last_ok_file, "r", encoding="utf-8") as f:
                    return json.load(f) or None
            except Exception as exc:
                logger.warning(f"读取 last_ok 失败: {exc}")
                return None
