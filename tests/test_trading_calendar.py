from lurker.trading_calendar import is_cn_trading_day


def test_cn_trading_calendar_marks_2026_dragon_boat_holiday_closed():
    assert is_cn_trading_day("2026-06-19") is False
    assert is_cn_trading_day("2026-06-20") is False
    assert is_cn_trading_day("2026-06-21") is False


def test_cn_trading_calendar_marks_next_session_open():
    assert is_cn_trading_day("2026-06-22") is True
