# Weekly Macro Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立的 `weekly_macro_flow` 周度策略，用真实 ETF 份额、两融、最近五个交易日超大单和板块周度排名回答“资金是否持续进场”，同时与旧 `weekly-report` 并存。

**Architecture:** ingest 层只采集和保存带日期、来源、可用性的原始事实；`application/weekly_macro_flow.py` 负责纯分类和总状态；`reports/weekly_macro_flow_report.py` 只渲染。独立 CLI 生成可审计 raw snapshot、读取上一期 snapshot 做连续性判断、调用策略注册表、落盘并使用日报接收人推送。

**Tech Stack:** Python 3.12、pandas、AkShare、Tushare、PyYAML、pytest、ruff。

---

## 0. 已确认边界与不可变约束

1. ETF 份额唯一主源是 Tushare `fund_share`，字段为
   `ts_code/trade_date/fd_share`，`fd_share` 单位为万份。
2. `fund_etf_hist_em`/Sina ETF 历史只能用于成交额确认，不能代理份额或申赎。
3. 2026-07-26 真实预检显示当前 token 没有 `fund_share` 权限；实现必须把 ETF
   项降级为 `unknown` 并披露原因，不能因此伪造净赎回或净申购。
4. 核心 ETF 必须严格使用 `configs/core_etfs.yaml` 的四只配置；任何一只份额
   缺失，聚合 ETF 状态均为 `unknown`。
5. 超大单周度值来自本地最近五个完整交易日 `flow_snapshots`；少一日即
   `unknown`，缺失值不能补零。
6. 板块当前排名使用 AkShare `stock_sector_fund_flow_rank(indicator="5日")`；
   上周排名只能读取上一期 `weekly_macro_flow` raw snapshot。
7. `weekly_macro_flow` 与旧 `weekly-report` 是两个独立产品。旧命令、文件名和
   标签不能被本计划替换。
8. 周末手动执行向前解析到最近中国交易日；同一截止日重复运行覆盖 raw
   snapshot 和报告，不追加。
9. 推送复用日报的 `PUSHPLUS_*`/`SMTP_*` 接收人；提供 `--no-push`。
10. 缺失数据只产生 `unknown` 和数据质量说明，不能变成 0、负向证据或默认值。

## 1. 文件结构

| 路径 | 责任 |
|---|---|
| `src/lurker/ingest/macro_flows.py` | raw snapshot 契约、ETF 份额/成交额、两融、基准、板块和本地超大单采集、snapshot 存取 |
| `src/lurker/application/weekly_macro_flow.py` | ETF、两融、超大单、板块和周度总状态的纯函数 |
| `src/lurker/reports/weekly_macro_flow_report.py` | `宏观资金周报` Markdown 渲染 |
| `src/lurker/application/strategy_runner.py` | 注册 `WeeklyMacroFlowStrategy`，传递本期/上期 macro snapshot |
| `src/lurker/cli.py` | `weekly-macro-flow` 命令、日期解析、采集、落盘、推送 |
| `configs/strategies.yaml` | 启用 `weekly_macro_flow`，cadence=`weekly` |
| `tests/test_macro_flows.py` | raw 契约和所有 provider normalizer/降级测试 |
| `tests/test_weekly_macro_flow.py` | 分类真值表和总体状态测试 |
| `tests/test_weekly_macro_flow_report.py` | 报告章节、来源和缺失披露测试 |
| `tests/test_strategy_runner.py` | 策略注册和 context 测试 |
| `tests/test_cli.py` | CLI、幂等落盘、周末解析、`--no-push` 测试 |

Raw snapshot 固定结构：

```json
{
  "schema_version": 1,
  "report_date": "2026-07-24",
  "week_start": "2026-07-20",
  "week_end": "2026-07-24",
  "generated_at": "2026-07-26T08:00:00+00:00",
  "etf_shares": {
    "configured_symbols": ["510300.SH", "510500.SH", "159915.SZ", "159361.SZ"],
    "items": [{
      "symbol": "510300.SH",
      "current_trade_date": "2026-07-24",
      "current_fd_share": 100.0,
      "previous_trade_date": "2026-07-17",
      "previous_fd_share": 99.0,
      "current_turnover_5d": 5000000000.0,
      "previous_turnover_5d": 4500000000.0,
      "current_close": 4.20,
      "previous_close": 4.15,
      "share_source": "tushare_fund_share",
      "turnover_source": "akshare_fund_etf_hist_sina",
      "availability": "fresh",
      "reason": null
    }],
    "failures": []
  },
  "margin": {
    "current_trade_date": "2026-07-24",
    "current_balance": 1900000000000.0,
    "previous_trade_date": "2026-07-17",
    "previous_balance": 1870000000000.0,
    "source": "akshare_jin10_margin_sh_sz",
    "availability": "fresh",
    "reason": null
  },
  "market_flow_days": [{
    "trade_date": "2026-07-20",
    "super_large_net_inflow": 1000000000.0,
    "source": "flow_snapshot",
    "availability": "fresh"
  }],
  "benchmark": {
    "symbol": "000300.SH",
    "start_trade_date": "2026-07-20",
    "start_close": 4600.0,
    "end_trade_date": "2026-07-24",
    "end_close": 4646.0,
    "source": "akshare_stock_zh_index_daily_sina",
    "availability": "fresh",
    "reason": null
  },
  "sectors": {
    "items": [{
      "name": "半导体",
      "category": "industry",
      "rank": 1,
      "return_5d": 0.03,
      "main_net_inflow_5d": 10000000000.0,
      "super_large_net_inflow_5d": 6000000000.0
    }],
    "source": "akshare_stock_sector_fund_flow_rank_5d",
    "availability": "fresh",
    "reason": null
  },
  "failures": []
}
```

---

### Task 1: Raw snapshot 契约、交易日窗口与存储

**Files:**
- Create: `src/lurker/ingest/macro_flows.py`
- Create: `tests/test_macro_flows.py`

- [ ] **Step 1: 写 snapshot 契约 RED 测试**

```python
def test_validate_weekly_macro_snapshot_rejects_unknown_schema():
    with pytest.raises(ValueError, match="schema_version"):
        validate_weekly_macro_snapshot({"schema_version": 2})


def test_validate_weekly_macro_snapshot_preserves_missing_as_none():
    snapshot = valid_macro_snapshot()
    snapshot["market_flow_days"][0]["super_large_net_inflow"] = None
    validated = validate_weekly_macro_snapshot(snapshot)
    assert validated["market_flow_days"][0]["super_large_net_inflow"] is None


def test_last_five_cn_sessions_excludes_weekends_and_2026_holidays():
    assert last_cn_sessions("2026-06-22", count=5) == [
        "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-22"
    ]


def test_load_latest_snapshot_before_never_loads_same_date(tmp_path):
    save_weekly_macro_snapshot(valid_macro_snapshot("2026-07-17"), tmp_path)
    save_weekly_macro_snapshot(valid_macro_snapshot("2026-07-24"), tmp_path)
    previous = load_latest_weekly_macro_snapshot_before(tmp_path, "2026-07-24")
    assert previous["report_date"] == "2026-07-17"
```

- [ ] **Step 2: 运行 RED**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_flows.py -v
```

Expected: import 失败，因为 `lurker.ingest.macro_flows` 尚不存在。

- [ ] **Step 3: 实现最小公开 API**

```python
SCHEMA_VERSION = 1


def last_cn_sessions(report_date: str, *, count: int) -> list[str]:
    cursor = date.fromisoformat(report_date)
    sessions: list[str] = []
    while len(sessions) < count:
        if is_cn_trading_day(cursor):
            sessions.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(sessions))


def previous_week_end_session(report_date: str) -> str:
    cursor = date.fromisoformat(report_date) - timedelta(days=7)
    while not is_cn_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor.isoformat()


def validate_weekly_macro_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported weekly macro schema_version")
    required = {
        "report_date", "week_start", "week_end", "generated_at",
        "etf_shares", "margin", "market_flow_days", "benchmark",
        "sectors", "failures",
    }
    missing = required - set(raw)
    if missing:
        raise ValueError(f"weekly macro snapshot missing {sorted(missing)}")
    date.fromisoformat(str(raw["report_date"]))
    date.fromisoformat(str(raw["week_start"]))
    date.fromisoformat(str(raw["week_end"]))
    if not isinstance(raw["market_flow_days"], list):
        raise ValueError("market_flow_days must be a list")
    return raw


def save_weekly_macro_snapshot(snapshot: dict[str, Any], directory: Path) -> Path:
    validated = validate_weekly_macro_snapshot(snapshot)
    path = directory / f"{validated['report_date']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_latest_weekly_macro_snapshot_before(
    directory: Path,
    report_date: str,
) -> dict[str, Any] | None:
    candidates = sorted(
        path for path in directory.glob("*.json")
        if path.stem < report_date
    )
    if not candidates:
        return None
    return validate_weekly_macro_snapshot(
        json.loads(candidates[-1].read_text(encoding="utf-8"))
    )
```

验证器还必须逐层拒绝：非有限数字、重复 ETF symbol、item 不属于
`configured_symbols`、日期倒置、非交易日和未知顶层字段。相应测试逐个先红后绿；
不能用 `float(value or 0)`。

- [ ] **Step 4: 运行 GREEN**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_flows.py -v
```

Expected: Task 1 测试全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/lurker/ingest/macro_flows.py tests/test_macro_flows.py
git commit -m "feat: add weekly macro raw snapshot contract"
```

---

### Task 2: ETF 真实份额与成交确认

**Files:**
- Modify: `src/lurker/ingest/macro_flows.py`
- Modify: `tests/test_macro_flows.py`

- [ ] **Step 1: 写 ETF RED 测试**

```python
def test_normalize_fund_share_uses_exact_week_end_points():
    raw = pd.DataFrame({
        "ts_code": ["510300.SH", "510300.SH"],
        "trade_date": ["20260724", "20260717"],
        "fd_share": [101.0, 100.0],
    })
    fact = normalize_fund_share_frame(
        raw,
        symbol="510300.SH",
        current_week_end="2026-07-24",
        previous_week_end="2026-07-17",
    )
    assert fact["current_fd_share"] == 101.0
    assert fact["previous_fd_share"] == 100.0
    assert fact["share_source"] == "tushare_fund_share"


def test_fund_share_permission_error_yields_unknown_not_turnover_proxy():
    batch = fetch_etf_share_facts(
        etf_configs=core_etf_configs(),
        report_date="2026-07-24",
        fund_share_fetcher=lambda **kwargs: raise_permission_error(),
        etf_history_fetcher=lambda **kwargs: valid_turnover_history(),
    )
    assert batch["items"] == []
    assert batch["configured_symbols"] == [
        "510300.SH", "510500.SH", "159915.SZ", "159361.SZ"
    ]
    assert "fund_share" in batch["failures"][0]["reason"]


def test_turnover_confirmation_requires_two_complete_five_day_windows():
    fact = normalize_etf_turnover_confirmation(
        valid_ten_session_history(),
        report_date="2026-07-24",
    )
    assert fact == {
        "current_turnover_5d": 600.0,
        "previous_turnover_5d": 500.0,
        "current_close": 12.0,
        "previous_close": 10.0,
        "turnover_source": "fixture",
    }


```

同时覆盖：

- `fd_share` 缺列、NaN、inf → Schema 错误，不能转 0。
- 当前/前周端点不是精确周末交易日 → 该 ETF unavailable。
- 四只中一只失败仍保留其他三只事实，但聚合层稍后必须返回 `unknown`。
- 主源网络异常可记录 provider failure；`TypeError`/`KeyError` 必须传播。
- 成交额窗口不足、重复日期、最新日期超出 report date → `unknown`/显式错误。

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_flows.py -k "fund_share or turnover" -v
```

Expected: FAIL，目标函数尚未定义。

- [ ] **Step 3: 实现 provider 边界**

公开签名：

```python
def normalize_fund_share_frame(
    raw: pd.DataFrame,
    *,
    symbol: str,
    current_week_end: str,
    previous_week_end: str,
) -> dict[str, Any]:
    required = {"ts_code", "trade_date", "fd_share"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"fund_share missing columns {sorted(missing)}")
    frame = raw.loc[raw["ts_code"].astype(str) == symbol, list(required)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["fd_share"] = pd.to_numeric(frame["fd_share"], errors="coerce")
    frame = frame.dropna().drop_duplicates("trade_date", keep=False)
    values = {
        row.trade_date.date().isoformat(): float(row.fd_share)
        for row in frame.itertuples()
        if math.isfinite(float(row.fd_share)) and float(row.fd_share) > 0
    }
    if current_week_end not in values or previous_week_end not in values:
        raise ValueError(f"{symbol}: fund_share missing exact week-end endpoint")
    return {
        "symbol": symbol,
        "current_trade_date": current_week_end,
        "current_fd_share": values[current_week_end],
        "previous_trade_date": previous_week_end,
        "previous_fd_share": values[previous_week_end],
        "share_source": "tushare_fund_share",
    }


def normalize_etf_turnover_confirmation(
    raw: pd.DataFrame,
    *,
    report_date: str,
) -> dict[str, Any]:
    required = {"日期", "成交额", "收盘"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"ETF turnover missing columns {sorted(missing)}")
    expected = last_cn_sessions(report_date, count=10)
    frame = raw.loc[:, ["日期", "成交额", "收盘"]].copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.date
    frame["成交额"] = pd.to_numeric(frame["成交额"], errors="coerce")
    frame["收盘"] = pd.to_numeric(frame["收盘"], errors="coerce")
    by_date = {
        day.isoformat(): (float(amount), float(close))
        for day, amount, close in zip(
            frame["日期"], frame["成交额"], frame["收盘"], strict=True
        )
        if (
            pd.notna(day) and pd.notna(amount) and pd.notna(close)
            and math.isfinite(float(amount)) and math.isfinite(float(close))
        )
    }
    previous_week_end = previous_week_end_session(report_date)
    if set(expected) - set(by_date) or previous_week_end not in by_date:
        raise ValueError("ETF turnover requires two complete five-session windows")
    return {
        "current_turnover_5d": sum(by_date[day][0] for day in expected[5:]),
        "previous_turnover_5d": sum(by_date[day][0] for day in expected[:5]),
        "current_close": by_date[report_date][1],
        "previous_close": by_date[previous_week_end][1],
        "turnover_source": str(raw.attrs.get("source", "injected_etf_history")),
    }


def fetch_etf_share_facts(
    *,
    etf_configs: list[dict[str, str]],
    report_date: str,
    fund_share_fetcher: Callable[..., pd.DataFrame] | None = None,
    etf_history_fetcher: Callable[..., pd.DataFrame] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    configured = [row["canonical_symbol"] for row in etf_configs]
    items: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    share_fetcher = fund_share_fetcher or build_tushare_fund_share_fetcher(token)
    turnover_fetcher = etf_history_fetcher or fetch_etf_turnover_history
    previous_week_end = previous_week_end_session(report_date)
    for config in etf_configs:
        symbol = config["canonical_symbol"]
        try:
            share = normalize_fund_share_frame(
                share_fetcher(
                    ts_code=symbol,
                    start_date=(date.fromisoformat(previous_week_end) - timedelta(days=7)).strftime("%Y%m%d"),
                    end_date=date.fromisoformat(report_date).strftime("%Y%m%d"),
                ),
                symbol=symbol,
                current_week_end=report_date,
                previous_week_end=previous_week_end,
            )
            turnover = normalize_etf_turnover_confirmation(
                turnover_fetcher(config=config, report_date=report_date),
                report_date=report_date,
            )
            items.append({**share, **turnover, "availability": "fresh", "reason": None})
        except RECOVERABLE_PROVIDER_ERRORS as exc:
            failures.append({"symbol": symbol, "reason": f"{type(exc).__name__}: {exc}"})
    return {
        "configured_symbols": configured,
        "items": items,
        "failures": failures,
    }
```

实现规则：

```python
share_change = current_fd_share / previous_fd_share - 1.0
turnover_change = current_turnover_5d / previous_turnover_5d - 1.0
```

raw snapshot 不保存 `share_change`/`turnover_change`，只保存可复算的两端事实。
默认 `fund_share_fetcher` 调用：

```python
ts.pro_api(token).fund_share(
    ts_code=canonical_symbol,
    start_date=query_start.strftime("%Y%m%d"),
    end_date=report_day.strftime("%Y%m%d"),
)
```

`build_tushare_fund_share_fetcher()` 必须把 Tushare 的通用
`Exception("没有接口(fund_share)访问权限...")` 识别并转换为
`MacroProviderError`；网络错误也转换为同一 provider 异常。`TypeError`、
`KeyError`、DataFrame Schema 错误不得转换。将 `MacroProviderError` 纳入
`RECOVERABLE_PROVIDER_ERRORS`。

默认成交额/收盘价主源调用 `ak.fund_etf_hist_em`，空表或可恢复网络异常时调用
`ak.fund_etf_hist_sina`。宏观 adapter 必须保留 `日期/成交额/收盘` 三列；
不能直接复用会丢弃 close 的两列 turnover normalizer。

- [ ] **Step 4: 运行 GREEN**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_flows.py -k "fund_share or turnover" -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/lurker/ingest/macro_flows.py tests/test_macro_flows.py
git commit -m "feat: collect real weekly ETF share facts"
```

---

### Task 3: 五日超大单与沪深300基准

**Files:**
- Modify: `src/lurker/ingest/macro_flows.py`
- Modify: `tests/test_macro_flows.py`

- [ ] **Step 1: 写 RED**

```python
def test_load_market_flow_week_requires_exact_five_sessions(tmp_path):
    write_flow_snapshots(tmp_path, dates=["2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"])
    result = load_market_flow_week(tmp_path, report_date="2026-07-24")
    assert result["availability"] == "unknown"
    assert result["missing_dates"] == ["2026-07-20"]
    assert result["items"] == []


def test_market_flow_week_preserves_real_zero():
    result = load_market_flow_week(
        complete_flow_snapshot_dir(super_large_values=[1.0, -1.0, 0.0, 2.0, -2.0]),
        report_date="2026-07-24",
    )
    assert result["items"][2]["super_large_net_inflow"] == 0.0


def test_benchmark_uses_five_session_endpoints():
    result = normalize_benchmark_history(valid_index_history(), report_date="2026-07-24")
    assert result["start_trade_date"] == "2026-07-20"
    assert result["end_trade_date"] == "2026-07-24"
```

- [ ] **Step 2: 确认 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_flows.py -k "market_flow_week or benchmark" -v
```

- [ ] **Step 3: 实现**

```python
def load_market_flow_week(
    flow_snapshot_dir: Path,
    *,
    report_date: str,
) -> dict[str, Any]:
    expected = last_cn_sessions(report_date, count=5)
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for trade_date in expected:
        path = flow_snapshot_dir / f"{trade_date}.json"
        if not path.exists():
            missing.append(trade_date)
            continue
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        fact = snapshot.get("market_flow", {})
        value = optional_finite_float(fact.get("super_large_net_inflow"))
        if fact.get("trade_date") != trade_date or value is None:
            missing.append(trade_date)
            continue
        items.append({
            "trade_date": trade_date,
            "super_large_net_inflow": value,
            "source": "flow_snapshot",
            "availability": "fresh",
        })
    return {
        "items": items if not missing else [],
        "missing_dates": missing,
        "availability": "fresh" if not missing else "unknown",
    }


def normalize_benchmark_history(
    raw: pd.DataFrame,
    *,
    report_date: str,
) -> dict[str, Any]:
    required = {"date", "close"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"benchmark history missing columns {sorted(missing)}")
    expected = last_cn_sessions(report_date, count=5)
    frame = raw.loc[:, ["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    by_date = {
        day.isoformat(): float(close)
        for day, close in zip(frame["date"], frame["close"], strict=True)
        if pd.notna(day) and pd.notna(close) and math.isfinite(float(close))
    }
    if set(expected) - set(by_date):
        raise ValueError("benchmark requires five complete sessions")
    return {
        "symbol": "000300.SH",
        "start_trade_date": expected[0],
        "start_close": by_date[expected[0]],
        "end_trade_date": expected[-1],
        "end_close": by_date[expected[-1]],
        "source": "akshare_stock_zh_index_daily_sina",
        "availability": "fresh",
        "reason": None,
    }


def fetch_benchmark_fact(
    *,
    report_date: str,
    fetcher: Callable[..., pd.DataFrame] | None = None,
) -> dict[str, Any]:
    resolved = fetcher or default_benchmark_fetcher
    return normalize_benchmark_history(
        resolved(symbol="sh000300"),
        report_date=report_date,
    )
```

默认基准：

```python
with _akshare_request_scope():
    raw = ak.stock_zh_index_daily(symbol="sh000300")
```

`load_market_flow_week` 必须同时验证文件名日期、snapshot 内
`market_flow.trade_date` 和目标交易日一致；字段缺失/非有限时整个周度项
`unknown`，不能只把坏日剔除后继续求和。

- [ ] **Step 4: GREEN**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_flows.py -k "market_flow_week or benchmark" -v
```

- [ ] **Step 5: 提交**

```bash
git add src/lurker/ingest/macro_flows.py tests/test_macro_flows.py
git commit -m "feat: collect strict five-session macro flow facts"
```

---

### Task 4: 两融周度事实与板块 5 日排名

**Files:**
- Modify: `src/lurker/ingest/macro_flows.py`
- Modify: `tests/test_macro_flows.py`

- [ ] **Step 1: 写 RED**

```python
def test_margin_week_uses_tushare_exact_endpoints():
    fact = normalize_margin_week(
        valid_margin_history(),
        current_week_end="2026-07-24",
        previous_week_end="2026-07-17",
        source="tushare_margin",
    )
    assert fact["current_balance"] == 1900.0
    assert fact["previous_balance"] == 1870.0


def test_margin_permission_error_uses_auditable_akshare_fallback():
    fact = fetch_margin_week_fact(
        report_date="2026-07-24",
        tushare_fetcher=lambda **kwargs: raise_permission_error(),
        akshare_fetcher=lambda: valid_combined_margin_history(),
    )
    assert fact["source"] == "akshare_jin10_margin_sh_sz"


def test_sector_normalizer_rejects_missing_five_day_columns():
    with pytest.raises(ValueError, match="5日主力净流入-净额"):
        normalize_sector_week(pd.DataFrame({"名称": ["半导体"]}), category="industry")
```

还要覆盖行业和概念两类、rank 从 1 开始、`5日涨跌幅` 百分数转小数、
NaN/inf 不得变 0、一个 category 失败时 failures 中保留 category。

- [ ] **Step 2: 确认 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_flows.py -k "margin_week or sector" -v
```

- [ ] **Step 3: 实现**

公开签名：

```python
def normalize_margin_week(
    raw: pd.DataFrame,
    *,
    current_week_end: str,
    previous_week_end: str,
    source: str,
) -> dict[str, Any]:
    required = {"trade_date", "margin_balance"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"margin history missing columns {sorted(missing)}")
    frame = raw.loc[:, ["trade_date", "margin_balance"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["margin_balance"] = pd.to_numeric(frame["margin_balance"], errors="coerce")
    by_date = {
        day.date().isoformat(): float(balance)
        for day, balance in zip(frame["trade_date"], frame["margin_balance"], strict=True)
        if pd.notna(day) and pd.notna(balance) and math.isfinite(float(balance))
    }
    if current_week_end not in by_date or previous_week_end not in by_date:
        raise ValueError("margin history missing exact week-end endpoint")
    return {
        "current_trade_date": current_week_end,
        "current_balance": by_date[current_week_end],
        "previous_trade_date": previous_week_end,
        "previous_balance": by_date[previous_week_end],
        "source": source,
        "availability": "fresh",
        "reason": None,
    }


def fetch_margin_week_fact(
    *,
    report_date: str,
    tushare_fetcher: Callable[..., pd.DataFrame] | None = None,
    akshare_fetcher: Callable[[], pd.DataFrame] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    current = report_date
    previous = previous_week_end_session(report_date)
    try:
        raw = (tushare_fetcher or build_tushare_margin_fetcher(token))(
            start_date=date.fromisoformat(previous).strftime("%Y%m%d"),
            end_date=date.fromisoformat(current).strftime("%Y%m%d"),
        )
        return normalize_margin_week(
            normalize_tushare_margin_history(raw),
            current_week_end=current,
            previous_week_end=previous,
            source="tushare_margin",
        )
    except RECOVERABLE_PROVIDER_ERRORS:
        raw = (akshare_fetcher or fetch_akshare_margin_history)()
        return normalize_margin_week(
            raw,
            current_week_end=current,
            previous_week_end=previous,
            source="akshare_jin10_margin_sh_sz",
        )


def normalize_sector_week(
    raw: pd.DataFrame,
    *,
    category: str,
) -> list[dict[str, Any]]:
    required = {
        "名称", "5日涨跌幅", "5日主力净流入-净额",
        "5日超大单净流入-净额",
    }
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"sector week missing columns {sorted(missing)}")
    items: list[dict[str, Any]] = []
    for rank, (_, values) in enumerate(raw.iterrows(), start=1):
        items.append({
            "name": str(values["名称"]).strip(),
            "category": category,
            "rank": rank,
            "return_5d": required_finite_float(values["5日涨跌幅"]) / 100.0,
            "main_net_inflow_5d": required_finite_float(
                values["5日主力净流入_净额"]
            ),
            "super_large_net_inflow_5d": required_finite_float(
                values["5日超大单净流入_净额"]
            ),
        })
    return items


def fetch_sector_week_facts(
    *,
    fetcher: Callable[..., pd.DataFrame] | None = None,
) -> dict[str, Any]:
    resolved = fetcher or default_sector_fetcher
    items: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for category, sector_type in (
        ("industry", "行业资金流"),
        ("concept", "概念资金流"),
    ):
        try:
            items.extend(normalize_sector_week(
                resolved(indicator="5日", sector_type=sector_type),
                category=category,
            ))
        except RECOVERABLE_PROVIDER_ERRORS as exc:
            failures.append({"category": category, "reason": str(exc)})
    return {
        "items": items,
        "source": "akshare_stock_sector_fund_flow_rank_5d",
        "availability": "fresh" if not failures else "unknown",
        "reason": None if not failures else "; ".join(row["reason"] for row in failures),
    }
```

板块默认调用两次：

```python
ak.stock_sector_fund_flow_rank(indicator="5日", sector_type="行业资金流")
ak.stock_sector_fund_flow_rank(indicator="5日", sector_type="概念资金流")
```

两融 fallback 只捕获网络/权限类 provider 错误；`TypeError`、`KeyError`、
返回非 DataFrame 直接抛出。
`build_tushare_margin_fetcher()` 与 ETF 份额 adapter 使用同一个
`MacroProviderError` 规则：只有网络、token、积分、无接口权限消息可恢复。

- [ ] **Step 4: GREEN**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_flows.py -k "margin_week or sector" -v
```

- [ ] **Step 5: 实现总采集入口并测试**

```python
def collect_weekly_macro_snapshot(
    *,
    report_date: str,
    etf_configs: list[dict[str, str]],
    flow_snapshot_dir: Path,
    generated_at: str | None = None,
    fund_share_fetcher: Callable[..., pd.DataFrame] | None = None,
    etf_history_fetcher: Callable[..., pd.DataFrame] | None = None,
    margin_tushare_fetcher: Callable[..., pd.DataFrame] | None = None,
    margin_akshare_fetcher: Callable[[], pd.DataFrame] | None = None,
    benchmark_fetcher: Callable[..., pd.DataFrame] | None = None,
    sector_fetcher: Callable[..., pd.DataFrame] | None = None,
) -> dict[str, Any]:
    sessions = last_cn_sessions(report_date, count=5)
    failures: list[dict[str, str]] = []

    def capture(
        source: str,
        operation: Callable[[], dict[str, Any]],
        unknown: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return operation()
        except RECOVERABLE_PROVIDER_ERRORS as exc:
            reason = f"{type(exc).__name__}: {exc}"
            failures.append({"source": source, "reason": reason})
            return {**unknown, "reason": reason}

    market_flow = load_market_flow_week(
        flow_snapshot_dir,
        report_date=report_date,
    )
    if market_flow["availability"] != "fresh":
        failures.append({
            "source": "market_flow_days",
            "reason": f"missing dates: {market_flow['missing_dates']}",
        })
    margin = capture(
        "margin",
        lambda: fetch_margin_week_fact(
            report_date=report_date,
            tushare_fetcher=margin_tushare_fetcher,
            akshare_fetcher=margin_akshare_fetcher,
        ),
        {
            "current_trade_date": None,
            "current_balance": None,
            "previous_trade_date": None,
            "previous_balance": None,
            "source": "unavailable",
            "availability": "unknown",
        },
    )
    benchmark = capture(
        "benchmark",
        lambda: fetch_benchmark_fact(
            report_date=report_date,
            fetcher=benchmark_fetcher,
        ),
        {
            "symbol": "000300.SH",
            "start_trade_date": None,
            "start_close": None,
            "end_trade_date": None,
            "end_close": None,
            "source": "unavailable",
            "availability": "unknown",
        },
    )
    sectors = capture(
        "sectors",
        lambda: fetch_sector_week_facts(fetcher=sector_fetcher),
        {
            "items": [],
            "source": "unavailable",
            "availability": "unknown",
        },
    )
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "report_date": report_date,
        "week_start": sessions[0],
        "week_end": sessions[-1],
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "etf_shares": fetch_etf_share_facts(
            etf_configs=etf_configs,
            report_date=report_date,
            fund_share_fetcher=fund_share_fetcher,
            etf_history_fetcher=etf_history_fetcher,
        ),
        "margin": margin,
        "market_flow_days": market_flow["items"],
        "benchmark": benchmark,
        "sectors": sectors,
        "failures": failures,
    }
    return validate_weekly_macro_snapshot(snapshot)
```

测试断言每个 provider 的失败只影响对应 section，且 snapshot 始终通过
`validate_weekly_macro_snapshot()`；程序错误不得进入 failures 后静默继续。

- [ ] **Step 6: 提交**

```bash
git add src/lurker/ingest/macro_flows.py tests/test_macro_flows.py
git commit -m "feat: complete weekly macro fact collection"
```

---

### Task 5: 周度信号纯函数与总状态真值表

**Files:**
- Create: `src/lurker/application/weekly_macro_flow.py`
- Create: `tests/test_weekly_macro_flow.py`

- [ ] **Step 1: 写分类 RED 测试**

```python
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ([0.006, 0.007, 0.008, -0.001], "净申购"),
        ([-0.006, -0.007, -0.008, 0.001], "净赎回"),
        ([0.001, -0.001, 0.0, 0.002], "份额平稳"),
        ([0.006, 0.007, -0.006, -0.007], "分化"),
    ],
)
def test_classify_etf_week(changes, expected):
    assert classify_etf_week(etf_items_from_changes(changes)) == expected


def test_classify_etf_week_requires_all_four_symbols():
    assert classify_etf_week(etf_items_from_changes([0.01, 0.01, 0.01])) == "unknown"


def test_etf_price_week_requires_all_four_exact_close_endpoints():
    items = etf_items_from_price_changes([0.01, 0.02, 0.03, -0.01])
    assert classify_etf_price_week(items) == "上涨"


def test_flat_shares_with_rising_prices_is_existing_money_game():
    assert classify_etf_trend("份额平稳", "份额平稳", "上涨") == "存量博弈"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (0.051, "过热预警"),
        (0.010, "健康上升"),
        (0.009, "盘整"),
        (-0.030, "盘整"),
        (-0.031, "恐慌出清"),
    ],
)
def test_classify_margin_week(change, expected):
    assert classify_margin_week(margin_fact_from_change(change)) == expected


def test_classify_market_flow_price_divergence():
    result = classify_market_flow_week(
        five_day_flows(total=10.0),
        benchmark_fact(return_5d=0.001),
    )
    assert result == "聪明钱进场，价格滞后"
```

边界明确：

- ETF 死区包含 `[-0.005, 0.005]`。
- 指数平包含 `[-0.005, 0.005]`。
- 两融 `1%` 属于健康上升，`5%` 属于健康上升；`-3%` 属于盘整。
- 任一输入缺失/非有限 → 对应信号 `unknown`。
- 成交确认只是 `expanded/not_expanded/unknown` 注释，不改 ETF 方向。

- [ ] **Step 2: RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_weekly_macro_flow.py -v
```

- [ ] **Step 3: 实现分类 API**

```python
ETF_DEADBAND = 0.005
INDEX_FLAT_BAND = 0.005


def classify_etf_week(items: list[dict[str, Any]]) -> str:
    symbols = {str(item.get("symbol", "")) for item in items}
    if len(items) != 4 or len(symbols) != 4:
        return "unknown"
    changes: list[float] = []
    for item in items:
        current = optional_finite_float(item.get("current_fd_share"))
        previous = optional_finite_float(item.get("previous_fd_share"))
        if current is None or previous is None or current <= 0 or previous <= 0:
            return "unknown"
        changes.append(current / previous - 1.0)
    positive = sum(change > ETF_DEADBAND for change in changes)
    negative = sum(change < -ETF_DEADBAND for change in changes)
    flat = sum(-ETF_DEADBAND <= change <= ETF_DEADBAND for change in changes)
    if positive >= 3:
        return "净申购"
    if negative >= 3:
        return "净赎回"
    if flat == 4:
        return "份额平稳"
    return "分化"


def classify_etf_price_week(items: list[dict[str, Any]]) -> str:
    if len(items) != 4 or len({item.get("symbol") for item in items}) != 4:
        return "unknown"
    changes: list[float] = []
    for item in items:
        current = optional_finite_float(item.get("current_close"))
        previous = optional_finite_float(item.get("previous_close"))
        if current is None or previous is None or current <= 0 or previous <= 0:
            return "unknown"
        changes.append(current / previous - 1.0)
    return "上涨" if sum(change > ETF_DEADBAND for change in changes) >= 3 else "未上涨"


def classify_etf_trend(
    current_status: str,
    previous_status: str,
    price_status: str,
) -> str:
    if current_status == previous_status == "净申购":
        return "持续进场"
    if current_status == previous_status == "净赎回":
        return "持续撤退"
    if current_status == "净赎回":
        return "资金撤退"
    if current_status == "份额平稳" and price_status == "上涨":
        return "存量博弈"
    if "unknown" in {current_status, previous_status}:
        return "unknown"
    return "未形成连续方向"


def classify_turnover_confirmation(item: dict[str, Any]) -> str:
    current = optional_finite_float(item.get("current_turnover_5d"))
    previous = optional_finite_float(item.get("previous_turnover_5d"))
    if current is None or previous is None or current < 0 or previous <= 0:
        return "unknown"
    return "expanded" if current > previous else "not_expanded"


def classify_margin_week(margin: dict[str, Any]) -> str:
    current = optional_finite_float(margin.get("current_balance"))
    previous = optional_finite_float(margin.get("previous_balance"))
    if current is None or previous is None or current < 0 or previous <= 0:
        return "unknown"
    change = current / previous - 1.0
    if change > 0.05:
        return "过热预警"
    if change >= 0.01:
        return "健康上升"
    if change < -0.03:
        return "恐慌出清"
    return "盘整"


def classify_market_flow_week(
    market_flow_days: list[dict[str, Any]],
    benchmark: dict[str, Any],
) -> str:
    if len(market_flow_days) != 5:
        return "unknown"
    flows = [
        optional_finite_float(item.get("super_large_net_inflow"))
        for item in market_flow_days
    ]
    start = optional_finite_float(benchmark.get("start_close"))
    end = optional_finite_float(benchmark.get("end_close"))
    if any(value is None for value in flows) or start is None or end is None or start <= 0:
        return "unknown"
    total = sum(cast(float, value) for value in flows)
    market_return = end / start - 1.0
    if total > 0 and abs(market_return) <= INDEX_FLAT_BAND:
        return "聪明钱进场，价格滞后"
    if total < 0 and market_return > INDEX_FLAT_BAND:
        return "散户拉盘，主力减仓"
    if total > 0 and market_return > INDEX_FLAT_BAND:
        return "共振进攻"
    if total < 0 and market_return < -INDEX_FLAT_BAND:
        return "共振退潮"
    return "分化"


def classify_sector_continuity(
    current_items: list[dict[str, Any]],
    previous_items: list[dict[str, Any]],
    *,
    benchmark_return: float | None,
) -> list[dict[str, Any]]:
    current = {(row["category"], row["name"]): row for row in current_items}
    previous = {(row["category"], row["name"]): row for row in previous_items}
    results: list[dict[str, Any]] = []
    for key in sorted(set(current) | set(previous)):
        now = current.get(key)
        before = previous.get(key)
        now_top = now is not None and int(now["rank"]) <= 5
        before_top = before is not None and int(before["rank"]) <= 5
        if now_top and before_top:
            continuity = "持续主线"
        elif now_top:
            continuity = "新兴热点"
        elif before_top:
            continuity = "热点退潮"
        else:
            continuity = "普通"
        relative_note = None
        if (
            now is not None
            and benchmark_return is not None
            and float(now["main_net_inflow_5d"]) > 0
            and float(now["return_5d"]) < benchmark_return
        ):
            relative_note = "资金滞留，吸筹"
        results.append({
            **(now or before),
            "continuity": continuity,
            "relative_note": relative_note,
        })
    return results


def classify_weekly_market_state(
    *,
    etf_status: str,
    previous_etf_status: str,
    margin_status: str,
    market_flow_status: str,
    mainline_clear: bool,
) -> str:
    flow_negative = market_flow_status in {"散户拉盘，主力减仓", "共振退潮"}
    flow_positive = market_flow_status in {"共振进攻", "聪明钱进场，价格滞后"}
    if margin_status == "恐慌出清" and etf_status == "净赎回" and flow_negative:
        return "恐慌区间"
    if (
        margin_status == "过热预警"
        or etf_status == previous_etf_status == "净赎回"
    ):
        return "风险上升"
    if (
        etf_status == "净申购"
        and margin_status == "健康上升"
        and flow_positive
        and mainline_clear
    ):
        return "积极进场"
    return "谨慎观望"


def analyze_weekly_macro_flow(
    *,
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    current_etf = classify_etf_week(snapshot["etf_shares"]["items"])
    etf_price = classify_etf_price_week(snapshot["etf_shares"]["items"])
    previous_etf = (
        classify_etf_week(previous_snapshot["etf_shares"]["items"])
        if previous_snapshot is not None
        else "unknown"
    )
    margin = classify_margin_week(snapshot["margin"])
    market_flow = classify_market_flow_week(
        snapshot["market_flow_days"],
        snapshot["benchmark"],
    )
    start = optional_finite_float(snapshot["benchmark"].get("start_close"))
    end = optional_finite_float(snapshot["benchmark"].get("end_close"))
    benchmark_return = (
        end / start - 1.0
        if start is not None and end is not None and start > 0
        else None
    )
    sectors = classify_sector_continuity(
        snapshot["sectors"]["items"],
        previous_snapshot["sectors"]["items"] if previous_snapshot else [],
        benchmark_return=benchmark_return,
    )
    mainline_clear = sum(
        row["continuity"] in {"持续主线", "新兴热点"}
        and int(row.get("rank", 999)) <= 5
        for row in sectors
    ) >= 2
    market_state = classify_weekly_market_state(
        etf_status=current_etf,
        previous_etf_status=previous_etf,
        margin_status=margin,
        market_flow_status=market_flow,
        mainline_clear=mainline_clear,
    )
    return {
        "market_state": market_state,
        "etf_status": current_etf,
        "previous_etf_status": previous_etf,
        "etf_price_status": etf_price,
        "etf_trend": classify_etf_trend(current_etf, previous_etf, etf_price),
        "margin_status": margin,
        "market_flow_status": market_flow,
        "sectors": sectors,
        "quality_notes": quality_notes_from_snapshot(snapshot),
    }
```

板块每行同时输出两个字段，避免日报/旧周报标签混用：

```python
{
    "continuity": "持续主线" | "新兴热点" | "热点退潮" | "普通",
    "relative_note": "资金滞留，吸筹" | None,
}
```

- [ ] **Step 4: 写总状态 RED**

```python
@pytest.mark.parametrize(
    ("etf", "previous_etf", "margin", "flow", "mainline", "expected"),
    [
        ("净申购", "净申购", "健康上升", "共振进攻", True, "积极进场"),
        ("净赎回", "净赎回", "盘整", "散户拉盘，主力减仓", False, "风险上升"),
        ("份额平稳", "份额平稳", "过热预警", "共振进攻", True, "风险上升"),
        ("净赎回", "分化", "恐慌出清", "散户拉盘，主力减仓", False, "恐慌区间"),
        ("unknown", "unknown", "unknown", "unknown", False, "谨慎观望"),
    ],
)
def test_classify_weekly_market_state(
    etf, previous_etf, margin, flow, mainline, expected
):
    assert classify_weekly_market_state(
        etf_status=etf,
        previous_etf_status=previous_etf,
        margin_status=margin,
        market_flow_status=flow,
        mainline_clear=mainline,
    ) == expected
```

优先级必须固定：

1. `恐慌区间`：两融恐慌 + ETF 净赎回 + 超大单净流出。
2. `风险上升`：两融过热，或 ETF 连续两周净赎回。
3. `积极进场`：ETF 净申购 + 超大单正向 + 两融健康 + 至少两个持续/新兴前五板块。
4. 其他全部 `谨慎观望`。

- [ ] **Step 5: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_weekly_macro_flow.py -v
git add src/lurker/application/weekly_macro_flow.py tests/test_weekly_macro_flow.py
git commit -m "feat: classify weekly macro flow state"
```

---

### Task 6: 宏观资金周报渲染

**Files:**
- Create: `src/lurker/reports/weekly_macro_flow_report.py`
- Create: `tests/test_weekly_macro_flow_report.py`

- [ ] **Step 1: 写报告 RED**

```python
def test_report_has_all_required_sections():
    report = render_weekly_macro_flow_report(
        snapshot=complete_snapshot(),
        previous_snapshot=previous_complete_snapshot(),
    )
    for heading in [
        "# 宏观资金周报",
        "## 一句话结论",
        "## ETF 资金动向",
        "## 两融余额状态",
        "## 超大单周度信号",
        "## 板块强弱排序",
        "## 数据质量",
    ]:
        assert heading in report.content_md


def test_report_discloses_fund_share_permission_failure():
    report = render_weekly_macro_flow_report(
        snapshot=snapshot_with_fund_share_permission_failure(),
        previous_snapshot=None,
    )
    assert "ETF 份额：unknown" in report.content_md
    assert "fund_share 无访问权限" in report.content_md
    assert "成交额不能替代份额" in report.content_md
```

还要覆盖真实来源、四只 ETF 每只变化、成交确认偏弱、五日缺一日、
上期 snapshot 缺失、板块持续/新兴/退潮、所有数值格式。

- [ ] **Step 2: RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_weekly_macro_flow_report.py -v
```

- [ ] **Step 3: 实现**

```python
def render_weekly_macro_flow_report(
    *,
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None,
) -> DailyReport:
    analysis = analyze_weekly_macro_flow(
        snapshot=snapshot,
        previous_snapshot=previous_snapshot,
    )
    return DailyReport(
        report_date=snapshot["report_date"],
        main_candidates_count=0,
        content_md=rendered_markdown,
    )
```

`analyze_weekly_macro_flow()` 放在 application 模块，返回完整字典：
`market_state/etf_status/etf_trend/margin_status/market_flow_status/sectors/quality_notes`。
renderer 不重新分类。

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_weekly_macro_flow_report.py tests/test_weekly_macro_flow.py -v
git add src/lurker/reports/weekly_macro_flow_report.py tests/test_weekly_macro_flow_report.py src/lurker/application/weekly_macro_flow.py
git commit -m "feat: render independent weekly macro flow report"
```

---

### Task 7: 策略注册与配置

**Files:**
- Modify: `src/lurker/application/strategy_runner.py`
- Modify: `configs/strategies.yaml`
- Modify: `tests/test_strategy_runner.py`

- [ ] **Step 1: 写 RED**

```python
def test_weekly_macro_flow_strategy_is_registered():
    assert "weekly_macro_flow" in DEFAULT_STRATEGIES


def test_weekly_macro_flow_strategy_requires_macro_snapshot():
    context = StrategyContext(
        snapshot_batch={"snapshots": []},
        theme_mapping={},
        report_date="2026-07-24",
        attributor=None,
        suppressed_symbols=set(),
    )
    with pytest.raises(ValueError, match="macro_flow_snapshot"):
        DEFAULT_STRATEGIES["weekly_macro_flow"].run(
            context,
            StrategyConfig(name="weekly_macro_flow", cadence="weekly"),
        )
```

- [ ] **Step 2: RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_strategy_runner.py -v
```

- [ ] **Step 3: 扩展 context 并注册**

```python
@dataclass
class StrategyContext:
    snapshot_batch: dict[str, Any]
    theme_mapping: dict[str, list[str]]
    report_date: str | None
    attributor: Any
    suppressed_symbols: set[str]
    flow_snapshot: dict[str, Any] | None = None
    macro_flow_snapshot: dict[str, Any] | None = None
    previous_macro_flow_snapshot: dict[str, Any] | None = None
    symbol_names: dict[str, str] = field(default_factory=dict)
    runtime_params: dict[str, Any] = field(default_factory=dict)
    db_session: Any = None


class WeeklyMacroFlowStrategy:
    name = "weekly_macro_flow"

    def run(self, context: StrategyContext, config: StrategyConfig) -> StrategyResult:
        if context.macro_flow_snapshot is None:
            raise ValueError("weekly_macro_flow requires macro_flow_snapshot")
        report = render_weekly_macro_flow_report(
            snapshot=context.macro_flow_snapshot,
            previous_snapshot=context.previous_macro_flow_snapshot,
        )
        return StrategyResult(
            name=self.name,
            title=config.title or "宏观资金周报",
            report=report,
            metadata={"cadence": config.cadence, "universe": config.universe},
        )
```

配置：

```yaml
  weekly_macro_flow:
    enabled: true
    cadence: weekly
    universe: macro_market
    title: 宏观资金周报
    params:
      etf_deadband: 0.005
      index_flat_band: 0.005
      sector_limit: 10
```

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_strategy_runner.py -v
git add src/lurker/application/strategy_runner.py configs/strategies.yaml tests/test_strategy_runner.py
git commit -m "feat: register weekly macro flow strategy"
```

---

### Task 8: 独立 CLI、落盘、推送和周末手动运行

**Files:**
- Modify: `src/lurker/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写 CLI RED**

```python
def test_parser_has_weekly_macro_flow_command():
    args = build_parser().parse_args(["weekly-macro-flow", "--no-push"])
    assert args.command == "weekly-macro-flow"
    assert args.no_push is True


def test_weekly_macro_flow_job_resolves_saturday_to_friday(tmp_path):
    message = weekly_macro_flow_job(
        report_date="2026-07-25",
        snapshot_dir=tmp_path / "macro",
        flow_snapshot_dir=complete_flow_dir(tmp_path),
        report_dir=tmp_path / "reports",
        strategy_config_path=fixture_strategy_config(tmp_path),
        core_etfs_path=fixture_core_etfs(tmp_path),
        push=False,
        snapshot_collector=fake_complete_collector,
    )
    assert (tmp_path / "macro" / "2026-07-24.json").exists()
    assert (tmp_path / "reports" / "2026-07-24.md").exists()
    assert "resolved 2026-07-25 to 2026-07-24" in message


def test_weekly_macro_flow_job_no_push_never_builds_notifier(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lurker.cli.build_notifier_from_env",
        lambda: pytest.fail("notifier must not be built"),
    )
    weekly_macro_flow_job(
        report_date="2026-07-24",
        snapshot_dir=tmp_path / "macro",
        flow_snapshot_dir=complete_flow_dir(tmp_path),
        report_dir=tmp_path / "reports",
        strategy_config_path=fixture_strategy_config(tmp_path),
        core_etfs_path=fixture_core_etfs(tmp_path),
        push=False,
        snapshot_collector=fake_complete_collector,
    )
```

同时测试：

- 重跑同一日期只保留一个 raw JSON 和一个 Markdown。
- 上一期 snapshot 必须严格早于当前日期。
- 配置未注册/禁用时明确失败，不生成伪报告。
- 推送标题为 `Lurker 宏观资金周报 (YYYY-MM-DD)`。
- provider 全部 unknown 时仍落盘并可 `--no-push` 演练。
- 旧 `weekly-report` parser 和函数测试保持原样通过。

- [ ] **Step 2: RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py -k "weekly_macro_flow" -v
```

- [ ] **Step 3: 实现 job 和 parser**

```python
def resolve_latest_cn_session(value: str) -> str:
    cursor = date.fromisoformat(value)
    while not is_cn_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor.isoformat()


def weekly_macro_flow_job(
    *,
    report_date: str | None,
    snapshot_dir: Path,
    flow_snapshot_dir: Path,
    report_dir: Path,
    strategy_config_path: Path,
    core_etfs_path: Path,
    push: bool,
    snapshot_collector=collect_weekly_macro_snapshot,
) -> str:
    requested = report_date or date.today().isoformat()
    resolved = resolve_latest_cn_session(requested)
    previous = load_latest_weekly_macro_snapshot_before(snapshot_dir, resolved)
    snapshot = snapshot_collector(
        report_date=resolved,
        etf_configs=load_core_etfs(core_etfs_path),
        flow_snapshot_dir=flow_snapshot_dir,
    )
    snapshot_path = save_weekly_macro_snapshot(snapshot, snapshot_dir)
    configured = load_strategy_configs(strategy_config_path)
    config = configured.get("weekly_macro_flow")
    if config is None or not config.enabled or config.cadence != "weekly":
        raise ValueError("weekly_macro_flow strategy is not enabled for weekly cadence")
    context = StrategyContext(
        snapshot_batch={"snapshots": []},
        theme_mapping={},
        report_date=resolved,
        attributor=None,
        suppressed_symbols=set(),
        macro_flow_snapshot=snapshot,
        previous_macro_flow_snapshot=previous,
    )
    report = run_strategies(context=context, configs=[config])[0].report
    report_path = report_dir / f"{resolved}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.content_md.rstrip() + "\n", encoding="utf-8")
    if push:
        build_notifier_from_env().send(
            title=f"Lurker 宏观资金周报 ({resolved})",
            markdown_content=report.content_md,
        )
    return render_job_summary(requested, resolved, snapshot_path, report_path, push)
```

Parser 默认：

```text
--snapshot-dir   data/processed/macro_flow_snapshots
--flow-snapshots data/processed/flow_snapshots
--report-dir     data/reports/weekly_macro_flow
--strategy-config configs/strategies.yaml
--core-etfs      configs/core_etfs.yaml
--date           today
--no-push        false
```

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py -k "weekly_macro_flow or weekly_report" -v
git add src/lurker/cli.py tests/test_cli.py
git commit -m "feat: add independent weekly macro flow job"
```

---

### Task 9: 全量回归、真实数据验收与文档记录

**Files:**
- Modify: `docs/superpowers/specs/2026-06-04-weekly-monthly-macro-flow-radar-design.md`
- Modify: `docs/superpowers/plans/2026-07-26-weekly-macro-flow.md`

- [ ] **Step 1: 全量测试与 lint**

```bash
PYTHONPATH=src .venv/bin/pytest tests/ -v
PYTHONPATH=src .venv/bin/ruff check src/ tests/
git diff --check
```

Expected: 0 failed、ruff `All checks passed!`、diff-check 无输出。

- [ ] **Step 2: 真实 no-push 演练**

```bash
set -a
source .env
set +a
PYTHONPATH=src .venv/bin/lurker weekly-macro-flow \
  --date 2026-07-24 \
  --no-push
```

检查：

```bash
python -m json.tool data/processed/macro_flow_snapshots/2026-07-24.json >/dev/null
rg -n "ETF 份额：unknown|fund_share|成交额不能替代份额|数据质量" \
  data/reports/weekly_macro_flow/2026-07-24.md
```

当前 token 的预期真实结果是 ETF 份额 `unknown` 且明确披露权限不足；如果届时
权限已补齐，则要求四只 ETF 都有精确的本周/前周端点，才允许输出净申购/
净赎回/份额平稳/分化。

- [ ] **Step 3: 幂等性**

```bash
shasum -a 256 data/processed/macro_flow_snapshots/2026-07-24.json \
  data/reports/weekly_macro_flow/2026-07-24.md > /tmp/weekly-macro-before.sha
PYTHONPATH=src .venv/bin/lurker weekly-macro-flow \
  --date 2026-07-24 \
  --no-push
shasum -a 256 data/processed/macro_flow_snapshots/2026-07-24.json \
  data/reports/weekly_macro_flow/2026-07-24.md > /tmp/weekly-macro-after.sha
```

raw snapshot 的 `generated_at` 允许变化；除该字段外，规范化 JSON 和报告内容
必须一致。使用测试内固定 `generated_at` 证明完全幂等，真实演练用脚本移除
该字段后比较。

- [ ] **Step 4: 验收记录**

在本计划末尾追加实际执行日期、各来源截止日、四只 ETF 完整度、两融来源、
五日 flow snapshot 完整度、板块接口列名、最终状态和所有 unknown 原因。
不得把“代码完成”写成“数据源验收通过”。

- [ ] **Step 5: 最终提交**

```bash
git add docs/superpowers/specs/2026-06-04-weekly-monthly-macro-flow-radar-design.md \
  docs/superpowers/plans/2026-07-26-weekly-macro-flow.md
git commit -m "docs: record weekly macro flow acceptance"
```

---

## 验收矩阵

| 要求 | 证据 |
|---|---|
| ETF 真实份额而非成交量代理 | `test_fund_share_permission_error_yields_unknown_not_turnover_proxy` |
| 四只 ETF 缺一即 unknown | `test_classify_etf_week_requires_all_four_symbols` |
| ±0.5% 死区和 3/4 聚合 | ETF 参数化真值表 |
| 成交额只做确认 | turnover 测试 + 报告“确认偏弱”测试 |
| 两融 Tushare/AkShare fallback | margin provider 测试 |
| 五个完整交易日，缺失不补零 | `test_load_market_flow_week_requires_exact_five_sessions` |
| 指数平和超大单背离 | market-flow 分类真值表 |
| 板块连续性读取上期 raw | sector continuity 测试 |
| 新旧周报并存 | CLI parser 回归测试 |
| 周末手动运行回退到周五 | Saturday CLI 测试 |
| 同日报接收人、支持 no-push | notifier 两条测试 |
| raw snapshot 可审计且幂等 | schema/store/CLI 重跑测试 |
| provider 缺失不转 0 | 所有 optional/non-finite 测试 |
| 全量回归 | `pytest tests/ -v`、ruff、diff-check |

## 实施完成定义

只有同时满足以下条件才算 Phase 1 完成：

1. Task 1–9 全部 checkbox 完成并逐任务提交。
2. 全量测试、ruff、diff-check 全绿。
3. 独立复审无 P0/P1/P2。
4. `weekly-macro-flow --no-push` 真实演练有可审计 raw snapshot 和报告。
5. 当前 `fund_share` 权限不足时，报告保持 `unknown`，没有任何成交量代理。
6. 旧 `weekly-report` 行为和测试未回归。
