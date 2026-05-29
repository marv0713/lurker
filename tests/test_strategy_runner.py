from lurker.application.strategy_runner import (
    StrategyConfig,
    StrategyContext,
    StrategyResult,
    load_strategy_configs,
    render_strategy_results,
    select_strategy_configs,
)


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


from lurker.reports.models import DailyReport

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
        theme_mapping={"300308.SZ": ["ai_infra"]},
        report_date="2026-05-18",
        attributor=None,
        suppressed_symbols={"300308.SZ"},
        runtime_params={"main_limit": 10},
    )

    assert context.theme_mapping["300308.SZ"] == ["ai_infra"]
    assert context.runtime_params["main_limit"] == 10
