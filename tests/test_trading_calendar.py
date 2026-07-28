import json
from datetime import date
from importlib.metadata import version
from pathlib import Path

import pytest

from lurker.trading_calendar import (
    CnTradingCalendar,
    ExchangeCalendarsCnProvider,
    TradingCalendarUnavailable,
    is_cn_trading_day,
)


ROOT = Path(__file__).resolve().parents[1]


def test_cn_trading_calendar_marks_2026_dragon_boat_holiday_closed():
    assert is_cn_trading_day("2026-06-19") is False
    assert is_cn_trading_day("2026-06-20") is False
    assert is_cn_trading_day("2026-06-21") is False


def test_cn_trading_calendar_marks_next_session_open():
    assert is_cn_trading_day("2026-06-22") is True


def test_ci_constraint_pins_exchange_calendars_baseline():
    text = (ROOT / "requirements" / "ci-constraints.txt").read_text(
        encoding="utf-8"
    )
    assert "exchange_calendars==4.13.2" in text.splitlines()


def test_exchange_calendar_provider_contract():
    provider = ExchangeCalendarsCnProvider()
    sessions = provider.sessions_in_range(
        date(2026, 6, 18),
        date(2026, 6, 22),
    )
    assert provider.provider_name == "exchange_calendars"
    assert provider.provider_version == version("exchange-calendars")
    assert sessions == (date(2026, 6, 18), date(2026, 6, 22))


class FakeProvider:
    def __init__(self, sessions, *, provider_version="4.13.2", error=None):
        self._sessions = tuple(sessions)
        self._version = provider_version
        self._error = error
        self.calls = []

    @property
    def provider_name(self):
        return "fake"

    @property
    def provider_version(self):
        return self._version

    def sessions_in_range(self, start, end):
        self.calls.append((start, end))
        if self._error is not None:
            raise self._error
        return tuple(day for day in self._sessions if start <= day <= end)


def _cache(path, *, start, end, sessions, provider_version="old"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calendar": "XSHG",
                "timezone": "Asia/Shanghai",
                "provider": "fake",
                "provider_version": provider_version,
                "generated_at": "2026-07-28T10:00:00+08:00",
                "coverage_start": start.isoformat(),
                "coverage_end": end.isoformat(),
                "sessions": [item.isoformat() for item in sessions],
            }
        ),
        encoding="utf-8",
    )


def test_sufficient_old_cache_never_initializes_provider(tmp_path):
    cache_path = tmp_path / "calendar.json"
    _cache(
        cache_path,
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        sessions=[date(2026, 6, 18), date(2026, 6, 22)],
    )
    initialized = []

    def factory():
        initialized.append(True)
        return FakeProvider([])

    calendar = CnTradingCalendar(cache_path, provider_factory=factory)
    assert calendar.is_trading_day(date(2026, 6, 18)) is True
    assert calendar.is_trading_day(date(2026, 6, 19)) is False
    assert initialized == []


def test_insufficient_cache_requeries_full_union_and_upgrades_version(tmp_path):
    cache_path = tmp_path / "calendar.json"
    _cache(
        cache_path,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        sessions=[date(2025, 12, 31)],
    )
    provider = FakeProvider(
        [date(2025, 12, 31), date(2026, 1, 5)],
        provider_version="new",
    )
    calendar = CnTradingCalendar(
        cache_path,
        provider_factory=lambda: provider,
    )
    assert calendar.is_trading_day(date(2026, 1, 5)) is True
    assert provider.calls == [(date(2025, 1, 1), date(2026, 12, 31))]
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["provider_version"] == "new"
    assert saved["coverage_start"] == "2025-01-01"
    assert saved["coverage_end"] == "2026-12-31"


def test_corrupt_cache_rebuilds_requested_year_without_merging(tmp_path):
    cache_path = tmp_path / "calendar.json"
    cache_path.write_text("{broken", encoding="utf-8")
    provider = FakeProvider([date(2027, 1, 4)])
    calendar = CnTradingCalendar(
        cache_path,
        provider_factory=lambda: provider,
    )
    assert calendar.is_trading_day(date(2027, 1, 4)) is True
    assert provider.calls == [(date(2027, 1, 1), date(2027, 12, 31))]


def test_provider_failure_with_insufficient_cache_fails_closed(tmp_path):
    calendar = CnTradingCalendar(
        tmp_path / "missing.json",
        provider_factory=lambda: FakeProvider(
            [],
            error=TradingCalendarUnavailable("offline"),
        ),
    )
    with pytest.raises(TradingCalendarUnavailable, match="offline"):
        calendar.is_trading_day(date(2027, 1, 4))


def test_atomic_write_failure_preserves_previous_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "calendar.json"
    _cache(
        cache_path,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        sessions=[date(2025, 12, 31)],
    )
    original = cache_path.read_bytes()
    provider = FakeProvider([date(2025, 12, 31), date(2026, 1, 5)])

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr("lurker.trading_calendar.os.replace", fail_replace)
    calendar = CnTradingCalendar(
        cache_path,
        provider_factory=lambda: provider,
    )
    with pytest.raises(OSError, match="replace failed"):
        calendar.is_trading_day(date(2026, 1, 5))
    assert cache_path.read_bytes() == original
