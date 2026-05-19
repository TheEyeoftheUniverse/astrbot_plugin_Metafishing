from typing import Any, Dict

from .abstract_effect import AbstractItemEffect
from ...repositories.sqlite_cthulhu_repo import SqliteCthulhuRepository


class AddSanEffect(AbstractItemEffect):
    effect_type = "ADD_SAN"

    def __init__(self, cthulhu_repo: SqliteCthulhuRepository | None = None, **kwargs):
        super().__init__(**kwargs)
        self.cthulhu_repo = cthulhu_repo

    def apply(self, user, item_template, payload: Dict[str, Any], quantity: int = 1) -> Dict[str, Any]:
        if self.cthulhu_repo is None:
            return {"success": False, "message": "克苏鲁系统未启用。"}
        amount = int(payload.get("amount", 0) or 0) * int(quantity)
        if amount <= 0:
            return {"success": False, "message": "无效的 SAN 恢复道具。"}
        state = self.cthulhu_repo.ensure_state(user.user_id)
        current_san = min(int(state["max_san"]), int(state["current_san"]) + amount)
        self.cthulhu_repo.update_state_fields(user.user_id, current_san=current_san)
        return {"success": True, "message": f"理智微微回潮，当前 SAN：{current_san}/{state['max_san']}。"}
