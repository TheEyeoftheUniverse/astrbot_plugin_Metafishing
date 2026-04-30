# 执行计划：图鉴奖励高级货币系统

## 内部执行等级

L：涉及数据库迁移、仓储接口、服务逻辑、指令入口和玩家 WebUI，按单主线顺序推进。

## 步骤

1. 冻结图鉴奖励需求文档，明确节点、总额、指令行为和 WebUI 展示要求。
2. 新增图鉴奖励领取记录表：
   - 添加迁移脚本。
   - 更新 `schema_latest.sql`。
   - 为仓储层补充查询与写入接口。
3. 在 `FishingService` 中实现统一的图鉴奖励状态与领取逻辑：
   - 复用现有图鉴统计口径。
   - 计算节点阈值、可领奖励、下一节点和累计状态。
   - 发放高级货币并持久化领取记录。
4. 接入指令端：
   - 注册 `图鉴奖励` 命令。
   - 输出当前进度、领取结果和下一节点信息。
5. 接入玩家 WebUI：
   - 图鉴页增加图鉴奖励卡片。
   - 增加领取提交入口。
   - 成功后刷新进度并显示提示信息。
6. 进行静态验证：
   - 校验迁移脚本与 Python 文件可编译。
   - 校验命令和 WebUI 关键链路没有引用缺失。

## 验证命令

- `python3 -m py_compile main.py handlers/fishing_handlers.py core/services/fishing_service.py core/repositories/abstract_repository.py core/repositories/sqlite_log_repo.py player/server.py manager/user_api.py`

## 回滚规则

- 只回滚本次新增的图鉴奖励表结构、服务逻辑、命令入口和玩家图鉴页改动。
- 不回滚仓库中已有的 README 改动或其他用户未授权修改。

## 清理

- 不保留临时脚本与临时验证文件。
- 仅保留正式迁移、需求文档、执行计划和功能代码。
