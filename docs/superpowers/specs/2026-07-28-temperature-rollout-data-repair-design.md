# 市场温度数据修复与 Rollout 上线设计

## 1. 目标

修复职业资金日报的两个长期降级源：

1. Tushare `margin` 权限不足时，两融采集长期回退到旧缓存，导致两融信号恒为
   `unknown`；
2. 大盘历史资金请求被改写到只返回最新一行的 delay 端点，导致 60 日回放
   全部降级为“观察”，无法生成合格的 rollout artifact。

完成后，2026-07-28 日报必须使用可审计的 60 日真实回放通过市场温度门禁，
两融信号必须基于最新已发布数据得到 `supportive` 或 `weakening`，并重新推送。

## 2. 根因

### 2.1 两融

生产 Tushare token 没有 `margin` 接口权限。`fetch_margin()` 捕获异常后仅回退
`data/processed/margin_cache.json`，缓存日期为 2026-06-08，因此准备层按
`stale_cache` 强制输出 `unknown`。

AkShare 的 Jin10 沪深两市两融历史接口在同一环境可用，2026-07-28 收盘后
最新已发布日期为 2026-07-27。这是两融数据的正常发布滞后，而不是陈旧缓存。

### 2.2 大盘历史资金

AkShare `stock_market_fund_flow()` 原本访问
`push2his.eastmoney.com`，可返回 120 条历史记录。项目请求包装层把该域名
改写为 `push2delay.eastmoney.com`；delay 端点只返回最新一条。

此外，`requests` 的 `proxies={}` 不会关闭环境中的 `HTTP_PROXY`、
`HTTPS_PROXY` 或 `ALL_PROXY`。历史采集必须使用独立 `Session` 并设置
`trust_env=False`，才能保证不受进程环境代理污染。

## 3. 方案选择

### 3.1 两融源

采用多源优先级：

1. 有 Tushare token 时优先调用 Tushare；
2. token 缺失、权限不足、限流或网络异常时，调用 AkShare/Jin10 的上海和
   深圳历史接口；
3. 两个在线源均失败时才回退本地缓存，并标记 `stale_cache`；
4. 编程错误、返回类型错误和 schema 错误不得伪装成可恢复网络错误。

AkShare 数据只接受沪深两个市场共同存在的日期。不能用单一市场数据冒充
全市场两融余额。

### 3.2 两融新鲜度

两融相对预期交易日允许最多滞后一个中国交易日：

- 同日：`fresh`，参与方向判断；
- 上一交易日：`published_lag`，参与方向判断并在报告中披露截止日；
- 超过一个交易日、缓存回退或缺少日期：`stale`/`stale_cache`，信号为
  `unknown`。

`published_lag` 属于正常数据发布节奏，不把整份日报标为降级，也不输出
“采集不完整”警告。

### 3.3 60 日回放

历史采集使用专用东方财富请求函数：

- 只访问 HTTPS 原始历史端点；
- 使用 `requests.Session(trust_env=False)`；
- 设置明确超时、User-Agent 和固定字段；
- 校验 HTTP、JSON、`data.klines` 和列数；
- 返回不足覆盖区间时保留缺失事实，不做前向填充或伪造；
- 每条记录保存 `trade_date`、数值、`availability=fresh` 和实际 `source`。

ETF 与两融仍使用现有历史采集器。输出区间为截至 2026-07-28 的最近 60 个
完整中国交易日，即 2026-04-30 至 2026-07-28；ETF warm-up 从
2026-04-01 开始，两融从 2026-04-29 开始。

VPS 若无法连接历史端点，可部署在受控环境生成并通过哈希校验的 replay
fixture 和 artifact。日报每次仍从原始 replay 逐日重算，不信任 artifact
中的摘要。

## 4. Rollout 授权

本次用户已经明确授权以 goal 模式一次完成，不需要中间人工审批。实现提供
一个显式审批函数或命令，只有在以下自动校验全部通过后才写入：

- replay 至少 60 个严格递增且不重复的中国交易日；
- 规则版本和规则指纹匹配当前代码；
- replay SHA256 与 artifact 一致；
- artifact 摘要与逐日重算一致；
- 任一状态占比不超过 80%；
- 原始历史源具有可审计的 source；
- 回放中不存在用零替代缺失值的行为。

审批字段写为：

- `approved=true`
- `approved_by=codex-goal-2026-07-28`
- `approved_at=<Asia/Shanghai ISO timestamp>`

这不是绕过门禁；审批命令在写入前执行与日报相同的完整性和状态集中度校验。

## 5. 数据流

### 5.1 日常日报

`fetch_margin`
→ Tushare 或 AkShare/Jin10
→ 写入最新在线数据缓存
→ `prepare_temperature_inputs`
→ 同日/上一交易日有效，超过一交易日 unknown
→ 市场温度分类
→ 正常或降级日报。

### 5.2 Rollout

东方财富历史大盘资金 + ETF 历史 + AkShare/Jin10 两融历史
→ 交易日对齐
→ 保存 60 日 replay
→ 逐日执行当前温度规则
→ 生成未审批 artifact
→ 完整校验
→ 写入审批字段
→ 日报门禁再次独立校验。

## 6. 失败语义

- Tushare 权限不足：自动使用 AkShare，不产生故障；
- AkShare 单市场缺失：在线两融采集失败，不生成不完整余额；
- 所有两融在线源失败但缓存存在：使用 `stale_cache`，信号 unknown，日报降级；
- 历史大盘资金不足 60 日：不得审批 artifact；
- 状态集中度超过 80%：不得审批；
- replay 或 artifact 被修改：哈希/摘要校验失败；
- 日报主体有效但 rollout 未通过：继续推送 `[降级]`；
- 日报主体生成或通知失败：发送 `[故障]` 并返回非零。

## 7. 测试

### 7.1 单元测试

- Tushare 权限错误触发 AkShare fallback；
- token 缺失直接使用 AkShare；
- AkShare 沪深共同日期聚合正确；
- 单一市场日期不写入结果；
- 同日两融为 `fresh`；
- 上一交易日为 `published_lag` 且信号有效；
- 超过一交易日为 `unknown`；
- 历史请求不使用环境代理、不改写 delay 域名；
- 历史 schema、JSON 和行数异常 fail closed；
- artifact 只有完整校验通过后才能审批。

### 7.2 集成与生产验收

- 生成 2026-04-30 至 2026-07-28 的 60 日 replay；
- 逐日来源可审计，交易日数等于 60；
- 状态最高占比不超过 80%；
- artifact 经审批后 `_check_temperature_gate()` 返回 allowed；
- VPS 在线两融截止 2026-07-27，信号不是 `unknown`；
- 2026-07-28 日报不再显示“缺少 rollout artifact”；
- 重新推送今天日报并输出 `DAILY_JOB_STATUS=SUCCESS`；
- 全量 pytest、Ruff 和 `git diff --check` 通过。

## 8. 不在本次范围

- 两融 overheated 阈值与流通市值分母；
- 更换职业资金温度真值表；
- 用估算值、指数涨跌或 ETF 成交额代理大盘主力资金；
- 修改 watchlist_anomaly、宏观周报或月报。
