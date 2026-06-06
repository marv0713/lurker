from datetime import date

from lurker.storage.db import create_session, init_db
from lurker.storage.models import Candidate, PriceDaily, SignalEvent, Symbol


def test_init_db_and_insert_symbol(tmp_path):
    db_path = tmp_path / "lurker.sqlite"
    engine = init_db(db_path)
    session = create_session(engine)

    session.add(Symbol(symbol="NVDA", name="NVIDIA", market="us", asset_type="stock"))
    session.commit()

    saved = session.get(Symbol, "NVDA")
    assert saved is not None
    assert saved.market == "us"


def test_insert_price_signal_and_candidate(tmp_path):
    db_path = tmp_path / "lurker.sqlite"
    engine = init_db(db_path)
    session = create_session(engine)

    session.add(Symbol(symbol="300308.SZ", name="中际旭创", market="cn", asset_type="stock"))
    session.add(
        PriceDaily(
            symbol="300308.SZ",
            trade_date=date(2026, 5, 15),
            open=100,
            high=110,
            low=98,
            close=108,
            adj_close=108,
            volume=1000000,
            amount=108000000,
            market_cap=100000000000,
        )
    )
    session.add(
        SignalEvent(
            symbol="300308.SZ",
            trade_date=date(2026, 5, 15),
            signal_type="stock_strength",
            signal_score=86,
            trigger_reason="60 日强度进入前 10%",
            related_theme_id="ai_infra",
            raw_metrics={"return_60d": 0.52},
        )
    )
    session.add(
        Candidate(
            trade_date=date(2026, 5, 15),
            theme_id="ai_infra",
            primary_symbols=["300308.SZ"],
            expanded_symbols=["300502.SZ"],
            stock_score=86,
            sector_score=76,
            ai_score=80,
            total_score=80.8,
            visibility_tier="main",
            status="active",
        )
    )
    session.commit()

    assert session.query(PriceDaily).count() == 1
    assert session.query(SignalEvent).one().raw_metrics["return_60d"] == 0.52
    assert session.query(Candidate).one().visibility_tier == "main"


def test_daily_job_with_db(monkeypatch, tmp_path):
    import pandas as pd
    from lurker.cli import daily_job
    from lurker.storage.models import Symbol, PriceDaily, Candidate, Report, AIAttribution

    db_path = tmp_path / "lurker.sqlite"
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-05-16T12:00:00+00:00",
  "theme_mapping": {"300308.SZ": ["ai_infra"]},
  "symbol_names": {"300308.SZ": "中际旭创"},
  "markets": {
    "cn": {
      "symbols": ["300308.SZ"],
      "sources": {}
    }
  }
}
""",
        encoding="utf-8",
    )

    # Mock price fetching to avoid hitting live APIs
    # Return 61 data points to allow returns calculation for 20 and 60 windows
    from datetime import timedelta
    dates = [date(2026, 3, 1) + timedelta(days=i) for i in range(70)]
    closes = [10.0 + i * 2.0 for i in range(70)]
    fake_df = pd.DataFrame(
        {
            "symbol": ["300308.SZ"] * 70,
            "trade_date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "adj_close": closes,
            "volume": [1000000] * 70,
        }
    )

    def fake_fetch_prices(symbol, market, period, fetchers=None):
        return fake_df

    monkeypatch.setattr("lurker.application.price_snapshot.fetch_prices_for_market", fake_fetch_prices)

    price_snapshot_dir = tmp_path / "price_snapshots"
    report_dir = tmp_path / "reports"

    daily_job(
        seed_pool_path=seed_pool_path,
        price_snapshot_dir=price_snapshot_dir,
        report_dir=report_dir,
        markets=["cn"],
        windows=[20, 60],
        period="6mo",
        limit_per_market=1,
        report_date="2026-05-17",
        signal_threshold=0,
        db_path=db_path,
    )

    # Assert DB tables are populated
    engine = init_db(db_path)
    session = create_session(engine)

    symbols = session.query(Symbol).all()
    assert len(symbols) == 1
    assert symbols[0].symbol == "300308.SZ"
    assert symbols[0].name == "中际旭创"

    prices = session.query(PriceDaily).all()
    assert len(prices) == 70
    assert prices[0].symbol == "300308.SZ"

    reports = session.query(Report).all()
    assert len(reports) == 1
    assert reports[0].report_date == date(2026, 5, 17)
    assert reports[0].report_type == "daily"
    assert len(reports[0].content) > 0

    candidates = session.query(Candidate).all()
    assert len(candidates) > 0
    assert candidates[0].primary_symbols == ["300308.SZ"]

    attributions = session.query(AIAttribution).all()
    assert len(attributions) > 0
    assert attributions[0].candidate_id == candidates[0].candidate_id
    assert len(attributions[0].reason_summary) > 0
