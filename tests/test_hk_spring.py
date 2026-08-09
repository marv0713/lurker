from __future__ import annotations

from datetime import date, timedelta

from lurker.domain.spring import analyze_hk_experimental_spring


def _trending_hk_bars(
    *,
    raw_close: float = 20.0,
    compressed: bool = True,
) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    closes = [80.0 + index * 0.5 for index in range(59)]
    closes.extend(109.0 + index * 0.1 for index in range(20))
    rows: list[dict[str, object]] = []
    for index, close in enumerate(closes):
        volume = 1_000_000.0
        if compressed and index >= 75:
            volume = 200_000.0
        rows.append(
            {
                "trade_date": start + timedelta(days=index),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.995,
                "close": close,
                "raw_close": raw_close,
                "volume": volume,
            }
        )
    return rows


def test_hk_zero_volume_outside_required_window_can_be_evaluated():
    bars = _trending_hk_bars()
    bars[20]["volume"] = 0.0

    result = analyze_hk_experimental_spring(bars)

    assert result["state"] == "compressed_watch"
    assert result["experimental"] is True
    assert result["positive_volume_ratio_60d"] == 59 / 60


def test_hk_zero_volume_in_compression_window_is_unknown():
    bars = _trending_hk_bars()
    bars[-2]["volume"] = 0.0

    result = analyze_hk_experimental_spring(bars)

    assert result["state"] == "unknown"
    assert result["reasons"] == ["hk_zero_volume_in_compression_window"]


def test_hk_liquidity_gate_uses_raw_close_turnover():
    bars = _trending_hk_bars(raw_close=250 / 21)

    result = analyze_hk_experimental_spring(bars)

    assert result["avg_turnover_hkd_20d"] == 10_000_000.0
    assert result["reasons"] == []


def test_hk_positive_volume_ratio_at_95_percent_is_eligible():
    bars = _trending_hk_bars()
    for index in (20, 21, 22):
        bars[index]["volume"] = 0.0

    result = analyze_hk_experimental_spring(bars)

    assert result["state"] == "compressed_watch"
    assert result["positive_volume_ratio_60d"] == 0.95


def test_hk_below_liquidity_threshold_is_unknown_not_weak():
    bars = _trending_hk_bars(raw_close=1.0)

    result = analyze_hk_experimental_spring(bars)

    assert result["state"] == "unknown"
    assert result["reasons"] == ["hk_insufficient_turnover"]
    assert result["experimental"] is True


def test_hk_negative_volume_is_invalid():
    bars = _trending_hk_bars()
    bars[20]["volume"] = -1.0

    result = analyze_hk_experimental_spring(bars)

    assert result["state"] == "unknown"
    assert result["reasons"] == ["invalid_volume_data"]
