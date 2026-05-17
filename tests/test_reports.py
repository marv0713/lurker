from lurker.reports.daily_report import render_daily_report
from lurker.reports.pushplus import build_pushplus_payload
from lurker.reports.trend_card import render_trend_card


def test_render_trend_card_contains_required_sections():
    card = render_trend_card(
        theme="AI 算力基础设施",
        status="主候选",
        stage="扩散",
        total_score=82,
        triggers=["A 股光模块多只个股 60 日强度进入前 10%"],
        attribution="云厂商资本开支带动高速互联需求。",
        evidence=["新闻", "公告"],
        risks=["估值偏高"],
        next_checks=["跟踪订单是否进入财报"],
    )

    assert "### AI 算力基础设施" in card
    assert "触发信号" in card
    assert "下一步验证" in card


def test_render_daily_report_has_main_and_secondary_sections():
    report = render_daily_report(
        report_date="2026-05-17",
        main_cards=["### AI 算力基础设施\n状态：主候选"],
        secondary_leads=["创新药出海：证据不足，保留观察"],
        watchlist_changes=["数据中心电力进入观察池"],
        risk_alerts=["部分光模块标的短期交易拥挤"],
    )

    assert "# 大趋势雷达日报" in report
    assert "## 今日主候选" in report
    assert "## 次级线索" in report


def test_build_pushplus_payload():
    payload = build_pushplus_payload(
        token="token-123",
        title="大趋势雷达日报",
        content="# 大趋势雷达日报\n正文",
    )

    assert payload["token"] == "token-123"
    assert payload["title"] == "大趋势雷达日报"
    assert payload["template"] == "markdown"
