from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from lurker.ingest.personal_prices import (
    load_personal_prices,
    normalize_personal_prices,
)


REPORT_DATE = date(2026, 8, 10)


def _frame(count: int = 2) -> pd.DataFrame:
    start = REPORT_DATE - timedelta(days=count - 1)
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=index) for index in range(count)],
            "open": [10.0] * count,
            "high": [12.0] * count,
            "low": [9.0] * count,
            "close": [10.0] * count,
            "adj_close": [5.0] * count,
            "volume": [100.0] * count,
        }
    )


def test_hk_adjusted_ohlc_uses_adj_close_ratio():
    normalized = normalize_personal_prices(
        _frame(),
        market="hk",
        report_date=REPORT_DATE,
    )

    row = normalized.iloc[-1]
    assert (row["open"], row["high"], row["low"], row["close"]) == (
        5.0,
        6.0,
        4.5,
        5.0,
    )
    assert row["raw_close"] == 10.0
    assert row["adj_close"] == 5.0


def test_cn_prices_are_already_adjusted():
    raw = _frame()
    raw["adj_close"] = raw["close"]

    normalized = normalize_personal_prices(
        raw,
        market="cn",
        report_date=REPORT_DATE,
    )

    assert normalized.iloc[-1]["close"] == 10.0
    assert normalized.iloc[-1]["raw_close"] == 10.0


def test_duplicate_trade_date_is_rejected_not_deduplicated():
    raw = _frame()
    raw.loc[1, "trade_date"] = raw.loc[0, "trade_date"]

    with pytest.raises(ValueError, match="duplicate_trade_date"):
        normalize_personal_prices(raw, market="hk", report_date=REPORT_DATE)


def test_invalid_trade_date_is_rejected():
    raw = _frame()
    raw.loc[0, "trade_date"] = "invalid"

    with pytest.raises(ValueError, match="invalid_trade_date"):
        normalize_personal_prices(raw, market="hk", report_date=REPORT_DATE)


def test_rows_after_report_date_are_removed_before_analysis():
    raw = _frame(3)
    raw.loc[2, "trade_date"] = REPORT_DATE + timedelta(days=1)

    normalized = normalize_personal_prices(raw, market="hk", report_date=REPORT_DATE)

    assert normalized["trade_date"].max() <= REPORT_DATE
    assert len(normalized) == 2


def test_invalid_hk_adjustment_factor_is_rejected():
    raw = _frame()
    raw.loc[1, "adj_close"] = 0.0

    with pytest.raises(ValueError, match="invalid_adjusted_price_data"):
        normalize_personal_prices(raw, market="hk", report_date=REPORT_DATE)


def test_loader_rejects_non_two_year_period_before_fetch():
    calls: list[tuple] = []

    def fetcher(*args, **kwargs):
        calls.append((args, kwargs))
        return _frame()

    with pytest.raises(ValueError, match="personal price period must equal 2y"):
        load_personal_prices(
            symbol="00700.HK",
            market="hk",
            report_date=REPORT_DATE,
            period="1y",
            fetcher=fetcher,
        )

    assert calls == []


def test_loader_fetches_exactly_two_years():
    calls: list[tuple] = []

    def fetcher(*args, **kwargs):
        calls.append((args, kwargs))
        return _frame()

    load_personal_prices(
        symbol="00700.HK",
        market="hk",
        report_date=REPORT_DATE,
        period="2y",
        fetcher=fetcher,
    )

    assert calls == [
        (
            ("00700.HK", "hk", "2y"),
            {"is_benchmark": False, "end_date": REPORT_DATE},
        )
    ]
