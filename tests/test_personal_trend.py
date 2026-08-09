from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from lurker.domain.personal_close import (
    analyze_personal_trend,
    project_first_bullish_quality,
)


def _prices(closes: list[float]) -> pd.DataFrame:
    start = date(2025, 1, 1)
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=index) for index in range(len(closes))],
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "raw_close": closes,
            "adj_close": closes,
            "volume": [1_000_000.0] * len(closes),
        }
    )


def test_uptrend_is_long_and_medium_term_strong():
    frame = _prices([100.0 + index * 0.1 for index in range(220)])

    result = analyze_personal_trend(frame)

    assert result.label == "long_medium_strong"
    assert result.ma5.direction == "up"
    assert result.ma20.direction == "up"
    assert result.ma200.direction == "up"
    assert result.ma20.distance_pct > 0


def test_below_ma20_above_rising_ma200_is_medium_pullback():
    closes = [100.0 + index * 0.1 for index in range(220)]
    closes[-1] = 115.0

    result = analyze_personal_trend(_prices(closes))

    assert result.label == "long_up_medium_pullback"
    assert result.ma20.distance_pct < 0
    assert result.ma200.distance_pct > 0


def test_two_days_below_ma200_is_long_structure_weakened():
    closes = [100.0] * 220
    closes[-2:] = [80.0, 79.0]

    result = analyze_personal_trend(_prices(closes))

    assert result.label == "long_structure_weakened"


def test_first_day_below_ma200_is_testing_ma200():
    closes = [100.0] * 220
    closes[-1] = 80.0

    result = analyze_personal_trend(_prices(closes))

    assert result.label == "testing_ma200"


def test_cross_above_non_rising_ma20_is_medium_repair():
    closes = [100.0] * 190
    closes.extend(110.0 - index * 0.2 for index in range(29))
    closes.append(110.0)

    result = analyze_personal_trend(_prices(closes))

    assert result.label == "medium_repair"
    assert result.ma20.direction != "up"


def test_equal_to_moving_averages_is_mixed_not_above_or_below():
    result = analyze_personal_trend(_prices([100.0] * 220))

    assert result.label == "mixed"
    assert result.ma5.distance_pct == 0.0
    assert result.ma20.distance_pct == 0.0
    assert result.ma200.distance_pct == 0.0


def test_less_than_220_rows_has_partial_facts_but_insufficient_composite():
    result = analyze_personal_trend(_prices([100.0] * 219))

    assert result.label == "data_insufficient"
    assert result.ma5 is not None
    assert result.ma20 is not None
    assert result.ma200 is None


@pytest.mark.parametrize(
    ("entity_ratio", "label"),
    [
        (0.004, "micro"),
        (0.005, "standard"),
        (0.02, "standard"),
        (0.021, "strong"),
    ],
)
def test_first_bullish_quality_boundaries(entity_ratio, label):
    as_of = date(2026, 8, 10)
    frame = pd.DataFrame(
        [
            {"trade_date": as_of - timedelta(days=1), "open": 100.0, "close": 100.0},
            {
                "trade_date": as_of,
                "open": 100.0,
                "close": 100.0 + entity_ratio * 100.0,
            },
        ]
    )
    spring = {"state": "first_bullish_confirmed", "as_of": as_of.isoformat()}
    before = dict(spring)

    quality = project_first_bullish_quality(spring, frame)

    assert quality is not None
    assert quality.label == label
    assert quality.entity_ratio == pytest.approx(entity_ratio)
    assert spring == before


def test_first_bullish_quality_rejects_cross_date_join():
    frame = pd.DataFrame(
        [
            {"trade_date": date(2026, 8, 9), "open": 100.0, "close": 100.0},
            {"trade_date": date(2026, 8, 10), "open": 100.0, "close": 101.0},
        ]
    )
    spring = {"state": "first_bullish_confirmed", "as_of": "2026-08-08"}

    assert project_first_bullish_quality(spring, frame) is None
