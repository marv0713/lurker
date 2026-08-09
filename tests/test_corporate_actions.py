from datetime import date, timedelta

import pandas as pd

from lurker.config import PersonalStockConfig
from lurker.domain.personal_close import CorporateAction
from lurker.ingest.corporate_actions import (
    CnCorporateActionProvider,
    HkCorporateActionProvider,
    collect_corporate_actions,
    default_disclosure_periods,
    normalize_actions,
)


REPORT_DATE = date(2026, 8, 10)


def test_default_disclosure_periods_use_akshare_supported_names():
    assert default_disclosure_periods(date(2026, 4, 1)) == (
        "2025年报",
        "2026一季",
    )
    assert default_disclosure_periods(date(2026, 8, 1)) == ("2026半年报",)
    assert default_disclosure_periods(date(2026, 10, 1)) == ("2026三季",)


def action(offset: int, *, symbol: str = "300308.SZ", event_type: str = "earnings"):
    return CorporateAction(
        symbol=symbol,
        event_type=event_type,
        primary_date=REPORT_DATE + timedelta(days=offset),
        status="expected",
        summary="事件",
    )


def test_action_window_contains_exactly_fourteen_calendar_days():
    actions = normalize_actions(
        (action(-1), action(0), action(13), action(14)),
        items=(PersonalStockConfig("300308.SZ", "cn", "中际旭创"),),
        report_date=REPORT_DATE,
    )

    assert [item.primary_date for item in actions] == [
        REPORT_DATE,
        REPORT_DATE + timedelta(days=13),
    ]


def test_action_normalization_deduplicates_primary_event_but_keeps_supplemental_dates():
    earlier = CorporateAction(
        symbol="300308.SZ",
        event_type="dividend",
        primary_date=REPORT_DATE,
        status="expected",
        summary="预计分红",
        record_date=REPORT_DATE - timedelta(days=1),
    )
    confirmed = CorporateAction(
        symbol="300308.SZ",
        event_type="dividend",
        primary_date=REPORT_DATE,
        status="confirmed",
        summary="现金分红",
        payment_date=REPORT_DATE + timedelta(days=2),
    )

    result = normalize_actions(
        (earlier, confirmed),
        items=(PersonalStockConfig("300308.SZ", "cn", "中际旭创"),),
        report_date=REPORT_DATE,
    )

    assert result == (
        CorporateAction(
            symbol="300308.SZ",
            event_type="dividend",
            primary_date=REPORT_DATE,
            status="confirmed",
            summary="现金分红",
            record_date=REPORT_DATE - timedelta(days=1),
            payment_date=REPORT_DATE + timedelta(days=2),
        ),
    )


class FailingProvider:
    def fetch_many(self, items, report_date):
        raise RuntimeError("source down")


def test_provider_failure_reports_incomplete_not_no_events():
    result = collect_corporate_actions(
        items=(PersonalStockConfig("300308.SZ", "cn", "中际旭创"),),
        report_date=REPORT_DATE,
        providers={"cn": FailingProvider()},
    )

    coverage = result["300308.SZ"]
    assert coverage.complete is False
    assert coverage.actions == ()
    assert coverage.issues[0].code == "corporate_actions_unavailable"


def test_cn_provider_uses_latest_disclosure_date_and_actual_is_confirmed():
    disclosures = {
        "2026中报": pd.DataFrame(
            [
                {
                    "股票代码": "300308",
                    "股票简称": "中际旭创",
                    "首次预约": "2026-08-10",
                    "初次变更": "2026-08-12",
                    "二次变更": None,
                    "三次变更": None,
                    "实际披露": None,
                },
                {
                    "股票代码": "600519",
                    "股票简称": "贵州茅台",
                    "首次预约": "2026-08-11",
                    "初次变更": "2026-08-13",
                    "二次变更": "2026-08-14",
                    "三次变更": "2026-08-15",
                    "实际披露": "2026-08-16",
                },
            ]
        )
    }
    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: disclosures.get(period, pd.DataFrame()),
        distribution_fetcher=lambda symbol: pd.DataFrame(),
        allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
        disclosure_periods=lambda report_date: ("2026中报",),
    )

    result = provider.fetch_many(
        (
            PersonalStockConfig("300308.SZ", "cn", "中际旭创"),
            PersonalStockConfig("600519.SH", "cn", "贵州茅台"),
        ),
        REPORT_DATE,
    )

    first, second = result["300308.SZ"].actions[0], result["600519.SH"].actions[0]
    assert (first.primary_date, first.status) == (date(2026, 8, 12), "expected")
    assert (second.primary_date, second.status) == (date(2026, 8, 16), "confirmed")


def test_cn_provider_normalizes_dividend_split_and_rights_issue():
    distributions = pd.DataFrame(
        [
            {
                "除权除息日": "2026-08-11",
                "股权登记日": "2026-08-10",
                "现金分红-现金分红比例": 1.5,
                "送转股份-送转总比例": 2.0,
                "分红描述": "10派1.5元转2股",
                "方案进度": "实施方案",
                "最新公告日期": "2026-08-01",
            }
        ]
    )
    allotments = pd.DataFrame(
        [{"除权基准日": "2026-08-12", "公告日期": "2026-08-02"}]
    )
    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: pd.DataFrame(),
        distribution_fetcher=lambda symbol: distributions,
        allotment_fetcher=lambda symbol, start, end: allotments,
        disclosure_periods=lambda report_date: (),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"]

    assert [item.event_type for item in coverage.actions] == [
        "dividend",
        "split",
        "rights_issue",
    ]
    assert coverage.complete is False
    assert coverage.unsupported_event_types == ("additional_issuance", "consolidation")


def test_cn_endpoint_failure_marks_coverage_incomplete_and_preserves_other_actions():
    def fail(symbol):
        raise RuntimeError("dividend unavailable")

    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: pd.DataFrame(),
        distribution_fetcher=fail,
        allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
        disclosure_periods=lambda report_date: (),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"]

    assert coverage.complete is False
    assert {issue.code for issue in coverage.issues} == {"corporate_actions_unavailable"}


def test_hk_provider_normalizes_calendar_and_dividend_with_explicit_unsupported_types():
    calendar = {
        "Earnings Date": [pd.Timestamp("2026-08-12")],
        "Ex-Dividend Date": pd.Timestamp("2026-08-13"),
    }
    payouts = pd.DataFrame(
        [
            {
                "除净日": "2026-08-13",
                "截至过户日": "2026-08-14",
                "发放日": "2026-08-20",
                "分红方案": "每股派0.5港元",
                "最新公告日期": "2026-08-01",
            },
            {
                "除净日": "2026-08-15",
                "截至过户日": None,
                "发放日": None,
                "分红方案": "每10股送1股",
                "最新公告日期": "2026-08-01",
            },
        ]
    )
    provider = HkCorporateActionProvider(
        calendar_fetcher=lambda symbol: calendar,
        dividend_fetcher=lambda symbol: payouts,
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("00700.HK", "hk", "腾讯控股"),), REPORT_DATE
    )["00700.HK"]

    assert [item.event_type for item in coverage.actions] == [
        "earnings",
        "dividend",
        "split",
    ]
    assert coverage.complete is False
    assert coverage.unsupported_event_types == (
        "additional_issuance",
        "consolidation",
        "rights_issue",
    )
    assert coverage.actions[1].payment_date == date(2026, 8, 20)


def test_collection_does_not_call_provider_for_unconfigured_market():
    class ExplodingProvider:
        def fetch_many(self, items, report_date):
            raise AssertionError("must not be called")

    result = collect_corporate_actions(
        items=(PersonalStockConfig("300308.SZ", "cn", "中际旭创"),),
        report_date=REPORT_DATE,
        providers={"cn": CnCorporateActionProvider.empty(), "hk": ExplodingProvider()},
    )

    assert set(result) == {"300308.SZ"}
