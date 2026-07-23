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
        """Deserialize from dict with nested CoreEtfItem recovery."""
        items_data = data.get("items", [])
        items = [
            CoreEtfItem(
                symbol=item["symbol"],
                name=item.get("name", ""),
                trade_date=item.get("trade_date", ""),
                current_turnover=float(item.get("current_turnover", 0)),
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
                status=item.get("status", "unknown"),
                source=item.get("source", ""),
                availability=item.get("availability", "unknown"),
                error=item.get("error"),
            )
            for item in items_data
        ]
        failures = data.get("failures", [])
        return cls(
            configured_symbols=data.get("configured_symbols", []),
            items=items,
            failures=failures,
            generated_at=data.get("generated_at", ""),
            schema_version=data.get("schema_version", 1),
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


class EtfProviderError(Exception):
    """AkShare ETF data source unavailable."""


class EtfSchemaError(Exception):
    """AkShare ETF response schema mismatch."""


def fetch_core_etfs(
    *,
    etf_configs: list[dict[str, str]] | None = None,
) -> CoreEtfBatch:
    """采集核心 ETF 成交额数据（stub — Task 3 full implementation pending AkShare schema check).

    Currently returns a stub batch for CI/testing. Real AkShare integration
    will be added after Task 2 schema pre-check.
    """
    from datetime import UTC, datetime

    if not etf_configs:
        return CoreEtfBatch(
            configured_symbols=[],
            items=[],
            failures=[],
            generated_at=datetime.now(UTC).isoformat(),
            schema_version=1,
        )

    configured_symbols = [row.get("canonical_symbol", row.get("symbol", "")) for row in etf_configs]
    # Stub: return a batch that will make classify_etf_status return "unknown"
    # Real implementation will call ak.fund_etf_hist_em() for each ETF
    return CoreEtfBatch(
        configured_symbols=configured_symbols,
        items=[],
        failures=[
            {"symbol": symbol, "reason": "ETF fetcher stub: real AkShare integration pending Task 2"}
            for symbol in configured_symbols
        ],
        generated_at=datetime.now(UTC).isoformat(),
        schema_version=1,
    )
