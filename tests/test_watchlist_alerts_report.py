from lurker.reports.watchlist_alerts import render_watchlist_alerts
from lurker.signals.anomaly import AlertType, AnomalyAlert


def test_render_watchlist_alerts_groups_multiple_alerts_by_symbol():
    alerts = [
        AnomalyAlert(
            "300308.SZ",
            "cn",
            "中际旭创",
            AlertType.PEAK_DRAWDOWN,
            "2026-07-20",
            0.25,
            {"drawdown": -0.25},
        ),
        AnomalyAlert(
            "300308.SZ",
            "cn",
            "中际旭创",
            AlertType.CHRONIC_UNDERPERFORMANCE,
            "2026-07-20",
            0.18,
            {"alpha_60d": -0.18},
        ),
    ]

    report = render_watchlist_alerts(
        report_date="2026-07-20",
        alerts=alerts,
        data_issues=[],
        checked_count=1,
    )

    assert report.count("## 中际旭创（300308.SZ）") == 1
    assert "高位回撤" in report
    assert "持续跑输" in report
    assert "数据截止日：2026-07-20" in report


def test_render_watchlist_alerts_records_silent_and_degraded_runs():
    report = render_watchlist_alerts(
        report_date="2026-07-20",
        alerts=[],
        data_issues=["NVDA：行情抓取失败"],
        checked_count=1,
    )

    assert "本次没有需要推送的新异常" in report
    assert "NVDA：行情抓取失败" in report
