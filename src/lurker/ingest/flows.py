from __future__ import annotations

import os
from typing import Any

import akshare as ak
import pandas as pd


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
    row = raw.iloc[0]
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


def normalize_margin_frame(raw: pd.DataFrame) -> dict[str, Any]:
    if raw.empty:
        return {}
    return {
        "trade_date": str(raw.iloc[0].get("trade_date", "")),
        "financing_balance": float(pd.to_numeric(raw.get("rzye", 0), errors="coerce").fillna(0).sum()),
        "securities_lending_balance": float(
            pd.to_numeric(raw.get("rqye", 0), errors="coerce").fillna(0).sum()
        ),
        "margin_balance": float(
            pd.to_numeric(raw.get("rzrqye", 0), errors="coerce").fillna(0).sum()
        ),
    }


def fetch_market_flow() -> dict[str, float]:
    raw = ak.stock_market_fund_flow()
    return normalize_market_flow_frame(raw)


def fetch_sector_flows() -> list[dict[str, Any]]:
    flows: list[dict[str, Any]] = []
    try:
        flows.extend(
            normalize_sector_flow_frame(
                ak.stock_sector_fund_flow_rank(indicator="行业资金流"), category="industry"
            )
        )
    except TypeError:
        flows.extend(normalize_sector_flow_frame(ak.stock_sector_fund_flow_rank(), category="industry"))
    return flows


def fetch_stock_flows() -> list[dict[str, Any]]:
    try:
        raw = ak.stock_individual_fund_flow_rank(indicator="今日")
    except TypeError:
        raw = ak.stock_individual_fund_flow_rank()
    return normalize_stock_flow_frame(raw)


def fetch_margin(*, token: str | None = None) -> dict[str, Any]:
    resolved_token = token or os.environ.get("TUSHARE_TOKEN", "")
    if not resolved_token:
        return {}
    import tushare as ts

    pro = ts.pro_api(resolved_token)
    raw = pro.margin()
    return normalize_margin_frame(raw)
