# Market Temperature Rollout Data Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a current margin signal, build an auditable 60-session market-temperature replay, approve it through strict validation, deploy it, and repush the 2026-07-28 daily report.

**Architecture:** Keep daily and historical provider behavior separate. Daily margin collection uses Tushare first and a complete SH+SZ AkShare/Jin10 fallback; the preparation layer accepts the provider's normal one-session publication lag. Historical market-flow collection uses a dedicated proxy-independent Eastmoney client, while rollout approval reuses the same replay-integrity checks as the daily gate.

**Tech Stack:** Python 3.12, pandas, requests, AkShare, Tushare, pytest, Ruff, Click/argparse CLI, JSON artifacts.

---

### Task 1: AkShare Margin Fallback

**Files:**
- Modify: `src/lurker/ingest/flows.py`
- Modify: `src/lurker/ingest/temperature_history.py`
- Test: `tests/test_flows.py`
- Test: `tests/test_temperature_history.py`

- [ ] **Step 1: Write failing daily fallback tests**

Add tests proving:

```python
def test_fetch_margin_uses_akshare_when_tushare_permission_is_denied(...):
    ...
    assert result["source"] == "akshare_jin10_margin_sh_sz"
    assert result["trade_date"] == "20260727"
    assert result["margin_balance_change"] == 15.0


def test_fetch_margin_uses_akshare_without_tushare_token(...):
    ...
    assert result["availability"] == "fresh"


def test_normalize_akshare_margin_requires_both_exchanges_for_date():
    ...
    assert "2026-07-27" not in result
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_flows.py tests/test_temperature_history.py -q
```

Expected: new fallback tests fail because daily `fetch_margin()` does not call AkShare and the normalizer accepts incomplete dates.

- [ ] **Step 3: Implement minimal shared normalization and fallback**

Move or expose `normalize_akshare_margin_histories()` from
`temperature_history.py` through `flows.py`. Normalize each exchange, inner-join
on date, sum balances, calculate change in ascending date order, and preserve:

```python
{
    "trade_date": "YYYYMMDD",
    "financing_balance": ...,
    "securities_lending_balance": ...,
    "margin_balance": ...,
    "margin_balance_change": ...,
    "availability": "fresh",
    "source": "akshare_jin10_margin_sh_sz",
}
```

Add a narrowly-scoped recoverable Tushare predicate that recognizes the actual
`访问权限` message plus network/rate-limit errors. On recoverable Tushare failure
or missing token, fetch both Jin10 frames. Only fall back to cache when both
online paths fail.

- [ ] **Step 4: Verify GREEN**

Run the Task 1 test command and confirm all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/lurker/ingest/flows.py src/lurker/ingest/temperature_history.py tests/test_flows.py tests/test_temperature_history.py
git commit -m "fix: add current margin data fallback"
```

### Task 2: One-Session Margin Publication Lag

**Files:**
- Modify: `src/lurker/application/market_temperature.py`
- Test: `tests/test_market_temperature.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing freshness tests**

Add tests proving:

```python
def test_previous_session_margin_is_published_lag_and_actionable():
    prepared = prepare_temperature_inputs(
        margin={
            "trade_date": "20260727",
            "margin_balance_change": 10.0,
            "availability": "fresh",
        },
        report_date="2026-07-28",
        ...
    )
    assert prepared.margin_signal == "supportive"
    assert "状态 published_lag" in prepared.quality_notes[2]
    assert "采集不完整" not in prepared.quality_notes


def test_margin_older_than_previous_session_is_unknown():
    ...
    assert prepared.margin_signal == "unknown"
```

Add a CLI degradation test proving an online `fresh` margin record whose
provider date is the previous session does not add a `两融数据非当日` degradation.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_market_temperature.py tests/test_cli.py -k "margin or degradation" -q
```

Expected: previous-session signal is currently `unknown`.

- [ ] **Step 3: Implement previous-session calculation**

Use the injected `is_trading_day` predicate to find the session immediately
before `expected_trade_date`. Accept margin dates equal to either expected date
or that previous session. Set report status to `fresh` or `published_lag`;
anything earlier remains stale. Treat `published_lag` as healthy for the generic
quality warning.

- [ ] **Step 4: Verify GREEN and commit**

Run the Task 2 test command, then:

```bash
git add src/lurker/application/market_temperature.py tests/test_market_temperature.py tests/test_cli.py
git commit -m "fix: accept normal margin publication lag"
```

### Task 3: Proxy-Independent Market-Flow History

**Files:**
- Modify: `src/lurker/ingest/temperature_history.py`
- Test: `tests/test_temperature_history.py`

- [ ] **Step 1: Write failing historical client tests**

Use an injected fake session to prove:

```python
def test_market_history_uses_original_https_endpoint_without_environment_proxy():
    ...
    assert session.trust_env is False
    assert requested_url.startswith("https://push2his.eastmoney.com/")
    assert "push2delay" not in requested_url


def test_market_history_rejects_malformed_kline_rows():
    ...
    with pytest.raises(MarketFlowHistorySchemaError):
        fetch_market_flow_history(...)
```

Also prove valid rows normalize to an AkShare-compatible DataFrame with a
`source` attribute.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_temperature_history.py -k "market_history" -q
```

Expected: dedicated client and schema error do not exist.

- [ ] **Step 3: Implement minimal client**

Add a focused `fetch_market_flow_history()` that:

- creates or accepts a `requests.Session`;
- sets `trust_env=False`;
- calls the original HTTPS history endpoint;
- uses a 30-second timeout and fixed fields;
- calls `raise_for_status()`;
- validates `data.klines` and exactly 15 comma-separated values;
- returns typed date and numeric flow columns;
- sets `source=eastmoney_market_flow_history`.

Make `_fetch_market_flow_history()` use this client without entering
`_akshare_request_scope()`.

- [ ] **Step 4: Verify GREEN and commit**

Run the Task 3 tests, then:

```bash
git add src/lurker/ingest/temperature_history.py tests/test_temperature_history.py
git commit -m "fix: fetch auditable market flow history"
```

### Task 4: Strict Rollout Approval Command

**Files:**
- Modify: `src/lurker/cli.py`
- Modify: `src/lurker/application/temperature_replay.py`
- Test: `tests/test_market_temperature_replay.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing approval tests**

Add tests proving:

```python
def test_approve_rollout_stamps_only_valid_replay(...):
    approved = approve_rollout_artifact(
        artifact_path=artifact_path,
        replay_path=replay_path,
        approved_by="codex-goal-2026-07-28",
        now=...,
    )
    assert approved["approved"] is True
    assert approved["approved_by"] == "codex-goal-2026-07-28"
    assert _check_temperature_gate(...)[0] is True


@pytest.mark.parametrize("mutation", [...])
def test_approve_rollout_rejects_invalid_replay(...):
    with pytest.raises(ValueError):
        approve_rollout_artifact(...)
```

Cover fewer than 60 days, a leading state above 80%, hash mismatch, rules
fingerprint mismatch, invalid source provenance, and missing approver.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_market_temperature_replay.py tests/test_cli.py -k "approve_rollout" -q
```

Expected: approval API and CLI do not exist.

- [ ] **Step 3: Refactor shared validation and implement approval**

Extract gate validation so it can validate an unapproved artifact with
`require_approval=False`. Keep the daily gate using `require_approval=True`.
Approval must validate first, stamp a copy, write atomically, then validate the
written approved artifact again.

Expose:

```text
lurker approve-temperature-rollout
  --artifact ...
  --replay ...
  --approved-by codex-goal-2026-07-28
```

- [ ] **Step 4: Verify GREEN and commit**

Run the Task 4 tests, then:

```bash
git add src/lurker/cli.py src/lurker/application/temperature_replay.py tests/test_market_temperature_replay.py tests/test_cli.py
git commit -m "feat: validate and approve temperature rollout"
```

### Task 5: Generate and Validate the Real 60-Day Replay

**Files:**
- Regenerate: `tests/fixtures/etf_60d_replay.json`
- Generate locally: `data/processed/temperature_rollout.json`
- Modify if needed: `docs/superpowers/specs/2026-07-28-temperature-rollout-data-repair-design.md`

- [ ] **Step 1: Build the replay**

Run:

```bash
PYTHONPATH=src .venv/bin/lurker build-temperature-replay \
  --etf-start 2026-04-01 \
  --margin-start 2026-04-29 \
  --output-start 2026-04-30 \
  --output-end 2026-07-28 \
  --output tests/fixtures/etf_60d_replay.json \
  --artifact data/processed/temperature_rollout.json
```

Expected: exactly 60 trading days, all three sources represented, and no state
above 80%.

- [ ] **Step 2: Inspect replay provenance**

Run a Python audit that asserts:

- 60 unique, ordered CN trading dates;
- all market-flow records use `eastmoney_market_flow_history`;
- all four ETF roles are present on each day after warm-up;
- margin records use `akshare_jin10_margin_sh_sz` when published;
- missing values remain null;
- distribution sum is 60 and maximum ratio is at most 0.80.

- [ ] **Step 3: Approve with the delegated identity**

Run:

```bash
PYTHONPATH=src .venv/bin/lurker approve-temperature-rollout \
  --artifact data/processed/temperature_rollout.json \
  --replay tests/fixtures/etf_60d_replay.json \
  --approved-by codex-goal-2026-07-28
```

Expected: approved artifact passes the same daily gate.

- [ ] **Step 4: Commit the auditable fixture**

Do not commit runtime secrets or VPS caches. Commit the replay fixture and any
design acceptance-record update:

```bash
git add tests/fixtures/etf_60d_replay.json docs/superpowers/specs/2026-07-28-temperature-rollout-data-repair-design.md
git commit -m "data: add validated temperature replay"
```

### Task 6: Full Verification and Integration

**Files:**
- All changed files

- [ ] **Step 1: Run complete local verification**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
git diff --check
```

- [ ] **Step 2: Inspect repository state and commits**

Confirm only intended files changed and preserve the user's pre-existing
untracked files.

- [ ] **Step 3: Merge and push**

Fast-forward the feature branch into `main`, rerun the complete verification
from merged `main`, and push `main` to origin.

### Task 7: VPS Deployment and 2026-07-28 Repush

**Files on VPS:**
- Deploy repository code and fixture via `git pull --ff-only`
- Copy: `data/processed/temperature_rollout.json`

- [ ] **Step 1: Deploy and install no-new-dependency code**

Pull `main`, copy the validated artifact, and verify its hash against the
deployed replay fixture.

- [ ] **Step 2: Run focused VPS tests**

Run margin fallback, freshness, history, approval, and CLI gate tests plus
Ruff.

- [ ] **Step 3: Refresh and repush today**

Run detached with logs:

```bash
PYTHONPATH=src .venv/bin/lurker daily-job \
  --markets cn \
  --limit 5 \
  --period 1y \
  --windows 20,60,120,180 \
  --date 2026-07-28
```

- [ ] **Step 4: Verify production evidence**

Require all of:

- `DAILY_JOB_STATUS=SUCCESS`;
- push success;
- flow snapshot failures are empty;
- margin source is `akshare_jin10_margin_sh_sz`;
- margin cutoff is 2026-07-27 and status `published_lag`;
- margin signal is `supportive` or `weakening`, not `unknown`;
- report has no rollout warning;
- temperature-driven candidates are no longer disabled;
- cron remains configured for 17:30 Asia/Shanghai.

- [ ] **Step 5: Clean up worktree and complete the goal**

Remove the merged worktree and feature branch, preserve unrelated user files,
and mark the goal complete only after all production evidence is present.
