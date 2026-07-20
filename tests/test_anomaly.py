from datetime import date, timedelta

import pandas as pd
import pytest

from lurker.signals.anomaly import (
    AlertType,
    DetectionStatus,
    detect_abnormal_volume,
    detect_chronic_underperformance,
    detect_peak_drawdown,
)


def frame(closes, volumes=None, start=date(2025, 1, 1)):
    volumes = volumes or [100.0] * len(closes)
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=i) for i in range(len(closes))],
            "adj_close": closes,
            "volume": volumes,
        }
    )


def test_abnormal_volume_excludes_current_day_from_average():
    prices = frame(
        [100.0] * 20 + [106.0],
        [100.0] * 20 + [300.0],
    )

    result = detect_abnormal_volume(
        prices,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        volume_ratio_threshold=3.0,
        price_change_threshold=0.05,
    )

    assert result.status is DetectionStatus.ALERT
    assert result.alert.alert_type is AlertType.ABNORMAL_VOLUME
    assert result.alert.metrics["volume_ratio"] == 3.0


def test_abnormal_volume_triggers_at_exact_price_and_volume_thresholds():
    prices = frame([100.0] * 20 + [105.0], [100.0] * 20 + [300.0])

    result = detect_abnormal_volume(
        prices,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        volume_ratio_threshold=3.0,
        price_change_threshold=0.05,
    )

    assert result.status is DetectionStatus.ALERT


def test_abnormal_volume_does_not_trigger_below_price_threshold():
    prices = frame([100.0] * 20 + [104.9], [100.0] * 20 + [300.0])

    result = detect_abnormal_volume(
        prices,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        volume_ratio_threshold=3.0,
        price_change_threshold=0.05,
    )

    assert result.status is DetectionStatus.NORMAL


def test_abnormal_volume_reports_insufficient_data_for_zero_average():
    prices = frame([100.0] * 20 + [106.0], [0.0] * 20 + [300.0])

    result = detect_abnormal_volume(
        prices,
        symbol="NVDA",
        market="us",
        name="NVIDIA",
        volume_ratio_threshold=3.0,
        price_change_threshold=0.10,
    )

    assert result.status is DetectionStatus.INSUFFICIENT_DATA


def test_abnormal_volume_reports_insufficient_data_when_volume_column_is_missing():
    prices = frame([100.0] * 20 + [106.0]).drop(columns=["volume"])

    result = detect_abnormal_volume(
        prices,
        symbol="NVDA",
        market="us",
        name="NVIDIA",
        volume_ratio_threshold=3.0,
        price_change_threshold=0.10,
    )

    assert result.status is DetectionStatus.INSUFFICIENT_DATA
    assert result.reason == "missing columns: volume"


def test_peak_drawdown_uses_adjusted_close_peak():
    prices = frame([100.0] + [90.0] * 248 + [80.0])

    result = detect_peak_drawdown(
        prices,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.20,
    )

    assert result.status is DetectionStatus.ALERT
    assert result.alert.metrics["drawdown"] == pytest.approx(-0.20)
    assert result.alert.severity == pytest.approx(0.20)


def test_peak_drawdown_does_not_trigger_below_threshold():
    prices = frame([100.0] + [90.0] * 248 + [80.1])

    result = detect_peak_drawdown(
        prices,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.20,
    )

    assert result.status is DetectionStatus.NORMAL


def test_underperformance_aligns_stock_and_benchmark_dates():
    stock = frame([100.0] * 60 + [80.0])
    benchmark = frame([100.0] * 60 + [100.0])

    result = detect_chronic_underperformance(
        stock,
        benchmark,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.15,
    )

    assert result.status is DetectionStatus.ALERT
    assert result.alert.metrics["alpha_60d"] == pytest.approx(-0.20)


def test_underperformance_triggers_at_exact_threshold():
    stock = frame([100.0] * 60 + [85.0])
    benchmark = frame([100.0] * 61)

    result = detect_chronic_underperformance(
        stock,
        benchmark,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.15,
    )

    assert result.status is DetectionStatus.ALERT


def test_underperformance_does_not_trigger_below_threshold():
    stock = frame([100.0] * 60 + [85.1])
    benchmark = frame([100.0] * 61)

    result = detect_chronic_underperformance(
        stock,
        benchmark,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.15,
    )

    assert result.status is DetectionStatus.NORMAL


def test_underperformance_uses_latest_common_date_not_stock_only_tail():
    stock = frame([100.0] * 60 + [80.0, 200.0])
    benchmark = frame([100.0] * 61)

    result = detect_chronic_underperformance(
        stock,
        benchmark,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.15,
    )

    assert result.status is DetectionStatus.ALERT
    assert result.alert.observed_on == str(benchmark.iloc[-1]["trade_date"])


def test_underperformance_requires_61_common_dates():
    stock = frame([100.0] * 61)
    benchmark = frame([100.0] * 60, start=date(2025, 1, 2))

    result = detect_chronic_underperformance(
        stock,
        benchmark,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.15,
    )

    assert result.status is DetectionStatus.INSUFFICIENT_DATA
