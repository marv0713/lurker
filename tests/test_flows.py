import pandas as pd

from lurker.ingest.flows import (
    fetch_stock_flows,
    normalize_margin_frame,
    normalize_market_flow_frame,
    normalize_sector_flow_frame,
    normalize_stock_flow_frame,
)


def test_normalize_stock_flow_frame_maps_eastmoney_columns():
    raw = pd.DataFrame(
        {
            "代码": ["300308"],
            "名称": ["中际旭创"],
            "今日主力净流入-净额": [100000000],
            "今日超大单净流入-净额": [50000000],
            "5日主力净流入-净额": [300000000],
            "10日主力净流入-净额": [400000000],
        }
    )

    result = normalize_stock_flow_frame(raw)

    assert result[0]["symbol"] == "300308.SZ"
    assert result[0]["name"] == "中际旭创"
    assert result[0]["main_net_inflow"] == 100000000
    assert result[0]["super_large_net_inflow"] == 50000000
    assert result[0]["main_net_inflow_5d"] == 300000000
    assert result[0]["main_net_inflow_10d"] == 400000000


def test_normalize_sector_flow_frame_maps_rankings():
    raw = pd.DataFrame({"名称": ["通信设备"], "今日主力净流入-净额": [200000000]})

    result = normalize_sector_flow_frame(raw, category="industry")

    assert result == [
        {"name": "通信设备", "category": "industry", "main_net_inflow": 200000000, "rank": 1}
    ]


def test_normalize_margin_frame_sums_exchanges():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260604", "20260604"],
            "rzye": [100.0, 200.0],
            "rqye": [10.0, 20.0],
            "rzrqye": [110.0, 220.0],
        }
    )

    result = normalize_margin_frame(raw)

    assert result["trade_date"] == "20260604"
    assert result["financing_balance"] == 300.0
    assert result["securities_lending_balance"] == 30.0
    assert result["margin_balance"] == 330.0


def test_normalize_market_flow_frame_keeps_known_fields():
    raw = pd.DataFrame({"主力净流入-净额": [1.0], "超大单净流入-净额": [2.0]})

    result = normalize_market_flow_frame(raw)

    assert result["main_net_inflow"] == 1.0
    assert result["super_large_net_inflow"] == 2.0


def test_normalize_market_flow_frame_uses_latest_date():
    raw = pd.DataFrame(
        {
            "日期": ["2026-06-03", "2026-06-04"],
            "主力净流入-净额": [-1.0, 10.0],
            "超大单净流入-净额": [-2.0, 20.0],
        }
    )

    result = normalize_market_flow_frame(raw)

    assert result["main_net_inflow"] == 10.0
    assert result["super_large_net_inflow"] == 20.0


def test_normalize_margin_frame_computes_change_when_previous_balance_provided():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260604"],
            "rzye": [100.0],
            "rqye": [10.0],
            "rzrqye": [110.0],
        }
    )

    result = normalize_margin_frame(raw, previous_margin_balance=90.0)

    assert result["margin_balance"] == 110.0
    assert result["margin_balance_change"] == 20.0


def test_fetch_stock_flows_merges_today_5d_and_10d_rankings(monkeypatch):
    calls = []

    def fake_rank(indicator):
        calls.append(indicator)
        if indicator == "今日":
            return pd.DataFrame(
                {
                    "代码": ["300308"],
                    "名称": ["中际旭创"],
                    "今日主力净流入-净额": [100.0],
                    "今日超大单净流入-净额": [50.0],
                }
            )
        if indicator == "5日":
            return pd.DataFrame({"代码": ["300308"], "5日主力净流入-净额": [300.0]})
        if indicator == "10日":
            return pd.DataFrame({"代码": ["300308"], "10日主力净流入-净额": [500.0]})
        raise AssertionError(indicator)

    monkeypatch.setattr("lurker.ingest.flows.ak.stock_individual_fund_flow_rank", fake_rank)

    result = fetch_stock_flows()

    assert calls == ["今日", "5日", "10日"]
    assert result[0]["symbol"] == "300308.SZ"
    assert result[0]["main_net_inflow"] == 100.0
    assert result[0]["super_large_net_inflow"] == 50.0
    assert result[0]["main_net_inflow_5d"] == 300.0
    assert result[0]["main_net_inflow_10d"] == 500.0


def test_importing_flows_does_not_monkeypatch_global_requests():
    import subprocess
    import sys

    code = (
        "import requests; "
        "orig_get = requests.get; "
        "orig_post = requests.post; "
        "import lurker.ingest.flows; "
        "raise SystemExit(0 if requests.get is orig_get and requests.post is orig_post else 1)"
    )
    result = subprocess.run([sys.executable, "-c", code], check=False)

    assert result.returncode == 0


def test_fetch_margin_cache_fallback(monkeypatch, tmp_path):
    import sys
    from unittest.mock import MagicMock
    import pytest
    from lurker.ingest.flows import fetch_margin

    mock_ts = MagicMock()
    mock_pro = MagicMock()
    mock_ts.pro_api.return_value = mock_pro
    
    monkeypatch.setitem(sys.modules, "tushare", mock_ts)

    # 1. Success case: returns API data and writes cache
    raw_df = pd.DataFrame(
        {
            "trade_date": ["20260604"],
            "rzye": [100.0],
            "rqye": [10.0],
            "rzrqye": [110.0],
        }
    )
    mock_pro.margin.return_value = raw_df

    cache_path = tmp_path / "margin_cache.json"
    res = fetch_margin(token="dummy_token", cache_path=cache_path)
    assert res["trade_date"] == "20260604"
    assert res["financing_balance"] == 100.0
    assert cache_path.exists()

    # 2. Failure case with cache: falls back to cache
    mock_pro.margin.side_effect = RuntimeError("Rate limit exceeded")
    
    res_fallback = fetch_margin(token="dummy_token", cache_path=cache_path)
    assert res_fallback["trade_date"] == "20260604"
    assert res_fallback["financing_balance"] == 100.0

    # 3. Failure case without cache: raises original exception
    non_existent_cache = tmp_path / "non_existent.json"
    with pytest.raises(RuntimeError) as exc_info:
        fetch_margin(token="dummy_token", cache_path=non_existent_cache)
    assert "Rate limit exceeded" in str(exc_info.value)
