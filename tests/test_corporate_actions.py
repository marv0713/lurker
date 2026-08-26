from datetime import date, timedelta

import pandas as pd

from lurker.config import PersonalStockConfig
from lurker.domain.personal_close import CorporateAction
from lurker.ingest import corporate_actions
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


def test_cn_provider_normalizes_cash_and_stock_distributions_as_dividend():
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
    allotments = pd.DataFrame([{"除权基准日": "2026-08-12", "公告日期": "2026-08-02"}])
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
        "rights_issue",
    ]
    assert coverage.complete is True
    assert coverage.unsupported_event_types == ("additional_issuance", "consolidation")


def test_cn_provider_merges_hithink_dividend_with_disclosure_and_allotment():
    disclosures = pd.DataFrame(
        [{"股票代码": "300308", "首次预约": "2026-08-10", "实际披露": None}]
    )
    allotments = pd.DataFrame([{"除权基准日": "2026-08-12"}])
    hithink_actions = (
        CorporateAction(
            "300308.SZ",
            "dividend",
            date(2026, 8, 11),
            "confirmed",
            "每股现金分红 0.5 元",
        ),
    )

    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: disclosures,
        hithink_distribution_fetcher=lambda symbol, report_date: hithink_actions,
        distribution_fetcher=lambda symbol: (_ for _ in ()).throw(
            AssertionError("AkShare fallback must not be called")
        ),
        allotment_fetcher=lambda symbol, start, end: allotments,
        disclosure_periods=lambda report_date: ("2026中报",),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"]

    assert [item.event_type for item in coverage.actions] == [
        "earnings",
        "dividend",
        "rights_issue",
    ]
    assert coverage.complete is True


def test_fetch_hithink_cn_corporate_actions_maps_cash_and_bonus(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "message": "success",
                "request_id": "request-1",
                "data": {
                    "thscode": "300308.SZ",
                    "ticker": "300308",
                    "item": [
                        {
                            "ticker": "300308",
                            "ex_date_ms": int(
                                pd.Timestamp("2026-08-11", tz="Asia/Shanghai").timestamp()
                                * 1000
                            ),
                            "dividend_per_share": 0.5,
                            "per_share_bonus": 0.1,
                        },
                        {
                            "ticker": "300308",
                            "ex_date_ms": int(
                                pd.Timestamp("2026-08-12", tz="Asia/Shanghai").timestamp()
                                * 1000
                            ),
                            "dividend_per_share": 0,
                            "per_share_bonus": 0,
                        },
                    ],
                },
            }

    def fake_get(url, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return FakeResponse()

    monkeypatch.setattr(corporate_actions.requests, "get", fake_get)

    actions = corporate_actions.fetch_hithink_cn_corporate_actions(
        "300308.SZ", REPORT_DATE, token="test-key"
    )

    assert [(item.event_type, item.primary_date, item.status) for item in actions] == [
        ("dividend", date(2026, 8, 11), "confirmed")
    ]
    assert actions[0].summary == "每股现金分红 0.5 元；每股送股 0.1 股"
    url, params, headers, timeout = calls[0]
    assert url.endswith("/api/a-share/corporate-actions/adjustment-factors")
    assert params == {"thscode": "300308.SZ", "from": "2026-08-10", "to": "2026-08-23"}
    assert headers == {"X-api-key": "test-key"}
    assert timeout == 15


def test_fetch_hithink_cn_corporate_actions_treats_no_events_3002_as_empty(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 3002,
                "message": "No adjustment events for thscode=300308.SZ",
                "request_id": "request-2",
                "data": None,
            }

    monkeypatch.setattr(
        corporate_actions.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(),
    )

    actions = corporate_actions.fetch_hithink_cn_corporate_actions(
        "300308.SZ", REPORT_DATE, token="test-key"
    )

    assert actions == ()


def test_cn_provider_uses_successful_empty_hithink_response_without_fallback():
    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: pd.DataFrame(),
        hithink_distribution_fetcher=lambda symbol, report_date: (),
        distribution_fetcher=lambda symbol: (_ for _ in ()).throw(
            AssertionError("AkShare fallback must not be called")
        ),
        allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
        disclosure_periods=lambda report_date: (),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"]

    assert coverage.actions == ()
    assert coverage.complete is True


def test_cn_provider_falls_back_to_akshare_when_hithink_fails():
    distributions = pd.DataFrame(
        [{"除权除息日": "2026-08-11", "现金分红-现金分红比例": 0.5}]
    )
    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: pd.DataFrame(),
        hithink_distribution_fetcher=lambda symbol, report_date: (_ for _ in ()).throw(
            RuntimeError("hithink unavailable")
        ),
        distribution_fetcher=lambda symbol: distributions,
        allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
        disclosure_periods=lambda report_date: (),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"]

    assert [item.primary_date for item in coverage.actions] == [date(2026, 8, 11)]
    assert coverage.complete is True
    assert coverage.issues == ()


def test_custom_akshare_distribution_fetcher_disables_ambient_hithink(
    monkeypatch,
):
    calls = []
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "ambient-key")

    def unexpected_get(*args, **kwargs):
        calls.append(args[0])
        raise RuntimeError("must not call live HiThink from an injected provider")

    monkeypatch.setattr(corporate_actions.requests, "get", unexpected_get)
    distributions = pd.DataFrame(
        [{"除权除息日": "2026-08-11", "现金分红-现金分红比例": 0.5}]
    )
    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: pd.DataFrame(),
        distribution_fetcher=lambda symbol: distributions,
        allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
        disclosure_periods=lambda report_date: (),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"]

    assert [item.primary_date for item in coverage.actions] == [date(2026, 8, 11)]
    assert calls == []


def test_cn_provider_marks_incomplete_when_both_dividend_sources_fail():
    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: pd.DataFrame(),
        hithink_distribution_fetcher=lambda symbol, report_date: (_ for _ in ()).throw(
            RuntimeError("hithink unavailable")
        ),
        distribution_fetcher=lambda symbol: (_ for _ in ()).throw(
            RuntimeError("akshare unavailable")
        ),
        allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
        disclosure_periods=lambda report_date: (),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"]

    assert coverage.complete is False
    assert {issue.code for issue in coverage.issues} == {"corporate_actions_unavailable"}


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
        "dividend",
    ]
    assert coverage.complete is True
    assert coverage.unsupported_event_types == (
        "additional_issuance",
        "consolidation",
        "rights_issue",
    )
    assert coverage.actions[1].payment_date == date(2026, 8, 20)


def test_distribution_text_ratios_are_not_silently_dropped():
    distributions = pd.DataFrame(
        [
            {
                "除权除息日": "2026-08-11",
                "现金分红-现金分红比例": "10派3.5",
                "送转股份-送转总比例": "--",
                "分红描述": "10派3.5元",
            },
            {
                "除权除息日": "2026-08-12",
                "现金分红-现金分红比例": "--",
                "送转股份-送转总比例": "10送2",
                "分红描述": "10送2股",
            },
        ]
    )
    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: pd.DataFrame(),
        distribution_fetcher=lambda symbol: distributions,
        allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
        disclosure_periods=lambda report_date: (),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"]

    assert [(item.event_type, item.primary_date) for item in coverage.actions] == [
        ("dividend", date(2026, 8, 11)),
        ("dividend", date(2026, 8, 12)),
    ]
    assert coverage.complete is True


def test_cn_distribution_uses_current_akshare_description_column():
    distributions = pd.DataFrame(
        [
            {
                "除权除息日": "2026-08-11",
                "现金分红-现金分红比例": 4.0,
                "送转股份-送转总比例": None,
                "现金分红-现金分红比例描述": "10派4.00元(含税)",
            }
        ]
    )
    provider = CnCorporateActionProvider(
        disclosure_fetcher=lambda period: pd.DataFrame(),
        distribution_fetcher=lambda symbol: distributions,
        allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
        disclosure_periods=lambda report_date: (),
    )

    action = provider.fetch_many(
        (PersonalStockConfig("300308.SZ", "cn", "中际旭创"),), REPORT_DATE
    )["300308.SZ"].actions[0]

    assert action.summary == "10派4.00元(含税)"


def test_hk_real_split_keywords_remain_split_not_stock_dividend():
    payouts = pd.DataFrame(
        [
            {"除净日": "2026-08-13", "分红方案": "每1股拆细为5股"},
            {"除净日": "2026-08-14", "分红方案": "每10股合并为1股"},
        ]
    )
    provider = HkCorporateActionProvider(
        calendar_fetcher=lambda symbol: {},
        dividend_fetcher=lambda symbol: payouts,
    )

    actions = provider.fetch_many(
        (PersonalStockConfig("00700.HK", "hk", "腾讯控股"),), REPORT_DATE
    )["00700.HK"].actions

    assert [item.event_type for item in actions] == ["split", "consolidation"]


def test_hk_provider_keeps_yfinance_ex_dividend_when_detail_source_has_no_row():
    provider = HkCorporateActionProvider(
        calendar_fetcher=lambda symbol: {"Ex-Dividend Date": pd.Timestamp("2026-08-13")},
        dividend_fetcher=lambda symbol: pd.DataFrame(),
    )

    coverage = provider.fetch_many(
        (PersonalStockConfig("00700.HK", "hk", "腾讯控股"),), REPORT_DATE
    )["00700.HK"]

    assert [(item.event_type, item.primary_date, item.status) for item in coverage.actions] == [
        ("dividend", date(2026, 8, 13), "expected")
    ]


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
