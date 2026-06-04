import pandas as pd

from lurker.ingest.flows import (
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
    raw = pd.DataFrame({"今日主力净流入-净额": [1.0], "今日超大单净流入-净额": [2.0]})

    result = normalize_market_flow_frame(raw)

    assert result["main_net_inflow"] == 1.0
    assert result["super_large_net_inflow"] == 2.0
