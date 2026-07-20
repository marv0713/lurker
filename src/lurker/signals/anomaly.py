from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class AlertType(str, Enum):
    ABNORMAL_VOLUME = "abnormal_volume"
    PEAK_DRAWDOWN = "peak_drawdown"
    CHRONIC_UNDERPERFORMANCE = "chronic_underperformance"


class DetectionStatus(str, Enum):
    ALERT = "alert"
    NORMAL = "normal"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class AnomalyAlert:
    symbol: str
    market: str
    name: str
    alert_type: AlertType
    observed_on: str
    severity: float
    metrics: dict[str, float]


@dataclass(frozen=True)
class DetectionOutcome:
    alert_type: AlertType
    status: DetectionStatus
    alert: AnomalyAlert | None = None
    reason: str | None = None


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.dropna(subset=["trade_date", "adj_close"]).copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    return result.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def _missing_columns(frame: pd.DataFrame, required: set[str]) -> str | None:
    missing = sorted(required - set(frame.columns))
    return f"missing columns: {', '.join(missing)}" if missing else None


def _outcome(
    alert_type: AlertType,
    *,
    status: DetectionStatus,
    alert: AnomalyAlert | None = None,
    reason: str | None = None,
) -> DetectionOutcome:
    return DetectionOutcome(alert_type=alert_type, status=status, alert=alert, reason=reason)


def detect_abnormal_volume(
    prices: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    name: str,
    volume_ratio_threshold: float,
    price_change_threshold: float,
) -> DetectionOutcome:
    kind = AlertType.ABNORMAL_VOLUME
    missing = _missing_columns(prices, {"trade_date", "adj_close", "volume"})
    if missing:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason=missing)
    rows = _ordered(prices).dropna(subset=["volume"])
    if len(rows) < 21:
        return _outcome(
            kind,
            status=DetectionStatus.INSUFFICIENT_DATA,
            reason="need 21 price rows",
        )
    previous = rows.iloc[-21:-1]
    average_volume = float(previous["volume"].mean())
    if average_volume <= 0:
        return _outcome(
            kind,
            status=DetectionStatus.INSUFFICIENT_DATA,
            reason="20-day average volume is zero",
        )
    current = rows.iloc[-1]
    prior = rows.iloc[-2]
    prior_close = float(prior["adj_close"])
    if prior_close <= 0:
        return _outcome(
            kind,
            status=DetectionStatus.INSUFFICIENT_DATA,
            reason="prior close is not positive",
        )
    volume_ratio = float(current["volume"]) / average_volume
    price_change = float(current["adj_close"]) / prior_close - 1
    if volume_ratio < volume_ratio_threshold or abs(price_change) < price_change_threshold:
        return _outcome(kind, status=DetectionStatus.NORMAL)
    alert = AnomalyAlert(
        symbol=symbol,
        market=market,
        name=name,
        alert_type=kind,
        observed_on=str(current["trade_date"].date()),
        severity=abs(price_change),
        metrics={"volume_ratio": volume_ratio, "price_change": price_change},
    )
    return _outcome(kind, status=DetectionStatus.ALERT, alert=alert)


def detect_peak_drawdown(
    prices: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    name: str,
    threshold: float,
) -> DetectionOutcome:
    kind = AlertType.PEAK_DRAWDOWN
    missing = _missing_columns(prices, {"trade_date", "adj_close"})
    if missing:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason=missing)
    rows = _ordered(prices)
    if len(rows) < 250:
        return _outcome(
            kind,
            status=DetectionStatus.INSUFFICIENT_DATA,
            reason="need 250 price rows",
        )
    window = rows.iloc[-250:]
    peak = float(window["adj_close"].max())
    current = float(window.iloc[-1]["adj_close"])
    if peak <= 0:
        return _outcome(
            kind,
            status=DetectionStatus.INSUFFICIENT_DATA,
            reason="250-day peak is not positive",
        )
    drawdown = current / peak - 1
    if abs(drawdown) + 1e-12 < threshold:
        return _outcome(kind, status=DetectionStatus.NORMAL)
    alert = AnomalyAlert(
        symbol=symbol,
        market=market,
        name=name,
        alert_type=kind,
        observed_on=str(window.iloc[-1]["trade_date"].date()),
        severity=abs(drawdown),
        metrics={"peak_250": peak, "drawdown": drawdown},
    )
    return _outcome(kind, status=DetectionStatus.ALERT, alert=alert)


def detect_chronic_underperformance(
    stock_prices: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    name: str,
    threshold: float,
) -> DetectionOutcome:
    kind = AlertType.CHRONIC_UNDERPERFORMANCE
    stock_missing = _missing_columns(stock_prices, {"trade_date", "adj_close"})
    benchmark_missing = _missing_columns(benchmark_prices, {"trade_date", "adj_close"})
    if stock_missing or benchmark_missing:
        return _outcome(
            kind,
            status=DetectionStatus.INSUFFICIENT_DATA,
            reason=stock_missing or benchmark_missing,
        )
    stock = _ordered(stock_prices)[["trade_date", "adj_close"]].rename(
        columns={"adj_close": "stock"}
    )
    benchmark = _ordered(benchmark_prices)[["trade_date", "adj_close"]].rename(
        columns={"adj_close": "benchmark"}
    )
    common = stock.merge(benchmark, on="trade_date", how="inner").sort_values("trade_date")
    if len(common) < 61:
        return _outcome(
            kind,
            status=DetectionStatus.INSUFFICIENT_DATA,
            reason="need 61 common price rows",
        )
    window = common.iloc[-61:]
    stock_return = float(window.iloc[-1]["stock"]) / float(window.iloc[0]["stock"]) - 1
    benchmark_return = (
        float(window.iloc[-1]["benchmark"]) / float(window.iloc[0]["benchmark"]) - 1
    )
    alpha = stock_return - benchmark_return
    if alpha > -threshold:
        return _outcome(kind, status=DetectionStatus.NORMAL)
    alert = AnomalyAlert(
        symbol=symbol,
        market=market,
        name=name,
        alert_type=kind,
        observed_on=str(window.iloc[-1]["trade_date"].date()),
        severity=abs(alpha),
        metrics={
            "stock_return_60d": stock_return,
            "benchmark_return_60d": benchmark_return,
            "alpha_60d": alpha,
        },
    )
    return _outcome(kind, status=DetectionStatus.ALERT, alert=alert)
