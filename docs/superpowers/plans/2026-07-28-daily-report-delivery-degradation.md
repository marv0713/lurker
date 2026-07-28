# Daily Report Delivery Degradation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make daily reports observable and deliverable when optional rollout checks degrade, while suppressing unapproved temperature-driven candidates and sending separate notifications for real failures.

**Architecture:** Resolve the rollout gate before strategy execution and pass a boolean capability flag through `StrategyContext` into `professional_flow_daily`. Keep report generation separate from delivery classification: valid reports become `SUCCESS` or `DEGRADED` and are pushed, while invalid runs use a small failure-notification wrapper and retain non-zero failure semantics.

**Tech Stack:** Python 3.12, pytest, Ruff, SQLAlchemy, existing notifier interfaces.

---

## File Map

- Modify `src/lurker/application/professional_flow_daily.py`: suppress temperature-driven candidates when rollout is unapproved.
- Modify `src/lurker/application/strategy_runner.py`: pass the rollout capability flag to the professional strategy.
- Modify `src/lurker/cli.py`: resolve the gate before strategy execution, classify delivery, push degraded reports, emit machine-readable status, and notify on failures.
- Modify `src/lurker/ingest/flows.py`: remove the implicit localhost proxy default.
- Modify `tests/test_professional_flow_daily.py`: prove unapproved rollout suppresses otherwise eligible candidates.
- Modify `tests/test_cli.py`: prove degraded delivery and failure notification behavior.
- Modify `tests/test_flows.py`: prove AkShare defaults to direct requests and honors an explicit proxy.

### Task 1: Suppress Unapproved Temperature Candidates

**Files:**
- Modify: `tests/test_professional_flow_daily.py`
- Modify: `src/lurker/application/professional_flow_daily.py`
- Modify: `src/lurker/application/strategy_runner.py`

- [ ] **Step 1: Write the failing candidate-suppression test**

Extend the existing promotable-candidate fixture with:

```python
report = run_professional_flow_daily(
    price_snapshot=price_snapshot,
    flow_snapshot=flow_snapshot,
    theme_mapping=theme_mapping,
    report_date="2026-07-28",
    temperature_rollout_approved=False,
)

assert report.main_candidates_count == 0
assert "2%候选" not in _candidate_rows(report.content_md)
assert "市场温度规则尚未完成上线验收" in report.content_md
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_professional_flow_daily.py::test_unapproved_temperature_rollout_suppresses_two_percent_candidate -q
```

Expected: `TypeError` because `temperature_rollout_approved` is not accepted.

- [ ] **Step 3: Add the capability flag**

Add a keyword argument defaulting to `True`:

```python
def run_professional_flow_daily(
    *,
    price_snapshot: dict[str, Any],
    flow_snapshot: dict[str, Any] | None,
    theme_mapping: dict[str, list[str]],
    symbol_names: dict[str, str] | None = None,
    report_date: str,
    now: datetime | None = None,
    is_trading_day: Callable[[date], bool] | None = None,
    temperature_rollout_approved: bool = True,
) -> DailyReport:
```

Include the flag in `can_be_two_pct`:

```python
can_be_two_pct = (
    temperature_rollout_approved
    and s_score >= sector_min
    and flow_score >= flow_min
    and trend_score >= trend_min
    and is_leader
    and contradiction is None
    and (temperature != "观察" or has_5d_inflow)
)
```

Append a data-quality note when false:

```python
if not temperature_rollout_approved:
    data_quality.append(
        "⚠️ 市场温度规则尚未完成上线验收，已禁用温度驱动的 2%候选。"
    )
```

In `ProfessionalFlowDailyStrategy.run`, pass:

```python
temperature_rollout_approved=bool(
    context.runtime_params.get("temperature_rollout_approved", True)
)
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_professional_flow_daily.py tests/test_strategy_runner.py -q
```

Expected: all pass.

### Task 2: Push Degraded Reports and Emit Status

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/lurker/cli.py`

- [ ] **Step 1: Replace the old rollout-blocking assertion with degraded-delivery assertions**

Rename the existing test and assert:

```python
assert len(sends) == 1
assert sends[0][0].startswith("[降级]")
assert "市场温度上线闸门：缺少 rollout artifact" in sends[0][1]
assert "DAILY_JOB_STATUS=DEGRADED" in message
assert "Pushed degraded report successfully" in message
```

Also add a no-push variant:

```python
assert sends == []
assert "DAILY_JOB_STATUS=DEGRADED" in message
assert "Skipped pushing report (--no-push)." in message
```

- [ ] **Step 2: Run both tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -k "rollout_artifact_is_missing" -q
```

Expected: the degraded report is not sent and the status line is missing.

- [ ] **Step 3: Resolve the gate before strategy execution**

In `daily_job`, determine `selected_strategies` before `build_strategy_report`, then compute:

```python
temperature_gate_applies = "professional_flow_daily" in selected_strategies
temperature_gate_allowed = True
temperature_gate_reason = ""
if temperature_gate_applies:
    temperature_gate_allowed, temperature_gate_reason = _check_temperature_gate(
        resolved_artifact_path,
        replay_path=resolved_replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )
```

Pass the result through `build_strategy_report` runtime params:

```python
"temperature_rollout_approved": temperature_gate_allowed,
```

Keep `_annotate_temperature_gate` so the rendered report includes the exact degradation reason.

- [ ] **Step 4: Change delivery classification**

Classify:

```python
degradation_reasons = []
if temperature_gate_applies and not temperature_gate_allowed:
    degradation_reasons.append(temperature_gate_reason)
if any(f.get("source") in non_blocking_flow_sources for f in flow_failures):
    degradation_reasons.append("部分非关键资金源不可用")

delivery_status = "DEGRADED" if degradation_reasons else "SUCCESS"
```

For valid reports, push regardless of rollout approval:

```python
if is_valid and push:
    title = report.push_title
    if delivery_status == "DEGRADED":
        title = f"[降级] {title}"
    notifier.send(title=title, markdown_content=report.content_md)
```

Append exactly one status line:

```python
status_line = f"DAILY_JOB_STATUS={delivery_status}"
```

- [ ] **Step 5: Run CLI tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: all pass.

### Task 3: Send a Separate Failure Notification

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/lurker/cli.py`

- [ ] **Step 1: Write failing invalid-data and exception tests**

Add tests asserting that an empty price batch sends one failure notification:

```python
assert sends[0][0] == "[故障] 职业资金雷达日报 2026-07-28"
assert "价格数据快照为空" in sends[0][1]
assert normal_report_title not in sends[0][0]
```

Add an exception test around a new wrapper:

```python
with pytest.raises(RuntimeError, match="collector exploded"):
    run_daily_job_with_failure_notification(
        action=fail,
        report_date="2026-07-28",
        push=True,
        notifier=FakeNotifier(),
    )
assert sends[0][0].startswith("[故障]")
```

Add a notifier-failure test proving the original `RuntimeError("collector exploded")`
is still raised rather than the notifier exception.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -k "failure_notification" -q
```

Expected: wrapper is missing and invalid data only produces a skip message.

- [ ] **Step 3: Implement the failure notifier boundary**

Add:

```python
def _send_daily_failure_notification(
    *,
    report_date: str,
    stage: str,
    reason: str,
    notifier,
) -> None:
    notifier.send(
        title=f"[故障] 职业资金雷达日报 {report_date}",
        markdown_content=(
            "# 日报任务故障\n\n"
            f"- 日期：{report_date}\n"
            f"- 阶段：{stage}\n"
            f"- 原因：{reason}\n"
        ),
    )
```

Add a wrapper that preserves the original exception:

```python
def run_daily_job_with_failure_notification(*, action, report_date, push, notifier):
    try:
        return action()
    except Exception as exc:
        if push:
            try:
                _send_daily_failure_notification(
                    report_date=report_date,
                    stage="daily_job",
                    reason=f"{type(exc).__name__}: {exc}",
                    notifier=notifier,
                )
            except Exception:
                pass
        raise
```

Use it in the `daily-job` CLI branch. For validation failures inside `daily_job`,
send the failure notification, return `DAILY_JOB_STATUS=FAILED`, and make the CLI
branch raise a dedicated `DailyJobFailed` after printing the result so cron exits
non-zero.

- [ ] **Step 4: Verify failure behavior**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -k "failure_notification or validation" -q
```

Expected: all selected tests pass.

### Task 4: Remove the Hidden Localhost Proxy Default

**Files:**
- Modify: `tests/test_flows.py`
- Modify: `src/lurker/ingest/flows.py`

- [ ] **Step 1: Write failing proxy-scope tests**

Reload the module with no environment variable and assert the wrapper does not
inject a proxy:

```python
monkeypatch.delenv("AKSHARE_PROXY", raising=False)
flows = importlib.reload(flows_module)
wrapped = flows._make_proxy_func("get")
wrapped("https://finance.sina.com.cn/test")
assert captured_kwargs.get("proxies") == {}
```

Add a second test with `AKSHARE_PROXY=http://127.0.0.1:7897` and assert that exact
proxy is injected.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_flows.py -k "akshare_proxy" -q
```

Expected: the no-env case injects the localhost proxy.

- [ ] **Step 3: Make direct access the default**

Replace the module constants with:

```python
_AKSHARE_PROXY = os.environ.get("AKSHARE_PROXY", "").strip()
_AKSHARE_PROXIES = (
    {"http": _AKSHARE_PROXY, "https": _AKSHARE_PROXY}
    if _AKSHARE_PROXY
    else {}
)
```

Keep Eastmoney’s explicit direct behavior unchanged.

- [ ] **Step 4: Run flow and ETF tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_flows.py tests/test_etf_flows.py tests/test_flow_snapshot.py -q
```

Expected: all pass.

### Task 5: Verify, Commit, Deploy, and Exercise Production

**Files:**
- Verify all modified files.
- Deploy to `/root/lurker` on `root@64.186.233.134`.

- [ ] **Step 1: Run local verification**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
git diff --check
```

Expected: all tests pass, Ruff reports `All checks passed!`, diff check is empty.

- [ ] **Step 2: Commit and push**

```bash
git add src/lurker/application/professional_flow_daily.py \
  src/lurker/application/strategy_runner.py \
  src/lurker/cli.py src/lurker/ingest/flows.py \
  tests/test_professional_flow_daily.py tests/test_strategy_runner.py \
  tests/test_cli.py tests/test_flows.py \
  docs/superpowers/plans/2026-07-28-daily-report-delivery-degradation.md
git commit -m "fix: deliver degraded daily reports visibly"
git push origin main
```

- [ ] **Step 3: Update and verify VPS**

```bash
ssh root@64.186.233.134 \
  'cd /root/lurker && git pull --ff-only origin main &&
   .venv/bin/python -m pytest tests/test_cli.py -k "rollout_artifact_is_missing or failure_notification" -q'
```

Expected: fast-forward to the new commit and selected tests pass.

- [ ] **Step 4: Re-run the current daily report**

```bash
ssh root@64.186.233.134 \
  'cd /root/lurker && set -a && . ./.env && set +a &&
   PYTHONPATH=src .venv/bin/lurker daily-job --markets cn --limit 5
   --period 1y --windows 20,60,120,180 --date 2026-07-28'
```

Expected:

```text
Pushed degraded report successfully.
DAILY_JOB_STATUS=DEGRADED
```

- [ ] **Step 5: Inspect production evidence**

Confirm:

- `data/reports/2026-07-28.md` contains the rollout warning;
- the flow snapshot has four ETF items and zero ETF failures;
- the cron configuration remains at 17:30 Asia/Shanghai;
- no secret values appear in logs or command output.
