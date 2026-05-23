"""Boss 图片生成 Provider 抽象与实现。"""

from __future__ import annotations

import base64
import json
import os
import re
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Optional

import requests
from PIL import Image

from astrbot.api import logger


_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._-]+")


def _sanitize_name(value: str) -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip())
    return text.strip("._") or "boss"


class BossImageProvider(ABC):
    """图片 Provider 抽象基类。"""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        region_key: str,
        boss_name: str,
        boss_star: int,
        max_retries: int = 3,
    ) -> Optional[str]:
        """生成图片，返回本地图片文件路径或 None。"""
        raise NotImplementedError


class AstrBotConfiguredOpenAIImageProvider(BossImageProvider):
    """读取 AstrBot cmd_config.json 中的 provider 配置，直连 OpenAI-compatible 生图接口。"""

    def __init__(self, plugin_root_dir: str, game_config: dict, output_dir: str):
        team_battle_cfg = (game_config or {}).get("team_battle", {}) if isinstance(game_config, dict) else {}
        self.provider_id = str(team_battle_cfg.get("image_provider_id", "") or "").strip()
        self.model_override = str(team_battle_cfg.get("image_model_override", "") or "").strip()
        self.api_mode = str(team_battle_cfg.get("image_api_mode", "openai_images") or "").strip() or "openai_images"
        self.image_size = str(team_battle_cfg.get("image_size", "1024x1024") or "").strip() or "1024x1024"
        self.timeout_seconds = int(team_battle_cfg.get("image_timeout_seconds", 120) or 120)
        self.config_path = self._resolve_config_path(
            plugin_root_dir=plugin_root_dir,
            configured_path=str(team_battle_cfg.get("image_provider_config_path", "") or "").strip(),
        )
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def _resolve_config_path(plugin_root_dir: str, configured_path: str) -> str:
        if configured_path:
            return configured_path
        plugins_dir = os.path.dirname(plugin_root_dir)
        astrbot_data_dir = os.path.dirname(plugins_dir)
        return os.path.join(astrbot_data_dir, "cmd_config.json")

    def _load_provider_spec(self) -> Optional[dict]:
        if not self.provider_id:
            return None
        if not os.path.exists(self.config_path):
            logger.warning("[team_battle] 未找到 AstrBot provider 配置文件: %s", self.config_path)
            return None
        try:
            with open(self.config_path, "r", encoding="utf-8-sig") as fp:
                config = json.load(fp)
        except Exception as exc:
            logger.warning("[team_battle] 读取 provider 配置失败: %s", exc)
            return None

        providers = config.get("provider") or []
        provider_sources = config.get("provider_sources") or []
        provider_cfg = next((item for item in providers if str(item.get("id", "")).strip() == self.provider_id), None)
        if not provider_cfg:
            logger.warning("[team_battle] 未找到 provider_id=%s 的配置", self.provider_id)
            return None

        source_id = str(provider_cfg.get("provider_source_id", "") or "").strip()
        source_cfg = next((item for item in provider_sources if str(item.get("id", "")).strip() == source_id), None)
        if not source_cfg:
            logger.warning("[team_battle] provider_id=%s 缺少 provider_source_id=%s", self.provider_id, source_id)
            return None

        api_base = str(source_cfg.get("api_base", "") or "").strip().rstrip("/")
        keys = source_cfg.get("key") or []
        api_key = str(keys[0] if keys else "").strip()
        model = self.model_override or str(provider_cfg.get("model", "") or "").strip()
        headers = dict(source_cfg.get("custom_headers") or {})

        if not api_base or not api_key or not model:
            logger.warning(
                "[team_battle] provider_id=%s 的生图配置不完整 api_base=%s model=%s",
                self.provider_id,
                bool(api_base),
                model or "<empty>",
            )
            return None

        return {
            "api_base": api_base,
            "api_key": api_key,
            "model": model,
            "headers": headers,
        }

    def generate(
        self,
        prompt: str,
        region_key: str,
        boss_name: str,
        boss_star: int,
        max_retries: int = 3,
    ) -> Optional[str]:
        spec = self._load_provider_spec()
        if spec is None:
            return None
        if self.api_mode != "openai_images":
            logger.warning("[team_battle] 暂不支持 image_api_mode=%s，仅支持 openai_images", self.api_mode)
            return None

        retry_count = max(1, int(max_retries or 1))
        last_error: Optional[Exception] = None
        for attempt in range(1, retry_count + 1):
            try:
                image_bytes = self._request_openai_images(prompt=prompt, spec=spec)
                if not image_bytes:
                    raise RuntimeError("empty image payload")
                return self._store_image(
                    image_bytes=image_bytes,
                    region_key=region_key,
                    boss_name=boss_name,
                    boss_star=boss_star,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[team_battle] Boss 生图失败 provider=%s attempt=%s/%s: %s",
                    self.provider_id,
                    attempt,
                    retry_count,
                    exc,
                )
        if last_error is not None:
            logger.warning("[team_battle] Boss 生图最终失败 provider=%s: %s", self.provider_id, last_error)
        return None

    def _request_openai_images(self, prompt: str, spec: dict) -> bytes:
        headers = {
            "Authorization": f"Bearer {spec['api_key']}",
            "Content-Type": "application/json",
        }
        headers.update(spec.get("headers") or {})

        response = requests.post(
            f"{spec['api_base']}/images/generations",
            headers=headers,
            json={
                "model": spec["model"],
                "prompt": prompt,
                "size": self.image_size,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

        data = response.json()
        items = data.get("data") or []
        if not items:
            raise RuntimeError("response missing data[0]")
        first = items[0] or {}
        if first.get("b64_json"):
            return base64.b64decode(first["b64_json"])
        if first.get("url"):
            download = requests.get(first["url"], timeout=self.timeout_seconds)
            download.raise_for_status()
            return download.content
        raise RuntimeError("response missing b64_json/url")

    def _store_image(self, image_bytes: bytes, region_key: str, boss_name: str, boss_star: int) -> str:
        with Image.open(BytesIO(image_bytes)) as image:
            normalized = image.convert("RGBA")
            filename = (
                f"{_sanitize_name(region_key)}_"
                f"{boss_star}_"
                f"{_sanitize_name(boss_name)[:48]}.png"
            )
            path = os.path.join(self.output_dir, filename)
            normalized.save(path, format="PNG")
            return path


class NullBossImageProvider(BossImageProvider):
    """占位 Provider：永远返回 None。"""

    def generate(
        self,
        prompt: str,
        region_key: str,
        boss_name: str,
        boss_star: int,
        max_retries: int = 3,
    ) -> Optional[str]:
        return None
