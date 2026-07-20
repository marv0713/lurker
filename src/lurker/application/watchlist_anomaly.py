from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from lurker.application.watchlist_alert_state import (
    AlertStateStore,
    decide_notification,
    mark_detected,
    mark_notified,
    mark_recovered,
    state_key,
)
from lurker.config import WatchlistConfig, WatchlistItemConfig
from lurker.ingest.prices import BENCHMARK_SYMBOLS, fetch_watchlist_history
from lurker.notification.notifier import Notifier
from lurker.reports.watchlist_alerts import render_watchlist_alerts
from lurker.signals.anomaly import (
    AlertType,
    AnomalyAlert,
    DetectionOutcome,
    DetectionStatus,
    detect_abnormal_volume,
    detect_chronic_underperformance,
    detect_peak_drawdown,
)


HistoryFetcher = Callable[..., pd.DataFrame]


@dataclass(frozen=True)
class WatchlistCheckupResult:
    report_path: Path
    checked_count: int
    new_alert_count: int
    failure_count: int
    pushed: bool
    content_md: str


def _trading_days_since(
    prices: pd.DataFrame,
    last_notified_date: str | None,
    observed_on: str,
) -> int | None:
    if not last_notified_date:
        return None
    dates = pd.to_datetime(prices["trade_date"], errors="coerce").dropna().dt.date.unique()
    cutoff = pd.Timestamp(last_notified_date).date()
    observed = pd.Timestamp(observed_on).date()
    return int(sum(cutoff < value <= observed for value in dates))


def _through_report_date(prices: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    if "trade_date" not in prices.columns:
        return prices
    dates = pd.to_datetime(prices["trade_date"], errors="coerce")
    return prices.loc[dates.notna() & (dates.dt.date <= cutoff)].copy()


def _has_rows_after_report_date(prices: pd.DataFrame, cutoff: date) -> bool:
    if "trade_date" not in prices.columns:
        return False
    dates = pd.to_datetime(prices["trade_date"], errors="coerce").dropna().dt.date
    return any(value > cutoff for value in dates)


def _state_has_dates_after(state: dict, cutoff: date) -> bool:
    for record in state.values():
        for field in ("last_detected_date", "last_notified_date"):
            value = record.get(field)
            if value and date.fromisoformat(str(value)) > cutoff:
                return True
    return False


def _detect(
    item: WatchlistItemConfig,
    stock: pd.DataFrame,
    benchmark: pd.DataFrame | None,
) -> list[DetectionOutcome]:
    outcomes: list[DetectionOutcome] = []
    enabled = set(item.rules.enabled_alerts)
    if AlertType.ABNORMAL_VOLUME.value in enabled:
        outcomes.append(
            detect_abnormal_volume(
                stock,
                symbol=item.symbol,
                market=item.market,
                name=item.name,
                volume_ratio_threshold=item.rules.volume_ratio,
                price_change_threshold=item.rules.price_change,
            )
        )
    if AlertType.PEAK_DRAWDOWN.value in enabled:
        outcomes.append(
            detect_peak_drawdown(
                stock,
                symbol=item.symbol,
                market=item.market,
                name=item.name,
                threshold=item.rules.drawdown,
            )
        )
    if AlertType.CHRONIC_UNDERPERFORMANCE.value in enabled and benchmark is not None:
        outcomes.append(
            detect_chronic_underperformance(
                stock,
                benchmark,
                symbol=item.symbol,
                market=item.market,
                name=item.name,
                threshold=item.rules.underperformance_60d,
            )
        )
    return outcomes


def run_watchlist_anomaly(
    *,
    config: WatchlistConfig,
    report_date: str,
    report_dir: str | Path,
    state_store: AlertStateStore,
    history_fetcher: HistoryFetcher = fetch_watchlist_history,
    notifier: Notifier | None = None,
    push: bool = True,
    period: str = "2y",
) -> WatchlistCheckupResult:
    report_cutoff = date.fromisoformat(report_date)
    state = state_store.load()
    historical_replay = _state_has_dates_after(state, report_cutoff)
    data_issues: list[str] = []
    benchmarks: dict[str, pd.DataFrame] = {}
    benchmark_errors: dict[str, str] = {}
    benchmark_markets = {
        item.market
        for item in config.items
        if AlertType.CHRONIC_UNDERPERFORMANCE.value in item.rules.enabled_alerts
    }
    for market in sorted(benchmark_markets):
        symbol = BENCHMARK_SYMBOLS[market]
        try:
            raw_benchmark = history_fetcher(
                symbol,
                market,
                period,
                is_benchmark=True,
            )
            historical_replay = historical_replay or _has_rows_after_report_date(
                raw_benchmark,
                report_cutoff,
            )
            benchmarks[market] = _through_report_date(
                raw_benchmark,
                report_cutoff,
            )
        except Exception as exc:
            benchmark_errors[market] = f"{type(exc).__name__}: {exc}"
            data_issues.append(f"{market} 基准 {symbol}：{benchmark_errors[market]}")

    new_alerts: list[AnomalyAlert] = []
    successful_stocks = 0
    stock_failures = 0
    detection_failures = 0
    for item in config.items:
        try:
            raw_stock = history_fetcher(
                item.symbol,
                item.market,
                period,
                is_benchmark=False,
            )
            historical_replay = historical_replay or _has_rows_after_report_date(
                raw_stock,
                report_cutoff,
            )
            stock = _through_report_date(
                raw_stock,
                report_cutoff,
            )
        except Exception as exc:
            stock_failures += 1
            data_issues.append(f"{item.symbol}：{type(exc).__name__}: {exc}")
            continue
        successful_stocks += 1
        benchmark = benchmarks.get(item.market)
        if (
            AlertType.CHRONIC_UNDERPERFORMANCE.value in item.rules.enabled_alerts
            and benchmark is None
        ):
            data_issues.append(f"{item.symbol}：缺少 {item.market} 基准，未运行持续跑输检测")

        try:
            outcomes = _detect(item, stock, benchmark)
        except Exception as exc:
            detection_failures += 1
            data_issues.append(f"{item.symbol} detector：{type(exc).__name__}: {exc}")
            continue

        for outcome in outcomes:
            if outcome.status is DetectionStatus.INSUFFICIENT_DATA:
                data_issues.append(
                    f"{item.symbol} {outcome.alert_type.value}：{outcome.reason}"
                )
                continue
            if outcome.status is DetectionStatus.NORMAL:
                if outcome.alert_type is not AlertType.ABNORMAL_VOLUME:
                    observed = str(pd.to_datetime(stock["trade_date"]).max().date())
                    mark_recovered(item.symbol, outcome.alert_type, state, observed)
                continue
            alert = outcome.alert
            if alert is None:
                raise RuntimeError("alert outcome is missing its alert payload")
            record = state.get(state_key(alert.symbol, alert.alert_type), {})
            trading_days = _trading_days_since(
                stock,
                record.get("last_notified_date"),
                alert.observed_on,
            )
            should_notify = decide_notification(
                alert,
                state,
                trading_days_since_notification=trading_days,
                cooldown=item.rules.cooldown_trading_days,
                worsening_step=item.rules.worsening_step,
            )
            mark_detected(alert, state)
            if should_notify:
                new_alerts.append(alert)

    if historical_replay:
        data_issues.insert(0, "历史回放只读模式：不更新实时告警状态，也不发送通知")

    rendered_content = render_watchlist_alerts(
        report_date=report_date,
        alerts=new_alerts,
        data_issues=data_issues,
        checked_count=len(config.items),
    )
    resolved_report_dir = Path(report_dir)
    resolved_report_dir.mkdir(parents=True, exist_ok=True)
    report_path = resolved_report_dir / f"{report_date}.md"
    if report_path.exists():
        previous_content = report_path.read_text(encoding="utf-8").rstrip()
        run_timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        content = (
            f"{previous_content}\n\n---\n\n"
            f"运行时间：{run_timestamp}\n\n"
            f"{rendered_content}"
        )
    else:
        content = rendered_content
    report_path.write_text(content, encoding="utf-8")
    if not historical_replay:
        state_store.save(state)

    pushed = False
    if (
        not historical_replay
        and push
        and notifier is not None
        and new_alerts
        and successful_stocks > 0
    ):
        notifier.send(
            title=f"[{len(new_alerts)}个异常] 自选股异常体检 ({report_date})",
            markdown_content=rendered_content,
        )
        for alert in new_alerts:
            mark_notified(alert, state)
        state_store.save(state)
        pushed = True

    return WatchlistCheckupResult(
        report_path=report_path,
        checked_count=len(config.items),
        new_alert_count=len(new_alerts),
        failure_count=stock_failures + detection_failures + len(benchmark_errors),
        pushed=pushed,
        content_md=content,
    )
