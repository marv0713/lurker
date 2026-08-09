from dataclasses import dataclass
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ALERT_TYPES = (
    "abnormal_volume",
    "peak_drawdown",
    "chronic_underperformance",
)
SUPPORTED_WATCHLIST_MARKETS = {"cn", "hk", "us"}


@dataclass(frozen=True)
class MonthlyMacroConfig:
    credit_table_urls: dict[int, str]
    allowed_hosts: tuple[str, ...]
    timeout_seconds: int
    max_response_bytes: int
    household_deposit_yoy_pct: float
    leverage_ratio_pct: float
    financing_monthly_growth_pct: float
    macro_max_lag_months: int
    leverage_max_lag_trading_days: int


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


@dataclass(frozen=True)
class PersonalStockConfig:
    symbol: str
    market: str
    name: str


@dataclass(frozen=True)
class HkExperimentalSpringConfig:
    min_avg_turnover_hkd_20d: float = 10_000_000.0
    min_positive_volume_ratio_60d: float = 0.95


@dataclass(frozen=True)
class PersonalWatchConfig:
    holdings: tuple[PersonalStockConfig, ...]
    watchlist: tuple[PersonalStockConfig, ...]
    hk_experimental_spring: HkExperimentalSpringConfig


_WATCHLIST_DEFAULTS = {
    "enabled_alerts": list(ALERT_TYPES),
    "volume_ratio": 3.0,
    "price_change": {"cn": 0.05, "hk": 0.05, "us": 0.10},
    "drawdown": 0.20,
    "underperformance_60d": 0.15,
    "cooldown_trading_days": 20,
    "worsening_step": 0.10,
}
_WATCHLIST_TOP_LEVEL_FIELDS = {"defaults", "watchlist"}
_WATCHLIST_ITEM_FIELDS = {"symbol", "market", "name", "overrides"}
_WATCHLIST_RULE_FIELDS = set(_WATCHLIST_DEFAULTS)
_PERSONAL_TOP_LEVEL_FIELDS = {"defaults", "holdings", "watchlist"}
_PERSONAL_DEFAULT_FIELDS = {"hk_experimental_spring"}
_PERSONAL_HK_SPRING_FIELDS = {
    "min_avg_turnover_hkd_20d",
    "min_positive_volume_ratio_60d",
}
_PERSONAL_STOCK_FIELDS = {"symbol", "market", "name"}
_PERSONAL_MARKETS = {"cn", "hk"}


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


def _ratio(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be within (0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 1:
        raise ValueError(f"{field} must be within (0, 1]")
    return result


def _positive_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite and positive")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return result


def _non_negative_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite and non-negative")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite and non-negative") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return result


def _integer(value: Any, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a mapping")
    return value


def _reject_unknown_fields(
    mapping: dict[str, Any],
    allowed: set[str],
    context: str,
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"unknown {context} field: {unknown[0]}")


def load_monthly_macro_config(path: str | Path) -> MonthlyMacroConfig:
    data = load_yaml(path)
    _reject_unknown_fields(
        data,
        {"schema_version", "pboc", "thresholds", "freshness"},
        "monthly macro top-level",
    )
    if data.get("schema_version") != 1:
        raise ValueError("macro_monthly schema_version must equal 1")

    pboc = _mapping(data.get("pboc"), "monthly macro pboc")
    _reject_unknown_fields(
        pboc,
        {
            "credit_table_urls",
            "allowed_hosts",
            "timeout_seconds",
            "max_response_bytes",
        },
        "monthly macro pboc",
    )
    hosts = pboc.get("allowed_hosts")
    if not isinstance(hosts, list) or not hosts:
        raise ValueError("allowed_hosts must be a non-empty list")
    allowed_hosts = tuple(str(host).strip().lower() for host in hosts)
    if any(not host or "/" in host for host in allowed_hosts):
        raise ValueError("allowed_hosts contains an invalid host")

    raw_urls = _mapping(pboc.get("credit_table_urls"), "credit_table_urls")
    if not raw_urls:
        raise ValueError("credit_table_urls must be non-empty")
    urls: dict[int, str] = {}
    for raw_year, raw_url in raw_urls.items():
        year_text = str(raw_year)
        if len(year_text) != 4 or not year_text.isdigit():
            raise ValueError("credit_table_urls key must be a four-digit year")
        url = str(raw_url).strip()
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("credit table URL must use https")
        if parsed.hostname not in allowed_hosts:
            raise ValueError("credit table URL host is not in allowed_hosts")
        urls[int(year_text)] = url

    thresholds = _mapping(data.get("thresholds"), "monthly macro thresholds")
    _reject_unknown_fields(
        thresholds,
        {
            "household_deposit_yoy_pct",
            "leverage_ratio_pct",
            "financing_monthly_growth_pct",
        },
        "monthly macro threshold",
    )
    freshness = _mapping(data.get("freshness"), "monthly macro freshness")
    _reject_unknown_fields(
        freshness,
        {"macro_max_lag_months", "leverage_max_lag_trading_days"},
        "monthly macro freshness",
    )
    return MonthlyMacroConfig(
        credit_table_urls=urls,
        allowed_hosts=allowed_hosts,
        timeout_seconds=_integer(pboc.get("timeout_seconds"), "timeout_seconds", minimum=1),
        max_response_bytes=_integer(
            pboc.get("max_response_bytes"),
            "max_response_bytes",
            minimum=1,
        ),
        household_deposit_yoy_pct=_non_negative_float(
            thresholds.get("household_deposit_yoy_pct"),
            "household_deposit_yoy_pct",
        ),
        leverage_ratio_pct=_non_negative_float(
            thresholds.get("leverage_ratio_pct"),
            "leverage_ratio_pct",
        ),
        financing_monthly_growth_pct=_non_negative_float(
            thresholds.get("financing_monthly_growth_pct"),
            "financing_monthly_growth_pct",
        ),
        macro_max_lag_months=_integer(
            freshness.get("macro_max_lag_months"),
            "macro_max_lag_months",
            minimum=0,
        ),
        leverage_max_lag_trading_days=_integer(
            freshness.get("leverage_max_lag_trading_days"),
            "leverage_max_lag_trading_days",
            minimum=0,
        ),
    )


_STOCK_WEIGHT_KEYS = {
    "return_20d",
    "return_60d",
    "return_180d",
    "double_bagger",
}
_SECTOR_WEIGHT_KEYS = {
    "sector_strength",
    "strong_stock_count",
    "cross_market_mapping",
}


def _validate_weight_mapping(
    values: Any,
    allowed: set[str],
    context: str,
) -> None:
    mapping = _mapping(values, context)
    if "return_120_180d" in mapping:
        raise ValueError("unsupported scoring weight return_120_180d; use return_180d")
    _reject_unknown_fields(mapping, allowed, context)
    for key, value in mapping.items():
        if isinstance(value, bool):
            raise ValueError(f"{context}.{key} must be finite and non-negative")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context}.{key} must be finite and non-negative") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{context}.{key} must be finite and non-negative")


def load_scoring(path: str | Path) -> dict[str, Any]:
    data = load_yaml(path)
    _reject_unknown_fields(
        data,
        {"stock_signal", "sector_signal", "candidate_weights"},
        "scoring top-level",
    )
    stock = _mapping(data.get("stock_signal"), "stock_signal")
    sector = _mapping(data.get("sector_signal"), "sector_signal")
    _validate_weight_mapping(
        stock.get("weights"),
        _STOCK_WEIGHT_KEYS,
        "stock_signal.weights",
    )
    _validate_weight_mapping(
        sector.get("weights"),
        _SECTOR_WEIGHT_KEYS,
        "sector_signal.weights",
    )
    return data


def load_watchlist(path: str | Path) -> WatchlistConfig:
    data = load_yaml(path)
    _reject_unknown_fields(data, _WATCHLIST_TOP_LEVEL_FIELDS, "watchlist top-level")
    configured_defaults = _mapping(data.get("defaults"), "watchlist defaults")
    _reject_unknown_fields(configured_defaults, _WATCHLIST_RULE_FIELDS, "watchlist default")
    raw_defaults = {**_WATCHLIST_DEFAULTS, **configured_defaults}
    configured_price_changes = _mapping(
        configured_defaults.get("price_change"),
        "watchlist price_change",
    )
    unknown_price_change_markets = sorted(
        set(configured_price_changes) - SUPPORTED_WATCHLIST_MARKETS
    )
    if unknown_price_change_markets:
        raise ValueError(f"unknown price_change market: {unknown_price_change_markets[0]}")
    merged_price_changes = {
        **_WATCHLIST_DEFAULTS["price_change"],
        **configured_price_changes,
    }
    price_changes = {
        market: _ratio(value, f"price_change.{market}")
        for market, value in merged_price_changes.items()
    }
    raw_items = data.get("watchlist")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("watchlist.yaml must contain a non-empty watchlist")

    seen: set[str] = set()
    items: list[WatchlistItemConfig] = []
    for raw_item in raw_items:
        raw_item = _mapping(raw_item, "watchlist item")
        _reject_unknown_fields(raw_item, _WATCHLIST_ITEM_FIELDS, "watchlist item")
        market = str(raw_item.get("market", "")).strip().lower()
        if market not in SUPPORTED_WATCHLIST_MARKETS:
            raise ValueError(f"unsupported watchlist market: {market}")
        symbol = str(raw_item.get("symbol", "")).strip().upper()
        if not symbol:
            raise ValueError("watchlist symbol is required")
        if symbol in seen:
            raise ValueError(f"duplicate watchlist symbol: {symbol}")
        seen.add(symbol)

        overrides = _mapping(raw_item.get("overrides"), "watchlist overrides")
        _reject_unknown_fields(overrides, _WATCHLIST_RULE_FIELDS, "watchlist override")
        enabled = tuple(overrides.get("enabled_alerts", raw_defaults["enabled_alerts"]))
        unknown = set(enabled) - set(ALERT_TYPES)
        if unknown:
            raise ValueError(f"unknown alert type: {sorted(unknown)[0]}")
        volume_ratio = _positive_float(
            overrides.get("volume_ratio", raw_defaults["volume_ratio"]),
            "volume_ratio",
        )
        cooldown = overrides.get(
            "cooldown_trading_days",
            raw_defaults["cooldown_trading_days"],
        )
        if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown <= 0:
            raise ValueError("cooldown_trading_days must be a positive integer")

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


def load_personal_watch(path: str | Path) -> PersonalWatchConfig:
    data = load_yaml(path)
    _reject_unknown_fields(data, _PERSONAL_TOP_LEVEL_FIELDS, "personal top-level")

    defaults = _mapping(data.get("defaults"), "personal defaults")
    _reject_unknown_fields(defaults, _PERSONAL_DEFAULT_FIELDS, "personal default")
    hk_defaults = _mapping(
        defaults.get("hk_experimental_spring"),
        "personal hk_experimental_spring",
    )
    _reject_unknown_fields(
        hk_defaults,
        _PERSONAL_HK_SPRING_FIELDS,
        "personal hk_experimental_spring",
    )
    hk_config = HkExperimentalSpringConfig(
        min_avg_turnover_hkd_20d=_positive_float(
            hk_defaults.get("min_avg_turnover_hkd_20d", 10_000_000.0),
            "min_avg_turnover_hkd_20d",
        ),
        min_positive_volume_ratio_60d=_ratio(
            hk_defaults.get("min_positive_volume_ratio_60d", 0.95),
            "min_positive_volume_ratio_60d",
        ),
    )

    seen: set[str] = set()

    def load_group(name: str) -> tuple[PersonalStockConfig, ...]:
        raw_items = data.get(name, [])
        if not isinstance(raw_items, list):
            raise ValueError(f"personal {name} must be a list")
        items: list[PersonalStockConfig] = []
        for value in raw_items:
            raw_item = _mapping(value, "personal stock")
            _reject_unknown_fields(
                raw_item,
                _PERSONAL_STOCK_FIELDS,
                "personal stock",
            )
            symbol = str(raw_item.get("symbol", "")).strip().upper()
            if not symbol:
                raise ValueError("personal stock symbol is required")
            if symbol in seen:
                raise ValueError(f"duplicate personal stock symbol: {symbol}")
            market = str(raw_item.get("market", "")).strip().lower()
            if market not in _PERSONAL_MARKETS:
                raise ValueError(f"unsupported personal stock market: {market}")
            pattern = r"\d{6}\.(?:SZ|SH|BJ)" if market == "cn" else r"\d{1,5}\.HK"
            if re.fullmatch(pattern, symbol) is None:
                raise ValueError(f"invalid personal stock symbol for market {market}: {symbol}")
            stock_name = str(raw_item.get("name", "")).strip()
            if not stock_name:
                raise ValueError("personal stock name is required")
            seen.add(symbol)
            items.append(
                PersonalStockConfig(
                    symbol=symbol,
                    market=market,
                    name=stock_name,
                )
            )
        return tuple(items)

    holdings = load_group("holdings")
    watchlist = load_group("watchlist")
    if not holdings and not watchlist:
        raise ValueError("personal watch must contain at least one stock")
    return PersonalWatchConfig(
        holdings=holdings,
        watchlist=watchlist,
        hk_experimental_spring=hk_config,
    )


def load_core_etfs(path: str | Path) -> list[dict[str, str]]:
    """Load core ETF configuration from YAML with strict validation.

    Validates:
    - Four required roles present and unique
    - canonical_symbol has valid .SH/.SZ suffix
    - No unknown top-level keys in config
    - No duplicate symbols or canonical_symbols
    """
    data = load_yaml(path)
    unknown = set(data) - {"etfs"}
    if unknown:
        raise ValueError(f"Unknown keys in core_etfs.yaml: {sorted(unknown)}")

    etfs = data.get("etfs", [])
    if not isinstance(etfs, list) or not etfs:
        raise ValueError("core_etfs.yaml must contain a non-empty 'etfs' list")
    if len(etfs) != 4:
        raise ValueError(
            f"core_etfs.yaml must contain exactly 4 ETFs (沪深300, 中证500, 创业板, A500), got {len(etfs)}"
        )

    result = []
    seen_symbols = set()
    seen_canonical = set()
    allowed_roles = {"csi300", "csi500", "chinext", "csi_a500"}

    for i, entry in enumerate(etfs):
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid ETF entry at index {i}: {entry}")
        unknown_fields = set(entry) - {"symbol", "canonical_symbol", "name", "market", "role"}
        if unknown_fields:
            raise ValueError(f"Unknown field in ETF entry {i}: {sorted(unknown_fields)[0]}")

        symbol = str(entry.get("symbol", "")).strip()
        canonical = str(entry.get("canonical_symbol", symbol)).strip()
        name = str(entry.get("name", "")).strip()
        role = str(entry.get("role", "")).strip()

        if not symbol:
            raise ValueError(f"ETF entry {i} missing 'symbol'")
        if symbol in seen_symbols:
            raise ValueError(f"Duplicate symbol in core_etfs.yaml: {symbol}")
        seen_symbols.add(symbol)

        if not canonical.endswith((".SH", ".SZ")):
            raise ValueError(f"canonical_symbol '{canonical}' must end with .SH or .SZ")
        if canonical in seen_canonical:
            raise ValueError(f"Duplicate canonical_symbol in core_etfs.yaml: {canonical}")
        seen_canonical.add(canonical)

        if role and role not in allowed_roles:
            raise ValueError(
                f"Unknown role '{role}' in ETF entry {i}. Allowed: {sorted(allowed_roles)}"
            )

        result.append(
            {
                "symbol": symbol,
                "canonical_symbol": canonical,
                "name": name,
                "market": str(entry.get("market", "cn")).strip(),
                "role": role,
            }
        )

    # All four roles must be present and unique
    roles = [r["role"] for r in result if r["role"]]
    if len(roles) != 4 or len(set(roles)) != 4:
        raise ValueError(
            "Each ETF must have a unique role. Required: csi300, csi500, chinext, csi_a500"
        )

    return result
