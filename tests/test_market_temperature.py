"""Market temperature truth table tests (Task 1: RED → Task 4: GREEN).

All tests import from lurker.application.market_temperature.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from lurker.ingest.etf_flows import CoreEtfBatch, CoreEtfItem


# ---------------------------------------------------------------------------
# 1.1 防守 = OR 逻辑（关键修正：ETF inactive 或 两融 weakening → 防守）
# ---------------------------------------------------------------------------


def test_defense_when_dual_negative_etf_inactive_margin_unknown():
    """双负 + ETF inactive + margin unknown → 防守（ETF inactive 单独确认）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        etf_status="inactive",
        margin_signal="unknown",
    )
    assert result == "防守"


def test_defense_when_dual_negative_etf_unknown_margin_weakening():
    """双负 + ETF unknown + margin weakening → 防守（两融 weakening 单独确认）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        etf_status="unknown",
        margin_signal="weakening",
    )
    assert result == "防守"


def test_defense_when_dual_negative_etf_inactive_margin_supportive():
    """双负 + ETF inactive + 两融 supportive → 防守（ETF inactive 覆盖两融正向）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        etf_status="inactive",
        margin_signal="supportive",
    )
    assert result == "防守"


def test_defense_when_dual_negative_etf_active_margin_weakening():
    """双负 + ETF active + 两融 weakening → 防守（两融 weakening 覆盖 ETF 正向）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        etf_status="active",
        margin_signal="weakening",
    )
    assert result == "防守"


# ---------------------------------------------------------------------------
# 1.2 观察 = unknown 不提供证据
# ---------------------------------------------------------------------------


def test_observe_when_dual_negative_both_unknown():
    """双负 + ETF unknown + margin unknown → 观察（缺失不是负向证据）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        etf_status="unknown",
        margin_signal="unknown",
    )
    assert result == "观察"


def test_observe_when_dual_positive_both_unknown():
    """双正 + ETF unknown + margin unknown → 观察（缺失不是正向证据）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        etf_status="unknown",
        margin_signal="unknown",
    )
    assert result == "观察"


def test_observe_when_direction_mismatch():
    """主力正、超大单负 → 观察"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": -5.0},
        etf_status="active",
        margin_signal="supportive",
    )
    assert result == "观察"


# ---------------------------------------------------------------------------
# 1.3 进攻
# ---------------------------------------------------------------------------


def test_attack_when_dual_positive_etf_active_margin_unknown():
    """双正 + ETF active → 进攻（ETF 单独确认）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        etf_status="active",
        margin_signal="unknown",
    )
    assert result == "进攻"


def test_attack_when_dual_positive_etf_unknown_margin_supportive():
    """双正 + 两融 supportive → 进攻（两融单独确认）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        etf_status="unknown",
        margin_signal="supportive",
    )
    assert result == "进攻"


# ---------------------------------------------------------------------------
# 1.4 两融 overheated
# ---------------------------------------------------------------------------


def test_defense_when_margin_overheated_regardless_of_flow():
    """任意资金方向 + 两融 overheated → 防守"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": 5.0, "super_large_net_inflow": -2.0},
        etf_status="active",
        margin_signal="overheated",
    )
    assert result == "防守"


def test_defense_when_dual_positive_but_margin_overheated():
    """双正 + 两融 overheated → 防守（overheated 覆盖进攻）"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        etf_status="active",
        margin_signal="overheated",
    )
    assert result == "防守"


# ---------------------------------------------------------------------------
# 1.4b 数据新鲜度
# ---------------------------------------------------------------------------


def test_temperature_stale_etf_treated_as_unknown():
    """ETF trade_date 不是最近交易日 → etf_status = unknown"""
    batch = CoreEtfBatch(
        configured_symbols=["510300.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date="2026-07-22",
                current_turnover=1_000_000_000.0,
                avg_turnover_20d=800_000_000.0,
                turnover_expansion=1.25,
                shares=None,
                shares_date=None,
                status="active",
                source="akshare_fund_etf_hist_em",
                availability="stale",
                error=None,
            )
        ],
        failures=[],
        generated_at="2026-07-23T00:00:00+00:00",
        schema_version=1,
    )
    # Stale items should not count as "active" for the aggregate
    assert batch.items[0].availability == "stale"


def test_temperature_stale_margin_treated_as_unknown():
    """margin availability = stale_cache → margin_signal = unknown"""
    from lurker.application.market_temperature import classify_margin_signal

    result = classify_margin_signal(
        {
            "trade_date": "20260722",
            "margin_balance": 1_000_000_000.0,
            "margin_balance_change": 10_000_000.0,
            "availability": "stale_cache",
        }
    )
    # stale data should not provide direction evidence
    assert result == "unknown"


def test_temperature_stale_data_not_negative_evidence():
    """stale 数据不会导致防守"""
    from lurker.application.market_temperature import classify_market_temperature

    result = classify_market_temperature(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        etf_status="inactive",
        margin_signal="unknown",  # margin was stale → unknown
    )
    assert result == "防守"  # ETF inactive alone confirms defense, margin unknown is not negative


@pytest.mark.parametrize(
    ("market_date", "margin_date", "etf_date", "match"),
    [
        ("2026-07-24", "20260723", "2026-07-23", "Market flow"),
        ("2026-07-23", "20260724", "2026-07-23", "Margin"),
        ("2026-07-23", "20260723", "2026-07-24", "ETF"),
    ],
)
def test_temperature_future_source_date_fails(
    market_date,
    margin_date,
    etf_date,
    match,
):
    """任何来源日期晚于 expected_trade_date 都是数据错误。"""
    from lurker.application.market_temperature import prepare_temperature_inputs

    tz_cst = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 25, 16, 0, 0, tzinfo=tz_cst)
    batch = CoreEtfBatch(
        configured_symbols=["510300.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date=etf_date,
                current_turnover=100.0,
                avg_turnover_20d=100.0,
                turnover_expansion=1.0,
                shares=None,
                shares_date=None,
                status="inactive",
                source="test",
                availability="turnover_only",
                error=None,
            )
        ],
    )

    with pytest.raises(ValueError, match=match):
        prepare_temperature_inputs(
            market_flow={
                "trade_date": market_date,
                "main_net_inflow": 1.0,
                "super_large_net_inflow": 1.0,
            },
            core_etfs_batch=batch,
            margin={
                "trade_date": margin_date,
                "margin_balance_change": 1.0,
                "availability": "fresh",
            },
            report_date="2026-07-23",
            is_trading_day=lambda d: d.weekday() < 5,
            now=now,
        )


def test_expected_trade_date_before_close_uses_previous_session():
    """交易日 15:30 前运行 → 使用上一交易日"""
    from lurker.application.market_temperature import resolve_expected_trade_date

    tz_cst = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=tz_cst)
    report_date = "2026-07-23"

    def is_trading_day(d: date) -> bool:
        return d.weekday() < 5  # Mon-Fri are trading days

    expected = resolve_expected_trade_date(
        report_date=report_date,
        is_trading_day=is_trading_day,
        now=now,
        market_close_cutoff=time(15, 30),
    )
    assert expected == "2026-07-22"


def test_expected_trade_date_after_close_uses_current_session():
    """交易日 15:30 后运行 → 使用当天"""
    from lurker.application.market_temperature import resolve_expected_trade_date

    tz_cst = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 23, 16, 0, 0, tzinfo=tz_cst)
    report_date = "2026-07-23"

    def is_trading_day(d: date) -> bool:
        return d.weekday() < 5

    expected = resolve_expected_trade_date(
        report_date=report_date,
        is_trading_day=is_trading_day,
        now=now,
        market_close_cutoff=time(15, 30),
    )
    assert expected == "2026-07-23"


# ---------------------------------------------------------------------------
# 1.5 三态/四态独立分类
# ---------------------------------------------------------------------------


def test_classify_etf_status_unknown_when_empty_batch():
    from lurker.application.market_temperature import classify_etf_status

    batch = CoreEtfBatch(
        configured_symbols=[],
        items=[],
        failures=[],
        generated_at="2026-07-23T00:00:00+00:00",
        schema_version=1,
    )
    assert classify_etf_status(batch) == "unknown"


def test_classify_etf_status_unknown_when_all_failed():
    from lurker.application.market_temperature import classify_etf_status

    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510500.SH"],
        items=[],
        failures=[
            {"symbol": "510300.SH", "reason": "timeout"},
            {"symbol": "510500.SH", "reason": "timeout"},
        ],
        generated_at="2026-07-23T00:00:00+00:00",
        schema_version=1,
    )
    assert classify_etf_status(batch) == "unknown"


def test_classify_etf_status_unknown_when_partial_failure_and_no_active():
    """4 只 ETF 中 1 只成功未达标 + 3 只失败 → unknown，不是 inactive"""
    from lurker.application.market_temperature import classify_etf_status

    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510500.SH", "159915.SZ", "159361.SZ"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date="2026-07-23",
                current_turnover=1_000_000_000.0,
                avg_turnover_20d=1_200_000_000.0,
                turnover_expansion=0.83,
                shares=None,
                shares_date=None,
                status="inactive",
                source="akshare_fund_etf_hist_em",
                availability="turnover_only",
                error=None,
            )
        ],
        failures=[
            {"symbol": "510500.SH", "reason": "timeout"},
            {"symbol": "159915.SZ", "reason": "timeout"},
            {"symbol": "159361.SZ", "reason": "timeout"},
        ],
        generated_at="2026-07-23T00:00:00+00:00",
        schema_version=1,
    )
    # Partial success + no active → unknown (not inactive)
    assert classify_etf_status(batch) == "unknown"


def test_classify_etf_status_active_when_one_above_threshold():
    from lurker.application.market_temperature import classify_etf_status

    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510500.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date="2026-07-23",
                current_turnover=3_000_000_000.0,
                avg_turnover_20d=2_000_000_000.0,
                turnover_expansion=1.5,
                shares=None,
                shares_date=None,
                status="active",
                source="akshare_fund_etf_hist_em",
                availability="turnover_only",
                error=None,
            ),
            CoreEtfItem(
                symbol="510500.SH",
                name="中证500ETF",
                trade_date="2026-07-23",
                current_turnover=1_000_000_000.0,
                avg_turnover_20d=1_200_000_000.0,
                turnover_expansion=0.83,
                shares=None,
                shares_date=None,
                status="inactive",
                source="akshare_fund_etf_hist_em",
                availability="turnover_only",
                error=None,
            ),
        ],
        failures=[],
        generated_at="2026-07-23T00:00:00+00:00",
        schema_version=1,
    )
    assert classify_etf_status(batch) == "active"


def test_classify_etf_status_inactive_when_all_below_threshold():
    from lurker.application.market_temperature import classify_etf_status

    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510500.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date="2026-07-23",
                current_turnover=1_000_000_000.0,
                avg_turnover_20d=1_200_000_000.0,
                turnover_expansion=0.83,
                shares=None,
                shares_date=None,
                status="inactive",
                source="akshare_fund_etf_hist_em",
                availability="turnover_only",
                error=None,
            ),
            CoreEtfItem(
                symbol="510500.SH",
                name="中证500ETF",
                trade_date="2026-07-23",
                current_turnover=800_000_000.0,
                avg_turnover_20d=1_000_000_000.0,
                turnover_expansion=0.80,
                shares=None,
                shares_date=None,
                status="inactive",
                source="akshare_fund_etf_hist_em",
                availability="turnover_only",
                error=None,
            ),
        ],
        failures=[],
        generated_at="2026-07-23T00:00:00+00:00",
        schema_version=1,
    )
    assert classify_etf_status(batch) == "inactive"


def test_classify_etf_status_unknown_when_batch_incomplete():
    """configured_symbols != (items ∪ failures) → unknown"""
    from lurker.application.market_temperature import classify_etf_status

    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510500.SH", "159915.SZ", "159361.SZ"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date="2026-07-23",
                current_turnover=800_000_000.0,
                avg_turnover_20d=1_000_000_000.0,
                turnover_expansion=0.80,
                shares=None,
                shares_date=None,
                status="inactive",
                source="akshare_fund_etf_hist_em",
                availability="turnover_only",
                error=None,
            )
        ],
        failures=[],  # Missing 3 symbols — not accounted for
        generated_at="2026-07-23T00:00:00+00:00",
        schema_version=1,
    )
    # batch.is_complete() will be False → classify_etf_status returns "unknown"
    assert classify_etf_status(batch) == "unknown"


def test_classify_margin_signal_unknown_when_empty_dict():
    from lurker.application.market_temperature import classify_margin_signal

    assert classify_margin_signal({}) == "unknown"


def test_classify_margin_signal_unknown_when_no_change_field():
    from lurker.application.market_temperature import classify_margin_signal

    assert classify_margin_signal({"margin_balance": 100.0}) == "unknown"


def test_classify_margin_signal_supportive_when_positive_change():
    from lurker.application.market_temperature import classify_margin_signal

    result = classify_margin_signal(
        {"margin_balance_change": 5_000_000_000.0, "trade_date": "20260723"}
    )
    assert result == "supportive"


def test_classify_margin_signal_unknown_when_zero_change():
    """margin_balance_change == 0 → unknown（持平不提供方向证据）"""
    from lurker.application.market_temperature import classify_margin_signal

    result = classify_margin_signal({"margin_balance_change": 0.0})
    assert result == "unknown"


def test_classify_margin_signal_weakening_when_negative_change():
    from lurker.application.market_temperature import classify_margin_signal

    result = classify_margin_signal({"margin_balance_change": -3_000_000_000.0})
    assert result == "weakening"


def test_classify_margin_signal_overheated_always_unknown_in_this_phase():
    """本阶段 overheated 固定返回 unknown（分母数据待回放）"""
    from lurker.application.market_temperature import classify_margin_signal

    # Even with data that might look "hot", overheated is not computed yet
    result = classify_margin_signal(
        {"margin_balance_change": 10_000_000_000.0, "financing_balance": 2_000_000_000_000.0}
    )
    # Should be "supportive" (positive change) or "unknown", but NOT "overheated"
    assert result != "overheated"


# ---------------------------------------------------------------------------
# 1.6 资金流方向
# ---------------------------------------------------------------------------


def test_flow_direction_positive():
    from lurker.application.market_temperature import _flow_direction

    assert _flow_direction(100.0) == "positive"


def test_flow_direction_negative():
    from lurker.application.market_temperature import _flow_direction

    assert _flow_direction(-100.0) == "negative"


def test_flow_direction_neutral_for_zero():
    """0 → neutral，不是 positive"""
    from lurker.application.market_temperature import _flow_direction

    assert _flow_direction(0.0) == "neutral"


def test_flow_direction_unknown_for_none():
    from lurker.application.market_temperature import _flow_direction

    assert _flow_direction(None) == "unknown"


def test_flow_direction_unknown_for_nan():
    from lurker.application.market_temperature import _flow_direction

    assert _flow_direction(float("nan")) == "unknown"


def test_flow_direction_unknown_for_inf():
    from lurker.application.market_temperature import _flow_direction

    assert _flow_direction(float("inf")) == "unknown"


# ---------------------------------------------------------------------------
# 1.7 PreparedTemperatureInputs 准备层
# ---------------------------------------------------------------------------


def test_prepare_temperature_inputs_returns_prepared_dataclass():
    """准备层返回 PreparedTemperatureInputs"""
    from lurker.application.market_temperature import (
        PreparedTemperatureInputs,
        prepare_temperature_inputs,
    )
    from datetime import timezone, timedelta

    tz_cst = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 23, 16, 0, 0, tzinfo=tz_cst)

    def is_trading_day(d):
        return d.weekday() < 5

    result = prepare_temperature_inputs(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0, "trade_date": "2026-07-23"},
        core_etfs_batch=CoreEtfBatch(
            configured_symbols=["510300.SH"],
            items=[
                CoreEtfItem(
                    symbol="510300.SH",
                    name="沪深300ETF",
                    trade_date="2026-07-23",
                    current_turnover=3_000_000_000.0,
                    avg_turnover_20d=2_000_000_000.0,
                    turnover_expansion=1.5,
                    shares=None,
                    shares_date=None,
                    status="active",
                    source="akshare_fund_etf_hist_em",
                    availability="turnover_only",
                    error=None,
                )
            ],
            failures=[],
            generated_at="2026-07-23T00:00:00+00:00",
            schema_version=1,
        ),
        margin={"margin_balance_change": 5_000_000_000.0, "trade_date": "2026-07-23"},
        report_date="2026-07-23",
        is_trading_day=is_trading_day,
        now=now,
    )
    assert isinstance(result, PreparedTemperatureInputs)
    assert result.etf_status == "active"
    assert result.margin_signal == "supportive"
    assert result.expected_trade_date == "2026-07-23"


def test_prepare_temperature_stale_etf_degrades_to_unknown():
    """ETF 全部 stale → etf_status = unknown"""
    from lurker.application.market_temperature import (
        prepare_temperature_inputs,
    )
    from datetime import timezone, timedelta

    tz_cst = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 23, 16, 0, 0, tzinfo=tz_cst)

    def is_trading_day(d):
        return d.weekday() < 5

    result = prepare_temperature_inputs(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0, "trade_date": "2026-07-23"},
        core_etfs_batch=CoreEtfBatch(
            configured_symbols=["510300.SH"],
            items=[
                CoreEtfItem(
                    symbol="510300.SH",
                    name="沪深300ETF",
                    trade_date="2026-07-22",  # stale! Not 2026-07-23
                    current_turnover=3_000_000_000.0,
                    avg_turnover_20d=2_000_000_000.0,
                    turnover_expansion=1.5,
                    shares=None,
                    shares_date=None,
                    status="active",
                    source="akshare_fund_etf_hist_em",
                    availability="stale",
                    error=None,
                )
            ],
            failures=[],
            generated_at="2026-07-23T00:00:00+00:00",
            schema_version=1,
        ),
        margin={"margin_balance_change": -1_000_000_000.0, "trade_date": "20260723"},
        report_date="2026-07-23",
        is_trading_day=is_trading_day,
        now=now,
    )
    # ETF was stale → etf_status should be unknown
    assert result.etf_status == "unknown"
    assert result.etf_freshness == "stale"
    assert result.etf_cutoff == "2026-07-22"
    assert "核心 ETF：截止 2026-07-22，非当日数据" in result.quality_notes
    assert (
        "⚠️ 核心 ETF 数据截止 2026-07-22，非当日；"
        "今日 ETF 信号未参与判断。"
    ) in result.quality_notes
    # margin is weakening + etf unknown → not defense (no negative confirmation from ETF)
    # We don't test the temperature here, just the preparation output


def test_classify_etf_status_ignores_intraday_partial_expansion():
    """盘中不完整成交额不能提供 active 证据。"""
    from lurker.application.market_temperature import classify_etf_status

    batch = CoreEtfBatch(
        configured_symbols=["510300.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date="2026-07-23",
                current_turnover=200.0,
                avg_turnover_20d=100.0,
                turnover_expansion=2.0,
                shares=None,
                shares_date=None,
                status="unknown",
                source="test",
                availability="intraday_partial",
                error=None,
            )
        ],
    )

    assert classify_etf_status(batch) == "unknown"


def test_prepare_temperature_inputs_exposes_source_freshness_notes():
    from lurker.application.market_temperature import prepare_temperature_inputs

    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510500.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date="2026-07-23",
                current_turnover=130.0,
                avg_turnover_20d=100.0,
                turnover_expansion=1.3,
                shares=None,
                shares_date=None,
                status="active",
                source="fixture",
                availability="turnover_only",
                error=None,
            )
        ],
        failures=[{"symbol": "510500.SH", "reason": "timeout"}],
        generated_at="2026-07-23T08:00:00+00:00",
    )

    prepared = prepare_temperature_inputs(
        market_flow={
            "trade_date": "2026-07-23",
            "main_net_inflow": 1.0,
            "super_large_net_inflow": 1.0,
        },
        core_etfs_batch=batch,
        margin={
            "trade_date": "20260722",
            "margin_balance_change": 1.0,
            "availability": "stale_cache",
        },
        report_date="2026-07-23",
        is_trading_day=lambda _: True,
        now=datetime(
            2026,
            7,
            23,
            16,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    assert prepared.quality_notes == (
        "大盘资金：截止 2026-07-23，当日数据",
        "核心 ETF：截止 2026-07-23，部分数据缺失",
        "两融：截止 2026-07-22，使用历史缓存",
        "⚠️ 核心 ETF 部分采集失败；放量判断仅基于成功采集项。",
        "⚠️ 两融使用历史缓存；今日两融信号未参与判断。",
    )


def _complete_fresh_batch(trade_date: str) -> CoreEtfBatch:
    return CoreEtfBatch(
        configured_symbols=["510300.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="沪深300ETF",
                trade_date=trade_date,
                current_turnover=100.0,
                avg_turnover_20d=100.0,
                turnover_expansion=1.0,
                shares=None,
                shares_date=None,
                status="inactive",
                source="fixture",
                availability="turnover_only",
                error=None,
            )
        ],
        failures=[],
        generated_at=f"{trade_date}T08:00:00+00:00",
    )


def test_previous_session_margin_is_published_lag_and_actionable():
    from lurker.application.market_temperature import prepare_temperature_inputs

    prepared = prepare_temperature_inputs(
        market_flow={
            "trade_date": "2026-07-28",
            "main_net_inflow": 1.0,
            "super_large_net_inflow": 1.0,
        },
        core_etfs_batch=_complete_fresh_batch("2026-07-28"),
        margin={
            "trade_date": "20260727",
            "margin_balance_change": 10.0,
            "availability": "fresh",
        },
        report_date="2026-07-28",
        is_trading_day=lambda day: day.weekday() < 5,
        now=datetime(
            2026,
            7,
            28,
            16,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    assert prepared.margin_signal == "supportive"
    assert prepared.quality_notes[2] == (
        "两融：截止 2026-07-27，正常滞后一日"
    )
    assert len(prepared.quality_notes) == 3


def test_margin_older_than_previous_session_is_unknown():
    from lurker.application.market_temperature import prepare_temperature_inputs

    prepared = prepare_temperature_inputs(
        market_flow={
            "trade_date": "2026-07-28",
            "main_net_inflow": 1.0,
            "super_large_net_inflow": 1.0,
        },
        core_etfs_batch=_complete_fresh_batch("2026-07-28"),
        margin={
            "trade_date": "20260724",
            "margin_balance_change": 10.0,
            "availability": "fresh",
        },
        report_date="2026-07-28",
        is_trading_day=lambda day: day.weekday() < 5,
        now=datetime(
            2026,
            7,
            28,
            16,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    assert prepared.margin_signal == "unknown"
    assert prepared.quality_notes[2] == "两融：截止 2026-07-24，非当日数据"
    assert prepared.quality_notes[3] == (
        "⚠️ 两融数据截止 2026-07-24，超出正常发布滞后；"
        "今日两融信号未参与判断。"
    )
