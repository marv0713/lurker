# Legacy 策略、报告语义与交易日历收口设计

日期：2026-07-28

## 1. 目标

本阶段收口三个彼此相关、但不改变主策略判断公式的问题：

1. 明确 Legacy 策略的生命周期、选择规则和风险披露；
2. 清除旧评分链路中从未接入的数据维度，并澄清日报、周报标签的时间口径；
3. 用可注入、可缓存、失败关闭的上交所交易日历替代 2026 年硬编码节假日表。

同时保留职业资金日报在个股资金流采集失败时仍可推送的降级策略，但报告必须明确
候选列表不完整。

本阶段不恢复“长牛发现器”，不新增 52 周新高、相对强度、成交量扩张、产业链扩散
或板块持续放量算法，也不修改日报和周报现有标签分类条件。

## 2. 已确认方案

### 2.1 Legacy 策略显式退役

`long_term_trend` 保持 `enabled: false`，新增
`lifecycle: deprecated`。自动选择永远不运行 disabled 或 deprecated 策略；用户在
命令行显式点名时仍可运行，但报告标题之后必须出现弃用警告和能力缺口。

### 2.2 清理配置，不伪造能力

删除调用方传入的固定零值和固定 `False`。发行版 `scoring.yaml` 只保留当前实际有
数据输入的评分项，并把 `return_120_180d` 更名为 `return_180d`。

旧算法的当前有效得分上限保持不变。不能通过修改阈值、重分配权重或把缺失指标当作
中性/正向指标来“修复”已弃用策略。

### 2.3 标签算法不变，只补时间语义

- 日报使用 `当日资金状态：主线 / 扩散 / 分化 / 退潮`；
- 周报使用 `周度持续状态：延续 / 新主线 / 退潮`。

两套算法回答不同问题，不合并枚举、不互相映射。

### 2.4 周报回退，日报不回填

周报在周末或休市日运行时，回退到最近一个已确认的中国股票市场交易日。日报在
非交易日仍直接跳过，不自动补跑上一交易日。显式未来日期一律拒绝。

## 3. 策略生命周期

### 3.1 配置契约

`configs/strategies.yaml` 中每个策略新增：

```yaml
strategies:
  professional_flow_daily:
    enabled: true
    lifecycle: active
    cadence: daily
    universe: resolved_seed_pool
    title: 职业资金雷达日报
    params: {}

  long_term_trend:
    enabled: false
    lifecycle: deprecated
    cadence: daily
    universe: resolved_seed_pool
    title: 中长期趋势雷达（Legacy）
    limitations:
      - 52 周高点距离未接入
      - 相对大盘和相对板块强度未接入
      - 成交量扩张未接入
      - 板块新高、产业链扩散和持续放量未接入
    params:
      signal_threshold: 60
      main_limit: 10
      low_score_watch_limit: 5
```

类型与校验：

- `lifecycle` 只接受 `active`、`deprecated`，缺省为 `active`，兼容现有自定义配置；
- `deprecated` 策略必须同时为 `enabled: false`；
- `deprecated` 策略必须提供至少一条非空 `limitations`；
- `active` 策略不允许声明非空 `limitations`；
- `enabled` 必须是真正的 YAML boolean，不能用字符串或数字代替；
- 未知 lifecycle 值在加载时立即报错。

### 3.2 选择矩阵

| 调用方式 | `active + enabled` | `active + disabled` | `deprecated + disabled` |
|---|---:|---:|---:|
| 未传策略名，自动选择 | 运行 | 不运行 | 不运行 |
| 显式传入策略名 | 运行 | 运行 | 运行并警告 |

未知策略名的处理不在本阶段扩展；`cadence` 过滤仍在显式选择后生效。

即使未来配置错误地绕过校验把 deprecated 策略设为 enabled，自动选择也必须以
`lifecycle` 再做一次防御性排除。

### 3.3 弃用警告

deprecated 策略每次显式运行时，在报告主标题和日期之后插入：

```markdown
> ⚠️ 弃用策略：`long_term_trend` 仅供历史兼容，不代表当前推荐信号。
> 能力缺口：52 周高点距离未接入；相对大盘和相对板块强度未接入；成交量扩张未接入；
> 板块新高、产业链扩散和持续放量未接入。
```

要求：

- 单策略报告和多策略组合报告都必须显示；
- 警告从结构化 lifecycle 与 limitations 生成，不能只写死在某一个 Markdown 模板；
- `StrategyResult.metadata` 保留 lifecycle 和 limitations，供归档与测试使用；
- active 策略不显示该警告。

## 4. Legacy 评分配置收口

### 4.1 个股评分

`configs/scoring.yaml` 的发行版个股权重只保留：

```yaml
stock_signal:
  thresholds:
    candidate: 70
    high_priority: 85
  weights:
    return_20d: 15
    return_60d: 15
    return_180d: 15
    double_bagger: 15
```

`signal_scan.py` 只向评分函数传入实际计算出的：

- `return_20d_percentile`；
- `return_60d_percentile`；
- `return_180d`。

`return_120d` 仍可保留在 `StockSignal.returns` 中用于历史数据展示，但不再进入强度评分
或 double-bagger 分类。

删除 `near_52w_high`、`relative_market_strength`、
`relative_sector_strength`、`turnover_expansion` 的调用方占位值。

`score_stock_strength()` 将中长周期权重读取键改为 `return_180d`。纯函数内部可以
继续用 `.get()` 安全处理调用方缺字段，也可以兼容显式传入的扩展指标；但发行版配置、
Legacy 报告和能力说明不得把这些扩展指标宣称为已接入。

权重不重分配，因此当前发行版个股 Legacy 评分最高仍为 60 分。`candidate: 70` 和
`high_priority: 85` 本轮不调整，因为该策略已弃用，本阶段目标是如实暴露现状，而不是
改变其选股结果。

### 4.2 板块评分

发行版板块权重只保留：

```yaml
sector_signal:
  thresholds:
    candidate: 65
    main_candidate: 75
    watchlist_pending: 85
  weights:
    sector_strength: 20
    strong_stock_count: 20
    cross_market_mapping: 15
```

`sector_scan.py` 只传入当前实际计算出的：

- `sector_outperformance`；
- `strong_stock_count`；
- `cross_market_count`。

删除 `new_high_ratio`、`chain_segments`、`turnover_persistent` 的占位值。不重分配
被删除的 45 分，因此当前发行版板块 Legacy 评分最高仍为 55 分。

### 4.3 权重键与指标键映射

`scoring.yaml` 的 weight key 是稳定的“评分维度名”，调用方传入的 metric key 是
“已计算事实名”。两者不要求同名，映射只在评分函数中维护；调用方不能为了匹配配置
而复制或改名指标。

发行版映射固定为：

| 评分 | weight key | metric key | 触发条件 |
|---|---|---|---|
| 个股 | `return_20d` | `return_20d_percentile` | `>= 0.90` |
| 个股 | `return_60d` | `return_60d_percentile` | `>= 0.90` |
| 个股 | `return_180d` | `return_180d` | `>= 0.30` |
| 个股 | `double_bagger` | `return_180d` | `>= 0.80` |
| 板块 | `sector_strength` | `sector_outperformance` | `is True` |
| 板块 | `strong_stock_count` | `strong_stock_count` | `>= 3` |
| 板块 | `cross_market_mapping` | `cross_market_count` | `>= 2` |

配置加载器校验 weight key，评分函数读取 metric key。映射测试必须逐项使用非默认
权重，证明修改某个 weight 只影响对应条件，避免实现时误用同名查找。

### 4.4 长周期窗口一致性

`stock_strength` 评分和 double-bagger 分类统一使用 `return_180d`：

- `return_180d >= 0.30` 触发中长周期强度分；
- 不再使用 `max(return_120d, return_180d)`；
- 180 日数据缺失时该分类为 `none`，不能用 120 日收益冒充 180 日收益。

分类按从高到低的顺序判断：

| 条件 | 分类 |
|---|---|
| `return_180d >= 2.00` | `multi_bagger` |
| `1.00 <= return_180d < 2.00` | `double` |
| `0.80 <= return_180d < 1.00` | `near_double` |
| `return_180d < 0.80` 或缺失 | `none` |

这会修正 Legacy 报告中长周期分类窗口不一致，但不改变评分权重、得分上限或 active
职业资金策略。

### 4.5 兼容边界

- 不为旧键 `return_120_180d` 建立长期迁移层；
- 仓库内配置、测试和文档全部改用 `return_180d`；
- 外部自定义配置若继续使用旧键，不得被静默解释为新键；加载评分配置时应明确报出
  未支持的旧键及替代名称；
- 领域纯函数仍可测试完整指标组合，但这类测试必须说明它验证的是函数扩展能力，不是
  发行版数据链路已接入。

## 5. 报告标签语义

### 5.1 日报

保留 `_classify_sector_label(rank, inflow)` 的现有条件。职业资金日报的每条板块输出
改为：

```text
通信设备：主力净流入 3.2亿，当日资金状态：主线
```

候选内部若展示板块标签，也使用相同前缀。禁止只输出裸词“主线”而不说明时间口径。

### 5.2 周报

保留 `_sector_label(row)` 的现有条件。职业资金周报的每条板块输出改为：

```text
通信设备：周度持续状态：延续，正流入 4 天，累计 12.0亿
```

“主线变化”区的分组标题改为：

- `周度持续状态—延续`；
- `周度持续状态—新主线`；
- `周度持续状态—退潮`。

日报的“主线”不能被解释成周报的“延续”，周报的“新主线”也不能改写成日报的
“主线”。

## 6. 职业资金日报降级披露

### 6.1 推送策略

`stock_flows`、`margin`、`core_etfs` 继续属于非阻断来源。只要现有价格、市场资金和
其他关键推送校验通过，单独的 `stock_flows` 失败不阻止职业资金日报落盘和推送。

本阶段不改变 `market_flow`、`sector_flows` 等关键来源的阻断规则。

### 6.2 强制披露

`2%候选` 是职业资金日报沿用的最高置信观察层标签：标的必须同时通过板块资金、个股
资金、趋势强度、板块领导者和无重大背离等门槛。名称中的“2%”不是 2% 收益率、
涨跌幅或资金流阈值，也不构成仓位或交易建议；本阶段不重命名这一既有报告枚举。

出现以下任一情况时，个股资金流覆盖状态为 `degraded`：

- `failures` 中存在 `source: stock_flows`；
- 快照缺少 `stock_flows` 字段；
- `stock_flows` 不是列表。

报告“数据质量”区必须出现：

```text
⚠️ 个股资金流不可用，2%候选、资金确认和核心股票资金流向列表不完整；
空列表不代表确认没有机会。
```

同时保留 provider 原始失败原因。不得把失败数据规范化为成功的空列表后丢失语义。

合法空列表且没有失败记录表示来源成功返回零条记录，状态为 `available_empty`，不显示
“不可用”警告；报告仍可显示“本次来源返回 0 条记录”以区分缺字段。

测试必须证明：个股资金流失败时报告仍被推送，而且推送正文包含上述降级语义。

## 7. 中国交易日历架构

### 7.1 依赖和市场

新增运行时依赖：

```text
exchange_calendars>=4.13.2,<5
```

默认使用库内置的 `XSHG` 日历。`XSHG` 是本项目中国股票市场交易日的统一代理；
本阶段不另外合并深交所日历，也不通过网络抓取节假日网页。

### 7.2 可注入接口

`src/lurker/trading_calendar.py` 提供：

```python
class CnTradingCalendarProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def provider_version(self) -> str: ...

    def sessions_in_range(self, start: date, end: date) -> tuple[date, ...]: ...
```

`provider_name` 和 `provider_version` 是实例级只读属性。默认适配器在实例构造后提供
实际库名与已导入版本；固定测试 provider 可以返回 fixture 中声明的值。

基于这一最小接口，由服务层实现：

- `is_trading_day(day)`；
- `previous_or_same_session(day)`；
- `resolve_daily_date(requested, today)`；
- `resolve_weekly_date(requested, today)`。

测试传入固定 provider，不访问真实库。业务层不得直接 import
`exchange_calendars`。

### 7.3 默认适配器

`ExchangeCalendarsCnProvider`：

- 延迟 import `exchange_calendars`；
- 通过 `get_calendar("XSHG")` 获取日历；
- 将 pandas 时间戳统一转换为无时区 ISO `date`；
- 输出严格递增、无重复的 session；
- provider import、日历创建、范围查询或 schema 转换失败时抛出明确的
  `TradingCalendarUnavailable`，不能回退到 `weekday() < 5`。

## 8. 交易日缓存

### 8.1 文件格式

默认缓存路径：

```text
data/cache/trading_calendars/xshg_sessions.json
```

格式：

```json
{
  "schema_version": 1,
  "calendar": "XSHG",
  "timezone": "Asia/Shanghai",
  "provider": "exchange_calendars",
  "provider_version": "4.13.2",
  "generated_at": "2026-07-28T10:00:00+08:00",
  "coverage_start": "2025-01-01",
  "coverage_end": "2025-01-03",
  "sessions": ["2025-01-02", "2025-01-03"]
}
```

`coverage_start` 到 `coverage_end` 表示已被 provider 完整判定的自然日范围；范围内没有
出现在 `sessions` 的日期才可以被判定为非交易日。仅有 session 列表但没有完整覆盖
边界的缓存无效。

### 8.2 读取、扩展和失败

1. 缓存 schema、市场、时区、覆盖边界和 session 顺序校验通过，且覆盖本次查询所需
   范围：直接使用缓存；不得仅为比较版本而初始化或调用 provider，provider 版本变化
   本身也不触发刷新；
2. 有效缓存覆盖不足时，调用当前 provider 重新查询“原有效覆盖范围与本次所需
   自然年”的完整并集，再以当前 provider 版本原子写回；如果此时版本与缓存不同，
   完整并集的重查同时完成版本升级，不能把不同版本的局部结果拼接后统一标成新版本；
3. 缓存损坏或缺失时，调用当前 provider 查询本次所需自然年并创建新缓存，不能合并
   无法验证的旧内容；
4. 第 2 或第 3 步中 provider 不可用时，抛出
   `TradingCalendarUnavailable`，不猜测工作日。

写入流程为同目录临时文件、flush、`fsync`、`os.replace`。写入失败不能破坏上一份有效
缓存。缓存内容不进入 Git。

### 8.3 查询范围

为避免每次单日查询产生碎片，默认适配器按自然年扩展：查询 2027 年任意日期时，至少
缓存 2027-01-01 至 2027-12-31。寻找上一交易日跨年时，服务层继续请求上一自然年，
直到找到 session 或 provider 明确失败；不能用固定回看天数后猜测。

## 9. 报告日期解析

所有日期以 `Asia/Shanghai` 当地日期为准。

### 9.1 通用规则

- 未传 `--date`：requested date 必须由
  `datetime.now(ZoneInfo("Asia/Shanghai")).date()` 获取；
- 每次命令入口只解析一次“上海当地今天”，并把这个 `date` 注入后续解析函数，避免
  运行跨越午夜时同一任务出现两个 today；
- 显式传入的日期晚于上海当地今天：抛出用户可读错误，且不写文件、不写数据库、
  不推送；
- 日历无法证明日期状态：失败关闭，且不产生部分报告。

### 9.2 日报

```text
requested date 是交易日 → effective date = requested date
requested date 非交易日 → 跳过
```

保持现有“不在非交易日自动补日报”的语义。返回消息应包含 requested date 和跳过原因。

### 9.3 周报

```text
requested date 是交易日 → effective date = requested date
requested date 非交易日 → effective date = 最近一个已确认 session
```

effective date 必须统一驱动：

- 资金快照读取上限；
- 报告对象的 `report_date`；
- 周报正文基准日期；
- 文件名 `weekly_<effective-date>.md`；
- 数据库 `Report.report_date`；
- 通知标题；
- CLI 成功消息。

当发生回退时，CLI 消息和报告数据质量区同时披露
`请求日期 <requested>，按最近交易日 <effective> 生成`。禁止一部分路径继续使用
requested date，造成快照、文件名、数据库或通知日期分裂。

同一 effective date 重复运行继续覆盖同名周报和同一数据库记录，保持现有幂等语义。

## 10. 组件改动边界

### 10.1 配置和 Legacy

- `configs/strategies.yaml`：lifecycle 与 limitations；
- `configs/scoring.yaml`：删除死权重、修正 `return_180d`；
- `src/lurker/application/strategy_runner.py`：严格加载、选择矩阵、结构化弃用元数据；
- `src/lurker/application/signal_scan.py`：删除个股占位输入；
- `src/lurker/application/sector_scan.py`：删除板块占位输入；
- `src/lurker/domain/signals.py` 及对应公共评分模块：新键与兼容边界；
- 报告组合层：统一插入弃用警告。

### 10.2 标签和降级

- `src/lurker/reports/professional_flow_report.py`：日报标签前缀；
- `src/lurker/application/weekly_flow_report.py`：周报标签前缀；
- `src/lurker/application/professional_flow_daily.py`：个股资金流覆盖状态与披露；
- `src/lurker/cli.py`：保留降级推送规则。

### 10.3 日历

- `pyproject.toml`：固定依赖范围；
- `requirements/ci-constraints.txt`：固定 CI 基线
  `exchange_calendars==4.13.2`；
- `src/lurker/trading_calendar.py`：provider、缓存和日期解析；
- `src/lurker/cli.py`：日报/周报统一使用日期解析结果；
- 使用 `is_cn_trading_day` 的回放和市场温度代码：通过兼容入口或依赖注入迁移，
  不再依赖年度常量；
- `.gitignore`：忽略运行时日历缓存。

## 11. 错误处理

- 配置错误：启动时报 `ValueError`，指出策略名和字段；
- deprecated 策略缺 limitations：拒绝加载；
- 旧评分键：明确指出 `return_120_180d` 已被
  `return_180d` 替代；
- 日历不可用：CLI 返回非零退出状态，不写报告、不推送；
- 未来日期：CLI 返回非零退出状态，并显示 requested date 与上海当地今天；
- 周报回退成功：不是错误，正常生成并披露 requested/effective date；
- 个股资金流失败：报告降级但不单独阻断推送。

## 12. 测试策略

所有行为变更测试先行。

### 12.1 策略和评分

- active/enabled、active/disabled、deprecated/disabled 的自动与显式选择矩阵；
- deprecated/enabled、空 limitations、未知 lifecycle、非 boolean enabled 拒绝；
- 单策略和多策略报告都显示完整弃用警告；
- active 策略不显示警告；
- 调用方 metrics 中不存在四个个股和三个板块占位键；
- 发行版评分配置不存在死权重和旧键；
- 权重键与指标键的七条映射逐项使用非默认权重验证；
- `return_180d` 自定义权重实际生效，旧键明确报错；
- 当前发行版个股、板块最高分分别保持 60、55；
- double-bagger 分类继续只使用一致的 180 日窗口。

### 12.2 标签和推送

- 日报四档标签都带 `当日资金状态`；
- 周报三档标签都带 `周度持续状态`；
- 分类边界与改动前完全一致；
- `stock_flows` 失败时仍推送且正文含“不完整、空列表不代表无机会”；
- 缺字段、错误类型、合法空列表三种状态可区分；
- 关键资金来源失败仍按原规则阻断。

### 12.3 日历

- 固定 provider 覆盖交易日、周末、法定休市日；
- 跨年寻找上一 session，不依赖 2026 常量；
- 缓存命中时不调用 provider；
- 缓存不足时按自然年扩展并原子替换；
- provider 版本变化但缓存覆盖充分时仍直接命中，不初始化 provider；
- provider 版本变化且缓存需要扩展时重查完整并集并升级版本；
- provider 不可用但旧缓存覆盖充分时仍可直接使用；
- 缓存损坏、provider 失败且覆盖不足时失败关闭；
- 日报非交易日跳过；
- 周报周末和节假日回退；
- 显式未来日期拒绝且零副作用；
- effective date 同时控制快照上限、正文、文件名、DB 日期、通知标题；
- 同一 effective date 重跑不产生重复 DB 记录。

真实 `XSHG` 适配器只做一组轻量集成测试；CI 的行为测试全部使用固定 provider 和临时
缓存，不依赖网络或当前日期。

生产依赖保留 `exchange_calendars>=4.13.2,<5`，CI 和标准验收环境通过
`requirements/ci-constraints.txt` 精确安装 `4.13.2`。仓库当前没有 CI workflow，
本阶段不新建 CI 服务；但所有文档化的 CI/验收安装命令都必须应用该 constraints 文件。
升级基线版本必须通过独立依赖升级提交，并重新运行真实适配器的名称、返回类型和日期
转换契约测试，不能由依赖解析器自动漂移。

标准验收命令必须先断言 `importlib.metadata.version("exchange-calendars") == "4.13.2"`，
再运行真实适配器契约测试；版本断言只属于受 constraints 控制的 CI/验收入口，不放进
可在生产依赖范围内任意 4.x 版本运行的普通单元测试。

## 13. 验收标准

阶段完成必须同时满足：

1. `long_term_trend` 自动运行路径不可达，显式运行可用且报告完整警告；
2. 发行版配置不再展示任何未接入评分维度，旧键已消失；
3. Legacy 评分结果和最高分没有因清理配置而被抬高；
4. 日报、周报所有板块标签都明确时间口径，分类公式未变；
5. 个股资金流失败时日报可以推送，但正文不能把空候选解释为确认无机会；
6. 仓库中不存在 `CN_MARKET_CLOSED_RANGES_2026` 或工作日猜测回退；
7. 周末、节假日和跨年周报都解析到最近已确认 session；
8. requested/effective date 在快照、报告、文件、数据库、通知中完全一致；
9. 未来日期和不可证明的交易日状态在任何写入或推送前失败；
10. 相关测试、全量测试和 lint 通过；
11. 标准验收环境确认安装 `exchange_calendars==4.13.2`，真实适配器契约测试通过；
12. 使用固定 fixture 完成一次日报降级演练和一次周报回退 `--no-push` 演练；
13. 实现说明、配置示例和 CLI 行为文档已同步更新。

## 14. 非目标

- 不重新设计职业资金日报或周报的分类公式；
- 不为 deprecated 策略接入新行情或基准数据；
- 不恢复 `long_term_trend` 的默认推送；
- 不建设远程交易日服务；
- 不自动生成未来多年节假日文件；
- 不对历史报告文件批量改名或重写；
- 不处理港股、美股交易日历。
