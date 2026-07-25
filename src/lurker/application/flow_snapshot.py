from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING

from lurker.ingest.flows import (
    fetch_margin,
    fetch_market_flow,
    fetch_sector_flows,
    fetch_stock_flows,
)

if TYPE_CHECKING:
    from lurker.ingest.etf_flows import CoreEtfBatch


FlowSnapshot = dict[str, Any]


class FlowSnapshotStore(Protocol):
    def save(self, snapshot: FlowSnapshot, snapshot_date: str) -> Path: ...

    def load_latest(self) -> FlowSnapshot | None: ...


def _capture(source: str, fetcher: Callable[[], Any], failures: list[dict[str, str]]) -> Any:
    try:
        return fetcher()
    except Exception as exc:
        reason = str(exc)
        if "频率超限" in reason or "limit" in reason.lower():
            friendly_reason = "接口调用频率超限（如每小时限制 1 次），请稍后再试。"
        elif "TOKEN" in reason.upper():
            friendly_reason = "接口 Token 未配置或已失效。"
        else:
            friendly_reason = f"{type(exc).__name__}: {reason}"
        failures.append({"source": source, "reason": friendly_reason})
        return [] if source.endswith("flows") or source == "core_etfs" else {}


def _default_etf_fetcher() -> "CoreEtfBatch":
    """Default ETF fetcher. Import/config errors fail loudly; only provider errors degrade."""
    from lurker.ingest.etf_flows import CoreEtfBatch, EtfProviderError, EtfSchemaError, fetch_core_etfs
    from lurker.config import load_core_etfs

    config_path = Path(__file__).resolve().parents[3] / "configs" / "core_etfs.yaml"
    if not config_path.exists():
        raise RuntimeError(
            f"core_etfs.yaml not found at {config_path}. "
            "This file is required for ETF market temperature."
        )
    configs = load_core_etfs(config_path)
    if not configs:
        raise RuntimeError("core_etfs.yaml is empty — at least one ETF must be configured.")

    try:
        return fetch_core_etfs(etf_configs=configs)
    except (EtfProviderError, EtfSchemaError, ConnectionError, TimeoutError, OSError) as e:
        configured_symbols = [row["canonical_symbol"] for row in configs]
        return CoreEtfBatch(
            configured_symbols=configured_symbols,
            items=[],
            failures=[
                {"symbol": symbol, "reason": f"数据源不可用: {e}"}
                for symbol in configured_symbols
            ],
            generated_at=datetime.now(UTC).isoformat(),
            schema_version=1,
        )
    # TypeError, AttributeError, KeyError → propagate (program error, not recoverable)


def collect_flow_snapshot(
    *,
    fetch_market_flow: Callable[[], dict[str, Any]] = fetch_market_flow,
    fetch_sector_flows: Callable[[], list[dict[str, Any]]] = fetch_sector_flows,
    fetch_stock_flows: Callable[[], list[dict[str, Any]]] = fetch_stock_flows,
    fetch_margin: Callable[[], dict[str, Any]] = fetch_margin,
    fetch_core_etfs: Callable[[], "CoreEtfBatch"] | None = None,
    generated_at: str | None = None,
) -> FlowSnapshot:
    failures: list[dict[str, str]] = []

    # --- ETF: bypass _capture() to avoid swallowing TypeError/KeyError ---
    from lurker.ingest.etf_flows import CoreEtfBatch

    etf_fetcher = fetch_core_etfs if fetch_core_etfs is not None else _default_etf_fetcher
    core_etfs_data = etf_fetcher()
    if not isinstance(core_etfs_data, CoreEtfBatch):
        raise TypeError("ETF fetcher must return CoreEtfBatch")

    # --- Margin: persist raw facts only; signals are derived by the preparation layer ---
    margin_data = _capture("margin", fetch_margin, failures)

    return {
        "schema_version": 2,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "market": "cn",
        "market_flow": _capture("market_flow", fetch_market_flow, failures),
        "sector_flows": _capture("sector_flows", fetch_sector_flows, failures),
        "stock_flows": _capture("stock_flows", fetch_stock_flows, failures),
        "margin": margin_data,
        "core_etfs": core_etfs_data.to_dict(),
        "failures": failures,
    }


def save_flow_snapshot_file(snapshot: FlowSnapshot, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_flow_snapshot_file(path: str | Path) -> FlowSnapshot:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def find_latest_flow_snapshot(directory: str | Path) -> Path | None:
    snapshot_dir = Path(directory)
    if not snapshot_dir.exists():
        return None
    paths = sorted(path for path in snapshot_dir.glob("*.json") if path.name != "latest.json")
    return paths[-1] if paths else None


class FileFlowSnapshotStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def save(self, snapshot: FlowSnapshot, snapshot_date: str) -> Path:
        output_path = self.directory / f"{snapshot_date}.json"
        save_flow_snapshot_file(snapshot, output_path)
        return output_path

    def load_latest(self) -> FlowSnapshot | None:
        latest_path = find_latest_flow_snapshot(self.directory)
        if latest_path is None:
            return None
        return load_flow_snapshot_file(latest_path)
