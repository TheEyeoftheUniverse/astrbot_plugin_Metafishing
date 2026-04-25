# 需求冻结：首启种子数据与当前整理表对齐

## 目标

将 `astrbot_plugin_fishing` 的首启数据注入能力扩展到当前已经整理好的“种子型表”，使新库初始化后即可得到与当前基线配置一致的模板、区域、卡池、商店及其关联数据。

## 范围

- 扩展首启注入逻辑，覆盖当前可视为基线配置的表。
- 保持注入逻辑幂等，可重复执行而不会重复插入。
- 保留现有 migration 负责建表，首启注入负责灌入基线数据的职责划分。
- 保留管理员手动同步入口，并让其与首启注入使用同一份基线数据来源。

## 进入首启种子的表

- `fish`
- `baits`
- `rods`
- `accessories`
- `titles`
- `items`
- `gacha_pools`
- `gacha_pool_items`
- `fishing_zones`
- `zone_fish_mapping`
- `shops`
- `shop_items`
- `shop_item_costs`
- `shop_item_rewards`
- `aquarium_upgrades`
- `commodities`

## 不进入首启种子的表

- 所有用户运行态表：
  `users`、`user_accessories`、`user_achievement_progress`、`user_aquarium`、`user_bait_inventory`、`user_buffs`、`user_commodities`、`user_fish_inventory`、`user_fish_stats`、`user_items`、`user_rods`、`user_titles`
- 所有记录/日志/历史表：
  `check_ins`、`fishing_records`、`gacha_records`、`shop_purchase_records`、`red_packet_records`、`wipe_bomb_log`、`taxes`
- 所有运行态业务表：
  `market`、`red_packets`
- 所有内部元数据表：
  `schema_version`、`sqlite_sequence`
- `exchange_prices`：
  继续视为运行期价格历史，不把历史快照并入首启种子；首启时仍由交易所逻辑使用配置中的初始价格兜底。

## 约束

- 不把当前 `fish.db` 中的用户数据快照带入新库。
- 不把价格历史、红包状态、市场挂单等带入新库。
- 不改动现有 migration 的版本线，不重写历史 migration 逻辑。
- 不要求把所有基线数据继续手写在 `initial_data.py` 中；可以引入新的种子载体，只要项目内置、可随仓库版本管理、可被首启与手动同步复用。

## 验收标准

- 新数据库首次启动后，上述“进入首启种子”的表具备完整基线数据。
- 重复执行首启种子同步不会产生重复行或主键冲突。
- 现有运行态表不会被种子逻辑写入基线快照数据。
- 管理员手动同步入口与首启使用同一份基线来源，不再出现一边来自 `initial_data.py`、一边来自其他快照的分叉真相。

## 非目标

- 不迁移现有线上用户数据。
- 不补做导入导出 UI。
- 不把交易所历史价格改造成静态配置。
