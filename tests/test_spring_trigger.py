from datetime import date, timedelta
from typing import Any

import pytest

from lurker.config import SpringTriggerConfig
from lurker.domain.spring_trigger import analyze_spring_trigger


def trigger_config(**overrides: Any) -> SpringTriggerConfig:
    values = {
        "support_low": 26.0,
        "support_high": 27.0,
        "shrink_max_turnover": 1_000_000_000.0,
        "shrink_min_days": 2,
        "trigger_min_gain_pct": 0.02,
        "trigger_min_turnover": 1_500_000_000.0,
        "trigger_min_volume_ratio": 1.5,
        "support_window_days": 10,
        "trigger_active_days": 3,
    }
    values.update(overrides)
    return SpringTriggerConfig(**values)


def make_bars(
    count: int = 60,
    *,
    closes: list[float] | None = None,
    opens: list[float] | None = None,
    lows: list[float] | None = None,
    highs: list[float] | None = None,
    amounts: list[float] | None = None,
    start: date = date(2026, 1, 1),
) -> list[dict[str, Any]]:
    closes = closes or [26.5] * count
    opens = opens or [26.3] * count
    lows = lows or [26.0] * count
    highs = highs or [26.8] * count
    amounts = amounts or [500_000_000.0] * count
    return [
        {
            "trade_date": start + timedelta(days=index),
            "open": opens[index],
            "high": highs[index],
            "low": lows[index],
            "close": closes[index],
            "volume": 1_000_000.0,
            "amount": amounts[index],
        }
        for index in range(count)
    ]


def test_less_than_thirty_bars_is_unknown():
    result = analyze_spring_trigger(make_bars(29), trigger_config())

    assert result["state"] == "unknown"
    assert result["reasons"] == ["insufficient_history"]


def test_duplicate_date_is_unknown():
    bars = make_bars()
    bars[-1]["trade_date"] = bars[-2]["trade_date"]

    result = analyze_spring_trigger(bars, trigger_config())

    assert result["reasons"] == ["duplicate_trade_date"]


def test_missing_turnover_is_unknown():
    bars = make_bars()
    bars[-1]["amount"] = None

    result = analyze_spring_trigger(bars, trigger_config())

    assert result["state"] == "unknown"
    assert result["reasons"] == ["turnover_unavailable"]


def test_support_holding_when_price_holds_above_support_without_shrink():
    amounts = [1_200_000_000.0] * 60  # 12 亿，高于缩量上限
    result = analyze_spring_trigger(make_bars(amounts=amounts), trigger_config())

    assert result["state"] == "support_holding"
    assert result["conditions"] == {
        "support_holding": True,
        "volume_shrunk": False,
        "trigger_day": False,
    }
    assert result["support"]["min_close_in_window"] == 26.5
    assert result["support"]["days_in_zone"] == 10


def test_primed_when_support_holds_and_consecutive_shrink_ready():
    amounts = [500_000_000.0] * 60  # 连续 5 亿 < 10 亿
    result = analyze_spring_trigger(make_bars(amounts=amounts), trigger_config())

    assert result["state"] == "primed"
    assert result["conditions"]["volume_shrunk"] is True
    assert result["shrink"]["consecutive_days"] == 30  # 分析窗口取最近 30 根
    assert result["shrink"]["latest_turnover"] == 500_000_000.0


def test_shrink_min_days_is_respected():
    closes = [26.5] * 58 + [26.4, 26.5]
    amounts = [1_200_000_000.0] * 58 + [500_000_000.0] * 2  # 仅缩量 2 天
    result = analyze_spring_trigger(
        make_bars(closes=closes, amounts=amounts),
        trigger_config(shrink_min_days=3),
    )

    assert result["state"] == "support_holding"
    assert result["shrink"]["consecutive_days"] == 2


def test_trigger_fired_when_all_three_conditions_meet():
    closes = [26.5] * 58 + [26.4, 27.06]  # 最后一根 +2.5%
    opens = [26.3] * 58 + [26.3, 26.5]
    lows = [26.0] * 58 + [26.1, 26.6]
    highs = [26.8] * 58 + [26.6, 27.2]
    amounts = [500_000_000.0] * 59 + [1_600_000_000.0]  # 扳机日 16 亿

    result = analyze_spring_trigger(
        make_bars(closes=closes, opens=opens, lows=lows, highs=highs, amounts=amounts),
        trigger_config(),
    )

    assert result["state"] == "trigger_fired"
    assert result["conditions"] == {
        "support_holding": True,
        "volume_shrunk": True,
        "trigger_day": True,
    }
    trigger = result["trigger"]
    assert trigger["gain_pct"] == pytest.approx(0.025)
    assert trigger["turnover"] == 1_600_000_000.0
    assert trigger["volume_ratio"] == pytest.approx(3.2)
    assert result["entry_plan"]["entry_reference"] == 27.06
    assert result["entry_plan"]["stop_price"] == 26.6


def test_trigger_fired_kept_active_on_following_day():
    closes = [26.5] * 57 + [26.4, 27.06, 27.0]  # 扳机在倒数第二根
    opens = [26.3] * 57 + [26.3, 26.5, 27.1]
    lows = [26.0] * 57 + [26.1, 26.6, 26.8]
    highs = [26.8] * 57 + [26.6, 27.2, 27.2]
    amounts = [500_000_000.0] * 58 + [1_600_000_000.0, 800_000_000.0]

    result = analyze_spring_trigger(
        make_bars(closes=closes, opens=opens, lows=lows, highs=highs, amounts=amounts),
        trigger_config(),
    )

    assert result["state"] == "trigger_fired"
    assert result["trigger"]["trade_date"] == (date(2026, 1, 1) + timedelta(days=58)).isoformat()


def test_trigger_decays_after_active_days():
    closes = [26.5] * 55 + [26.4, 27.06] + [27.0] * 3  # 扳机在 index 56
    opens = [26.3] * 55 + [26.3, 26.5] + [27.1] * 3
    lows = [26.0] * 55 + [26.1, 26.6] + [26.8] * 3
    highs = [26.8] * 55 + [26.6, 27.2] + [27.2] * 3
    amounts = [500_000_000.0] * 56 + [1_600_000_000.0] + [800_000_000.0] * 3

    result = analyze_spring_trigger(
        make_bars(closes=closes, opens=opens, lows=lows, highs=highs, amounts=amounts),
        trigger_config(),
    )

    assert result["state"] == "primed"
    assert result["trigger"] is None


def test_support_broken_overrides_recent_trigger():
    closes = [26.5] * 58 + [26.4, 25.5]  # 最后一根跌破 26
    opens = [26.3] * 58 + [26.3, 26.5]
    lows = [26.0] * 58 + [26.1, 25.3]
    highs = [26.8] * 58 + [26.6, 25.9]
    amounts = [500_000_000.0] * 59 + [1_600_000_000.0]

    result = analyze_spring_trigger(
        make_bars(closes=closes, opens=opens, lows=lows, highs=highs, amounts=amounts),
        trigger_config(),
    )

    assert result["state"] == "support_broken"
    assert result["reasons"] == ["support_broken"]


def test_exact_two_percent_gain_counts_as_trigger():
    closes = [26.5] * 58 + [26.4, 26.928]
    opens = [26.3] * 58 + [26.3, 26.6]
    lows = [26.0] * 58 + [26.1, 26.6]
    highs = [26.8] * 58 + [26.6, 27.1]
    amounts = [500_000_000.0] * 59 + [1_600_000_000.0]

    result = analyze_spring_trigger(
        make_bars(closes=closes, opens=opens, lows=lows, highs=highs, amounts=amounts),
        trigger_config(),
    )

    assert result["state"] == "trigger_fired"


def test_turnover_just_below_threshold_is_not_trigger():
    closes = [26.5] * 58 + [26.4, 26.928]  # 涨幅恰好 +2%
    opens = [26.3] * 58 + [26.3, 26.6]
    lows = [26.0] * 58 + [26.1, 26.6]
    highs = [26.8] * 58 + [26.6, 27.1]
    amounts = [500_000_000.0] * 59 + [1_499_999_999.0]

    result = analyze_spring_trigger(
        make_bars(closes=closes, opens=opens, lows=lows, highs=highs, amounts=amounts),
        trigger_config(),
    )

    assert result["state"] != "trigger_fired"


def test_insufficient_volume_expansion_is_not_trigger():
    closes = [26.5] * 58 + [26.4, 27.06]
    opens = [26.3] * 58 + [26.3, 26.5]
    lows = [26.0] * 58 + [26.1, 26.6]
    highs = [26.8] * 58 + [26.6, 27.2]
    amounts = [1_200_000_000.0] * 3 + [500_000_000.0, 500_000_000.0] + [1_400_000_000.0] * 55

    result = analyze_spring_trigger(
        make_bars(closes=closes, opens=opens, lows=lows, highs=highs, amounts=amounts),
        trigger_config(),
    )

    assert result["state"] != "trigger_fired"


def test_days_in_zone_counts_only_close_within_support_range():
    closes = [26.5] * 52 + [27.6, 26.4, 26.6, 27.8, 26.7, 26.4, 26.9, 26.3]
    amounts = [1_200_000_000.0] * 60  # 高于缩量上限，避免触发压紧
    result = analyze_spring_trigger(make_bars(closes=closes, amounts=amounts), trigger_config())

    assert result["state"] == "support_holding"
    assert result["support"]["days_in_zone"] == 8
    assert result["support"]["min_close_in_window"] == 26.3
