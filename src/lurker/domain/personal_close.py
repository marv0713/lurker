from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping

import pandas as pd


MovingAverageDirection = Literal["up", "down", "flat"]
TrendLabel = Literal[
    "long_structure_weakened",
    "testing_ma200",
    "long_medium_strong",
    "long_up_medium_pullback",
    "medium_repair",
    "mixed",
    "data_insufficient",
]
BullishQualityLabel = Literal["micro", "standard", "strong"]


@dataclass(frozen=True)
class MovingAverageFact:
    window: int
    value: float
    distance_pct: float
    direction: MovingAverageDirection


@dataclass(frozen=True)
class TrendAnalysis:
    label: TrendLabel
    as_of: date | None
    adjusted_close: float | None
    ma5: MovingAverageFact | None
    ma20: MovingAverageFact | None
    ma200: MovingAverageFact | None


@dataclass(frozen=True)
class FirstBullishQuality:
    entity_ratio: float
    daily_return: float
    label: BullishQualityLabel


def _moving_average(closes: list[float], end: int, window: int) -> float:
    return sum(closes[end - window + 1 : end + 1]) / window


def _direction(current: float, previous: float) -> MovingAverageDirection:
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"


def _moving_average_fact(
    closes: list[float],
    *,
    window: int,
    lookback: int,
) -> MovingAverageFact | None:
    if len(closes) < window + lookback:
        return None
    current = _moving_average(closes, len(closes) - 1, window)
    previous = _moving_average(closes, len(closes) - 1 - lookback, window)
    return MovingAverageFact(
        window=window,
        value=current,
        distance_pct=closes[-1] / current - 1.0,
        direction=_direction(current, previous),
    )


def analyze_personal_trend(prices: pd.DataFrame) -> TrendAnalysis:
    if prices.empty:
        return TrendAnalysis("data_insufficient", None, None, None, None, None)
    closes = [float(value) for value in prices["close"]]
    as_of = pd.Timestamp(prices.iloc[-1]["trade_date"]).date()
    ma5 = _moving_average_fact(closes, window=5, lookback=3)
    ma20 = _moving_average_fact(closes, window=20, lookback=5)
    ma200 = _moving_average_fact(closes, window=200, lookback=20)
    if len(closes) < 220 or ma20 is None or ma200 is None:
        return TrendAnalysis(
            "data_insufficient",
            as_of,
            closes[-1],
            ma5,
            ma20,
            ma200,
        )

    current_close = closes[-1]
    prior_close = closes[-2]
    prior_ma20 = _moving_average(closes, len(closes) - 2, 20)
    prior_ma200 = _moving_average(closes, len(closes) - 2, 200)
    current_below_ma200 = current_close < ma200.value
    prior_below_ma200 = prior_close < prior_ma200

    label: TrendLabel = "mixed"
    if current_below_ma200 and prior_below_ma200:
        label = "long_structure_weakened"
    elif current_below_ma200 and not prior_below_ma200:
        label = "testing_ma200"
    elif (
        current_close > ma20.value
        and current_close > ma200.value
        and ma20.direction == "up"
        and ma200.direction != "down"
    ):
        label = "long_medium_strong"
    elif (
        current_close < ma20.value
        and current_close > ma200.value
        and ma200.direction != "down"
    ):
        label = "long_up_medium_pullback"
    elif (
        current_close > ma20.value
        and prior_close <= prior_ma20
        and ma20.direction != "up"
    ):
        label = "medium_repair"

    return TrendAnalysis(label, as_of, current_close, ma5, ma20, ma200)


def project_first_bullish_quality(
    spring: Mapping[str, Any],
    prices: pd.DataFrame,
) -> FirstBullishQuality | None:
    if spring.get("state") != "first_bullish_confirmed" or len(prices) < 2:
        return None
    as_of = str(spring.get("as_of") or "")
    latest_date = pd.Timestamp(prices.iloc[-1]["trade_date"]).date().isoformat()
    if as_of != latest_date:
        return None
    previous_close = float(prices.iloc[-2]["close"])
    latest_open = float(prices.iloc[-1]["open"])
    latest_close = float(prices.iloc[-1]["close"])
    entity_ratio = (latest_close - latest_open) / previous_close
    daily_return = latest_close / previous_close - 1.0
    label: BullishQualityLabel = "micro"
    if entity_ratio > 0.02:
        label = "strong"
    elif entity_ratio >= 0.005:
        label = "standard"
    return FirstBullishQuality(entity_ratio, daily_return, label)
