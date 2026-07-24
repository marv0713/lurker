"""Core ETF flow ingestion — CoreEtfBatch dataclasses with strict validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


# ---------------------------------------------------------------------------
# Supported schema version
# ---------------------------------------------------------------------------

_CURRENT_SCHEMA_VERSION = 1

# Allowed keys per level for strict deserialization
_BATCH_KEYS = {"configured_symbols", "items", "failures", "generated_at", "schema_version"}
_ITEM_KEYS = {
    "symbol", "name", "trade_date", "current_turnover", "avg_turnover_20d",
    "turnover_expansion", "shares", "shares_date", "status", "source",
    "availability", "error",
}
_REQUIRED_ITEM_KEYS = {"symbol", "trade_date", "current_turnover", "status"}


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
        if not configured_symbols:
            raise ValueError("CoreEtfBatch missing configured_symbols")

        # Deserialize items with per-item validation
        items_data = data.get("items", [])
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
            if symbol in seen_item_symbols:
                raise ValueError(f"Duplicate symbol in CoreEtfBatch items: {symbol}")
            seen_item_symbols.add(symbol)

            items.append(CoreEtfItem(
                symbol=symbol,
                name=str(item.get("name", "")),
                trade_date=str(item.get("trade_date", "")),
                current_turnover=float(item["current_turnover"]),
                avg_turnover_20d=(
                    float(item["avg_turnover_20d"])
                    if item.get("avg_turnover_20d") is not None
                    else None
                ),
                turnover_expansion=(
                    float(item["turnover_expansion"])
                    if item.get("turnover_expansion") is not None
                    else None
                ),
                shares=(
                    float(item["shares"])
                    if item.get("shares") is not None
                    else None
                ),
                shares_date=item.get("shares_date"),
                status=str(item.get("status", "unknown")),
                source=str(item.get("source", "")),
                availability=str(item.get("availability", "unknown")),
                error=item.get("error"),
            ))

        # Deserialize failures with duplicate check
        failures_data = data.get("failures", [])
        failures = []
        seen_failure_symbols = set()
        for f in failures_data:
            if not isinstance(f, dict):
                raise ValueError(f"CoreEtfBatch failure must be a dict, got {type(f)}")
            symbol = str(f.get("symbol", ""))
            if symbol in seen_failure_symbols:
                raise ValueError(f"Duplicate symbol in CoreEtfBatch failures: {symbol}")
            seen_failure_symbols.add(symbol)
            failures.append({"symbol": symbol, "reason": str(f.get("reason", ""))})

        return cls(
            configured_symbols=list(configured_symbols),
            items=items,
            failures=failures,
            generated_at=str(data.get("generated_at", "")),
            schema_version=version,
        )

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


# ---------------------------------------------------------------------------
# Fetcher (stub — real AkShare integration after schema pre-check)
# ---------------------------------------------------------------------------


def fetch_core_etfs(
    *,
    etf_configs: list[dict[str, str]] | None = None,
) -> CoreEtfBatch:
    """采集核心 ETF 成交额数据。

    Currently returns a stub batch. Real AkShare integration pending:
    1. Schema pre-check (ak.fund_etf_hist_em column names, code format, units)
    2. Network proxy verification in deployment environment
    """
    if not etf_configs:
        return CoreEtfBatch(
            configured_symbols=[],
            items=[],
            failures=[],
            generated_at=datetime.now(UTC).isoformat(),
            schema_version=_CURRENT_SCHEMA_VERSION,
        )

    configured_symbols = [row.get("canonical_symbol", row.get("symbol", "")) for row in etf_configs]
    return CoreEtfBatch(
        configured_symbols=configured_symbols,
        items=[],
        failures=[
            {"symbol": symbol, "reason": "ETF fetcher stub: real AkShare integration pending Task 2"}
            for symbol in configured_symbols
        ],
        generated_at=datetime.now(UTC).isoformat(),
        schema_version=_CURRENT_SCHEMA_VERSION,
    )
