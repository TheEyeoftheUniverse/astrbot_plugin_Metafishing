# 执行计划：成就日检与钓鱼后台减负

## 内部执行等级

M：改动集中在 `AchievementService`、`FishingService`、日志仓储和用户仓储，外部接口变化有限。

## 步骤

1. 冻结本次需求文档，明确“成就日检、自动钓鱼减负、日志日清”的边界。
2. 调整成就后台任务：
   - 注入 `daily_reset_hour` 配置。
   - 用刷新周期标记替代 `10` 分钟轮询。
   - 启动后补做当前周期未执行的检查。
3. 调整日志仓储：
   - 去掉写日志时的全局过期清理。
   - 新增每日集中清理方法，保留 `30` 天过期删除和每用户最近 `50` 条截断规则。
4. 优化自动钓鱼：
   - 新增批量读取自动钓鱼用户方法。
   - 自动钓鱼循环复用已取用户对象。
   - 把每日维护改成按刷新周期检查，避免在循环中重复触发。
5. 新增数据库迁移：
   - 添加 `auto_fishing_enabled` 索引。
   - 添加日志时间清理索引。
6. 做静态验证并检查受影响代码路径是否一致。

## 验证命令

- `python3 -m py_compile main.py core/services/achievement_service.py core/services/fishing_service.py core/repositories/abstract_repository.py core/repositories/sqlite_user_repo.py core/repositories/sqlite_log_repo.py core/database/migrations/044_optimize_auto_fishing_and_log_cleanup.py`

## 回滚规则

- 只回滚本次新增的成就调度、日志集中清理、自动钓鱼批量查询和索引迁移。
- 不回滚用户已有数据库内容。
- 不删除历史成就进度、日志记录和现有业务配置。

## 清理

- 不保留临时脚本。
- 保留本次需求与计划文档。
- 不新增额外守护进程或外部依赖。
