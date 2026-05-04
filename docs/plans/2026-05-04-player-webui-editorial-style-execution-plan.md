# 玩家 WebUI Minimal Editorial Style 执行计划

日期：2026-05-04

## 内部等级

L：单仓库前端重构，涉及全局 CSS 与少量模板结构，串行执行即可。

## 执行阶段

1. Skeleton Check
   - 确认分支、未提交改动、玩家端模板与样式入口。
   - 输出运行收据。

2. Requirement Freeze
   - 冻结 `docs/requirements/2026-05-04-player-webui-editorial-style.md`。

3. Style System
   - 重写 `player/static/css/style.css`：
     - 暖纸背景与微弱纸张纹理。
     - serif 标题、细分隔线、低阴影。
     - 全局 Bootstrap 组件覆盖，降低卡片厚重感。
     - 单主 CTA 视觉：`btn-primary` 明亮游戏色，其余按钮更克制。

4. Template Refinement
   - 更新 `player/templates/layout.html` 的导航、main、footer、通知面板类名。
   - 更新 `player/templates/index.html` 的首页开头、统计区和快捷操作，使“开始钓鱼”为唯一主 CTA。
   - 更新 `player/templates/login.html` 的登录首屏表达，匹配编辑工作台风格。
   - 不修改 `oauth_complete.html`，避免覆盖用户已有改动。

5. Verification
   - `python3 -m py_compile player/server.py`
   - Jinja 模板基础解析检查。
   - `git diff --check`

6. Cleanup
   - 清理临时文件，写入 cleanup receipt 与 delivery acceptance report。

## 回滚规则

- 若模板解析失败，优先回滚对应模板改动。
- 若 CSS 影响过宽，保留结构改动并收窄选择器。
- 不回滚用户已有未提交改动。
