from collections.abc import Callable, Iterable
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from lurker.ingest.prices import fetch_cn_prices, fetch_yfinance_prices
from lurker.signals.stock_strength import calculate_returns


PriceFetcher = Callable[[str, str], pd.DataFrame]
MarketFetchers = dict[str, PriceFetcher]
PriceSnapshotBatch = dict[str, Any]


class PriceSnapshotStore(Protocol):
    def save(self, snapshot: PriceSnapshotBatch, snapshot_date: str) -> Path: ...

    def load_latest(self) -> PriceSnapshotBatch | None: ...

DEFAULT_FETCHERS: MarketFetchers = {
    "cn": fetch_cn_prices,
    "us": fetch_yfinance_prices,
    "hk": fetch_yfinance_prices,
}


def fetch_prices_for_market(
    symbol: str,
    market: str,
    period: str,
    fetchers: MarketFetchers | None = None,
) -> pd.DataFrame:
    market_fetchers = fetchers or DEFAULT_FETCHERS
    return market_fetchers[market](symbol, period)


def collect_price_snapshots(
    *,
    seed_symbols: dict[str, list[str]],
    markets: Iterable[str],
    windows: Iterable[int],
    period: str,
    fetcher: PriceFetcher | None = None,
    fetchers: MarketFetchers | None = None,
    limit_per_market: int | None = None,
) -> list[dict[str, float | str]]:
    return collect_price_snapshot_batch(
        seed_symbols=seed_symbols,
        markets=markets,
        windows=windows,
        period=period,
        fetcher=fetcher,
        fetchers=fetchers,
        limit_per_market=limit_per_market,
    )["snapshots"]


def collect_price_snapshot_batch(
    *,
    seed_symbols: dict[str, list[str]],
    markets: Iterable[str],
    windows: Iterable[int],
    period: str,
    fetcher: PriceFetcher | None = None,
    fetchers: MarketFetchers | None = None,
    limit_per_market: int | None = None,
    generated_at: str | None = None,
    seed_pool_generated_at: str | None = None,
) -> PriceSnapshotBatch:
    snapshots: list[dict[str, float | str]] = []
    failures: list[dict[str, str]] = []
    window_list = list(windows)
    market_list = list(markets)

    for market in market_list:
        symbols = seed_symbols.get(market, [])
        if limit_per_market is not None:
            symbols = symbols[:limit_per_market]
        for symbol in symbols:
            try:
                if fetcher is None:
                    prices = fetch_prices_for_market(symbol, market, period, fetchers=fetchers)
                else:
                    prices = fetcher(symbol, period)
            except Exception as exc:
                failures.append(
                    {
                        "symbol": symbol,
                        "market": market,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            if prices.empty:
                failures.append({"symbol": symbol, "market": market, "reason": "empty price data"})
                continue
            closes = prices["adj_close"].dropna()
            if closes.empty:
                failures.append({"symbol": symbol, "market": market, "reason": "empty close data"})
                continue
            returns = calculate_returns(closes, window_list)
            snapshots.append(
                {
                    "symbol": symbol,
                    "market": market,
                    "latest_close": float(closes.iloc[-1]),
                    **returns,
                }
            )

    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "seed_pool_generated_at": seed_pool_generated_at,
        "markets": market_list,
        "windows": window_list,
        "snapshots": snapshots,
        "failures": failures,
    }


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def render_price_snapshot(snapshots: list[dict[str, float | str]], windows: Iterable[int]) -> str:
    window_list = list(windows)
    return_headers = " | ".join(f"{window}D" for window in window_list)
    headers = f"| Symbol | Market | Close | {return_headers} |"
    separators = "|---|---|---:|" + "---:|" * len(window_list)

    rows = []
    for snapshot in snapshots:
        return_cells = " | ".join(
            format_percent(snapshot.get(f"return_{window}d")) for window in window_list
        )
        rows.append(
            f"| {snapshot['symbol']} | {snapshot['market']} | "
            f"{float(snapshot['latest_close']):.2f} | {return_cells} |"
        )

    if not rows:
        rows.append(f"| No available data | - | - | {' | '.join('-' for _ in window_list)} |")

    return "\n".join([headers, separators, *rows])


def save_price_snapshot_file(snapshot: PriceSnapshotBatch, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_price_snapshot_file(path: str | Path) -> PriceSnapshotBatch:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def find_latest_price_snapshot(directory: str | Path) -> Path | None:
    snapshot_dir = Path(directory)
    if not snapshot_dir.exists():
        return None
    paths = sorted(path for path in snapshot_dir.glob("*.json") if path.name != "latest.json")
    return paths[-1] if paths else None


def select_price_snapshot_rows(
    snapshot: PriceSnapshotBatch,
    *,
    markets: Iterable[str],
) -> list[dict[str, float | str]]:
    market_set = set(markets)
    return [row for row in snapshot.get("snapshots", []) if row.get("market") in market_set]


class FilePriceSnapshotStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def save(self, snapshot: PriceSnapshotBatch, snapshot_date: str) -> Path:
        output_path = self.directory / f"{snapshot_date}.json"
        save_price_snapshot_file(snapshot, output_path)
        return output_path

    def load_latest(self) -> PriceSnapshotBatch | None:
        latest_path = find_latest_price_snapshot(self.directory)
        if latest_path is None:
            return None
        return load_price_snapshot_file(latest_path)
