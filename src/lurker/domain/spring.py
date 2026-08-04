from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Any


RULE_VERSION = "ma20-v1"
MINIMUM_BARS = 79
PRICE_FIELDS = ("open", "high", "low", "close")


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

    closes = [bar.close for bar in normalized]
    ma20_values = [
        _moving_average(closes, end=index, window=20)
        for index in range(19, MINIMUM_BARS)
    ]
    touch_flags = [
        normalized[index].low <= ma20 * 1.02
        and normalized[index].close >= ma20 * 0.98
        for index, ma20 in zip(range(19, MINIMUM_BARS), ma20_values, strict=True)
    ]
    segments = _merge_touch_segments(touch_flags)
    ma20_distance = normalized[-1].close / ma20_values[-1] - 1.0
    prior_distance = normalized[-2].close / ma20_values[-2] - 1.0

    return {
        "rule_version": RULE_VERSION,
        "state": "none",
        "as_of": as_of,
        "ma20_distance_pct": ma20_distance,
        "volume_compression_ratio": None,
        "support_touch_count_60d": len(segments),
        "min_ma20_distance_2d_pct": min(prior_distance, ma20_distance),
        "reasons": [],
    }
