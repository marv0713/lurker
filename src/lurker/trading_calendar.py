from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class TradingCalendarUnavailable(RuntimeError):
    pass


class FutureReportDateError(ValueError):
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


CALENDAR_TIMEZONES = {
    "XSHG": "Asia/Shanghai",
    "XHKG": "Asia/Hong_Kong",
}


class ExchangeCalendarsProvider:
    def __init__(self, calendar_name: str) -> None:
        if calendar_name not in CALENDAR_TIMEZONES:
            raise ValueError(f"unsupported exchange calendar: {calendar_name}")
        self.calendar_name = calendar_name

    @property
    def provider_name(self) -> str:
        return "exchange_calendars"

    @property
    def provider_version(self) -> str:
        try:
            return version("exchange-calendars")
        except Exception as exc:
            raise TradingCalendarUnavailable(
                f"{self.calendar_name} calendar unavailable: {exc}"
            ) from exc

    def sessions_in_range(
        self,
        start: date,
        end: date,
    ) -> tuple[date, ...]:
        try:
            import exchange_calendars as xcals
            import pandas as pd

            calendar = xcals.get_calendar(self.calendar_name)
            sessions = calendar.sessions_in_range(
                pd.Timestamp(start),
                pd.Timestamp(end),
            )
            normalized = tuple(item.date() for item in sessions)
        except Exception as exc:
            raise TradingCalendarUnavailable(
                f"{self.calendar_name} calendar unavailable: {exc}"
            ) from exc
        if tuple(sorted(set(normalized))) != normalized:
            raise TradingCalendarUnavailable(
                f"{self.calendar_name} sessions are not strictly increasing and unique"
            )
        return normalized


class ExchangeCalendarsCnProvider(ExchangeCalendarsProvider):
    def __init__(self) -> None:
        super().__init__("XSHG")


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


@dataclass(frozen=True)
class CalendarCache:
    calendar_name: str
    provider: str
    provider_version: str
    generated_at: str
    coverage_start: date
    coverage_end: date
    sessions: tuple[date, ...]

    @classmethod
    def from_dict(
        cls,
        raw: object,
        *,
        expected_calendar: str = "XSHG",
    ) -> CalendarCache:
        if not isinstance(raw, dict):
            raise ValueError("calendar cache must be a mapping")
        if raw.get("schema_version") != 1:
            raise ValueError("unsupported calendar cache schema")
        if raw.get("calendar") != expected_calendar:
            raise ValueError(f"calendar cache must use {expected_calendar}")
        expected_timezone = CALENDAR_TIMEZONES[expected_calendar]
        if raw.get("timezone") != expected_timezone:
            raise ValueError("calendar cache timezone mismatch")
        start = parse_iso_date(str(raw["coverage_start"]))
        end = parse_iso_date(str(raw["coverage_end"]))
        raw_sessions = raw["sessions"]
        if not isinstance(raw_sessions, list):
            raise ValueError("calendar cache sessions must be a list")
        sessions = tuple(parse_iso_date(str(item)) for item in raw_sessions)
        if start > end:
            raise ValueError("calendar cache coverage is reversed")
        if tuple(sorted(set(sessions))) != sessions:
            raise ValueError("calendar cache sessions must be sorted and unique")
        if any(item < start or item > end for item in sessions):
            raise ValueError("calendar cache session outside coverage")
        return cls(
            calendar_name=expected_calendar,
            provider=str(raw["provider"]),
            provider_version=str(raw["provider_version"]),
            generated_at=str(raw["generated_at"]),
            coverage_start=start,
            coverage_end=end,
            sessions=sessions,
        )

    def covers(self, start: date, end: date) -> bool:
        return self.coverage_start <= start and self.coverage_end >= end

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "calendar": self.calendar_name,
            "timezone": CALENDAR_TIMEZONES[self.calendar_name],
            "provider": self.provider,
            "provider_version": self.provider_version,
            "generated_at": self.generated_at,
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "sessions": [item.isoformat() for item in self.sessions],
        }


def _read_cache(path: Path, calendar_name: str = "XSHG") -> CalendarCache | None:
    if not path.exists():
        return None
    try:
        return CalendarCache.from_dict(
            json.loads(path.read_text(encoding="utf-8")),
            expected_calendar=calendar_name,
        )
    # json.JSONDecodeError inherits from ValueError and is covered here.
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _write_cache(path: Path, cache: CalendarCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(cache.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


ProviderFactory = Callable[[], CnTradingCalendarProvider]


class TradingCalendar:
    def __init__(
        self,
        calendar_name: str,
        cache_path: Path,
        *,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        if calendar_name not in CALENDAR_TIMEZONES:
            raise ValueError(f"unsupported exchange calendar: {calendar_name}")
        self.calendar_name = calendar_name
        self.cache_path = Path(cache_path)
        self.provider_factory = provider_factory or (
            lambda: ExchangeCalendarsProvider(calendar_name)
        )

    def _ensure(self, start: date, end: date) -> CalendarCache:
        requested_start = date(start.year, 1, 1)
        requested_end = date(end.year, 12, 31)
        cache = _read_cache(self.cache_path, self.calendar_name)
        if cache is not None and cache.covers(requested_start, requested_end):
            return cache
        if cache is None:
            query_start, query_end = requested_start, requested_end
        else:
            query_start = min(cache.coverage_start, requested_start)
            query_end = max(cache.coverage_end, requested_end)
        try:
            provider = self.provider_factory()
            sessions = provider.sessions_in_range(query_start, query_end)
        except TradingCalendarUnavailable:
            if cache is not None and cache.covers(start, end):
                return cache
            raise
        rebuilt = CalendarCache(
            calendar_name=self.calendar_name,
            provider=provider.provider_name,
            provider_version=provider.provider_version,
            generated_at=datetime.now(SHANGHAI_TZ).isoformat(),
            coverage_start=query_start,
            coverage_end=query_end,
            sessions=sessions,
        )
        _write_cache(self.cache_path, rebuilt)
        return rebuilt

    def sessions_in_range(
        self,
        start: date,
        end: date,
    ) -> tuple[date, ...]:
        if start > end:
            raise ValueError("calendar range start must not exceed end")
        cache = self._ensure(start, end)
        return tuple(
            item for item in cache.sessions if start <= item <= end
        )

    def is_trading_day(self, day: date | str) -> bool:
        resolved = parse_iso_date(day) if isinstance(day, str) else day
        cache = self._ensure(resolved, resolved)
        return resolved in set(cache.sessions)

    def previous_or_same_session(self, day: date | str) -> date:
        resolved = parse_iso_date(day) if isinstance(day, str) else day
        cursor_year = resolved.year
        while True:
            start = date(cursor_year, 1, 1)
            end = (
                resolved
                if cursor_year == resolved.year
                else date(cursor_year, 12, 31)
            )
            sessions = self.sessions_in_range(start, end)
            if sessions:
                return sessions[-1]
            cursor_year -= 1


class CnTradingCalendar(TradingCalendar):
    def __init__(
        self,
        cache_path: Path,
        *,
        provider_factory: ProviderFactory = ExchangeCalendarsCnProvider,
    ) -> None:
        super().__init__(
            "XSHG",
            cache_path,
            provider_factory=provider_factory,
        )


@dataclass(frozen=True)
class ReportDateResolution:
    requested: date
    effective: date | None
    adjusted: bool
    reason: str | None = None


def shanghai_today(now: datetime | None = None) -> date:
    if now is None:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(SHANGHAI_TZ).date()


def _requested_date(value: str | None, today: date) -> date:
    requested = parse_iso_date(value) if value is not None else today
    if requested > today:
        raise FutureReportDateError(
            f"future report date {requested.isoformat()} exceeds "
            f"Shanghai today {today.isoformat()}"
        )
    return requested


def resolve_daily_date(
    value: str | None,
    today: date,
    calendar: CnTradingCalendar,
) -> ReportDateResolution:
    requested = _requested_date(value, today)
    if not calendar.is_trading_day(requested):
        return ReportDateResolution(
            requested=requested,
            effective=None,
            adjusted=False,
            reason="cn market closed",
        )
    return ReportDateResolution(requested, requested, False)


def resolve_weekly_date(
    value: str | None,
    today: date,
    calendar: CnTradingCalendar,
) -> ReportDateResolution:
    requested = _requested_date(value, today)
    effective = calendar.previous_or_same_session(requested)
    return ReportDateResolution(
        requested=requested,
        effective=effective,
        adjusted=effective != requested,
        reason=(
            "previous confirmed CN trading session"
            if effective != requested
            else None
        ),
    )


DEFAULT_CALENDAR_CACHE_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "cache"
    / "trading_calendars"
)
DEFAULT_CALENDAR_CACHE = DEFAULT_CALENDAR_CACHE_DIR / "xshg_sessions.json"


def build_default_cn_calendar(
    cache_path: Path | None = None,
) -> CnTradingCalendar:
    return CnTradingCalendar(cache_path or DEFAULT_CALENDAR_CACHE)


def build_default_personal_calendars(
    cache_dir: Path | None = None,
) -> dict[str, TradingCalendar]:
    root = Path(cache_dir) if cache_dir is not None else DEFAULT_CALENDAR_CACHE_DIR
    return {
        "cn": TradingCalendar("XSHG", root / "xshg_sessions.json"),
        "hk": TradingCalendar("XHKG", root / "xhkg_sessions.json"),
    }


def is_cn_trading_day(
    day: date | str,
    *,
    calendar: CnTradingCalendar | None = None,
) -> bool:
    resolved_calendar = calendar or build_default_cn_calendar()
    return resolved_calendar.is_trading_day(day)


def all_markets_are_cn(markets: list[str]) -> bool:
    normalized = {market.strip().lower() for market in markets if market.strip()}
    return bool(normalized) and normalized <= {"cn"}
