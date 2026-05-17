import pandas as pd

from lurker.ingest.constituents import (
    format_cn_stock_symbol,
    load_resolved_theme_seed_symbols,
    load_theme_seed_sources,
    normalize_cn_index_constituents,
    resolve_cn_index_constituents,
    resolve_cn_etf_constituents,
)
from lurker.ingest.prices import normalize_cn_price_frame, to_akshare_symbol, normalize_price_frame, to_yfinance_symbol
from lurker.ingest.prices import (
    fetch_cn_prices,
    normalize_baostock_cn_price_frame,
    normalize_tushare_cn_price_frame,
    to_baostock_symbol,
)


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
