from datetime import date

from lurker.config import PersonalStockConfig
from lurker.domain.personal_close import (
    CorporateAction,
    DataQualityIssue,
    FirstBullishQuality,
    MovingAverageFact,
    PersonalReportFacts,
    PersonalStockReportFact,
    TrendAnalysis,
)
from lurker.reports.personal_close_report import render_personal_close_report


def stock_fact(
    symbol: str,
    market: str,
    name: str,
    *,
    group: str,
    market_open: bool = True,
    trend_label: str = "long_medium_strong",
    spring_state: str = "none",
    experimental: bool = False,
    actions=(),
    coverage_complete: bool = True,
    issues=(),
    unsupported=(),
    spring_trigger=None,
):
    ma5 = MovingAverageFact(5, 102.0, -0.01, "down")
    ma20 = MovingAverageFact(20, 96.0, 0.061, "up")
    ma200 = MovingAverageFact(200, 75.0, 0.352, "up")
    trend = TrendAnalysis(
        trend_label,
        date(2026, 8, 10),
        101.0,
        ma5,
        ma20,
        ma200,
    )
    spring = {
        "rule_version": "hk-ma20-v1-experimental" if experimental else "ma20-v1",
        "state": spring_state,
        "as_of": "2026-08-10",
        "ma20_distance_pct": 0.061,
        "volume_compression_ratio": 0.25,
        "support_touch_count_60d": 2,
        "reasons": [],
    }
    if experimental:
        spring["experimental"] = True
    return PersonalStockReportFact(
        config=PersonalStockConfig(symbol, market, name),
        group=group,
        market_open=market_open,
        as_of=date(2026, 8, 10),
        adjusted_close=101.0,
        trend=trend,
        spring=spring,
        bullish_quality=(
            FirstBullishQuality(0.012, 0.018, "standard")
            if spring_state == "first_bullish_confirmed"
            else None
        ),
        spring_trigger=spring_trigger,
        actions=actions,
        action_coverage_complete=coverage_complete,
        issues=issues,
        unsupported_event_types=unsupported,
    )


def test_report_leads_with_one_line_and_keeps_every_stock_in_yaml_order():
    issue = DataQualityIssue("calendar_incomplete", "港股公司行动日历不完整", market="hk")
    facts = PersonalReportFacts(
        report_date=date(2026, 8, 10),
        holdings=(
            stock_fact("300308.SZ", "cn", "中际旭创", group="holding"),
            stock_fact("600519.SH", "cn", "贵州茅台", group="holding"),
        ),
        watchlist=(
            stock_fact(
                "00700.HK",
                "hk",
                "腾讯控股",
                group="watchlist",
                market_open=False,
                experimental=True,
                coverage_complete=False,
                issues=(issue,),
            ),
        ),
        issues=(issue,),
    )

    report = render_personal_close_report(facts)

    assert report.index("一句话结论：") < report.index("## 持仓")
    assert report.index("中际旭创（300308.SZ）") < report.index("贵州茅台（600519.SH）")
    assert report.index("贵州茅台（600519.SH）") < report.index("腾讯控股（00700.HK）")
    assert "部分数据不完整，详见数据质量" in report
    assert "持仓趋势整体稳定" not in report
    assert "当前未发现可确认的优先重点" in report
    assert "今日休市，数据截止至 2026-08-10" in report
    assert "港股实验弹簧" in report
    assert "未来两周：公司行动日历不完整" in report
    assert "暂无已知事件" not in report.split("腾讯控股（00700.HK）", 1)[1]


def test_report_renders_ma_spring_quality_and_all_actions():
    actions = (
        CorporateAction(
            "300308.SZ",
            "earnings",
            date(2026, 8, 15),
            "expected",
            "半年度报告披露",
        ),
        CorporateAction(
            "300308.SZ",
            "dividend",
            date(2026, 8, 16),
            "confirmed",
            "每10股派1元",
        ),
    )
    facts = PersonalReportFacts(
        date(2026, 8, 10),
        holdings=(
            stock_fact(
                "300308.SZ",
                "cn",
                "中际旭创",
                group="holding",
                spring_state="first_bullish_confirmed",
                actions=actions,
            ),
        ),
        watchlist=(),
    )

    report = render_personal_close_report(facts)

    assert "1 只持仓出现 A 股正式弹簧首阳确认" in report
    assert "MA5：下方 -1.0% ↘" in report
    assert "MA20：上方 +6.1% ↗" in report
    assert "MA200：上方 +35.2% ↗" in report
    assert "缩量比 25.0%｜近60日回踩 2 次" in report
    assert "实体比例 +1.2%｜当日涨跌 +1.8%｜标准首阳" in report
    assert "2026-08-15 半年度报告披露（预计）" in report
    assert "2026-08-16 每10股派1元（已确认）" in report


def test_report_only_says_no_known_events_when_coverage_is_complete():
    complete = stock_fact("300308.SZ", "cn", "中际旭创", group="holding")
    incomplete = stock_fact(
        "00700.HK",
        "hk",
        "腾讯控股",
        group="watchlist",
        coverage_complete=False,
        experimental=True,
    )

    report = render_personal_close_report(
        PersonalReportFacts(date(2026, 8, 10), (complete,), (incomplete,))
    )

    cn_section, hk_section = report.split("### 腾讯控股（00700.HK）")
    assert "未来两周：暂无已知事件" in cn_section
    assert "未来两周：公司行动日历不完整" in hk_section


def test_report_keeps_stock_with_unavailable_prices():
    issue = DataQualityIssue("price_unavailable", "行情获取失败", symbol="300308.SZ")
    fact = PersonalStockReportFact(
        config=PersonalStockConfig("300308.SZ", "cn", "中际旭创"),
        group="holding",
        market_open=True,
        as_of=None,
        adjusted_close=None,
        trend=None,
        spring=None,
        bullish_quality=None,
        actions=(),
        action_coverage_complete=False,
        issues=(issue,),
    )

    report = render_personal_close_report(
        PersonalReportFacts(date(2026, 8, 10), (fact,), (), (issue,))
    )

    assert "中际旭创（300308.SZ）" in report
    assert "收盘：不可用｜行情日期：不可用" in report
    assert "趋势：数据不足" in report
    assert "行情获取失败" in report


def test_headline_includes_pullback_and_formal_watchlist_compression():
    holding = stock_fact(
        "300308.SZ",
        "cn",
        "中际旭创",
        group="holding",
        trend_label="long_up_medium_pullback",
    )
    watch = stock_fact(
        "000001.SZ",
        "cn",
        "平安银行",
        group="watchlist",
        spring_state="compressed_watch",
    )

    report = render_personal_close_report(
        PersonalReportFacts(date(2026, 8, 10), (holding,), (watch,))
    )

    headline = report.split("一句话结论：", 1)[1].split("\n", 1)[0]
    assert "1 只持仓位于 MA20 下方或处于回踩/混合" in headline
    assert "1 只 A 股标的进入正式弹簧压紧观察" in headline
    assert "未见优先级更高" not in headline


def test_spring_reasons_are_rendered_in_chinese_without_internal_codes():
    weak = stock_fact(
        "300308.SZ",
        "cn",
        "中际旭创",
        group="holding",
        spring_state="weak_excluded",
    )
    weak.spring["reasons"] = [
        "ma20_broken",
        "third_support_test",
        "future_internal_code",
    ]

    report = render_personal_close_report(PersonalReportFacts(date(2026, 8, 10), (weak,), ()))

    assert "连续2日有效跌破 MA20" in report
    assert "近60日第2次回踩" in report
    assert "规则原因暂不可用" in report
    assert "ma20_broken" not in report
    assert "third_support_test" not in report
    assert "future_internal_code" not in report


def test_hk_unknown_reason_is_rendered_as_chinese_data_reason():
    hk = stock_fact(
        "00700.HK",
        "hk",
        "腾讯控股",
        group="watchlist",
        spring_state="unknown",
        experimental=True,
    )
    hk.spring["reasons"] = ["hk_insufficient_turnover"]

    report = render_personal_close_report(PersonalReportFacts(date(2026, 8, 10), (), (hk,)))

    assert "港股实验弹簧：不可判断：流动性不足" in report
    assert "hk_insufficient_turnover" not in report


def test_unsupported_actions_are_chinese_and_deduplicated_by_market():
    first = stock_fact(
        "300308.SZ",
        "cn",
        "中际旭创",
        group="holding",
        unsupported=("additional_issuance", "consolidation"),
    )
    second = stock_fact(
        "600519.SH",
        "cn",
        "贵州茅台",
        group="watchlist",
        unsupported=("consolidation", "additional_issuance"),
    )

    report = render_personal_close_report(
        PersonalReportFacts(date(2026, 8, 10), (first,), (second,))
    )

    assert report.count("A股：公司行动数据暂未覆盖增发、合股") == 1
    assert "additional_issuance" not in report
    assert "consolidation" not in report
    assert "部分数据不完整" not in report
    assert report.count("未来两周：已支持类型暂无已知事件") == 2


def _fired_trigger() -> dict:
    return {
        "rule_version": "spring-trigger-v1",
        "state": "trigger_fired",
        "as_of": "2026-08-10",
        "conditions": {
            "support_holding": True,
            "volume_shrunk": True,
            "trigger_day": True,
        },
        "support": {
            "low": 26.0,
            "high": 27.0,
            "window_days": 10,
            "min_close_in_window": 26.3,
            "days_in_zone": 8,
        },
        "shrink": {
            "consecutive_days": 2,
            "latest_turnover": 850_000_000.0,
            "max_turnover": 1_000_000_000.0,
        },
        "trigger": {
            "trade_date": "2026-08-10",
            "gain_pct": 0.031,
            "turnover": 1_600_000_000.0,
            "volume_ratio": 3.0,
        },
        "entry_plan": {
            "entry_reference": 27.4,
            "stop_price": 26.8,
            "stop_rule": "two_night_stop",
        },
        "reasons": [],
    }


def test_report_renders_spring_trigger_fired_with_entry_plan_and_headline():
    fact = stock_fact(
        "002001.SZ",
        "cn",
        "新和成",
        group="watchlist",
        spring_trigger=_fired_trigger(),
    )

    report = render_personal_close_report(PersonalReportFacts(date(2026, 8, 10), (), (fact,)))

    headline = report.split("一句话结论：", 1)[1].split("\n", 1)[0]
    assert "1 只标的弹簧扳机扣动" in headline
    assert "扳机信号：扳机扣动" in report
    assert "支撑 26.00-27.00｜近10日最低收盘 26.30｜区间内 8/10 日" in report
    assert "连续缩量 2 日（最新 8.5 亿）" in report
    assert "扳机日 涨幅 +3.1%、成交额 16.0 亿、日期 2026-08-10" in report
    assert "参与参考：试探价 27.40｜止损 26.80" in report
    assert "收盘跌破次日开盘无条件执行，两个隔夜止损" in report


def test_report_renders_spring_trigger_unknown_reason_without_internal_code():
    trigger = {
        "rule_version": "spring-trigger-v1",
        "state": "unknown",
        "as_of": None,
        "conditions": {
            "support_holding": False,
            "volume_shrunk": False,
            "trigger_day": False,
        },
        "support": None,
        "shrink": None,
        "trigger": None,
        "entry_plan": None,
        "reasons": ["turnover_unavailable"],
    }
    fact = stock_fact(
        "002001.SZ",
        "cn",
        "新和成",
        group="watchlist",
        spring_trigger=trigger,
    )

    report = render_personal_close_report(PersonalReportFacts(date(2026, 8, 10), (), (fact,)))

    assert "扳机信号：不可判断（成交额数据缺失）" in report
    assert "turnover_unavailable" not in report
    assert "参与参考" not in report


def test_report_renders_primed_headline_without_trigger():
    trigger = {
        "rule_version": "spring-trigger-v1",
        "state": "primed",
        "as_of": "2026-08-10",
        "conditions": {
            "support_holding": True,
            "volume_shrunk": True,
            "trigger_day": False,
        },
        "support": {
            "low": 26.0,
            "high": 27.0,
            "window_days": 10,
            "min_close_in_window": 26.4,
            "days_in_zone": 9,
        },
        "shrink": {
            "consecutive_days": 3,
            "latest_turnover": 800_000_000.0,
            "max_turnover": 1_000_000_000.0,
        },
        "trigger": None,
        "entry_plan": None,
        "reasons": [],
    }
    fact = stock_fact(
        "002001.SZ",
        "cn",
        "新和成",
        group="watchlist",
        spring_trigger=trigger,
    )

    report = render_personal_close_report(PersonalReportFacts(date(2026, 8, 10), (), (fact,)))

    headline = report.split("一句话结论：", 1)[1].split("\n", 1)[0]
    assert "1 只标的压紧就绪、等待扳机" in headline
    assert "扳机信号：压紧就绪" in report
    assert "连续缩量 3 日（最新 8.0 亿）" in report
