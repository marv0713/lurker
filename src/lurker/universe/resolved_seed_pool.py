import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lurker.ingest.constituents import (
    CnIndexResolver,
    CnSymbolNameResolver,
    load_theme_seed_sources,
    resolve_cn_index_constituents,
    resolve_cn_etf_constituents,
    resolve_cn_symbol_names,
)

CnEtfResolver = Any # Can type properly if needed


ResolvedSeedPool = dict[str, Any]


def merge_symbols(*symbol_groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for symbols in symbol_groups:
        for symbol in symbols:
            if symbol not in seen:
                seen.add(symbol)
                merged.append(symbol)
    return merged


def build_resolved_seed_pool(
    themes_path: str | Path,
    *,
    generated_at: str | None = None,
    cn_index_resolver: CnIndexResolver = resolve_cn_index_constituents,
    cn_etf_resolver: Any = resolve_cn_etf_constituents,
    cn_symbol_name_resolver: CnSymbolNameResolver = resolve_cn_symbol_names,
) -> ResolvedSeedPool:
    from lurker.config import load_themes
    themes = load_themes(themes_path)

    sources_by_market = load_theme_seed_sources(themes_path)
    markets: dict[str, Any] = {}

    # Global theme mapping: symbol -> list[theme_id]
    theme_mapping: dict[str, list[str]] = {}

    for market, sources in sources_by_market.items():
        manual_symbols = list(sources["symbols"])
        index_sources: dict[str, list[str]] = {}
        etf_sources: dict[str, list[str]] = {}
        unresolved: list[dict[str, str]] = []

        if market == "cn":
            for index_name in sources["indexes"]:
                index_sources[index_name] = cn_index_resolver(index_name)
            for etf_name in sources["etfs"]:
                resolved_holdings = cn_etf_resolver(etf_name)
                if resolved_holdings:
                    etf_sources[etf_name] = resolved_holdings
                else:
                    unresolved.append({"type": "etf", "name": etf_name})
        else:
            unresolved.extend({"type": "etf", "name": etf_name} for etf_name in sources["etfs"])

        index_symbols = [symbol for symbols in index_sources.values() for symbol in symbols]
        etf_symbols = [symbol for symbols in etf_sources.values() for symbol in symbols]
        symbols = merge_symbols(manual_symbols, sorted(index_symbols), sorted(etf_symbols))
        if not symbols and not unresolved:
            continue

        markets[market] = {
            "symbols": symbols,
            "sources": {
                "manual": manual_symbols,
                "indexes": index_sources,
                "etfs": etf_sources,
                "unresolved": unresolved,
            },
        }

    # Build theme_mapping
    for theme in themes:
        theme_id = theme["id"]
        for market, market_config in theme.get("markets", {}).items():
            for symbol in market_config.get("seed_symbols", []):
                theme_mapping.setdefault(symbol, []).append(theme_id)
            for index_name in market_config.get("seed_indexes", []):
                # Lookup the resolved index constituents
                if market in markets and "indexes" in markets[market]["sources"]:
                    for symbol in markets[market]["sources"]["indexes"].get(index_name, []):
                        theme_mapping.setdefault(symbol, []).append(theme_id)
            for etf_name in market_config.get("seed_etfs", []):
                # Lookup the resolved ETF constituents
                if market in markets and "etfs" in markets[market]["sources"]:
                    for symbol in markets[market]["sources"]["etfs"].get(etf_name, []):
                        theme_mapping.setdefault(symbol, []).append(theme_id)

    # Deduplicate theme mappings
    for symbol in theme_mapping:
        theme_mapping[symbol] = sorted(list(set(theme_mapping[symbol])))

    cn_symbols = markets.get("cn", {}).get("symbols", [])
    symbol_names = cn_symbol_name_resolver(cn_symbols) if cn_symbols else {}

    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "markets": markets,
        "theme_mapping": theme_mapping,
        "symbol_names": symbol_names,
    }


def save_resolved_seed_pool(pool: ResolvedSeedPool, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(pool, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_resolved_seed_pool(path: str | Path) -> ResolvedSeedPool:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_seed_symbols(pool: ResolvedSeedPool) -> dict[str, list[str]]:
    return {
        market: list(market_pool.get("symbols", []))
        for market, market_pool in pool.get("markets", {}).items()
        if market_pool.get("symbols")
    }
