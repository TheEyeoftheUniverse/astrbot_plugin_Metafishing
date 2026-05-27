# 弹窗商店商品属性展示执行计划

## Internal Grade

M

## Plan

1. 在 `ShopService.get_shop_details` 的奖励补全阶段生成结构化 `attributes` 列表。
2. 对鱼竿、饰品、鱼饵、道具分别按需求字段输出标签和值。
3. 在商店弹窗渲染中显示每个奖励对应的属性标签。
4. 补充卡片内属性行样式，保持移动端可换行。
5. 运行 Python 语法检查，查看 git diff，按 AGENTS 同步测试插件目录。
6. 提交并推送到远程 `main`。

## Verification

- `python3 -m py_compile core/services/shop_service.py`
- `git diff -- core/services/shop_service.py player/templates/layout.html player/static/css/style.css`
- `rsync -a` 同步到 `/mnt/c/Users/26459/.astrbot/data/plugins/astrbot_plugin_metafishing/`

## Rollback

回滚本次涉及的需求/计划文档、`core/services/shop_service.py`、`player/templates/layout.html`、`player/static/css/style.css` 的对应改动即可。
