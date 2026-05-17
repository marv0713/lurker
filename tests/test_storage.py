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
