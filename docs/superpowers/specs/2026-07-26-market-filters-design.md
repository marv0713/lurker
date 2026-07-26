# 市场过滤器设计

日期：2026-07-26

## 1. 目标

补齐并收口 `configs/markets.yaml` 中的市场过滤能力，使每个启用字段满足以下二选一：

1. 存在可测试、可追溯的执行路径；
2. 配置加载时明确拒绝尚未支持的能力。

本阶段覆盖：

- A 股 ST 名称过滤；
- 北交所代码过滤；
- A 股、美股、港股最低平均成交额；
- 港股最低价格；
- 美股最低市值；
- 缺失数据策略；
- 过滤决策、来源、截止日期和报告摘要。

本阶段不实现壳股识别、频繁资本运作识别或完整证券主数据仓库。

## 2. 已确认方案

采用两阶段、可审计的统一过滤引擎：

```text
主题/指数/ETF 种子
        │
        ▼
确定性预过滤
├─ 北交所代码
└─ A 股 ST 名称
        │
        ▼
价格与独立元数据采集
├─ 价格、成交量、成交额
└─ 美股 quote metadata
        │
        ▼
纯函数量化过滤
├─ 港股最新价格
├─ 各市场 20 日平均成交额
└─ 美股市值与新鲜度
        │
        ├─ included → 价格快照与评分
        └─ excluded → filter_decisions
```

不采用以下方案：

- 不继续在 `resolved_seed_pool.py` 和 `price_snapshot.py` 中堆叠静默 `if`；
- 不先建设完整元数据仓库、历史市值库或公司行动系统。

## 3. 组件边界

### 3.1 `src/lurker/config.py`

负责严格加载和规范化市场配置，生成不可变的类型对象及稳定过滤配置哈希。

### 3.2 `src/lurker/universe/market_filters.py`

只包含无 I/O 的过滤规则、指标规范化和 `FilterDecision` 构建。它不能读取 YAML、
访问网络、读写文件或发送通知。

### 3.3 `src/lurker/ingest/equity_metadata.py`

负责采集、校验和规范化美股 quote metadata。首版使用 yfinance
`Ticker.get_info()`，但应用层只依赖规范化类型，不依赖 provider 字段。

### 3.4 `src/lurker/ingest/prices.py`

继续负责价格行情适配，并补齐规范化成交额和来源元数据。不得在这里做阈值判断。

### 3.5 `src/lurker/universe/resolved_seed_pool.py`

调用纯过滤器执行北交所和 ST 预过滤，维护来源归属、主题映射、名称映射和预过滤决定。

### 3.6 `src/lurker/application/price_snapshot.py`

编排价格、元数据和量化过滤，保存被纳入的价格快照、所有过滤决定、失败和摘要。

### 3.7 报告与 CLI

CLI 输出过滤计数；日报和数据快照展示简短摘要。逐标的完整证据保留在 JSON 快照。

## 4. 严格配置

`configs/markets.yaml` 改为：

```yaml
schema_version: 1

filter_policy:
  missing_data_policy: exclude
  turnover_window_trading_days: 20
  min_turnover_observations: 15
  us_market_cap_max_age_days: 7

markets:
  cn:
    name: A 股
    role: primary_discovery
    universe_sources:
      - 沪深 300
      - 中证 1000
      - 科创 50
      - 创业板核心指数
      - 重点行业 ETF 成分股
    filters:
      exclude_st: true
      exclude_beijing_exchange: true
      min_avg_turnover_cny: 50000000

  us:
    name: 美股
    role: global_anchor
    universe_sources:
      - 主题字典核心龙头
      - 行业 ETF
      - 主题 ETF
    filters:
      min_market_cap_usd: 2000000000
      min_avg_turnover_usd: 10000000

  hk:
    name: 港股
    role: mapping_supplement
    universe_sources:
      - 主题字典核心映射股
      - A/H 映射股
      - 中概和创新药核心公司
    filters:
      min_price_hkd: 1.0
      min_avg_turnover_hkd: 20000000
      exclude_shell_like: false
      exclude_frequent_capital_actions: false
```

### 4.1 类型

配置加载后形成：

- `MarketFilterPolicy`；
- `CnMarketFilters`；
- `UsMarketFilters`；
- `HkMarketFilters`；
- `MarketProfile`；
- `MarketsConfig`。

调用方不再索引任意嵌套字典。

### 4.2 校验

- 只接受 `schema_version: 1`；
- 未知顶层、市场、profile 或过滤字段报错；
- 市场只接受 `cn`、`us`、`hk`，三个市场都必须存在；
- 布尔字段只接受真正的 YAML boolean；
- 数值阈值必须是有限正数，不能是 boolean；
- 窗口、样本数和最大年龄必须是正整数；
- `min_turnover_observations <= turnover_window_trading_days`；
- `missing_data_policy` 只接受 `exclude`、`include_with_warning`；
- 过滤字段不能放到错误市场；
- `exclude_shell_like` 或 `exclude_frequent_capital_actions` 为 `true` 时明确报
  “unsupported market filter”；
- 两个未支持字段为 `false` 时允许保留，表示用户明确关闭。

### 4.3 配置哈希

`filter_config_hash` 是以下规范化内容的 canonical JSON SHA-256：

- schema version；
- `filter_policy`；
- 三个市场的 `filters`。

展示名称、角色和 `universe_sources` 不影响过滤结论，因此不进入过滤哈希。

## 5. 统一过滤决策

每个被评估标的生成一条 `FilterDecision`：

```json
{
  "symbol": "NVDA",
  "market": "us",
  "stage": "quantitative",
  "status": "excluded",
  "reason_codes": ["market_cap_below_minimum"],
  "metrics": {
    "market_cap_usd": 1500000000,
    "avg_turnover_usd": 25000000,
    "turnover_observations": 20
  },
  "thresholds": {
    "min_market_cap_usd": 2000000000,
    "min_avg_turnover_usd": 10000000
  },
  "sources": [
    {
      "source": "yfinance.quote_metadata",
      "data_date": "2026-07-24",
      "retrieved_at": "2026-07-26T12:00:00+00:00",
      "sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      "hash_scope": "normalized_metadata"
    }
  ]
}
```

字段契约：

- `stage`：`seed_prefilter` 或 `quantitative`；
- `status`：`included`、`excluded`、`included_with_warning`；
- `reason_codes`：稳定的机器可读代码，按字典序输出；
- `metrics`：本次实际得到的规范化指标，缺失使用 `null`；
- `thresholds`：只保留该标的实际启用的过滤阈值；
- `sources`：来源、数据日期、获取时间、哈希和哈希范围。

不使用自由文本作为唯一判断依据。报告可以把 reason code 映射为中文，但 JSON 中
必须保留稳定代码。

## 6. 缺失策略

默认：

```text
missing_data_policy = exclude
```

该策略只控制“已有核心价格数据，但某个启用的过滤辅助指标缺失”的情况，例如：

- ST 名称缺失；
- 成交额列或有效样本不足；
- 美股市值缺失、过期或 schema 不合法。

规则：

- `exclude`：状态为 `excluded`；
- `include_with_warning`：已知指标全部通过时，状态为
  `included_with_warning`；
- 任一已知指标明确低于阈值时始终 `excluded`；
- 已知 ST 或北交所标的始终 `excluded`；
- 核心价格序列为空或不可用时无法形成快照，始终排除并记为采集失败，不受缺失策略
  影响。

## 7. 种子池预过滤

### 7.1 北交所

当 `exclude_beijing_exchange=true` 时，规范化后以 `.BJ` 结尾的 symbol 排除。
比较不区分大小写。不能只过滤指数成分；手工种子和 ETF 成分同样执行。

### 7.2 ST

当 `exclude_st=true` 时，名称执行 Unicode 空白清理和大小写规范化，再识别：

```text
ST
*ST
SST
S*ST
```

只判断名称前缀，不用 symbol 代码、价格或其他启发式规则猜测 ST。

名称解析器必须返回：

- 已解析名称；
- 来源；
- 获取时间；
- 缺失 symbol；
- provider 失败。

不能像当前实现一样捕获异常后返回空字典并静默放行。

### 7.3 一致性

排除 symbol 后必须同步更新：

- `markets.<market>.symbols`；
- `sources.manual`；
- `sources.indexes`；
- `sources.etfs`；
- `theme_mapping`。

`symbol_names` 可以保留所有成功解析名称用于审计，但只有纳入标的进入下游价格采集。

种子池保存：

- `schema_version: 2`；
- `filter_config_hash`；
- `filter_decisions`；
- `filter_summary`。

价格快照编排接收同一 seed pool 的预过滤决定，并把它们复制到最终
`filter_decisions`。因此被预过滤的 symbol 即使没有进入价格请求，也仍能在本次价格
快照中追溯。

## 8. 成交额契约

### 8.1 窗口

默认取不晚于 `snapshot_date` 的最近 20 个不同交易日，要求至少 15 个有效观测。

- 输入先按交易日期升序；
- 晚于 `snapshot_date` 的行丢弃；
- 重复交易日期是 schema 错误，不静默去重；
- 交易日期、价格、成交量或成交额中的非有限值不构成有效观测；
- 合法的零成交额计入窗口，体现停牌或无成交；
- 负成交额非法；
- 平均值是选中窗口的算术平均。

整个 `period` 的长度不能改变平均成交额口径。

### 8.2 A 股

A 股优先使用 provider 直接提供的日成交额：

- AkShare `成交额`：人民币元；
- Tushare `amount`：千元，乘 `1000` 转人民币元；
- Baostock `amount`：人民币元。

规范化价格 frame 增加 `turnover` 列，单位为人民币元。A 股不使用前复权价格乘成交量
替代直接成交额；直接成交额缺失时按缺失策略处理。

### 8.3 美股和港股

yfinance 没有稳定的逐日成交额列，因此使用：

```text
turnover = unadjusted_close * volume
```

- 美股单位 USD；
- 港股单位 HKD；
- 必须使用未复权 `close`，不能使用 `adj_close`；
- volume 必须有限且非负。

### 8.4 阈值

- A 股：`avg_turnover_cny >= min_avg_turnover_cny`；
- 美股：`avg_turnover_usd >= min_avg_turnover_usd`；
- 港股：`avg_turnover_hkd >= min_avg_turnover_hkd`。

恰好等于阈值通过。

## 9. 港股最低价格

使用不晚于 `snapshot_date` 的最新有效未复权收盘价：

```text
latest_close_hkd >= min_price_hkd
```

恰好等于阈值通过。NaN、inf、零、负数或没有有效收盘价视为缺失。不能使用
`adj_close` 替代名义价格。

## 10. 美股市值元数据

### 10.1 采集

首版 adapter 使用：

```python
yfinance.Ticker(symbol).get_info()
```

要求字段：

- `marketCap`；
- `currency`；
- `quoteType`；
- `regularMarketTime`；
- `exchangeTimezoneName`，缺失时只能用 UTC 解释时间戳并披露降级。

本地 2026-07-26 预检确认 yfinance 1.3.0 的 NVDA quote metadata 提供
`marketCap`、`currency=USD`、`quoteType=EQUITY` 和 `regularMarketTime`。
`fast_info` 在同一环境返回的 market cap 和 last price 为空，因此不作为主数据源。

### 10.2 规范化

输出 `EquityMetadata`：

- `symbol`；
- `market_cap_usd`；
- `data_date`；
- `retrieved_at`；
- `source`；
- `source_hash`；
- `hash_scope=normalized_metadata`。

要求：

- `marketCap` 是有限正数；
- `currency == USD`；
- `quoteType == EQUITY`；
- `regularMarketTime` 是有效 Unix timestamp；
- `data_date` 按交易所时区转换；
- `data_date <= snapshot_date`；
- `snapshot_date - data_date <= us_market_cap_max_age_days`。

最大年龄按自然日计算，默认 7 日，避免在本阶段引入独立美国交易日历。

### 10.3 历史运行

不能用当前 quote metadata 给历史日期回填。如果 `data_date` 晚于显式
`snapshot_date`，市值视为不可用并进入缺失策略。

### 10.4 阈值

```text
market_cap_usd >= min_market_cap_usd
```

恰好等于阈值通过。

## 11. 价格来源元数据

价格适配器保持返回 DataFrame 的兼容接口，但在 `DataFrame.attrs` 中设置规范化来源
元数据。A 股 fallback 成功后记录实际成功 provider，而不是笼统写“CN prices”。

用于过滤证据的价格来源包含：

- provider 名；
- 截止交易日；
- 获取时间；
- 参与过滤窗口的 canonical JSON SHA-256；
- `hash_scope=normalized_filter_window`。

测试 fixture 若不提供 attrs，可以通过显式依赖注入补充来源；生产路径不得生成来源
不明的量化过滤决定。

## 12. 价格快照 schema v2

快照增加：

```json
{
  "schema_version": 2,
  "snapshot_date": "2026-07-26",
  "filter_config_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "filter_summary": {
    "included": 8,
    "excluded": 3,
    "included_with_warning": 1,
    "reason_counts": {
      "market_cap_below_minimum": 2,
      "turnover_data_missing": 1
    }
  },
  "filter_decisions": [],
  "failures": []
}
```

约束：

- 每个进入评估的 symbol 都有最终决定；
- 一个 symbol 只有一个 `quantitative` 最终决定；
- 预过滤已排除的 symbol 没有 `quantitative` 决定，其 `seed_prefilter` 决定就是终态；
- 预过滤纳入后又进入量化过滤的 symbol，以 `quantitative` 决定作为终态；
- 多个排除原因可以共存；
- `snapshots` 只包含 `included` 和 `included_with_warning`；
- 每个被纳入 snapshot 保存其 `filter_status` 和已计算过滤指标；
- 预期阈值排除不进入 `failures`；
- provider 失败进入 `failures`，并同时产生对应的缺失过滤原因；
- filter summary 必须能由 decisions 重新计算得到。

`filter_summary` 只统计每个 symbol 的终态，不能把同一 symbol 的
`seed_prefilter=included` 和 `quantitative=included` 重复计数。

## 13. 缓存与配置一致性

### 13.1 seed pool

加载 seed pool 时比较当前 `filter_config_hash`：

- 相同：可以使用；
- 不同或缺失：不能把其中的预过滤结果视为有效；能够访问 themes 和 resolver 的命令
  必须重建 seed pool，否则在任何价格或元数据网络请求前失败，并明确要求执行
  `refresh-seed-pool`。

仅靠重新过滤旧 seed pool 不足以恢复曾被旧配置排除的 symbol，因此不能把防御性
复检当作 hash 不匹配的替代方案。价格快照层仍会重新执行北交所和 ST 检查，防止
手工构造的 seed pool 或直接函数调用绕过。

### 13.2 price snapshot

`build_data_snapshot` 读取缓存时：

- schema v2 且 hash 与当前配置一致：可以复用；
- hash 不同或缺失：重新采集；
- 无市场过滤配置时，旧 v1 快照仍可只读展示。

配置启用时不能静默复用未证明经过相同过滤的 v1 快照。

### 13.3 幂等

seed pool 和 price snapshot 写入均使用临时文件加 `os.replace` 原子覆盖。同一日期重跑
覆盖同日文件，不追加过滤决定。JSON 序列化和文件错误向上抛出。

## 14. CLI 与报告

### 14.1 CLI

价格刷新和日度任务输出：

```text
snapshots=8, excluded=3, warnings=1, failures=1
```

配置错误在网络访问前终止。配置启用未支持过滤器时，错误信息包含具体字段名。

### 14.2 数据快照渲染

价格表后追加过滤摘要：

- 纳入数量；
- 排除数量；
- 带警告纳入数量；
- reason code 计数；
- provider 失败数量。

### 14.3 日报

日报增加简短“市场过滤摘要”。存在 `included_with_warning` 时必须明确说明结论包含
降级数据。逐标的完整过滤记录不进入正文，留在价格快照 JSON。

如果价格快照没有任何可用标的，现有不推送保护继续生效，不能因为生成了过滤摘要就
绕过空价格快照门。

## 15. 错误处理

### 15.1 可按标的继续

- 价格或元数据网络错误；
- HTTP/provider 错误；
- 上游空数据；
- 上游缺列、非有限值或 schema 漂移；
- 名称、市值或成交额样本缺失。

这些错误必须具有 symbol、market、source 和 reason。核心价格不可用时标的不进入
快照；辅助过滤字段缺失时应用配置策略。

### 15.2 必须终止

- 配置非法；
- 依赖注入签名错误；
- 内部 `TypeError`、`AttributeError`；
- canonical hash 失败；
- JSON 序列化失败；
- 原子写入失败；
- 报告渲染错误。

应用编排不能使用宽泛 `except Exception` 把程序错误伪装成 provider 失败。provider
adapter 负责把已知外部异常包装成明确的 source error，应用层只捕获声明过的外部或
schema 错误。

## 16. 稳定 reason codes

首版至少定义：

```text
beijing_exchange_excluded
st_name_excluded
symbol_name_missing
latest_price_below_minimum
latest_price_missing
turnover_below_minimum
turnover_data_missing
turnover_observations_insufficient
market_cap_below_minimum
market_cap_missing
market_cap_currency_invalid
market_cap_quote_type_invalid
market_cap_timestamp_invalid
market_cap_from_future
market_cap_stale
price_data_unavailable
```

provider 的自由文本错误写入 `failures.reason`，不能动态拼成新的 reason code。

## 17. 测试

CI 全部使用固定 fixture，不访问网络。

### 17.1 配置

- 正常 typed config；
- 未知字段；
- 错误市场字段；
- boolean 隐式值；
- NaN、inf、零、负阈值；
- 窗口和最小观测边界；
- 两种缺失策略；
- 两个未支持字段为 true；
- 稳定配置哈希和描述字段不影响哈希。

### 17.2 预过滤

- `.BJ` 大小写；
- 手工、指数和 ETF 来源都过滤；
- `ST`、`*ST`、`SST`、`S*ST`；
- Unicode 空白和大小写；
- 名称部分缺失；
- 名称 provider 全量失败；
- 两种缺失策略；
- symbols、sources 和 theme mapping 同步；
- 旧 seed pool 不能绕过价格快照防御检查。

### 17.3 成交额和港股价格

- AkShare、Tushare、Baostock 单位；
- yfinance 未复权 close × volume；
- 20 日窗口和 15 日最小观测；
- 长 `period` 不改变口径；
- 乱序、重复日期、未来日期；
- 零成交额、负数、NaN 和 inf；
- 港股价格等于、低于阈值；
- 成交额等于、低于阈值。

### 17.4 美股市值

- 正常 metadata；
- 市值等于和低于阈值；
- 缺失、NaN、inf、零和负值；
- 非 USD；
- 非 EQUITY；
- timestamp 非法；
- 未来数据；
- 7 日边界和过期；
- provider 失败；
- 当前 metadata 不回填历史 snapshot date。

### 17.5 编排、缓存与输出

- 多原因排除；
- known failure 优先于 missing warning；
- 两种缺失策略；
- `filter_summary` 可从 decisions 重算；
- failures 与正常排除分离；
- price snapshot schema v2；
- 配置 hash 匹配、缺失和不匹配；
- v1 无配置只读兼容；
- 同日原子覆盖及失败清理；
- CLI 计数；
- 数据快照摘要；
- 日报降级披露；
- 空价格快照不推送；
- 意外 `TypeError` 向上抛出。

## 18. 真实数据验收

真实验收不提交运行产物：

1. 用当前 yfinance 版本采集 NVDA quote metadata；
2. 核对 `marketCap`、USD、EQUITY、`regularMarketTime` 和规范化数据日期；
3. 用 NVDA 最近行情手算 20 日平均成交额，与过滤指标一致；
4. 用一个低于 20 日有效观测的 fixture 验证缺失策略；
5. 用默认 `configs/markets.yaml` 跑一次有限标的价格刷新；
6. 核对快照 schema、配置 hash、decisions、summary 和 failures；
7. 核对 CLI 计数与 JSON 一致；
8. 核对日报过滤摘要；
9. 确认 `exclude_shell_like=true` 会在任何网络调用前失败；
10. 全量 pytest 和 Ruff 通过。

实时 provider 不满足契约时，验收结果必须明确失败或降级，不能手工填充市值或绕过
过滤器。

## 19. 非目标

- 不实现壳股识别；
- 不实现频繁资本运作识别；
- 不建设历史市值数据库；
- 不自动修复错误市场代码；
- 不把当前市值用于历史回填；
- 不用总市值、成交额或固定常量估算缺失市值；
- 不改变主题、评分或市场温度算法；
- 不借本阶段重写全部行情采集架构。
