from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ALERT_TYPES = (
    "abnormal_volume",
    "peak_drawdown",
    "chronic_underperformance",
)
SUPPORTED_WATCHLIST_MARKETS = {"cn", "hk", "us"}


@dataclass(frozen=True)
class WatchlistRules:
    enabled_alerts: tuple[str, ...]
    volume_ratio: float
    price_change: float
    drawdown: float
    underperformance_60d: float
    cooldown_trading_days: int
    worsening_step: float


@dataclass(frozen=True)
class WatchlistItemConfig:
    symbol: str
    market: str
    name: str
    rules: WatchlistRules


@dataclass(frozen=True)
class WatchlistConfig:
    items: tuple[WatchlistItemConfig, ...]


_WATCHLIST_DEFAULTS = {
    "enabled_alerts": list(ALERT_TYPES),
    "volume_ratio": 3.0,
    "price_change": {"cn": 0.05, "hk": 0.05, "us": 0.10},
    "drawdown": 0.20,
    "underperformance_60d": 0.15,
    "cooldown_trading_days": 20,
    "worsening_step": 0.10,
}


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def load_themes(path: str | Path) -> list[dict[str, Any]]:
    data = load_yaml(path)
    themes = data.get("themes", [])
    if not isinstance(themes, list) or not themes:
        raise ValueError("themes.yaml must contain a non-empty themes list")
    return themes


def load_markets(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)


def load_scoring(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)


def _ratio(value: Any, field: str) -> float:
    result = float(value)
    if not 0 < result <= 1:
        raise ValueError(f"{field} must be within (0, 1]")
    return result


def load_watchlist(path: str | Path) -> WatchlistConfig:
    data = load_yaml(path)
    raw_defaults = {**_WATCHLIST_DEFAULTS, **dict(data.get("defaults") or {})}
    price_changes = {
        **_WATCHLIST_DEFAULTS["price_change"],
        **dict(raw_defaults.get("price_change") or {}),
    }
    raw_items = data.get("watchlist")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("watchlist.yaml must contain a non-empty watchlist")

    seen: set[str] = set()
    items: list[WatchlistItemConfig] = []
    for raw_item in raw_items:
        market = str(raw_item.get("market", "")).strip().lower()
        if market not in SUPPORTED_WATCHLIST_MARKETS:
            raise ValueError(f"unsupported watchlist market: {market}")
        symbol = str(raw_item.get("symbol", "")).strip().upper()
        if not symbol:
            raise ValueError("watchlist symbol is required")
        if symbol in seen:
            raise ValueError(f"duplicate watchlist symbol: {symbol}")
        seen.add(symbol)

        overrides = dict(raw_item.get("overrides") or {})
        enabled = tuple(overrides.get("enabled_alerts", raw_defaults["enabled_alerts"]))
        unknown = set(enabled) - set(ALERT_TYPES)
        if unknown:
            raise ValueError(f"unknown alert type: {sorted(unknown)[0]}")
        volume_ratio = float(overrides.get("volume_ratio", raw_defaults["volume_ratio"]))
        if volume_ratio <= 0:
            raise ValueError("volume_ratio must be positive")
        cooldown = int(
            overrides.get("cooldown_trading_days", raw_defaults["cooldown_trading_days"])
        )
        if cooldown <= 0:
            raise ValueError("cooldown_trading_days must be positive")

        rules = WatchlistRules(
            enabled_alerts=enabled,
            volume_ratio=volume_ratio,
            price_change=_ratio(
                overrides.get("price_change", price_changes[market]),
                "price_change",
            ),
            drawdown=_ratio(overrides.get("drawdown", raw_defaults["drawdown"]), "drawdown"),
            underperformance_60d=_ratio(
                overrides.get(
                    "underperformance_60d",
                    raw_defaults["underperformance_60d"],
                ),
                "underperformance_60d",
            ),
            cooldown_trading_days=cooldown,
            worsening_step=_ratio(
                overrides.get("worsening_step", raw_defaults["worsening_step"]),
                "worsening_step",
            ),
        )
        items.append(
            WatchlistItemConfig(
                symbol=symbol,
                market=market,
                name=str(raw_item.get("name") or symbol).strip(),
                rules=rules,
            )
        )
    return WatchlistConfig(items=tuple(items))
