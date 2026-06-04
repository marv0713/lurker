from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Protocol

from lurker.ingest.flows import (
    fetch_margin,
    fetch_market_flow,
    fetch_sector_flows,
    fetch_stock_flows,
)


FlowSnapshot = dict[str, Any]


class FlowSnapshotStore(Protocol):
    def save(self, snapshot: FlowSnapshot, snapshot_date: str) -> Path: ...

    def load_latest(self) -> FlowSnapshot | None: ...


def _capture(source: str, fetcher: Callable[[], Any], failures: list[dict[str, str]]) -> Any:
    try:
        return fetcher()
    except Exception as exc:
        failures.append({"source": source, "reason": f"{type(exc).__name__}: {exc}"})
        return [] if source.endswith("flows") or source == "core_etfs" else {}


def collect_flow_snapshot(
    *,
    fetch_market_flow: Callable[[], dict[str, Any]] = fetch_market_flow,
    fetch_sector_flows: Callable[[], list[dict[str, Any]]] = fetch_sector_flows,
    fetch_stock_flows: Callable[[], list[dict[str, Any]]] = fetch_stock_flows,
    fetch_margin: Callable[[], dict[str, Any]] = fetch_margin,
    fetch_core_etfs: Callable[[], list[dict[str, Any]]] | None = None,
    generated_at: str | None = None,
) -> FlowSnapshot:
    failures: list[dict[str, str]] = []
    return {
        "schema_version": 1,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "market": "cn",
        "market_flow": _capture("market_flow", fetch_market_flow, failures),
        "sector_flows": _capture("sector_flows", fetch_sector_flows, failures),
        "stock_flows": _capture("stock_flows", fetch_stock_flows, failures),
        "margin": _capture("margin", fetch_margin, failures),
        "core_etfs": _capture("core_etfs", fetch_core_etfs or (lambda: []), failures),
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
