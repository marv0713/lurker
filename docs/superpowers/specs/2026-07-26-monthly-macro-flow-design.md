# 宏观流动性月报设计

日期：2026-07-26

## 1. 目标

新增独立策略 `monthly_macro_flow`，回答三个问题：

1. 居民存款搬家是否正在发生；
2. 活钱是否正在进入金融体系；
3. 股票市场杠杆是否进入危险区。

月报只描述已发布的宏观和市场资金事实，不预测指数点位。它与
`professional_flow_daily`、`weekly_macro_flow` 分开运行，拥有独立命令、原始快照、
报告目录和数据质量披露；通知沿用日报接收人。

## 2. 已确认决策

### 2.1 不使用代理变量替代直接事实

- 居民存款和非银行业金融机构存款只读取中国人民银行
  《金融机构人民币信贷收支表》的直接余额数据。
- `macro_china_money_supply` 只用于 M1、M2，不能代理非银存款。
- AkShare `macro_rmb_deposit` 的列名虽然包含“新增”，实测数值是存款余额，
  但它缺少非银存款且不是央行直接适配器，因此不作为本策略数据源。
- 非银存款缺失时该维度为 `unknown`，报告可以单独展示 M1-M2，但不能把它命名为
  非银存款或据此补齐非银存款得分。

### 2.2 流通市值使用交易所直接数据

融资余额占比的分母不用总市值估算，也不依赖当前无权限的 Tushare
`daily_basic.circ_mv`。分母来自指定交易日的交易所市场概况：

- 上交所：`stock_sse_deal_daily(date)` 的“主板A”与“科创板”流通市值之和；
- 深交所：`stock_szse_summary(date)` 的“主板A股”与“创业板A股”流通市值之和。

排除 B 股、基金、债券和其他证券类别。上交所数据以亿元计，深交所数据以元计，
规范化后统一为元。

### 2.3 严格缺失语义

任何缺失、过期、月份错位、日期错位、单位不明、非有限数或 schema 变化都产生
`unknown`，不能转成零、负面证据或近似值。

## 3. 应用边界

```text
央行年度信贷收支表 ─┐
AkShare M1/M2       ├─> 月度宏观事实 ─┐
沪深融资余额历史    ┤                  ├─> 月度状态 ─> Markdown ─> 日报接收人
沪深交易所市场概况 ─┘                  │
上一期规范化快照 ─────────────────────┘
```

组件边界：

- `src/lurker/ingest/pboc_deposits.py`
  只负责下载、校验和规范化央行存款余额表。
- `src/lurker/ingest/macro_monthly.py`
  负责 M1/M2、融资余额、流通市值、月份与日期对齐，并生成规范化事实。
- `src/lurker/application/monthly_macro_flow.py`
  只接受规范化事实，计算四个维度和综合状态，不访问网络或文件。
- `src/lurker/reports/monthly_macro_flow_report.py`
  只渲染结论、指标、截止日期、来源和数据质量。
- `src/lurker/cli.py`
  负责参数、快照、策略注册、报告落盘和可选通知。

## 4. 配置

新增 `configs/macro_monthly.yaml`：

```yaml
schema_version: 1

pboc:
  credit_table_urls:
    "2024": "https://www.pbc.gov.cn/eportal/fileDir/diaochatongjisi/resource/cms/2025/01/2025011417071510290.htm"
    "2025": "https://www.pbc.gov.cn/eportal/fileDir/diaochatongjisi/resource/cms/2025/02/2025021418100389332.htm"
  allowed_hosts:
    - www.pbc.gov.cn
  timeout_seconds: 30
  max_response_bytes: 10000000

thresholds:
  household_deposit_yoy_pct: 12.0
  leverage_ratio_pct: 4.0
  financing_monthly_growth_pct: 20.0

freshness:
  macro_max_lag_months: 2
  leverage_max_lag_trading_days: 3
```

`credit_table_urls` 至少覆盖目标月份所在年份和上一年，因为居民存款同比需要
同月上年数据。URL 不是秘密，可进入版本库，但每年必须显式更新，避免自动抓取
央行网站导航结构后误选其他统计表。

上例两个 URL 已在 2026-07-26 实际解析验证，分别包含 2024 全年数据和
2025 年已发布月份。生产配置必须把目标年的 URL 更新为央行最新发布版本；解析器
不会根据示例年份推断新 URL。

配置校验要求：

- 只接受 `https`；
- 主机必须精确出现在 `allowed_hosts`；
- 年份键必须是四位数字；
- 超时和最大响应大小必须为正整数；
- 三个阈值必须是有限非负数；
- 宏观滞后月份和交易日滞后必须为非负整数；
- 未知字段启动时报错。

## 5. 央行存款数据契约

### 5.1 原始来源

只接受中国人民银行发布的《金融机构人民币信贷收支表》，单位必须能识别为
“亿元”或 `100 Million Yuan`。支持 HTML、XLS 和 XLSX，不解析 PDF。

每次下载保存：

- 请求 URL；
- 获取时间；
- HTTP 状态；
- 内容类型；
- SHA-256；
- 原始字节缓存路径。

原始字节写入 `data/raw/pboc_credit_tables/<year>-<sha256>.<ext>`，同一内容不重复
写入。规范化快照保留 URL 和 SHA-256，保证报告可以追溯到原始表。

### 5.2 精确行

中文空白和序号规范化后，只接受以下两行：

```text
住户存款 / Deposits of Households
非银行业金融机构存款 / Deposits of Non-banking Financial Institutions
```

不能使用“新增储蓄存款”“其他存款”“非银贷款”或包含相似词的其他行代替。
同一目标行出现零次或多次都视为 schema 错误。

### 5.3 月份与数值

列标题必须能规范化为 `YYYY-MM`。空单元格表示该月尚未发布，不参与计算。
余额必须是有限正数，统一转换为亿元。

两个年度表合并后：

- 同一指标、同一月份只有一个值时接受；
- 重复值完全相同则去重；
- 重复值不一致表示历史修订冲突，本次运行将该月份标为 `unknown` 并披露冲突，
  不静默选择新值或旧值。

## 6. M1-M2 数据契约

使用 AkShare `macro_china_money_supply()`，要求以下列存在：

```text
月份
货币和准货币(M2)-同比增长
货币(M1)-同比增长
```

月份规范化为 `YYYY-MM`，同比增速统一为百分点。定义：

```text
spread_pp = m1_yoy_pct - m2_yoy_pct
spread_delta_pp = current_spread_pp - previous_spread_pp
```

- `spread_delta_pp > 0`：剪刀差改善；
- `spread_delta_pp == 0`：持平；
- `spread_delta_pp < 0`：剪刀差恶化。

M1-M2 只描述活钱的宏观背景，与非银存款维度并列，不互相替代。

## 7. 宏观月份对齐

目标宏观月份是央行存款表与 M1/M2 共同拥有的最新月份，并且不得晚于报告月份。

要求：

- 目标月份距离报告月份不超过 `macro_max_lag_months`；
- 居民存款需要目标月份、上月、上年同月和上年上月，才能同时计算本月同比及
  上月同比；
- 非银存款需要目标月份和上月；
- M1-M2 需要目标月份和上月；
- 不能用不同月份的三个指标拼成同一个结论。

如果没有共同月份，或者目标月份过期，则三个宏观维度均为 `unknown`。报告仍可
展示各数据源各自的最新月份，但不能形成趋势结论。

## 8. 居民存款与非银存款

### 8.1 居民存款

```text
household_yoy_pct =
    (household_balance_t / household_balance_t_minus_12 - 1) * 100
```

- `< 12%`：`relocation_signal`，居民存款增速低于枯荣线；
- `>= 12%`：`deposit_dominant`；
- 恰好 12% 不算低于枯荣线。

同时计算本月同比相对上月同比的变化，仅用于报告解释，不改变 12% 主分类。

### 8.2 非银存款

```text
nonbank_mom_amount =
    nonbank_balance_t - nonbank_balance_t_minus_1

nonbank_mom_pct =
    (nonbank_balance_t / nonbank_balance_t_minus_1 - 1) * 100
```

- `nonbank_mom_amount > 0`：`rising`；
- `== 0`：`flat`；
- `< 0`：`falling`。

原始余额为零、负数或非有限数时返回 `unknown`。月环比容易受季末影响，因此报告
必须同时展示最近两个月余额和变化额，不能只显示方向标签。

## 9. 杠杆水位

### 9.1 截止日

融资余额通常晚于行情一个交易日发布。杠杆截止日取不晚于报告日、且沪深融资历史
都存在的最新共同交易日。

该日期距离报告日不得超过 `leverage_max_lag_trading_days` 个已确认中国交易日。
超过则杠杆维度为 `unknown`。

### 9.2 融资余额

只使用融资余额，不把融券余额并入分子：

```text
financing_balance =
    shanghai_financing_balance + shenzhen_financing_balance
```

两个市场必须是同一日期。任一市场缺失、非有限或为负，整体为 `unknown`。

### 9.3 A 股流通市值

在同一杠杆截止日请求：

```text
sse_a_circ_mv =
    SSE["主板A"]["流通市值"] + SSE["科创板"]["流通市值"]

szse_a_circ_mv =
    SZSE["主板A股"]["流通市值"] + SZSE["创业板A股"]["流通市值"]

a_share_circ_mv = sse_a_circ_mv + szse_a_circ_mv
```

交易所返回表必须包含所有精确类别。不能退回“股票”合计，因为其中可能包含 B 股。
不能退回总市值或用成交额估算。

### 9.4 比率与月增速

```text
leverage_ratio_pct =
    financing_balance / a_share_circ_mv * 100

financing_monthly_growth_pct =
    (financing_balance_current /
     financing_balance_previous_month_end - 1) * 100
```

上月基准取上一个自然月内沪深融资余额都存在的最后共同交易日。两期分子口径一致。

- `leverage_ratio_pct > 4%`：过热；
- `financing_monthly_growth_pct > 20%`：过热；
- 恰好 4% 或 20% 不触发过热；
- 任一已获得的指标触发红线即为 `overheated`，即使另一项缺失也不能压掉已确认的
  风险证据；
- 两项都有效且未触发时为 `healthy`；
- 只有一项有效且未触发时可以披露该事实，但杠杆总状态为 `unknown`，避免漏判
  另一条红线。

## 10. 综合状态

输出层分成 `report_mode` 和 `market_state`：

```text
report_mode = classified | data_observation
market_state = 牛市加速 | 慢牛蓄力 | 震荡磨底 | 过热警报 | null
```

优先级：

1. 杠杆状态为 `overheated` 时，无论宏观数据是否齐全，都输出 `过热警报`，
   且 `report_mode=classified`。
2. 非过热结论要求居民存款、非银存款、M1-M2 和杠杆健康四个维度全部有效。
3. 任一必要维度为 `unknown` 时，`report_mode=data_observation`、
   `market_state=null`，只展示事实，不声称趋势改善或恶化。
4. 所有必要维度有效且杠杆健康时，对三个宏观正向信号计数：
   - 居民存款同比 `< 12%`；
   - 非银存款环比上升；
   - M1-M2 剪刀差较上月改善。
5. 三项全正向：`牛市加速`。
6. 两项正向：`慢牛蓄力`。
7. 零或一项正向：`震荡磨底`。

缺失项不计零分，也不能让“慢牛蓄力”更容易触发。

## 11. 快照与幂等

规范化事实保存到：

```text
data/processed/monthly_macro_flow_snapshots/YYYY-MM.json
```

同一报告月份重复运行采用临时文件加原子替换，覆盖同月快照，不追加。快照包含：

- schema version；
- 报告月份和生成时间；
- 宏观共同月份；
- 杠杆共同交易日；
- 四个维度的原始值、状态、来源和可用性；
- 每个外部响应的 URL、数据日期和 SHA-256；
- 所有失败和降级原因。

历史修订可以使同月重跑结果变化。报告必须显示生成时间和源哈希，旧快照由外部备份
或版本化存储负责，本策略不自行维护无限版本。

## 12. 报告

报告路径：

```text
data/reports/monthly_macro_flow/YYYY-MM.md
```

固定结构：

```text
# 宏观流动性月报

报告月份：YYYY-MM
宏观数据截止月：YYYY-MM 或 unknown
杠杆数据截止日：YYYY-MM-DD 或 unknown

## 一句话结论
## 牛市进度条
## 居民存款趋势
## 非银存款
## M1-M2 活钱指标
## 杠杆水位
## 数据质量
```

数据观察模式必须在标题下方明确写：

```text
数据不足，仅展示观察事实，不形成趋势结论。
```

每个章节必须列出数值、前值、变化、截止日期、来源和状态。不能只渲染综合标签。

## 13. CLI 与通知

新增命令：

```bash
python -m lurker.cli monthly-macro-flow \
  --month 2026-06 \
  --config configs/macro_monthly.yaml \
  --no-push
```

规则：

- 未传 `--month` 时，以当前日期所在月份作为报告月份；
- 显式未来月份拒绝运行；
- 允许月中演练，但自动选择已发布的最新共同宏观月份；
- 默认推送到日报接收人；
- `--no-push` 仍采集、保存快照和报告，但绝不构建通知器；
- 所有关键数据缺失时仍生成数据观察报告，但不推送；
- 部分数据缺失且没有形成市场状态时不推送；
- 得到四档市场状态之一时才允许推送。

策略注册为：

```yaml
monthly_macro_flow:
  enabled: true
  cadence: monthly
  universe: macro
  title: 宏观流动性月报
  params: {}
```

## 14. 错误处理

可降级为 `unknown` 的外部错误：

- 网络超时和连接失败；
- HTTP 非成功状态；
- 央行内容类型、大小、单位或 schema 不符合契约；
- AkShare 上游返回空表或缺列；
- 目标月份或交易日没有共同数据。

必须向上抛出的程序错误：

- 配置字段或阈值非法；
- 依赖注入函数签名错误；
- 内部类型错误；
- JSON 序列化失败；
- 原子写入失败；
- 报告渲染代码异常。

不能用宽泛 `except Exception` 把程序错误伪装成数据缺失。

## 15. 测试与验收

CI 全部使用固定 fixture，不访问实时网络。

### 15.1 央行表

- HTML、XLS/XLSX 正常解析；
- 精确识别居民和非银两行；
- 单位、重复行、冲突修订、空月份和非有限值失败关闭；
- URL host、协议、响应大小和 SHA-256 可审计；
- 目标月和上年同月跨年度合并正确。

### 15.2 月度计算

- 居民存款跨年 14 个月数据和 12% 边界；
- 非银存款正、零、负变化；
- M1-M2 改善、持平、恶化；
- 4% 和 20% 杠杆边界；
- 分母为零和沪深日期错位；
- 上交所、深交所单位换算和 A 股类别排除；
- 宏观共同月份和发布日期滞后。

### 15.3 综合状态

固定样本覆盖：

- `牛市加速`；
- `慢牛蓄力`；
- `震荡磨底`；
- 比率过热；
- 月增速过热；
- 数据观察模式；
- 杠杆过热优先于数据缺失。

### 15.4 应用与报告

- 同月重跑覆盖且原子写入；
- 报告披露两个不同截止日期；
- 缺失原因出现在数据质量章节；
- 数据观察模式不输出趋势结论；
- `--no-push` 不构建通知器；
- 无市场状态时不推送；
- 正常月报使用日报接收人。

### 15.5 真实数据验收

人工验收必须完成：

1. 央行官方表最新共同月份能解析两行余额；
2. 居民余额与央行表逐项核对；
3. 非银余额与央行表逐项核对；
4. M1/M2 最新月份与公开值一致；
5. 沪深融资余额具有同一截止日；
6. 沪深交易所 A 股流通市值具有同一截止日；
7. 杠杆比率手工复算一致；
8. 报告完整披露来源、月份、日期和降级项。

真实源未满足时保持 `data_observation` 或不推送，不允许用代理数据绕过验收。

## 16. 非目标

- 不预测指数或牛市持续时间；
- 不把 M1-M2 当作非银存款；
- 不自动猜测央行年度统计页 URL；
- 不解析 PDF；
- 不用总市值、成交额或固定常量估算流通市值；
- 不在日报中重复计算月度指标；
- 不回填无限历史；首版只要求支持配置覆盖范围内的月份。
