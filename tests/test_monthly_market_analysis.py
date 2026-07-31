from dataclasses import replace

import pytest

from lurker.application.monthly_market_analysis import (
    analyze_monthly_market,
    combine_latest_direction,
)
from lurker.application.weekly_flow_report import WeeklyFlowSummary


def _monthly(
    *,
    report_mode="classified",
    market_state="震荡磨底",
    household="relocation_signal",
    nonbank="falling",
    money="worsening",
    leverage="healthy",
):
    return {
        "report_mode": report_mode,
        "market_state": market_state,
        "household": {"status": household},
        "nonbank": {"status": nonbank},
        "money_supply": {"status": money},
        "leverage": {"status": leverage},
    }


def _weekly(**changes):
    base = WeeklyFlowSummary(
        availability="available",
        start_date="2026-07-27",
        end_date="2026-07-31",
        snapshot_count=5,
        temperature_counts={"进攻": 0, "观察": 3, "防守": 2},
        main_net_inflow_sum=-46_268_411_904.0,
        super_large_net_inflow_sum=14_686_212_096.0,
        latest_etf_status="unknown",
        latest_margin_signal="weakening",
        continued_sectors=("通信设备",),
        new_sectors=("机器人",),
        ebb_sectors=("银行",),
        failure_count=0,
        quality_notes=(),
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    ("etf", "margin", "expected"),
    [
        ("active", "unknown", "supportive"),
        ("unknown", "supportive", "supportive"),
        ("inactive", "unknown", "weakening"),
        ("unknown", "weakening", "weakening"),
        ("active", "weakening", "mixed"),
        ("inactive", "supportive", "mixed"),
        ("unknown", "unknown", "unknown"),
    ],
)
def test_latest_direction_is_conflict_safe(etf, margin, expected):
    assert combine_latest_direction(etf, margin) == expected


def test_july_real_shape_is_observe_inventory_structure():
    result = analyze_monthly_market(_monthly(), _weekly())

    assert result.stance == "观察"
    assert result.market_stage == "存量结构"
    assert result.main_contradiction == "场外资金已经松动，但机构承接和资金活化尚未确认。"
    assert "居民存款出现搬家信号" in result.supporting_evidence
    assert "非银存款减少，机构承接尚未确认" in result.constraining_evidence


def test_three_layer_confirmation_is_attack_ready():
    result = analyze_monthly_market(
        _monthly(market_state="慢牛蓄力", nonbank="rising", money="improving"),
        _weekly(
            temperature_counts={"进攻": 3, "观察": 1, "防守": 1},
            main_net_inflow_sum=10.0,
            super_large_net_inflow_sum=5.0,
            latest_etf_status="active",
            latest_margin_signal="unknown",
        ),
    )

    assert (result.stance, result.market_stage) == ("进攻准备", "增量确认")


def test_high_frequency_weakening_can_override_supportive_macro_tactically():
    result = analyze_monthly_market(
        _monthly(market_state="牛市加速", nonbank="rising", money="improving"),
        _weekly(
            temperature_counts={"进攻": 0, "观察": 1, "防守": 4},
            main_net_inflow_sum=-10.0,
            super_large_net_inflow_sum=-5.0,
            latest_etf_status="unknown",
            latest_margin_signal="weakening",
        ),
    )

    assert (result.stance, result.market_stage) == ("防守", "减量防守")
    assert "宏观支持仍在，但周、日资金已经转弱" in result.main_contradiction


def test_latest_conflict_provides_no_direction_confirmation():
    result = analyze_monthly_market(
        _monthly(market_state="慢牛蓄力", nonbank="rising", money="improving"),
        _weekly(
            temperature_counts={"进攻": 3, "观察": 1, "防守": 1},
            main_net_inflow_sum=10.0,
            super_large_net_inflow_sum=5.0,
            latest_etf_status="active",
            latest_margin_signal="weakening",
        ),
    )

    assert result.latest_direction == "mixed"
    assert (result.stance, result.market_stage) == ("观察", "存量结构")
    assert "ETF 与两融方向分化，最新层暂不提供方向确认" in result.daily_view


def test_overheated_leverage_has_highest_priority():
    result = analyze_monthly_market(
        _monthly(report_mode="data_observation", market_state=None, leverage="overheated"),
        _weekly(availability="unavailable", snapshot_count=0),
    )

    assert (result.stance, result.market_stage) == ("防守", "杠杆过热")


@pytest.mark.parametrize("availability", ["partial", "unavailable"])
def test_insufficient_weekly_context_forces_observation(availability):
    result = analyze_monthly_market(
        _monthly(),
        _weekly(availability=availability, snapshot_count=2 if availability == "partial" else 0),
    )

    assert (result.stance, result.market_stage) == ("观察", "数据不足")
