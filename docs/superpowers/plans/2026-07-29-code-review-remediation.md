# Code Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed review findings without breaking existing CLI, application, or compatibility entry points.

**Architecture:** Keep the domain dataclasses and existing business use cases authoritative. Tighten configuration and strategy contracts at load/selection boundaries, remove unreachable legacy branches, and mechanically extract CLI parsing/dispatch while re-exporting existing public entry points.

**Tech Stack:** Python 3.11+, pytest, PyYAML, argparse, Ruff

---

### Task 1: Model planned strategies and reject accidental execution

**Files:**
- Modify: `tests/test_strategy_runner.py`
- Modify: `configs/strategies.yaml`
- Modify: `src/lurker/application/strategy_runner.py`

- [ ] **Step 1: Write failing lifecycle and execution tests**

Add tests that load a disabled planned strategy with limitations, reject enabled planned strategies, reject explicit selection of planned strategies, and reject an unregistered active strategy:

```python
def test_planned_strategy_requires_disabled_with_limitations(tmp_path):
    configs = load_strategy_configs(_strategy_yaml(tmp_path, """
strategies:
  future:
    enabled: false
    lifecycle: planned
    limitations: [尚未实现]
"""))
    assert configs["future"].lifecycle == "planned"

def test_explicit_planned_strategy_selection_is_rejected():
    configs = {
        "future": StrategyConfig(
            "future",
            enabled=False,
            lifecycle="planned",
            limitations=("尚未实现",),
        )
    }
    with pytest.raises(ValueError, match="planned.*future.*尚未实现"):
        select_strategy_configs(configs, names=["future"], cadence=None)

def test_unregistered_active_strategy_is_rejected():
    with pytest.raises(ValueError, match="active strategy is not registered"):
        run_strategies(
            context=_empty_context(),
            configs=[StrategyConfig("missing", enabled=True)],
            registry={},
        )
```

- [ ] **Step 2: Run tests and verify the expected failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_strategy_runner.py -q
```

Expected: failures because `planned` is not a valid lifecycle, planned selection is not rejected, and missing strategies still return placeholder reports.

- [ ] **Step 3: Implement lifecycle validation and fail-fast execution**

Change the lifecycle type and validation:

```python
StrategyLifecycle = Literal["active", "planned", "deprecated"]

if lifecycle not in {"active", "planned", "deprecated"}:
    raise ValueError(...)
if lifecycle == "planned" and enabled:
    raise ValueError(f"planned strategy must be disabled: {name}")
if lifecycle == "planned" and not limitations:
    raise ValueError(f"planned strategy requires limitations: {name}")
```

In `select_strategy_configs()`, reject explicitly named planned strategies with their limitations. In `run_strategies()`, raise for any missing active implementation instead of returning a placeholder `DailyReport`.

Update the three placeholder YAML entries to:

```yaml
enabled: false
lifecycle: planned
limitations:
  - 尚未实现
```

- [ ] **Step 4: Run strategy and CLI regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_strategy_runner.py tests/test_cli.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add configs/strategies.yaml src/lurker/application/strategy_runner.py tests/test_strategy_runner.py
git commit -m "fix: prevent planned strategies from running"
```

### Task 2: Remove the ineffective AI scoring configuration contract

**Files:**
- Modify: `tests/test_config.py`
- Modify: `configs/scoring.yaml`
- Modify: `src/lurker/config.py`

- [ ] **Step 1: Write a failing top-level contract test**

```python
def test_load_scoring_rejects_unknown_top_level_fields(tmp_path):
    path = tmp_path / "scoring.yaml"
    path.write_text(
        """
stock_signal: {weights: {return_20d: 15}}
sector_signal: {weights: {sector_strength: 20}}
ai_attribution: {weights: {reason_clarity: 20}}
candidate_weights:
  stock_first: {stock_score: 0.35, sector_score: 0.35, ai_score: 0.30}
  sector_first: {stock_score: 0.25, sector_score: 0.45, ai_score: 0.30}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown scoring top-level field: ai_attribution"):
        load_scoring(path)
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py::test_load_scoring_rejects_unknown_top_level_fields -q
```

Expected: fail because `load_scoring()` currently accepts the unused field.

- [ ] **Step 3: Enforce the top-level schema and remove dead configuration**

In `load_scoring()`:

```python
_reject_unknown_fields(
    data,
    {"stock_signal", "sector_signal", "candidate_weights"},
    "scoring top-level",
)
```

Delete `ai_attribution.weights` from `configs/scoring.yaml`.

- [ ] **Step 4: Run config and scoring tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_legacy_scoring.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add configs/scoring.yaml src/lurker/config.py tests/test_config.py
git commit -m "fix: reject ineffective scoring configuration"
```

### Task 3: Consolidate AI attribution validation

**Files:**
- Modify: `tests/test_ai_schema.py`
- Modify: `tests/test_gemini_attributor.py`
- Modify: `src/lurker/domain/attribution.py`
- Modify: `src/lurker/ai/attributor.py`
- Delete: `src/lurker/ai/schemas.py`
- Delete: `src/lurker/ai/attribution.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing domain parsing tests**

Replace Pydantic-model tests with:

```python
def test_attribution_result_from_mapping_accepts_expected_payload():
    result = attribution_result_from_mapping({
        "classification": "产业趋势型",
        "reason_summary": "订单和财报验证需求。",
        "evidence": ["新闻", "公告", "伪证据"],
        "risk_flags": ["估值高"],
        "upgrade_recommendation": "升级",
        "missing_evidence": [],
    })
    assert result.classification == "产业趋势型"
    assert result.evidence == ["新闻", "公告"]

def test_attribution_result_from_mapping_downgrades_unknown_enums():
    result = attribution_result_from_mapping({
        "classification": "未知",
        "upgrade_recommendation": "未知",
    })
    assert result.classification == "证据不足型"
    assert result.upgrade_recommendation == "证据不足"
```

Update attributor tests to assert `_build_attribution_result()` delegates to the same behavior.

- [ ] **Step 2: Run tests and verify missing factory failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_ai_schema.py tests/test_gemini_attributor.py -q
```

Expected: import/failure because `attribution_result_from_mapping` does not exist.

- [ ] **Step 3: Add the canonical factory and use it at the LLM boundary**

Add to `domain/attribution.py`:

```python
def attribution_result_from_mapping(data: Mapping[str, Any]) -> AttributionResult:
    classification = data.get("classification", "证据不足型")
    if classification not in VALID_CLASSIFICATIONS:
        classification = "证据不足型"
    recommendation = data.get("upgrade_recommendation", "证据不足")
    if recommendation not in VALID_RECOMMENDATIONS:
        recommendation = "证据不足"
    raw_evidence = data.get("evidence", [])
    evidence = [
        item for item in raw_evidence
        if item in VALID_EVIDENCE
    ] if isinstance(raw_evidence, list) else []
    return AttributionResult(
        classification=classification,
        reason_summary=str(data.get("reason_summary", ""))[:200],
        evidence=evidence,
        risk_flags=_string_list(data.get("risk_flags")),
        upgrade_recommendation=recommendation,
        missing_evidence=_string_list(data.get("missing_evidence")),
    )
```

Make `_build_attribution_result()` call the factory. Delete the duplicate Pydantic model/wrapper and remove the now-unused `pydantic` dependency.

- [ ] **Step 4: Run AI and domain tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ai_schema.py tests/test_gemini_attributor.py tests/test_domain_architecture.py -q
```

Expected: all pass.

- [ ] **Step 5: Verify no legacy imports remain and commit**

Run:

```bash
rg -n "AIAttributionResult|lurker\\.ai\\.schemas|lurker\\.ai\\.attribution|pydantic" src tests pyproject.toml
```

Expected: no matches.

```bash
git add pyproject.toml src/lurker/domain/attribution.py src/lurker/ai/attributor.py tests/test_ai_schema.py tests/test_gemini_attributor.py
git add -u src/lurker/ai
git commit -m "refactor: unify AI attribution validation"
```

### Task 4: Remove unreachable legacy score dimensions

**Files:**
- Modify: `tests/test_legacy_scoring.py`
- Modify: `tests/test_domain_architecture.py`
- Modify: `src/lurker/domain/signals.py`

- [ ] **Step 1: Write failing tests proving hidden dimensions have no effect**

```python
def test_unwired_stock_dimensions_do_not_score():
    assert score_stock_strength({
        "near_52w_high": True,
        "relative_market_strength": 1.0,
        "relative_sector_strength": 1.0,
        "turnover_expansion": 10.0,
    }) == 0

def test_unwired_sector_dimensions_do_not_score():
    assert score_sector_breadth({
        "new_high_ratio": 1.0,
        "chain_segments": 10,
        "turnover_persistent": True,
    }) == 0
```

- [ ] **Step 2: Run tests and verify they fail with legacy points**

Run:

```bash
.venv/bin/python -m pytest tests/test_legacy_scoring.py -q
```

Expected: stock result 40 and sector result 45 instead of 0.

- [ ] **Step 3: Delete the hidden weights and branches**

Keep only configured/produced dimensions in both scoring functions. Update `test_domain_exports_core_language()` to assert the wired `return_180d` score without `near_52w_high`.

- [ ] **Step 4: Run scoring and application tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_legacy_scoring.py tests/test_domain_architecture.py tests/test_signals.py tests/test_scoring.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/lurker/domain/signals.py tests/test_legacy_scoring.py tests/test_domain_architecture.py
git commit -m "refactor: remove unreachable legacy score dimensions"
```

### Task 5: Add direct report coverage and harden test imports

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_daily_report.py`
- Create: `tests/test_trend_card.py`

- [ ] **Step 1: Add direct characterization tests**

Cover empty output and literal preservation:

```python
def test_daily_report_renders_empty_sections():
    report = render_daily_report(
        report_date="2026-07-29",
        main_cards=[],
        secondary_leads=[],
        low_score_watch_samples=[],
        watchlist_changes=[],
        risk_alerts=[],
    )
    assert "今日无主候选。" in report
    assert report.count("- 无") == 4

def test_trend_card_preserves_special_and_long_names():
    name = "AI (算力) 100% #主线 " + "长" * 120
    card = render_trend_card(
        theme=name,
        status="观察",
        stage="扩散",
        total_score=80,
        triggers=[],
        attribution="证据不足",
        evidence=[],
        risks=[],
        next_checks=[],
    )
    assert name in card
    assert card.count("- 无") == 4
```

- [ ] **Step 2: Run all direct report tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_daily_report.py tests/test_trend_card.py tests/test_professional_flow_daily.py tests/test_monthly_macro_flow_report.py -q
```

Expected: all pass; these are characterization tests and intentionally do not change rendering behavior.

- [ ] **Step 3: Verify explicit package import and full collection**

Run:

```bash
.venv/bin/python -c "import tests; print(tests.__file__)"
.venv/bin/python -m pytest --collect-only -q
```

Expected: `tests.__file__` points to the repository `tests/__init__.py`; all tests collect.

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/test_daily_report.py tests/test_trend_card.py
git commit -m "test: cover report renderers directly"
```

### Task 6: Extract CLI parser and dispatch while preserving imports

**Files:**
- Create: `src/lurker/cli_parser.py`
- Create: `src/lurker/cli_dispatch.py`
- Create: `tests/test_cli_structure.py`
- Modify: `src/lurker/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing extraction contract tests**

```python
def test_cli_reexports_extracted_parser():
    from lurker.cli import build_parser as compatibility_parser
    from lurker.cli_parser import build_parser
    assert compatibility_parser is build_parser

def test_dispatch_uses_existing_cli_command_for_list_reports(monkeypatch, capsys):
    from lurker.cli import build_parser
    from lurker.cli_dispatch import dispatch_command
    monkeypatch.setattr("lurker.cli.list_reports", lambda **kwargs: "reports")
    parser = build_parser()
    args = parser.parse_args(["list-reports"])
    assert dispatch_command(parser, args) is True
    assert capsys.readouterr().out == "reports\n"
```

- [ ] **Step 2: Run the new tests and verify module import failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli_structure.py -q
```

Expected: fail because `cli_parser` and `cli_dispatch` do not exist.

- [ ] **Step 3: Move parser construction without behavior changes**

Move `build_parser()` verbatim to `cli_parser.py`, define its own `ROOT`, and import/re-export it in `cli.py`:

```python
from lurker.cli_parser import build_parser
```

- [ ] **Step 4: Move command dispatch into focused handlers**

Create `dispatch_command(parser, args) -> bool` in `cli_dispatch.py`. Each command handler lazily imports `lurker.cli` so existing monkeypatch paths remain valid:

```python
def _list_reports(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli
    print(cli.list_reports(report_dir=args.report_dir, limit=args.limit))

COMMAND_HANDLERS = {
    "list-reports": _list_reports,
    "monthly-macro-flow": _monthly_macro_flow,
    "watchlist-checkup": _watchlist_checkup,
    "data-snapshot": _data_snapshot,
    "resolve-seeds": _resolve_seeds,
    "run-daily": _run_daily,
    "refresh-prices": _refresh_prices,
    "refresh-flows": _refresh_flows,
    "build-temperature-replay": _build_temperature_replay,
    "approve-temperature-rollout": _approve_temperature_rollout,
    "daily-job": _daily_job,
    "weekly-report": _weekly_report,
}

def dispatch_command(parser, args) -> bool:
    handler = COMMAND_HANDLERS.get(args.command)
    if handler is None:
        return False
    handler(parser, args)
    return True
```

Keep the no-command demo fallback in `main()`. Move `.env` loading into `_load_project_env()` so `main()` becomes parse → dispatch → fallback.

- [ ] **Step 5: Run CLI structure and full CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli_structure.py tests/test_cli.py -q
```

Expected: all pass, including existing `lurker.cli.*` monkeypatch tests.

- [ ] **Step 6: Verify CLI help and representative parsing**

Run:

```bash
.venv/bin/lurker --help
.venv/bin/lurker list-reports --limit 1
```

Expected: help lists all existing commands; list-reports executes through extracted dispatch.

- [ ] **Step 7: Commit**

```bash
git add src/lurker/cli.py src/lurker/cli_parser.py src/lurker/cli_dispatch.py tests/test_cli.py tests/test_cli_structure.py
git commit -m "refactor: extract CLI parsing and dispatch"
```

### Task 7: Documentation, full verification, and completion audit

**Files:**
- Modify: `docs/code-review-2026-07-29.md`
- Modify: `task_plan.md`
- Modify: `findings.md`
- Modify: `progress.md`

- [ ] **Step 1: Update the review document with verified resolutions**

Add a resolution section recording:

- the original P0 was not reproducible and import hardening was still applied;
- AI configuration/schema and strategy lifecycle were fixed;
- compatibility `pipeline.py` intentionally remains;
- direct report test coverage is now complete;
- CLI line-count correction and targeted extraction.

- [ ] **Step 2: Run focused invariant searches**

Run:

```bash
rg -n "ai_attribution:" configs/scoring.yaml
rg -n "AIAttributionResult|lurker\\.ai\\.schemas|lurker\\.ai\\.attribution" src tests
rg -n "near_52w_high|relative_market_strength|relative_sector_strength|Domain-only extension" src/lurker/domain/signals.py
rg -n "lifecycle: active" configs/strategies.yaml
```

Expected: no unused AI/schema/legacy matches; only implemented strategies are active.

- [ ] **Step 3: Run full verification**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
git diff --check
```

Expected: all tests pass, Ruff reports `All checks passed!`, and diff check exits 0.

- [ ] **Step 4: Audit every design acceptance criterion**

Read the design and map each criterion to current source or command evidence. Do not mark complete if any criterion lacks direct evidence.

- [ ] **Step 5: Update planning records and commit**

```bash
git add docs/code-review-2026-07-29.md task_plan.md findings.md progress.md docs/superpowers/plans/2026-07-29-code-review-remediation.md
git commit -m "docs: record code review remediation"
```
