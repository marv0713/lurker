from datetime import date
from importlib.metadata import version
from pathlib import Path

from lurker.trading_calendar import (
    ExchangeCalendarsCnProvider,
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
