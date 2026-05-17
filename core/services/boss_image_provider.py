"""
Boss 图片生成 Provider 抽象。

本期 NullProvider 始终返回 None，调用方应在拿到 None 时降级为占位封面。
真实生图接入留 V2.1（外部 API / AstrBot 内置 image provider）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


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
        """生成图片，返回本地图片文件路径或 None（失败 / 未实装）。"""
        raise NotImplementedError


class NullBossImageProvider(BossImageProvider):
    """占位 Provider：永远返回 None。

    本期默认装配此实现；PIL 渲染层检测 None 时使用占位封面图。
    """

    def generate(
        self,
        prompt: str,
        region_key: str,
        boss_name: str,
        boss_star: int,
        max_retries: int = 3,
    ) -> Optional[str]:
        return None
