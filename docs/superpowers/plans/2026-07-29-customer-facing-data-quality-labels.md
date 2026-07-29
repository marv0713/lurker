# Customer-Facing Data Quality Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace internal English freshness codes in the daily report with approved concise Chinese labels.

**Architecture:** Keep all internal freshness values and decision logic unchanged. Add one presentation-only mapping in `market_temperature.py` and apply it only while constructing `quality_notes`; unknown codes use a fixed Chinese fallback.

**Tech Stack:** Python 3.12, pytest

---

### Task 1: Map internal freshness codes at the report boundary

**Files:**
- Modify: `tests/test_market_temperature.py`
- Modify: `tests/test_professional_flow_daily.py`
- Modify: `src/lurker/application/market_temperature.py`

- [x] **Step 1: Write the failing report-output tests**

Update existing `quality_notes` assertions to require:

```python
assert prepared.quality_notes == (
    "大盘资金：截止 2026-07-23，当日数据",
    "核心 ETF：截止 2026-07-23，部分数据缺失",
    "两融：截止 2026-07-22，使用历史缓存",
    "⚠️ 部分数据非当日或采集不完整",
)
```

Require normal publication lag to render without an additional explanation:

```python
assert prepared.quality_notes[2] == (
    "两融：截止 2026-07-27，正常滞后一日"
)
```

Require stale data to render as:

```python
assert prepared.quality_notes[2] == "两融：截止 2026-07-24，数据已过期"
```

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_market_temperature.py::test_prepare_temperature_inputs_exposes_source_freshness_notes \
  tests/test_market_temperature.py::test_previous_session_margin_is_published_lag_and_actionable \
  tests/test_market_temperature.py::test_margin_older_than_previous_session_is_unknown -q
```

Expected: assertion failures showing the current English status codes.

- [x] **Step 3: Implement the presentation-only mapping**

Add:

```python
_CUSTOMER_QUALITY_LABELS = {
    "fresh": "当日数据",
    "published_lag": "正常滞后一日",
    "partial": "部分数据缺失",
    "stale": "数据已过期",
    "stale_cache": "使用历史缓存",
    "unknown": "暂不可用",
}


def _customer_quality_label(status: str) -> str:
    return _CUSTOMER_QUALITY_LABELS.get(status, "状态异常")
```

Use the helper only in the three `quality_notes` strings. Do not change
`healthy_statuses`, signal classification, degradation, or delivery logic.

- [x] **Step 4: Run focused and report regression tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_market_temperature.py \
  tests/test_professional_flow_daily.py -q
```

Expected: all tests pass.

- [x] **Step 5: Run lint and commit**

Run:

```bash
.venv/bin/ruff check \
  src/lurker/application/market_temperature.py \
  tests/test_market_temperature.py
git diff --check
```

Expected: both commands pass without findings.

Commit:

```bash
git add \
  docs/superpowers/plans/2026-07-29-customer-facing-data-quality-labels.md \
  src/lurker/application/market_temperature.py \
  tests/test_market_temperature.py \
  tests/test_professional_flow_daily.py
git commit -m "fix: localize daily data quality labels"
```
