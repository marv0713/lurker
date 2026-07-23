"""Core ETF flow ingestion (stub — Task 3 will implement fully)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CoreEtfItem:
    """Single ETF data point (stub for Task 1 test collection)."""

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
    """Collected ETF batch (stub for Task 1 test collection)."""

    configured_symbols: list[str]
    items: list[CoreEtfItem] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    generated_at: str = ""
    schema_version: int = 1

    def is_complete(self) -> bool:
        """Verify configured_symbols == (item symbols ∪ failure symbols)."""
        item_symbols = {item.symbol for item in self.items}
        failure_symbols = {f["symbol"] for f in self.failures}
        configured = set(self.configured_symbols)
        return configured == (item_symbols | failure_symbols)

    @classmethod
    def from_dict(cls, data: dict) -> "CoreEtfBatch":
        """Deserialize from dict (stub — Task 3 implements fully)."""
        raise NotImplementedError("from_dict will be implemented in Task 3")

    def to_dict(self) -> dict:
        """Serialize to dict (stub — Task 3 implements fully)."""
        raise NotImplementedError("to_dict will be implemented in Task 3")


class EtfProviderError(Exception):
    """AkShare ETF data source unavailable."""


class EtfSchemaError(Exception):
    """AkShare ETF response schema mismatch."""
