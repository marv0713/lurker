from lurker.reports.trend_card import render_trend_card


def test_trend_card_renders_empty_lists():
    card = render_trend_card(
        theme="AI 算力",
        status="观察",
        stage="扩散",
        total_score=80,
        triggers=[],
        attribution="证据不足",
        evidence=[],
        risks=[],
        next_checks=[],
    )

    assert card.count("- 无") == 4


def test_trend_card_preserves_special_and_long_names():
    name = "AI (算力) 100% #主线 " + "长" * 120
    card = render_trend_card(
        theme=name,
        status="观察",
        stage="扩散",
        total_score=80,
        triggers=["20 日涨幅"],
        attribution="证据不足",
        evidence=["公告"],
        risks=["估值"],
        next_checks=["订单"],
    )

    assert name in card
    assert "总分：80" in card
