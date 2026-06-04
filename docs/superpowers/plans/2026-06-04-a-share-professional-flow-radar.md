# A-Share Professional Flow Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an A-share-only professional flow radar daily report and add email/multi-channel notification support.

**Architecture:** Keep the existing price snapshot pipeline intact, but add a separate flow snapshot pipeline so fund-flow failures do not corrupt price snapshots. Add a new `professional_flow_daily` strategy with independent scoring/reporting, then make it the default configured daily strategy. Notification remains behind the existing `Notifier` protocol, with `EmailNotifier` and `CompositeNotifier` added beside PushPlus.

**Tech Stack:** Python 3.11, pandas, AkShare, optional Tushare, requests, markdown-it-py, smtplib/email stdlib, pytest.

---

## File Structure

- Create `src/lurker/ingest/flows.py`: AkShare/Tushare fetchers and normalization for market, sector, stock, ETF, and margin flow data.
- Create `src/lurker/application/flow_snapshot.py`: collect, save, load, and render `flow_snapshot` payloads.
- Create `src/lurker/application/professional_flow_daily.py`: score flow snapshots plus price snapshots and produce the professional report model.
- Create `src/lurker/reports/professional_flow_report.py`: render `职业资金雷达日报` Markdown.
- Create `src/lurker/notification/email_notifier.py`: SMTP email provider with Markdown text plus HTML body.
- Modify `src/lurker/notification/notifier.py`: add `CompositeNotifier`.
- Modify `src/lurker/application/strategy_runner.py`: register `professional_flow_daily` and pass flow snapshot context.
- Modify `src/lurker/cli.py`: add `refresh-flows`, wire flow snapshots into `daily-job` and `run-daily`, build composite notifier.
- Modify `configs/strategies.yaml`: enable `professional_flow_daily`, keep `long_term_trend` disabled as legacy.
- Modify `configs/push.yaml.example`: document email and multi-provider configuration.
- Add tests in `tests/test_flows.py`, `tests/test_flow_snapshot.py`, `tests/test_professional_flow_daily.py`, and `tests/test_notification_email.py`.

---

### Task 1: Email and Composite Notifications

**Files:**
- Modify: `src/lurker/notification/notifier.py`
- Create: `src/lurker/notification/email_notifier.py`
- Test: `tests/test_notification_email.py`

- [ ] **Step 1: Write failing tests**

Add `tests/test_notification_email.py`:

```python
from lurker.notification.email_notifier import EmailNotifier, build_email_message
from lurker.notification.notifier import CompositeNotifier


class FakeSMTP:
    sent_messages = []

    def __init__(self, host, port, timeout=20):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent_messages.append(message)


def test_build_email_message_contains_text_and_html_parts():
    message = build_email_message(
        subject="职业资金雷达日报",
        markdown_content="# 标题\n\n- A",
        sender="from@example.com",
        recipients=["to@example.com"],
    )

    assert message["Subject"] == "职业资金雷达日报"
    assert message["From"] == "from@example.com"
    assert message["To"] == "to@example.com"
    assert "plain" in message.get_body(preferencelist=("plain",)).get_content_type()
    assert "html" in message.get_body(preferencelist=("html",)).get_content_type()


def test_email_notifier_sends_via_injected_smtp_class():
    FakeSMTP.sent_messages = []
    notifier = EmailNotifier(
        host="smtp.example.com",
        port=587,
        username="user",
        password="pass",
        sender="from@example.com",
        recipients=["to@example.com"],
        smtp_class=FakeSMTP,
    )

    notifier.send("日报", "# 内容")

    assert len(FakeSMTP.sent_messages) == 1
    assert FakeSMTP.sent_messages[0]["Subject"] == "日报"


def test_composite_notifier_sends_to_all_providers():
    calls = []

    class Provider:
        def __init__(self, name):
            self.name = name

        def send(self, title, markdown_content):
            calls.append((self.name, title, markdown_content))

    notifier = CompositeNotifier([Provider("push"), Provider("email")])

    notifier.send("title", "body")

    assert calls == [("push", "title", "body"), ("email", "title", "body")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_notification_email.py -v
```

Expected: fail because `email_notifier` and `CompositeNotifier` do not exist.

- [ ] **Step 3: Implement notifier code**

In `src/lurker/notification/notifier.py`, add:

```python
class CompositeNotifier:
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, title: str, markdown_content: str) -> None:
        errors: list[str] = []
        for notifier in self.notifiers:
            try:
                notifier.send(title=title, markdown_content=markdown_content)
            except Exception as exc:
                errors.append(f"{type(notifier).__name__}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))
```

Create `src/lurker/notification/email_notifier.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from email.message import EmailMessage
import smtplib
from typing import Any

from markdown_it import MarkdownIt


def build_email_message(
    *,
    subject: str,
    markdown_content: str,
    sender: str,
    recipients: Sequence[str],
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(markdown_content)
    html = MarkdownIt().render(markdown_content)
    message.add_alternative(html, subtype="html")
    return message


class EmailNotifier:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        recipients: Sequence[str],
        use_tls: bool = True,
        use_ssl: bool = False,
        smtp_class: Any | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = [recipient for recipient in recipients if recipient]
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.smtp_class = smtp_class

    def send(self, title: str, markdown_content: str) -> None:
        if not self.recipients:
            return
        message = build_email_message(
            subject=title,
            markdown_content=markdown_content,
            sender=self.sender,
            recipients=self.recipients,
        )
        smtp_class = self.smtp_class or (smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP)
        with smtp_class(self.host, self.port, timeout=20) as smtp:
            if self.use_tls and not self.use_ssl:
                smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
```

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/test_notification_email.py -v
```

Expected: all tests pass.

---

### Task 2: Flow Fetcher Normalization

**Files:**
- Create: `src/lurker/ingest/flows.py`
- Test: `tests/test_flows.py`

- [ ] **Step 1: Write failing tests**

Add tests for normalization and optional failures:

```python
import pandas as pd

from lurker.ingest.flows import (
    normalize_market_flow_frame,
    normalize_sector_flow_frame,
    normalize_stock_flow_frame,
    normalize_margin_frame,
)


def test_normalize_stock_flow_frame_maps_eastmoney_columns():
    raw = pd.DataFrame(
        {
            "代码": ["300308"],
            "名称": ["中际旭创"],
            "今日主力净流入-净额": [100000000],
            "今日超大单净流入-净额": [50000000],
            "5日主力净流入-净额": [300000000],
            "10日主力净流入-净额": [400000000],
        }
    )

    result = normalize_stock_flow_frame(raw)

    assert result[0]["symbol"] == "300308.SZ"
    assert result[0]["name"] == "中际旭创"
    assert result[0]["main_net_inflow"] == 100000000
    assert result[0]["super_large_net_inflow"] == 50000000
    assert result[0]["main_net_inflow_5d"] == 300000000
    assert result[0]["main_net_inflow_10d"] == 400000000


def test_normalize_sector_flow_frame_maps_rankings():
    raw = pd.DataFrame({"名称": ["通信设备"], "今日主力净流入-净额": [200000000]})

    result = normalize_sector_flow_frame(raw, category="industry")

    assert result == [
        {"name": "通信设备", "category": "industry", "main_net_inflow": 200000000, "rank": 1}
    ]


def test_normalize_margin_frame_sums_exchanges():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260604", "20260604"],
            "rzye": [100.0, 200.0],
            "rqye": [10.0, 20.0],
            "rzrqye": [110.0, 220.0],
        }
    )

    result = normalize_margin_frame(raw)

    assert result["trade_date"] == "20260604"
    assert result["financing_balance"] == 300.0
    assert result["securities_lending_balance"] == 30.0
    assert result["margin_balance"] == 330.0


def test_normalize_market_flow_frame_keeps_known_fields():
    raw = pd.DataFrame({"今日主力净流入-净额": [1.0], "今日超大单净流入-净额": [2.0]})

    result = normalize_market_flow_frame(raw)

    assert result["main_net_inflow"] == 1.0
    assert result["super_large_net_inflow"] == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_flows.py -v
```

Expected: fail because `lurker.ingest.flows` does not exist.

- [ ] **Step 3: Implement normalization functions**

Create `src/lurker/ingest/flows.py` with symbol formatting, safe numeric conversion, normalization functions, and fetcher wrappers around AkShare/Tushare. The fetcher wrappers should call AkShare functions by name but tests should cover normalization only.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/test_flows.py -v
```

Expected: all tests pass.

---

### Task 3: Flow Snapshot Application Layer

**Files:**
- Create: `src/lurker/application/flow_snapshot.py`
- Test: `tests/test_flow_snapshot.py`

- [ ] **Step 1: Write failing tests**

Add tests for collecting and storing snapshots:

```python
from lurker.application.flow_snapshot import (
    FileFlowSnapshotStore,
    collect_flow_snapshot,
    load_flow_snapshot_file,
)


def test_collect_flow_snapshot_records_successes_and_failures():
    def fetch_market_flow():
        return {"main_net_inflow": 1.0}

    def fetch_sector_flows():
        raise RuntimeError("sector offline")

    def fetch_stock_flows():
        return [{"symbol": "300308.SZ", "main_net_inflow": 10.0}]

    def fetch_margin():
        return {"margin_balance": 100.0}

    snapshot = collect_flow_snapshot(
        fetch_market_flow=fetch_market_flow,
        fetch_sector_flows=fetch_sector_flows,
        fetch_stock_flows=fetch_stock_flows,
        fetch_margin=fetch_margin,
        generated_at="2026-06-04T00:00:00+00:00",
    )

    assert snapshot["market"] == "cn"
    assert snapshot["market_flow"]["main_net_inflow"] == 1.0
    assert snapshot["stock_flows"][0]["symbol"] == "300308.SZ"
    assert snapshot["margin"]["margin_balance"] == 100.0
    assert snapshot["failures"][0]["source"] == "sector_flows"


def test_file_flow_snapshot_store_round_trips(tmp_path):
    store = FileFlowSnapshotStore(tmp_path)
    snapshot = {
        "schema_version": 1,
        "generated_at": "2026-06-04T00:00:00+00:00",
        "market": "cn",
        "market_flow": {},
        "sector_flows": [],
        "stock_flows": [],
        "margin": {},
        "core_etfs": [],
        "failures": [],
    }

    path = store.save(snapshot, "2026-06-04")
    loaded = load_flow_snapshot_file(path)

    assert loaded == snapshot
    assert store.load_latest() == snapshot
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_flow_snapshot.py -v
```

Expected: fail because `flow_snapshot` module does not exist.

- [ ] **Step 3: Implement flow snapshot collection and store**

Create `src/lurker/application/flow_snapshot.py` with:

- `collect_flow_snapshot(...)`
- `save_flow_snapshot_file(...)`
- `load_flow_snapshot_file(...)`
- `find_latest_flow_snapshot(...)`
- `FileFlowSnapshotStore`

Use the same style as `src/lurker/application/price_snapshot.py`.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/test_flow_snapshot.py -v
```

Expected: all tests pass.

---

### Task 4: Professional Flow Scoring and Report Rendering

**Files:**
- Create: `src/lurker/application/professional_flow_daily.py`
- Create: `src/lurker/reports/professional_flow_report.py`
- Test: `tests/test_professional_flow_daily.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
from lurker.application.professional_flow_daily import (
    classify_market_temperature,
    run_professional_flow_daily,
)


def test_classify_market_temperature_detects_attack_mode():
    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        margin={"margin_balance_change": 1.0},
        core_etfs=[{"symbol": "510300.SH", "turnover_expansion": 1.5}],
    )

    assert result == "进攻"


def test_professional_report_promotes_two_percent_candidate():
    price_snapshot = {
        "windows": [20, 60, 120, 180],
        "snapshots": [
            {
                "symbol": "300308.SZ",
                "market": "cn",
                "latest_close": 100.0,
                "return_20d": 0.30,
                "return_60d": 0.60,
                "return_120d": 0.80,
                "return_180d": 1.00,
            },
            {
                "symbol": "300054.SZ",
                "market": "cn",
                "latest_close": 100.0,
                "return_20d": 0.05,
                "return_60d": 0.10,
                "return_120d": 0.15,
                "return_180d": 0.20,
            },
        ],
        "failures": [],
    }
    flow_snapshot = {
        "market_flow": {"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        "sector_flows": [
            {"name": "ai_infra", "category": "theme", "main_net_inflow": 100.0, "rank": 1}
        ],
        "stock_flows": [
            {
                "symbol": "300308.SZ",
                "name": "中际旭创",
                "main_net_inflow": 80.0,
                "super_large_net_inflow": 40.0,
                "main_net_inflow_5d": 200.0,
                "main_net_inflow_10d": 300.0,
            }
        ],
        "margin": {"margin_balance_change": 1.0},
        "core_etfs": [],
        "failures": [],
    }

    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={"300308.SZ": ["ai_infra"]},
        symbol_names={"300308.SZ": "中际旭创"},
        report_date="2026-06-04",
    )

    assert "职业资金雷达日报" in report.content_md
    assert "进攻" in report.content_md
    assert "2%候选" in report.content_md
    assert "中际旭创" in report.content_md
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_professional_flow_daily.py -v
```

Expected: fail because `professional_flow_daily` does not exist.

- [ ] **Step 3: Implement scoring and rendering**

Implement:

- market temperature classification
- stock trend percentiles for A-share rows only
- sector flow score from mapped themes
- stock flow score from individual fund flow
- rare `2%候选` classification
- report rendering with the sections in the design spec

Keep this deterministic. Do not call LLM attribution in this strategy.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/test_professional_flow_daily.py -v
```

Expected: all tests pass.

---

### Task 5: Strategy and CLI Wiring

**Files:**
- Modify: `src/lurker/application/strategy_runner.py`
- Modify: `src/lurker/cli.py`
- Modify: `configs/strategies.yaml`
- Modify: `configs/push.yaml.example`
- Test: `tests/test_strategy_runner.py`, `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

Extend strategy/CLI tests to verify:

- `professional_flow_daily` is registered.
- `StrategyContext` carries `flow_snapshot`.
- `build_strategy_report` can run `professional_flow_daily`.
- CLI parser includes `refresh-flows`.
- daily job can build a composite notifier when both PushPlus and email env vars are configured.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_strategy_runner.py tests/test_cli.py -v
```

Expected: fail for missing context field, strategy registration, and CLI command.

- [ ] **Step 3: Implement wiring**

Implementation requirements:

- Add `flow_snapshot: dict[str, Any] | None = None` to `StrategyContext`.
- Add `ProfessionalFlowDailyStrategy` to `DEFAULT_STRATEGIES`.
- Add `refresh-flows` command using `collect_flow_snapshot`.
- In `daily_job`, collect and save flow snapshot before report generation.
- In `build_run_daily`, load latest flow snapshot if strategy is `professional_flow_daily`.
- Add a `build_notifier_from_env()` helper that returns `CompositeNotifier`, `PushPlusNotifier`, `EmailNotifier`, or `StubNotifier` depending on environment.
- Update `configs/strategies.yaml` so `professional_flow_daily` is enabled and `long_term_trend` is disabled.

- [ ] **Step 4: Verify focused tests pass**

Run:

```bash
pytest tests/test_strategy_runner.py tests/test_cli.py -v
```

Expected: all tests pass.

---

### Task 6: End-to-End Verification

**Files:**
- No new files unless tests expose a missing fixture.

- [ ] **Step 1: Run full test suite**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run local report generation with existing snapshots**

Run:

```bash
PYTHONPATH=src .venv/bin/lurker run-daily --strategies professional_flow_daily --markets cn
```

Expected: command prints a `职业资金雷达日报` report. If no local flow snapshot exists, it should state missing flow data instead of crashing.

- [ ] **Step 3: Run daily job dry path**

Run:

```bash
PYTHONPATH=src .venv/bin/lurker daily-job --markets cn --strategies professional_flow_daily --limit 20
```

Expected: writes price snapshot, flow snapshot, daily report, candidate history, and archive index. Network failures should appear in data quality rather than crashing after local report files are written when possible.

---

## Self-Review

- Spec coverage: email, composite notification, flow snapshots, A-share-only data, professional report, legacy strategy preservation, and graceful degradation are covered.
- Scope: HK/US data, ETF creation/redemption, futures basis, and options IV are explicitly excluded.
- Type consistency: strategy name is consistently `professional_flow_daily`; flow snapshot keys match the design spec.
- Implementation risk: Task 5 is the largest integration step; complete Tasks 1-4 first so failures are isolated.
