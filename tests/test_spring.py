from __future__ import annotations

from datetime import date, timedelta
import math

import pytest

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


def _trending_state_bars() -> list[dict[str, object]]:
    values = _bars()
    closes = [80.0 + index * 0.5 for index in range(59)]
    closes.extend(109.0 + index * 0.1 for index in range(20))
    for index, (row, close) in enumerate(zip(values, closes, strict=True)):
        row.update(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.995,
                "close": close,
                "volume": 1_000_000.0 if index < 75 else 200_000.0,
            }
        )
    return values


def _bars_for_touch_flags(flags: list[bool]) -> list[dict[str, object]]:
    assert len(flags) == 60
    values = _bars()
    closes = [80.0 + index * 0.25 for index in range(19)]
    for touched in flags:
        prior_average = sum(closes[-19:]) / 19
        closes.append(prior_average if touched else prior_average * 1.10)
    for row, close in zip(values, closes, strict=True):
        row.update(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.995,
                "close": close,
                "volume": 1_000_000.0,
            }
        )
    return values


def _set_last_close_distance(values: list[dict[str, object]], distance: float) -> None:
    prior_sum = sum(float(row["close"]) for row in values[-20:-1])
    close = (1.0 + distance) * prior_sum / (19.0 - distance)
    values[-1].update(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.995,
            "close": close,
        }
    )


def _golden_cases() -> list[tuple[list[dict[str, object]], dict[str, object]]]:
    watch = _trending_state_bars()

    bullish = _trending_state_bars()
    bullish[-1]["open"] = float(bullish[-1]["close"]) - 0.05
    bullish[-1]["volume"] = 3_000_000.0

    uncompressed = _trending_state_bars()
    for row in uncompressed[-3:]:
        row["volume"] = 600_000.0

    third_flags = [False] * 60
    third_flags[40] = True
    third_flags[50] = True
    third_flags[59] = True
    third = _bars_for_touch_flags(third_flags)

    broken_flags = [False] * 60
    broken_flags[55] = True
    broken = _bars_for_touch_flags(broken_flags)
    for index in (-2, -1):
        prior_sum = sum(float(row["close"]) for row in broken[index - 19 : index])
        close = 0.95 * prior_sum / 19
        broken[index].update(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.995,
                "close": close,
            }
        )

    return [
        (
            watch,
            {
                "rule_version": "ma20-v1",
                "state": "compressed_watch",
                "as_of": "2026-03-20",
                "ma20_distance_pct": 0.00864029104138253,
                "volume_compression_ratio": 0.2,
                "support_touch_count_60d": 1,
                "min_ma20_distance_2d_pct": 0.008602248418369651,
                "reasons": [],
            },
        ),
        (
            bullish,
            {
                "rule_version": "ma20-v1",
                "state": "first_bullish_confirmed",
                "as_of": "2026-03-20",
                "ma20_distance_pct": 0.00864029104138253,
                "volume_compression_ratio": 0.2,
                "support_touch_count_60d": 1,
                "min_ma20_distance_2d_pct": 0.008602248418369651,
                "reasons": [],
            },
        ),
        (
            uncompressed,
            {
                "rule_version": "ma20-v1",
                "state": "weak_excluded",
                "as_of": "2026-03-20",
                "ma20_distance_pct": 0.00864029104138253,
                "volume_compression_ratio": 0.6,
                "support_touch_count_60d": 1,
                "min_ma20_distance_2d_pct": 0.008602248418369651,
                "reasons": ["volume_not_compressed"],
            },
        ),
        (
            third,
            {
                "rule_version": "ma20-v1",
                "state": "weak_excluded",
                "as_of": "2026-03-20",
                "ma20_distance_pct": 0.0,
                "volume_compression_ratio": 1.0,
                "support_touch_count_60d": 3,
                "min_ma20_distance_2d_pct": 0.0,
                "reasons": ["third_support_test", "volume_not_compressed"],
            },
        ),
        (
            broken,
            {
                "rule_version": "ma20-v1",
                "state": "weak_excluded",
                "as_of": "2026-03-20",
                "ma20_distance_pct": -0.04761904761904778,
                "volume_compression_ratio": None,
                "support_touch_count_60d": 1,
                "min_ma20_distance_2d_pct": -0.04761904761904778,
                "reasons": ["ma20_broken"],
            },
        ),
    ]


@pytest.mark.parametrize(("bars", "expected"), _golden_cases())
def test_ma20_v1_full_result_golden(
    bars: list[dict[str, object]],
    expected: dict[str, object],
) -> None:
    assert analyze_spring_bars(bars) == expected


def test_rising_ma20_touch_with_compression_is_watch():
    result = analyze_spring_bars(_trending_state_bars())

    assert result["state"] == "compressed_watch"
    assert result["support_touch_count_60d"] == 1
    assert result["volume_compression_ratio"] == 0.2


def test_first_bullish_after_compression_is_confirmed():
    values = _trending_state_bars()
    values[-1]["open"] = float(values[-1]["close"]) - 0.05

    result = analyze_spring_bars(values)

    assert result["state"] == "first_bullish_confirmed"
    assert result["reasons"] == []


def test_first_bullish_uses_prior_three_days_not_its_own_volume():
    values = _trending_state_bars()
    values[-1]["open"] = float(values[-1]["close"]) - 0.05
    values[-1]["volume"] = 3_000_000.0

    result = analyze_spring_bars(values)

    assert result["state"] == "first_bullish_confirmed"
    assert result["volume_compression_ratio"] == 0.2


def test_first_bullish_volume_does_not_overflow_to_next_day():
    values = _trending_state_bars()
    values[-2]["open"] = float(values[-2]["close"]) - 0.05
    values[-2]["volume"] = 3_000_000.0

    result = analyze_spring_bars(values)

    assert result["state"] == "none"
    assert "volume_not_compressed" not in result["reasons"]


def test_uncompressed_touch_is_weak_excluded():
    values = _trending_state_bars()
    for row in values[-3:]:
        row["volume"] = 600_000.0

    result = analyze_spring_bars(values)

    assert result["state"] == "weak_excluded"
    assert result["reasons"] == ["volume_not_compressed"]


def test_volume_ratio_at_exactly_thirty_percent_is_compressed():
    values = _trending_state_bars()
    for row in values[-3:]:
        row["volume"] = 300_000.0

    result = analyze_spring_bars(values)

    assert result["state"] == "compressed_watch"
    assert result["volume_compression_ratio"] == 0.3


def test_third_independent_touch_is_weak_excluded():
    flags = [False] * 60
    flags[40] = True
    flags[50] = True
    flags[59] = True
    values = _bars_for_touch_flags(flags)

    result = analyze_spring_bars(values)

    assert result["state"] == "weak_excluded"
    assert result["support_touch_count_60d"] == 3
    assert "third_support_test" in result["reasons"]


def test_latest_two_day_break_after_recent_touch_is_weak_excluded():
    flags = [False] * 60
    flags[55] = True
    values = _bars_for_touch_flags(flags)
    for index in (-2, -1):
        prior_sum = sum(float(row["close"]) for row in values[index - 19 : index])
        close = 0.95 * prior_sum / 19
        values[index].update(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.995,
                "close": close,
            }
        )

    result = analyze_spring_bars(values)

    assert result["state"] == "weak_excluded"
    assert result["reasons"] == ["ma20_broken"]


def test_ma20_down_does_not_create_watch_or_confirmation():
    values = _trending_state_bars()
    for index, row in enumerate(values[-30:]):
        close = 112.0 - index * 0.2
        row.update({"open": close, "high": close * 1.01, "low": close * 0.995, "close": close})

    result = analyze_spring_bars(values)

    assert result["state"] == "none"


def test_far_above_ma20_is_none():
    values = _bars()
    for index, row in enumerate(values):
        close = 50.0 * (1.03**index)
        row.update({"open": close, "high": close * 1.01, "low": close * 0.995, "close": close})
    for row in values[-3:]:
        row["volume"] = 200_000.0

    result = analyze_spring_bars(values)

    assert result["state"] == "none"


def test_long_shadow_with_close_above_band_and_no_first_bullish_is_none():
    values = _trending_state_bars()
    prior_sum = sum(float(row["close"]) for row in values[-20:-1])
    close = 1.05 * prior_sum / 18.95
    ma20 = (prior_sum + close) / 20
    values[-1].update(
        {
            "open": close,
            "high": close * 1.01,
            "low": ma20 * 1.01,
            "close": close,
            "volume": 600_000.0,
        }
    )

    result = analyze_spring_bars(values)

    assert result["ma20_distance_pct"] > 0.02
    assert result["state"] == "none"
    assert result["reasons"] == []


def test_ma20_distance_boundaries_are_inside_support_band():
    for distance in (-0.02, 0.02):
        values = _trending_state_bars()
        _set_last_close_distance(values, distance)
        result = analyze_spring_bars(values)

        assert math.isclose(result["ma20_distance_pct"], distance, abs_tol=1e-12)
        assert result["state"] == "compressed_watch"
