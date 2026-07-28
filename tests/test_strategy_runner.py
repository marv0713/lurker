from pathlib import Path
from textwrap import indent

import pytest

from lurker.application.strategy_runner import (
    StrategyConfig,
    StrategyContext,
    StrategyResult,
    load_strategy_configs,
    render_strategy_results,
    select_strategy_configs,
)
from lurker.reports.models import DailyReport


def _strategy_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "strategies.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_strategy_configs_from_yaml(tmp_path):
    config_path = tmp_path / "strategies.yaml"
    config_path.write_text(
        """
strategies:
  long_term_trend:
    enabled: true
    cadence: daily
    universe: resolved_seed_pool
    params:
      signal_threshold: 50
  short_term_setup:
    enabled: false
    cadence: daily
    universe: active_a_share_pool
""",
        encoding="utf-8",
    )

    configs = load_strategy_configs(config_path)

    assert configs["long_term_trend"].enabled is True
    assert configs["long_term_trend"].params["signal_threshold"] == 50
    assert configs["short_term_setup"].universe == "active_a_share_pool"


def test_select_strategy_configs_filters_enabled_names_and_cadence():
    configs = {
        "long_term_trend": StrategyConfig(
            name="long_term_trend",
            enabled=True,
            cadence="daily",
            universe="resolved_seed_pool",
        ),
        "deep_research": StrategyConfig(
            name="deep_research",
            enabled=True,
            cadence="weekly",
            universe="main_candidates",
        ),
        "disabled": StrategyConfig(
            name="disabled",
            enabled=False,
            cadence="daily",
            universe="resolved_seed_pool",
        ),
    }

    selected = select_strategy_configs(configs, names=None, cadence="daily")

    assert [config.name for config in selected] == ["long_term_trend"]
    assert select_strategy_configs(configs, names=["deep_research"], cadence=None)[0].name == (
        "deep_research"
    )

def test_render_strategy_results_composes_multiple_sections():
    report = render_strategy_results(
        report_date="2026-05-18",
        results=[
            StrategyResult(
                name="long_term_trend",
                title="中长期趋势雷达",
                report=DailyReport(report_date="2026-05-18", main_candidates_count=0, content_md="## 今日主候选\n\n- A"),
            ),
            StrategyResult(
                name="short_term_setup",
                title="短期交易雷达",
                report=DailyReport(report_date="2026-05-18", main_candidates_count=0, content_md="## 买点观察\n\n- B"),
            ),
        ],
    )
    assert "## 中长期趋势雷达" in report.content_md
    assert "## 短期交易雷达" in report.content_md
    assert "- A" in report.content_md
    assert "- B" in report.content_md


def test_strategy_context_carries_shared_runtime_inputs():
    context = StrategyContext(
        snapshot_batch={"snapshots": []},
        flow_snapshot={"market_flow": {"main_net_inflow": 1}},
        theme_mapping={"300308.SZ": ["ai_infra"]},
        report_date="2026-05-18",
        attributor=None,
        suppressed_symbols={"300308.SZ"},
        runtime_params={"main_limit": 10},
    )

    assert context.theme_mapping["300308.SZ"] == ["ai_infra"]
    assert context.flow_snapshot["market_flow"]["main_net_inflow"] == 1
    assert context.runtime_params["main_limit"] == 10


def test_professional_flow_strategy_is_registered():
    from lurker.application.strategy_runner import DEFAULT_STRATEGIES

    assert "professional_flow_daily" in DEFAULT_STRATEGIES


def test_load_strategy_lifecycle_and_limitations(tmp_path):
    configs = load_strategy_configs(
        _strategy_yaml(
            tmp_path,
            """
strategies:
  active:
    enabled: true
    lifecycle: active
  legacy:
    enabled: false
    lifecycle: deprecated
    limitations: [52 周高点距离未接入, 成交量扩张未接入]
""",
        )
    )

    assert configs["active"].lifecycle == "active"
    assert configs["active"].limitations == ()
    assert configs["legacy"].lifecycle == "deprecated"
    assert configs["legacy"].limitations == (
        "52 周高点距离未接入",
        "成交量扩张未接入",
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("enabled: 'yes'", "enabled must be a boolean"),
        ("enabled: true\nlifecycle: retired", "lifecycle"),
        (
            "enabled: true\nlifecycle: deprecated\nlimitations: [缺口]",
            "deprecated strategy must be disabled",
        ),
        (
            "enabled: false\nlifecycle: deprecated\nlimitations: []",
            "deprecated strategy requires limitations",
        ),
        (
            "enabled: true\nlifecycle: active\nlimitations: [不应存在]",
            "active strategy cannot declare limitations",
        ),
    ],
)
def test_load_strategy_config_rejects_invalid_lifecycle(tmp_path, body, message):
    path = _strategy_yaml(
        tmp_path,
        f"strategies:\n  sample:\n{indent(body, '    ')}\n",
    )
    with pytest.raises(ValueError, match=message):
        load_strategy_configs(path)


def test_strategy_selection_matrix_excludes_deprecated_automatically():
    configs = {
        "active_on": StrategyConfig("active_on", enabled=True),
        "active_off": StrategyConfig("active_off", enabled=False),
        "legacy": StrategyConfig(
            "legacy",
            enabled=True,
            lifecycle="deprecated",
            limitations=("52 周高点距离未接入",),
        ),
    }

    assert [
        item.name
        for item in select_strategy_configs(configs, names=None, cadence=None)
    ] == ["active_on"]
    assert [
        item.name
        for item in select_strategy_configs(
            configs,
            names=["active_off", "legacy"],
            cadence=None,
        )
    ] == ["active_off", "legacy"]


def test_deprecated_warning_is_rendered_for_single_and_multi_reports():
    legacy = StrategyResult(
        name="long_term_trend",
        title="中长期趋势雷达（Legacy）",
        report=DailyReport(
            report_date="2026-07-28",
            main_candidates_count=0,
            content_md="# 大趋势雷达日报\n\n日期：2026-07-28\n\n## 今日主候选\n\n- 无",
        ),
        metadata={
            "lifecycle": "deprecated",
            "limitations": ["52 周高点距离未接入", "成交量扩张未接入"],
        },
    )
    single = render_strategy_results("2026-07-28", [legacy])
    assert "⚠️ 弃用策略：`long_term_trend`" in single.content_md
    assert "52 周高点距离未接入；成交量扩张未接入" in single.content_md

    active = StrategyResult(
        name="professional_flow_daily",
        title="职业资金雷达日报",
        report=DailyReport("2026-07-28", 0, "## 数据质量\n\n- 正常"),
        metadata={"lifecycle": "active", "limitations": []},
    )
    combined = render_strategy_results("2026-07-28", [legacy, active])
    assert combined.content_md.count("⚠️ 弃用策略") == 1
    assert "## 职业资金雷达日报" in combined.content_md
