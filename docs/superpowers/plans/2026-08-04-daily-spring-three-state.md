# Daily Spring Three-State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the return-window proxy “弹簧买点观察” with a deterministic MA20/OHLCV three-state scan, deploy it, and repush the 2026-08-04 professional daily report.

**Architecture:** Add a pandas-free pure rule module under `domain/` that consumes normalized bar mappings and returns a compact JSON-safe result. Invoke it while each CN price DataFrame is already in memory, attach the result to the price snapshot, then let the professional-flow application select and sort the three report groups without another market-data request. Keep old snapshot readers compatible and fail closed only for CN rows.

**Tech Stack:** Python 3.12, dataclasses/standard library, pandas at the ingestion boundary, pytest, Ruff, Markdown reports, Git worktrees, SSH deployment.

---

## File map

- Create `src/lurker/domain/spring.py`: validation, MA20/support episodes, volume compression, first-bullish event, reason ordering, JSON result.
- Create `tests/test_spring.py`: pure rule truth table and boundary tests.
- Modify `src/lurker/application/price_snapshot.py`: attach `spring` only to `market == "cn"` rows while preserving every existing snapshot field.
- Modify `tests/test_price_snapshot.py`: integration and non-CN compatibility tests.
- Modify `src/lurker/application/professional_flow_daily.py`: build three independently sorted lists, aggregate unknown reasons, retire `setup_watch` rendering, and neutralize the old conclusion.
- Modify `src/lurker/reports/professional_flow_report.py`: render the fixed explanation and three subsections.
- Modify `tests/test_professional_flow_daily.py`: report grouping, ordering, degradation, defense mode, and old-label regression tests.
- Modify `docs/professional_flow_radar.md`: replace the obsolete proxy description with the shipped three-state contract.

### Task 1: Pure spring rule — validation and support episodes

**Files:**
- Create: `src/lurker/domain/spring.py`
- Create: `tests/test_spring.py`

- [ ] **Step 1: Write failing validation and episode tests**

Create fixtures that build 79 daily bars and assert the public function contract:

```python
from datetime import date, timedelta

from lurker.domain.spring import analyze_spring_bars


def bars(count: int = 79) -> list[dict]:
    return [
        {
            "trade_date": date(2026, 1, 1) + timedelta(days=index),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        }
        for index in range(count)
    ]


def test_less_than_79_bars_is_unknown():
    result = analyze_spring_bars(bars(78))
    assert result["state"] == "unknown"
    assert result["reasons"] == ["insufficient_history"]


def test_duplicate_date_in_latest_window_is_unknown():
    values = bars()
    values[-1]["trade_date"] = values[-2]["trade_date"]
    result = analyze_spring_bars(values)
    assert result["reasons"] == ["duplicate_trade_date"]
```

Add focused cases for parseable out-of-order dates, invalid dates, zero/NaN price or volume, a bad row before the latest 79, continuous touches, merged anchors with `j - i < 5`, and separate anchors with `j - i == 5`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_spring.py -q
```

Expected: collection fails with `ModuleNotFoundError: lurker.domain.spring`.

- [ ] **Step 3: Implement validation and episode helpers**

Implement `analyze_spring_bars(bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]` together with these stable constants and unknown-result shape:

```python
RULE_VERSION = "ma20-v1"
MINIMUM_BARS = 79

def unknown_spring_result(reason: str, *, as_of: str | None = None) -> dict[str, Any]:
    return {
        "rule_version": RULE_VERSION,
        "state": "unknown",
        "as_of": as_of,
        "ma20_distance_pct": None,
        "volume_compression_ratio": None,
        "support_touch_count_60d": 0,
        "min_ma20_distance_2d_pct": None,
        "reasons": [reason],
    }
```

Parse all dates before sorting; validate only the latest 79 price/volume rows; calculate 60 daily MA20 values; build raw contiguous touch segments; merge segments using the last-touch anchor and the exact `j - i < 5` rule.

- [ ] **Step 4: Run validation and episode tests GREEN**

Run the same focused command. Expected: all validation and episode tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/lurker/domain/spring.py tests/test_spring.py
git commit -m "feat: add spring bar validation and support episodes"
```

### Task 2: Pure spring rule — state truth table

**Files:**
- Modify: `src/lurker/domain/spring.py`
- Modify: `tests/test_spring.py`

- [ ] **Step 1: Write one failing test per state transition**

Add tests proving:

```python
assert analyze_spring_bars(compressed_bars())["state"] == "compressed_watch"
assert analyze_spring_bars(first_bullish_bars())["state"] == "first_bullish_confirmed"
assert analyze_spring_bars(third_touch_bars())["reasons"] == ["third_support_test"]
assert analyze_spring_bars(broken_ma20_bars())["reasons"] == ["ma20_broken"]
assert analyze_spring_bars(uncompressed_touch_bars())["reasons"] == ["volume_not_compressed"]
```

Also test exact ±2% and 30% boundaries, MA20 down, far-from-MA20, the third-touch-over-first-bullish precedence, multiple reason order, long-shadow close above +2% returning `none`, first-bullish-day volume exclusion, and the next-day no-volume-overflow lifecycle.

- [ ] **Step 2: Run new tests and verify RED**

Run:

```bash
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_spring.py -q
```

Expected: state assertions fail because Task 1 returns only validation/neutral results.

- [ ] **Step 3: Implement the state calculation**

Use these exact calculations:

```python
ma20_distance = close_today / ma20_today - 1.0
ma20_up = ma20_today > ma20_five_sessions_ago
touch = low_today <= ma20_today * 1.02 and close_today >= ma20_today * 0.98
broken = all(close < ma20 * 0.98 for close, ma20 in latest_two)
compression_ratio = mean(compression_three) / max(rolling_mean(active_forty, 5))
```

Compute all weak reasons, store them in `ma20_broken`, `third_support_test`, `volume_not_compressed` order, and then select exactly one state. Skip volume reclassification after an earlier first-bullish K condition in the same merged segment.

- [ ] **Step 4: Run pure-rule tests GREEN and Ruff**

```bash
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_spring.py -q
/Users/marv/Documents/lurker/.venv/bin/python -m ruff check src/lurker/domain/spring.py tests/test_spring.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/lurker/domain/spring.py tests/test_spring.py
git commit -m "feat: classify spring three-state signals"
```

### Task 3: Attach spring results to CN price snapshots

**Files:**
- Modify: `src/lurker/application/price_snapshot.py`
- Modify: `tests/test_price_snapshot.py`

- [ ] **Step 1: Write failing snapshot integration tests**

Add a 79-row CN fetcher and assert:

```python
cn_row = collect_price_snapshot_batch(
    seed_symbols={"cn": ["300001.SZ"]},
    markets=["cn"],
    windows=[20],
    period="6mo",
    fetcher=fetcher,
)["snapshots"][0]
assert cn_row["spring"]["rule_version"] == "ma20-v1"
assert cn_row["spring"]["as_of"] == "2026-08-04"

us_row = collect_price_snapshot_batch(
    seed_symbols={"us": ["NVDA"]},
    markets=["us"],
    windows=[20],
    period="6mo",
    fetcher=fetcher,
)["snapshots"][0]
assert "spring" not in us_row
```

Add a test that analyzer failure still preserves the original CN price snapshot with `spring.state == "unknown"` rather than adding a batch fetch failure.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_price_snapshot.py -q
```

Expected: missing `spring` assertion fails.

- [ ] **Step 3: Add the ingestion-boundary call**

After returns are calculated, construct the row, and only for CN add:

```python
if market == "cn":
    try:
        row["spring"] = analyze_spring_bars(prices.to_dict("records"))
    except Exception:
        row["spring"] = unknown_spring_result("invalid_price_data")
```

Do not issue another fetch and do not remove the row when spring calculation fails.

- [ ] **Step 4: Run snapshot and storage tests GREEN**

```bash
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_price_snapshot.py tests/test_storage.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/lurker/application/price_snapshot.py tests/test_price_snapshot.py
git commit -m "feat: persist spring state in CN price snapshots"
```

### Task 4: Replace the old report list with three state groups

**Files:**
- Modify: `src/lurker/application/professional_flow_daily.py`
- Modify: `src/lurker/reports/professional_flow_report.py`
- Modify: `tests/test_professional_flow_daily.py`

- [ ] **Step 1: Write failing report grouping and copy tests**

Build snapshot rows for all five internal states and assert:

```python
assert "## 弹簧三态扫描" in report.content_md
assert "### 首阳确认" in report.content_md
assert "### 压紧观察" in report.content_md
assert "### 弱弹簧排除" in report.content_md
assert "仅代表形态确认" in report.content_md
assert "## 弹簧买点观察" not in report.content_md
assert "建议观望或布局弹簧买点" not in report.content_md
```

Add tests for fixed list limits, defense copy, multiple weak reasons, exact metrics, missing-score ordering, unknown CN quality aggregation, and non-CN rows not generating spring quality warnings.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_professional_flow_daily.py -q
```

Expected: old heading/renderer assertions fail.

- [ ] **Step 3: Implement selection, sorting, and quality aggregation**

Add a helper returning:

```python
{
    "confirmed": confirmed[:5],
    "watch": watch[:10],
    "excluded": excluded[:5],
}
```

Scan all CN snapshot rows. Join the optional existing candidate score by symbol, preserve missing score as `None`, use the specified stable sort tuples, and aggregate `unknown` reasons through a fixed Chinese mapping.

- [ ] **Step 4: Implement the renderer**

Change `render_professional_flow_report()` to receive the three-state mapping. Render the fixed explanation, three always-present subsections, readable metrics, all weak reasons, and “暂无” for empty groups. In defense mode append “仅供形态跟踪，不进入候选”。

- [ ] **Step 5: Run focused report tests GREEN**

```bash
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_professional_flow_daily.py -q
```

Expected: all pass.

- [ ] **Step 6: Run all direct call-site tests**

```bash
rg -n "render_professional_flow_report\(" src tests
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_strategy_runner.py tests/test_cli.py -q
```

Expected: no stale call signature and all tests pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/lurker/application/professional_flow_daily.py src/lurker/reports/professional_flow_report.py tests/test_professional_flow_daily.py tests/test_strategy_runner.py tests/test_cli.py
git commit -m "feat: render daily spring three-state scan"
```

### Task 5: Documentation and full verification

**Files:**
- Modify: `docs/professional_flow_radar.md`
- Modify if required by the implementation: `docs/superpowers/specs/2026-08-04-daily-spring-three-state-design.md`

- [ ] **Step 1: Update operational documentation**

Replace the old `setup_score >= 60` spring section with the shipped MA20 rules, state meanings, fail-closed behavior, and the statement that first-bullish confirmation is not a buy recommendation.

- [ ] **Step 2: Run focused and complete verification**

```bash
/Users/marv/Documents/lurker/.venv/bin/python -m pytest tests/test_spring.py tests/test_price_snapshot.py tests/test_professional_flow_daily.py -q
/Users/marv/Documents/lurker/.venv/bin/python -m pytest -q
/Users/marv/Documents/lurker/.venv/bin/python -m ruff check src tests
git diff --check
```

Expected: all tests pass, Ruff reports `All checks passed!`, and diff check is clean.

- [ ] **Step 3: Commit docs and final test adaptations**

```bash
git add docs/professional_flow_radar.md docs/superpowers/specs/2026-08-04-daily-spring-three-state-design.md
git commit -m "docs: document daily spring three-state scan"
```

### Task 6: Review, integrate, deploy, and repush

**Files:**
- No source changes expected.
- VPS runtime report: `/root/lurker/data/reports/2026-08-04.md`
- VPS runtime snapshot: `/root/lurker/data/processed/price_snapshots/2026-08-04.json`

- [ ] **Step 1: Review the branch diff**

Compare the branch to `main`, verify every acceptance item has a named test, and confirm only intended source/test/docs files changed.

- [ ] **Step 2: Merge into main and push**

From the main checkout, preserve the user’s unrelated `progress.md` and `task_plan.md`, merge `codex/daily-spring-three-state`, and run:

```bash
git push origin main
```

- [ ] **Step 3: Update VPS and run focused verification**

```bash
ssh root@64.186.233.134 \
  'cd /root/lurker && git pull --ff-only origin main && .venv/bin/python -m pytest tests/test_spring.py tests/test_price_snapshot.py tests/test_professional_flow_daily.py -q'
```

Expected: VPS HEAD matches pushed main and focused tests pass.

- [ ] **Step 4: Repush the 2026-08-04 daily report**

Read `crontab -l` first and verify the production arguments. Then load the existing environment and rerun the same daily job with the explicit date; the current CLI defaults are restated to make the intended data scope auditable:

```bash
ssh root@64.186.233.134 \
  'crontab -l; cd /root/lurker && set -a && . ./.env && set +a && \
   PYTHONPATH=src .venv/bin/lurker daily-job --date 2026-08-04 \
   --markets cn --period 1y --windows 20,60,120,180 --limit 5'
```

Expected: command reports a successful/degraded valid delivery and sends the report through the configured notifier.

- [ ] **Step 5: Verify the deployed artifact and delivery content**

Confirm the report contains the fixed explanation and all three subsections, contains no old spring heading or old conclusion, gives concrete spring data-quality reasons, and matches the latest pushed body. Record the VPS commit, report path, snapshot `spring` counts, and delivery result in the final handoff.
