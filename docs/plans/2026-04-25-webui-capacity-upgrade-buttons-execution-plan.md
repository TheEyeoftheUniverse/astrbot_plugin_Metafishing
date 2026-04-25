# WebUI 容量升级按钮执行计划

- 日期：2026-04-25
- Internal grade：`M`

## Wave

1. 为鱼塘和水族箱页面整理容量升级数据来源。
2. 在 `player/server.py` 增加页面上下文与升级 API。
3. 修改 `fishpond.html` 与 `aquarium.html`，补齐容量卡片、升级信息与按钮交互。
4. 运行静态验证并整理运行收据。

## Ownership Boundaries

- 后端路由与页面上下文：`player/server.py`
- 前端模板：`player/templates/fishpond.html`、`player/templates/aquarium.html`
- 治理文档与收据：`docs/`、`outputs/runtime/`

## Verification Commands

- `python -m py_compile /mnt/d/Fishing/astrbot_plugin_fishing/player/server.py`
- `git -C /mnt/d/Fishing/astrbot_plugin_fishing diff -- player/server.py player/templates/fishpond.html player/templates/aquarium.html`

## Delivery Acceptance Plan

- 期货页作为交互参考，检查两处容量卡片是否都有“下一级 + 花费 + 升级按钮”。
- 检查升级 API 名称与模板 `url_for(...)` 是否一致。

## Completion Language Rules

- 只有在模板和后端路由都落地，且完成静态验证后，才能说明任务完成。

## Rollback Rules

- 若页面上下文结构导致模板渲染异常，仅回退本次新增字段与按钮逻辑，不影响现有库存展示。

## Phase Cleanup Expectations

- 写入 `outputs/runtime/vibe-sessions/<run-id>/` 收据文件。
- 不产生临时脚本或无用中间文件。
