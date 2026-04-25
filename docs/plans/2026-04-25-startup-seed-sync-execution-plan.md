# 执行计划：首启种子数据与当前整理表对齐

## 内部执行等级

L：本次工作跨越基线数据载体、`DataSetupService` 和手动同步入口，但实现链路集中，适合串行执行。

## 步骤

1. 冻结种子边界：
   明确只导入配置/模板类表，排除用户运行态、日志历史、市场与红包状态、交易所价格历史。
2. 选择统一种子载体：
   将当前基线配置表整理为仓库内置的 SQL 种子文件，避免继续把大批量结构化数据手写散落在多个 Python 常量中。
3. 改造 `DataSetupService`：
   - 在首启时执行内置种子脚本。
   - 保持幂等。
   - 让手动同步入口复用同一逻辑。
4. 保留现有职责边界：
   - migration 只负责 schema。
   - 种子脚本只负责基线配置数据。
5. 做静态验证：
   - 校验 SQL 种子文件包含目标表。
   - 校验 Python 代码可编译。
   - 校验首启判断与手动同步入口都指向统一实现。

## 验证命令

- `python3 -m py_compile core/services/data_setup_service.py handlers/admin_handlers.py main.py`
- 只读检查种子文件中是否覆盖目标表：
  `rg "INSERT OR IGNORE INTO (fish|baits|rods|accessories|titles|items|gacha_pools|gacha_pool_items|fishing_zones|zone_fish_mapping|shops|shop_items|shop_item_costs|shop_item_rewards|aquarium_upgrades|commodities)" core`

## 回滚规则

- 只回滚本次新增的种子文件和 `DataSetupService` 相关接入改动。
- 不回滚用户已有的其他未提交改动。

## 清理

- 不保留一次性导出脚本或临时中间文件。
- 交付结果只保留正式种子文件、代码接入与本次文档。
