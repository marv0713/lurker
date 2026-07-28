from __future__ import annotations

from datetime import date, datetime
from importlib.metadata import version
from typing import Protocol


CN_MARKET_CLOSED_RANGES_2026: tuple[tuple[date, date], ...] = (
    (date(2026, 1, 1), date(2026, 1, 3)),
    (date(2026, 1, 4), date(2026, 1, 4)),
    (date(2026, 2, 14), date(2026, 2, 14)),
    (date(2026, 2, 15), date(2026, 2, 23)),
    (date(2026, 2, 28), date(2026, 2, 28)),
    (date(2026, 4, 4), date(2026, 4, 6)),
    (date(2026, 5, 1), date(2026, 5, 5)),
    (date(2026, 5, 9), date(2026, 5, 9)),
    (date(2026, 6, 19), date(2026, 6, 21)),
    (date(2026, 9, 20), date(2026, 9, 20)),
    (date(2026, 9, 25), date(2026, 9, 27)),
    (date(2026, 10, 1), date(2026, 10, 7)),
    (date(2026, 10, 10), date(2026, 10, 10)),
)


class TradingCalendarUnavailable(RuntimeError):
    pass


class CnTradingCalendarProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def provider_version(self) -> str: ...

    def sessions_in_range(
        self,
        start: date,
        end: date,
    ) -> tuple[date, ...]: ...


class ExchangeCalendarsCnProvider:
    @property
    def provider_name(self) -> str:
        return "exchange_calendars"

    @property
    def provider_version(self) -> str:
        return version("exchange-calendars")

    def sessions_in_range(
        self,
        start: date,
        end: date,
    ) -> tuple[date, ...]:
        try:
            import exchange_calendars as xcals
            import pandas as pd

            calendar = xcals.get_calendar("XSHG")
            sessions = calendar.sessions_in_range(
                pd.Timestamp(start),
                pd.Timestamp(end),
            )
            normalized = tuple(item.date() for item in sessions)
        except Exception as exc:
            raise TradingCalendarUnavailable(
                f"XSHG calendar unavailable: {exc}"
            ) from exc
        if tuple(sorted(set(normalized))) != normalized:
            raise TradingCalendarUnavailable(
                "XSHG sessions are not strictly increasing and unique"
            )
        return normalized


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_cn_trading_day(day: date | str) -> bool:
    resolved = parse_iso_date(day) if isinstance(day, str) else day
    if resolved.weekday() >= 5:
        return False
    for start, end in CN_MARKET_CLOSED_RANGES_2026:
        if start <= resolved <= end:
            return False
    return True


def all_markets_are_cn(markets: list[str]) -> bool:
    normalized = {market.strip().lower() for market in markets if market.strip()}
    return bool(normalized) and normalized <= {"cn"}
