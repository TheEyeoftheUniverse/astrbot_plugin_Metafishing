from typing import Dict, Any, Optional

from .abstract_effect import AbstractItemEffect


class _GrantTitleBaseEffect(AbstractItemEffect):
    effect_type = None

    def __init__(
        self,
        item_template_repo=None,
        inventory_repo=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.item_template_repo = item_template_repo
        self.inventory_repo = inventory_repo

    def _resolve_title_id(self, item_template, payload: Dict[str, Any]) -> Optional[int]:
        title_id = int(payload.get("title_id", 0) or 0)
        if title_id > 0:
            return title_id

        # 优先按名称匹配：称号道具名去掉 "称号·" 前缀即为目标称号名。
        # 必须排在 item_id 兜底之前——称号道具的 item_id(30~46) 与团战称号的
        # title_id(30~36) 重叠，若先按 item_id 解析，会把「称号·鱼甄选CEO」等
        # 错发成团战称号（战团老狗/战团团长/…）。
        raw_name = getattr(item_template, "name", "") or ""
        candidate_names = [raw_name]
        if raw_name.startswith("称号·"):
            candidate_names.append(raw_name[len("称号·"):])

        if self.item_template_repo:
            for name in candidate_names:
                title = self.item_template_repo.get_title_by_name(name)
                if title:
                    return title.title_id

        # 退化兜底：item_id 恰好等于 title_id（仅在名称匹配失败时使用）
        item_id = int(getattr(item_template, "item_id", 0) or 0)
        if item_id > 0 and self.item_template_repo and self.item_template_repo.get_title_by_id(item_id):
            return item_id

        return None

    def apply(self, user, item_template, payload: Dict[str, Any], quantity: int = 1) -> Dict[str, Any]:
        if not self.inventory_repo or not self.item_template_repo:
            return {"success": False, "message": "称号授予依赖未就绪。"}

        title_id = self._resolve_title_id(item_template, payload)
        if not title_id:
            return {"success": False, "message": f"【{item_template.name}】未配置对应称号。"}

        title = self.item_template_repo.get_title_by_id(title_id)
        if not title:
            return {"success": False, "message": f"称号 ID={title_id} 不存在。"}

        owned_titles = set(self.inventory_repo.get_user_titles(user.user_id))
        if title_id in owned_titles:
            return {"success": False, "message": f"您已拥有称号【{title.name}】。"}

        self.inventory_repo.grant_title_to_user(user.user_id, title_id)
        return {"success": True, "message": f"获得称号【{title.name}】！"}


class GrantTitleEffect(_GrantTitleBaseEffect):
    effect_type = "GRANT_TITLE"


class GrantTitlePlaceholderEffect(_GrantTitleBaseEffect):
    effect_type = "GRANT_TITLE_TBD"
