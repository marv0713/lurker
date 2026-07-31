# 月报市场分析增强设计

## 目标

把现有“宏观流动性数据体检表”升级为可解释的市场月报。月报继续保留居民存款、非银存款、M1-M2 和杠杆水位的原始事实，同时使用截至报告日最近五个交易日的结构化资金快照进行日、周、月交叉验证，输出明确但克制的市场立场、市场阶段、证据链和下月触发条件。

本功能只使用确定性规则，不调用 AI，不解析日报或周报 Markdown，不把宏观滞后数据直接解释成买卖预测。

## 回答的问题

增强后的月报回答五个问题：

1. 当前市场立场是进攻准备、观察还是防守？
2. 当前属于增量确认、存量结构、减量防守、杠杆过热还是数据不足？
3. 月度背景、周度资金和最新交易日信号是否同向？
4. 当前主要矛盾是什么，行情是全面还是结构性？
5. 哪些可验证条件会使结论转强、转弱或失效？

## 输入与边界

### 月度输入

继续使用现有月度快照和 `analyze_monthly_macro_flow()` 输出：

- 居民存款状态；
- 非银存款状态；
- M1-M2 剪刀差状态；
- 杠杆水位状态；
- `report_mode` 与 `market_state`；
- 数据截止日期、来源和失败原因。

月度快照 schema 不变。

### 市场输入

从 `data/processed/flow_snapshots/` 读取不晚于报告基准日的最近五个中国交易日 JSON 快照。报告基准日固定为报告月最后一个中国交易日，复用 CLI 现有的 `_last_cn_trading_day()` 和同一份交易日历；即使在次月补跑或重跑，也不把次月快照混入上月报告。只使用结构化字段：

- `market_flow.main_net_inflow`；
- `market_flow.super_large_net_inflow`；
- `core_etfs`；
- `margin`；
- `sector_flows`；
- `failures`。

不读取或解析 `data/reports/*.md`。快照按现有交易日历过滤，忽略 `latest.json` 和文件名不符合 `YYYY-MM-DD.json` 的文件。

### 数据可用性

- 有至少 3 个有效交易日快照：市场上下文 `available`，允许形成市场阶段。
- 有 1–2 个有效交易日快照：市场上下文 `partial`，展示事实，但市场立场固定为“观察”、阶段为“数据不足”。
- 没有有效快照：市场上下文 `unavailable`，月度宏观章节继续生成；市场分析明确说明无法交叉验证。
- 单个快照损坏或包含失败记录时保留其他有效快照，并在数据质量中披露。

## 结构化周度上下文

在 `application/weekly_flow_report.py` 中提取公开的结构化汇总边界，使周报和月报共用同一套最近五日计算，不复制规则。新增不可变数据对象 `WeeklyFlowSummary`，至少包含：

```text
availability
start_date
end_date
snapshot_count
temperature_counts: 进攻 / 观察 / 防守
main_net_inflow_sum
super_large_net_inflow_sum
latest_etf_status
latest_margin_signal
continued_sectors
new_sectors
ebb_sectors
failure_count
quality_notes
```

`build_weekly_flow_report()` 改为先生成 `WeeklyFlowSummary` 再渲染，现有周报输出保持兼容。月报直接消费该对象，不消费周报文本。

ETF 和两融最新状态必须复用 `prepare_temperature_inputs()` 的时效判定，过期或缓存数据不得提供正负证据。

## 市场阶段真值表

先定义中间条件：

```text
macro_supportive = market_state in {牛市加速, 慢牛蓄力}
weekly_positive = 周度主力净流入 > 0 且周度超大单净流入 > 0
weekly_negative = 周度主力净流入 < 0 且周度超大单净流入 < 0
temperature_positive = 进攻天数 > 防守天数
temperature_negative = 防守天数 > 进攻天数
etf_direction = supportive（active）/ weakening（inactive）/ unknown
margin_direction = supportive / weakening / unknown
latest_direction = supportive / weakening / mixed / unknown
```

`latest_direction` 按以下规则合成：

- 至少一个来源为 `supportive`，且没有来源为 `weakening`：`supportive`；
- 至少一个来源为 `weakening`，且没有来源为 `supportive`：`weakening`；
- 同时出现 `supportive` 和 `weakening`：`mixed`；
- 两个来源都没有有效方向：`unknown`。

因此 ETF 与两融冲突时，最新层视为分化，不给任何方向确认，也不同时写入支持证据和制约证据。原始状态仍在日度视图中分别披露。真值表中的 `latest_supportive` 表示 `latest_direction == supportive`，`latest_weakening` 表示 `latest_direction == weakening`。

按以下优先级分类：

| 优先级 | 条件 | 市场立场 | 市场阶段 |
| --- | --- | --- | --- |
| 1 | 月度杠杆状态为 `overheated` | 防守 | 杠杆过热 |
| 2 | 月度 `report_mode != classified` 或周度上下文不是 `available` | 观察 | 数据不足 |
| 3 | `macro_supportive and weekly_positive and temperature_positive and latest_supportive` | 进攻准备 | 增量确认 |
| 4 | `weekly_negative and temperature_negative and latest_weakening` | 防守 | 减量防守 |
| 5 | 其余数据完整场景 | 观察 | 存量结构 |

只有月、周、日三层支持证据同时成立，才允许输出“增量确认”。方向冲突、相等、零值或未知状态一律落入“存量结构/观察”，不强行给方向。

防守规则有意不增加 `macro_supportive` 的反向约束。月度宏观数据频率低且存在滞后；当周度累计、市场温度和最新有效信号同时转弱时，高频资金证据允许把战术立场切换为“防守/减量防守”。如果宏观背景仍为支持，报告必须明确写为“宏观支持仍在，但周、日资金已经转弱”，表示短期防守，不得写成月度宏观逻辑已经反转。

## 确定性分析文案

新增纯函数 `analyze_monthly_market()`，接收月度分析和 `WeeklyFlowSummary`，返回结构化结果，不直接拼 Markdown：

```text
stance
market_stage
core_reasons
supporting_evidence
constraining_evidence
main_contradiction
monthly_view
weekly_view
daily_view
continued_sectors
new_sectors
ebb_sectors
strengthening_conditions
weakening_conditions
invalidation_condition
quality_notes
```

所有文案从有限模板生成，每条必须绑定已存在的状态或数值。

### 核心理由

固定最多三条，依次回答：

1. 月度背景：存款搬家、非银、M1-M2 和杠杆是否支持增量资金；
2. 周度确认：主力/超大单累计方向及进攻、观察、防守天数；
3. 最新确认：ETF 和两融是否给出当日有效证据。

### 证据链

支持证据与制约证据分别列出，不能把“没有过热”写成“资金正在流入”：

- 居民存款 `relocation_signal`：支持场外资金松动；
- 非银 `rising`：支持机构资金承接；`falling`：制约；
- M1-M2 `improving`：支持资金活化；`worsening`：制约；
- 杠杆 `healthy`：只表示未触发风险红线，属于中性背景；
- 杠杆 `overheated`：明确风险证据；
- 周度主力和超大单同为正/负：对应支持/制约；
- `latest_direction == supportive`：最新支持证据；
- `latest_direction == weakening`：最新制约证据；
- `latest_direction == mixed`：只说明 ETF 与两融分化，不进入支持或制约证据；
- `latest_direction == unknown`：不进入任何正负证据。

主矛盾由规则模板生成。例如月度出现存款搬家、但非银和 M1-M2 仍弱时：

```text
场外资金已经松动，但机构承接和资金活化尚未确认。
```

## 报告结构

现有原始数据章节全部保留，在“一句话结论”之后新增：

```text
## 本月市场判断
- 市场立场：观察
- 市场阶段：存量结构
- 理由一：...
- 理由二：...
- 理由三：...

## 资金证据链
### 支持证据
### 制约证据
### 当前主要矛盾

## 日周月交叉验证
- 月度：...
- 周度：...
- 日度：...

## 当前市场结构
- 持续主线：...
- 新主线：...
- 退潮方向：...
- 结构判断：全面行情 / 结构行情 / 暂不可判断

## 下月观察条件
### 转强条件
### 转弱条件
### 当前结论失效条件
```

“一句话结论”同步升级，不再只输出 `本月状态：震荡磨底`。模板为：

```text
本月立场：{stance}；市场处于{market_stage}。{main_contradiction}
```

宏观数据章节继续位于分析章节之后，确保读者可以追溯判断依据。

## 当前 2026-07 预期输出

按 2026-07-31 VPS 已有数据：

- 月度：居民存款出现搬家信号，非银存款减少，M1-M2 恶化，杠杆未过热；
- 周度：进攻 0 天、观察 3 天、防守 2 天；
- 周度累计：主力约 -462.7 亿元、超大单约 +146.9 亿元，一负一正，因此 `weekly_negative == False`；
- 日度：大盘主力与超大单当日流入，但 ETF 非当日、两融回落；
- 结构：存在持续资金主线，但三层信号没有形成增量共振。

确定性结果应为：

```text
市场立场：观察
市场阶段：存量结构
当前主要矛盾：场外资金已经松动，但机构承接和资金活化尚未确认。
```

不得输出“牛市加速”“全面进攻”或确定性涨跌预测。

对应回归测试必须同时固定“周度主力为负、超大单为正”这一前置条件，不能只用温度天数推导预期结果。

## CLI 与部署

`monthly-macro-flow` 新增可选参数：

```text
--flow-snapshot-dir data/processed/flow_snapshots
```

默认值与 VPS 现有目录一致，因此现有 cron 命令无需修改。`monthly_macro_flow_job()` 先用 `_last_cn_trading_day(report_month, calendar)` 得到报告基准日，再以该日期构建最近五日汇总，然后分析和渲染。`--month-end-only` 与汇总窗口必须复用同一个交易日历口径。

同月重跑继续原子覆盖：

```text
data/processed/monthly_macro_flow_snapshots/YYYY-MM.json
data/reports/monthly_macro_flow/YYYY-MM.md
```

市场分析结果只进入报告，不写入现有月度快照，避免 schema 漂移。通知仍使用现有月报接收人；重跑成功后覆盖并重新推送同月报告。推送决策保持兼容，继续只由原月度宏观分析控制：`analysis["market_state"] is None`（即 `report_mode == "data_observation"`）时不推送。增强层的“观察”或“数据不足”不新增推送拦截；只要原宏观分析已经形成分类，报告仍可推送，并在正文披露市场上下文不足。

## 降级与错误处理

- 月度必要数据不足：保留现有 `report_mode == "data_observation"` 的判定逻辑，市场阶段为“数据不足”，不形成方向性立场。
- 资金快照不足：宏观报告照常生成；交叉验证章节说明可用快照数，不形成市场方向。
- 最新 ETF 或两融过期：显示“暂不判断”，不提供正负证据。
- 部分快照损坏：保留其他快照，列出失败文件和原因。
- 结构性板块列表为空：显示“暂无确认”，不能解释为全面退潮。
- 报告生成成功但通知失败：文件保留，CLI 明确返回通知失败信息，沿用现有通知行为。

## 测试

### 周度结构化汇总

- 最近五个交易日选择、日期边界和损坏快照降级；
- 进攻/观察/防守计数；
- 主力和超大单周度累计；
- 最新 ETF/两融时效；
- 持续、新增、退潮板块；
- 现有周报 Markdown 输出不回归。

### 市场阶段真值表

- 杠杆过热优先；
- 数据不足强制观察；
- 三层同向得到“进攻准备/增量确认”；
- 三层负向得到“防守/减量防守”；
- 宏观支持但三层高频负向仍得到“防守/减量防守”，且生成“宏观仍支持、高频已转弱”的说明；
- 信号冲突得到“观察/存量结构”；
- ETF 与两融相反得到 `mixed`，不触发最新层正负确认；
- `unknown` 不产生正负证据。

### 报告与 CLI

- 五个新增章节及升级后的一句话结论；
- 2026-07 在周度主力为负、超大单为正的前置条件下得到“观察/存量结构”；
- 缺少周度快照仍生成宏观报告；
- 新 CLI 参数默认值和显式路径；
- 报告月末交易日作为五日窗口上界，次月补跑不混入次月快照；
- `--month-end-only`、原子覆盖和通知行为保持不变；
- 原宏观 `data_observation` 继续不推送；增强层“数据不足”不新增推送拦截；
- 完整 pytest 与 Ruff。
