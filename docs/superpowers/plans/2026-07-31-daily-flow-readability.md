# Daily Flow Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make daily-report margin and ETF lines readable in Chinese and replace the generic data-quality warning with precise source-specific explanations.

**Architecture:** Keep snapshot schemas and classification enums unchanged. Extend the existing preparation result with ETF display metadata, centralize report-only formatting in `professional_flow_daily.py`, and make `market_temperature.py` produce precise quality notes from the freshness decisions it already owns.

**Tech Stack:** Python 3.11+, dataclasses, pytest, Ruff.

---

## File map

- Modify `src/lurker/application/market_temperature.py`: expose ETF cutoff/freshness, unify the `stale` customer label, and generate source-specific quality warnings.
- Modify `src/lurker/application/professional_flow_daily.py`: format margin amounts, translate internal signals, render ETF unknown reasons, and pass prepared metadata to the renderer.
- Modify `tests/test_market_temperature.py`: lock the preparation metadata and precise quality-warning behavior.
- Modify `tests/test_professional_flow_daily.py`: lock human-readable margin/ETF/signal text and end-to-end report output.

### Task 1: Expose ETF freshness and replace the generic quality warning

**Files:**
- Modify: `src/lurker/application/market_temperature.py:16-23,299-306,407-460`
- Test: `tests/test_market_temperature.py:597-870`

- [ ] **Step 1: Write failing preparation-layer tests**

Add assertions and focused cases showing that stale ETF data exposes its cutoff/freshness, `stale` is displayed consistently as “非当日数据”, a normally lagged margin produces no warning, and each abnormal source gets its own warning:

```python
assert prepared.etf_cutoff == "2026-07-22"
assert prepared.etf_freshness == "stale"
assert "核心 ETF：截止 2026-07-22，非当日数据" in prepared.quality_notes
assert (
    "⚠️ 核心 ETF 数据截止 2026-07-22，非当日；"
    "今日 ETF 信号未参与判断。"
) in prepared.quality_notes
assert "⚠️ 部分数据非当日或采集不完整" not in prepared.quality_notes
```

For a healthy market/ETF plus previous-session margin, assert exactly three informational notes and no warning. For market, ETF, and margin abnormalities together, assert three separate warning lines.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_market_temperature.py -k 'prepare_temperature or previous_session_margin or margin_older'
```

Expected: failures because `PreparedTemperatureInputs` lacks `etf_cutoff`/`etf_freshness`, the label is still “数据已过期”, and the generic warning is still emitted.

- [ ] **Step 3: Implement preparation metadata and precise warnings**

Change the customer label and dataclass fields:

```python
_CUSTOMER_QUALITY_LABELS = {
    "fresh": "当日数据",
    "published_lag": "正常滞后一日",
    "partial": "部分数据缺失",
    "stale": "非当日数据",
    "stale_cache": "使用历史缓存",
    "unknown": "暂不可用",
}

@dataclass(frozen=True)
class PreparedTemperatureInputs:
    market_flow: dict[str, Any]
    etf_status: str
    etf_freshness: str
    etf_cutoff: str
    margin_signal: str
    expected_trade_date: str
    quality_notes: tuple[str, ...]
```

After the three informational lines, append warnings independently. Use the existing `market_status`, `etf_freshness`, `margin_status`, cutoffs, and `core_etfs_batch.failures`; do not reclassify signals:

```python
if market_status != "fresh":
    quality_notes.append(
        f"⚠️ 大盘资金数据截止 {market_cutoff}，非当日；"
        "今日大盘资金信号未参与判断。"
    )
if etf_freshness != "fresh":
    if core_etfs_batch.failures:
        detail = "全部采集失败" if not core_etfs_batch.items else "部分采集失败"
        if etf_status == "active" and core_etfs_batch.items:
            quality_notes.append(
                "⚠️ 核心 ETF 部分采集失败；放量判断仅基于成功采集项。"
            )
        else:
            quality_notes.append(
                f"⚠️ 核心 ETF {detail}；今日 ETF 信号未参与判断。"
            )
    else:
        quality_notes.append(
            f"⚠️ 核心 ETF 数据截止 {etf_cutoff}，非当日；"
            "今日 ETF 信号未参与判断。"
        )
if margin_status not in {"fresh", "published_lag"}:
    quality_notes.append(
        f"⚠️ 两融数据截止 {margin_cutoff}，超出正常发布滞后；"
        "今日两融信号未参与判断。"
    )
```

When ETF items and failures coexist and the successful cutoff is non-current, include both facts in the warning. Return `etf_freshness` and `etf_cutoff` in the dataclass.

- [ ] **Step 4: Run preparation tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_market_temperature.py
```

Expected: all tests in the file pass with updated exact strings.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/lurker/application/market_temperature.py tests/test_market_temperature.py
git commit -m "fix: explain daily data quality precisely"
```

### Task 2: Format margin amounts and translate report state labels

**Files:**
- Modify: `src/lurker/application/professional_flow_daily.py:17-27,353-401`
- Test: `tests/test_professional_flow_daily.py:35-100`

- [ ] **Step 1: Write failing unit tests for report text**

Replace raw-number expectations and add edge cases:

```python
assert (
    "两融余额：2.60万亿元，较上一交易日减少368.1亿元（-1.40%）"
    in notes
)
assert "两融方向：杠杆资金回落" in notes
assert not any(note.startswith("两融余额：") for note in invalid_balance_notes)
assert "两融余额：0.00万亿元，较上一交易日增加1.0亿元" in zero_balance_notes
assert "%" not in next(note for note in zero_balance_notes if note.startswith("两融余额："))
```

Parameterize the four internal margin states:

```python
@pytest.mark.parametrize(
    ("signal", "label"),
    [
        ("supportive", "杠杆资金增加"),
        ("weakening", "杠杆资金回落"),
        ("overheated", "杠杆资金过热"),
        ("unknown", "暂不判断"),
    ],
)
```

Add ETF expectations for active with leader, active without leader, inactive, stale success, partial failure with stale successful items, total failure, and no collection.

- [ ] **Step 2: Run market-note tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_professional_flow_daily.py -k market_notes
```

Expected: failures show raw yuan values and English internal states.

- [ ] **Step 3: Implement finite-number and margin formatting helpers**

Add report-only helpers without changing `_as_float` callers elsewhere:

```python
def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None

def _format_margin_note(margin: dict[str, Any]) -> str | None:
    balance = _finite_float(margin.get("margin_balance"))
    if balance is None:
        return None
    note = f"两融余额：{balance / 1_000_000_000_000:.2f}万亿元"
    change = _finite_float(margin.get("margin_balance_change"))
    if change is None:
        return note
    direction = "增加" if change >= 0 else "减少"
    note += f"，较上一交易日{direction}{abs(change) / 100_000_000:.1f}亿元"
    previous_balance = balance - change
    if balance != 0 and previous_balance > 0:
        note += f"（{change / previous_balance * 100:+.2f}%）"
    return note
```

Map margin states with a total fallback to “暂不判断”; keep `overheated` even though currently unreachable.

- [ ] **Step 4: Implement ETF display-reason helper**

Create `_format_etf_note(...)` accepting the batch, status, freshness, cutoff, and expected date. Apply this order: active, inactive, partial failure (including stale cutoff when present), total failure, stale successful collection, fallback unavailable. Produce exactly the Chinese strings in the approved spec.

Update `_market_notes` to append formatted margin/ETF notes and `两融方向：{Chinese label}`. Do not change temperature or candidate behavior.

- [ ] **Step 5: Run market-note tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_professional_flow_daily.py -k market_notes
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/lurker/application/professional_flow_daily.py tests/test_professional_flow_daily.py
git commit -m "fix: make daily flow notes readable"
```

### Task 3: Wire prepared metadata into the complete daily report

**Files:**
- Modify: `src/lurker/application/professional_flow_daily.py:430-600`
- Test: `tests/test_professional_flow_daily.py:100-190`

- [ ] **Step 1: Write failing integration tests**

Build a `2026-07-31` report fixture with fresh market flow, four successful ETF items dated `2026-07-30`, and margin dated `2026-07-30`. Assert:

```python
assert "两融余额：2.60万亿元" in report.content_md
assert "两融方向：杠杆资金回落" in report.content_md
assert (
    "核心 ETF：暂不判断（数据截止 2026-07-30，非当日；采集成功）"
    in report.content_md
)
assert "核心 ETF：截止 2026-07-30，非当日数据" in report.content_md
assert (
    "⚠️ 核心 ETF 数据截止 2026-07-30，非当日；"
    "今日 ETF 信号未参与判断。"
    in report.content_md
)
assert "⚠️ 部分数据非当日或采集不完整" not in report.content_md
```

- [ ] **Step 2: Run the integration test and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_professional_flow_daily.py -k 'readable or data_quality'
```

Expected: `_market_notes` is not yet receiving the prepared ETF metadata, or old exact-string assertions fail.

- [ ] **Step 3: Pass prepared metadata through the report entry point**

Update the call:

```python
market_notes=_market_notes(
    prepared.market_flow,
    margin,
    temperature,
    etf_batch=etf_batch,
    etf_status=prepared.etf_status,
    etf_freshness=prepared.etf_freshness,
    etf_cutoff=prepared.etf_cutoff,
    expected_trade_date=prepared.expected_trade_date,
    margin_signal=prepared.margin_signal,
)
```

Update existing integration expectations from English/raw values to approved Chinese text.

- [ ] **Step 4: Run daily-report tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_professional_flow_daily.py tests/test_daily_report.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/lurker/application/professional_flow_daily.py tests/test_professional_flow_daily.py
git commit -m "test: cover readable daily flow report"
```

### Task 4: Regression verification

**Files:**
- Verify only; no planned production changes.

- [ ] **Step 1: Run focused market-temperature and report tests**

```bash
.venv/bin/python -m pytest -q tests/test_market_temperature.py tests/test_professional_flow_daily.py tests/test_daily_report.py tests/test_temperature_history.py
```

Expected: all pass.

- [ ] **Step 2: Run the full suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run static checks**

```bash
.venv/bin/ruff check .
git diff --check
```

Expected: Ruff reports “All checks passed!” and `git diff --check` has no output.

- [ ] **Step 4: Render the VPS fixture locally**

Use the values from the approved spec in a focused test or Python invocation and verify the generated report contains the exact three readable lines and the specific ETF quality warning, with no internal state codes or generic warning.

- [ ] **Step 5: Review the branch diff**

```bash
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected: only the two application files and their tests differ beyond the already committed spec/plan documents.
