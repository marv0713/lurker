import os
import time
from collections.abc import Callable, Sequence
from datetime import date, timedelta

import pandas as pd
import akshare as ak
import yfinance as yf


PRICE_COLUMNS = [
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
]

BENCHMARK_SYMBOLS = {"cn": "000300.SH", "hk": "^HSI", "us": "SPY"}

CN_INDEX_COLUMN_ALIASES = {
    "trade_date": ("日期", "trade_date", "date"),
    "open": ("开盘", "open"),
    "high": ("最高", "high"),
    "low": ("最低", "low"),
    "close": ("收盘", "close"),
    "volume": ("成交量", "volume"),
}


def to_yfinance_symbol(symbol: str) -> str:
    if not symbol.endswith(".HK"):
        return symbol

    code = symbol.removesuffix(".HK")
    if len(code) > 4 and code.isdigit():
        return f"{code.lstrip('0')}.HK"
    return symbol


def to_akshare_symbol(symbol: str) -> str:
    return symbol.removesuffix(".SZ").removesuffix(".SH").removesuffix(".BJ")


def to_baostock_symbol(symbol: str) -> str:
    code = to_akshare_symbol(symbol)
    if symbol.endswith(".SZ"):
        return f"sz.{code}"
    if symbol.endswith(".SH"):
        return f"sh.{code}"
    if code.startswith(("60", "68", "90")):
        return f"sh.{code}"
    return f"sz.{code}"


def normalize_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.copy()
        raw.columns = raw.columns.get_level_values(0)

    normalized = raw.rename(
        columns={
            "Date": "trade_date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
    ).copy()
    if "trade_date" not in normalized.columns:
        normalized = normalized.reset_index(names="trade_date")
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    return normalized[PRICE_COLUMNS]


def normalize_cn_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = raw.rename(
        columns={
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
        }
    ).copy()
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    normalized["adj_close"] = normalized["close"]
    return normalized[PRICE_COLUMNS]


def _resolve_cn_index_columns(raw: pd.DataFrame) -> dict[str, str]:
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for canonical, aliases in CN_INDEX_COLUMN_ALIASES.items():
        source = next((alias for alias in aliases if alias in raw.columns), None)
        if source is None:
            missing.append(canonical)
        else:
            resolved[source] = canonical
    if missing:
        raise ValueError(f"missing CN index price columns: {', '.join(missing)}")
    return resolved


def normalize_cn_index_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = raw.rename(columns=_resolve_cn_index_columns(raw)).copy()
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    normalized["adj_close"] = normalized["close"]
    return normalized[PRICE_COLUMNS].sort_values("trade_date").reset_index(drop=True)


def normalize_tushare_cn_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = raw.rename(columns={"vol": "volume"}).copy()
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    normalized["adj_close"] = normalized["close"]
    return normalized[PRICE_COLUMNS].sort_values("trade_date").reset_index(drop=True)


def normalize_baostock_cn_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = raw.rename(columns={"date": "trade_date"}).copy()
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    normalized["adj_close"] = normalized["close"]
    return normalized[PRICE_COLUMNS].dropna(subset=["close"]).reset_index(drop=True)


def fetch_yfinance_prices(
    symbol: str,
    period: str = "1y",
    *,
    end_date: date | None = None,
) -> pd.DataFrame:
    download_args: dict[str, object] = {
        "progress": False,
        "auto_adjust": False,
        "multi_level_index": False,
    }
    if end_date is None:
        download_args["period"] = period
    else:
        download_args["start"] = pd.Timestamp(
            period_to_start_date(period, end_date=end_date)
        ).strftime("%Y-%m-%d")
        download_args["end"] = (end_date + timedelta(days=1)).isoformat()
    raw = yf.download(to_yfinance_symbol(symbol), **download_args)
    return normalize_price_frame(raw, symbol=symbol)


def period_to_start_date(period: str, *, end_date: date | None = None) -> str:
    anchor = pd.Timestamp(end_date) if end_date is not None else pd.Timestamp.today().normalize()
    if period.endswith("mo"):
        amount = int(period.removesuffix("mo"))
        start = anchor - pd.DateOffset(months=amount)
    elif period.endswith("d"):
        amount = int(period.removesuffix("d"))
        start = anchor - pd.DateOffset(days=amount)
    elif period.endswith("y"):
        amount = int(period.removesuffix("y"))
        start = anchor - pd.DateOffset(years=amount)
    else:
        raise ValueError(f"Unsupported period: {period}")
    return start.strftime("%Y%m%d")


def today_yyyymmdd(end_date: date | None = None) -> str:
    return (pd.Timestamp(end_date) if end_date is not None else pd.Timestamp.today()).strftime(
        "%Y%m%d"
    )


def fetch_akshare_cn_prices(
    symbol: str,
    period: str = "1y",
    *,
    end_date: date | None = None,
) -> pd.DataFrame:
    raw = ak.stock_zh_a_hist(
        symbol=to_akshare_symbol(symbol),
        period="daily",
        start_date=period_to_start_date(period, end_date=end_date),
        end_date=today_yyyymmdd(end_date),
        adjust="qfq",
    )
    return normalize_cn_price_frame(raw, symbol=symbol)


def fetch_cn_benchmark_prices(
    symbol: str = "000300.SH",
    period: str = "2y",
    *,
    end_date: date | None = None,
) -> pd.DataFrame:
    raw = ak.index_zh_a_hist(
        symbol=to_akshare_symbol(symbol),
        period="daily",
        start_date=period_to_start_date(period, end_date=end_date),
        end_date=today_yyyymmdd(end_date),
    )
    return normalize_cn_index_price_frame(raw, symbol)


def fetch_tushare_cn_prices(
    symbol: str,
    period: str = "1y",
    *,
    token: str | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    resolved_token = token or os.environ.get("TUSHARE_TOKEN", "")
    if not resolved_token:
        raise ValueError("TUSHARE_TOKEN is not set")

    import tushare as ts

    raw = ts.pro_bar(
        ts_code=symbol,
        adj="qfq",
        start_date=period_to_start_date(period, end_date=end_date),
        end_date=today_yyyymmdd(end_date),
        token=resolved_token,
    )
    if raw is None or raw.empty:
        raise ValueError("empty tushare price data")
    return normalize_tushare_cn_price_frame(raw, symbol=symbol)


def fetch_baostock_cn_prices(
    symbol: str,
    period: str = "1y",
    *,
    end_date: date | None = None,
) -> pd.DataFrame:
    import baostock as bs

    login = bs.login()
    if getattr(login, "error_code", "0") != "0":
        raise RuntimeError(f"baostock login failed: {getattr(login, 'error_msg', '')}")
    try:
        result = bs.query_history_k_data_plus(
            to_baostock_symbol(symbol),
            "date,code,open,high,low,close,volume",
            start_date=pd.to_datetime(
                period_to_start_date(period, end_date=end_date)
            ).strftime("%Y-%m-%d"),
            end_date=(end_date or date.today()).isoformat(),
            frequency="d",
            adjustflag="2",
        )
        rows: list[list[str]] = []
        while result.error_code == "0" and result.next():
            rows.append(result.get_row_data())
        if result.error_code != "0":
            raise RuntimeError(f"baostock query failed: {result.error_msg}")
        raw = pd.DataFrame(rows, columns=result.fields)
        if raw.empty:
            raise ValueError("empty baostock price data")
        return normalize_baostock_cn_price_frame(raw, symbol=symbol)
    finally:
        bs.logout()


CnPriceFetcher = Callable[..., pd.DataFrame]


def fetch_cn_prices(
    symbol: str,
    period: str = "1y",
    *,
    fetchers: Sequence[CnPriceFetcher] | None = None,
    sleep_seconds: float = 0.8,
    end_date: date | None = None,
) -> pd.DataFrame:
    providers = list(fetchers or [
        fetch_tushare_cn_prices,
        fetch_akshare_cn_prices,
        fetch_baostock_cn_prices,
    ])
    errors: list[str] = []

    for index, fetcher in enumerate(providers):
        try:
            result = (
                fetcher(symbol, period)
                if end_date is None
                else fetcher(symbol, period, end_date=end_date)
            )
            if result.empty:
                raise ValueError("empty price data")
            return result
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {type(exc).__name__}: {exc}")
            if sleep_seconds > 0 and index < len(providers) - 1:
                time.sleep(sleep_seconds)

    raise RuntimeError("; ".join(errors))


def fetch_watchlist_history(
    symbol: str,
    market: str,
    period: str = "2y",
    *,
    is_benchmark: bool = False,
    stock_fetcher: CnPriceFetcher | None = None,
    cn_benchmark_fetcher: CnPriceFetcher | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    if market == "cn" and is_benchmark:
        fetcher = cn_benchmark_fetcher or fetch_cn_benchmark_prices
        return (
            fetcher(symbol, period)
            if end_date is None
            else fetcher(symbol, period, end_date=end_date)
        )
    if market == "cn":
        fetcher = stock_fetcher or fetch_cn_prices
        return (
            fetcher(symbol, period)
            if end_date is None
            else fetcher(symbol, period, end_date=end_date)
        )
    return fetch_yfinance_prices(symbol, period, end_date=end_date)
