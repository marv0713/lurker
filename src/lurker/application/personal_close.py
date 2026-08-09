from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from lurker.application.personal_close_state import PersonalCloseStateStore
from lurker.config import (
    PersonalStockConfig,
    PersonalWatchConfig,
    load_personal_watch,
)
from lurker.domain.personal_close import (
    CorporateActionCoverage,
    DataQualityIssue,
    PersonalReportFacts,
    PersonalStockReportFact,
    analyze_personal_trend,
    project_first_bullish_quality,
)
from lurker.domain.spring import analyze_hk_experimental_spring, analyze_spring_bars
from lurker.ingest.corporate_actions import (
    CnCorporateActionProvider,
    CorporateActionProvider,
    HkCorporateActionProvider,
    collect_corporate_actions,
)
from lurker.ingest.personal_prices import load_personal_prices
from lurker.notification.notifier import Notifier
from lurker.reports.personal_close_report import render_personal_close_report
from lurker.trading_calendar import build_default_personal_calendars, shanghai_today


class PersonalCalendar(Protocol):
    def is_trading_day(self, day: date | str) -> bool: ...


ConfigLoader = Callable[[str | Path], PersonalWatchConfig]
PriceLoader = Callable[..., pd.DataFrame]
ReportRenderer = Callable[[PersonalReportFacts], str]
ReportWriter = Callable[[Path, str], None]


@dataclass(frozen=True)
class PersonalCloseRunResult:
    status: str
    report_date: date
    report_path: Path | None
    content_md: str
    push_status: str
    checked_count: int = 0
    failure_count: int = 0


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _default_action_providers() -> dict[str, CorporateActionProvider]:
    return {"cn": CnCorporateActionProvider(), "hk": HkCorporateActionProvider()}


def _price_issue(item: PersonalStockConfig, exc: Exception) -> DataQualityIssue:
    return DataQualityIssue(
        "price_unavailable",
        f"行情获取失败：{exc}",
        symbol=item.symbol,
        market=item.market,
    )


def _missing_action_coverage(
    item: PersonalStockConfig,
    exc: Exception,
) -> CorporateActionCoverage:
    return CorporateActionCoverage(
        actions=(),
        complete=False,
        issues=(
            DataQualityIssue(
                "corporate_actions_unavailable",
                f"公司行动获取失败：{exc}",
                symbol=item.symbol,
                market=item.market,
            ),
        ),
    )


def _analyze_stock(
    *,
    item: PersonalStockConfig,
    group: str,
    market_open: bool,
    report_date: date,
    period: str,
    config: PersonalWatchConfig,
    price_loader: PriceLoader,
    coverage: CorporateActionCoverage,
) -> PersonalStockReportFact:
    issues = list(coverage.issues)
    try:
        prices = price_loader(
            symbol=item.symbol,
            market=item.market,
            report_date=report_date,
            period=period,
        )
        trend = analyze_personal_trend(prices)
        records = prices.to_dict(orient="records")
        if item.market == "cn":
            spring = analyze_spring_bars(records)
        else:
            thresholds = config.hk_experimental_spring
            spring = analyze_hk_experimental_spring(
                records,
                min_avg_turnover_hkd_20d=thresholds.min_avg_turnover_hkd_20d,
                min_positive_volume_ratio_60d=thresholds.min_positive_volume_ratio_60d,
            )
        bullish_quality = project_first_bullish_quality(spring, prices)
        as_of = trend.as_of
        adjusted_close = trend.adjusted_close
        if trend.label == "data_insufficient":
            issues.append(
                DataQualityIssue(
                    "trend_data_insufficient",
                    "趋势所需的 220 根日线不足",
                    symbol=item.symbol,
                    market=item.market,
                )
            )
    except Exception as exc:
        trend = None
        spring = None
        bullish_quality = None
        as_of = None
        adjusted_close = None
        issues.append(_price_issue(item, exc))
    return PersonalStockReportFact(
        config=item,
        group="holding" if group == "holding" else "watchlist",
        market_open=market_open,
        as_of=as_of,
        adjusted_close=adjusted_close,
        trend=trend,
        spring=spring,
        bullish_quality=bullish_quality,
        actions=coverage.actions,
        action_coverage_complete=coverage.complete,
        issues=tuple(issues),
        unsupported_event_types=coverage.unsupported_event_types,
    )


def _validate_run_parameters(
    *,
    report_date: date,
    today: date,
    period: str,
    no_push: bool,
    force_push: bool,
) -> None:
    if period != "2y":
        raise ValueError("personal close period must equal 2y")
    if no_push and force_push:
        raise ValueError("--no-push and --force-push are mutually exclusive")
    if report_date > today:
        raise ValueError("future personal close report date is not allowed")
    if report_date < today and force_push:
        raise ValueError("historical replay cannot use --force-push")


def run_personal_close(
    *,
    config_path: str | Path,
    report_dir: str | Path,
    state_file: str | Path,
    report_date: date,
    today: date | None = None,
    now: datetime | None = None,
    period: str = "2y",
    no_push: bool = False,
    force_push: bool = False,
    notifier: Notifier | None = None,
    config_loader: ConfigLoader = load_personal_watch,
    calendars: Mapping[str, PersonalCalendar] | None = None,
    price_loader: PriceLoader = load_personal_prices,
    action_providers: Mapping[str, CorporateActionProvider] | None = None,
    renderer: ReportRenderer = render_personal_close_report,
    report_writer: ReportWriter = _atomic_write_text,
) -> PersonalCloseRunResult:
    resolved_today = today or shanghai_today(now)
    _validate_run_parameters(
        report_date=report_date,
        today=resolved_today,
        period=period,
        no_push=no_push,
        force_push=force_push,
    )
    config = config_loader(config_path)
    all_items = (*config.holdings, *config.watchlist)
    configured_markets = tuple(dict.fromkeys(item.market for item in all_items))
    calendar_map: Mapping[str, PersonalCalendar] = (
        calendars if calendars is not None else build_default_personal_calendars()
    )
    market_open: dict[str, bool] = {}
    for market in configured_markets:
        calendar = calendar_map.get(market)
        if calendar is None:
            raise ValueError(f"missing personal trading calendar: {market}")
        market_open[market] = calendar.is_trading_day(report_date)
    if not any(market_open.values()):
        return PersonalCloseRunResult(
            "skipped_markets_closed",
            report_date,
            None,
            "",
            "not_attempted",
            checked_count=len(all_items),
        )

    provider_map = action_providers or _default_action_providers()
    try:
        action_coverages = collect_corporate_actions(
            items=all_items,
            report_date=report_date,
            providers=provider_map,
        )
    except Exception as exc:
        action_coverages = {
            item.symbol: _missing_action_coverage(item, exc) for item in all_items
        }

    holdings = tuple(
        _analyze_stock(
            item=item,
            group="holding",
            market_open=market_open[item.market],
            report_date=report_date,
            period=period,
            config=config,
            price_loader=price_loader,
            coverage=action_coverages.get(item.symbol)
            or _missing_action_coverage(item, RuntimeError("missing provider result")),
        )
        for item in config.holdings
    )
    watchlist = tuple(
        _analyze_stock(
            item=item,
            group="watchlist",
            market_open=market_open[item.market],
            report_date=report_date,
            period=period,
            config=config,
            price_loader=price_loader,
            coverage=action_coverages.get(item.symbol)
            or _missing_action_coverage(item, RuntimeError("missing provider result")),
        )
        for item in config.watchlist
    )
    facts = PersonalReportFacts(report_date, holdings, watchlist)
    content = renderer(facts)
    report_path = Path(report_dir) / f"{report_date.isoformat()}.md"
    report_writer(report_path, content)

    if report_date < resolved_today:
        push_status = "historical_read_only"
    elif no_push:
        push_status = "disabled"
    elif notifier is None:
        push_status = "not_configured"
    else:
        store = PersonalCloseStateStore(state_file)
        state = store.load()
        report_key = report_date.isoformat()
        if store.was_accepted(state, report_key) and not force_push:
            push_status = "already_accepted"
        else:
            notifier.send(
                title=f"个人盘后简报 {report_key}",
                markdown_content=content,
            )
            accepted_at = now or datetime.now().astimezone()
            store.mark_accepted(state, report_key, accepted_at)
            store.save(state)
            push_status = "accepted"

    all_facts = (*holdings, *watchlist)
    return PersonalCloseRunResult(
        "generated",
        report_date,
        report_path,
        content,
        push_status,
        checked_count=len(all_facts),
        failure_count=sum(bool(fact.issues) for fact in all_facts),
    )
