from __future__ import annotations

from datetime import date, timedelta
import math

from lurker.domain.spring import _merge_touch_segments, analyze_spring_bars


def _bars(count: int = 79) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    return [
        {
            "trade_date": start + timedelta(days=index),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        }
        for index in range(count)
    ]


def test_less_than_79_bars_is_unknown():
    result = analyze_spring_bars(_bars(78))

    assert result["state"] == "unknown"
    assert result["reasons"] == ["insufficient_history"]


def test_duplicate_date_in_latest_window_is_unknown():
    values = _bars()
    values[-1]["trade_date"] = values[-2]["trade_date"]

    result = analyze_spring_bars(values)

    assert result["state"] == "unknown"
    assert result["reasons"] == ["duplicate_trade_date"]


def test_unparseable_date_is_unknown():
    values = _bars()
    values[-1]["trade_date"] = "not-a-date"

    result = analyze_spring_bars(values)

    assert result["state"] == "unknown"
    assert result["reasons"] == ["invalid_trade_date"]
    assert result["as_of"] is None


def test_parseable_out_of_order_dates_are_sorted():
    values = _bars()
    values[-1], values[-2] = values[-2], values[-1]

    result = analyze_spring_bars(values)

    assert result["as_of"] == "2026-03-20"
    assert result["state"] != "unknown"


def test_invalid_price_in_latest_window_is_unknown():
    values = _bars()
    values[-1]["close"] = math.nan

    result = analyze_spring_bars(values)

    assert result["reasons"] == ["invalid_price_data"]


def test_invalid_volume_in_latest_window_is_unknown():
    values = _bars()
    values[-1]["volume"] = 0

    result = analyze_spring_bars(values)

    assert result["reasons"] == ["invalid_volume_data"]


def test_invalid_bar_before_latest_79_does_not_close_current_scan():
    values = _bars(80)
    values[0]["volume"] = 0

    result = analyze_spring_bars(values)

    assert result["state"] != "unknown"


def test_contiguous_touch_days_form_one_segment():
    assert _merge_touch_segments([False, True, True, True, False]) == [(1, 3)]


def test_segments_with_anchor_distance_less_than_five_are_merged():
    flags = [False, True, False, False, True, False, False, False, True]

    assert _merge_touch_segments(flags) == [(1, 8)]


def test_segments_with_anchor_distance_exactly_five_stay_separate():
    flags = [False, True, False, False, False, False, True]

    assert _merge_touch_segments(flags) == [(1, 1), (6, 6)]
