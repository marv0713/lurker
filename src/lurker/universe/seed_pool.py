from collections import defaultdict
from typing import Any


def collect_seed_sources(themes: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    sources_by_market: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"symbols": set(), "indexes": set(), "etfs": set()}
    )
    for theme in themes:
        for market, market_config in theme.get("markets", {}).items():
            sources_by_market[market]["symbols"].update(market_config.get("seed_symbols", []))
            sources_by_market[market]["indexes"].update(market_config.get("seed_indexes", []))
            sources_by_market[market]["etfs"].update(market_config.get("seed_etfs", []))

    return {
        market: {
            "symbols": sorted(sources["symbols"]),
            "indexes": sorted(sources["indexes"]),
            "etfs": sorted(sources["etfs"]),
        }
        for market, sources in sources_by_market.items()
    }


def build_seed_symbols(themes: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        market: sources["symbols"]
        for market, sources in collect_seed_sources(themes).items()
        if sources["symbols"]
    }
