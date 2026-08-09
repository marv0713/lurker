from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Any


RULE_VERSION = "ma20-v1"
HK_RULE_VERSION = "hk-ma20-experimental-v1"
MINIMUM_BARS = 79
PRICE_FIELDS = ("open", "high", "low", "close")
BOUNDARY_EPSILON = 1e-12


@dataclass(frozen=True)
class _Bar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def unknown_spring_result(
    reason: str,
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    return {
        "rule_version": RULE_VERSION,
        "state": "unknown",
        "as_of": as_of,
        "ma20_distance_pct": None,
        "volume_compression_ratio": None,
        "support_touch_count_60d": 0,
        "min_ma20_distance_2d_pct": None,
        "reasons": [reason],
    }


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                return None
    return None


def _as_positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _as_non_negative_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _merge_touch_segments(flags: Sequence[bool]) -> list[tuple[int, int]]:
    raw_segments: list[tuple[int, int]] = []
    start: int | None = None
    for index, touched in enumerate(flags):
        if touched and start is None:
            start = index
        if not touched and start is not None:
            raw_segments.append((start, index - 1))
            start = None
    if start is not None:
        raw_segments.append((start, len(flags) - 1))

    merged: list[tuple[int, int]] = []
    for segment_start, segment_end in raw_segments:
        if merged and segment_end - merged[-1][1] < 5:
            merged[-1] = (merged[-1][0], segment_end)
        else:
            merged.append((segment_start, segment_end))
    return merged


def _moving_average(values: Sequence[float], end: int, window: int) -> float:
    start = end - window + 1
    return sum(values[start : end + 1]) / window


def _is_bullish(bars: Sequence[_Bar], index: int) -> bool:
    return (
        index > 0
        and bars[index].close > bars[index].open
        and bars[index].close > bars[index - 1].close
    )


def _compression_ratio(bars: Sequence[_Bar], compression_end: int) -> float:
    compression = [bar.volume for bar in bars[compression_end - 2 : compression_end + 1]]
    active = [bar.volume for bar in bars[compression_end - 42 : compression_end - 2]]
    rolling_five = [
        sum(active[index : index + 5]) / 5
        for index in range(len(active) - 4)
    ]
    baseline = max(rolling_five)
    return (sum(compression) / 3) / baseline


def _analyze_shape(
    bars: Sequence[_Bar],
    *,
    rule_version: str,
    as_of: str,
    compression_ratio_for: Callable[[int], float],
) -> dict[str, Any]:
    closes = [bar.close for bar in bars]
    ma20_values = [
        _moving_average(closes, end=index, window=20)
        for index in range(19, MINIMUM_BARS)
    ]
    touch_flags = [
        bars[index].low / ma20 - 1.0 <= 0.02 + BOUNDARY_EPSILON
        and bars[index].close / ma20 - 1.0 >= -0.02 - BOUNDARY_EPSILON
        for index, ma20 in zip(range(19, MINIMUM_BARS), ma20_values, strict=True)
    ]
    segments = _merge_touch_segments(touch_flags)
    ma20_distance = bars[-1].close / ma20_values[-1] - 1.0
    prior_distance = bars[-2].close / ma20_values[-2] - 1.0

    current_segment = (
        segments[-1]
        if touch_flags[-1] and segments and segments[-1][1] == len(touch_flags) - 1
        else None
    )
    prior_bullish_in_segment = False
    if current_segment is not None:
        prior_bullish_in_segment = any(
            _is_bullish(bars, local_index + 19)
            for local_index in range(current_segment[0], len(touch_flags) - 1)
        )
    current_bullish = (
        current_segment is not None
        and not prior_bullish_in_segment
        and _is_bullish(bars, len(bars) - 1)
    )

    ma20_up = ma20_values[-1] > ma20_values[-6]
    recent_touch = any(touch_flags[-10:])
    broken = recent_touch and all(
        distance < -0.02 - BOUNDARY_EPSILON
        for distance in (prior_distance, ma20_distance)
    )
    third_or_later_touch = current_segment is not None and len(segments) >= 3

    compression_ratio: float | None = None
    if current_segment is not None and not prior_bullish_in_segment:
        compression_end = len(bars) - 2 if current_bullish else len(bars) - 1
        compression_ratio = compression_ratio_for(compression_end)

    volume_not_compressed = (
        ma20_up
        and current_segment is not None
        and not prior_bullish_in_segment
        and compression_ratio is not None
        and (ma20_distance <= 0.02 + BOUNDARY_EPSILON or current_bullish)
        and compression_ratio > 0.30 + BOUNDARY_EPSILON
    )

    reasons: list[str] = []
    if broken:
        reasons.append("ma20_broken")
    if third_or_later_touch:
        reasons.append("third_support_test")
    if volume_not_compressed:
        reasons.append("volume_not_compressed")

    state = "none"
    if reasons:
        state = "weak_excluded"
    elif (
        ma20_up
        and current_bullish
        and compression_ratio is not None
        and compression_ratio <= 0.30 + BOUNDARY_EPSILON
    ):
        state = "first_bullish_confirmed"
    elif (
        ma20_up
        and current_segment is not None
        and not prior_bullish_in_segment
        and not current_bullish
        and -0.02 - BOUNDARY_EPSILON
        <= ma20_distance
        <= 0.02 + BOUNDARY_EPSILON
        and compression_ratio is not None
        and compression_ratio <= 0.30 + BOUNDARY_EPSILON
    ):
        state = "compressed_watch"

    return {
        "rule_version": rule_version,
        "state": state,
        "as_of": as_of,
        "ma20_distance_pct": ma20_distance,
        "volume_compression_ratio": compression_ratio,
        "support_touch_count_60d": len(segments),
        "min_ma20_distance_2d_pct": min(prior_distance, ma20_distance),
        "reasons": reasons,
    }


def analyze_spring_bars(
    bars: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    dated_rows: list[tuple[date, Mapping[str, Any]]] = []
    for row in bars:
        trade_date = _parse_date(row.get("trade_date"))
        if trade_date is None:
            return unknown_spring_result("invalid_trade_date")
        dated_rows.append((trade_date, row))

    dated_rows.sort(key=lambda item: item[0])
    if len(dated_rows) < MINIMUM_BARS:
        as_of = dated_rows[-1][0].isoformat() if dated_rows else None
        return unknown_spring_result("insufficient_history", as_of=as_of)

    latest = dated_rows[-MINIMUM_BARS:]
    dates = [item[0] for item in latest]
    as_of = dates[-1].isoformat()
    if len(set(dates)) != len(dates):
        return unknown_spring_result("duplicate_trade_date", as_of=as_of)

    normalized: list[_Bar] = []
    for trade_date, row in latest:
        prices = [_as_positive_float(row.get(field)) for field in PRICE_FIELDS]
        if any(value is None for value in prices):
            return unknown_spring_result("invalid_price_data", as_of=as_of)
        volume = _as_positive_float(row.get("volume"))
        if volume is None:
            return unknown_spring_result("invalid_volume_data", as_of=as_of)
        normalized.append(
            _Bar(
                trade_date=trade_date,
                open=prices[0],  # type: ignore[arg-type]
                high=prices[1],  # type: ignore[arg-type]
                low=prices[2],  # type: ignore[arg-type]
                close=prices[3],  # type: ignore[arg-type]
                volume=volume,
            )
        )

    return _analyze_shape(
        normalized,
        rule_version=RULE_VERSION,
        as_of=as_of,
        compression_ratio_for=lambda end: _compression_ratio(normalized, end),
    )


def _unknown_hk_spring_result(
    reason: str,
    *,
    as_of: str | None = None,
    avg_turnover_hkd_20d: float | None = None,
    positive_volume_ratio_60d: float | None = None,
) -> dict[str, Any]:
    return {
        "rule_version": HK_RULE_VERSION,
        "state": "unknown",
        "as_of": as_of,
        "ma20_distance_pct": None,
        "volume_compression_ratio": None,
        "support_touch_count_60d": 0,
        "min_ma20_distance_2d_pct": None,
        "reasons": [reason],
        "experimental": True,
        "avg_turnover_hkd_20d": avg_turnover_hkd_20d,
        "positive_volume_ratio_60d": positive_volume_ratio_60d,
    }


class _HkCompressionVolumeError(ValueError):
    pass


def analyze_hk_experimental_spring(
    bars: Sequence[Mapping[str, Any]],
    *,
    min_avg_turnover_hkd_20d: float = 10_000_000.0,
    min_positive_volume_ratio_60d: float = 0.95,
) -> dict[str, Any]:
    dated_rows: list[tuple[date, Mapping[str, Any]]] = []
    for row in bars:
        trade_date = _parse_date(row.get("trade_date"))
        if trade_date is None:
            return _unknown_hk_spring_result("invalid_trade_date")
        dated_rows.append((trade_date, row))

    dated_rows.sort(key=lambda item: item[0])
    if len(dated_rows) < MINIMUM_BARS:
        as_of = dated_rows[-1][0].isoformat() if dated_rows else None
        return _unknown_hk_spring_result("insufficient_history", as_of=as_of)

    latest = dated_rows[-MINIMUM_BARS:]
    dates = [item[0] for item in latest]
    as_of = dates[-1].isoformat()
    if len(set(dates)) != len(dates):
        return _unknown_hk_spring_result("duplicate_trade_date", as_of=as_of)

    normalized: list[_Bar] = []
    raw_closes: list[float] = []
    for trade_date, row in latest:
        prices = [_as_positive_float(row.get(field)) for field in PRICE_FIELDS]
        if any(value is None for value in prices):
            return _unknown_hk_spring_result("invalid_price_data", as_of=as_of)
        open_price, high, low, close = prices
        if not (low <= open_price <= high and low <= close <= high):
            return _unknown_hk_spring_result("invalid_price_data", as_of=as_of)
        raw_close = _as_positive_float(row.get("raw_close"))
        if raw_close is None:
            return _unknown_hk_spring_result("invalid_price_data", as_of=as_of)
        volume = _as_non_negative_float(row.get("volume"))
        if volume is None:
            return _unknown_hk_spring_result("invalid_volume_data", as_of=as_of)
        normalized.append(
            _Bar(
                trade_date=trade_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
        raw_closes.append(raw_close)

    avg_turnover = sum(
        raw_close * bar.volume
        for raw_close, bar in zip(raw_closes[-20:], normalized[-20:], strict=True)
    ) / 20
    positive_ratio = sum(bar.volume > 0 for bar in normalized[-60:]) / 60
    if avg_turnover + BOUNDARY_EPSILON < min_avg_turnover_hkd_20d:
        return _unknown_hk_spring_result(
            "hk_insufficient_turnover",
            as_of=as_of,
            avg_turnover_hkd_20d=avg_turnover,
            positive_volume_ratio_60d=positive_ratio,
        )
    if positive_ratio + BOUNDARY_EPSILON < min_positive_volume_ratio_60d:
        return _unknown_hk_spring_result(
            "hk_insufficient_positive_volume_days",
            as_of=as_of,
            avg_turnover_hkd_20d=avg_turnover,
            positive_volume_ratio_60d=positive_ratio,
        )

    def compression_ratio_for(compression_end: int) -> float:
        required = normalized[compression_end - 42 : compression_end + 1]
        if any(bar.volume <= 0 for bar in required):
            raise _HkCompressionVolumeError
        return _compression_ratio(normalized, compression_end)

    try:
        result = _analyze_shape(
            normalized,
            rule_version=HK_RULE_VERSION,
            as_of=as_of,
            compression_ratio_for=compression_ratio_for,
        )
    except _HkCompressionVolumeError:
        return _unknown_hk_spring_result(
            "hk_zero_volume_in_compression_window",
            as_of=as_of,
            avg_turnover_hkd_20d=avg_turnover,
            positive_volume_ratio_60d=positive_ratio,
        )
    return {
        **result,
        "experimental": True,
        "avg_turnover_hkd_20d": avg_turnover,
        "positive_volume_ratio_60d": positive_ratio,
    }
