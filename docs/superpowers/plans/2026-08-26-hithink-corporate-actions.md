# HiThink Corporate Actions Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix turnover and trigger-report regressions, prevent truncated HiThink histories, and compose HiThink A-share dividend events with the existing disclosure and rights-issue sources.

**Architecture:** Keep price ingestion fixes local to `prices.py` and trigger semantics local to `spring_trigger.py`. Extend `CnCorporateActionProvider` with a preferred HiThink dividend fetcher and an AkShare fallback; preserve its normalized provider contract and per-capability coverage semantics.

**Tech Stack:** Python 3.11, pandas, requests, pytest, Ruff.

---

### Task 1: Preserve turnover-to-date alignment

**Files:**
- Modify: `tests/test_ingest.py`
- Modify: `src/lurker/ingest/prices.py:128-139`

- [ ] **Step 1: Write a failing regression test**

Add a two-row reverse-ordered Tushare frame and assert the earlier date retains its own converted amount.

```python
def test_tushare_amount_stays_attached_to_date_after_sorting():
    raw = pd.DataFrame({
        "trade_date": ["20260516", "20260515"],
        "open": [20, 10], "high": [21, 11], "low": [19, 9],
        "close": [20, 10], "vol": [2, 1], "amount": [200, 100],
    })
    result = normalize_tushare_cn_price_frame(raw, "000001.SZ")
    assert list(result["amount"]) == [100_000.0, 200_000.0]
```

- [ ] **Step 2: Run the test and confirm the values are reversed**

Run: `.venv/bin/python -m pytest tests/test_ingest.py::test_tushare_amount_stays_attached_to_date_after_sorting -q`

- [ ] **Step 3: Convert amount on the normalized frame before sorting**

Assign the converted series to `normalized["amount"]`, include it in the selected columns, and sort/reset once.

- [ ] **Step 4: Run the focused ingest tests**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -q`

### Task 2: Report the pre-trigger shrink streak

**Files:**
- Modify: `tests/test_spring_trigger.py`
- Modify: `src/lurker/domain/spring_trigger.py:129-180`

- [ ] **Step 1: Extend the fired-trigger test with a failing assertion**

```python
assert result["shrink"]["consecutive_days"] == 2
assert result["shrink"]["latest_turnover"] == 1_600_000_000.0
```

- [ ] **Step 2: Run the focused test and confirm it reports zero days**

Run: `.venv/bin/python -m pytest tests/test_spring_trigger.py::test_trigger_fired_when_all_three_conditions_meet -q`

- [ ] **Step 3: When fired, replace only `consecutive_days` with the streak ending at `fired - 1`**

Keep `latest_turnover` as the most recent bar so the report still shows current liquidity.

- [ ] **Step 4: Run trigger and report tests**

Run: `.venv/bin/python -m pytest tests/test_spring_trigger.py tests/test_personal_close_report.py -q`

### Task 3: Fail closed at the HiThink price pagination cap

**Files:**
- Modify: `tests/test_ingest.py`
- Modify: `src/lurker/ingest/prices.py:343-378`

- [ ] **Step 1: Write a failing cap test**

Monkeypatch `_HITHINK_MAX_PAGES` to `2`, return one fresh item for each requested offset, and assert `fetch_hithink_cn_prices` raises `RuntimeError("hithink pagination limit reached")`.

- [ ] **Step 2: Run the test and confirm the current function returns a partial frame**

Run: `.venv/bin/python -m pytest tests/test_ingest.py::test_fetch_hithink_cn_prices_rejects_truncated_pagination -q`

- [ ] **Step 3: Track whether pagination ended naturally**

Set a completion flag only for empty or duplicate-only pages. Raise the specified runtime error if the loop exhausts without that flag.

- [ ] **Step 4: Run all ingest tests**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -q`

### Task 4: Add and compose the HiThink dividend adapter

**Files:**
- Modify: `tests/test_corporate_actions.py`
- Modify: `src/lurker/ingest/corporate_actions.py`

- [ ] **Step 1: Write failing adapter and composition tests**

Cover: envelope parsing; cash/bonus summaries; zero/zero ignore; successful empty response without fallback; API failure with AkShare fallback; merged earnings/dividend/rights events; both dividend sources failing marks coverage incomplete.

- [ ] **Step 2: Run the new tests and confirm imports/constructor expectations fail**

Run: `.venv/bin/python -m pytest tests/test_corporate_actions.py -q`

- [ ] **Step 3: Implement the REST adapter**

Add `fetch_hithink_cn_corporate_actions(symbol, report_date, token=None)` using the existing base URL, `X-api-key`, `[report_date, report_date + 13 days]`, bounded retry for request failures and `code=4001`, and strict envelope validation. Return normalized `CorporateAction` objects without exposing the key.

- [ ] **Step 4: Compose the preferred and fallback dividend sources**

Inject `hithink_distribution_fetcher` into `CnCorporateActionProvider`. For each stock, accept a successful HiThink result (including empty); on failure call the existing AkShare distribution fetcher; record a data-quality issue only if both fail. Keep disclosure and allotment collection unchanged.

- [ ] **Step 5: Run corporate-action and application tests**

Run: `.venv/bin/python -m pytest tests/test_corporate_actions.py tests/test_personal_close_job.py -q`

### Task 5: Verify with live and offline evidence

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the corporate-action capability and fallback**

State that `HITHINK_FINANCE_API_KEY` enables both preferred A-share prices and preferred A-share dividend/bonus ex-dates, with AkShare fallback.

- [ ] **Step 2: Use the supplied key only in process memory for one price request and one adjustment-factor request**

Print only response codes, item counts, and non-sensitive sample fields. Never persist or echo the key.

- [ ] **Step 3: Run the complete verification suite**

Run: `.venv/bin/python -m pytest -q`

Run: `.venv/bin/python -m ruff check src tests`

- [ ] **Step 4: Inspect the final diff and secret scan**

Run: `git diff --check && git diff --stat && git grep -n 'sk-fuyao-' -- . ':!docs/superpowers/plans/2026-08-26-hithink-corporate-actions.md'`
