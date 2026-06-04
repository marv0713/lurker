from lurker.application.flow_snapshot import (
    FileFlowSnapshotStore,
    collect_flow_snapshot,
    load_flow_snapshot_file,
)


def test_collect_flow_snapshot_records_successes_and_failures():
    def fetch_market_flow():
        return {"main_net_inflow": 1.0}

    def fetch_sector_flows():
        raise RuntimeError("sector offline")

    def fetch_stock_flows():
        return [{"symbol": "300308.SZ", "main_net_inflow": 10.0}]

    def fetch_margin():
        return {"margin_balance": 100.0}

    snapshot = collect_flow_snapshot(
        fetch_market_flow=fetch_market_flow,
        fetch_sector_flows=fetch_sector_flows,
        fetch_stock_flows=fetch_stock_flows,
        fetch_margin=fetch_margin,
        generated_at="2026-06-04T00:00:00+00:00",
    )

    assert snapshot["market"] == "cn"
    assert snapshot["market_flow"]["main_net_inflow"] == 1.0
    assert snapshot["stock_flows"][0]["symbol"] == "300308.SZ"
    assert snapshot["margin"]["margin_balance"] == 100.0
    assert snapshot["failures"][0]["source"] == "sector_flows"


def test_file_flow_snapshot_store_round_trips(tmp_path):
    store = FileFlowSnapshotStore(tmp_path)
    snapshot = {
        "schema_version": 1,
        "generated_at": "2026-06-04T00:00:00+00:00",
        "market": "cn",
        "market_flow": {},
        "sector_flows": [],
        "stock_flows": [],
        "margin": {},
        "core_etfs": [],
        "failures": [],
    }

    path = store.save(snapshot, "2026-06-04")
    loaded = load_flow_snapshot_file(path)

    assert loaded == snapshot
    assert store.load_latest() == snapshot
