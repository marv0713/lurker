from lurker.application.monthly_macro_flow import (
    analyze_monthly_macro_flow,
)
from lurker.application.monthly_market_analysis import analyze_monthly_market
from lurker.application.weekly_flow_report import WeeklyFlowSummary
from lurker.reports.monthly_macro_flow_report import (
    render_monthly_macro_flow_report,
)
from tests.test_monthly_macro_flow import complete_snapshot


def _weekly_summary(**changes):
    values = {
        "availability": "available",
        "start_date": "2026-07-27",
        "end_date": "2026-07-31",
        "snapshot_count": 5,
        "temperature_counts": {"进攻": 0, "观察": 3, "防守": 2},
        "main_net_inflow_sum": -46_268_411_904.0,
        "super_large_net_inflow_sum": 14_686_212_096.0,
        "latest_etf_status": "unknown",
        "latest_margin_signal": "weakening",
        "continued_sectors": ("通信设备",),
        "new_sectors": ("机器人",),
        "ebb_sectors": ("银行",),
        "failure_count": 0,
        "quality_notes": (),
    }
    values.update(changes)
    return WeeklyFlowSummary(**values)


def test_report_discloses_dates_values_sources_and_quality():
    snapshot = complete_snapshot()
    snapshot["sources"] = [
        {
            "url": "https://www.pbc.gov.cn/table",
            "data_date": "2025-01",
            "sha256": "sha256:abc",
            "retrieved_at": "2026-07-26T12:00:00+00:00",
        }
    ]
    report = render_monthly_macro_flow_report(
        snapshot,
        analyze_monthly_macro_flow(snapshot),
    )
    content = report.content_md
    assert "# 宏观流动性月报" in content
    assert "报告月份：2025-01" in content
    assert "宏观数据截止月：2025-01" in content
    assert "杠杆数据截止日：2025-01-30" in content
    assert "## 居民存款趋势" in content
    assert "## 非银存款" in content
    assert "## M1-M2 活钱指标" in content
    assert "## 杠杆水位" in content
    assert "上月杠杆基准日：2024-12-31" in content
    assert "sha256:abc" in content


def test_data_observation_report_does_not_claim_trend():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"] = None
    report = render_monthly_macro_flow_report(
        snapshot,
        analyze_monthly_macro_flow(snapshot),
    )
    assert (
        "数据不足，仅展示观察事实，不形成趋势结论。"
        in report.content_md
    )
    assert "牛市加速" not in report.content_md
    assert "慢牛蓄力" not in report.content_md


def test_report_lists_each_failure_reason():
    snapshot = complete_snapshot()
    snapshot["failures"] = [
        {"source": "macro", "reason": "PBOC timeout"},
        {"source": "leverage", "reason": "margin stale"},
    ]
    report = render_monthly_macro_flow_report(
        snapshot,
        analyze_monthly_macro_flow(snapshot),
    )
    assert "macro：PBOC timeout" in report.content_md
    assert "leverage：margin stale" in report.content_md


def test_report_renders_rule_based_market_analysis_sections():
    snapshot = complete_snapshot()
    macro = analyze_monthly_macro_flow(snapshot)
    market = analyze_monthly_market(macro, _weekly_summary())

    report = render_monthly_macro_flow_report(snapshot, macro, market)

    for heading in (
        "## 本月市场判断",
        "## 资金证据链",
        "## 日周月交叉验证",
        "## 当前市场结构",
        "## 下月观察条件",
    ):
        assert heading in report.content_md
    assert "本月立场：观察；市场处于存量结构。" in report.content_md
    assert "主力累计 -462.7 亿元" in report.content_md
    assert "超大单累计 +146.9 亿元" in report.content_md
    assert "## 居民存款趋势" in report.content_md


def test_report_discloses_mixed_latest_layer_without_double_counting():
    snapshot = complete_snapshot()
    macro = analyze_monthly_macro_flow(snapshot)
    market = analyze_monthly_market(
        macro,
        _weekly_summary(
            latest_etf_status="active",
            latest_margin_signal="weakening",
        ),
    )

    report = render_monthly_macro_flow_report(snapshot, macro, market)

    assert "ETF 与两融方向分化，最新层暂不提供方向确认" in report.content_md
    assert "最新 ETF/两融合成信号偏强" not in report.content_md
    assert "最新 ETF/两融合成信号偏弱" not in report.content_md
