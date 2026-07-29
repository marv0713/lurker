from lurker.application.monthly_macro_flow import (
    analyze_monthly_macro_flow,
)
from lurker.reports.monthly_macro_flow_report import (
    render_monthly_macro_flow_report,
)
from tests.test_monthly_macro_flow import complete_snapshot


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
