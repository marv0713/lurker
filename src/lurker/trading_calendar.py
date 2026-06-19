from __future__ import annotations

from datetime import date, datetime


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
