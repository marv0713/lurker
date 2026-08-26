from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
import os
import re
import time
from typing import Any, Protocol, cast

import akshare as ak
import pandas as pd
import requests
import yfinance as yf

from lurker.config import PersonalStockConfig
from lurker.domain.personal_close import (
    CorporateAction,
    CorporateActionCoverage,
    CorporateEventType,
    DataQualityIssue,
)
from lurker.ingest.prices import HITHINK_BASE_URL, to_akshare_symbol, to_yfinance_symbol


EVENT_ORDER: dict[str, int] = {
    "earnings": 0,
    "dividend": 1,
    "split": 2,
    "consolidation": 3,
    "rights_issue": 4,
    "additional_issuance": 5,
}
CN_UNSUPPORTED: tuple[CorporateEventType, ...] = (
    "additional_issuance",
    "consolidation",
)
HK_UNSUPPORTED: tuple[CorporateEventType, ...] = (
    "additional_issuance",
    "consolidation",
    "rights_issue",
)
_DEFAULT_HITHINK_DISTRIBUTION = object()


class CorporateActionProvider(Protocol):
    def fetch_many(
        self,
        items: Sequence[PersonalStockConfig],
        report_date: date,
    ) -> Mapping[str, CorporateActionCoverage]: ...


def _date(value: Any) -> date | None:
    if value is None or (not isinstance(value, (list, tuple)) and pd.isna(value)):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if isinstance(parsed, pd.DatetimeIndex):
        parsed = next((item for item in parsed if not pd.isna(item)), pd.NaT)
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).date()


def _number(value: Any) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if not pd.isna(parsed):
        return float(parsed)
    numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", str(value))
    return max((float(number) for number in numbers), default=0.0)


def _epoch_ms_date(value: Any) -> date | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    timestamp = pd.to_datetime(parsed, unit="ms", utc=True, errors="coerce")
    if pd.isna(timestamp):
        return None
    return pd.Timestamp(timestamp).tz_convert("Asia/Shanghai").date()


def _text(value: Any, fallback: str) -> str:
    if value is None or pd.isna(value):
        return fallback
    result = str(value).strip()
    return result or fallback


def normalize_actions(
    actions: Sequence[CorporateAction],
    *,
    items: Sequence[PersonalStockConfig],
    report_date: date,
) -> tuple[CorporateAction, ...]:
    symbols = {item.symbol for item in items}
    last_date = report_date + timedelta(days=13)
    selected = {
        (item.symbol, item.event_type, item.primary_date): item
        for item in actions
        if item.symbol in symbols and report_date <= item.primary_date <= last_date
    }
    for item in actions:
        key = (item.symbol, item.event_type, item.primary_date)
        if key not in selected or item.symbol not in symbols:
            continue
        previous = selected[key]
        preferred = item if item.status == "confirmed" else previous
        selected[key] = CorporateAction(
            symbol=item.symbol,
            event_type=item.event_type,
            primary_date=item.primary_date,
            status=("confirmed" if "confirmed" in {previous.status, item.status} else "expected"),
            summary=preferred.summary,
            record_date=previous.record_date or item.record_date,
            payment_date=previous.payment_date or item.payment_date,
            source_updated_at=preferred.source_updated_at,
        )
    return tuple(
        sorted(
            selected.values(),
            key=lambda item: (
                item.primary_date,
                EVENT_ORDER[item.event_type],
                item.symbol,
            ),
        )
    )


def _unavailable(symbol: str, market: str, exc: Exception) -> DataQualityIssue:
    return DataQualityIssue(
        code="corporate_actions_unavailable",
        message=f"公司行动获取失败：{exc}",
        symbol=symbol,
        market=market,
    )


def collect_corporate_actions(
    *,
    items: Sequence[PersonalStockConfig],
    report_date: date,
    providers: Mapping[str, CorporateActionProvider],
) -> dict[str, CorporateActionCoverage]:
    result: dict[str, CorporateActionCoverage] = {}
    for market in ("cn", "hk"):
        market_items = tuple(item for item in items if item.market == market)
        if not market_items:
            continue
        provider = providers.get(market)
        if provider is None:
            error = RuntimeError(f"missing {market} corporate-action provider")
            fetched: Mapping[str, CorporateActionCoverage] = {}
        else:
            try:
                fetched = provider.fetch_many(market_items, report_date)
                error = None
            except Exception as exc:  # provider boundary is deliberately isolated
                fetched = {}
                error = exc
        for item in market_items:
            coverage = fetched.get(item.symbol)
            if coverage is not None:
                result[item.symbol] = CorporateActionCoverage(
                    actions=normalize_actions(
                        coverage.actions,
                        items=(item,),
                        report_date=report_date,
                    ),
                    complete=coverage.complete,
                    issues=coverage.issues,
                    unsupported_event_types=coverage.unsupported_event_types,
                )
            else:
                failure = error or RuntimeError("provider omitted configured symbol")
                result[item.symbol] = CorporateActionCoverage(
                    actions=(),
                    complete=False,
                    issues=(_unavailable(item.symbol, market, failure),),
                )
    return result


def default_disclosure_periods(report_date: date) -> tuple[str, ...]:
    year = report_date.year
    if report_date.month <= 4:
        return (f"{year - 1}年报", f"{year}一季")
    if report_date.month <= 8:
        return (f"{year}半年报",)
    if report_date.month <= 10:
        return (f"{year}三季",)
    return (f"{year}年报",)


def _ak_disclosures(period: str) -> pd.DataFrame:
    return ak.stock_report_disclosure(market="沪深京", period=period)


def _ak_distribution(symbol: str) -> pd.DataFrame:
    return ak.stock_fhps_detail_em(symbol=symbol)


def _ak_allotment(symbol: str, start: str, end: str) -> pd.DataFrame:
    return ak.stock_allotment_cninfo(symbol=symbol, start_date=start, end_date=end)


def fetch_hithink_cn_corporate_actions(
    symbol: str,
    report_date: date,
    *,
    token: str | None = None,
) -> tuple[CorporateAction, ...]:
    resolved_token = token or os.environ.get("HITHINK_FINANCE_API_KEY", "")
    if not resolved_token:
        raise ValueError("HITHINK_FINANCE_API_KEY is not set")

    params = {
        "thscode": symbol,
        "from": report_date.isoformat(),
        "to": (report_date + timedelta(days=13)).isoformat(),
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(
                f"{HITHINK_BASE_URL}/api/a-share/corporate-actions/adjustment-factors",
                params=params,
                headers={"X-api-key": resolved_token},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
        else:
            if not isinstance(payload, Mapping):
                raise RuntimeError("hithink malformed corporate-action response")
            code = payload.get("code")
            if code == 4001:
                last_error = RuntimeError(
                    f"hithink rate limited: {payload.get('message')}"
                )
            elif code == 3002 and str(payload.get("message", "")).startswith(
                "No adjustment events"
            ):
                return ()
            elif code != 0:
                raise RuntimeError(
                    f"hithink corporate-action error {code}: {payload.get('message')}"
                )
            else:
                data = payload.get("data")
                if not isinstance(data, Mapping):
                    raise RuntimeError("hithink missing corporate-action data")
                items = data.get("item")
                if not isinstance(items, list):
                    raise RuntimeError("hithink missing corporate-action item list")
                actions: list[CorporateAction] = []
                for item in items:
                    if not isinstance(item, Mapping):
                        raise RuntimeError("hithink malformed corporate-action item")
                    cash = _number(item.get("dividend_per_share"))
                    bonus = _number(item.get("per_share_bonus"))
                    if cash <= 0 and bonus <= 0:
                        continue
                    primary_date = _epoch_ms_date(item.get("ex_date_ms"))
                    if primary_date is None:
                        raise RuntimeError("hithink invalid corporate-action date")
                    summary_parts: list[str] = []
                    if cash > 0:
                        summary_parts.append(f"每股现金分红 {cash:g} 元")
                    if bonus > 0:
                        summary_parts.append(f"每股送股 {bonus:g} 股")
                    actions.append(
                        CorporateAction(
                            symbol,
                            "dividend",
                            primary_date,
                            "confirmed",
                            "；".join(summary_parts),
                        )
                    )
                return tuple(actions)
        if attempt < 2:
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"hithink corporate-action request failed: {last_error}")


class CnCorporateActionProvider:
    def __init__(
        self,
        *,
        disclosure_fetcher: Callable[[str], pd.DataFrame] = _ak_disclosures,
        hithink_distribution_fetcher: Callable[
            [str, date], Sequence[CorporateAction]
        ]
        | None
        | object = _DEFAULT_HITHINK_DISTRIBUTION,
        distribution_fetcher: Callable[[str], pd.DataFrame] = _ak_distribution,
        allotment_fetcher: Callable[[str, str, str], pd.DataFrame] = _ak_allotment,
        disclosure_periods: Callable[[date], Sequence[str]] = default_disclosure_periods,
    ) -> None:
        self.disclosure_fetcher = disclosure_fetcher
        self.hithink_distribution_fetcher: Callable[
            [str, date], Sequence[CorporateAction]
        ] | None = (
            fetch_hithink_cn_corporate_actions
            if hithink_distribution_fetcher is _DEFAULT_HITHINK_DISTRIBUTION
            else cast(
                Callable[[str, date], Sequence[CorporateAction]] | None,
                hithink_distribution_fetcher,
            )
        )
        self.distribution_fetcher = distribution_fetcher
        self.allotment_fetcher = allotment_fetcher
        self.disclosure_periods = disclosure_periods

    @classmethod
    def empty(cls) -> CnCorporateActionProvider:
        return cls(
            disclosure_fetcher=lambda period: pd.DataFrame(),
            hithink_distribution_fetcher=lambda symbol, report_date: (),
            distribution_fetcher=lambda symbol: pd.DataFrame(),
            allotment_fetcher=lambda symbol, start, end: pd.DataFrame(),
            disclosure_periods=lambda report_date: (),
        )

    def fetch_many(
        self,
        items: Sequence[PersonalStockConfig],
        report_date: date,
    ) -> dict[str, CorporateActionCoverage]:
        actions: dict[str, list[CorporateAction]] = {item.symbol: [] for item in items}
        issues: dict[str, list[DataQualityIssue]] = {item.symbol: [] for item in items}
        by_code = {to_akshare_symbol(item.symbol): item for item in items}
        for period in self.disclosure_periods(report_date):
            try:
                frame = self.disclosure_fetcher(period)
            except Exception as exc:
                for item in items:
                    issues[item.symbol].append(_unavailable(item.symbol, "cn", exc))
                continue
            for _, row in frame.iterrows():
                code = str(row.get("股票代码", "")).strip().zfill(6)
                item = by_code.get(code)
                if item is None:
                    continue
                actual = _date(row.get("实际披露"))
                scheduled = next(
                    (
                        candidate
                        for column in ("三次变更", "二次变更", "初次变更", "首次预约")
                        if (candidate := _date(row.get(column))) is not None
                    ),
                    None,
                )
                primary = actual or scheduled
                if primary is not None:
                    actions[item.symbol].append(
                        CorporateAction(
                            item.symbol,
                            "earnings",
                            primary,
                            "confirmed" if actual else "expected",
                            f"{period}披露",
                        )
                    )

        start = report_date.strftime("%Y%m%d")
        end = (report_date + timedelta(days=13)).strftime("%Y%m%d")
        for item in items:
            code = to_akshare_symbol(item.symbol)
            use_fallback = self.hithink_distribution_fetcher is None
            if self.hithink_distribution_fetcher is not None:
                try:
                    hithink_actions = self.hithink_distribution_fetcher(
                        item.symbol, report_date
                    )
                    actions[item.symbol].extend(hithink_actions)
                except Exception:
                    use_fallback = True
            if use_fallback:
                try:
                    fallback = self.distribution_fetcher(code)
                    actions[item.symbol].extend(self._distribution_actions(item.symbol, fallback))
                except Exception as exc:
                    issues[item.symbol].append(_unavailable(item.symbol, "cn", exc))
            try:
                allotments = self.allotment_fetcher(code, start, end)
                actions[item.symbol].extend(self._allotment_actions(item.symbol, allotments))
            except Exception as exc:
                issues[item.symbol].append(_unavailable(item.symbol, "cn", exc))

        return {
            item.symbol: CorporateActionCoverage(
                actions=normalize_actions(
                    actions[item.symbol], items=(item,), report_date=report_date
                ),
                complete=not issues[item.symbol],
                issues=tuple(issues[item.symbol]),
                unsupported_event_types=CN_UNSUPPORTED,
            )
            for item in items
        }

    @staticmethod
    def _distribution_actions(symbol: str, frame: pd.DataFrame) -> list[CorporateAction]:
        result: list[CorporateAction] = []
        for _, row in frame.iterrows():
            primary = _date(row.get("除权除息日"))
            if primary is None:
                continue
            summary = _text(row.get("分红描述"), "") or _text(
                row.get("现金分红-现金分红比例描述"),
                "除权除息",
            )
            common = {
                "symbol": symbol,
                "primary_date": primary,
                "status": "confirmed",
                "summary": summary,
                "record_date": _date(row.get("股权登记日")),
                "source_updated_at": _text(row.get("最新公告日期"), "") or None,
            }
            if _number(row.get("现金分红-现金分红比例")) > 0:
                result.append(CorporateAction(event_type="dividend", **common))
            if _number(row.get("送转股份-送转总比例")) > 0:
                result.append(CorporateAction(event_type="dividend", **common))
        return result

    @staticmethod
    def _allotment_actions(symbol: str, frame: pd.DataFrame) -> list[CorporateAction]:
        result: list[CorporateAction] = []
        for _, row in frame.iterrows():
            primary = _date(row.get("除权基准日"))
            if primary is not None:
                result.append(
                    CorporateAction(
                        symbol,
                        "rights_issue",
                        primary,
                        "confirmed",
                        "配股除权",
                        source_updated_at=_text(row.get("公告日期"), "") or None,
                    )
                )
        return result


def _yf_calendar(symbol: str) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], yf.Ticker(to_yfinance_symbol(symbol)).get_calendar())


def _ak_hk_dividend(symbol: str) -> pd.DataFrame:
    return ak.stock_hk_dividend_payout_em(symbol=to_akshare_symbol(symbol).zfill(5))


class HkCorporateActionProvider:
    def __init__(
        self,
        *,
        calendar_fetcher: Callable[[str], Mapping[str, Any]] = _yf_calendar,
        dividend_fetcher: Callable[[str], pd.DataFrame] = _ak_hk_dividend,
    ) -> None:
        self.calendar_fetcher = calendar_fetcher
        self.dividend_fetcher = dividend_fetcher

    def fetch_many(
        self,
        items: Sequence[PersonalStockConfig],
        report_date: date,
    ) -> dict[str, CorporateActionCoverage]:
        result: dict[str, CorporateActionCoverage] = {}
        for item in items:
            actions: list[CorporateAction] = []
            issues: list[DataQualityIssue] = []
            try:
                calendar = self.calendar_fetcher(item.symbol)
                earnings = _date(calendar.get("Earnings Date"))
                if earnings is not None:
                    actions.append(
                        CorporateAction(
                            item.symbol,
                            "earnings",
                            earnings,
                            "expected",
                            "财报披露",
                        )
                    )
                ex_dividend = _date(calendar.get("Ex-Dividend Date"))
                if ex_dividend is not None:
                    actions.append(
                        CorporateAction(
                            item.symbol,
                            "dividend",
                            ex_dividend,
                            "expected",
                            "除息日",
                        )
                    )
            except Exception as exc:
                issues.append(_unavailable(item.symbol, "hk", exc))
            try:
                payouts = self.dividend_fetcher(item.symbol)
                actions.extend(self._dividend_actions(item.symbol, payouts))
            except Exception as exc:
                issues.append(_unavailable(item.symbol, "hk", exc))
            result[item.symbol] = CorporateActionCoverage(
                actions=normalize_actions(actions, items=(item,), report_date=report_date),
                complete=not issues,
                issues=tuple(issues),
                unsupported_event_types=HK_UNSUPPORTED,
            )
        return result

    @staticmethod
    def _dividend_actions(symbol: str, frame: pd.DataFrame) -> list[CorporateAction]:
        result: list[CorporateAction] = []
        for _, row in frame.iterrows():
            primary = _date(row.get("除净日"))
            if primary is None:
                continue
            summary = _text(row.get("分红方案"), "除净")
            event_type: CorporateEventType = "dividend"
            if any(word in summary for word in ("合股", "合并")):
                event_type = "consolidation"
            elif any(word in summary for word in ("拆股", "拆细")):
                event_type = "split"
            result.append(
                CorporateAction(
                    symbol,
                    event_type,
                    primary,
                    "confirmed",
                    summary,
                    record_date=_date(row.get("截至过户日")),
                    payment_date=_date(row.get("发放日")),
                    source_updated_at=_text(row.get("最新公告日期"), "") or None,
                )
            )
        return result
