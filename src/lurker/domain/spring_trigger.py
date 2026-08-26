from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Any

from lurker.config import SpringTriggerConfig


RULE_VERSION = "spring-trigger-v1"
MINIMUM_BARS = 30
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
    amount: float


def unknown_spring_trigger_result(
    reason: str,
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    return {
        "rule_version": RULE_VERSION,
        "state": "unknown",
        "as_of": as_of,
        "conditions": {
            "support_holding": False,
            "volume_shrunk": False,
            "trigger_day": False,
        },
        "support": None,
        "shrink": None,
        "trigger": None,
        "entry_plan": None,
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


def _analyze_trigger(
    bars: Sequence[_Bar],
    config: SpringTriggerConfig,
    *,
    as_of: str,
) -> dict[str, Any]:
    closes = [bar.close for bar in bars]
    opens = [bar.open for bar in bars]
    lows = [bar.low for bar in bars]
    amounts = [bar.amount for bar in bars]
    count = len(bars)
    window = config.support_window_days
    shrink_days = config.shrink_min_days

    support_ok = all(
        close >= config.support_low - BOUNDARY_EPSILON for close in closes[-window:]
    )
    min_close_in_window = min(closes[-window:])
    days_in_zone = sum(
        config.support_low - BOUNDARY_EPSILON <= close <= config.support_high + BOUNDARY_EPSILON
        for close in closes[-window:]
    )

    def shrink_streak_ending(end_index: int) -> int:
        streak = 0
        for index in range(end_index, -1, -1):
            if amounts[index] < config.shrink_max_turnover - BOUNDARY_EPSILON:
                streak += 1
            else:
                break
        return streak

    def is_trigger_day(index: int) -> bool:
        if index < 6:
            return False
        gain = closes[index] / closes[index - 1] - 1.0
        base = sum(amounts[index - 5 : index]) / 5
        volume_ratio = amounts[index] / base if base > 0 else 0.0
        return (
            closes[index] > opens[index]
            and gain >= config.trigger_min_gain_pct - BOUNDARY_EPSILON
            and amounts[index] >= config.trigger_min_turnover - BOUNDARY_EPSILON
            and volume_ratio >= config.trigger_min_volume_ratio - BOUNDARY_EPSILON
        )

    support = {
        "low": config.support_low,
        "high": config.support_high,
        "window_days": window,
        "min_close_in_window": min_close_in_window,
        "days_in_zone": days_in_zone,
    }
    shrink = {
        "consecutive_days": shrink_streak_ending(count - 1),
        "latest_turnover": amounts[-1],
        "max_turnover": config.shrink_max_turnover,
    }
    conditions = {
        "support_holding": support_ok,
        "volume_shrunk": False,
        "trigger_day": False,
    }
    trigger: dict[str, Any] | None = None
    entry_plan: dict[str, Any] | None = None
    reasons: list[str] = []

    if not support_ok:
        state = "support_broken"
        conditions["support_holding"] = False
        reasons.append("support_broken")
    else:
        conditions["support_holding"] = True
        fired: int | None = None
        for offset in range(config.trigger_active_days):
            candidate = count - 1 - offset
            if candidate - window < 0 or candidate - shrink_days < 0:
                break
            support_before = all(
                closes[index] >= config.support_low - BOUNDARY_EPSILON
                for index in range(candidate - window, candidate)
            )
            shrink_before = all(
                amounts[index] < config.shrink_max_turnover - BOUNDARY_EPSILON
                for index in range(candidate - shrink_days, candidate)
            )
            if support_before and shrink_before and is_trigger_day(candidate):
                fired = candidate
                break
        if fired is not None:
            state = "trigger_fired"
            conditions["volume_shrunk"] = True
            conditions["trigger_day"] = True
            shrink["consecutive_days"] = shrink_streak_ending(fired - 1)
            base = sum(amounts[fired - 5 : fired]) / 5
            trigger = {
                "trade_date": bars[fired].trade_date.isoformat(),
                "open": opens[fired],
                "high": bars[fired].high,
                "low": lows[fired],
                "close": closes[fired],
                "gain_pct": closes[fired] / closes[fired - 1] - 1.0,
                "turnover": amounts[fired],
                "volume_ratio": amounts[fired] / base if base > 0 else 0.0,
            }
            entry_plan = {
                "entry_reference": closes[fired],
                "stop_price": lows[fired],
                "stop_rule": "two_night_stop",
            }
        elif shrink_streak_ending(count - 1) >= shrink_days:
            state = "primed"
            conditions["volume_shrunk"] = True
        else:
            state = "support_holding"

    return {
        "rule_version": RULE_VERSION,
        "state": state,
        "as_of": as_of,
        "conditions": conditions,
        "support": support,
        "shrink": shrink,
        "trigger": trigger,
        "entry_plan": entry_plan,
        "reasons": reasons,
    }


def analyze_spring_trigger(
    bars: Sequence[Mapping[str, Any]],
    config: SpringTriggerConfig,
) -> dict[str, Any]:
    dated_rows: list[tuple[date, Mapping[str, Any]]] = []
    for row in bars:
        trade_date = _parse_date(row.get("trade_date"))
        if trade_date is None:
            return unknown_spring_trigger_result("invalid_trade_date")
        dated_rows.append((trade_date, row))

    dated_rows.sort(key=lambda item: item[0])
    if len(dated_rows) < MINIMUM_BARS:
        as_of = dated_rows[-1][0].isoformat() if dated_rows else None
        return unknown_spring_trigger_result("insufficient_history", as_of=as_of)

    latest = dated_rows[-MINIMUM_BARS:]
    dates = [item[0] for item in latest]
    as_of = dates[-1].isoformat()
    if len(set(dates)) != len(dates):
        return unknown_spring_trigger_result("duplicate_trade_date", as_of=as_of)

    normalized: list[_Bar] = []
    for trade_date, row in latest:
        prices = [_as_positive_float(row.get(field)) for field in PRICE_FIELDS]
        if any(value is None for value in prices):
            return unknown_spring_trigger_result("invalid_price_data", as_of=as_of)
        volume = _as_positive_float(row.get("volume"))
        if volume is None:
            return unknown_spring_trigger_result("invalid_volume_data", as_of=as_of)
        amount = _as_positive_float(row.get("amount"))
        if amount is None:
            return unknown_spring_trigger_result("turnover_unavailable", as_of=as_of)
        normalized.append(
            _Bar(
                trade_date=trade_date,
                open=prices[0],  # type: ignore[arg-type]
                high=prices[1],  # type: ignore[arg-type]
                low=prices[2],  # type: ignore[arg-type]
                close=prices[3],  # type: ignore[arg-type]
                volume=volume,
                amount=amount,
            )
        )

    return _analyze_trigger(
        normalized,
        config,
        as_of=as_of,
    )
