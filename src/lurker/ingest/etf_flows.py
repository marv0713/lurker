"""Core ETF flow ingestion — CoreEtfBatch dataclasses with strict validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta, timezone
import math
from typing import Any

import pandas as pd
import requests


# ---------------------------------------------------------------------------
# Supported schema version
# ---------------------------------------------------------------------------

_CURRENT_SCHEMA_VERSION = 1
_ETF_ACTIVE_THRESHOLD = 1.2
_MARKET_CLOSE_CUTOFF = time(15, 30)
_SHANGHAI_TZ = timezone(timedelta(hours=8))

# Allowed keys per level for strict deserialization
_BATCH_KEYS = {"configured_symbols", "items", "failures", "generated_at", "schema_version"}
_ITEM_KEYS = {
    "symbol", "name", "trade_date", "current_turnover", "avg_turnover_20d",
    "turnover_expansion", "shares", "shares_date", "status", "source",
    "availability", "error",
}
_REQUIRED_ITEM_KEYS = {"symbol", "trade_date", "current_turnover", "status"}
_VALID_STATUSES = {"active", "inactive", "unknown"}
_VALID_AVAILABILITIES = {
    "turnover_only",
    "insufficient_history",
    "intraday_partial",
    "invalid_average",
    "stale",
    "unknown",
}


def _finite_number(
    value: Any,
    *,
    field_name: str,
    allow_none: bool = False,
) -> float | None:
    if value is None and allow_none:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    if result < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return result


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CoreEtfItem:
    """Single ETF data point."""

    symbol: str
    name: str
    trade_date: str
    current_turnover: float
    avg_turnover_20d: float | None
    turnover_expansion: float | None
    shares: float | None
    shares_date: str | None
    status: str  # "active" | "inactive" | "unknown"
    source: str
    availability: str  # "turnover_only" | "insufficient_history" | "intraday_partial" | "invalid_average" | "stale"
    error: str | None


@dataclass
class CoreEtfBatch:
    """Collected ETF batch with strict validation."""

    configured_symbols: list[str]
    items: list[CoreEtfItem] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    generated_at: str = ""
    schema_version: int = _CURRENT_SCHEMA_VERSION

    def is_complete(self) -> bool:
        """Verify every configured symbol appears exactly once across items + failures.

        Rejects:
        - Symbols missing from both items and failures
        - Duplicate symbols in items
        - Duplicate symbols in failures
        - Symbols appearing in both items and failures
        """
        configured = self.configured_symbols
        if not configured:
            return False
        if len(configured) != len(set(configured)):
            return False

        item_symbols = [item.symbol for item in self.items]
        failure_symbols = [f["symbol"] for f in self.failures]

        # Duplicate detection
        if len(item_symbols) != len(set(item_symbols)):
            return False
        if len(failure_symbols) != len(set(failure_symbols)):
            return False

        item_set = set(item_symbols)
        failure_set = set(failure_symbols)

        # No overlap between items and failures
        if item_set & failure_set:
            return False

        # All configured symbols accounted for
        return set(configured) == (item_set | failure_set)

    @classmethod
    def from_dict(cls, data: dict) -> "CoreEtfBatch":
        """Deserialize with strict validation.

        Rejects:
        - Unknown top-level keys
        - Unsupported schema_version
        - Missing configured_symbols
        - Corrupted items (missing required keys, unknown keys, duplicate symbols)
        """
        if not isinstance(data, dict):
            raise ValueError("CoreEtfBatch data must be a dict")

        # Schema version check
        version = data.get("schema_version", _CURRENT_SCHEMA_VERSION)
        if version != _CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported core_etfs schema_version {version} (expected {_CURRENT_SCHEMA_VERSION})"
            )

        # Unknown top-level keys
        unknown = set(data) - _BATCH_KEYS
        if unknown:
            raise ValueError(f"Unknown keys in CoreEtfBatch: {sorted(unknown)}")

        configured_symbols = data.get("configured_symbols", [])
        if not isinstance(configured_symbols, list) or not configured_symbols:
            raise ValueError("CoreEtfBatch missing configured_symbols")
        if any(not isinstance(symbol, str) or not symbol.strip() for symbol in configured_symbols):
            raise ValueError("CoreEtfBatch configured_symbols must contain non-empty strings")
        if len(configured_symbols) != len(set(configured_symbols)):
            raise ValueError("Duplicate symbol in CoreEtfBatch configured_symbols")

        # Deserialize items with per-item validation
        items_data = data.get("items", [])
        if not isinstance(items_data, list):
            raise ValueError("CoreEtfBatch items must be a list")
        items = []
        seen_item_symbols = set()
        for item in items_data:
            if not isinstance(item, dict):
                raise ValueError(f"CoreEtfBatch item must be a dict, got {type(item)}")
            # Unknown keys per item
            unknown_item = set(item) - _ITEM_KEYS
            if unknown_item:
                raise ValueError(
                    f"Unknown keys in CoreEtfItem[{item.get('symbol', '?')}]: {sorted(unknown_item)}"
                )
            # Required keys
            missing = _REQUIRED_ITEM_KEYS - set(item)
            if missing:
                raise ValueError(
                    f"Missing required keys in CoreEtfItem[{item.get('symbol', '?')}]: {sorted(missing)}"
                )
            # Duplicate symbol in items
            symbol = item["symbol"]
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("CoreEtfItem symbol must be a non-empty string")
            if symbol in seen_item_symbols:
                raise ValueError(f"Duplicate symbol in CoreEtfBatch items: {symbol}")
            seen_item_symbols.add(symbol)

            status = str(item["status"])
            if status not in _VALID_STATUSES:
                raise ValueError(f"Invalid CoreEtfItem status: {status}")
            availability = str(item.get("availability", "unknown"))
            if availability not in _VALID_AVAILABILITIES:
                raise ValueError(f"Invalid CoreEtfItem availability: {availability}")

            items.append(CoreEtfItem(
                symbol=symbol,
                name=str(item.get("name", "")),
                trade_date=str(item.get("trade_date", "")),
                current_turnover=_finite_number(
                    item["current_turnover"],
                    field_name=f"CoreEtfItem[{symbol}].current_turnover",
                ),
                avg_turnover_20d=_finite_number(
                    item.get("avg_turnover_20d"),
                    field_name=f"CoreEtfItem[{symbol}].avg_turnover_20d",
                    allow_none=True,
                ),
                turnover_expansion=_finite_number(
                    item.get("turnover_expansion"),
                    field_name=f"CoreEtfItem[{symbol}].turnover_expansion",
                    allow_none=True,
                ),
                shares=_finite_number(
                    item.get("shares"),
                    field_name=f"CoreEtfItem[{symbol}].shares",
                    allow_none=True,
                ),
                shares_date=item.get("shares_date"),
                status=status,
                source=str(item.get("source", "")),
                availability=availability,
                error=item.get("error"),
            ))

        # Deserialize failures with duplicate check
        failures_data = data.get("failures", [])
        if not isinstance(failures_data, list):
            raise ValueError("CoreEtfBatch failures must be a list")
        failures = []
        seen_failure_symbols = set()
        for f in failures_data:
            if not isinstance(f, dict):
                raise ValueError(f"CoreEtfBatch failure must be a dict, got {type(f)}")
            unknown_failure = set(f) - {"symbol", "reason"}
            if unknown_failure:
                raise ValueError(f"Unknown keys in CoreEtfBatch failure: {sorted(unknown_failure)}")
            symbol = str(f.get("symbol", ""))
            if not symbol:
                raise ValueError("CoreEtfBatch failure symbol must be non-empty")
            if symbol in seen_failure_symbols:
                raise ValueError(f"Duplicate symbol in CoreEtfBatch failures: {symbol}")
            seen_failure_symbols.add(symbol)
            failures.append({"symbol": symbol, "reason": str(f.get("reason", ""))})

        batch = cls(
            configured_symbols=list(configured_symbols),
            items=items,
            failures=failures,
            generated_at=str(data.get("generated_at", "")),
            schema_version=version,
        )
        if not batch.is_complete():
            raise ValueError("CoreEtfBatch is not complete or contains conflicting symbols")
        return batch

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "configured_symbols": self.configured_symbols,
            "items": [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "trade_date": item.trade_date,
                    "current_turnover": item.current_turnover,
                    "avg_turnover_20d": item.avg_turnover_20d,
                    "turnover_expansion": item.turnover_expansion,
                    "shares": item.shares,
                    "shares_date": item.shares_date,
                    "status": item.status,
                    "source": item.source,
                    "availability": item.availability,
                    "error": item.error,
                }
                for item in self.items
            ],
            "failures": self.failures,
            "generated_at": self.generated_at,
            "schema_version": self.schema_version,
        }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EtfProviderError(Exception):
    """AkShare ETF data source unavailable."""


class EtfSchemaError(Exception):
    """AkShare ETF response schema mismatch."""


def fetch_core_etfs(
    *,
    etf_configs: list[dict[str, str]] | None = None,
    hist_fetcher: Callable[..., pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> CoreEtfBatch:
    """采集核心 ETF 成交额，单标的外部失败不阻塞其他标的。"""
    if not etf_configs:
        return CoreEtfBatch(
            configured_symbols=[],
            items=[],
            failures=[],
            generated_at=datetime.now(UTC).isoformat(),
            schema_version=_CURRENT_SCHEMA_VERSION,
        )

    resolved_now = now or datetime.now(_SHANGHAI_TZ)
    if resolved_now.tzinfo is None or resolved_now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_shanghai = resolved_now.astimezone(_SHANGHAI_TZ)

    if hist_fetcher is None:
        import akshare as ak
        from lurker.ingest.flows import _akshare_request_scope

        provider_fetcher = ak.fund_etf_hist_em

        def scoped_hist_fetcher(**kwargs: Any) -> pd.DataFrame:
            with _akshare_request_scope():
                return provider_fetcher(**kwargs)

        hist_fetcher = scoped_hist_fetcher

    configured_symbols = [
        str(row.get("canonical_symbol") or row.get("symbol") or "").strip()
        for row in etf_configs
    ]
    if any(not symbol for symbol in configured_symbols):
        raise ValueError("ETF config requires symbol or canonical_symbol")
    if len(configured_symbols) != len(set(configured_symbols)):
        raise ValueError("ETF config canonical symbols must be unique")

    start_date = (now_shanghai.date() - timedelta(days=60)).strftime("%Y%m%d")
    end_date = now_shanghai.date().strftime("%Y%m%d")
    items: list[CoreEtfItem] = []
    failures: list[dict[str, str]] = []

    for config, canonical_symbol in zip(etf_configs, configured_symbols, strict=True):
        provider_symbol = str(config.get("symbol") or canonical_symbol.split(".", 1)[0]).strip()
        try:
            raw = hist_fetcher(
                symbol=provider_symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
            item = _normalize_etf_history(
                raw,
                canonical_symbol=canonical_symbol,
                name=str(config.get("name", "")).strip(),
                now_shanghai=now_shanghai,
            )
        except (EtfProviderError, EtfSchemaError) as exc:
            failures.append({"symbol": canonical_symbol, "reason": str(exc)})
            continue
        except (requests.RequestException, ConnectionError, TimeoutError, OSError) as exc:
            failures.append({"symbol": canonical_symbol, "reason": str(exc)})
            continue
        items.append(item)

    return CoreEtfBatch(
        configured_symbols=configured_symbols,
        items=items,
        failures=failures,
        generated_at=datetime.now(UTC).isoformat(),
        schema_version=_CURRENT_SCHEMA_VERSION,
    )


def _normalize_etf_history(
    raw: pd.DataFrame,
    *,
    canonical_symbol: str,
    name: str,
    now_shanghai: datetime,
) -> CoreEtfItem:
    if not isinstance(raw, pd.DataFrame):
        raise EtfSchemaError(f"{canonical_symbol}: ETF history must be a DataFrame")
    if raw.empty:
        raise EtfProviderError(f"{canonical_symbol}: empty ETF history")

    missing_columns = {"日期", "成交额"} - set(raw.columns)
    if missing_columns:
        raise EtfSchemaError(
            f"{canonical_symbol}: ETF history missing columns {sorted(missing_columns)}"
        )

    normalized = raw.loc[:, ["日期", "成交额"]].copy()
    normalized["日期"] = pd.to_datetime(normalized["日期"], errors="coerce")
    normalized["成交额"] = pd.to_numeric(normalized["成交额"], errors="coerce")
    normalized = normalized[
        normalized["日期"].notna()
        & normalized["成交额"].notna()
        & normalized["成交额"].map(lambda value: math.isfinite(float(value)) and value >= 0)
    ]
    normalized = (
        normalized.sort_values("日期")
        .drop_duplicates(subset=["日期"], keep="last")
        .reset_index(drop=True)
    )
    if normalized.empty:
        raise EtfSchemaError(f"{canonical_symbol}: no valid ETF turnover rows")

    latest = normalized.iloc[-1]
    trade_date = latest["日期"].date().isoformat()
    current_turnover = float(latest["成交额"])

    if len(normalized) < 21:
        return CoreEtfItem(
            symbol=canonical_symbol,
            name=name,
            trade_date=trade_date,
            current_turnover=current_turnover,
            avg_turnover_20d=None,
            turnover_expansion=None,
            shares=None,
            shares_date=None,
            status="unknown",
            source="akshare_fund_etf_hist_em",
            availability="insufficient_history",
            error=None,
        )

    history = normalized.iloc[-21:-1]["成交额"]
    average = float(history.mean())
    if not math.isfinite(average) or average <= 0:
        return CoreEtfItem(
            symbol=canonical_symbol,
            name=name,
            trade_date=trade_date,
            current_turnover=current_turnover,
            avg_turnover_20d=None,
            turnover_expansion=None,
            shares=None,
            shares_date=None,
            status="unknown",
            source="akshare_fund_etf_hist_em",
            availability="invalid_average",
            error=None,
        )

    expansion = current_turnover / average
    is_intraday = (
        latest["日期"].date() == now_shanghai.date()
        and now_shanghai.time() < _MARKET_CLOSE_CUTOFF
    )
    return CoreEtfItem(
        symbol=canonical_symbol,
        name=name,
        trade_date=trade_date,
        current_turnover=current_turnover,
        avg_turnover_20d=average,
        turnover_expansion=expansion,
        shares=None,
        shares_date=None,
        status=(
            "unknown"
            if is_intraday
            else "active" if expansion >= _ETF_ACTIVE_THRESHOLD else "inactive"
        ),
        source="akshare_fund_etf_hist_em",
        availability="intraday_partial" if is_intraday else "turnover_only",
        error=None,
    )
