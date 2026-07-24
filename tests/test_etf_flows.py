"""Tests for CoreEtfBatch/CoreEtfItem validation and serialization."""

import pytest

from lurker.ingest.etf_flows import CoreEtfBatch, CoreEtfItem


# ---------------------------------------------------------------------------
# CoreEtfItem
# ---------------------------------------------------------------------------


def test_core_etf_item_construction():
    item = CoreEtfItem(
        symbol="510300.SH",
        name="沪深300ETF",
        trade_date="2026-07-23",
        current_turnover=3_000_000_000.0,
        avg_turnover_20d=2_000_000_000.0,
        turnover_expansion=1.5,
        shares=None,
        shares_date=None,
        status="active",
        source="akshare_fund_etf_hist_em",
        availability="turnover_only",
        error=None,
    )
    assert item.symbol == "510300.SH"
    assert item.turnover_expansion == 1.5


# ---------------------------------------------------------------------------
# CoreEtfBatch.is_complete
# ---------------------------------------------------------------------------


def test_is_complete_true_when_all_accounted():
    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510500.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH", name="", trade_date="2026-07-23",
                current_turnover=1.0, avg_turnover_20d=1.0, turnover_expansion=1.0,
                shares=None, shares_date=None, status="active",
                source="", availability="turnover_only", error=None,
            ),
        ],
        failures=[{"symbol": "510500.SH", "reason": "timeout"}],
    )
    assert batch.is_complete() is True


def test_is_complete_false_when_symbol_missing():
    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510500.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH", name="", trade_date="2026-07-23",
                current_turnover=1.0, avg_turnover_20d=1.0, turnover_expansion=1.0,
                shares=None, shares_date=None, status="active",
                source="", availability="turnover_only", error=None,
            ),
        ],
        failures=[],  # Missing 510500.SH
    )
    assert batch.is_complete() is False


def test_is_complete_false_when_duplicate_items():
    batch = CoreEtfBatch(
        configured_symbols=["510300.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH", name="", trade_date="2026-07-23",
                current_turnover=1.0, avg_turnover_20d=1.0, turnover_expansion=1.0,
                shares=None, shares_date=None, status="active",
                source="", availability="turnover_only", error=None,
            ),
            CoreEtfItem(
                symbol="510300.SH", name="", trade_date="2026-07-23",
                current_turnover=2.0, avg_turnover_20d=2.0, turnover_expansion=1.0,
                shares=None, shares_date=None, status="active",
                source="", availability="turnover_only", error=None,
            ),
        ],
        failures=[],
    )
    assert batch.is_complete() is False


def test_is_complete_false_when_duplicate_failures():
    batch = CoreEtfBatch(
        configured_symbols=["510300.SH"],
        items=[],
        failures=[
            {"symbol": "510300.SH", "reason": "a"},
            {"symbol": "510300.SH", "reason": "b"},
        ],
    )
    assert batch.is_complete() is False


def test_is_complete_false_when_overlap():
    batch = CoreEtfBatch(
        configured_symbols=["510300.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH", name="", trade_date="2026-07-23",
                current_turnover=1.0, avg_turnover_20d=1.0, turnover_expansion=1.0,
                shares=None, shares_date=None, status="active",
                source="", availability="turnover_only", error=None,
            ),
        ],
        failures=[{"symbol": "510300.SH", "reason": "also failed"}],
    )
    assert batch.is_complete() is False


# ---------------------------------------------------------------------------
# CoreEtfBatch.from_dict validation
# ---------------------------------------------------------------------------


def test_from_dict_round_trips():
    data = {
        "configured_symbols": ["510300.SH"],
        "items": [
            {
                "symbol": "510300.SH",
                "name": "沪深300ETF",
                "trade_date": "2026-07-23",
                "current_turnover": 3_000_000_000.0,
                "avg_turnover_20d": 2_000_000_000.0,
                "turnover_expansion": 1.5,
                "shares": None,
                "shares_date": None,
                "status": "active",
                "source": "akshare_fund_etf_hist_em",
                "availability": "turnover_only",
                "error": None,
            }
        ],
        "failures": [],
        "generated_at": "2026-07-23T00:00:00+00:00",
        "schema_version": 1,
    }
    batch = CoreEtfBatch.from_dict(data)
    assert batch.is_complete() is True
    assert batch.items[0].symbol == "510300.SH"
    assert batch.items[0].turnover_expansion == 1.5
    # to_dict round-trip
    back = batch.to_dict()
    batch2 = CoreEtfBatch.from_dict(back)
    assert batch2.items[0].symbol == "510300.SH"


def test_from_dict_rejects_unknown_top_level_keys():
    with pytest.raises(ValueError, match="Unknown keys"):
        CoreEtfBatch.from_dict({
            "configured_symbols": ["510300.SH"],
            "items": [],
            "failures": [],
            "generated_at": "",
            "schema_version": 1,
            "bogus_field": 123,
        })


def test_from_dict_rejects_unknown_item_keys():
    with pytest.raises(ValueError, match="Unknown keys"):
        CoreEtfBatch.from_dict({
            "configured_symbols": ["510300.SH"],
            "items": [{
                "symbol": "510300.SH",
                "trade_date": "2026-07-23",
                "current_turnover": 1.0,
                "status": "active",
                "bogus": "x",
            }],
            "failures": [],
            "generated_at": "",
            "schema_version": 1,
        })


def test_from_dict_rejects_unsupported_schema_version():
    with pytest.raises(ValueError, match="Unsupported"):
        CoreEtfBatch.from_dict({
            "configured_symbols": ["510300.SH"],
            "items": [],
            "failures": [],
            "generated_at": "",
            "schema_version": 99,
        })


def test_from_dict_rejects_missing_configured_symbols():
    with pytest.raises(ValueError, match="missing configured_symbols"):
        CoreEtfBatch.from_dict({
            "items": [],
            "failures": [],
            "generated_at": "",
            "schema_version": 1,
        })


def test_from_dict_rejects_missing_required_item_keys():
    with pytest.raises(ValueError, match="Missing required keys"):
        CoreEtfBatch.from_dict({
            "configured_symbols": ["510300.SH"],
            "items": [{
                "symbol": "510300.SH",
                # missing trade_date, current_turnover, status
            }],
            "failures": [],
            "generated_at": "",
            "schema_version": 1,
        })


def test_from_dict_rejects_duplicate_items():
    with pytest.raises(ValueError, match="Duplicate symbol"):
        CoreEtfBatch.from_dict({
            "configured_symbols": ["510300.SH"],
            "items": [
                {"symbol": "510300.SH", "trade_date": "2026-07-23", "current_turnover": 1.0, "status": "active"},
                {"symbol": "510300.SH", "trade_date": "2026-07-23", "current_turnover": 2.0, "status": "active"},
            ],
            "failures": [],
            "generated_at": "",
            "schema_version": 1,
        })


def test_from_dict_rejects_corrupted_item_not_dict():
    with pytest.raises(ValueError, match="must be a dict"):
        CoreEtfBatch.from_dict({
            "configured_symbols": ["510300.SH"],
            "items": ["not_a_dict"],
            "failures": [],
            "generated_at": "",
            "schema_version": 1,
        })
