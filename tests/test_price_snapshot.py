import pandas as pd

from lurker.application.price_snapshot import (
    FilePriceSnapshotStore,
    collect_price_snapshot_batch,
    collect_price_snapshots,
    fetch_prices_for_market,
    find_latest_price_snapshot,
    load_price_snapshot_file,
    render_price_snapshot,
    save_price_snapshot_file,
    select_price_snapshot_rows,
)


def fake_fetcher(symbol: str, period: str) -> pd.DataFrame:
    del period
    return pd.DataFrame(
        {
            "symbol": [symbol, symbol, symbol],
            "trade_date": [pd.Timestamp("2026-05-13").date(), pd.Timestamp("2026-05-14").date(), pd.Timestamp("2026-05-15").date()],
            "open": [100, 110, 130],
            "high": [110, 130, 150],
            "low": [95, 108, 125],
            "close": [100, 120, 140],
            "adj_close": [100, 120, 140],
            "volume": [1000, 1200, 2000],
        }
    )


def test_collect_price_snapshots_from_seed_symbols():
    seed_symbols = {"us": ["NVDA", "AVGO"], "hk": ["0700.HK"]}

    snapshots = collect_price_snapshots(
        seed_symbols=seed_symbols,
        markets=["us"],
        windows=[1, 2],
        period="5d",
        fetcher=fake_fetcher,
        limit_per_market=1,
    )

    assert len(snapshots) == 1
    assert snapshots[0]["symbol"] == "NVDA"
    assert snapshots[0]["market"] == "us"
    assert round(snapshots[0]["return_1d"], 4) == 0.1667
    assert round(snapshots[0]["return_2d"], 4) == 0.4


def test_render_price_snapshot_as_markdown_table():
    markdown = render_price_snapshot(
        [
            {
                "symbol": "NVDA",
                "market": "us",
                "latest_close": 140.0,
                "return_1d": 0.1667,
                "return_2d": 0.4,
            }
        ],
        windows=[1, 2],
    )

    assert "| Symbol | Market | Close | 1D | 2D |" in markdown
    assert "| NVDA | us | 140.00 | 16.67% | 40.00% |" in markdown


def test_render_price_snapshot_marks_empty_data():
    markdown = render_price_snapshot([], windows=[20, 60])

    assert "| No available data | - | - | - | - |" in markdown


def test_fetch_prices_for_market_uses_market_specific_fetcher():
    calls = []

    def cn_fetcher(symbol: str, period: str) -> pd.DataFrame:
        calls.append(("cn", symbol, period))
        return fake_fetcher(symbol, period)

    def global_fetcher(symbol: str, period: str) -> pd.DataFrame:
        calls.append(("global", symbol, period))
        return fake_fetcher(symbol, period)

    result = fetch_prices_for_market(
        "300308.SZ",
        "cn",
        "5d",
        fetchers={"cn": cn_fetcher, "us": global_fetcher, "hk": global_fetcher},
    )

    assert not result.empty
    assert calls == [("cn", "300308.SZ", "5d")]


def test_collect_price_snapshots_skips_symbols_that_fail_to_fetch():
    def flaky_fetcher(symbol: str, period: str) -> pd.DataFrame:
        if symbol == "300308.SZ":
            raise RuntimeError("upstream failed")
        return fake_fetcher(symbol, period)

    snapshots = collect_price_snapshots(
        seed_symbols={"cn": ["300308.SZ", "300502.SZ"]},
        markets=["cn"],
        windows=[1],
        period="5d",
        fetcher=flaky_fetcher,
    )

    assert len(snapshots) == 1
    assert snapshots[0]["symbol"] == "300502.SZ"


def test_collect_price_snapshot_batch_records_failures():
    def flaky_fetcher(symbol: str, period: str) -> pd.DataFrame:
        if symbol == "300308.SZ":
            raise RuntimeError("upstream failed")
        return fake_fetcher(symbol, period)

    batch = collect_price_snapshot_batch(
        seed_symbols={"cn": ["300308.SZ", "300502.SZ"]},
        markets=["cn"],
        windows=[1],
        period="5d",
        fetcher=flaky_fetcher,
        generated_at="2026-05-17T12:00:00+00:00",
        seed_pool_generated_at="2026-05-16T12:00:00+00:00",
    )

    assert batch["generated_at"] == "2026-05-17T12:00:00+00:00"
    assert batch["seed_pool_generated_at"] == "2026-05-16T12:00:00+00:00"
    assert batch["markets"] == ["cn"]
    assert batch["windows"] == [1]
    assert batch["snapshots"][0]["symbol"] == "300502.SZ"
    assert batch["failures"] == [
        {"symbol": "300308.SZ", "market": "cn", "reason": "RuntimeError: upstream failed"}
    ]


def test_save_load_and_find_latest_price_snapshot(tmp_path):
    old_path = tmp_path / "2026-05-16.json"
    latest_path = tmp_path / "2026-05-17.json"
    old_path.write_text('{"generated_at": "old", "snapshots": []}', encoding="utf-8")
    snapshot = {"generated_at": "new", "snapshots": [{"symbol": "NVDA", "market": "us"}]}

    save_price_snapshot_file(snapshot, latest_path)

    assert load_price_snapshot_file(latest_path) == snapshot
    assert find_latest_price_snapshot(tmp_path) == latest_path


def test_select_price_snapshot_rows_filters_markets():
    snapshot = {
        "snapshots": [
            {"symbol": "300308.SZ", "market": "cn", "latest_close": 140.0},
            {"symbol": "NVDA", "market": "us", "latest_close": 1000.0},
        ]
    }

    assert select_price_snapshot_rows(snapshot, markets=["cn"]) == [
        {"symbol": "300308.SZ", "market": "cn", "latest_close": 140.0}
    ]


def test_file_price_snapshot_store_saves_and_loads_latest(tmp_path):
    store = FilePriceSnapshotStore(tmp_path)
    snapshot = {"generated_at": "2026-05-17T12:00:00+00:00", "snapshots": []}

    output_path = store.save(snapshot, snapshot_date="2026-05-17")

    assert output_path == tmp_path / "2026-05-17.json"
    assert store.load_latest() == snapshot
