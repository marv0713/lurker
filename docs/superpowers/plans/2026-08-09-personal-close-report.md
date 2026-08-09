# Personal Close Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify an independent A/H-share personal holdings and watchlist close report with YAML scope, MA5/20/200 trends, market-specific spring analysis, a 14-calendar-day corporate-action window, and isolated idempotent notification delivery.

**Architecture:** Keep market data and external calendars behind adapters, keep trend/spring decisions in pure domain functions, and pass normalized facts into a renderer and job orchestrator. Preserve the public A-share `ma20-v1` result exactly with golden tests before extracting shared spring helpers; the Hong Kong analyzer gets its own volume-validation entry point. The command, report directory, state file, and `PERSONAL_*` notifier builder remain independent from all daily, weekly, monthly, and watchlist-anomaly workflows.

**Tech Stack:** Python 3.11+, pandas, PyYAML, exchange_calendars 4.13.2, AkShare, yfinance, requests, pytest, Ruff.

---

## File map

- Modify `src/lurker/domain/spring.py`: extract market-neutral spring helpers without changing `analyze_spring_bars()` output; add HK experimental entry point.
- Create `src/lurker/domain/personal_close.py`: immutable personal-report facts, trend analysis, and first-bullish quality projection.
- Modify `src/lurker/config.py`: strict personal YAML dataclasses and loader.
- Modify `src/lurker/trading_calendar.py`: reusable XSHG/XHKG cached calendar while preserving `CnTradingCalendar` compatibility.
- Create `src/lurker/ingest/personal_prices.py`: report-date clipping, duplicate rejection, adjusted HK OHLC derivation, and two-year loading.
- Create `src/lurker/ingest/corporate_actions.py`: provider protocol, normalized events, AkShare A-share adapter, and HK adapter with explicit coverage.
- Create `src/lurker/reports/personal_close_report.py`: one-line summary and complete Markdown rendering.
- Create `src/lurker/application/personal_close_state.py`: atomic accepted-push state.
- Create `src/lurker/application/personal_close.py`: independent job orchestration and failure isolation.
- Modify `src/lurker/notification/pushplus_notifier.py`: validate HTTP and PushPlus business response code.
- Modify `src/lurker/cli.py`: personal notifier builder and CLI façade.
- Modify `src/lurker/cli_parser.py`: `personal-close-report` arguments.
- Modify `src/lurker/cli_dispatch.py`: command dispatch.
- Create `configs/personal_watch.yaml`: editable example configuration.
- Modify `README.md`: local command, environment variables, and scheduling example.
- Add focused tests under `tests/test_personal_*.py` and extend `tests/test_spring.py`, `tests/test_trading_calendar.py`, `tests/test_cli.py`, and notification tests.

### Task 1: Freeze the A-share spring contract with full-field golden tests

**Files:**
- Modify: `tests/test_spring.py`

- [ ] **Step 1: Add golden cases before refactoring**

Add a parametrized test that feeds the existing fixture builders into the current `analyze_spring_bars()` and compares the complete dictionary exactly:

```python
def _golden_cases():
    watch = _trending_state_bars()
    bullish = _trending_state_bars()
    bullish[-1]["open"] = float(bullish[-1]["close"]) - 0.05
    bullish[-1]["volume"] = 3_000_000.0
    uncompressed = _trending_state_bars()
    for row in uncompressed[-3:]:
        row["volume"] = 600_000.0
    third_flags = [False] * 60
    third_flags[40] = third_flags[50] = third_flags[59] = True
    third = _bars_for_touch_flags(third_flags)
    return [
        (watch, {"rule_version": "ma20-v1", "state": "compressed_watch", "as_of": "2026-03-20", "ma20_distance_pct": 0.00864029104138253, "volume_compression_ratio": 0.2, "support_touch_count_60d": 1, "min_ma20_distance_2d_pct": 0.008602248418369651, "reasons": []}),
        (bullish, {"rule_version": "ma20-v1", "state": "first_bullish_confirmed", "as_of": "2026-03-20", "ma20_distance_pct": 0.00864029104138253, "volume_compression_ratio": 0.2, "support_touch_count_60d": 1, "min_ma20_distance_2d_pct": 0.008602248418369651, "reasons": []}),
        (uncompressed, {"rule_version": "ma20-v1", "state": "weak_excluded", "as_of": "2026-03-20", "ma20_distance_pct": 0.00864029104138253, "volume_compression_ratio": 0.6, "support_touch_count_60d": 1, "min_ma20_distance_2d_pct": 0.008602248418369651, "reasons": ["volume_not_compressed"]}),
        (third, {"rule_version": "ma20-v1", "state": "weak_excluded", "as_of": "2026-03-20", "ma20_distance_pct": 0.0, "volume_compression_ratio": 1.0, "support_touch_count_60d": 3, "min_ma20_distance_2d_pct": 0.0, "reasons": ["third_support_test", "volume_not_compressed"]}),
    ]


@pytest.mark.parametrize(("bars", "expected"), _golden_cases())
def test_ma20_v1_full_result_golden(bars, expected):
    assert analyze_spring_bars(bars) == expected
```

Keep the existing exact-five segment test and first-bullish prior-volume test beside this golden test; together they freeze segment anchoring and `compression_end = len - 2`.

- [ ] **Step 2: Run the golden test against the untouched implementation**

Run: `.venv/bin/python -m pytest tests/test_spring.py::test_ma20_v1_full_result_golden -q`

Expected: PASS for all four cases.

- [ ] **Step 3: Commit the compatibility lock**

```bash
git add tests/test_spring.py
git commit -m "test: lock spring ma20 v1 golden outputs"
```

### Task 2: Extract shared spring shape logic and add the HK entry point

**Files:**
- Modify: `src/lurker/domain/spring.py`
- Modify: `tests/test_spring.py`
- Create: `tests/test_hk_spring.py`

- [ ] **Step 1: Write failing HK validation tests**

Add tests for the market difference and preserve the A-share zero-volume test:

```python
def test_hk_zero_volume_outside_required_window_can_be_evaluated():
    bars = trending_hk_bars()
    bars[20]["volume"] = 0
    result = analyze_hk_experimental_spring(bars)
    assert result["state"] == "compressed_watch"
    assert result["experimental"] is True


def test_hk_zero_volume_in_compression_window_is_unknown():
    bars = trending_hk_bars()
    bars[-2]["volume"] = 0
    result = analyze_hk_experimental_spring(bars)
    assert result["state"] == "unknown"
    assert result["reasons"] == ["hk_zero_volume_in_compression_window"]


def test_hk_liquidity_gate_uses_raw_close_turnover():
    bars = trending_hk_bars(raw_close=10.0, adj_close=5.0, volume=1_000_000.0)
    result = analyze_hk_experimental_spring(bars)
    assert result["avg_turnover_hkd_20d"] == 10_000_000.0
```

- [ ] **Step 2: Run the HK tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_hk_spring.py -q`

Expected: FAIL because `analyze_hk_experimental_spring` does not exist.

- [ ] **Step 3: Extract a private `_analyze_shape` helper without changing A-share validation**

Keep `analyze_spring_bars()` responsible for parsing the latest 79 rows and requiring every A-share volume to be finite and positive. Move only post-validation calculations into a helper with an explicit compression callback:

```python
def _analyze_shape(
    bars: Sequence[_Bar],
    *,
    rule_version: str,
    compression_ratio_for: Callable[[int], float],
) -> dict[str, Any]:
    """Run the existing post-validation MA20 shape calculation."""
    # Move the current implementation verbatim from
    # `closes = [bar.close for bar in normalized]` through its return mapping.
    # Replace only `_compression_ratio(normalized, compression_end)` with
    # `compression_ratio_for(compression_end)` and `RULE_VERSION` with
    # `rule_version`. Keep `_merge_touch_segments`, `compression_end`,
    # `volume_not_compressed`, reason order, and every returned field unchanged.
```

The A-share wrapper must continue returning exactly the original eight fields and reason order. Do not add `experimental` to A-share results.

- [ ] **Step 4: Implement the HK wrapper with differential volume validation**

Add:

```python
HK_RULE_VERSION = "hk-ma20-experimental-v1"


def analyze_hk_experimental_spring(
    bars: Sequence[Mapping[str, Any]],
    *,
    min_avg_turnover_hkd_20d: float = 10_000_000.0,
    min_positive_volume_ratio_60d: float = 0.95,
) -> dict[str, Any]:
    # Parse latest 79 rows; price fields are adjusted OHLC.
    # raw_close is required for turnover; volume may equal zero but not be negative.
    # Gate on 20-row mean(raw_close * volume) and 60-row positive-volume ratio.
    # When shape needs compression, reject any zero in its exact 3+40 rows.
    result = _analyze_shape(
        normalized,
        rule_version=HK_RULE_VERSION,
        compression_ratio_for=hk_compression_ratio_for,
    )
    return {**result, "rule_version": HK_RULE_VERSION, "experimental": True,
            "avg_turnover_hkd_20d": avg_turnover,
            "positive_volume_ratio_60d": positive_ratio}
```

- [ ] **Step 5: Run A and HK tests**

Run: `.venv/bin/python -m pytest tests/test_spring.py tests/test_hk_spring.py -q`

Expected: PASS, including exact full-field A-share golden comparisons.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/domain/spring.py tests/test_spring.py tests/test_hk_spring.py
git commit -m "feat: add market-specific spring analysis"
```

### Task 3: Add strict personal YAML configuration

**Files:**
- Modify: `src/lurker/config.py`
- Create: `tests/test_personal_config.py`
- Create: `configs/personal_watch.yaml`

- [ ] **Step 1: Write failing loader tests**

Cover ordered holdings/watchlist, required names, duplicate symbols across groups, unsupported markets, unknown fields, empty scope, and HK thresholds.

```python
def test_load_personal_watch_preserves_group_order(tmp_path):
    path = write_yaml(tmp_path, """
defaults:
  hk_experimental_spring:
    min_avg_turnover_hkd_20d: 10000000
    min_positive_volume_ratio_60d: 0.95
holdings:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
watchlist:
  - {symbol: 00700.HK, market: hk, name: 腾讯控股}
""")
    config = load_personal_watch(path)
    assert [item.symbol for item in config.holdings] == ["300308.SZ"]
    assert [item.symbol for item in config.watchlist] == ["00700.HK"]
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_personal_config.py -q`

Expected: FAIL because the dataclasses and loader do not exist.

- [ ] **Step 3: Implement immutable config types and validation**

Add:

```python
@dataclass(frozen=True)
class PersonalStockConfig:
    symbol: str
    market: str
    name: str


@dataclass(frozen=True)
class HkExperimentalSpringConfig:
    min_avg_turnover_hkd_20d: float = 10_000_000.0
    min_positive_volume_ratio_60d: float = 0.95


@dataclass(frozen=True)
class PersonalWatchConfig:
    holdings: tuple[PersonalStockConfig, ...]
    watchlist: tuple[PersonalStockConfig, ...]
    hk_experimental_spring: HkExperimentalSpringConfig
```

`load_personal_watch()` must reject any duplicate after uppercase normalization and require a non-empty stripped `name`.

- [ ] **Step 4: Add the editable example YAML**

Create a valid file with empty `holdings` and one clearly marked example watchlist item using the repository's existing `300308.SZ` symbol so the default command is runnable and the user can edit it.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_personal_config.py tests/test_config.py -q`

Expected: PASS.

```bash
git add src/lurker/config.py tests/test_personal_config.py configs/personal_watch.yaml
git commit -m "feat: add personal watch configuration"
```

### Task 4: Generalize cached market calendars for XSHG and XHKG

**Files:**
- Modify: `src/lurker/trading_calendar.py`
- Modify: `tests/test_trading_calendar.py`

- [ ] **Step 1: Write failing XHKG and cache-isolation tests**

```python
def test_market_calendars_use_separate_names_and_cache_files(tmp_path):
    cn, hk = build_default_personal_calendars(tmp_path)
    assert cn.calendar_name == "XSHG"
    assert hk.calendar_name == "XHKG"
    assert cn.cache_path.name == "xshg_sessions.json"
    assert hk.cache_path.name == "xhkg_sessions.json"


def test_calendar_failure_without_covering_cache_is_hard_failure(tmp_path):
    calendar = TradingCalendar("XHKG", tmp_path / "xhkg.json",
                               provider_factory=lambda: FailingProvider())
    with pytest.raises(TradingCalendarUnavailable):
        calendar.is_trading_day(date(2026, 8, 10))
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_trading_calendar.py -q`

Expected: FAIL because the generic calendar and personal builder do not exist.

- [ ] **Step 3: Generalize cache/provider while keeping aliases**

Introduce `TradingCalendar(calendar_name, cache_path, provider_factory)` and store `calendar_name` in `CalendarCache`. Preserve the existing constructor through `class CnTradingCalendar(TradingCalendar)` whose `__init__(cache_path, provider_factory=ExchangeCalendarsCnProvider)` calls `super().__init__("XSHG", cache_path, provider_factory=provider_factory)`, so existing callers and tests remain valid. Add:

```python
def build_default_personal_calendars(cache_dir: Path | None = None) -> dict[str, TradingCalendar]:
    root = cache_dir or DEFAULT_CALENDAR_CACHE.parent
    return {
        "cn": TradingCalendar("XSHG", root / "xshg_sessions.json"),
        "hk": TradingCalendar("XHKG", root / "xhkg_sessions.json"),
    }
```

Use valid covering cache when provider initialization fails; otherwise re-raise `TradingCalendarUnavailable`.

- [ ] **Step 4: Run calendar and legacy tests**

Run: `.venv/bin/python -m pytest tests/test_trading_calendar.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lurker/trading_calendar.py tests/test_trading_calendar.py
git commit -m "feat: add hong kong trading calendar"
```

### Task 5: Implement personal price normalization, trends, and bullish quality

**Files:**
- Create: `src/lurker/ingest/personal_prices.py`
- Create: `src/lurker/domain/personal_close.py`
- Create: `tests/test_personal_prices.py`
- Create: `tests/test_personal_trend.py`

- [ ] **Step 1: Write failing adjusted-OHLC and duplicate-date tests**

```python
def test_hk_adjusted_ohlc_uses_adj_close_ratio():
    raw = frame(open=10, high=12, low=9, close=10, adj_close=5, volume=100)
    normalized = normalize_personal_prices(raw, market="hk", report_date=date(2026, 8, 10))
    row = normalized.iloc[-1]
    assert (row.open, row.high, row.low, row.close) == (5.0, 6.0, 4.5, 5.0)
    assert row.raw_close == 10.0


def test_duplicate_trade_date_is_rejected_not_deduplicated():
    with pytest.raises(ValueError, match="duplicate_trade_date"):
        normalize_personal_prices(duplicate_frame(), market="cn", report_date=REPORT_DATE)
```

- [ ] **Step 2: Write failing trend and quality tests**

Create synthetic 220-row adjusted frames and assert all six trend labels, equality boundaries, MA directions, and quality boundaries at 0.5% and 2%.

```python
def test_first_bullish_quality_does_not_mutate_spring_result():
    spring = {"state": "first_bullish_confirmed", "as_of": "2026-08-10"}
    before = dict(spring)
    quality = project_first_bullish_quality(spring, last_two_rows())
    assert quality.label == "标准首阳"
    assert spring == before
```

- [ ] **Step 3: Run and verify failures**

Run: `.venv/bin/python -m pytest tests/test_personal_prices.py tests/test_personal_trend.py -q`

Expected: FAIL because modules do not exist.

- [ ] **Step 4: Implement normalization and loader**

`normalize_personal_prices()` must clip first, reject any invalid/duplicate date in the returned on-or-before frame, validate only the latest 220 price rows, derive HK adjusted OHLC via `adj_close / raw_close`, and retain `raw_close`. `load_personal_prices()` must only accept `period="2y"` and call `fetch_watchlist_history(symbol, market, "2y")`.

- [ ] **Step 5: Implement trend facts**

Define immutable `MovingAverageFact`, `TrendAnalysis`, and `FirstBullishQuality`. Implement `analyze_personal_trend(frame)` with design priority: two-day MA200 break, first MA200 test, strong, medium pullback, repair, mixed, or insufficient. Return partial MA5/MA20 facts when safe, but use `data_insufficient` for the composite label under 220 rows.

- [ ] **Step 6: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_personal_prices.py tests/test_personal_trend.py -q`

Expected: PASS.

```bash
git add src/lurker/ingest/personal_prices.py src/lurker/domain/personal_close.py tests/test_personal_prices.py tests/test_personal_trend.py
git commit -m "feat: add personal price and trend analysis"
```

### Task 6: Implement normalized corporate-action providers

**Files:**
- Modify: `src/lurker/domain/personal_close.py`
- Create: `src/lurker/ingest/corporate_actions.py`
- Create: `tests/test_corporate_actions.py`

- [ ] **Step 1: Write failing normalization and window tests**

Use injected DataFrame fetchers; do not hit the network in tests. Cover `[report_date, report_date + 13]`, expected/confirmed status, de-duplication, and complementary dates not increasing counts.

```python
def test_action_window_contains_exactly_fourteen_calendar_days():
    actions = normalize_actions(rows_on_offsets(0, 13, 14), report_date=REPORT_DATE)
    assert [item.primary_date for item in actions] == [REPORT_DATE, REPORT_DATE + timedelta(days=13)]


def test_provider_failure_reports_incomplete_not_no_events():
    provider = StubCorporateActionProvider(error=RuntimeError("source down"))
    result = collect_corporate_actions(
        items=(PersonalStockConfig("300308.SZ", "cn", "中际旭创"),),
        report_date=REPORT_DATE,
        providers={"cn": provider},
    )
    assert result.complete is False
    assert result.issues[0].code == "corporate_actions_unavailable"
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_corporate_actions.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement protocol and normalized facts**

Define immutable `CorporateAction` and `CorporateActionCoverage` facts in `domain/personal_close.py`; define `CorporateActionProvider` in the ingest module. Event types are `earnings`, `dividend`, `split`, `consolidation`, `rights_issue`, and `additional_issuance`; statuses are `expected` and `confirmed`. This keeps the domain/report modules independent of ingest adapters.

- [ ] **Step 4: Implement A-share adapter**

Use injectable wrappers around:

```python
ak.stock_report_disclosure(market="沪深京", period=period)
ak.stock_fhps_detail_em(symbol=code)
ak.stock_allotment_cninfo(symbol=code, start_date=start, end_date=end)
```

Select the latest non-null disclosure date from actual/third/second/first-change/initial reservation columns. Treat an actual disclosure as confirmed and a reservation/change as expected. Use `除权除息日` for dividend/stock-distribution events and `除权基准日` for allotment. If a supported endpoint is unavailable, mark coverage incomplete rather than returning an empty complete result.

- [ ] **Step 5: Implement HK adapter with explicit coverage**

Use injectable `yf.Ticker(symbol).get_calendar()` for earnings/ex-dividend facts and `ak.stock_hk_dividend_payout_em(symbol=code)` for dividend details. Normalize stock distributions found in the dividend description as `split`. When structured sources cannot cover consolidation, rights issue, or additional issuance, return those event types in `unsupported_event_types` so the report says the HK calendar is incomplete instead of “no events.”

- [ ] **Step 6: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_corporate_actions.py -q`

Expected: PASS.

```bash
git add src/lurker/domain/personal_close.py src/lurker/ingest/corporate_actions.py tests/test_corporate_actions.py
git commit -m "feat: add corporate action calendar adapters"
```

### Task 7: Render the complete personal close report

**Files:**
- Modify: `src/lurker/domain/personal_close.py`
- Create: `src/lurker/reports/personal_close_report.py`
- Create: `tests/test_personal_close_report.py`

- [ ] **Step 1: Write failing report tests**

Construct domain facts directly. Assert one-line priority, holdings before watchlist, YAML order, every name/code, MA display, market-closed as-of text, formal versus experimental spring labels, all actions, and quality warnings.

```python
def test_report_leads_with_one_line_and_keeps_every_stock():
    report = render_personal_close_report(sample_report_facts())
    assert report.index("一句话结论：") < report.index("## 持仓")
    assert report.index("中际旭创（300308.SZ）") < report.index("腾讯控股（00700.HK）")
    assert "部分数据不完整，详见数据质量" in report
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_personal_close_report.py -q`

Expected: FAIL because the renderer does not exist.

- [ ] **Step 3: Implement a presentation-only renderer**

Add immutable aggregation models to `domain/personal_close.py` before implementing the renderer:

```python
@dataclass(frozen=True)
class PersonalStockReportFact:
    config: PersonalStockConfig
    group: Literal["holding", "watchlist"]
    market_open: bool
    as_of: date | None
    adjusted_close: float | None
    trend: TrendAnalysis | None
    spring: Mapping[str, Any] | None
    bullish_quality: FirstBullishQuality | None
    actions: tuple[CorporateAction, ...]
    action_coverage_complete: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class PersonalReportFacts:
    report_date: date
    holdings: tuple[PersonalStockReportFact, ...]
    watchlist: tuple[PersonalStockReportFact, ...]
    issues: tuple[str, ...]
```

The renderer consumes `PersonalReportFacts`; it must not calculate moving averages, spring state, dates, or coverage. Build the one-line conclusion in priority order, then render all holdings and watchlist items. Only render “暂无已知事件” when that stock's corporate-action coverage is complete.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_personal_close_report.py -q`

Expected: PASS.

```bash
git add src/lurker/domain/personal_close.py src/lurker/reports/personal_close_report.py tests/test_personal_close_report.py
git commit -m "feat: render personal close report"
```

### Task 8: Add push state and the independent job orchestrator

**Files:**
- Create: `src/lurker/application/personal_close_state.py`
- Create: `src/lurker/application/personal_close.py`
- Create: `tests/test_personal_close_state.py`
- Create: `tests/test_personal_close_job.py`

- [ ] **Step 1: Write failing state tests**

Test atomic load/save, unknown-field tolerance, accepted-date lookup, no state on `--no-push`, and forced resend timestamp update.

- [ ] **Step 2: Write failing orchestration tests**

Inject config loader, two calendars, price loader, action provider, renderer, notifier, and clock. Cover both markets closed, one market open, single-stock price failure, action failure, historical read-only behavior, same-day idempotence, force push, report-write failure, and notification failure.

```python
def test_same_day_rerun_overwrites_report_but_does_not_repush(tmp_path):
    notifier = RecordingNotifier()
    kwargs = personal_job_kwargs(tmp_path=tmp_path, notifier=notifier)
    first = run_personal_close(**kwargs)
    second = run_personal_close(**kwargs)
    assert len(notifier.calls) == 1
    assert second.report_path.read_text() == second.content_md
    assert second.push_status == "already_accepted"
```

- [ ] **Step 3: Run and verify failures**

Run: `.venv/bin/python -m pytest tests/test_personal_close_state.py tests/test_personal_close_job.py -q`

Expected: FAIL because state and job modules do not exist.

- [ ] **Step 4: Implement atomic state and job**

Use a versioned JSON mapping with `accepted_dates[YYYY-MM-DD].accepted_at`. The job reads YAML once per invocation, evaluates only configured market calendars, loads every stock independently, attaches trend/spring/quality/action facts, writes `YYYY-MM-DD.md` with atomic replacement, then sends. Historical runs and no-channel runs never mutate state. Notification failure leaves state unchanged.

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_personal_close_state.py tests/test_personal_close_job.py -q`

Expected: PASS.

```bash
git add src/lurker/application/personal_close_state.py src/lurker/application/personal_close.py tests/test_personal_close_state.py tests/test_personal_close_job.py
git commit -m "feat: orchestrate personal close report"
```

### Task 9: Harden PushPlus and add the isolated PERSONAL notifier builder

**Files:**
- Modify: `src/lurker/notification/pushplus_notifier.py`
- Modify: `src/lurker/cli.py`
- Create: `tests/test_pushplus_notifier.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing PushPlus response tests**

Inject or monkeypatch `requests.post`. Assert HTTP errors, non-JSON bodies, and top-level `code != 200` raise; `code == 200` returns normally.

- [ ] **Step 2: Write failing PERSONAL environment tests**

Mirror the WATCHLIST test matrix for all nine variables. Assert partial email configuration, empty recipients, and invalid port fail; no personal variables returns `None`; unrelated daily/watchlist variables are ignored.

- [ ] **Step 3: Run and verify failures**

Run: `.venv/bin/python -m pytest tests/test_pushplus_notifier.py tests/test_cli.py -q`

Expected: FAIL because PushPlus only checks HTTP and `build_personal_notifier_from_env()` is missing.

- [ ] **Step 4: Implement business-code validation**

After `raise_for_status()`, call `resp.json()`, require a mapping with `code == 200`, otherwise raise `RuntimeError` including the returned code/message without exposing tokens.

- [ ] **Step 5: Implement the PERSONAL builder**

Add `build_personal_notifier_from_env()` using exactly the design's nine variables and the same `_env_bool`, email completeness, recipient parsing, and composite behavior as the watchlist builder. Return `None` when no personal channel is configured.

- [ ] **Step 6: Run all notification tests and commit**

Run: `.venv/bin/python -m pytest tests/test_pushplus_notifier.py tests/test_notification_email.py tests/test_cli.py -q`

Expected: PASS.

```bash
git add src/lurker/notification/pushplus_notifier.py src/lurker/cli.py tests/test_pushplus_notifier.py tests/test_cli.py
git commit -m "fix: validate personal notification acceptance"
```

### Task 10: Wire the CLI and enforce safe v1 parameters

**Files:**
- Modify: `src/lurker/cli_parser.py`
- Modify: `src/lurker/cli_dispatch.py`
- Modify: `src/lurker/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing parser and façade tests**

Assert defaults, all paths, `--period 2y`, mutual exclusion, rejection of `1y` before the façade is called, future date rejection, and historical force-push rejection.

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k personal_close -q`

Expected: FAIL because the command is unregistered.

- [ ] **Step 3: Add parser and dispatch**

Register `personal-close-report` with `--config`, `--report-dir`, `--state-file`, `--date`, `--period` using `choices=("2y",)`, a mutually exclusive `--no-push`/`--force-push` group, and the documented defaults. Dispatch every parsed value by name to `cli.personal_close_report`.

- [ ] **Step 4: Add façade validation and dependency construction**

The façade resolves Shanghai today, rejects future/historical-force combinations, reloads YAML on every call, builds XSHG/XHKG calendars, builds the personal notifier, and calls the application job. Return a status line containing report path, checked/failure counts, and `accepted`, `already_accepted`, `disabled`, `no_channel`, or `historical_read_only`.

- [ ] **Step 5: Run CLI tests and commit**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/test_cli_structure.py -q`

Expected: PASS.

```bash
git add src/lurker/cli_parser.py src/lurker/cli_dispatch.py src/lurker/cli.py tests/test_cli.py
git commit -m "feat: add personal close report command"
```

### Task 11: Documentation, integration verification, and live no-push smoke test

**Files:**
- Modify: `README.md`
- Modify: `configs/personal_watch.yaml` only if the user supplies real symbols; otherwise retain the safe example.

- [ ] **Step 1: Document setup and schedule**

Add the command, YAML schema, all nine `PERSONAL_*` variables, 14-day action coverage caveat, report path, idempotence, and a weekday post-close cron example. Explicitly state that exchange calendars suppress weekends/holidays even though cron runs Monday-Friday.

- [ ] **Step 2: Run focused feature tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_spring.py tests/test_hk_spring.py \
  tests/test_personal_config.py tests/test_trading_calendar.py \
  tests/test_personal_prices.py tests/test_personal_trend.py \
  tests/test_corporate_actions.py tests/test_personal_close_report.py \
  tests/test_personal_close_state.py tests/test_personal_close_job.py \
  tests/test_pushplus_notifier.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full regression and lint**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
```

Expected: all tests pass; Ruff reports no errors.

- [ ] **Step 4: Run a real no-push smoke test**

Run:

```bash
PYTHONPATH=src .venv/bin/lurker personal-close-report \
  --config configs/personal_watch.yaml \
  --period 2y \
  --no-push
```

Expected: on an A/H trading day, writes `data/reports/personal_close/YYYY-MM-DD.md`, includes every configured name/code, actual data dates, trend/spring facts, and company-action coverage; on a joint holiday, exits successfully with an explicit skipped status and no report.

- [ ] **Step 5: Inspect the generated report**

Verify manually that adjusted HK prices are plausible, the one-line conclusion appears first, holdings precede watchlist entries, incomplete action coverage is not described as no events, and no notification state was written by `--no-push`.

- [ ] **Step 6: Commit docs**

```bash
git add README.md
git commit -m "docs: document personal close report"
```

### Task 12: Final compatibility audit

**Files:**
- No planned source changes; fix only failures discovered by this audit.

- [ ] **Step 1: Re-run the A-share golden contract alone**

Run: `.venv/bin/python -m pytest tests/test_spring.py::test_ma20_v1_full_result_golden -q`

Expected: PASS with exact dictionaries.

- [ ] **Step 2: Confirm repository isolation**

Run:

```bash
git status --short
git diff --name-only HEAD~11..HEAD
```

Expected: personal feature files plus intentional shared spring/calendar/notifier/CLI/docs changes only; no generated reports, secrets, `progress.md`, or `task_plan.md` staged or committed.

- [ ] **Step 3: Record final verification evidence**

Capture exact pytest count, Ruff output, smoke-test outcome, generated report path, and any provider coverage warnings in the handoff. Do not mark the Goal complete until required tests and the no-push behavior are verified.
