"""Tests for core ETF ingestion, validation, and serialization."""

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

import pandas as pd
import pytest
import requests

from lurker.ingest.etf_flows import (
    CoreEtfBatch,
    CoreEtfItem,
    EtfProviderError,
    fetch_core_etfs,
)


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


def test_from_dict_rejects_unaccounted_configured_symbol():
    with pytest.raises(ValueError, match="not complete"):
        CoreEtfBatch.from_dict(
            {
                "configured_symbols": ["510300.SH", "510500.SH"],
                "items": [
                    {
                        "symbol": "510300.SH",
                        "trade_date": "2026-07-23",
                        "current_turnover": 1.0,
                        "status": "inactive",
                    }
                ],
                "failures": [],
                "schema_version": 1,
            }
        )


def test_is_complete_rejects_duplicate_configured_symbols():
    batch = CoreEtfBatch(
        configured_symbols=["510300.SH", "510300.SH"],
        items=[
            CoreEtfItem(
                symbol="510300.SH",
                name="",
                trade_date="2026-07-23",
                current_turnover=1.0,
                avg_turnover_20d=1.0,
                turnover_expansion=1.0,
                shares=None,
                shares_date=None,
                status="inactive",
                source="",
                availability="turnover_only",
                error=None,
            ),
        ],
    )

    assert batch.is_complete() is False


def test_from_dict_rejects_non_finite_turnover():
    with pytest.raises(ValueError, match="finite"):
        CoreEtfBatch.from_dict(
            {
                "configured_symbols": ["510300.SH"],
                "items": [
                    {
                        "symbol": "510300.SH",
                        "trade_date": "2026-07-23",
                        "current_turnover": 1.0,
                        "turnover_expansion": float("nan"),
                        "status": "inactive",
                    }
                ],
                "failures": [],
                "schema_version": 1,
            }
        )


def _etf_history(*, latest_turnover: float = 240.0, periods: int = 21) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-07-23", periods=periods)
    turnovers = [100.0] * max(periods - 1, 0) + [latest_turnover]
    return pd.DataFrame({"日期": dates, "成交额": turnovers})


def test_fetch_core_etfs_computes_average_excluding_current_day():
    calls = []

    def hist_fetcher(**kwargs):
        calls.append(kwargs)
        return _etf_history()

    batch = fetch_core_etfs(
        etf_configs=[
            {
                "symbol": "510300",
                "canonical_symbol": "510300.SH",
                "name": "沪深300ETF",
            }
        ],
        hist_fetcher=hist_fetcher,
        now=datetime(2026, 7, 23, 16, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert calls[0]["symbol"] == "510300"
    assert len(batch.items) == 1
    assert batch.failures == []
    assert batch.items[0].avg_turnover_20d == 100.0
    assert batch.items[0].turnover_expansion == 2.4
    assert batch.items[0].status == "active"


def test_fetch_core_etfs_keeps_success_when_one_provider_call_fails():
    def hist_fetcher(**kwargs):
        if kwargs["symbol"] == "510500":
            raise EtfProviderError("timeout")
        return _etf_history(latest_turnover=80.0)

    batch = fetch_core_etfs(
        etf_configs=[
            {"symbol": "510300", "canonical_symbol": "510300.SH", "name": "沪深300ETF"},
            {"symbol": "510500", "canonical_symbol": "510500.SH", "name": "中证500ETF"},
        ],
        hist_fetcher=hist_fetcher,
        now=datetime(2026, 7, 23, 16, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert [item.symbol for item in batch.items] == ["510300.SH"]
    assert batch.failures == [{"symbol": "510500.SH", "reason": "timeout"}]
    assert batch.is_complete() is True


def test_fetch_core_etfs_marks_insufficient_history_unknown():
    batch = fetch_core_etfs(
        etf_configs=[
            {"symbol": "510300", "canonical_symbol": "510300.SH", "name": "沪深300ETF"}
        ],
        hist_fetcher=lambda **kwargs: _etf_history(periods=20),
        now=datetime(2026, 7, 23, 16, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    item = batch.items[0]
    assert item.avg_turnover_20d is None
    assert item.turnover_expansion is None
    assert item.status == "unknown"
    assert item.availability == "insufficient_history"


def test_fetch_core_etfs_marks_current_session_intraday_unknown():
    batch = fetch_core_etfs(
        etf_configs=[
            {"symbol": "510300", "canonical_symbol": "510300.SH", "name": "沪深300ETF"}
        ],
        hist_fetcher=lambda **kwargs: _etf_history(),
        now=datetime(2026, 7, 23, 10, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    item = batch.items[0]
    assert item.status == "unknown"
    assert item.availability == "intraday_partial"


def test_fetch_core_etfs_marks_zero_average_invalid():
    raw = _etf_history()
    raw.loc[:19, "成交额"] = 0.0

    batch = fetch_core_etfs(
        etf_configs=[
            {"symbol": "510300", "canonical_symbol": "510300.SH", "name": "沪深300ETF"}
        ],
        hist_fetcher=lambda **kwargs: raw,
        now=datetime(2026, 7, 23, 16, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    item = batch.items[0]
    assert item.avg_turnover_20d is None
    assert item.turnover_expansion is None
    assert item.status == "unknown"
    assert item.availability == "invalid_average"


def test_fetch_core_etfs_records_schema_failure_per_symbol():
    batch = fetch_core_etfs(
        etf_configs=[
            {"symbol": "510300", "canonical_symbol": "510300.SH", "name": "沪深300ETF"}
        ],
        hist_fetcher=lambda **kwargs: pd.DataFrame({"日期": ["2026-07-23"]}),
        now=datetime(2026, 7, 23, 16, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert batch.items == []
    assert batch.is_complete() is True
    assert batch.failures[0]["symbol"] == "510300.SH"
    assert "missing columns" in batch.failures[0]["reason"]


def test_fetch_core_etfs_default_provider_uses_akshare_request_scope(monkeypatch):
    entered = []

    @contextmanager
    def fake_scope():
        entered.append(True)
        yield

    monkeypatch.setattr("lurker.ingest.flows._akshare_request_scope", fake_scope)
    monkeypatch.setattr("akshare.fund_etf_hist_em", lambda **kwargs: _etf_history())

    batch = fetch_core_etfs(
        etf_configs=[
            {"symbol": "510300", "canonical_symbol": "510300.SH", "name": "沪深300ETF"}
        ],
        now=datetime(2026, 7, 23, 16, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert entered == [True]
    assert batch.items[0].symbol == "510300.SH"


def test_fetch_core_etfs_uses_auditable_fallback_when_primary_is_empty():
    dates = pd.bdate_range("2026-06-26", periods=21)
    fallback = pd.DataFrame(
        {
            "日期": dates,
            "成交额": [100.0] * 20 + [130.0],
        }
    )
    fallback.attrs["source"] = "akshare_fund_etf_hist_sina"
    fallback_calls = []

    batch = fetch_core_etfs(
        etf_configs=[
            {
                "symbol": "510300",
                "canonical_symbol": "510300.SH",
                "name": "沪深300ETF",
            }
        ],
        hist_fetcher=lambda **_: pd.DataFrame(),
        fallback_hist_fetcher=lambda **kwargs: (
            fallback_calls.append(kwargs) or fallback
        ),
        now=datetime(
            2026,
            7,
            24,
            16,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    assert batch.failures == []
    assert batch.items[0].source == "akshare_fund_etf_hist_sina"
    assert batch.items[0].status == "active"
    assert fallback_calls[0]["symbol"] == "510300"


def test_fetch_core_etfs_uses_fallback_when_primary_network_fails():
    fallback = _etf_history(latest_turnover=130.0)
    fallback.attrs["source"] = "akshare_fund_etf_hist_sina"

    def network_failure(**_):
        raise requests.ConnectionError("primary unavailable")

    batch = fetch_core_etfs(
        etf_configs=[
            {
                "symbol": "510300",
                "canonical_symbol": "510300.SH",
                "name": "沪深300ETF",
            }
        ],
        hist_fetcher=network_failure,
        fallback_hist_fetcher=lambda **_: fallback,
        now=datetime(
            2026,
            7,
            23,
            16,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    assert batch.failures == []
    assert batch.items[0].source == "akshare_fund_etf_hist_sina"


def test_fetch_core_etfs_does_not_hide_primary_programming_errors():
    fallback_called = False

    def programming_error(**_):
        raise TypeError("bad call contract")

    def fallback(**_):
        nonlocal fallback_called
        fallback_called = True
        return _etf_history()

    with pytest.raises(TypeError, match="bad call contract"):
        fetch_core_etfs(
            etf_configs=[
                {
                    "symbol": "510300",
                    "canonical_symbol": "510300.SH",
                    "name": "沪深300ETF",
                }
            ],
            hist_fetcher=programming_error,
            fallback_hist_fetcher=fallback,
            now=datetime(
                2026,
                7,
                23,
                16,
                0,
                tzinfo=timezone(timedelta(hours=8)),
            ),
        )

    assert fallback_called is False
