from lurker.reports.daily_report import render_daily_report


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


def test_daily_report_preserves_special_and_long_content():
    content = "AI (算力) 100% #主线 " + "长" * 120
    report = render_daily_report(
        report_date="2026-07-29",
        main_cards=[content],
        secondary_leads=[content],
        low_score_watch_samples=[content],
        watchlist_changes=[content],
        risk_alerts=[content],
    )

    assert report.count(content) == 5
