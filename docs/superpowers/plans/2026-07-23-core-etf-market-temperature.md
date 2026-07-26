# core-etf-market-temperature 实施计划（修订版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 采集四类核心 ETF 成交额并将 ETF、两融和大盘资金流组合成缺失安全、可回放、可审计的三态市场温度。

**Architecture:** ingest 层只保存带日期和可用性的原始事实，`application/market_temperature.py` 统一完成新鲜度准备、三态/四态分类和温度真值表。上线前由独立历史采集命令生成 60 日固定回放与带指纹的 rollout artifact，日报只在 artifact 有效且经人工批准时推送 `professional_flow_daily`。

**Tech Stack:** Python、pandas、AkShare、Tushare、YAML、pytest、ruff。

日期：2026-07-23
基于总设计：`docs/superpowers/specs/2026-07-20-radar-remediation-roadmap-design.md` §5, §10, §11.2, §12

---

## 0. 问题诊断

### 0.1 当前代码的根因

`src/lurker/application/professional_flow_daily.py:30-47` 的 `classify_market_temperature()` 存在结构性缺陷：

```python
# 当前实现（有问题）
etf_active = any(_as_float(etf.get("turnover_expansion")) >= 1.2 for etf in core_etfs)
# 双负分支
if main_flow < 0 and super_large_flow < 0 and not etf_active:
    return "防守"
```

`collect_flow_snapshot()` 中 `fetch_core_etfs` 默认为 `lambda: []`（`flow_snapshot.py:59`），且没有真实 ETF 采集器。因此 `core_etfs` 在所有快照中始终为 `[]`，`etf_active` 恒为 `False`。这导致：

- 主力 + 超大单双负 → 无条件防守（因 ETF 永远是 `not etf_active`）
- 空 ETF 数据被当作 "ETF 不活跃"（inactive），而非 "未知"（unknown）
- 两融也缺少方向分级，只做二值判断（`margin_balance_change >= 0`）
- 日报长期偏向防守的结构性原因就在此处

此外，`_as_float()` 将 `None` / 缺失 key / `NaN` / `inf` 统一转为 `0.0`（`professional_flow_daily.py:19-23`），在市场资金流中 `main_net_inflow=0` 不等价于"未知"。

### 0.2 总设计要求的修正

总设计 §5.2 要求 ETF 状态为三态 `active | inactive | unknown`，两融为四态 `supportive | weakening | overheated | unknown`。§5.3 定义了新的真值表，其中 `unknown` 不提供正向或负向证据。

---

## 1. 真值表修正（最高优先级）

### 1.1 总设计 §5.3 原文

| 大盘主力与超大单 | ETF / 两融确认 | 结果 |
|---|---|---|
| 同为正 | ETF active **或** 两融 supportive | 进攻 |
| 同为负 | ETF inactive **或** 两融 weakening | 防守 |
| 同为正或负 | ETF、两融均 unknown | 观察 |
| 方向不一致 | 任意 | 观察 |
| 任意 | 两融 overheated | 防守 |

关键词：**或**，不是 **且**。

### 1.2 衍生出的完整测试矩阵

双负场景的正确判定：

| 主力 | 超大单 | ETF 状态 | 两融信号 | 结果 | 依据 |
|------|--------|---------|---------|------|------|
| - | - | inactive | weakening | **防守** | ETF inactive **或** 两融 weakening（两个都满足） |
| - | - | inactive | unknown | **防守** | ETF inactive **或** 两融 weakening（ETF inactive 满足） |
| - | - | unknown | weakening | **防守** | ETF inactive **或** 两融 weakening（两融 weakening 满足） |
| - | - | unknown | unknown | **观察** | 均 unknown，缺失不是负向证据 |
| - | - | active | weakening | **防守** | 两融 weakening 满足（即使 ETF active） |
| - | - | inactive | supportive | **防守** | ETF inactive 满足（即使两融 supportive） |
| - | - | inactive | overheated | **防守** | overheated 优先（且 ETF inactive 也满足） |

双正场景的正确判定：

| 主力 | 超大单 | ETF 状态 | 两融信号 | 结果 | 依据 |
|------|--------|---------|---------|------|------|
| + | + | active | supportive | **进攻** | ETF active **或** 两融 supportive（两个都满足） |
| + | + | active | unknown | **进攻** | ETF active 满足 |
| + | + | unknown | supportive | **进攻** | 两融 supportive 满足 |
| + | + | unknown | unknown | **观察** | 均 unknown，缺失不是正向证据 |
| + | + | inactive | unknown | **观察** | 无正向确认 |
| + | + | unknown | weakening | **观察** | 无正向确认 |

方向不一致：

| 主力 | 超大单 | ETF 状态 | 两融信号 | 结果 |
|------|--------|---------|---------|------|
| + | - | active | supportive | **观察** |
| - | + | inactive | weakening | **观察** |

两融 overheated 覆盖：

| 主力 | 超大单 | ETF 状态 | 两融信号 | 结果 |
|------|--------|---------|---------|------|
| + | + | active | overheated | **防守** |
| - | - | inactive | overheated | **防守** |
| + | - | unknown | overheated | **防守** |

---

## 2. 数据契约冻结

### 2.1 ETF 采集返回结构：`CoreEtfBatch`

```python
@dataclass
class CoreEtfItem:
    symbol: str                    # "510300.SH"
    name: str                      # "沪深300ETF"
    trade_date: str                # "2026-07-23" — 成交额数据日期
    current_turnover: float        # 当日成交额（元）
    avg_turnover_20d: float | None   # 前20日平均成交额（不含当日）；历史不足21日时为None
    turnover_expansion: float | None  # current / avg_20d；avg为零时为None
    shares: float | None           # 份额（股），本阶段不采集（fund_etf_hist_em 不提供）
    shares_date: str | None        # 份额数据日期，本阶段固定为None
    status: str                    # "active" | "inactive" | "unknown"
    source: str                    # "akshare_fund_etf_hist_em" | "akshare_fund_etf_hist_sina"
    availability: str              # "turnover_only" | "insufficient_history" | "intraday_partial" | "invalid_average" | "stale"

@dataclass
class CoreEtfBatch:
    configured_symbols: list[str]   # ["510300.SH", "510500.SH", "159915.SZ", "159361.SZ"]
    items: list[CoreEtfItem]       # 成功采集的 ETF（可空）
    failures: list[dict[str, str]] # {"symbol": "510300.SH", "reason": "provider timeout"}
    generated_at: str              # ISO 8601
    schema_version: int            # 1

    @classmethod
    def from_dict(cls, data: dict) -> "CoreEtfBatch":
        """从序列化 dict 反序列化，含嵌套 CoreEtfItem 恢复和 schema 校验。"""

    def to_dict(self) -> dict:
        """序列化为 JSON 兼容 dict。"""

    def is_complete(self) -> bool:
        """验证 configured_symbols == (item symbols ∪ failure symbols)。"""
```

**完整度验证**：
- `classify_etf_status()` 调用前必须验证 `batch.is_complete()`
- 不完整 → `"unknown"`（fetcher 意外漏掉了配置的 ETF，不能静默当作 inactive）
- 例如：配置了 4 只 ETF，fetcher 只返回 1 只且未登记另外 3 只失败 → `is_complete() == False` → `"unknown"`

**关键规则**：
- `current_turnover` 和 `avg_turnover_20d` 必须关联同一 `trade_date`
- 日常采集优先 `ak.fund_etf_hist_em()`；若该接口返回空表，使用
  `ak.fund_etf_hist_sina()` 作为可审计 fallback，并在 `source` 中保留实际来源
- `avg_turnover_20d` 计算使用前 20 个有效交易日，**不含当日**
- 单标的完全失败（网络错误、接口异常、无返回数据）→ **不进入 `items`**，只进入 `failures`
- `items` 仅保存有可用成交额事实的记录（即使 `status = "unknown"`，只要 `current_turnover` 可计算就进入 items）
- 有效历史不足 21 日 → `status = "unknown"`，`availability = "insufficient_history"`，但仍在 `items` 中（成交额数据存在）
- 全量采集失败 → `items = []`，`failures` 记录全部
- `shares` 和 `shares_date` 本阶段固定为 `None`——`ak.fund_etf_hist_em()` 仅提供成交额，不提供份额；份额数据后续阶段通过 Tushare `fund_share` 的 `fd_share` 采集
- `turnover_expansion` 中 `avg_turnover_20d == 0` → `turnover_expansion = None`，`status = "unknown"`，`availability = "invalid_average"`——**不得写入 `float("inf")`**（非标准 JSON）
- 反序列化必须使用 `CoreEtfBatch.from_dict(data)`，不能依赖 `CoreEtfBatch(**dict)`（嵌套 dataclass 不会自动恢复）
- `configured_symbols` 必须非空、规范化且唯一；`items` 与 `failures` 中的 symbol 也必须唯一，不能用集合相等掩盖重复记录

### 2.2 两融事实结构与派生信号

```python
# normalize_margin_frame 返回原始事实，不写入派生信号
{
    "trade_date": "20260723",
    "financing_balance": 1_800_000_000_000.0,
    "securities_lending_balance": 10_000_000_000.0,
    "margin_balance": 1_810_000_000_000.0,
    "margin_balance_change": 5_000_000_000.0,
    "availability": "fresh"
}
```

**两融信号分类逻辑**（纯函数，位于 `application/market_temperature.py`）：

```python
def classify_margin_signal(margin: dict[str, Any]) -> str:
    """
    四态：supportive | weakening | overheated | unknown
    - margin 为空 dict {} → "unknown"
    - margin_balance_change 不存在或非有限数 → "unknown"
    - margin_balance_change > 0 → "supportive"
    - margin_balance_change < 0 → "weakening"
    - margin_balance_change == 0 → "unknown"  # 持平不提供方向证据
    - 本阶段 overheated 固定返回 "unknown"（分母数据待回放校准后启用）

    注意：不引入第五态 "neutral"。市场资金流内部的 _flow_direction() 保留 neutral，
    但两融公开信号严格遵守总设计四态契约。
    """
```

**`overheated` 降级说明**：
- 总设计要求 `overheated` = 融资余额 / 流通市值 > 阈值
- 计算公式（后续阶段实现）：
  ```python
  # 分子：融资余额（rzye / financing_balance），不含融券
  # 分母：全市场 circ_mv 之和（Tushare daily_basic.circ_mv，单位万元）
  # overheated_ratio = sum(rzye) / (sum(circ_mv) * 10_000)
  # 分子分母必须同一交易日
  ```
- 当前缺少以下基础设施：`daily_basic` 全市场流通市值采集、日期对齐、阈值回放校准
- 本阶段 `classify_margin_signal()` **永远不返回 `"overheated"`**；真值表中 overheated 分支仅 fixture 测试覆盖
- `overheated` 阈值未来放入独立 `configs/market_temperature.yaml`，不放入 `core_etfs.yaml`

### 2.3 市场资金流 NaN/None/inf 处理

当前 `_as_float()` 将 `None`/`NaN`/`inf` 统一转为 `0.0`，在市场资金流中这是错误的——缺失数据不应变成 `main_net_inflow = 0`（恰好是方向中立的 0，会绕过双正/双负判断）。

新增纯函数：

```python
def _flow_direction(value: Any) -> str:
    """
    返回 "positive" | "negative" | "neutral" | "unknown"
    - None / NaN / inf / -inf → "unknown"
    - > 0 → "positive"
    - < 0 → "negative"
    - == 0 → "neutral"
    
    neutral 不等于 positive。主力和超大单均为 neutral 时不应进入进攻。
    """
```

`classify_market_temperature()` 调用此函数判断主力/超大单方向，而非依赖 `_as_float()` 与 `0` 比较。

**方向一致性判断**：只有当主力方向 == 超大单方向，且两者均为 `"positive"` 或两者均为 `"negative"` 时才算"同为正"或"同为负"。`"neutral"` 或 `"unknown"` 参与的比较 → 方向不一致 → 观察。

### 2.4 ETF 聚合三态

```python
def classify_etf_status(batch: CoreEtfBatch, *, threshold: float = 1.2) -> str:
    """
    返回 "active" | "inactive" | "unknown"

    前置条件：batch.is_complete() == True，否则直接返回 "unknown"。

    严格规则（防止将部分失败误判为 inactive）：
    - 任意一只有效且 turnover_expansion >= threshold（非 None）→ "active"
    - 所有配置 ETF 均有效且均未达标 → "inactive"
    - 以下情况 → "unknown"：
      · batch.is_complete() == False（fetcher 漏掉配置标的）
      · items 全部为空且 failures 非空（全部失败）
      · items 全部为空且 failures 为空（未采集）
      · 存在 failures/unknown，但无任何一只有效达标
        （部分失败 + 其余未达标 → unknown，不是 inactive）
    """
```

**关键区别**：4 只 ETF 中 1 只成功且未放量、3 只失败 → `"unknown"`（不是 `"inactive"`）。缺失数据不能间接成为负向证据。

**区分**：`CoreEtfItem.status` 是单标的分类（`turnover_expansion >= 1.2` → `active`，否则 `inactive`，数据不足 → `unknown`）。`classify_etf_status()` 是市场聚合三态。

---

### 2.5 数据新鲜度契约

**问题**：当前 `market_flow` 没有 `trade_date`，`margin` 在 Tushare 失败后加载旧缓存无标记，ETF 可能返回昨天甚至更早的数据。旧数据可能继续影响今天的进攻/防守判断。

**统一规则**：

```python
def resolve_expected_trade_date(
    report_date: str,
    *,
    is_trading_day: Callable[[date], bool],
    now: datetime,
    market_close_cutoff: time = time(15, 30),
) -> str:
    """
    返回报告日期对应的最近一个已完成交易日。
    - 历史交易日 → 当天
    - 当日且 now 位于 Asia/Shanghai、时间 >= 15:30 → 当天
    - 当日且 now 位于 Asia/Shanghai、时间 < 15:30 → 上一个交易日
    - 周末/节假日 → 回退到节前最后一个交易日
    - 未来日期 → 拒绝
    """
```

**各数据源新鲜度规则**：

| 数据源 | 新鲜度检查 | 不新鲜时的处理 |
|--------|----------|--------------|
| `market_flow` | `trade_date == expected` | 方向降级为 `"unknown"`；`availability = "stale"` |
| `core_etfs` (每个 item) | `trade_date == expected` | `status = "unknown"`；`availability = "stale"` |
| `margin` | `trade_date == expected` | `margin_signal = "unknown"`；`availability = "stale_cache"` |
| `margin` (Tushare 失败回退到旧缓存) | cache `trade_date < expected` | 同上 + `availability = "stale_cache"` |

**规则**：
- `source.trade_date == expected_trade_date` → 可参与分类
- `source.trade_date < expected_trade_date` → stale，不提供正向或负向证据
- `source.trade_date > expected_trade_date` → 数据错误
- `source.trade_date` 缺失 → `"unknown"`
- `now` 必须是带时区时间并统一转换为 `Asia/Shanghai`；测试不得依赖真实系统时间
- 本阶段通过可注入的 `is_trading_day` 调用现有交易日历；可更新交易日提供器留在既定的 `legacy-calendar-cleanup` 阶段

**数据结构变更**：

```python
# market_flow 增加 trade_date（当前实现不保存此字段）
{
    "main_net_inflow": 100_000_000.0,
    "super_large_net_inflow": 50_000_000.0,
    "trade_date": "2026-07-23",  # 新增
    "availability": "fresh"       # "fresh" | "stale" | "unknown"
}

# margin 增加 availability（当前 cache 回退无标记）
{
    "margin_balance": 1_810_000_000_000.0,
    "margin_balance_change": 5_000_000_000.0,
    "trade_date": "20260723",
    "availability": "fresh"       # "fresh" | "stale_cache" | "unknown"
}

# CoreEtfItem 已有 trade_date 和 availability 字段，新鲜度通过它们判断
```

**报告要求**：
- 日报数据质量区显示三个来源各自的截止日期和新鲜度状态
- 任何来源 stale → 在报告中标注 "⚠️ 部分数据非当日"

**在温度分类中的集成**：
- `classify_market_temperature()` 不直接接收新鲜度标记
- 新增唯一准备入口 `prepare_temperature_inputs()`；`run_professional_flow_daily()` 与 `weekly_flow_report()` 都调用它，不各自复制新鲜度逻辑
- 例如：ETF 全部 stale → `etf_status = "unknown"`

```python
@dataclass(frozen=True)
class PreparedTemperatureInputs:
    market_flow: dict[str, Any]
    etf_status: str
    margin_signal: str
    expected_trade_date: str
    quality_notes: tuple[str, ...]


def prepare_temperature_inputs(
    flow_snapshot: dict[str, Any],
    *,
    expected_trade_date: str,
) -> PreparedTemperatureInputs:
    """
    market_flow stale/缺日期 → 两个净流入值均设为 None
    ETF stale/partial/不完整 → 聚合状态 unknown
    margin stale_cache/stale/缺日期 → margin_signal unknown
    任一来源 trade_date > expected_trade_date → ValueError
    """
```

### 2.6 ingest 缺失值保真

分类层只能识别仍然保留的缺失值。`normalize_market_flow_frame()` 不得继续使用“失败时返回 0”的 `_to_float()` 处理主力和超大单净流入。

```python
def _to_optional_float(value: Any) -> float | None:
    """
    None、空字符串、占位符、NaN、inf、-inf → None
    其他可转换且有限的值 → float
    """
```

- `main_net_inflow`、`super_large_net_inflow` 使用 `_to_optional_float()`
- 原始列缺失时保存 `None`，不能保存 `0.0`
- `normalize_market_flow_frame()` 同时保存提供方返回的最新 `trade_date`
- 真实数值 `0` 保留为 `0.0`，之后由 `_flow_direction(0)` 分类为 `neutral`

---

## 3. 文件变更清单

### 3.1 新增文件

| 文件 | 用途 |
|------|------|
| `configs/core_etfs.yaml` | 核心 ETF 可配置列表 |
| `src/lurker/ingest/etf_flows.py` | ETF 成交额采集、CoreEtfBatch 构建 |
| `src/lurker/application/market_temperature.py` | 新鲜度日期解析、输入准备、ETF/两融分类与市场温度真值表 |
| `tests/test_etf_flows.py` | ETF 采集单元测试 |
| `tests/test_market_temperature.py` | 温度真值表 + 三态/四态分类测试 |
| `tests/fixtures/etf_synthetic_truth_table.json` | 合成 fixture：覆盖所有真值表组合 |
| `tests/fixtures/etf_60d_replay.json` | 60 个真实历史交易日快照回放 |
| `tests/test_market_temperature_replay.py` | 合成真值表回放 + 60 日真实回放 + 闸门测试 |

### 3.2 修改文件

| 文件 | 变更 |
|------|------|
| `src/lurker/application/professional_flow_daily.py` | 删除旧的 `classify_market_temperature()`；通过 `prepare_temperature_inputs()` 调用新模块 |
| `src/lurker/application/flow_snapshot.py` | `collect_flow_snapshot()` 绕过 `_capture()` 接入 ETF；保留两融事实及数据可用性 |
| `src/lurker/ingest/flows.py` | 市场资金缺失值保真并保存 `trade_date`；margin cache 标记新鲜度 |
| `src/lurker/cli.py` | `daily-job` 增加 `--no-push`；`refresh-flows` 接入 ETF 采集；新增 `build-temperature-replay` 命令；增加温度闸门检查 |
| `src/lurker/application/weekly_flow_report.py` | `_status_counts()` 通过统一准备层适配新温度签名 |
| `src/lurker/reports/professional_flow_report.py` | 市场温度区显示 ETF 状态、两融信号 |
| `src/lurker/config.py` | 新增 `load_core_etfs()` |
| `tests/test_professional_flow_daily.py` | 存量测试适配新签名/新逻辑；新增三态温度测试 |
| `tests/test_flow_snapshot.py` | 新增 ETF 采集的 snapshot 测试 |
| `tests/test_flows.py` | 新增缺失值保真、trade_date 和 margin cache 可用性测试 |
| `tests/test_cli.py` | 新增 `--no-push` 测试；core_etfs fixture 更新 |

---

## 4. Task 分解

### Task 1: 真值表测试先行（RED，不写实现）

**目标**：先把全部真值表写成失败测试，确认每个用例的预期失败原因正确

**新增文件**：
- `tests/test_market_temperature.py`

**测试矩阵**（全部 RED，因为 `classify_market_temperature` 新位置/新签名不存在）：

#### 1.1 防守 = OR 逻辑（关键修正）

```python
def test_defense_when_dual_negative_etf_inactive_margin_unknown():
    """双负 + ETF inactive + margin unknown → 防守（ETF inactive 单独确认）"""

def test_defense_when_dual_negative_etf_unknown_margin_weakening():
    """双负 + ETF unknown + margin weakening → 防守（两融 weakening 单独确认）"""

def test_defense_when_dual_negative_etf_inactive_margin_supportive():
    """双负 + ETF inactive + 两融 supportive → 防守（ETF inactive 覆盖两融正向）"""

def test_defense_when_dual_negative_etf_active_margin_weakening():
    """双负 + ETF active + 两融 weakening → 防守（两融 weakening 覆盖 ETF 正向）"""
```

#### 1.2 观察 = unknown 不提供证据

```python
def test_observe_when_dual_negative_both_unknown():
    """双负 + ETF unknown + margin unknown → 观察（缺失不是负向证据）"""

def test_observe_when_dual_positive_both_unknown():
    """双正 + ETF unknown + margin unknown → 观察（缺失不是正向证据）"""

def test_observe_when_direction_mismatch():
    """主力正、超大单负 → 观察"""
```

#### 1.3 进攻

```python
def test_attack_when_dual_positive_etf_active_margin_unknown():
    """双正 + ETF active → 进攻（ETF 单独确认）"""

def test_attack_when_dual_positive_etf_unknown_margin_supportive():
    """双正 + 两融 supportive → 进攻（两融单独确认）"""
```

#### 1.4 两融 overheated（fixture 路径测试，真实数据不触发）

```python
def test_defense_when_margin_overheated_regardless_of_flow():
    """任意资金方向 + 两融 overheated → 防守"""

def test_defense_when_dual_positive_but_margin_overheated():
    """双正 + 两融 overheated → 防守（overheated 覆盖进攻）"""
```

#### 1.4b 数据新鲜度

```python
def test_temperature_stale_etf_treated_as_unknown():
    """ETF trade_date 不是最近交易日 → etf_status = unknown"""
def test_temperature_stale_margin_treated_as_unknown():
    """margin availability = stale_cache → margin_signal = unknown"""
def test_temperature_stale_data_not_negative_evidence():
    """stale 数据不会导致防守"""
def test_temperature_future_source_date_fails():
    """来源日期晚于 expected_trade_date → ValueError"""
def test_expected_trade_date_before_close_uses_previous_session():
    """交易日 15:30 前运行 → 使用上一交易日"""
def test_expected_trade_date_after_close_uses_current_session():
    """交易日 15:30 后运行 → 使用当天"""
```

#### 1.5 三态/四态独立分类

```python
def test_classify_etf_status_unknown_when_empty_batch():
def test_classify_etf_status_unknown_when_all_failed():
def test_classify_etf_status_unknown_when_partial_failure_and_no_active():
    """4 只 ETF 中 1 只成功未达标 + 3 只失败 → unknown，不是 inactive"""
def test_classify_etf_status_active_when_one_above_threshold():
def test_classify_etf_status_inactive_when_all_below_threshold():
def test_classify_margin_signal_unknown_when_empty_dict():
def test_classify_margin_signal_unknown_when_no_change_field():
def test_classify_margin_signal_supportive_when_positive_change():
def test_classify_margin_signal_unknown_when_zero_change():
    """margin_balance_change == 0 → unknown（持平不提供方向证据）"""
def test_classify_margin_signal_weakening_when_negative_change():
def test_classify_margin_signal_overheated_always_unknown_in_this_phase():
```

#### 1.6 资金流方向

```python
def test_flow_direction_positive():
def test_flow_direction_negative():
def test_flow_direction_neutral_for_zero():
    """0 → neutral，不是 positive"""
def test_flow_direction_unknown_for_none():
def test_flow_direction_unknown_for_nan():
def test_flow_direction_unknown_for_inf():
```

#### 1.7 ingest 缺失值保真

在 `tests/test_flows.py` 增加：

```python
def test_market_flow_normalizer_preserves_missing_main_flow_as_none():
    """缺失主力净流入不能变成 0.0"""

def test_market_flow_normalizer_preserves_real_zero():
    """真实数值 0 保留为 0.0，供方向层判为 neutral"""

def test_market_flow_normalizer_includes_latest_trade_date():
    """标准化结果携带提供方最新交易日期"""

def test_margin_cache_fallback_marked_stale_cache():
    """Tushare 失败回退缓存时不能伪装成 fresh"""
```

**测试命令**：
```bash
cd /Users/marv/Documents/lurker
PYTHONPATH=src .venv/bin/pytest tests/test_market_temperature.py tests/test_flows.py -v
# 预期：新增的温度、缺失值保真和 freshness 测试 RED；失败原因分别是新模块/新函数/新字段尚未实现
```

**提交检查点**：不单独提交（RED 测试随 Task 4 一起 GREEN）

---

### Task 2: AkShare Schema 预检（阻塞性前置）

**目标**：在写任何适配器代码前，交互式验证 AkShare ETF 接口的实际 schema

**验证清单**（人工执行，不依赖 CI）：

```bash
cd /Users/marv/Documents/lurker
PYTHONPATH=src .venv/bin/python3
```

```python
import akshare as ak

# 1. 验证 fund_etf_hist_em 接受代码格式
df = ak.fund_etf_hist_em(symbol="510300", period="daily", start_date="20260701", end_date="20260723", adjust="")
print(df.columns.tolist())
print(df.head(2))
print(df.dtypes)

# 2. 测试带后缀
# df2 = ak.fund_etf_hist_em(
#     symbol="510300.SH",
#     period="daily",
#     start_date="20260701",
#     end_date="20260723",
#     adjust="",
# )  # 是否报错？

# 3. 确认成交额列名和单位
# 预期列名：日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
# 确认成交额单位（元 vs 万元）

# 4. 确认 fund_etf_fund_info_em 不提供份额
# 已知该接口返回历史净值、增长率、申赎状态，不提供 ETF 份额
# 如可访问则验证确认，但不依赖其结果

# 5. 测试不足 21 日历史的 ETF
# 用较新的 ETF 代码测试

# 6. 测试当日数据是否为盘中实时数据
# 收盘前运行，检查当日数据的成交额是否完整
```

**必须确认的结论**：
- [ ] ETF 代码格式（纯数字 vs 带后缀）
- [ ] 成交额列名和单位
- [ ] 当日数据是否为盘中实时数据（收盘前运行是否有当日不完整数据）
- [ ] ~~份额接口可用性~~ → 已确认 `fund_etf_fund_info_em` **不提供**份额，本阶段 `shares` 固定为 `None`
- [ ] 少于 21 日历史的 ETF 返回什么（空 DataFrame？还是报错？）
- [ ] 网络代理环境是否可达东方财富端点（当前代理环境测试被阻断，部署环境实测是阻塞性验收项）

**输出**：在计划中补充「AkShare ETF Schema 确认记录」小节

#### AkShare ETF Schema 确认记录（2026-07-25）

- 本地版本：AkShare `1.18.60`
- 实际调用：`fund_etf_hist_em(symbol="510300", period="daily", start_date="20260601", end_date="20260725", adjust="")`
- 线上结果：当前开发机代理访问 `push2his.eastmoney.com` 抛出 `requests.exceptions.ProxyError`；经项目既有 request scope 切换到 delay 端点后返回空 DataFrame。部署环境真实连通性验收仍未解除
- 已安装版本源码确认：接口接受纯数字 `symbol`，返回列为 `日期、开盘、收盘、最高、最低、成交量、成交额、振幅、涨跌幅、涨跌额、换手率`，其中价格及成交字段通过 `pd.to_numeric()` 标准化
- 适配策略：配置保留 canonical symbol 用于报告，调用 AkShare 时使用纯数字代码；以 `成交额` 计算当日值和排除当日的前 20 日均值
- 测试策略：通过可注入的历史行情 fetcher 固定验证正常、单标的失败、历史不足和盘中不完整场景；真实网络失败只形成可审计 failure，不伪造 ETF 状态

---

### Task 3: ETF 采集器实现（GREEN Task 1 中 ETF 相关测试）

**目标**：实现 ETF 成交额采集和 CoreEtfBatch 构建

**新增文件**：
- `src/lurker/ingest/etf_flows.py`
- `configs/core_etfs.yaml`

`configs/core_etfs.yaml`：
```yaml
etfs:
  - role: "csi300"
    symbol: "510300"
    canonical_symbol: "510300.SH"
    name: "沪深300ETF"
    market: "cn"
  - role: "csi500"
    symbol: "510500"
    canonical_symbol: "510500.SH"
    name: "中证500ETF"
    market: "cn"
  - role: "chinext"
    symbol: "159915"
    canonical_symbol: "159915.SZ"
    name: "创业板ETF"
    market: "cn"
  - role: "csi_a500"
    symbol: "159361"
    canonical_symbol: "159361.SZ"
    name: "A500ETF易方达"
    market: "cn"
```
注：159361.SZ 是易方达中证A500ETF（深交所），非华泰柏瑞。如使用华泰柏瑞，代码为 563360.SH。

**配置校验**：
- `role` 必须至少覆盖且各自恰好出现一次：`csi300`、`csi500`、`chinext`、`csi_a500`
- 可以增加未来扩展角色，但不能删除四个必需角色
- `symbol` 与 `canonical_symbol` 必须全局唯一
- `canonical_symbol` 必须带 `.SH` 或 `.SZ` 后缀
- 文件缺失、空列表、角色缺失、重复角色或重复 symbol 都是配置错误，启动时明确失败

**代码设计**：

```python
# src/lurker/ingest/etf_flows.py

@dataclass
class CoreEtfItem:
    symbol: str
    name: str
    trade_date: str
    current_turnover: float
    avg_turnover_20d: float | None   # 历史不足21日时为None
    turnover_expansion: float | None  # avg为零时为None
    shares: float | None
    shares_date: str | None
    status: str          # active | inactive | unknown
    source: str
    availability: str    # turnover_only | insufficient_history | intraday_partial | invalid_average | stale

@dataclass
class CoreEtfBatch:
    configured_symbols: list[str]
    items: list[CoreEtfItem]
    failures: list[dict[str, str]]
    generated_at: str
    schema_version: int

def fetch_core_etfs(
    *,
    etf_configs: list[dict[str, str]] | None = None,
) -> CoreEtfBatch:
    """采集核心 ETF 成交额数据。单标的失败不阻塞其他标的。"""
    # 使用 ak.fund_etf_hist_em() 获取日线
    # 最近 21 个交易日（当日 + 前 20 日）
    # 计算 avg_turnover_20d（排除当日）
    # 单标的完全失败 → 不进入 items，只进入 failures
```

**AkShare 接口处理**（基于 Task 2 预检结果）：
- 如果 AkShare 不接受 `.SH`/`.SZ` 后缀，在适配器内部 strip 后缀，`canonical_symbol` 仅用于报告显示
- 如果当日数据为盘中数据（收盘前不完整），标记 `availability = "intraday_partial"`，`status = "unknown"`
- `shares` 和 `shares_date` 本阶段固定为 `None`（`fund_etf_fund_info_em` 不提供份额；份额后续通过 Tushare `fund_share` 的 `fd_share` 采集）
- `< 21 日有效历史 → `status = "unknown"`，`availability = "insufficient_history"`
- `avg_turnover_20d == 0` → `turnover_expansion = None`，`status = "unknown"`，`availability = "invalid_average"`

**import 行为**：
- `from lurker.ingest.etf_flows import fetch_core_etfs` 的模块导入错误**不得静默回退**
- 只有 `fetch_core_etfs()` 运行时因外部数据源（网络、API）失败才允许降级
- 模块导入失败 → 启动时报错退出，不能伪装成 `unknown`

**RED 测试**（`tests/test_etf_flows.py`）：

| 测试 | 预期失败 |
|------|---------|
| `test_fetch_core_etfs_returns_core_etf_batch` | `fetch_core_etfs` 未定义 |
| `test_core_etf_batch_has_items_and_failures` | `CoreEtfBatch` 未定义 |
| `test_core_etf_item_status_active_when_expansion_above_threshold` | 同上 |
| `test_core_etf_item_status_inactive_when_expansion_below_threshold` | 同上 |
| `test_single_etf_failure_does_not_block_others` | 同上 |
| `test_partial_failure_without_active_gives_unknown` | 同上（关键：不降级为 inactive） |
| `test_all_etfs_fail_gives_empty_items_and_failures` | 同上 |
| `test_avg_turnover_excludes_current_day` | 同上 |
| `test_turnover_expansion_none_when_average_zero` | 同上（不是 inf） |
| `test_insufficient_history_gives_unknown_status` | 同上 |
| `test_batch_from_dict_restores_nested_items` | `CoreEtfBatch.from_dict` 未实现 |
| `test_batch_to_dict_round_trips` | 同上 |
| `test_batch_from_dict_rejects_unknown_fields` | 同上 |
| `test_batch_from_dict_handles_corrupted_item` | 同上 |
| `test_batch_is_complete_true_when_all_accounted` | `is_complete` 未实现 |
| `test_batch_is_complete_false_when_symbol_missing` | 同上 |
| `test_classify_etf_status_unknown_when_batch_incomplete` | fetcher 漏掉配置标的 → unknown |
| `test_module_import_error_not_silently_swallowed` | 同上 |
| `test_config_requires_all_four_roles` | 缺任一必需角色 → 配置错误 |
| `test_config_rejects_duplicate_role_or_symbol` | 重复角色/symbol → 配置错误 |

**GREEN 步骤**：
- [ ] Task 2 预检完成，冻结 AkShare 适配规则
- [ ] 创建 `configs/core_etfs.yaml`
- [ ] 在 `src/lurker/config.py` 增加 `load_core_etfs(path) -> list[dict[str, str]]`
- [ ] 实现 `src/lurker/ingest/etf_flows.py`
- [ ] 运行 ETF 测试并确认全部变绿

**测试命令**：
```bash
cd /Users/marv/Documents/lurker && PYTHONPATH=src .venv/bin/pytest tests/test_etf_flows.py -v
```

**提交检查点**：
```
feat: add core ETF fetcher with CoreEtfBatch and tristate per-item classification
```

---

### Task 4: 市场温度 + 两融信号实现（GREEN Task 1 全部测试）

**目标**：实现 `application/market_temperature.py`，让 Task 1 所有测试变绿

**新增文件**：
- `src/lurker/application/market_temperature.py`

**修改文件**：
- `src/lurker/application/professional_flow_daily.py`
- `src/lurker/ingest/flows.py`
- `src/lurker/application/weekly_flow_report.py`

**`market_temperature.py` 公开 API**：

```python
def classify_etf_status(batch: CoreEtfBatch, *, threshold: float = 1.2) -> str:
    """聚合 ETF 三态：active | inactive | unknown"""

def classify_margin_signal(margin: dict[str, Any]) -> str:
    """两融四态：supportive | weakening | overheated | unknown
    本阶段 overheated 永远返回 "unknown"（等待分母数据）。"""

def _flow_direction(value: Any) -> str:
    """positive | negative | neutral | unknown（NaN/None/inf → unknown）"""

def prepare_temperature_inputs(
    flow_snapshot: dict[str, Any],
    *,
    expected_trade_date: str,
) -> PreparedTemperatureInputs:
    """统一执行新鲜度检查和缺失降级。"""

def classify_market_temperature(
    *,
    market_flow: dict[str, Any],
    etf_status: str,
    margin_signal: str,
) -> str:
    """三档市场温度：进攻 / 观察 / 防守，按 §1 真值表"""
```

**`professional_flow_daily.py` 变更**：

```python
# 旧代码删除
# 删除 professional_flow_daily.py 中原有的 classify_market_temperature 定义

# 新代码
from lurker.application.market_temperature import (
    classify_market_temperature,
    prepare_temperature_inputs,
    resolve_expected_trade_date,
)

# run_professional_flow_daily() 增加 now: datetime | None = None，
# 仅用于调度时钟注入和确定性测试。

# 在 run_professional_flow_daily() 中：
resolved_now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
expected_trade_date = resolve_expected_trade_date(
    report_date,
    is_trading_day=is_cn_trading_day,
    now=resolved_now,
)
prepared = prepare_temperature_inputs(
    flow_snapshot,
    expected_trade_date=expected_trade_date,
)
temperature = classify_market_temperature(
    market_flow=prepared.market_flow,
    etf_status=prepared.etf_status,
    margin_signal=prepared.margin_signal,
)
```

**`flows.py` 变更**：
- 新增 `_to_optional_float()`，市场主力/超大单净流入缺失或非有限时保存 `None`
- `normalize_market_flow_frame()` 保存最新 `trade_date`，真实零值仍保存为 `0.0`
- `normalize_margin_frame()` 的数值列出现全缺失时不得静默汇总为零
- `fetch_margin()` 成功时设置 `availability="fresh"`；失败回退缓存时强制覆盖为 `availability="stale_cache"`
- `margin_signal` 值由准备层从带新鲜度的原始事实重新计算，不直接信任缓存中旧的 `margin_signal`
- `ingest/flows.py` 不 import `application/market_temperature.py`（避免循环依赖）；信号计算在应用层准备函数中完成

**`flow_snapshot.py` 变更**：
```python
# collect_flow_snapshot() 中只记录事实及采集时可用性
margin_data = _capture("margin", fetch_margin, failures)
```

**`weekly_flow_report.py` 变更**：
- `_status_counts()` 以快照文件日期作为 `expected_trade_date` 调用 `prepare_temperature_inputs()`
- 周报不自行实现 ETF、margin 或 market flow 的 stale 规则

**测试命令**：
```bash
# Task 1 测试全部变绿
cd /Users/marv/Documents/lurker
PYTHONPATH=src .venv/bin/pytest tests/test_market_temperature.py tests/test_flows.py -v

# 存量测试（预期部分失败，因为 fixture 需要适配）
PYTHONPATH=src .venv/bin/pytest tests/test_professional_flow_daily.py tests/test_weekly_flow_report.py tests/test_flow_snapshot.py -v
```

**提交检查点**：
```
fix: rewrite market temperature with tristate ETF and four-state margin signals
```

---

### Task 5: Flow Snapshot + CLI 接入 + 存量适配

**目标**：接入真实 ETF 采集器到 snapshot；适配存量测试；增加 `--no-push`

**修改文件**：
- `src/lurker/application/flow_snapshot.py`
- `src/lurker/cli.py`
- `tests/test_professional_flow_daily.py`
- `tests/test_flow_snapshot.py`
- `tests/test_cli.py`
- `tests/test_weekly_flow_report.py`

**`flow_snapshot.py` 变更**：

**关键**：不能通过现有的 `_capture()` 调用 ETF fetcher。`_capture()` 捕获所有 `Exception`（`flow_snapshot.py:29`），会把 `TypeError`、`KeyError`、配置错误等程序错误也伪装成 `unknown`。必须绕过 `_capture()`，在 `collect_flow_snapshot()` 中直接调用 fetcher。

```python
# collect_flow_snapshot() 中 ETF 采集（绕过 _capture）
resolved_etf_fetcher = fetch_core_etfs or _default_etf_fetcher
core_etfs_batch = resolved_etf_fetcher()
# fetcher 契约：可恢复的数据源失败必须返回保留 configured_symbols 的 CoreEtfBatch。
# TypeError、AttributeError、KeyError、ValueError、ImportError 直接向上传播。
```

`_default_etf_fetcher()` 内部：

```python
def _default_etf_fetcher() -> CoreEtfBatch:
    try:
        from lurker.ingest.etf_flows import fetch_core_etfs
        from lurker.config import load_core_etfs
    except ImportError as e:
        raise RuntimeError(
            "core ETF fetcher module is not importable."
        ) from e

    config_path = Path(__file__).resolve().parents[3] / "configs" / "core_etfs.yaml"
    if not config_path.exists():
        raise RuntimeError(
            f"core_etfs.yaml not found at {config_path}. "
            "This file is required for ETF market temperature."
        )
    configs = load_core_etfs(config_path)
    if not configs:
        raise RuntimeError("core_etfs.yaml is empty.")

    configured_symbols = [row["canonical_symbol"] for row in configs]
    try:
        return fetch_core_etfs(etf_configs=configs)
    except (EtfProviderError, EtfSchemaError, ConnectionError, TimeoutError, OSError) as exc:
        return CoreEtfBatch(
            configured_symbols=configured_symbols,
            items=[],
            failures=[
                {"symbol": symbol, "reason": f"数据源不可用: {exc}"}
                for symbol in configured_symbols
            ],
            generated_at=datetime.now(UTC).isoformat(),
            schema_version=1,
        )
    # TypeError/KeyError/ValueError 等程序/配置错误向上传播
```

**集成测试**（`tests/test_flow_snapshot.py` 新增）：

| 测试 | 验证 |
|------|------|
| `test_collect_flow_snapshot_propagates_etf_type_error` | `TypeError` 在 ETF fetcher 中 → `collect_flow_snapshot` 向外抛出 |
| `test_default_etf_fetcher_returns_complete_unknown_batch_on_provider_error` | `EtfProviderError` → 保留四个 `configured_symbols`，每只均有 failure |
| `test_collect_flow_snapshot_fails_on_missing_config` | `core_etfs.yaml` 不存在 → `RuntimeError` |

**异常类定义**（`src/lurker/ingest/etf_flows.py`）：
```python
class EtfProviderError(Exception):
    """AkShare ETF 数据源不可用（网络、API 限流等）。"""

class EtfSchemaError(Exception):
    """AkShare ETF 返回结构与预期不符（列名、类型变化等）。"""
```

**异常处理原则**：
- `ImportError` → 启动时明确失败（`RuntimeError`）
- `core_etfs.yaml` 缺失或为空 → 启动时明确失败（`RuntimeError`）
- `TypeError`, `AttributeError`, `KeyError` → 程序错误，明确失败
- `ValueError`（配置校验）→ 明确失败
- `EtfProviderError`, `EtfSchemaError`, `ConnectionError`, `TimeoutError`, `OSError` → 降级为 `unknown`
- `except Exception` 不得出现在 ETF 采集路径中；**不通过 `_capture()` 调用 ETF fetcher**（`_capture()` 会吞掉程序错误）
- 注入的测试 fetcher 也必须遵守相同契约：可恢复失败返回 batch，程序错误抛出

**`cli.py` 变更**：
- `daily-job` 子命令增加 `--no-push` flag
- `daily_job()` 函数增加 `push: bool = True` 参数
- 推送逻辑受 `push` 参数控制
- `refresh-flows` 使用真实 ETF 采集器

**存量测试适配策略**：
- 所有使用 `core_etfs: []` 的 fixture：改为使用带 `configured_symbols` 的序列化 `CoreEtfBatch` 或直接 mock 准备函数
- `test_classify_market_temperature_defense_when_all_negative`：旧逻辑用空 `core_etfs=[]` + 空 `margin={}` 期望防守 → **新逻辑下为观察**，测试断言需改为观察，或 fixture 补齐 ETF inactive / margin weakening
- 逐测试检查修改

**测试命令**：
```bash
cd /Users/marv/Documents/lurker && PYTHONPATH=src .venv/bin/pytest tests/test_flow_snapshot.py tests/test_cli.py tests/test_professional_flow_daily.py tests/test_weekly_flow_report.py tests/test_flows.py -v
# 目标：全部 GREEN
```

**提交检查点**：
```
feat: wire core ETF fetcher into flow snapshot and add --no-push to daily-job
```

---

### Task 6: 合成真值表回放 + 60 日真实回放 + 闸门

**目标**：两类 fixture + 回放脚本 + 上线闸门

**新增文件**：
- `tests/fixtures/etf_synthetic_truth_table.json`
- `tests/fixtures/etf_60d_replay.json`
- `tests/test_market_temperature_replay.py`

#### 6.1 合成真值表 fixture（`etf_synthetic_truth_table.json`）

覆盖 §1.2 完整测试矩阵的每一行。每条记录包含：
```json
{
  "case_id": "defense_001",
  "market_flow": {"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
  "etf_status": "inactive",
  "margin_signal": "unknown",
  "expected": "防守",
  "rationale": "双负 + ETF inactive → 防守（ETF 独立确认）"
}
```

~25 条记录，覆盖全部真值表组合 + 边界（NaN/None/inf 资金流、空 dict、缺失 key）。

**NaN/inf 在 JSON fixture 中的处理**：标准 JSON 不支持 `NaN`/`Infinity`。合成 fixture 使用字符串标签 `"NaN"`、`"Infinity"`、`"-Infinity"`，在测试加载器中转换为 Python `float("nan")`、`float("inf")`、`float("-inf")`。或改用 pytest `@pytest.mark.parametrize` 直接在 Python 中构造边界用例。

#### 6.2 60 日真实历史回放 fixture（`etf_60d_replay.json`）

**回放区间**：输出 2026-04-24 至 2026-07-22（截至 7 月 22 日的最近 60 个完整交易日，按项目现有 2026 年交易日历）。

**预热数据（关键）**：仅查询输出区间会导致前 20 个回放日全部因历史不足而降级。必须扩展查询区间：

| 数据源 | 查询区间 | 原因 |
|--------|---------|------|
| ETF 历史行情 | **2026-03-26** ~ 2026-07-22 | 前 20 个有效交易日预热，用于计算 4 月 24 日的 `avg_turnover_20d` |
| 两融历史 | **2026-04-23** ~ 2026-07-22 | 前一日余额用于计算 4 月 24 日的 `margin_balance_change` |
| 大盘资金历史 | 2026-04-24 ~ 2026-07-22 | 单日数据，无预热需求 |
| **最终输出** | **2026-04-24** ~ 2026-07-22 | 60 个完整交易日 |

**构造方式**：**不能使用 `refresh-flows --date` 循环补采集**。当前 `refresh-flows --date` 只决定保存文件名，`collect_flow_snapshot()` 始终抓取最新数据（`cli.py:1153-1164`）。循环传历史日期会把同一天的当前数据伪装成不同历史日期。

必须新增专用的历史回放采集器：

```bash
PYTHONPATH=src .venv/bin/lurker build-temperature-replay \
  --etf-start   2026-03-26 \
  --margin-start 2026-04-23 \
  --output-start 2026-04-24 \
  --output-end   2026-07-22 \
  --output tests/fixtures/etf_60d_replay.json
```

**`build-temperature-replay` 命令设计**：
- 按 `trade_date` 对齐三个数据源：
  1. **大盘资金历史**：`ak.stock_market_fund_flow()` 历史序列，按日期匹配
  2. **ETF 历史行情**：优先 `ak.fund_etf_hist_em()`；空表或网络/提供方
     可恢复异常时使用 `ak.fund_etf_hist_sina()`，统一提取成交额并保存
     实际 `source`。`TypeError`、字段契约错误等程序错误不得触发 fallback
     或被吞掉
  3. **两融历史**：优先 Tushare `pro.margin(trade_date="20260424")`；
     token 无权限时使用 AkShare 金十沪深两市历史汇总，并保存实际 `source`
- 每个交易日输出一条记录，包含原始事实（不包含分类结果，分类由回放脚本在运行时计算）
- 如某个来源某日缺失，标记 `availability` 而非跳过该日

**Fixture 每条记录格式**：
```json
{
  "date": "2026-04-24",
  "market_flow": {
    "trade_date": "2026-04-24",
    "main_net_inflow": 100000000.0,
    "super_large_net_inflow": 50000000.0,
    "availability": "fresh"
  },
  "core_etfs": {
    "configured_symbols": ["510300.SH", "510500.SH", "159915.SZ", "159361.SZ"],
    "items": [
      {
        "symbol": "510300.SH",
        "trade_date": "2026-04-24",
        "current_turnover": 3500000000.0,
        "avg_turnover_20d": 2800000000.0,
        "turnover_expansion": 1.25
      }
    ],
    "failures": []
  },
  "margin": {
    "trade_date": "20260424",
    "financing_balance": 1800000000000.0,
    "margin_balance": 1810000000000.0,
    "margin_balance_change": 5000000000.0,
    "availability": "fresh"
  }
}
```

**回放脚本输出**（三列差异）：
```
| 日期 | 旧规则状态 | 新规则状态 | 变化原因 |
| 2026-04-24 | 防守 | 观察 | 旧：空ETF→not etf_active→防守；新：ETF unknown+margin unknown→观察 |
| 2026-04-27 | 防守 | 防守 | 旧：空ETF+双负→防守；新：ETF inactive→防守（ETF独立确认） |
```

#### 6.3 回放测试

```python
def test_synthetic_truth_table_all_cases_match():
    """逐条验证合成真值表，预期输出与 expected 一致"""

def test_60d_replay_outputs_status_distribution():
    """输出三种状态天数和比例"""

def test_60d_replay_outputs_per_day_raw_input_and_result():
    """每个交易日输出：原始输入 + 旧规则状态 + 新规则状态 + 变化原因"""

def test_60d_replay_counts_unknown_degradation_days():
    """统计因数据缺失降级为观察的天数"""

def test_60d_replay_shows_rule_diff_columns():
    """输出包含三列：旧规则状态、新规则状态、变化原因"""
```

#### 6.4 上线闸门 + Rollout Artifact

**问题**：生产环境 `data/processed/flow_snapshots/` 目录当前只有 2 份快照，日报会因"历史不足 60 日"永远无法推送。需要一个一次性 rollout artifact 来解除闸门。

**Rollout Artifact**（`data/processed/temperature_rollout.json`）：

```json
{
  "rules_version": "2026-07-23",
  "rules_fingerprint": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "replay_path": "tests/fixtures/etf_60d_replay.json",
  "replay_start": "2026-04-24",
  "replay_end": "2026-07-22",
  "trading_days": 60,
  "distribution": {
    "进攻": 15,
    "观察": 30,
    "防守": 15
  },
  "max_ratio": 0.50,
  "approved": false,
  "approved_by": null,
  "approved_at": null,
  "replay_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "notes": "待人工审查回放结果后将 approved 改为 true"
}
```

**Artifact 验证**：
- `approved` 默认 `false`，由 `build-temperature-replay` 生成
- `rules_fingerprint` = 规范化规则载荷的 SHA256。载荷至少包含 `rules_version`、ETF 阈值、真值表版本、四个必需 ETF role 和新鲜度策略；任一规则参数变化都会使 artifact 失效
- `replay_sha256` = 回放 fixture 文件的 SHA256（fixture 被修改后 → artifact 失效）
- 人工审查通过后，将 `approved` 改为 `true`，填写 `approved_by` 和 `approved_at`
- 闸门读取原始 fixture，逐日重新执行当前规则；`trading_days`、`distribution`、
  `replay_start` 和 `replay_end` 必须与重算结果一致，不能由人工填写绕过
- 回放只读取一次；SHA256 校验和规则执行必须使用同一份 bytes，避免两次
  读取之间文件被替换
- 回放日期必须是项目交易日历确认的中国交易日，并且严格递增、不重复；
  60 个连续自然日不能冒充 60 个交易日。状态计数必须是非负整数

**Artifact 生命周期**：
- 由 `build-temperature-replay` 命令在输出 fixture 的同时生成（带 `approved: false`）
- 人工审查回放结果后，将 `approved` 改为 `true`
- `rules_version` 或阈值变化后 artifact 失效，必须重新回放
- 文件不存在 → 视同"历史不足 60 日"→ 阻断

**闸门代码**（`cli.py`）：

```python
def _check_temperature_gate(
    artifact_path: Path,
    *,
    replay_path: Path,
    current_rules_fingerprint: str,
    max_ratio: float = 0.80,
) -> tuple[bool, str]:
    """
    读取 rollout artifact，返回 (allowed, reason)
    - artifact 不存在 → False, "缺少 rollout artifact"
    - artifact.rules_version != 当前 rules_version → False, "规则版本已变更，需重新回放"
    - artifact.rules_fingerprint != current_rules_fingerprint → False, "规则指纹不一致"
    - artifact.replay_path 解析后的路径 != replay_path → False, "回放路径不一致"
    - replay 文件 SHA256 != artifact.replay_sha256 → False, "回放文件已变化"
    - replay 文件不可读或路径为目录 → False, "回放文件无法读取"
    - artifact.approved == false → False, "回放尚未通过人工审查"
    - approved_by / approved_at 缺失 → False, "审批信息不完整"
    - trading_days < 60 → False, "历史不足60日"
    - sum(distribution.values()) != trading_days → False, "分布与交易日数不一致"
    - 从回放文件逐日重跑当前规则，校验日期属于中国交易日且严格递增、不重复
    - 重算 trading_days/distribution/日期范围必须与 artifact 一致
    - 根据重算后的 distribution 计算最高占比；不得信任 artifact.max_ratio
    - 重算后任一状态占比 > max_ratio → False, "状态X占比Y%超过80%"
    - 任一状态占比 == max_ratio → True, 附带 "状态X恰好80%，请人工复核"
    - 否则 → True, ""
    """

def _current_rules_version() -> str:
    """返回当前温度分类规则版本标识。规则逻辑变更时需手动递增。"""
    return "2026-07-23"

def _current_rules_fingerprint() -> str:
    payload = {
        "rules_version": "2026-07-23",
        "etf_threshold": 1.2,
        "required_etf_roles": ["chinext", "csi300", "csi500", "csi_a500"],
        "attack_confirmation": "etf_active_or_margin_supportive",
        "defense_confirmation": "etf_inactive_or_margin_weakening",
        "margin_zero": "unknown",
        "market_flow_zero": "neutral",
        "overheated_threshold": None,
        "freshness": {
            "timezone": "Asia/Shanghai",
            "market_close_cutoff": "15:30",
            "stale_result": "unknown",
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
```

**闸门作用范围**：
- 单独运行 `professional_flow_daily` → 闸门阻断时报告落盘但不推送
- 单独运行其他策略（`long_term_trend`、`watchlist_anomaly` 等）→ 闸门不参与，正常推送
- 多个策略合并运行（`daily-job --strategies professional_flow_daily,other`）→ 若闸门阻断，**整个合并推送被阻断**（所有策略的报告均落盘但不推送）。这不是逐策略通知重构，而是保守安全策略：只要 professional_flow 的结果未经回放验证，不推送任何内容给日报接收人
- `--no-push` 模式下：显示闸门检查结果，但不实际发送推送
- 推送阻断时：报告仍然落盘，数据质量区标注阻断原因

**测试**：

```python
def test_temperature_gate_blocks_when_artifact_missing():
    """artifact 文件不存在 → False"""

def test_temperature_gate_blocks_when_any_state_exceeds_80_percent():
    """任一状态 > 80%（不含等于）→ gate 返回 False"""

def test_temperature_gate_blocks_when_not_approved():
    """approved: false → False"""

def test_temperature_gate_blocks_when_rules_version_changed():
    """artifact 版本 != 当前版本 → False"""

def test_temperature_gate_blocks_when_rules_fingerprint_changed():
    """规则载荷变化 → False"""

def test_temperature_gate_blocks_when_replay_hash_changed():
    """回放 fixture 被修改 → False"""

def test_temperature_gate_blocks_when_replay_path_changed():
    """artifact 指向的回放路径与实际校验路径不一致 → False"""

def test_temperature_gate_blocks_when_distribution_total_mismatches_days():
    """distribution 总数与 trading_days 不一致 → False"""

def test_temperature_gate_blocks_when_trading_days_below_60():
    """即使 approved=true，少于 60 日仍阻断"""

def test_temperature_gate_recomputes_ratio_instead_of_trusting_artifact_value():
    """伪造 artifact.max_ratio 不能绕过 >80% 闸门"""

def test_temperature_gate_rejects_forged_summary_for_empty_replay():
    """空回放 + 人工填写 60 日分布不能绕过闸门"""

def test_temperature_gate_rejects_forged_distribution_with_same_total():
    """总数相同但与逐日重算不一致的分布必须阻断"""

def test_temperature_gate_rejects_duplicate_or_unsorted_replay_dates():
    """回放日期必须严格递增且不重复"""

def test_temperature_gate_rejects_non_trading_replay_dates():
    """周末/节假日不能计入 60 个交易日"""

def test_temperature_gate_fails_closed_when_replay_path_is_directory():
    """路径存在但不可读时返回阻断原因，不能让 daily-job 异常退出"""

def test_temperature_gate_hashes_and_executes_same_replay_bytes():
    """哈希与规则执行使用同一次读取，避免 TOCTOU"""

def test_temperature_gate_requires_complete_approval_metadata():
    """approved=true 但 approved_by/approved_at 缺失 → False"""

def test_temperature_gate_allows_when_all_under_80_and_approved():
    """各状态均 ≤ 80% 且 approved → True"""

def test_temperature_gate_warns_at_exact_80_percent():
    """恰好 80% → True 但输出复核警告"""

def test_temperature_gate_only_blocks_professional_flow_strategy():
    """闸门不阻断 long_term_trend 等策略的单独推送"""

def test_temperature_gate_blocks_combined_push_containing_professional_flow():
    """合并策略中包含 professional_flow_daily 时阻断整个合并推送"""
```

**闸门触发位置**：在 `daily_job()` 推送前调用，`--no-push` 时跳过闸门（但输出闸门检查结果）。

**测试命令**：
```bash
cd /Users/marv/Documents/lurker && PYTHONPATH=src .venv/bin/pytest tests/test_market_temperature_replay.py -v
```

**提交检查点**：
```
feat: add synthetic truth table replay, 60-day real replay, and >80% temperature gate
```

---

### Task 7: 报告层适配 + 数据质量披露

**目标**：报告中显示 ETF/两融状态，数据质量区披露 ETF 部分失败

**修改文件**：
- `src/lurker/reports/professional_flow_report.py`
- `src/lurker/application/professional_flow_daily.py`（`_market_notes()` 更新）

**报告变更**：
- 市场温度区增加一行：`ETF 状态：active（沪深300ETF 放量 1.35x）` 或 `ETF 状态：unknown（全部采集失败）`
- 数据质量区输出 ETF 采集失败的逐标的错误信息
- `_market_notes()` 接收 `CoreEtfBatch` + `etf_status` + `margin_signal`

**RED 测试**（`test_professional_flow_daily.py`）：

| 测试 | 预期失败 |
|------|---------|
| `test_report_shows_etf_status_when_active` | 模板未更新 |
| `test_report_shows_etf_status_when_unknown` | 同上 |
| `test_report_shows_margin_signal` | 同上 |
| `test_report_data_quality_lists_etf_failures` | 同上 |
| `test_report_data_quality_lists_partial_etf_failure` | 同上 |

**GREEN 步骤**：
- [x] 更新 `_market_notes()` 签名和内容
- [x] 更新报告模板
- [x] 运行报告测试并确认全部变绿

**测试命令**：
```bash
cd /Users/marv/Documents/lurker && PYTHONPATH=src .venv/bin/pytest tests/test_professional_flow_daily.py -v
```

**提交检查点**：
```
feat: display ETF status, margin signal, and partial failures in daily report
```

---

### Task 8: 全量回归 + 真实数据人工验收

**目标**：全部测试通过 + lint + 真实数据演练

**步骤**：

- [ ] **全量测试**：
```bash
cd /Users/marv/Documents/lurker && PYTHONPATH=src .venv/bin/pytest tests/ -v
# 全部 GREEN（含 25 个存量测试文件 + 3 个新增测试文件）
```

- [ ] **Lint**：
```bash
cd /Users/marv/Documents/lurker && .venv/bin/ruff check src/ tests/
```

- [ ] **真实数据演练**（`--no-push`）：
```bash
# 采集资金流（含 ETF）
cd /Users/marv/Documents/lurker && PYTHONPATH=src .venv/bin/lurker refresh-flows --date $(date +%Y-%m-%d)

# 验证快照中 core_etfs 结构
cat data/processed/flow_snapshots/$(date +%Y-%m-%d).json | python3 -c "import json,sys; d=json.load(sys.stdin); print(type(d['core_etfs']), len(d.get('core_etfs',{}).get('items',[])))"

# 生成日报（不推送）
PYTHONPATH=src .venv/bin/lurker daily-job --strategies professional_flow_daily --no-push

# 检查报告中的 ETF 状态和两融信号
```

- [ ] **60 日回放输出审查**：
   人工检查 `test_60d_replay_outputs_per_day_raw_input_and_result` 的输出：
   - 三种状态各自天数和比例
   - 是否有 >80% 的状态
   - 每个交易日的原始输入 + 旧规则状态 + 新规则状态 + 变化原因
   - 因数据缺失降级为观察的天数

- [ ] **幂等性验证**：
```bash
# 第一次运行
PYTHONPATH=src .venv/bin/lurker daily-job --strategies professional_flow_daily --no-push --date 2026-07-23
# 复制为基准
cp data/reports/2026-07-23.md /tmp/baseline.md
# 第二次运行
PYTHONPATH=src .venv/bin/lurker daily-job --strategies professional_flow_daily --no-push --date 2026-07-23
# 比较（排除 generated_at 等时间戳字段）
diff <(grep -v 'generated_at' /tmp/baseline.md) <(grep -v 'generated_at' data/reports/2026-07-23.md)
```

---

## 5. 不修改的范围

- ✅ `watchlist_anomaly` — 不改变
- ✅ `long_term_trend` — 不修补 deprecated 策略
- ✅ 宏观周报/月报 — 不实现（阶段三/四）
- ✅ 个股资金流缺失降级推送规则 — 不改变
- ✅ `price_snapshot` 采集 — 不改变
- ✅ `themes.yaml` / `scoring.yaml` — 不改变

---

## 6. 配置与规则版本化

### 6.1 Schema v2 存储策略

`flow_snapshot` 的 `schema_version` 从 1 升级到 2：

```json
{
  "schema_version": 2,
  "market_flow": {
    "trade_date": "2026-07-23",
    "main_net_inflow": 123.0,
    "super_large_net_inflow": 45.0,
    "availability": "fresh"
  },
  "core_etfs": {
    "configured_symbols": ["510300.SH", "510500.SH", "159915.SZ", "159361.SZ"],
    "items": [],
    "failures": [
      {"symbol": "510300.SH", "reason": "示例：数据源不可用"},
      {"symbol": "510500.SH", "reason": "示例：数据源不可用"},
      {"symbol": "159915.SZ", "reason": "示例：数据源不可用"},
      {"symbol": "159361.SZ", "reason": "示例：数据源不可用"}
    ],
    "generated_at": "2026-07-23T08:00:00+00:00",
    "schema_version": 1
  },
  "margin": {
    "trade_date": "20260723",
    "margin_balance": 1810000000000.0,
    "margin_balance_change": 5000000000.0,
    "availability": "fresh"
  },
  "classification_rules": {
    "etf_threshold": 1.2,
    "overheated_threshold": null,
    "rules_version": "2026-07-23"
  }
}
```

**设计原则**：存储原始事实 + 分类时使用的规则版本。调阈值后可以复现历史分类结果，因为原始事实（成交额、均量、两融变化）不变，只变规则参数。

### 6.2 独立配置文件

`configs/core_etfs.yaml`：ETF 列表和基础参数
`configs/market_temperature.yaml`（未来）：两融 overheated 阈值、ETF 放量阈值、回放窗口等

---

## 7. 兼容方案汇总

### 7.1 Schema v1 → v2 兼容

| 字段 | v1 格式 | v2 格式 | 兼容策略 |
|------|---------|---------|---------|
| `market_flow.trade_date` | 不存在 | ISO 日期 | 缺失时 freshness unknown，不提供方向证据 |
| `core_etfs` | `[]`（空列表） | `CoreEtfBatch.to_dict()` 的完整对象 | `isinstance(dict)` → v2；`isinstance(list)` → unknown |
| `margin.availability` | 不存在 | `"fresh"` 等 | v1 快照回放按文件日期与 `trade_date` 比较；无法证明新鲜时为 unknown |

### 7.2 存量测试兼容

- mock `collect_flow_snapshot` 的测试：需要将 `core_etfs: []` 改为包含 `configured_symbols` 的 v2 batch
- mock `classify_market_temperature` 的测试：需要调整参数为三态字符串

### 7.3 周报兼容

- `weekly_flow_report.py` 加载快照后反序列化 `core_etfs`，调用 `classify_etf_status()`
- v1/v2 快照都先经过 `prepare_temperature_inputs()`；只有日期和新鲜度验证通过后才计算 margin signal

---

## 8. 测试命令速查

```bash
cd /Users/marv/Documents/lurker

# Task 1: 真值表测试（RED）
PYTHONPATH=src .venv/bin/pytest tests/test_market_temperature.py -v

# Task 3: ETF 采集器
PYTHONPATH=src .venv/bin/pytest tests/test_etf_flows.py -v

# Task 4: 温度实现（GREEN Task 1 全部）
PYTHONPATH=src .venv/bin/pytest tests/test_market_temperature.py -v

# Task 5: 全量接入 + CLI
PYTHONPATH=src .venv/bin/pytest tests/test_flow_snapshot.py tests/test_cli.py tests/test_professional_flow_daily.py tests/test_weekly_flow_report.py tests/test_flows.py -v

# Task 6: 回放 + 闸门
PYTHONPATH=src .venv/bin/pytest tests/test_market_temperature_replay.py -v

# Task 8: 全量回归
PYTHONPATH=src .venv/bin/pytest tests/ -v
.venv/bin/ruff check src/ tests/
```

---

## 9. 提交检查点

```
1. feat: add core ETF fetcher with CoreEtfBatch and tristate per-item classification
2. fix: rewrite market temperature with tristate ETF and four-state margin signals
3. feat: wire core ETF fetcher into flow snapshot and add --no-push to daily-job
4. feat: add synthetic truth table replay, 60-day real replay, and >80% temperature gate
5. feat: display ETF status, margin signal, and partial failures in daily report
6. chore: full regression passing with schema v2 compatibility
```

---

## 10. 风险与待确认决策

### 10.1 风险

| 风险 | 缓解 |
|------|------|
| AkShare ETF 接口不稳定或字段变化 | Task 2 Schema 预检在前；东方财富空表或可恢复外部异常时使用新浪 ETF 历史 fallback，实际来源写入 `source`；程序/契约错误直接抛出 |
| Tushare 不可用时两融为 unknown → 温度偏观察 | 日常快照仍严格降级 unknown；历史回放可使用 AkShare 金十沪深汇总 fallback 并记录 provenance |
| 大盘历史资金接口只返回最新一行 | 回放保留该日缺失事实，不伪造历史值；当前 60 日回放因此 100% 为观察并由 >80% 闸门阻断 |
| 模块导入错误静默回退 | **已修复**：导入错误明确抛异常；`EtfProviderError`/`EtfSchemaError` 用于已知降级场景 |
| `margin_balance_change == 0` 边界 | 明确定义为 `"unknown"`（持平不提供方向证据），保持四态契约 |
| NaN/None/inf 通过 `_as_float` 变 0 | **已修复**：`_flow_direction()` 返回 `"unknown"`；0 返回 `"neutral"` |
| `main_net_inflow == 0` 被当作正向 | **已修复**：`_flow_direction(0)` → `"neutral"`，不进入进攻确认 |
| 部分 ETF 失败被误判为 inactive | **已修复**：存在 any failure + no active → `"unknown"` |
| ingest 已把缺失资金流转成 0 | **已修复**：市场资金字段使用 `_to_optional_float()`，缺失保存为 `None` |
| 旧缓存或滞后数据继续提供温度证据 | **已修复**：统一准备层按最近已完成交易日降级 stale 数据 |
| rollout artifact 被陈旧规则、修改后的 fixture 或伪造摘要复用 | **已修复**：校验规则指纹、fixture SHA256、审批信息，并从原始回放逐日重算交易日数、状态分布和日期范围；逐日验证中国交易日，文件读取失败时 fail closed |
| ETF 配置缺少必需市场代表 | **已修复**：配置 loader 强制四个必需 role 且拒绝重复 symbol |

### 10.2 已确认决策

| 决策 | 结论 |
|------|------|
| A500 ETF 代码 | `159361.SZ`（易方达中证A500ETF，深交所）。如需华泰柏瑞，代码为 `563360.SH` |
| ETF 成交额数据源 | 主源 `ak.fund_etf_hist_em()`；空表或可恢复外部异常 fallback 为 `ak.fund_etf_hist_sina()`。均**仅用于成交额**，并保存实际 `source` |
| ETF 份额数据源 | 本阶段不采集。后续宏观周报使用 Tushare `fund_share` 的 `fd_share`（万份）；`fund_etf_hist_em()` 不提供份额，不能代理申赎 |
| 60 日回放区间 | 2026-04-24 至 2026-07-22（60 个完整交易日） |
| 60 日回放采集方式 | 新增 `build-temperature-replay` 命令，按 trade_date 对齐三个数据源。**不使用** `refresh-flows --date`；缺失日保留 unavailable 事实 |
| 两融过热分母 | Tushare `daily_basic.circ_mv`（流通市值，万元）。分子使用 `financing_balance`/`rzye`（不含融券）。分子分母同一交易日 |
| 两融过热实现 | 本阶段固定为 `"unknown"`。分母数据和阈值回放校准后再启用 |

### 10.3 待确认决策

1. **两融 overheated 阈值**：`financing_balance / sum(circ_mv)` 超过多少百分比算过热？需回放后校准

---

## 11. 验收标准对照（总设计 §11.2）

| 验收标准 | 对应 Task | 验证方式 |
|---------|----------|---------|
| 空 ETF 数据得到 `unknown`，不能得到 `inactive` | Task 4 | `test_classify_etf_status_unknown_when_empty_batch` |
| 资金双负但 ETF、两融均 unknown → 观察 | Task 1, 4 | `test_observe_when_dual_negative_both_unknown` |
| 资金双负 + ETF inactive（即使 margin unknown）→ 防守 | Task 1, 4 | `test_defense_when_dual_negative_etf_inactive_margin_unknown` |
| 资金双负 + 两融 weakening（即使 ETF unknown）→ 防守 | Task 1, 4 | `test_defense_when_dual_negative_etf_unknown_margin_weakening` |
| 资金双正 + ETF active → 进攻 | Task 1, 4 | `test_attack_when_dual_positive_etf_active_margin_unknown` |
| 资金双正 + 两融 supportive → 进攻 | Task 1, 4 | `test_attack_when_dual_positive_etf_unknown_margin_supportive` |
| 60 日回放完成 | Task 6 | `test_real_60d_replay_has_auditable_source_provenance` |
| 任一状态 >80% 阻止上线 | Task 6 | `test_temperature_gate_blocks_invalid_artifacts` |
| 恰好 80% 允许但输出复核警告 | Task 6 | `test_temperature_gate_warns_at_exact_80_percent` |
| artifact 缺失或未审批 → 阻断 | Task 6 | `test_temperature_gate_blocks_when_artifact_missing` |
| 规则版本变更 → artifact 失效 | Task 6 | `test_temperature_gate_blocks_when_rules_version_changed` |
| 闸门仅阻断 professional_flow_daily | Task 6 | `test_temperature_gate_only_blocks_professional_flow_strategy` |
| 规则变更前后状态差异输出 | Task 6 | `test_60d_replay_shows_rule_diff_columns` |
| 缺失数据不被转为零或负向信号 | Task 4 | `_flow_direction(None)` → `"unknown"` |
| ingest 缺失净流入保留为 None | Task 4 | `test_market_flow_normalizer_preserves_missing_main_flow_as_none` |
| 盘中运行使用上一完整交易日 | Task 1, 4 | `test_expected_trade_date_before_close_uses_previous_session` |
| stale ETF/margin/market flow 不提供证据 | Task 1, 4 | freshness 测试组 |
| ETF 配置覆盖四个必需 role | Task 3 | `test_config_requires_all_four_roles` |
| artifact 规则指纹与 replay 哈希必须有效 | Task 6 | fingerprint/hash 篡改测试 |
| artifact 分布和交易日数重新校验 | Task 6 | distribution/trading-days 测试 |
| artifact 摘要不能脱离原始回放伪造 | Task 6 | 空回放、同总数伪分布、重复日期、非交易日测试 |
| 新增测试先红后绿 | Task 1 → 4 | Task 1 全部 RED；Task 4 全部 GREEN |
| 全量测试 + lint 通过 | Task 8 | `pytest tests/ -v` + `ruff check` |
| `--no-push` 演练 | Task 8 | 真实数据执行 |

---

## 12. Task 6–8 真实验收记录（2026-07-25/26）

- 合成真值表：25 条，覆盖双正、双负、方向分歧、overheated、
  `None/NaN/±inf/0` 边界。
- 固定回放：`tests/fixtures/etf_60d_replay.json`，区间
  2026-04-24 至 2026-07-22，共 60 个交易日。
- ETF 历史：四只 ETF × 60 日均成功，实际来源为
  `akshare_fund_etf_hist_sina`；东方财富历史接口在当前网络返回空表。
- 两融历史：60 日均成功，实际来源为 `akshare_jin10_margin_sh_sz`；
  当前 Tushare token 无 `margin` 权限。
- 大盘历史资金：东方财富 history 端点在当前网络不可达，delay 端点仅返回
  最新一行；60 个回放日均明确保存为 `availability=unknown`、
  `source=unavailable`，没有伪造或日期回填。
- 回放分布：进攻 0、观察 60、防守 0；最高占比 100%。
- rollout artifact：已生成但保持 `approved=false`。即使人工改为 approved，
  重算后的 100% 观察仍会触发 `>80%` 闸门，因此当前不得上线推送。
- 安全复核后闸门改为直接读取原始回放并逐日执行当前规则；artifact 中的
  `trading_days`、`distribution`、日期范围和 `max_ratio` 均不能作为独立
  可信输入。空回放、伪分布、重复日期、周末/节假日均会阻断；回放文件
  不可读时返回阻断原因，不中断报告落盘流程；哈希与执行使用同一份 bytes，
  不存在二次读取替换窗口。
- 历史采集不再尝试非 TLS 的市场资金接口；AkShare 只返回最新行时明确
  标记历史不足。ETF 主源仅在网络/提供方可恢复异常时 fallback，程序错误
  直接抛出。
- 日常核心 ETF 实采：4/4 成功，截止 2026-07-24，均使用新浪 fallback，
  `CoreEtfBatch.is_complete() == true`。
- 日报演练：已显示 ETF inactive、两融 unknown、三个来源截止日和
  “部分数据非当日或采集不完整”提示。
- 结论：代码、回放、报告和闸门验收完成；**上线验收未通过**，阻断原因是
  大盘历史资金 60 日不可用导致状态集中度 100%。不得人工批准 artifact，
  直至取得完整历史源并重新生成回放。
