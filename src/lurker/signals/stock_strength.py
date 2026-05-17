from collections.abc import Iterable

import pandas as pd

from lurker.domain.signals import score_stock_strength


def calculate_returns(prices: pd.Series, windows: Iterable[int]) -> dict[str, float]:
    if prices.empty:
        return {}

    latest = float(prices.iloc[-1])
    returns: dict[str, float] = {}
    for window in windows:
        if len(prices) <= window:
            continue
        base = float(prices.iloc[-1 - window])
        returns[f"return_{window}d"] = latest / base - 1
    return returns


__all__ = ["calculate_returns", "score_stock_strength"]
