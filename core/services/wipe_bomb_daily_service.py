import json
import os
import threading
from copy import deepcopy
from typing import Any, Dict, Tuple

from astrbot.api import logger

from ..utils import get_last_reset_time


_STATE_LOCK = threading.Lock()


def _plugin_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _data_dir() -> str:
    data_dir = os.path.join(_plugin_root(), "data")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_wipe_bomb_daily_state_file() -> str:
    return os.path.join(_data_dir(), "tavern_wipe_bomb_daily.json")


def get_wipe_bomb_reset_marker(reset_hour: int) -> str:
    return get_last_reset_time(reset_hour).strftime("%Y-%m-%d %H:%M:%S")


def _empty_notice() -> Dict[str, Any]:
    return {
        "has_record": False,
        "user_id": "",
        "title_name": "",
        "nickname": "",
        "multiplier": 0.0,
        "profit": 0,
    }


def create_default_wipe_bomb_daily_state(reset_marker: str) -> Dict[str, Any]:
    return {
        "reset_marker": reset_marker,
        "jackpot_amount": 0,
        "king": _empty_notice(),
        "ghost": _empty_notice(),
    }


def _normalize_notice(entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        return _empty_notice()

    return {
        "has_record": bool(entry.get("has_record", False)),
        "user_id": str(entry.get("user_id", "") or ""),
        "title_name": str(entry.get("title_name", "") or ""),
        "nickname": str(entry.get("nickname", "") or ""),
        "multiplier": float(entry.get("multiplier", 0.0) or 0.0),
        "profit": int(entry.get("profit", 0) or 0),
    }


def _get_user_display_name(user: Any) -> str:
    nickname = getattr(user, "nickname", "") or ""
    if nickname:
        return nickname
    user_id = str(getattr(user, "user_id", "0000") or "0000")
    return f"渔夫{user_id[-4:]}"


def _build_notice_entry(user: Any, title_name: str, multiplier: float, profit: int) -> Dict[str, Any]:
    return {
        "has_record": True,
        "user_id": str(getattr(user, "user_id", "") or ""),
        "title_name": title_name or "",
        "nickname": _get_user_display_name(user),
        "multiplier": float(multiplier or 0.0),
        "profit": int(profit or 0),
    }


def normalize_wipe_bomb_daily_state(data: Any, reset_marker: str) -> Dict[str, Any]:
    normalized = create_default_wipe_bomb_daily_state(reset_marker)
    if isinstance(data, dict):
        normalized["reset_marker"] = str(data.get("reset_marker", reset_marker) or reset_marker)
        normalized["jackpot_amount"] = max(0, int(data.get("jackpot_amount", 0) or 0))
        normalized["king"] = _normalize_notice(data.get("king"))
        normalized["ghost"] = _normalize_notice(data.get("ghost"))
    return normalized


def _read_state_unlocked(reset_hour: int) -> Dict[str, Any]:
    state_file = get_wipe_bomb_daily_state_file()
    reset_marker = get_wipe_bomb_reset_marker(reset_hour)
    default_state = create_default_wipe_bomb_daily_state(reset_marker)

    if not os.path.exists(state_file):
        return default_state

    try:
        with open(state_file, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as exception:
        logger.error(f"读取每日擦弹状态失败: {exception}", exc_info=True)
        return default_state

    normalized = normalize_wipe_bomb_daily_state(data, reset_marker)
    if normalized.get("reset_marker") != reset_marker:
        rolled_state = create_default_wipe_bomb_daily_state(reset_marker)
        rolled_state["jackpot_amount"] = max(0, int(normalized.get("jackpot_amount", 0) or 0))
        return rolled_state
    return normalized


def _write_state_unlocked(data: Dict[str, Any]) -> None:
    state_file = get_wipe_bomb_daily_state_file()
    with open(state_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_wipe_bomb_daily_state(reset_hour: int) -> Dict[str, Any]:
    with _STATE_LOCK:
        state = _read_state_unlocked(reset_hour)
        _write_state_unlocked(state)
        return deepcopy(state)


def save_wipe_bomb_daily_state(reset_hour: int, data: Dict[str, Any]) -> Dict[str, Any]:
    with _STATE_LOCK:
        reset_marker = get_wipe_bomb_reset_marker(reset_hour)
        normalized = normalize_wipe_bomb_daily_state(data, reset_marker)
        normalized["reset_marker"] = reset_marker
        _write_state_unlocked(normalized)
        return deepcopy(normalized)


def record_wipe_bomb_daily_notice(
    reset_hour: int,
    user: Any,
    title_name: str,
    multiplier: float,
    profit: int,
) -> Dict[str, Any]:
    """Record the daily highest and lowest wipe-bomb multipliers."""
    with _STATE_LOCK:
        state = _read_state_unlocked(reset_hour)
        king = _normalize_notice(state.get("king"))
        ghost = _normalize_notice(state.get("ghost"))

        if (not king["has_record"]) or float(multiplier or 0.0) > king["multiplier"]:
            state["king"] = _build_notice_entry(user, title_name, multiplier, profit)

        if (not ghost["has_record"]) or float(multiplier or 0.0) < ghost["multiplier"]:
            state["ghost"] = _build_notice_entry(user, title_name, multiplier, profit)

        _write_state_unlocked(state)
        return deepcopy(state)


def add_wipe_bomb_jackpot(amount: int, reset_hour: int) -> int:
    if amount <= 0:
        return get_wipe_bomb_jackpot_amount(reset_hour)

    with _STATE_LOCK:
        state = _read_state_unlocked(reset_hour)
        state["jackpot_amount"] = int(state.get("jackpot_amount", 0) or 0) + int(amount)
        _write_state_unlocked(state)
        return int(state["jackpot_amount"])


def consume_wipe_bomb_jackpot(amount: int, reset_hour: int) -> Tuple[int, int]:
    if amount <= 0:
        current = get_wipe_bomb_jackpot_amount(reset_hour)
        return 0, current

    with _STATE_LOCK:
        state = _read_state_unlocked(reset_hour)
        current = int(state.get("jackpot_amount", 0) or 0)
        paid = min(current, int(amount))
        state["jackpot_amount"] = current - paid
        _write_state_unlocked(state)
        return paid, int(state["jackpot_amount"])


def get_wipe_bomb_jackpot_amount(reset_hour: int) -> int:
    with _STATE_LOCK:
        state = _read_state_unlocked(reset_hour)
        _write_state_unlocked(state)
        return int(state.get("jackpot_amount", 0) or 0)
