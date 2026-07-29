import json
from datetime import date

import pandas as pd
import pytest

from lurker.config import MonthlyMacroConfig
from lurker.ingest.macro_monthly import (
    ExchangeCircMvResult,
    MonthlyMacroSnapshotStore,
    MonthlySchemaError,
    MonthlySourceError,
    build_leverage_facts,
    build_macro_facts,
    collect_monthly_macro_snapshot,
    normalize_exchange_circ_mv,
    normalize_money_supply,
    select_common_macro_month,
)
from lurker.ingest.pboc_deposits import PbocSourceError


def exchange_result(value: float) -> ExchangeCircMvResult:
    return ExchangeCircMvResult(value_yuan=value, sources=())


def monthly_config() -> MonthlyMacroConfig:
    return MonthlyMacroConfig(
        credit_table_urls={2025: "https://www.pbc.gov.cn/2025.htm"},
        allowed_hosts=("www.pbc.gov.cn",),
        timeout_seconds=30,
        max_response_bytes=1_000_000,
        household_deposit_yoy_pct=12.0,
        leverage_ratio_pct=4.0,
        financing_monthly_growth_pct=20.0,
        macro_max_lag_months=2,
        leverage_max_lag_trading_days=3,
    )


def money_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "月份": "2025年01月份",
                "货币和准货币(M2)-同比增长": 7.0,
                "货币(M1)-同比增长": 0.4,
            },
            {
                "月份": "2024年12月份",
                "货币和准货币(M2)-同比增长": 7.3,
                "货币(M1)-同比增长": -1.4,
            },
        ]
    )


def valid_pboc(config, raw_dir):
    return {
        "balances": {
            "household": {
                "2023-12": 80.0,
                "2024-01": 85.0,
                "2024-12": 90.0,
                "2025-01": 100.0,
            },
            "nonbank": {"2024-12": 20.0, "2025-01": 21.0},
        },
        "sources": [],
        "failures": [],
    }


def margin_frame(current: float, previous: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"日期": date(2025, 1, 30), "融资余额": current},
            {"日期": date(2024, 12, 31), "融资余额": previous},
        ]
    )


def test_normalize_money_supply_uses_yoy_columns_only():
    assert normalize_money_supply(money_frame()) == {
        "2025-01": {"m1_yoy_pct": 0.4, "m2_yoy_pct": 7.0},
        "2024-12": {"m1_yoy_pct": -1.4, "m2_yoy_pct": 7.3},
    }


def test_normalize_money_supply_fails_closed():
    with pytest.raises(MonthlySchemaError, match="missing columns"):
        normalize_money_supply(pd.DataFrame([{"月份": "2025年01月份"}]))
    row = money_frame().iloc[0].to_dict()
    with pytest.raises(MonthlySchemaError, match="duplicate"):
        normalize_money_supply(pd.DataFrame([row, row]))


def test_select_common_macro_month_and_build_facts():
    deposits = valid_pboc(None, None)["balances"]
    money = normalize_money_supply(money_frame())
    assert (
        select_common_macro_month(
            deposits,
            money,
            report_month="2025-02",
            max_lag_months=2,
        )
        == "2025-01"
    )
    facts = build_macro_facts(
        deposits,
        money,
        report_month="2025-01",
        max_lag_months=2,
    )
    assert facts["macro_month"] == "2025-01"
    assert facts["household"]["previous_year_previous_month"] == 80.0
    assert facts["nonbank"]["current"] == 21.0


def test_stale_macro_month_returns_observation_facts():
    facts = build_macro_facts(
        {
            "household": {"2024-01": 100.0},
            "nonbank": {"2024-01": 20.0},
        },
        {"2024-01": {"m1_yoy_pct": 1.0, "m2_yoy_pct": 2.0}},
        report_month="2025-01",
        max_lag_months=2,
    )
    assert facts["macro_month"] is None
    assert facts["failures"] == ["no fresh common macro month"]


def test_exchange_market_cap_excludes_b_shares_and_converts_units():
    sse = pd.DataFrame(
        [
            {
                "单日情况": "流通市值",
                "主板A": 500000.0,
                "主板B": 700.0,
                "科创板": 100000.0,
            }
        ]
    )
    szse = pd.DataFrame(
        [
            {"证券类别": "主板A股", "流通市值": 15_000_000_000_000.0},
            {"证券类别": "主板B股", "流通市值": 40_000_000_000.0},
            {"证券类别": "创业板A股", "流通市值": 7_000_000_000_000.0},
        ]
    )
    assert normalize_exchange_circ_mv(sse, szse) == 82_000_000_000_000.0


def test_build_leverage_facts_aligns_dates_and_previous_month():
    result = build_leverage_facts(
        margin_frame(100.0, 80.0),
        margin_frame(50.0, 40.0),
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        trading_day_checker=lambda value: value.weekday() < 5,
        today=date(2026, 7, 26),
    )
    assert result["trade_date"] == "2025-01-30"
    assert result["current_financing_balance"] == 150.0
    assert result["previous_financing_balance"] == 120.0
    assert result["a_share_circ_mv"] == 10_000.0


def test_build_leverage_facts_rejects_missing_or_stale_data():
    empty = pd.DataFrame(columns=["日期", "融资余额"])
    missing = build_leverage_facts(
        empty,
        empty,
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(1.0),
        trading_day_checker=lambda value: True,
        today=date(2026, 7, 26),
    )
    assert missing["failure"] == "no common Shanghai/Shenzhen margin date"

    stale_frame = pd.DataFrame(
        [
            {"日期": date(2025, 1, 20), "融资余额": 100.0},
            {"日期": date(2024, 12, 31), "融资余额": 80.0},
        ]
    )
    stale = build_leverage_facts(
        stale_frame,
        stale_frame,
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(1.0),
        trading_day_checker=lambda value: value.weekday() < 5,
        today=date(2026, 7, 26),
    )
    assert stale["failure"] == "margin data is stale"


def test_collect_snapshot_preserves_sources_thresholds_and_failures(tmp_path):
    snapshot = collect_monthly_macro_snapshot(
        report_month="2025-01",
        config=monthly_config(),
        raw_dir=tmp_path / "raw",
        pboc_collector=valid_pboc,
        money_fetcher=money_frame,
        margin_sh_fetcher=lambda: margin_frame(100.0, 80.0),
        margin_sz_fetcher=lambda: margin_frame(50.0, 40.0),
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        generated_at=lambda: "2026-07-26T12:00:00+00:00",
        today=date(2026, 7, 26),
    )
    assert snapshot["schema_version"] == 1
    assert snapshot["macro"]["macro_month"] == "2025-01"
    assert snapshot["leverage"]["trade_date"] == "2025-01-30"
    assert snapshot["thresholds"]["leverage_ratio_pct"] == 4.0
    assert snapshot["failures"] == []


def test_external_source_failure_degrades_without_hiding_programming_errors(
    tmp_path,
):
    def source_failure(config, raw_dir):
        raise PbocSourceError("PBOC timeout")

    snapshot = collect_monthly_macro_snapshot(
        report_month="2025-01",
        config=monthly_config(),
        raw_dir=tmp_path,
        pboc_collector=source_failure,
        margin_sh_fetcher=lambda: margin_frame(100.0, 80.0),
        margin_sz_fetcher=lambda: margin_frame(50.0, 40.0),
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        today=date(2026, 7, 26),
    )
    assert snapshot["macro"]["household"] is None
    assert snapshot["leverage"]["trade_date"] == "2025-01-30"

    with pytest.raises(TypeError, match="programmer error"):
        collect_monthly_macro_snapshot(
            report_month="2025-01",
            config=monthly_config(),
            raw_dir=tmp_path,
            pboc_collector=lambda config, raw_dir: (_ for _ in ()).throw(
                TypeError("programmer error")
            ),
        )


def test_monthly_snapshot_store_overwrites_atomically(tmp_path):
    store = MonthlyMacroSnapshotStore(tmp_path)
    first = {"schema_version": 1, "report_month": "2025-01", "value": 1}
    second = {"schema_version": 1, "report_month": "2025-01", "value": 2}
    path = store.save(first)
    assert store.save(second) == path
    assert json.loads(path.read_text(encoding="utf-8"))["value"] == 2
    assert list(tmp_path.glob("*.tmp")) == []


def test_monthly_source_error_type_is_runtime_error():
    assert issubclass(MonthlySourceError, RuntimeError)
