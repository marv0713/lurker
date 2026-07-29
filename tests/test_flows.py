import importlib
from datetime import date

import pandas as pd
import lurker.ingest.flows as flows_module

from lurker.ingest.flows import (
    fetch_margin,
    fetch_stock_flows,
    normalize_akshare_margin_histories,
    normalize_margin_frame,
    normalize_market_flow_frame,
    normalize_sector_flow_frame,
    normalize_stock_flow_frame,
)


def test_akshare_requests_default_to_direct_connection(monkeypatch):
    monkeypatch.delenv("AKSHARE_PROXY", raising=False)

    reloaded = importlib.reload(flows_module)

    assert reloaded._AKSHARE_PROXIES == {}


def test_akshare_requests_honor_explicit_proxy(monkeypatch):
    monkeypatch.setenv("AKSHARE_PROXY", "http://127.0.0.1:7897")

    reloaded = importlib.reload(flows_module)

    assert reloaded._AKSHARE_PROXIES == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }


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


def test_normalize_margin_frame_uses_latest_trade_date_only():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260604", "20260604", "20260605", "20260605"],
            "rzye": [100.0, 200.0, 300.0, 400.0],
            "rqye": [10.0, 20.0, 30.0, 40.0],
            "rzrqye": [110.0, 220.0, 330.0, 440.0],
        }
    )

    result = normalize_margin_frame(raw, previous_margin_balance=330.0)

    assert result["trade_date"] == "20260605"
    assert result["financing_balance"] == 700.0
    assert result["securities_lending_balance"] == 70.0
    assert result["margin_balance"] == 770.0
    assert result["margin_balance_change"] == 440.0


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


def test_normalize_margin_frame_skips_change_for_same_trade_date():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260604"],
            "rzye": [100.0],
            "rqye": [10.0],
            "rzrqye": [110.0],
        }
    )

    result = normalize_margin_frame(
        raw,
        previous_margin_balance=110.0,
        previous_trade_date="20260604",
    )

    assert result["margin_balance"] == 110.0
    assert "margin_balance_change" not in result


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
    monkeypatch.setattr(
        "lurker.ingest.flows.fetch_akshare_margin_latest",
        lambda: (_ for _ in ()).throw(RuntimeError("AkShare offline")),
        raising=False,
    )
    
    res_fallback = fetch_margin(token="dummy_token", cache_path=cache_path)
    assert res_fallback["trade_date"] == "20260604"
    assert res_fallback["financing_balance"] == 100.0

    # 3. Failure case without cache: raises original exception
    non_existent_cache = tmp_path / "non_existent.json"
    with pytest.raises(RuntimeError) as exc_info:
        fetch_margin(token="dummy_token", cache_path=non_existent_cache)
    assert "Rate limit exceeded" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 1.7 ingest 缺失值保真
# ---------------------------------------------------------------------------


def test_market_flow_normalizer_preserves_missing_main_flow_as_none():
    """缺失主力净流入不能变成 0.0。"""
    raw = pd.DataFrame({"超大单净流入-净额": [100.0]})
    result = normalize_market_flow_frame(raw)
    # Missing column → None (not 0.0)
    assert result.get("main_net_inflow") is None


def test_market_flow_normalizer_preserves_real_zero():
    """真实数值 0 保留为 0.0，供方向层判为 neutral。"""
    raw = pd.DataFrame({"主力净流入-净额": [0.0], "超大单净流入-净额": [0.0]})
    result = normalize_market_flow_frame(raw)
    assert result["main_net_inflow"] == 0.0
    assert result["super_large_net_inflow"] == 0.0


def test_market_flow_normalizer_rejects_non_finite_values():
    raw = pd.DataFrame(
        {
            "主力净流入-净额": [float("inf")],
            "超大单净流入-净额": [float("-inf")],
        }
    )

    result = normalize_market_flow_frame(raw)

    assert result["main_net_inflow"] is None
    assert result["super_large_net_inflow"] is None


def test_market_flow_normalizer_includes_latest_trade_date():
    """标准化结果携带提供方最新交易日期。"""
    raw = pd.DataFrame({
        "日期": ["2026-06-03", "2026-06-04"],
        "主力净流入-净额": [-1.0, 10.0],
        "超大单净流入-净额": [-2.0, 20.0],
    })
    result = normalize_market_flow_frame(raw)
    # Should include the trade_date field for freshness checks
    assert "trade_date" in result
    assert result["trade_date"] == "2026-06-04"


def test_margin_cache_fallback_marked_stale_cache(monkeypatch, tmp_path):
    """Tushare 失败回退缓存时，availability 应标记为 stale_cache。"""
    # This tests the fetch_margin cache fallback behavior.
    # When Tushare fails and we fall back to cache, the availability must be
    # marked so that classify_margin_signal() returns "unknown".
    import json
    import sys
    from unittest.mock import MagicMock
    from lurker.ingest.flows import fetch_margin

    mock_ts = MagicMock()
    mock_pro = MagicMock()
    mock_ts.pro_api.return_value = mock_pro
    # Simulate rate limit failure
    mock_pro.margin.side_effect = RuntimeError("Rate limit exceeded")
    monkeypatch.setattr(
        "lurker.ingest.flows.fetch_akshare_margin_latest",
        lambda: (_ for _ in ()).throw(RuntimeError("AkShare offline")),
        raising=False,
    )

    # Create a cache file with prior data
    cache = {
        "trade_date": "20260603",
        "financing_balance": 100.0,
        "securities_lending_balance": 10.0,
        "margin_balance": 110.0,
        "margin_balance_change": 20.0,
    }
    cache_path = tmp_path / "stale_margin_cache.json"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    # Replace module-level tushare reference
    monkeypatch.setitem(sys.modules, "tushare", mock_ts)

    result = fetch_margin(token="dummy_token", cache_path=cache_path)
    # Fallback to cache: data is present but should be marked stale
    assert result.get("availability") == "stale_cache"
    # The data itself is still available for display purposes
    assert result["trade_date"] == "20260603"


def _akshare_margin_frames():
    sh = pd.DataFrame(
        {
            "日期": [date(2026, 7, 24), date(2026, 7, 27)],
            "融资余额": [100.0, 110.0],
            "融券余额": [10.0, 10.0],
            "融资融券余额": [110.0, 120.0],
        }
    )
    sz = pd.DataFrame(
        {
            "日期": [date(2026, 7, 24), date(2026, 7, 27)],
            "融资余额": [200.0, 205.0],
            "融券余额": [20.0, 20.0],
            "融资融券余额": [220.0, 225.0],
        }
    )
    return sh, sz


def test_fetch_margin_uses_akshare_when_tushare_permission_is_denied(
    monkeypatch,
    tmp_path,
):
    import sys
    from unittest.mock import MagicMock

    mock_ts = MagicMock()
    mock_pro = MagicMock()
    mock_ts.pro_api.return_value = mock_pro
    mock_pro.margin.side_effect = RuntimeError(
        "抱歉，您没有接口(margin)访问权限"
    )
    monkeypatch.setitem(sys.modules, "tushare", mock_ts)
    sh, sz = _akshare_margin_frames()
    monkeypatch.setattr(
        "lurker.ingest.flows._fetch_akshare_margin_frames",
        lambda: (sh, sz),
        raising=False,
    )

    result = fetch_margin(
        token="no-margin-permission",
        cache_path=tmp_path / "margin.json",
    )

    assert result["source"] == "akshare_jin10_margin_sh_sz"
    assert result["trade_date"] == "20260727"
    assert result["margin_balance"] == 345.0
    assert result["margin_balance_change"] == 15.0
    assert result["availability"] == "fresh"


def test_fetch_margin_uses_akshare_without_tushare_token(monkeypatch, tmp_path):
    sh, sz = _akshare_margin_frames()
    monkeypatch.setattr(
        "lurker.ingest.flows._fetch_akshare_margin_frames",
        lambda: (sh, sz),
        raising=False,
    )

    result = fetch_margin(token="", cache_path=tmp_path / "margin.json")

    assert result["source"] == "akshare_jin10_margin_sh_sz"
    assert result["availability"] == "fresh"


def test_normalize_akshare_margin_requires_both_exchanges_for_date():
    sh, sz = _akshare_margin_frames()
    sz = sz.iloc[:1].copy()

    result = normalize_akshare_margin_histories(sh, sz)

    assert set(result) == {"2026-07-24"}


def test_margin_normalizer_does_not_fabricate_change_from_all_null_values():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260723"],
            "rzye": [None],
            "rqye": [None],
            "rzrqye": [None],
        }
    )

    result = normalize_margin_frame(
        raw,
        previous_margin_balance=100.0,
        previous_trade_date="20260722",
    )

    assert result["financing_balance"] is None
    assert result["securities_lending_balance"] is None
    assert result["margin_balance"] is None
    assert "margin_balance_change" not in result
    assert result["availability"] == "fresh"


def test_fetch_margin_preserves_same_day_cached_change(monkeypatch, tmp_path):
    import json
    import sys
    from unittest.mock import MagicMock

    cached = {
        "trade_date": "20260723",
        "financing_balance": 90.0,
        "securities_lending_balance": 10.0,
        "margin_balance": 100.0,
        "margin_balance_change": 12.0,
        "availability": "fresh",
    }
    cache_path = tmp_path / "margin.json"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")

    mock_ts = MagicMock()
    mock_ts.pro_api.return_value.margin.return_value = pd.DataFrame(
        {
            "trade_date": ["20260723"],
            "rzye": [90.0],
            "rqye": [10.0],
            "rzrqye": [100.0],
        }
    )
    monkeypatch.setitem(sys.modules, "tushare", mock_ts)

    result = fetch_margin(token="token", cache_path=cache_path)

    assert result["margin_balance_change"] == 12.0


def test_fetch_margin_does_not_treat_null_cached_balance_as_zero(monkeypatch, tmp_path):
    import json
    import sys
    from unittest.mock import MagicMock

    cache_path = tmp_path / "margin.json"
    cache_path.write_text(
        json.dumps({"trade_date": "20260722", "margin_balance": None}),
        encoding="utf-8",
    )
    mock_ts = MagicMock()
    mock_ts.pro_api.return_value.margin.return_value = pd.DataFrame(
        {
            "trade_date": ["20260723"],
            "rzye": [90.0],
            "rqye": [10.0],
            "rzrqye": [100.0],
        }
    )
    monkeypatch.setitem(sys.modules, "tushare", mock_ts)

    result = fetch_margin(token="token", cache_path=cache_path)

    assert "margin_balance_change" not in result
