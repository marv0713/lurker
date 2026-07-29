from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
import requests

from lurker.ingest.temperature_history import collect_temperature_replay
from lurker.ingest.temperature_history import (
    MarketFlowHistorySchemaError,
    fetch_market_flow_history,
    normalize_sina_etf_history,
)
from lurker.ingest.flows import normalize_akshare_margin_histories


class _HistoryResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _HistorySession:
    def __init__(self, payload):
        self.payload = payload
        self.trust_env = True
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _HistoryResponse(self.payload)


def test_market_history_uses_original_endpoint_without_environment_proxy():
    session = _HistorySession(
        {
            "data": {
                "klines": [
                    "2026-07-27,10,1,2,3,4,0,0,0,0,0,3600,1,12000,2",
                    "2026-07-28,-10,-1,-2,-3,-4,0,0,0,0,0,3500,-1,11000,-2",
                ]
            }
        }
    )

    result = fetch_market_flow_history(session=session)

    assert session.trust_env is False
    requested_url, kwargs = session.calls[0]
    assert requested_url.startswith("https://push2his.eastmoney.com/")
    assert "push2delay" not in requested_url
    assert kwargs["timeout"] == 30
    assert result.attrs["source"] == "eastmoney_market_flow_history"
    assert result.iloc[-1]["主力净流入-净额"] == -10.0


def test_market_history_rejects_malformed_kline_rows():
    session = _HistorySession(
        {"data": {"klines": ["2026-07-28,1,2"]}}
    )

    with pytest.raises(MarketFlowHistorySchemaError, match="15 fields"):
        fetch_market_flow_history(session=session)


def test_collect_temperature_replay_aligns_sources_and_uses_etf_warmup():
    history_dates = [
        date(2026, 3, 26) + timedelta(days=offset)
        for offset in range(30)
        if (date(2026, 3, 26) + timedelta(days=offset)).weekday() < 5
    ]
    output_date = history_dates[20]
    market_frame = pd.DataFrame(
        {
            "日期": [output_date],
            "今日主力净流入-净额": [10.0],
            "今日超大单净流入-净额": [5.0],
        }
    )
    etf_frame = pd.DataFrame(
        {
            "日期": history_dates[:21],
            "成交额": [100.0] * 20 + [130.0],
        }
    )

    margin_calls = []

    def margin_fetcher(*, trade_date):
        margin_calls.append(trade_date)
        balance = 1000.0 if trade_date < output_date.strftime("%Y%m%d") else 1010.0
        return pd.DataFrame(
            {
                "trade_date": [trade_date],
                "rzye": [balance],
                "rqye": [0.0],
                "rzrqye": [balance],
            }
        )

    records = collect_temperature_replay(
        etf_configs=[
            {
                "symbol": "510300",
                "canonical_symbol": "510300.SH",
                "name": "沪深300ETF",
            }
        ],
        etf_start=history_dates[0].isoformat(),
        margin_start=(output_date - timedelta(days=1)).isoformat(),
        output_start=output_date.isoformat(),
        output_end=output_date.isoformat(),
        market_flow_fetcher=lambda: market_frame,
        etf_history_fetcher=lambda **_: etf_frame,
        margin_fetcher=margin_fetcher,
        is_trading_day=lambda _: True,
    )

    assert len(records) == 1
    record = records[0]
    assert record["date"] == output_date.isoformat()
    assert record["market_flow"]["main_net_inflow"] == 10.0
    item = record["core_etfs"]["items"][0]
    assert item["trade_date"] == output_date.isoformat()
    assert item["avg_turnover_20d"] == 100.0
    assert item["turnover_expansion"] == 1.3
    assert item["status"] == "active"
    assert record["margin"]["margin_balance_change"] == 10.0
    assert margin_calls == [
        (output_date - timedelta(days=1)).strftime("%Y%m%d"),
        output_date.strftime("%Y%m%d"),
    ]


def test_collect_temperature_replay_keeps_day_when_all_sources_missing():
    records = collect_temperature_replay(
        etf_configs=[
            {
                "symbol": "510300",
                "canonical_symbol": "510300.SH",
                "name": "沪深300ETF",
            }
        ],
        etf_start="2026-04-01",
        margin_start="2026-04-23",
        output_start="2026-04-24",
        output_end="2026-04-24",
        market_flow_fetcher=lambda: pd.DataFrame(),
        etf_history_fetcher=lambda **_: pd.DataFrame(),
        margin_fetcher=lambda **_: pd.DataFrame(),
        is_trading_day=lambda _: True,
    )

    record = records[0]
    assert record["market_flow"]["availability"] == "unknown"
    assert record["core_etfs"]["failures"] == [
        {"symbol": "510300.SH", "reason": "no ETF history for replay date"}
    ]
    assert record["margin"]["availability"] == "unknown"


def test_normalize_sina_etf_history_maps_date_and_amount_columns():
    raw = pd.DataFrame(
        {
            "date": [date(2026, 4, 24)],
            "amount": [123.0],
            "close": [4.2],
        }
    )

    result = normalize_sina_etf_history(raw)

    assert list(result.columns) == ["日期", "成交额"]
    assert result.iloc[0].to_dict() == {
        "日期": date(2026, 4, 24),
        "成交额": 123.0,
    }
    assert result.attrs["source"] == "akshare_fund_etf_hist_sina"


def test_normalize_akshare_margin_histories_combines_sh_and_sz():
    sh = pd.DataFrame(
        {
            "日期": [date(2026, 4, 23), date(2026, 4, 24)],
            "融资余额": [100.0, 110.0],
            "融券余额": [10.0, 10.0],
            "融资融券余额": [110.0, 120.0],
        }
    )
    sz = pd.DataFrame(
        {
            "日期": [date(2026, 4, 23), date(2026, 4, 24)],
            "融资余额": [200.0, 205.0],
            "融券余额": [20.0, 20.0],
            "融资融券余额": [220.0, 225.0],
        }
    )

    result = normalize_akshare_margin_histories(sh, sz)

    assert result["2026-04-24"] == {
        "trade_date": "20260424",
        "financing_balance": 315.0,
        "securities_lending_balance": 30.0,
        "margin_balance": 345.0,
        "margin_balance_change": 15.0,
        "availability": "fresh",
        "source": "akshare_jin10_margin_sh_sz",
    }


def test_collect_temperature_replay_propagates_injected_etf_programming_error():
    def programming_error(**_):
        raise TypeError("bad ETF contract")

    with pytest.raises(TypeError, match="bad ETF contract"):
        collect_temperature_replay(
            etf_configs=[
                {
                    "symbol": "510300",
                    "canonical_symbol": "510300.SH",
                    "name": "沪深300ETF",
                }
            ],
            etf_start="2026-04-01",
            margin_start="2026-04-23",
            output_start="2026-04-24",
            output_end="2026-04-24",
            market_flow_fetcher=lambda: pd.DataFrame(),
            etf_history_fetcher=programming_error,
            margin_fetcher=lambda **_: pd.DataFrame(),
            is_trading_day=lambda _: True,
        )


def test_collect_temperature_replay_propagates_injected_margin_programming_error():
    def programming_error(**_):
        raise TypeError("bad margin contract")

    with pytest.raises(TypeError, match="bad margin contract"):
        collect_temperature_replay(
            etf_configs=[
                {
                    "symbol": "510300",
                    "canonical_symbol": "510300.SH",
                    "name": "沪深300ETF",
                }
            ],
            etf_start="2026-04-01",
            margin_start="2026-04-23",
            output_start="2026-04-24",
            output_end="2026-04-24",
            market_flow_fetcher=lambda: pd.DataFrame(),
            etf_history_fetcher=lambda **_: pd.DataFrame(),
            margin_fetcher=programming_error,
            is_trading_day=lambda _: True,
        )


def test_default_etf_history_falls_back_on_recoverable_network_error(monkeypatch):
    sina = pd.DataFrame(
        {
            "date": pd.bdate_range(end="2026-04-24", periods=21),
            "amount": [100.0] * 20 + [130.0],
        }
    )
    monkeypatch.setattr(
        "akshare.fund_etf_hist_em",
        lambda **_: (_ for _ in ()).throw(requests.ConnectionError("offline")),
    )
    monkeypatch.setattr("akshare.fund_etf_hist_sina", lambda **_: sina)

    records = collect_temperature_replay(
        etf_configs=[
            {
                "symbol": "510300",
                "canonical_symbol": "510300.SH",
                "name": "沪深300ETF",
            }
        ],
        etf_start="2026-03-27",
        margin_start="2026-04-23",
        output_start="2026-04-24",
        output_end="2026-04-24",
        market_flow_fetcher=lambda: pd.DataFrame(),
        margin_fetcher=lambda **_: pd.DataFrame(),
        is_trading_day=lambda _: True,
    )

    assert records[0]["core_etfs"]["failures"] == []
    assert records[0]["core_etfs"]["items"][0]["source"] == (
        "akshare_fund_etf_hist_sina"
    )
