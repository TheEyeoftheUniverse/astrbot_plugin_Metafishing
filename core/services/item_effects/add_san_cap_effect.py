from typing import Any, Dict

from .abstract_effect import AbstractItemEffect
from ...repositories.sqlite_cthulhu_repo import SqliteCthulhuRepository


class AddSanCapEffect(AbstractItemEffect):
    effect_type = "ADD_MAX_SAN"

    def __init__(self, cthulhu_repo: SqliteCthulhuRepository | None = None, **kwargs):
        super().__init__(**kwargs)
        self.cthulhu_repo = cthulhu_repo

    def apply(self, user, item_template, payload: Dict[str, Any], quantity: int = 1) -> Dict[str, Any]:
        if self.cthulhu_repo is None:
            return {"success": False, "message": "克苏鲁系统未启用。"}
        amount = int(payload.get("amount", 0) or 0) * int(quantity)
        if amount <= 0:
            return {"success": False, "message": "无效的 SAN 上限道具。"}
        state = self.cthulhu_repo.ensure_state(user.user_id)
        new_max = int(state["max_san"]) + amount
        self.cthulhu_repo.update_state_fields(user.user_id, max_san=new_max)
        return {"success": True, "message": f"你听见更多目光落在自己身上。SAN 上限提升至 {new_max}。"}
