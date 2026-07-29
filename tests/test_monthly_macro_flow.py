import pytest

from lurker.application.monthly_macro_flow import (
    analyze_monthly_macro_flow,
)


def complete_snapshot() -> dict:
    return {
        "schema_version": 1,
        "report_month": "2025-01",
        "generated_at": "2026-07-26T12:00:00+00:00",
        "macro": {
            "macro_month": "2025-01",
            "household": {
                "current": 111.0,
                "previous_month": 109.0,
                "previous_year": 100.0,
                "previous_year_previous_month": 100.0,
            },
            "nonbank": {"current": 21.0, "previous_month": 20.0},
            "money_supply": {
                "current_m1_yoy_pct": 5.0,
                "current_m2_yoy_pct": 7.0,
                "previous_m1_yoy_pct": 4.0,
                "previous_m2_yoy_pct": 7.0,
            },
            "failures": [],
        },
        "leverage": {
            "trade_date": "2025-01-30",
            "current_financing_balance": 200.0,
            "previous_trade_date": "2024-12-31",
            "previous_financing_balance": 190.0,
            "a_share_circ_mv": 10_000.0,
            "failure": None,
        },
        "thresholds": {
            "household_deposit_yoy_pct": 12.0,
            "leverage_ratio_pct": 4.0,
            "financing_monthly_growth_pct": 20.0,
        },
        "sources": [],
        "failures": [],
    }


@pytest.mark.parametrize(
    ("household_current", "nonbank_current", "current_m1", "expected"),
    [
        (111.0, 21.0, 5.0, "牛市加速"),
        (113.0, 21.0, 5.0, "慢牛蓄力"),
        (113.0, 19.0, 5.0, "震荡磨底"),
        (113.0, 19.0, 3.0, "震荡磨底"),
    ],
)
def test_complete_state_matrix(
    household_current,
    nonbank_current,
    current_m1,
    expected,
):
    snapshot = complete_snapshot()
    snapshot["macro"]["household"]["current"] = household_current
    snapshot["macro"]["nonbank"]["current"] = nonbank_current
    snapshot["macro"]["money_supply"]["current_m1_yoy_pct"] = current_m1
    result = analyze_monthly_macro_flow(snapshot)
    assert result["report_mode"] == "classified"
    assert result["market_state"] == expected


def test_overheat_has_priority_when_other_data_is_missing():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"] = None
    snapshot["leverage"]["current_financing_balance"] = 401.0
    result = analyze_monthly_macro_flow(snapshot)
    assert result["market_state"] == "过热警报"
    assert result["leverage"]["status"] == "overheated"


def test_growth_overheat_survives_missing_market_cap():
    snapshot = complete_snapshot()
    snapshot["leverage"]["current_financing_balance"] = 121.0
    snapshot["leverage"]["previous_financing_balance"] = 100.0
    snapshot["leverage"]["a_share_circ_mv"] = None
    assert analyze_monthly_macro_flow(snapshot)["market_state"] == "过热警报"


def test_missing_dimension_yields_observation_not_negative_score():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"] = None
    result = analyze_monthly_macro_flow(snapshot)
    assert result["report_mode"] == "data_observation"
    assert result["market_state"] is None


@pytest.mark.parametrize(
    ("ratio", "growth"),
    [(4.0, 20.0), (3.99, 19.99)],
)
def test_exact_leverage_boundaries_are_not_overheated(ratio, growth):
    snapshot = complete_snapshot()
    snapshot["leverage"]["current_financing_balance"] = ratio * 100.0
    snapshot["leverage"]["previous_financing_balance"] = (
        snapshot["leverage"]["current_financing_balance"]
        / (1 + growth / 100)
    )
    result = analyze_monthly_macro_flow(snapshot)
    assert result["leverage"]["status"] == "healthy"


def test_exact_twelve_percent_is_deposit_dominant():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"]["current"] = 112.0
    result = analyze_monthly_macro_flow(snapshot)
    assert result["household"]["yoy_pct"] == pytest.approx(12.0)
    assert result["household"]["status"] == "deposit_dominant"


@pytest.mark.parametrize(
    ("current", "expected"),
    [(21.0, "rising"), (20.0, "flat"), (19.0, "falling")],
)
def test_nonbank_direction(current, expected):
    snapshot = complete_snapshot()
    snapshot["macro"]["nonbank"]["current"] = current
    result = analyze_monthly_macro_flow(snapshot)
    assert result["nonbank"]["status"] == expected


@pytest.mark.parametrize(
    ("current_m1", "expected"),
    [(5.0, "improving"), (4.0, "flat"), (3.0, "worsening")],
)
def test_money_spread_direction(current_m1, expected):
    snapshot = complete_snapshot()
    snapshot["macro"]["money_supply"]["current_m1_yoy_pct"] = current_m1
    result = analyze_monthly_macro_flow(snapshot)
    assert result["money_supply"]["status"] == expected


@pytest.mark.parametrize("denominator", [0.0, float("nan"), float("inf")])
def test_invalid_market_cap_yields_observation(denominator):
    snapshot = complete_snapshot()
    snapshot["leverage"]["a_share_circ_mv"] = denominator
    result = analyze_monthly_macro_flow(snapshot)
    assert result["leverage"]["status"] == "unknown"
    assert result["market_state"] is None


def test_unknown_snapshot_schema_is_rejected():
    snapshot = complete_snapshot()
    snapshot["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        analyze_monthly_macro_flow(snapshot)
