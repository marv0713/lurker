from pathlib import Path
from typing import Any, Callable

import akshare as ak
import pandas as pd

from lurker.config import load_markets, load_themes
from lurker.universe.seed_pool import build_seed_symbols, collect_seed_sources


CnIndexFetcher = Callable[[str], pd.DataFrame]
CnIndexResolver = Callable[[str], list[str]]
CnEtfResolver = Callable[[str], list[str]]

CN_INDEX_SOURCES: dict[str, tuple[str, str]] = {
    "沪深300": ("csindex", "000300"),
    "沪深 300": ("csindex", "000300"),
    "中证1000": ("csindex", "000852"),
    "中证 1000": ("csindex", "000852"),
    "科创50": ("csindex", "000688"),
    "科创 50": ("csindex", "000688"),
    "创业板指": ("generic", "399006"),
    "创业板核心指数": ("generic", "399006"),
}

CN_ETF_SOURCES: dict[str, str] = {
    "通信 ETF": "515880",
    "通信ETF": "515880",
    "人工智能 ETF": "159819",
    "人工智能ETF": "159819",
    "创新药 ETF": "159992",
    "创新药ETF": "159992",
    "生物医药 ETF": "512290",
    "生物医药ETF": "512290",
}


def format_cn_stock_symbol(code: str, exchange: str | None = None) -> str:
    normalized = str(code).strip().zfill(6)
    if exchange and "上海" in exchange:
        return f"{normalized}.SH"
    if exchange and "深圳" in exchange:
        return f"{normalized}.SZ"
    if exchange and "北京" in exchange:
        return f"{normalized}.BJ"
    if normalized.startswith(("60", "68", "90")):
        return f"{normalized}.SH"
    if normalized.startswith(("43", "83", "87", "92")):
        return f"{normalized}.BJ"
    return f"{normalized}.SZ"


def normalize_cn_index_constituents(raw: pd.DataFrame) -> list[str]:
    if "成分券代码" in raw.columns:
        exchange_values = raw["交易所"] if "交易所" in raw.columns else [None] * len(raw)
        return sorted(
            {
                format_cn_stock_symbol(code, exchange)
                for code, exchange in zip(raw["成分券代码"], exchange_values, strict=False)
            }
        )
    if "品种代码" in raw.columns:
        return sorted({format_cn_stock_symbol(code) for code in raw["品种代码"]})
    if "代码" in raw.columns:
        return sorted({format_cn_stock_symbol(code) for code in raw["代码"]})
    raise ValueError(f"Unsupported CN index constituent columns: {list(raw.columns)}")


def resolve_cn_index_constituents(
    index_name: str,
    *,
    csindex_fetcher: CnIndexFetcher = ak.index_stock_cons_csindex,
    generic_fetcher: CnIndexFetcher = ak.index_stock_cons,
) -> list[str]:
    source = CN_INDEX_SOURCES.get(index_name)
    if source is None:
        return []
    provider, symbol = source
    raw = csindex_fetcher(symbol) if provider == "csindex" else generic_fetcher(symbol)
    return normalize_cn_index_constituents(raw)


def resolve_cn_etf_constituents(
    etf_name: str,
    top_n: int = 10,
    *,
    fetcher: Callable[[str], pd.DataFrame] = ak.fund_portfolio_hold_em,
) -> list[str]:
    """解析 A 股 ETF 重仓股。"""
    symbol = CN_ETF_SOURCES.get(etf_name)
    if not symbol:
        return []

    try:
        raw = fetcher(symbol=symbol)
        if raw.empty or "季度" not in raw.columns:
            return []
        latest_quarter = raw["季度"].iloc[0]
        latest_holdings = raw[raw["季度"] == latest_quarter]
        codes = latest_holdings.head(top_n)["股票代码"].tolist()
        return sorted({format_cn_stock_symbol(str(c)) for c in codes})
    except Exception:
        return []


def merge_symbols_preserving_order(*symbol_groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for symbols in symbol_groups:
        for symbol in symbols:
            if symbol not in seen:
                seen.add(symbol)
                merged.append(symbol)
    return merged


def load_theme_seed_symbols(themes_path: str | Path) -> dict[str, list[str]]:
    return build_seed_symbols(load_themes(themes_path))


def load_theme_seed_sources(themes_path: str | Path) -> dict[str, dict[str, list[str]]]:
    return collect_seed_sources(load_themes(themes_path))


def load_resolved_theme_seed_symbols(
    themes_path: str | Path,
    *,
    cn_index_resolver: CnIndexResolver = resolve_cn_index_constituents,
    cn_etf_resolver: CnEtfResolver = resolve_cn_etf_constituents,
) -> dict[str, list[str]]:
    sources_by_market = load_theme_seed_sources(themes_path)
    resolved: dict[str, list[str]] = {}

    for market, sources in sources_by_market.items():
        symbols = list(sources["symbols"])
        if market == "cn":
            index_symbols: list[str] = []
            for index_name in sources["indexes"]:
                index_symbols.extend(cn_index_resolver(index_name))
            etf_symbols: list[str] = []
            for etf_name in sources["etfs"]:
                etf_symbols.extend(cn_etf_resolver(etf_name))
            symbols = merge_symbols_preserving_order(symbols, sorted(index_symbols), sorted(etf_symbols))
        if symbols:
            resolved[market] = symbols

    return resolved


def load_market_profiles(markets_path: str | Path) -> dict[str, Any]:
    return load_markets(markets_path)
