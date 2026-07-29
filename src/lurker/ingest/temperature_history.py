"""Historical collectors for the market-temperature rollout replay."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
import http.client
import json
import os
import ssl
import subprocess
import time
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import pandas as pd
import requests

from lurker.ingest.etf_flows import (
    CoreEtfBatch,
    CoreEtfItem,
    EtfProviderError,
    EtfSchemaError,
    _normalize_etf_history,
)
from lurker.ingest.flows import (
    _akshare_request_scope,
    fetch_akshare_margin_history,
    normalize_margin_frame,
    normalize_market_flow_frame,
)
from lurker.trading_calendar import is_cn_trading_day


_SHANGHAI_TZ = timezone(timedelta(hours=8))
_RECOVERABLE_PROVIDER_ERRORS = (
    requests.RequestException,
    ConnectionError,
    TimeoutError,
    OSError,
)


class MarketFlowHistorySchemaError(ValueError):
    """Raised when Eastmoney market-flow history violates its schema."""


def _fetch_market_history_payload_with_curl(
    request_url: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    result = runner(
        [
            "curl",
            "--noproxy",
            "*",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "30",
            "--user-agent",
            "Mozilla/5.0",
            request_url,
        ],
        check=True,
        capture_output=True,
        timeout=40,
    )
    try:
        payload = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketFlowHistorySchemaError(
            "curl market-flow history response is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketFlowHistorySchemaError(
            "curl market-flow history response is not an object"
        )
    return payload


def fetch_market_flow_history(
    *,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch market-flow history without inheriting process proxy settings."""
    url = (
        "https://push2his.eastmoney.com/"
        "api/qt/stock/fflow/daykline/get"
    )
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": "1.000001",
        "secid2": "0.399001",
        "fields1": "f1,f2,f3,f7",
        "fields2": (
            "f51,f52,f53,f54,f55,f56,f57,f58,"
            "f59,f60,f61,f62,f63,f64,f65"
        ),
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    headers = {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": "Mozilla/5.0",
    }
    if session is not None:
        session.trust_env = False
        response = None
        for attempt in range(3):
            request_params = dict(params)
            request_params["_"] = int(time.time() * 1000)
            try:
                response = session.get(
                    url,
                    params=request_params,
                    headers=headers,
                    timeout=30,
                )
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(0.25 * (attempt + 1))
        if response is None:
            raise RuntimeError("market-flow history request did not run")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketFlowHistorySchemaError(
                "market-flow history response is not valid JSON"
            ) from exc
    else:
        request_params = dict(params)
        request_params["_"] = int(time.time() * 1000)
        request_url = f"{url}?{urllib_parse.urlencode(request_params)}"
        ssl_context = ssl.create_default_context(
            cafile=requests.certs.where()
        )
        opener = urllib_request.build_opener(
            urllib_request.ProxyHandler({}),
            urllib_request.HTTPSHandler(context=ssl_context),
        )
        payload = None
        last_network_error: Exception | None = None
        for attempt in range(3):
            try:
                request = urllib_request.Request(
                    request_url,
                    headers=headers,
                )
                with opener.open(request, timeout=30) as response:
                    payload = json.loads(response.read())
                break
            except (
                urllib_error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.RemoteDisconnected,
            ) as exc:
                last_network_error = exc
                if attempt == 2:
                    break
                time.sleep(0.25 * (attempt + 1))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MarketFlowHistorySchemaError(
                    "market-flow history response is not valid JSON"
                ) from exc
        if payload is None:
            try:
                payload = _fetch_market_history_payload_with_curl(request_url)
            except (OSError, subprocess.SubprocessError):
                if last_network_error is not None:
                    raise last_network_error
                raise
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("klines") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise MarketFlowHistorySchemaError(
            "market-flow history response has no data.klines"
        )

    parsed_rows: list[list[str]] = []
    for row in rows:
        values = str(row).split(",")
        if len(values) != 15:
            raise MarketFlowHistorySchemaError(
                "market-flow history kline must contain 15 fields"
            )
        parsed_rows.append(values)

    columns = [
        "日期",
        "主力净流入-净额",
        "小单净流入-净额",
        "中单净流入-净额",
        "大单净流入-净额",
        "超大单净流入-净额",
        "主力净流入-净占比",
        "小单净流入-净占比",
        "中单净流入-净占比",
        "大单净流入-净占比",
        "超大单净流入-净占比",
        "上证-收盘价",
        "上证-涨跌幅",
        "深证-收盘价",
        "深证-涨跌幅",
    ]
    frame = pd.DataFrame(parsed_rows, columns=columns)
    parsed_dates = pd.to_datetime(frame["日期"], errors="coerce")
    if parsed_dates.isna().any():
        raise MarketFlowHistorySchemaError(
            "market-flow history contains invalid dates"
        )
    frame["日期"] = parsed_dates.dt.date
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    required_values = [
        "主力净流入-净额",
        "超大单净流入-净额",
        "大单净流入-净额",
    ]
    if frame[required_values].isna().any(axis=None):
        raise MarketFlowHistorySchemaError(
            "market-flow history contains invalid flow values"
        )
    frame.attrs["source"] = "eastmoney_market_flow_history"
    return frame


def collect_temperature_replay(
    *,
    etf_configs: list[dict[str, str]],
    etf_start: str,
    margin_start: str,
    output_start: str,
    output_end: str,
    market_flow_fetcher: Callable[[], pd.DataFrame] | None = None,
    etf_history_fetcher: Callable[..., pd.DataFrame] | None = None,
    margin_fetcher: Callable[..., pd.DataFrame] | None = None,
    is_trading_day: Callable[[date], bool] = is_cn_trading_day,
) -> list[dict[str, Any]]:
    """Collect aligned raw facts for every output trading day.

    ETF history and margin history start earlier than the output range so the
    first output day has a 20-session turnover average and a prior margin
    balance. Missing sources are retained as explicit unavailable facts.
    """
    start_etf = _parse_date(etf_start, "etf_start")
    start_margin = _parse_date(margin_start, "margin_start")
    start_output = _parse_date(output_start, "output_start")
    end_output = _parse_date(output_end, "output_end")
    if start_etf > start_output:
        raise ValueError("etf_start must not be after output_start")
    if start_margin > start_output:
        raise ValueError("margin_start must not be after output_start")
    if start_output > end_output:
        raise ValueError("output_start must not be after output_end")
    if not etf_configs:
        raise ValueError("etf_configs must not be empty")

    output_days = list(_trading_days(start_output, end_output, is_trading_day))
    market_by_date = _load_market_history(market_flow_fetcher)
    etf_histories, etf_errors = _load_etf_histories(
        etf_configs,
        start_date=start_etf,
        end_date=end_output,
        fetcher=etf_history_fetcher,
    )
    margin_by_date = _load_margin_history(
        start_date=start_margin,
        end_date=end_output,
        fetcher=margin_fetcher,
        is_trading_day=is_trading_day,
    )

    records: list[dict[str, Any]] = []
    for trade_day in output_days:
        trade_date = trade_day.isoformat()
        records.append(
            {
                "date": trade_date,
                "market_flow": market_by_date.get(
                    trade_date,
                    {
                        "trade_date": trade_date,
                        "main_net_inflow": None,
                        "super_large_net_inflow": None,
                        "large_net_inflow": None,
                        "availability": "unknown",
                        "source": "unavailable",
                        "reason": "historical provider did not return replay date",
                    },
                ),
                "core_etfs": _build_etf_batch_for_day(
                    etf_configs,
                    etf_histories=etf_histories,
                    etf_errors=etf_errors,
                    trade_day=trade_day,
                ).to_dict(),
                "margin": margin_by_date.get(
                    trade_date,
                    {
                        "trade_date": trade_day.strftime("%Y%m%d"),
                        "financing_balance": None,
                        "securities_lending_balance": None,
                        "margin_balance": None,
                        "margin_balance_change": None,
                        "availability": "unknown",
                        "source": "unavailable",
                        "reason": "historical provider did not return replay date",
                    },
                ),
            }
        )
    return records


def _load_market_history(
    fetcher: Callable[[], pd.DataFrame] | None,
) -> dict[str, dict[str, Any]]:
    if fetcher is None:
        raw = _fetch_market_flow_history()
    else:
        raw = fetcher()
    if not isinstance(raw, pd.DataFrame):
        raise TypeError("market_flow_fetcher must return a DataFrame")
    if raw.empty or "日期" not in raw.columns:
        return {}

    result: dict[str, dict[str, Any]] = {}
    normalized_dates = pd.to_datetime(raw["日期"], errors="coerce")
    for index, parsed_date in normalized_dates.items():
        if pd.isna(parsed_date):
            continue
        normalized = normalize_market_flow_frame(raw.loc[[index]])
        normalized["availability"] = "fresh"
        normalized["source"] = raw.attrs.get("source", "injected_market_flow_history")
        result[parsed_date.date().isoformat()] = normalized
    return result


def _load_etf_histories(
    configs: list[dict[str, str]],
    *,
    start_date: date,
    end_date: date,
    fetcher: Callable[..., pd.DataFrame] | None,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    using_default_fetcher = fetcher is None
    if using_default_fetcher:
        import akshare as ak

        def fetcher(**kwargs: Any) -> pd.DataFrame:
            try:
                with _akshare_request_scope():
                    raw = ak.fund_etf_hist_em(**kwargs)
            except _RECOVERABLE_PROVIDER_ERRORS:
                raw = pd.DataFrame()
            if not raw.empty:
                raw.attrs["source"] = "akshare_fund_etf_hist_em"
                return raw

            symbol = str(kwargs["symbol"])
            prefix = "sh" if symbol.startswith(("5", "6")) else "sz"
            try:
                with _akshare_request_scope():
                    sina_raw = ak.fund_etf_hist_sina(symbol=f"{prefix}{symbol}")
            except _RECOVERABLE_PROVIDER_ERRORS as exc:
                raise EtfProviderError(
                    f"{symbol}: both ETF history providers unavailable: {exc}"
                ) from exc
            normalized = normalize_sina_etf_history(sina_raw)
            dates = pd.to_datetime(normalized["日期"], errors="coerce")
            start = pd.to_datetime(str(kwargs["start_date"]), format="%Y%m%d")
            end = pd.to_datetime(str(kwargs["end_date"]), format="%Y%m%d")
            filtered = normalized.loc[(dates >= start) & (dates <= end)].copy()
            filtered.attrs.update(normalized.attrs)
            return filtered

    histories: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for config in configs:
        canonical = str(config.get("canonical_symbol") or "").strip()
        provider_symbol = str(config.get("symbol") or canonical.split(".", 1)[0]).strip()
        if not canonical or not provider_symbol:
            raise ValueError("ETF replay config requires symbol and canonical_symbol")
        if using_default_fetcher:
            try:
                raw = fetcher(
                    symbol=provider_symbol,
                    period="daily",
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjust="",
                )
            except (EtfProviderError, EtfSchemaError) as exc:
                errors[canonical] = str(exc)
                continue
        else:
            raw = fetcher(
                symbol=provider_symbol,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="",
            )
        if not isinstance(raw, pd.DataFrame):
            raise TypeError("ETF history fetcher must return a DataFrame")
        histories[canonical] = raw
    return histories, errors


def _load_margin_history(
    *,
    start_date: date,
    end_date: date,
    fetcher: Callable[..., pd.DataFrame] | None,
    is_trading_day: Callable[[date], bool],
) -> dict[str, dict[str, Any]]:
    using_default_tushare = False
    if fetcher is None:
        token = os.environ.get("TUSHARE_TOKEN", "")
        if token:
            import tushare as ts

            tushare_fetcher = ts.pro_api(token).margin
            try:
                tushare_fetcher(trade_date=start_date.strftime("%Y%m%d"))
            except Exception as exc:
                if _is_recoverable_tushare_error(exc):
                    return _load_akshare_margin_history(start_date, end_date)
                raise
            fetcher = tushare_fetcher
            using_default_tushare = True
        else:
            return _load_akshare_margin_history(start_date, end_date)

    result: dict[str, dict[str, Any]] = {}
    previous_balance: float | None = None
    previous_trade_date: str | None = None
    for trade_day in _trading_days(start_date, end_date, is_trading_day):
        tushare_date = trade_day.strftime("%Y%m%d")
        if using_default_tushare:
            try:
                raw = fetcher(trade_date=tushare_date)
            except Exception as exc:
                if _is_recoverable_tushare_error(exc):
                    return _load_akshare_margin_history(start_date, end_date)
                raise
        else:
            raw = fetcher(trade_date=tushare_date)
        if not isinstance(raw, pd.DataFrame):
            raise TypeError("margin_fetcher must return a DataFrame")
        if raw.empty:
            continue
        normalized = normalize_margin_frame(
            raw,
            previous_margin_balance=previous_balance,
            previous_trade_date=previous_trade_date,
        )
        if not normalized:
            continue
        normalized["availability"] = "fresh"
        result[trade_day.isoformat()] = normalized
        current_balance = normalized.get("margin_balance")
        if current_balance is not None:
            previous_balance = float(current_balance)
            previous_trade_date = str(normalized.get("trade_date", ""))
    return result


def _build_etf_batch_for_day(
    configs: list[dict[str, str]],
    *,
    etf_histories: dict[str, pd.DataFrame],
    etf_errors: dict[str, str],
    trade_day: date,
) -> CoreEtfBatch:
    items: list[CoreEtfItem] = []
    failures: list[dict[str, str]] = []
    configured_symbols = [
        str(config.get("canonical_symbol") or "").strip()
        for config in configs
    ]
    for config, canonical in zip(configs, configured_symbols, strict=True):
        raw = etf_histories.get(canonical)
        if raw is None:
            failures.append(
                {
                    "symbol": canonical,
                    "reason": etf_errors.get(canonical) or "no ETF history for replay date",
                }
            )
            continue
        try:
            if raw.empty:
                raise EtfProviderError("no ETF history for replay date")
            subset = _etf_history_through(raw, trade_day)
            if subset.empty:
                raise EtfProviderError("no ETF history for replay date")
            latest_date = pd.to_datetime(subset["日期"], errors="coerce").max()
            if pd.isna(latest_date) or latest_date.date() != trade_day:
                raise EtfProviderError("no ETF history for replay date")
            item = _normalize_etf_history(
                subset,
                canonical_symbol=canonical,
                name=str(config.get("name", "")),
                now_shanghai=datetime.combine(
                    trade_day,
                    datetime.min.time().replace(hour=16),
                    tzinfo=_SHANGHAI_TZ,
                ),
            )
            item.source = str(
                raw.attrs.get("source", "injected_etf_history")
            )
        except (EtfProviderError, EtfSchemaError) as exc:
            failures.append({"symbol": canonical, "reason": str(exc)})
            continue
        items.append(item)
    return CoreEtfBatch(
        configured_symbols=configured_symbols,
        items=items,
        failures=failures,
        generated_at=datetime.now(UTC).isoformat(),
        schema_version=1,
    )


def _etf_history_through(raw: pd.DataFrame, trade_day: date) -> pd.DataFrame:
    if "日期" not in raw.columns:
        raise EtfSchemaError("ETF history missing columns ['日期']")
    parsed = pd.to_datetime(raw["日期"], errors="coerce")
    return raw.loc[parsed.dt.date <= trade_day].copy()


def _trading_days(
    start_date: date,
    end_date: date,
    predicate: Callable[[date], bool],
):
    cursor = start_date
    while cursor <= end_date:
        if predicate(cursor):
            yield cursor
        cursor += timedelta(days=1)


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc


def normalize_sina_etf_history(raw: pd.DataFrame) -> pd.DataFrame:
    """Map Sina ETF history to the canonical AkShare turnover columns."""
    if not isinstance(raw, pd.DataFrame):
        raise TypeError("Sina ETF history must be a DataFrame")
    missing = {"date", "amount"} - set(raw.columns)
    if missing:
        raise EtfSchemaError(f"Sina ETF history missing columns {sorted(missing)}")
    result = raw.loc[:, ["date", "amount"]].rename(
        columns={"date": "日期", "amount": "成交额"}
    )
    result.attrs["source"] = "akshare_fund_etf_hist_sina"
    return result


def _fetch_market_flow_history() -> pd.DataFrame:
    return fetch_market_flow_history()


def _is_recoverable_tushare_error(exc: Exception) -> bool:
    if isinstance(exc, _RECOVERABLE_PROVIDER_ERRORS):
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "没有访问该接口的权限",
            "无权限",
            "permission",
            "积分",
            "token",
        )
    )


def _load_akshare_margin_history(
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, Any]]:
    combined = fetch_akshare_margin_history()
    return {
        trade_date: item
        for trade_date, item in combined.items()
        if start_date <= date.fromisoformat(trade_date) <= end_date
    }
