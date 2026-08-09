from __future__ import annotations

from collections.abc import Callable
from datetime import date
import math

import pandas as pd

from lurker.ingest.prices import fetch_watchlist_history


PersonalHistoryFetcher = Callable[..., pd.DataFrame]
_RAW_PRICE_COLUMNS = ("open", "high", "low", "close")


def _finite_positive(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def normalize_personal_prices(
    raw: pd.DataFrame,
    *,
    market: str,
    report_date: date,
) -> pd.DataFrame:
    if market not in {"cn", "hk"}:
        raise ValueError(f"unsupported personal price market: {market}")
    required = {"trade_date", "open", "high", "low", "close", "adj_close", "volume"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"missing personal price column: {missing[0]}")

    normalized = raw.copy()
    parsed_dates = pd.to_datetime(
        normalized["trade_date"],
        errors="coerce",
        format="mixed",
    )
    if parsed_dates.isna().any():
        raise ValueError("invalid_trade_date")
    normalized["trade_date"] = parsed_dates.dt.date
    normalized = normalized.loc[normalized["trade_date"] <= report_date].copy()
    normalized = normalized.sort_values("trade_date").reset_index(drop=True)
    if normalized["trade_date"].duplicated().any():
        raise ValueError("duplicate_trade_date")
    normalized = normalized.tail(220).reset_index(drop=True)

    for column in (*_RAW_PRICE_COLUMNS, "adj_close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if normalized.empty:
        return normalized.assign(raw_close=pd.Series(dtype=float))
    if normalized[list(_RAW_PRICE_COLUMNS)].map(_finite_positive).eq(False).any().any():
        raise ValueError("invalid_price_data")
    volumes = normalized["volume"]
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in volumes):
        raise ValueError("invalid_volume_data")

    raw_open = normalized["open"].copy()
    raw_high = normalized["high"].copy()
    raw_low = normalized["low"].copy()
    raw_close = normalized["close"].copy()
    if market == "hk":
        if any(not _finite_positive(value) for value in normalized["adj_close"]):
            raise ValueError("invalid_adjusted_price_data")
        factor = normalized["adj_close"] / raw_close
        if any(not _finite_positive(value) for value in factor):
            raise ValueError("invalid_adjusted_price_data")
        normalized["open"] = raw_open * factor
        normalized["high"] = raw_high * factor
        normalized["low"] = raw_low * factor
        normalized["close"] = normalized["adj_close"]
    else:
        normalized["adj_close"] = normalized["close"]

    valid_ranges = (
        (normalized["low"] <= normalized["open"])
        & (normalized["open"] <= normalized["high"])
        & (normalized["low"] <= normalized["close"])
        & (normalized["close"] <= normalized["high"])
    )
    if not valid_ranges.all():
        raise ValueError("invalid_adjusted_price_data")
    normalized["raw_close"] = raw_close
    return normalized[
        [
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "raw_close",
            "volume",
        ]
    ]


def load_personal_prices(
    *,
    symbol: str,
    market: str,
    report_date: date,
    period: str = "2y",
    fetcher: PersonalHistoryFetcher = fetch_watchlist_history,
) -> pd.DataFrame:
    if period != "2y":
        raise ValueError("personal price period must equal 2y")
    raw = fetcher(
        symbol,
        market,
        period,
        is_benchmark=False,
        end_date=report_date,
    )
    return normalize_personal_prices(
        raw,
        market=market,
        report_date=report_date,
    )
