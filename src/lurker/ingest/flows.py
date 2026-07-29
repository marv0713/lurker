from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd

import functools
import json
import requests as _requests
import sys

_AKSHARE_PROXY = os.environ.get("AKSHARE_PROXY", "").strip()
_AKSHARE_PROXIES = (
    {"http": _AKSHARE_PROXY, "https": _AKSHARE_PROXY}
    if _AKSHARE_PROXY
    else {}
)


def _make_proxy_func(method: str):  # type: ignore[no-untyped-def]
    orig = getattr(_requests, method)

    @functools.wraps(orig)
    def _wrapped(url, **kwargs):  # type: ignore[no-untyped-def]
        is_eastmoney = False
        for domain in ["push2.eastmoney.com", "push2his.eastmoney.com"]:
            if domain in url:
                url = url.replace(domain, "push2delay.eastmoney.com")
                is_eastmoney = True
                break

        if is_eastmoney:
            kwargs["proxies"] = {}
            # 增加超时时间以防止 delay 接口在拥堵时超时 (Increase timeout to 30s)
            if "timeout" in kwargs:
                t = kwargs["timeout"]
                if isinstance(t, (int, float)):
                    kwargs["timeout"] = max(t, 30)
                elif isinstance(t, tuple) and len(t) == 2:
                    kwargs["timeout"] = (t[0], max(t[1], 30))
            else:
                kwargs["timeout"] = 30
        else:
            kwargs.setdefault("proxies", _AKSHARE_PROXIES)
        return orig(url, **kwargs)

    return _wrapped


@contextmanager
def _akshare_request_scope():
    """Temporarily patch requests only while AkShare fetchers run."""
    original_get = _requests.get
    original_post = _requests.post
    _requests.get = _make_proxy_func("get")  # type: ignore[assignment]
    _requests.post = _make_proxy_func("post")  # type: ignore[assignment]
    try:
        yield
    finally:
        _requests.get = original_get  # type: ignore[assignment]
        _requests.post = original_post  # type: ignore[assignment]


def format_cn_symbol(code: str) -> str:
    cleaned = str(code).strip()
    if cleaned.endswith((".SZ", ".SH", ".BJ")):
        return cleaned
    if cleaned.startswith(("60", "68", "90", "51", "58")):
        return f"{cleaned}.SH"
    if cleaned.startswith(("43", "83", "87", "92")):
        return f"{cleaned}.BJ"
    return f"{cleaned}.SZ"


def _first_present(row: pd.Series, names: list[str]) -> Any:
    for name in names:
        if name in row and pd.notna(row[name]):
            return row[name]
    return None


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
        if value in {"", "-", "--", "---"}:
            return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_float(value: Any) -> float | None:
    """Convert to float, returning None for truly missing/empty values.

    Used for market flow columns where 0.0 is a meaningful value (资金持平)
    but a missing column is not (缺失不是零).
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
        if value in {"", "-", "--", "---"}:
            return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _sum_optional_numeric(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    numeric = pd.to_numeric(frame[column], errors="coerce")
    numeric = numeric[numeric.map(lambda value: pd.notna(value) and math.isfinite(float(value)))]
    if numeric.empty:
        return None
    return float(numeric.sum())


def normalize_market_flow_frame(raw: pd.DataFrame) -> dict[str, Any]:
    if raw.empty:
        return {}
    trade_date = ""
    if "日期" in raw.columns:
        ordered = raw.copy()
        ordered["日期"] = pd.to_datetime(ordered["日期"], errors="coerce")
        ordered = ordered.sort_values("日期")
        row = ordered.iloc[-1]
        latest_date = row.get("日期")
        if pd.notna(latest_date):
            trade_date = str(latest_date.date()) if hasattr(latest_date, "date") else str(latest_date)
    else:
        row = raw.iloc[-1]
    return {
        "trade_date": trade_date,
        "main_net_inflow": _to_optional_float(
            _first_present(row, ["今日主力净流入-净额", "主力净流入", "主力净流入-净额"])
        ),
        "super_large_net_inflow": _to_optional_float(
            _first_present(row, ["今日超大单净流入-净额", "超大单净流入", "超大单净流入-净额"])
        ),
        "large_net_inflow": _to_optional_float(
            _first_present(row, ["今日大单净流入-净额", "大单净流入", "大单净流入-净额"])
        ),
    }


def normalize_sector_flow_frame(raw: pd.DataFrame, *, category: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, row in raw.reset_index(drop=True).iterrows():
        name = _first_present(row, ["名称", "行业", "板块名称"])
        if not name:
            continue
        results.append(
            {
                "name": str(name),
                "category": category,
                "main_net_inflow": _to_float(
                    _first_present(row, ["今日主力净流入-净额", "主力净流入", "主力净流入-净额"])
                ),
                "rank": int(index) + 1,
            }
        )
    return results


def normalize_stock_flow_frame(raw: pd.DataFrame) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        code = _first_present(row, ["代码", "股票代码", "code"])
        if not code:
            continue
        results.append(
            {
                "symbol": format_cn_symbol(str(code)),
                "name": str(_first_present(row, ["名称", "股票简称", "name"]) or ""),
                "main_net_inflow": _to_float(
                    _first_present(row, ["今日主力净流入-净额", "主力净流入", "主力净流入-净额"])
                ),
                "super_large_net_inflow": _to_float(
                    _first_present(row, ["今日超大单净流入-净额", "超大单净流入", "超大单净流入-净额"])
                ),
                "main_net_inflow_5d": _to_float(
                    _first_present(row, ["5日主力净流入-净额", "5日主力净流入"])
                ),
                "main_net_inflow_10d": _to_float(
                    _first_present(row, ["10日主力净流入-净额", "10日主力净流入"])
                ),
            }
        )
    return results


def normalize_margin_frame(
    raw: pd.DataFrame,
    *,
    previous_margin_balance: float | None = None,
    previous_trade_date: str | None = None,
    previous_margin_balance_change: float | None = None,
) -> dict[str, Any]:
    if raw.empty:
        return {}
    current = raw
    if "trade_date" in raw.columns:
        trade_dates = raw["trade_date"].dropna().astype(str)
        if not trade_dates.empty:
            latest_trade_date = trade_dates.max()
            current = raw[raw["trade_date"].astype(str) == latest_trade_date]
    margin_balance = _sum_optional_numeric(current, "rzrqye")
    trade_date = str(current.iloc[0].get("trade_date", ""))
    result = {
        "trade_date": trade_date,
        "financing_balance": _sum_optional_numeric(current, "rzye"),
        "securities_lending_balance": _sum_optional_numeric(current, "rqye"),
        "margin_balance": margin_balance,
        "availability": "fresh",
    }
    if (
        margin_balance is not None
        and previous_margin_balance is not None
        and str(previous_trade_date or "") != trade_date
    ):
        result["margin_balance_change"] = margin_balance - previous_margin_balance
    elif (
        margin_balance is not None
        and str(previous_trade_date or "") == trade_date
        and previous_margin_balance_change is not None
    ):
        result["margin_balance_change"] = previous_margin_balance_change
    return result


def normalize_akshare_margin_histories(
    sh_raw: pd.DataFrame,
    sz_raw: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    """Combine complete Shanghai and Shenzhen margin totals by date."""
    required = {"日期", "融资余额", "融券余额", "融资融券余额"}
    frames: list[pd.DataFrame] = []
    for exchange, raw in (("SH", sh_raw), ("SZ", sz_raw)):
        missing = required - set(raw.columns)
        if missing:
            raise ValueError(
                f"{exchange} margin history missing columns {sorted(missing)}"
            )
        frame = raw.loc[:, list(required)].copy()
        frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
        for column in required - {"日期"}:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["_exchange"] = exchange
        frames.append(frame.dropna(subset=["日期"]))

    combined = pd.concat(frames, ignore_index=True)
    complete_dates = (
        combined.groupby("日期")["_exchange"].nunique().loc[lambda value: value == 2]
    ).index
    totals = (
        combined.loc[combined["日期"].isin(complete_dates)]
        .groupby("日期", as_index=False)[
            ["融资余额", "融券余额", "融资融券余额"]
        ]
        .sum(min_count=1)
        .sort_values("日期")
    )

    result: dict[str, dict[str, Any]] = {}
    previous_balance: float | None = None
    for _, row in totals.iterrows():
        margin_balance = _to_optional_float(row["融资融券余额"])
        if margin_balance is None:
            continue
        trade_day = row["日期"].date()
        item: dict[str, Any] = {
            "trade_date": trade_day.strftime("%Y%m%d"),
            "financing_balance": _to_optional_float(row["融资余额"]),
            "securities_lending_balance": _to_optional_float(row["融券余额"]),
            "margin_balance": margin_balance,
            "availability": "fresh",
            "source": "akshare_jin10_margin_sh_sz",
        }
        if previous_balance is not None:
            item["margin_balance_change"] = margin_balance - previous_balance
        result[trade_day.isoformat()] = item
        previous_balance = margin_balance
    return result


def _fetch_akshare_margin_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    with _akshare_request_scope():
        sh_raw = ak.macro_china_market_margin_sh()
        sz_raw = ak.macro_china_market_margin_sz()
    return sh_raw, sz_raw


def fetch_akshare_margin_history() -> dict[str, dict[str, Any]]:
    sh_raw, sz_raw = _fetch_akshare_margin_frames()
    return normalize_akshare_margin_histories(sh_raw, sz_raw)


def fetch_akshare_margin_latest() -> dict[str, Any]:
    history = fetch_akshare_margin_history()
    if not history:
        raise ValueError("AkShare margin history has no complete SH+SZ dates")
    return history[max(history)]


def _is_recoverable_margin_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            _requests.RequestException,
            ConnectionError,
            TimeoutError,
            OSError,
        ),
    ):
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "访问权限",
            "无权限",
            "permission",
            "积分",
            "token",
            "rate limit",
            "timeout",
            "timed out",
        )
    )


def fetch_market_flow() -> dict[str, Any]:
    with _akshare_request_scope():
        raw = ak.stock_market_fund_flow()
    return normalize_market_flow_frame(raw)


def fetch_sector_flows() -> list[dict[str, Any]]:
    flows: list[dict[str, Any]] = []
    try:
        # 尝试最新版 AkShare 传参方式
        with _akshare_request_scope():
            raw = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        flows.extend(normalize_sector_flow_frame(raw, category="industry"))
    except Exception:
        try:
            # 兼容中旧版写法
            with _akshare_request_scope():
                raw = ak.stock_sector_fund_flow_rank(indicator="行业资金流")
            flows.extend(normalize_sector_flow_frame(raw, category="industry"))
        except Exception:
            # 终极降级兜底
            with _akshare_request_scope():
                raw = ak.stock_sector_fund_flow_rank()
            flows.extend(normalize_sector_flow_frame(raw, category="industry"))
    return flows


def fetch_stock_flows() -> list[dict[str, Any]]:
    with _akshare_request_scope():
        try:
            today_raw = ak.stock_individual_fund_flow_rank(indicator="今日")
        except TypeError:
            today_raw = ak.stock_individual_fund_flow_rank()
        five_raw = ak.stock_individual_fund_flow_rank(indicator="5日")
        ten_raw = ak.stock_individual_fund_flow_rank(indicator="10日")

    merged: dict[str, dict[str, Any]] = {}
    for row in normalize_stock_flow_frame(today_raw):
        merged[row["symbol"]] = row
    for row in normalize_stock_flow_frame(five_raw):
        current = merged.setdefault(row["symbol"], {"symbol": row["symbol"], "name": row["name"]})
        if row.get("name"):
            current["name"] = row["name"]
        current["main_net_inflow_5d"] = row.get("main_net_inflow_5d", 0.0)
    for row in normalize_stock_flow_frame(ten_raw):
        current = merged.setdefault(row["symbol"], {"symbol": row["symbol"], "name": row["name"]})
        if row.get("name"):
            current["name"] = row["name"]
        current["main_net_inflow_10d"] = row.get("main_net_inflow_10d", 0.0)
    return list(merged.values())


def fetch_margin(
    *,
    token: str | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    resolved_token = (
        os.environ.get("TUSHARE_TOKEN", "")
        if token is None
        else token
    )
    if cache_path is None:
        root_dir = Path(__file__).resolve().parents[3]
        cache_path = root_dir / "data" / "processed" / "margin_cache.json"
    else:
        cache_path = Path(cache_path)

    previous: dict[str, Any] = {}
    if cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = {}

    primary_error: Exception | None = None
    data: dict[str, Any] = {}
    if resolved_token:
        import tushare as ts

        try:
            pro = ts.pro_api(resolved_token)
            raw = pro.margin()
            data = normalize_margin_frame(
                raw,
                previous_margin_balance=_to_optional_float(
                    previous.get("margin_balance")
                ),
                previous_trade_date=str(previous.get("trade_date", "")) or None,
                previous_margin_balance_change=_to_optional_float(
                    previous.get("margin_balance_change")
                ),
            )
            if data:
                data["source"] = "tushare_margin"
        except Exception as exc:
            if not _is_recoverable_margin_error(exc):
                raise
            primary_error = exc

    if not data:
        try:
            data = fetch_akshare_margin_latest()
        except Exception as exc:
            failure = primary_error or exc
            if previous:
                print(
                    "Warning: online margin providers failed "
                    f"({failure}; AkShare: {exc}). Loading cached margin data "
                    f"from {cache_path}.",
                    file=sys.stderr,
                )
                previous["availability"] = "stale_cache"
                return previous
            raise failure

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"Warning: failed to write margin cache: {exc}", file=sys.stderr)
    return data
