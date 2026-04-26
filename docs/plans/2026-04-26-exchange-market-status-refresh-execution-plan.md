# 交易所市场状态动态刷新执行计划

日期：2026-04-26

## 级别

M

## 实施步骤

1. 在 `ExchangePriceService` 内新增市场状态汇总逻辑。
2. 用最近价格点与前一价格点计算整体趋势与情绪。
3. 用当前用户持仓统计推导供需状态。
4. 将结果接入 `get_market_status()` 返回结构。
5. 进行静态检查并同步到测试目录。

## 验证

- `python3 -m py_compile core/services/exchange_price_service.py`
- 读取 `get_market_status()` 相关返回值，确认不再是固定常量路径。

## 回滚规则

- 仅影响 `ExchangePriceService` 的状态文案生成。
- 若计算逻辑异常，回退到默认文案而不是中断交易所页面。

