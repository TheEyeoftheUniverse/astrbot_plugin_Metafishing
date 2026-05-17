import threading
import time
from typing import Dict, Optional

from astrbot.api import logger

from .sqlite_utils import get_wal_size_bytes, run_wal_checkpoint


class WalMaintenanceService:
    """Keep SQLite WAL growth under control during long-running plugin uptime."""

    def __init__(
        self,
        db_path: str,
        *,
        interval_seconds: int = 300,
        passive_threshold_mb: int = 64,
        aggressive_threshold_mb: int = 512,
    ):
        self.db_path = db_path
        self.interval_seconds = max(30, int(interval_seconds))
        self.passive_threshold_bytes = max(1, int(passive_threshold_mb)) * 1024 * 1024
        self.aggressive_threshold_bytes = max(
            int(passive_threshold_mb),
            int(aggressive_threshold_mb),
        ) * 1024 * 1024
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="fish-db-wal-maintenance",
                daemon=True,
            )
            self._thread.start()
        logger.info("SQLite WAL 维护线程已启动")

    def stop(self) -> None:
        with self._lock:
            self._running = False
            thread = self._thread
            self._thread = None

        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        logger.info("SQLite WAL 维护线程已停止")

    def checkpoint_if_needed(self) -> Optional[Dict[str, int]]:
        wal_size = get_wal_size_bytes(self.db_path)
        if wal_size < self.passive_threshold_bytes:
            return None

        mode = "PASSIVE"
        if wal_size >= self.aggressive_threshold_bytes:
            mode = "RESTART"

        return self._checkpoint(mode=mode, wal_size=wal_size)

    def force_truncate_checkpoint(self) -> Dict[str, int]:
        return self._checkpoint(mode="TRUNCATE", wal_size=get_wal_size_bytes(self.db_path))

    def _checkpoint(self, *, mode: str, wal_size: int) -> Dict[str, int]:
        result = run_wal_checkpoint(self.db_path, mode=mode)
        size_mb = wal_size / (1024 * 1024) if wal_size else 0
        if wal_size >= self.passive_threshold_bytes or result["busy"] > 0:
            logger.info(
                "执行 SQLite WAL checkpoint: mode=%s wal=%.2fMB busy=%s log=%s checkpointed=%s",
                mode,
                size_mb,
                result["busy"],
                result["log_frames"],
                result["checkpointed_frames"],
            )
        return result

    def _run_loop(self) -> None:
        while self._running:
            try:
                self.checkpoint_if_needed()
            except Exception as exc:
                logger.warning(f"SQLite WAL 维护线程执行 checkpoint 失败: {exc}")

            deadline = time.time() + self.interval_seconds
            while self._running and time.time() < deadline:
                time.sleep(1)
