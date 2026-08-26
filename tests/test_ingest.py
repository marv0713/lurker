from datetime import date

import pandas as pd
import pytest

from lurker.ingest.constituents import (
    format_cn_stock_symbol,
    load_resolved_theme_seed_symbols,
    load_theme_seed_sources,
    normalize_cn_index_constituents,
    resolve_cn_index_constituents,
    resolve_cn_etf_constituents,
)
from lurker.ingest.prices import (
    PRICE_COLUMNS,
    fetch_akshare_cn_prices,
    fetch_cn_prices,
    fetch_hithink_cn_prices,
    fetch_watchlist_history,
    fetch_yfinance_prices,
    normalize_baostock_cn_price_frame,
    normalize_cn_index_price_frame,
    normalize_cn_price_frame,
    normalize_hithink_cn_price_frame,
    normalize_price_frame,
    normalize_tushare_cn_price_frame,
    to_akshare_symbol,
    to_baostock_symbol,
    to_yfinance_symbol,
)


def test_yfinance_price_window_is_anchored_to_explicit_report_date(monkeypatch):
    calls = []

    def download(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return pd.DataFrame(
            {
                "Date": ["2024-08-12"],
                "Open": [10],
                "High": [11],
                "Low": [9],
                "Close": [10],
                "Adj Close": [10],
                "Volume": [100],
            }
        )

    monkeypatch.setattr("lurker.ingest.prices.yf.download", download)

    fetch_yfinance_prices("00700.HK", "2y", end_date=date(2026, 8, 10))

    _, kwargs = calls[0]
    assert kwargs["start"] == "2024-08-10"
    assert kwargs["end"] == "2026-08-11"
    assert "period" not in kwargs


def test_akshare_price_window_is_anchored_to_explicit_report_date(monkeypatch):
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            {
                "日期": ["2024-08-12"],
                "开盘": [10],
                "最高": [11],
                "最低": [9],
                "收盘": [10],
                "成交量": [100],
            }
        )

    monkeypatch.setattr("lurker.ingest.prices.ak.stock_zh_a_hist", fetch)

    fetch_akshare_cn_prices("300308.SZ", "2y", end_date=date(2026, 8, 10))

    assert calls[0]["start_date"] == "20240810"
    assert calls[0]["end_date"] == "20260810"


def test_normalize_price_frame_outputs_required_columns():
    raw = pd.DataFrame(
        {
            "Date": ["2026-05-15"],
            "Open": [100],
            "High": [110],
            "Low": [98],
            "Close": [108],
            "Adj Close": [108],
            "Volume": [1000000],
        }
    )

    result = normalize_price_frame(raw, symbol="NVDA")

    assert list(result.columns) == [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]
    assert result.iloc[0]["symbol"] == "NVDA"


def test_normalize_price_frame_flattens_yfinance_multiindex_columns():
    raw = pd.DataFrame(
        [[108, 108, 110, 98, 100, 1000000]],
        index=pd.to_datetime(["2026-05-15"]),
        columns=pd.MultiIndex.from_tuples(
            [
                ("Adj Close", "NVDA"),
                ("Close", "NVDA"),
                ("High", "NVDA"),
                ("Low", "NVDA"),
                ("Open", "NVDA"),
                ("Volume", "NVDA"),
            ],
            names=["Price", "Ticker"],
        ),
    )

    result = normalize_price_frame(raw, symbol="NVDA")

    assert list(result.columns) == [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]
    assert result.iloc[0]["adj_close"] == 108


@pytest.mark.parametrize(
    ("configured", "yahoo"),
    [
        ("7.HK", "0007.HK"),
        ("700.HK", "0700.HK"),
        ("00700.HK", "0700.HK"),
        ("09988.HK", "9988.HK"),
        ("12345.HK", "12345.HK"),
    ],
)
def test_hk_symbols_are_normalized_to_yahoo_code_width(configured, yahoo):
    assert to_yfinance_symbol(configured) == yahoo


def test_to_yfinance_symbol_normalizes_five_digit_hk_codes():
    assert to_yfinance_symbol("01801.HK") == "1801.HK"
    assert to_yfinance_symbol("06160.HK") == "6160.HK"
    assert to_yfinance_symbol("0700.HK") == "0700.HK"
    assert to_yfinance_symbol("NVDA") == "NVDA"


def test_to_akshare_symbol_strips_a_share_exchange_suffix():
    assert to_akshare_symbol("300308.SZ") == "300308"
    assert to_akshare_symbol("688235.SH") == "688235"
    assert to_akshare_symbol("600519") == "600519"


def test_to_baostock_symbol_converts_exchange_suffix():
    assert to_baostock_symbol("300308.SZ") == "sz.300308"
    assert to_baostock_symbol("688235.SH") == "sh.688235"
    assert to_baostock_symbol("600519") == "sh.600519"


def test_normalize_cn_price_frame_outputs_required_columns():
    raw = pd.DataFrame(
        {
            "日期": ["2026-05-15"],
            "开盘": [100],
            "最高": [110],
            "最低": [98],
            "收盘": [108],
            "成交量": [1000000],
        }
    )

    result = normalize_cn_price_frame(raw, symbol="300308.SZ")

    assert list(result.columns) == [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]
    assert result.iloc[0]["symbol"] == "300308.SZ"
    assert result.iloc[0]["adj_close"] == 108


def test_normalize_tushare_cn_price_frame_outputs_required_columns():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260515"],
            "open": [100],
            "high": [110],
            "low": [98],
            "close": [108],
            "vol": [1000],
        }
    )

    result = normalize_tushare_cn_price_frame(raw, symbol="300308.SZ")

    assert list(result.columns) == [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]
    assert result.iloc[0]["symbol"] == "300308.SZ"
    assert result.iloc[0]["adj_close"] == 108
    assert result.iloc[0]["volume"] == 1000


def test_normalize_cn_price_frame_carries_amount_when_present():
    raw = pd.DataFrame(
        {
            "日期": ["2026-05-15"],
            "开盘": [100],
            "最高": [110],
            "最低": [98],
            "收盘": [108],
            "成交量": [1000000],
            "成交额": [108000000],
        }
    )

    result = normalize_cn_price_frame(raw, symbol="300308.SZ")

    assert list(result.columns) == [*PRICE_COLUMNS, "amount"]
    assert result.iloc[0]["amount"] == 108_000_000


def test_normalize_tushare_cn_price_frame_converts_amount_to_yuan():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260515"],
            "open": [100],
            "high": [110],
            "low": [98],
            "close": [108],
            "vol": [1000],
            "amount": [108000],
        }
    )

    result = normalize_tushare_cn_price_frame(raw, symbol="300308.SZ")

    assert list(result.columns) == [*PRICE_COLUMNS, "amount"]
    assert result.iloc[0]["amount"] == 108_000_000


def test_tushare_amount_stays_attached_to_date_after_sorting():
    raw = pd.DataFrame(
        {
            "trade_date": ["20260516", "20260515"],
            "open": [20, 10],
            "high": [21, 11],
            "low": [19, 9],
            "close": [20, 10],
            "vol": [2, 1],
            "amount": [200, 100],
        }
    )

    result = normalize_tushare_cn_price_frame(raw, symbol="000001.SZ")

    assert list(result["trade_date"]) == [date(2026, 5, 15), date(2026, 5, 16)]
    assert list(result["amount"]) == [100_000.0, 200_000.0]


def test_normalize_baostock_cn_price_frame_outputs_required_columns():
    raw = pd.DataFrame(
        {
            "date": ["2026-05-15"],
            "open": ["100"],
            "high": ["110"],
            "low": ["98"],
            "close": ["108"],
            "volume": ["1000000"],
        }
    )

    result = normalize_baostock_cn_price_frame(raw, symbol="300308.SZ")

    assert list(result.columns) == [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]
    assert result.iloc[0]["symbol"] == "300308.SZ"
    assert result.iloc[0]["adj_close"] == 108
    assert result.iloc[0]["volume"] == 1000000


def test_fetch_cn_prices_uses_slow_fallback_order():
    calls = []
    raw = pd.DataFrame(
        {
            "date": ["2026-05-15"],
            "open": ["100"],
            "high": ["110"],
            "low": ["98"],
            "close": ["108"],
            "volume": ["1000000"],
        }
    )

    def tushare_fetcher(symbol: str, period: str) -> pd.DataFrame:
        calls.append(("tushare", symbol, period))
        raise RuntimeError("no token")

    def akshare_fetcher(symbol: str, period: str) -> pd.DataFrame:
        calls.append(("akshare", symbol, period))
        raise RuntimeError("eastmoney disconnected")

    def baostock_fetcher(symbol: str, period: str) -> pd.DataFrame:
        calls.append(("baostock", symbol, period))
        return normalize_baostock_cn_price_frame(raw, symbol=symbol)

    result = fetch_cn_prices(
        "300308.SZ",
        "6mo",
        fetchers=[tushare_fetcher, akshare_fetcher, baostock_fetcher],
        sleep_seconds=0,
    )

    assert calls == [
        ("tushare", "300308.SZ", "6mo"),
        ("akshare", "300308.SZ", "6mo"),
        ("baostock", "300308.SZ", "6mo"),
    ]
    assert result.iloc[0]["symbol"] == "300308.SZ"


def test_normalize_cn_index_price_frame_uses_adjusted_close_contract():
    raw = pd.DataFrame(
        {
            "日期": ["2026-07-17", "2026-07-20"],
            "开盘": [4000.0, 4010.0],
            "最高": [4020.0, 4030.0],
            "最低": [3990.0, 4000.0],
            "收盘": [4010.0, 4025.0],
            "成交量": [100, 120],
        }
    )

    result = normalize_cn_index_price_frame(raw, symbol="000300.SH")

    assert list(result.columns) == PRICE_COLUMNS
    assert result.iloc[-1]["adj_close"] == 4025.0
    assert str(result.iloc[-1]["trade_date"]) == "2026-07-20"


def test_normalize_cn_index_price_frame_fails_loudly_when_required_field_is_missing():
    raw = pd.DataFrame(
        {
            "日期": ["2026-07-20"],
            "开盘": [4010.0],
            "最高": [4030.0],
            "最低": [4000.0],
            "收盘": [4025.0],
        }
    )

    with pytest.raises(ValueError, match="missing CN index price columns: volume"):
        normalize_cn_index_price_frame(raw, symbol="000300.SH")


def test_fetch_watchlist_history_dispatches_cn_benchmark_separately():
    calls = []

    def stock_fetcher(symbol, period):
        calls.append(("stock", symbol, period))
        return pd.DataFrame()

    def benchmark_fetcher(symbol, period):
        calls.append(("benchmark", symbol, period))
        return pd.DataFrame()

    fetch_watchlist_history(
        symbol="000300.SH",
        market="cn",
        period="2y",
        is_benchmark=True,
        stock_fetcher=stock_fetcher,
        cn_benchmark_fetcher=benchmark_fetcher,
    )

    assert calls == [("benchmark", "000300.SH", "2y")]


def test_load_theme_seed_sources_exposes_unexpanded_boundaries(tmp_path):
    themes_path = tmp_path / "themes.yaml"
    themes_path.write_text(
        """
themes:
  - id: ai_infra
    markets:
      cn:
        seed_indexes: [科创 50]
        seed_etfs: [人工智能 ETF]
        seed_symbols: [300308.SZ]
""",
        encoding="utf-8",
    )

    result = load_theme_seed_sources(themes_path)

    assert result["cn"]["symbols"] == ["300308.SZ"]
    assert result["cn"]["indexes"] == ["科创 50"]
    assert result["cn"]["etfs"] == ["人工智能 ETF"]


def test_format_cn_stock_symbol_adds_exchange_suffix():
    assert format_cn_stock_symbol("300308") == "300308.SZ"
    assert format_cn_stock_symbol("688235") == "688235.SH"
    assert format_cn_stock_symbol("430047") == "430047.BJ"


def test_normalize_cn_index_constituents_handles_csindex_columns():
    raw = pd.DataFrame(
        {
            "成分券代码": ["000001", "600519"],
            "成分券名称": ["平安银行", "贵州茅台"],
            "交易所": ["深圳证券交易所", "上海证券交易所"],
        }
    )

    result = normalize_cn_index_constituents(raw)

    assert result == ["000001.SZ", "600519.SH"]


def test_normalize_cn_index_constituents_handles_generic_code_columns():
    raw = pd.DataFrame({"品种代码": ["300308", "688235"], "品种名称": ["中际旭创", "百济神州"]})

    result = normalize_cn_index_constituents(raw)

    assert result == ["300308.SZ", "688235.SH"]


def test_resolve_cn_index_constituents_uses_named_index_mapping():
    calls = []

    def csindex_fetcher(symbol: str) -> pd.DataFrame:
        calls.append(("csindex", symbol))
        return pd.DataFrame({"成分券代码": ["000001"], "交易所": ["深圳证券交易所"]})

    def generic_fetcher(symbol: str) -> pd.DataFrame:
        calls.append(("generic", symbol))
        return pd.DataFrame({"品种代码": ["300308"]})

    result = resolve_cn_index_constituents(
        "沪深 300",
        csindex_fetcher=csindex_fetcher,
        generic_fetcher=generic_fetcher,
    )

    assert result == ["000001.SZ"]
    assert calls == [("csindex", "000300")]


def test_load_resolved_theme_seed_symbols_expands_cn_indexes(tmp_path):
    themes_path = tmp_path / "themes.yaml"
    themes_path.write_text(
        """
themes:
  - id: ai_infra
    markets:
      cn:
        seed_indexes: [创业板指]
        seed_etfs: [人工智能 ETF]
        seed_symbols: [300308.SZ]
      us:
        seed_symbols: [NVDA]
""",
        encoding="utf-8",
    )

    result = load_resolved_theme_seed_symbols(
        themes_path,
        cn_index_resolver=lambda index_name: ["300502.SZ"] if index_name == "创业板指" else [],
        cn_etf_resolver=lambda etf_name: [],
    )

    assert result["cn"] == ["300308.SZ", "300502.SZ"]
    assert result["us"] == ["NVDA"]


def test_resolve_cn_etf_constituents_uses_latest_quarter_top_holdings():
    raw = pd.DataFrame(
        {
            "季度": ["2026Q1", "2026Q1", "2025Q4"],
            "股票代码": ["300308", "600519", "000001"],
        }
    )

    result = resolve_cn_etf_constituents(
        "人工智能 ETF",
        top_n=2,
        fetcher=lambda symbol: raw,
    )

    assert result == ["300308.SZ", "600519.SH"]


def test_load_resolved_theme_seed_symbols_expands_cn_etfs(tmp_path):
    themes_path = tmp_path / "themes.yaml"
    themes_path.write_text(
        """
themes:
  - id: ai_infra
    markets:
      cn:
        seed_indexes: []
        seed_etfs: [人工智能 ETF]
        seed_symbols: [300308.SZ]
""",
        encoding="utf-8",
    )

    result = load_resolved_theme_seed_symbols(
        themes_path,
        cn_index_resolver=lambda index_name: [],
        cn_etf_resolver=lambda etf_name: ["002230.SZ"] if etf_name == "人工智能 ETF" else [],
    )

    assert result["cn"] == ["300308.SZ", "002230.SZ"]


def _shanghai_ms(day: str) -> int:
    return int(pd.Timestamp(day, tz="Asia/Shanghai").timestamp() * 1000)


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_normalize_hithink_cn_price_frame_maps_fields_and_dates():
    raw = pd.DataFrame(
        {
            "date_ms": [_shanghai_ms("2024-08-28"), _shanghai_ms("2024-08-29")],
            "open_price": [100.0, 101.0],
            "high_price": [110.0, 111.0],
            "low_price": [98.0, 99.0],
            "close_price": [108.0, 109.0],
            "volume": [1000000, 1200000],
            "turnover": [108000000, 130000000],
        }
    )

    result = normalize_hithink_cn_price_frame(raw, symbol="300308.SZ")

    assert list(result.columns) == [*PRICE_COLUMNS, "amount"]
    assert str(result.iloc[0]["trade_date"]) == "2024-08-28"
    assert str(result.iloc[-1]["trade_date"]) == "2024-08-29"
    assert result.iloc[0]["symbol"] == "300308.SZ"
    assert result.iloc[0]["adj_close"] == 108.0
    assert result.iloc[-1]["close"] == 109.0
    assert result.iloc[-1]["amount"] == 130_000_000


def test_fetch_hithink_cn_prices_requires_token(monkeypatch):
    monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)

    with pytest.raises(ValueError, match="HITHINK_FINANCE_API_KEY is not set"):
        fetch_hithink_cn_prices("300308.SZ", "6mo")


def _hithink_bar(day: str) -> dict:
    return {
        "date_ms": _shanghai_ms(day),
        "open_price": 100.0,
        "high_price": 110.0,
        "low_price": 98.0,
        "close_price": 108.0,
        "volume": 1000000,
        "turnover": 108000000,
    }


def test_fetch_hithink_cn_prices_parses_envelope_and_builds_frame(monkeypatch):
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append((url, dict(params), dict(headers)))
        if params["offset"] == 0:
            data = {"timestamp": _shanghai_ms("2024-08-29"), "item": [_hithink_bar("2024-08-29")]}
        else:
            data = {"timestamp": _shanghai_ms("2024-08-29"), "item": []}
        return _FakeResponse({"code": 0, "message": "ok", "request_id": "r1", "data": data})

    monkeypatch.setattr("lurker.ingest.prices.requests.get", fake_get)

    result = fetch_hithink_cn_prices("300308.SZ", "6mo", token="test-key")

    assert len(calls) == 2
    url, params, headers = calls[0]
    assert url == "https://fuyao.aicubes.cn/api/a-share/prices/historical"
    assert params["thscode"] == "300308.SZ"
    assert params["interval"] == "1d"
    assert params["adjust"] == "forward"
    assert params["offset"] == 0
    assert calls[1][1]["offset"] == 1
    assert headers == {"X-api-key": "test-key"}
    assert result.iloc[0]["close"] == 108.0
    assert result.iloc[0]["amount"] == 108_000_000
    assert str(result.iloc[0]["trade_date"]) == "2024-08-29"


def test_fetch_hithink_cn_prices_paginates_until_short_page(monkeypatch):
    full_page = [_hithink_bar(f"2024-08-{day:02d}") for day in range(1, 11)]
    pages = [full_page, [_hithink_bar(f"2024-08-{day:02d}") for day in (11, 12, 13)], []]
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(params["offset"])
        return _FakeResponse(
            {"code": 0, "message": "ok", "request_id": "r", "data": {"timestamp": 1, "item": pages[params["offset"] // 10]}}
        )

    monkeypatch.setattr("lurker.ingest.prices.requests.get", fake_get)

    result = fetch_hithink_cn_prices("300308.SZ", "6mo", token="test-key")

    assert calls == [0, 10, 13]
    assert len(result) == 13


def test_fetch_hithink_cn_prices_rejects_truncated_pagination(monkeypatch):
    monkeypatch.setattr("lurker.ingest.prices._HITHINK_MAX_PAGES", 2)

    def fake_get(url, params, headers, timeout):
        day = 1 + params["offset"]
        return _FakeResponse(
            {
                "code": 0,
                "message": "ok",
                "request_id": "r",
                "data": {"timestamp": 1, "item": [_hithink_bar(f"2024-08-{day:02d}")]},
            }
        )

    monkeypatch.setattr("lurker.ingest.prices.requests.get", fake_get)

    with pytest.raises(RuntimeError, match="hithink pagination limit reached"):
        fetch_hithink_cn_prices("300308.SZ", "6mo", token="test-key")


def test_fetch_hithink_cn_prices_retries_on_rate_limit(monkeypatch):
    responses = iter(
        [
            _FakeResponse({"code": 4001, "message": "too fast", "request_id": "r", "data": None}),
            _FakeResponse(
                {
                    "code": 0,
                    "message": "ok",
                    "request_id": "r",
                    "data": {"timestamp": 1, "item": [_hithink_bar("2024-08-29")]},
                }
            ),
            _FakeResponse({"code": 0, "message": "ok", "request_id": "r", "data": {"timestamp": 1, "item": []}}),
        ]
    )
    monkeypatch.setattr("lurker.ingest.prices.time.sleep", lambda seconds: None)

    def fake_get(url, params, headers, timeout):
        return next(responses)

    monkeypatch.setattr("lurker.ingest.prices.requests.get", fake_get)

    result = fetch_hithink_cn_prices("300308.SZ", "6mo", token="test-key")

    assert result.iloc[0]["close"] == 108.0


def test_fetch_hithink_cn_prices_null_data_is_error(monkeypatch):
    def fake_get(url, params, headers, timeout):
        return _FakeResponse({"code": 0, "message": "ok", "request_id": "r", "data": None})

    monkeypatch.setattr("lurker.ingest.prices.requests.get", fake_get)

    with pytest.raises(RuntimeError, match="empty data payload"):
        fetch_hithink_cn_prices("300308.SZ", "6mo", token="test-key")


def test_fetch_cn_prices_skips_hithink_without_key(monkeypatch):
    monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
    calls = []

    def akshare_fetcher(symbol, period):
        calls.append(("akshare", symbol, period))
        return pd.DataFrame(
            {
                "trade_date": [date(2024, 8, 29)],
                "open": [100.0],
                "high": [110.0],
                "low": [98.0],
                "close": [108.0],
                "adj_close": [108.0],
                "volume": [1000000],
                "symbol": ["300308.SZ"],
            }
        )[PRICE_COLUMNS]

    result = fetch_cn_prices(
        "300308.SZ",
        "6mo",
        fetchers=[fetch_hithink_cn_prices, akshare_fetcher],
        sleep_seconds=0,
    )

    assert calls == [("akshare", "300308.SZ", "6mo")]
    assert result.iloc[0]["close"] == 108.0
