from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd

import functools
import json
import requests as _requests
import sys

_AKSHARE_PROXY = os.environ.get("AKSHARE_PROXY", "http://127.0.0.1:7897")
_AKSHARE_PROXIES = {"http": _AKSHARE_PROXY, "https": _AKSHARE_PROXY}


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


def normalize_market_flow_frame(raw: pd.DataFrame) -> dict[str, float]:
    if raw.empty:
        return {}
    if "日期" in raw.columns:
        ordered = raw.copy()
        ordered["日期"] = pd.to_datetime(ordered["日期"], errors="coerce")
        ordered = ordered.sort_values("日期")
        row = ordered.iloc[-1]
    else:
        row = raw.iloc[-1]
    return {
        "main_net_inflow": _to_float(
            _first_present(row, ["今日主力净流入-净额", "主力净流入", "主力净流入-净额"])
        ),
        "super_large_net_inflow": _to_float(
            _first_present(row, ["今日超大单净流入-净额", "超大单净流入", "超大单净流入-净额"])
        ),
        "large_net_inflow": _to_float(
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
) -> dict[str, Any]:
    if raw.empty:
        return {}
    margin_balance = float(
        pd.to_numeric(raw.get("rzrqye", 0), errors="coerce").fillna(0).sum()
    )
    result = {
        "trade_date": str(raw.iloc[0].get("trade_date", "")),
        "financing_balance": float(pd.to_numeric(raw.get("rzye", 0), errors="coerce").fillna(0).sum()),
        "securities_lending_balance": float(
            pd.to_numeric(raw.get("rqye", 0), errors="coerce").fillna(0).sum()
        ),
        "margin_balance": margin_balance,
    }
    if previous_margin_balance is not None:
        result["margin_balance_change"] = margin_balance - previous_margin_balance
    return result


def fetch_market_flow() -> dict[str, float]:
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


def fetch_margin(*, token: str | None = None, cache_path: Path | None = None) -> dict[str, Any]:
    resolved_token = token or os.environ.get("TUSHARE_TOKEN", "")
    if not resolved_token:
        return {}

    if cache_path is None:
        root_dir = Path(__file__).resolve().parents[3]
        cache_path = root_dir / "data" / "processed" / "margin_cache.json"
    else:
        cache_path = Path(cache_path)

    import tushare as ts
    try:
        previous_margin_balance = None
        if cache_path.exists():
            try:
                previous = json.loads(cache_path.read_text(encoding="utf-8"))
                previous_margin_balance = _to_float(previous.get("margin_balance"))
            except Exception:
                previous_margin_balance = None
        pro = ts.pro_api(resolved_token)
        raw = pro.margin()
        data = normalize_margin_frame(raw, previous_margin_balance=previous_margin_balance)
        if data:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"Warning: failed to write margin cache: {e}", file=sys.stderr)
        return data
    except Exception as exc:
        if cache_path.exists():
            try:
                print(
                    f"Warning: fetch_margin failed ({exc}). Loading cached margin data from {cache_path}.",
                    file=sys.stderr
                )
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception as cache_exc:
                print(f"Warning: failed to read margin cache: {cache_exc}", file=sys.stderr)
                raise exc
        raise exc
