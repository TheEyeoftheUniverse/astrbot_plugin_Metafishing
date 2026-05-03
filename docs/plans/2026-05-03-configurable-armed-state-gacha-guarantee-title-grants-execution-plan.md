# 执行计划：可配置使用状态、抽卡保底发货与称号道具

## 内部执行等级

L：同一条主线内涉及服务逻辑、仓储读写、效果系统与少量命令/API 接入，但不做前端展示扩展，适合顺序推进。

## 步骤

1. 冻结仓库内需求文档，明确本轮不改迁移文件、只落后端能力。
2. 为道具 armed 状态机补充一个“无迁移依赖”的持久化承载方案：
   - 复用现有可持久化结构存储 armed 状态。
   - 在库存服务中封装通用的 armed 读取、设置、取消逻辑。
3. 接入精炼保护道具：
   - `use_item` 对支持 armed 状态的道具改为启用/取消 armed，而非直接消耗。
   - 精炼失败时只消费 armed 的保护道具。
4. 接入鱼饵自动使用过滤：
   - 自动选鱼饵时，若鱼饵被配置为受 armed 状态控制，则只从 armed 集合中选择。
   - `use_bait` 保持手动使用语义。
5. 实现抽卡保底发货：
   - 读取卡池级配置或默认映射。
   - 抽卡完成后按抽数线性发货到背包。
   - 不在返回结果中新增强制前端字段。
6. 实现称号道具效果：
   - 新增通用 item effect 处理器。
   - 使用道具时授予称号，已拥有时不消耗。
7. 做静态验证：
   - Python 语法编译
   - 关键服务导入检查

## 关键文件

- `core/services/inventory_service.py`
- `core/services/fishing_service.py`
- `core/services/gacha_service.py`
- `core/services/effect_manager.py`
- `core/services/item_effects/`
- `core/repositories/abstract_repository.py`
- `core/repositories/sqlite_inventory_repo.py`
- `core/repositories/sqlite_item_template_repo.py`
- `core/repositories/sqlite_gacha_repo.py`
- `core/domain/models.py`
- `handlers/inventory_handlers.py`
- `player/server.py`

## 验证命令

- `python3 -m py_compile main.py core/domain/models.py core/repositories/abstract_repository.py core/repositories/sqlite_inventory_repo.py core/repositories/sqlite_item_template_repo.py core/repositories/sqlite_gacha_repo.py core/services/inventory_service.py core/services/fishing_service.py core/services/gacha_service.py core/services/effect_manager.py core/services/item_effects/grant_title_effect.py handlers/inventory_handlers.py player/server.py`

## 回滚规则

- 只回滚本次新增的 armed 状态机逻辑、抽卡保底发货逻辑、称号道具效果逻辑与相关文档。
- 不回滚仓库内既有数据设计文档和用户未要求改动的功能。

## 清理

- 不保留临时脚本。
- 仅保留正式代码修改与需求/计划文档。
