from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from lurker.application.personal_close import run_personal_close
from lurker.config import (
    HkExperimentalSpringConfig,
    PersonalStockConfig,
    PersonalWatchConfig,
)
from lurker.domain.personal_close import CorporateActionCoverage


REPORT_DATE = date(2026, 8, 10)
NOW = datetime(2026, 8, 10, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class Calendar:
    def __init__(self, open_: bool, previous=None):
        self.open = open_
        self.previous = previous
        self.calls = []
        self.previous_calls = []

    def is_trading_day(self, day):
        self.calls.append(day)
        return self.open

    def previous_or_same_session(self, day):
        self.previous_calls.append(day)
        return self.previous or day


class RecordingNotifier:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def send(self, title, markdown_content):
        self.calls.append((title, markdown_content))
        if self.error:
            raise self.error


class CompleteProvider:
    def fetch_many(self, items, report_date):
        return {
            item.symbol: CorporateActionCoverage(actions=(), complete=True)
            for item in items
        }


def prices(symbol, market, report_date, period):
    days = pd.bdate_range(end=report_date, periods=220)
    close = pd.Series([80 + index * 0.1 for index in range(220)], dtype=float)
    return pd.DataFrame(
        {
            "trade_date": days.date,
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "adj_close": close,
            "raw_close": close,
            "volume": [1_000_000.0] * 220,
        }
    )


def config():
    return PersonalWatchConfig(
        holdings=(PersonalStockConfig("300308.SZ", "cn", "中际旭创"),),
        watchlist=(PersonalStockConfig("00700.HK", "hk", "腾讯控股"),),
        hk_experimental_spring=HkExperimentalSpringConfig(),
    )


def kwargs(tmp_path, **overrides):
    values = {
        "config_path": tmp_path / "personal.yaml",
        "report_dir": tmp_path / "reports",
        "state_file": tmp_path / "state.json",
        "report_date": REPORT_DATE,
        "today": REPORT_DATE,
        "now": NOW,
        "period": "2y",
        "no_push": False,
        "force_push": False,
        "notifier": RecordingNotifier(),
        "config_loader": lambda path: config(),
        "calendars": {"cn": Calendar(True), "hk": Calendar(False)},
        "price_loader": prices,
        "action_providers": {"cn": CompleteProvider(), "hk": CompleteProvider()},
    }
    values.update(overrides)
    return values


def test_both_configured_markets_closed_skips_without_prices_or_report(tmp_path):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("price loader must not run")

    cn_calendar = Calendar(False)
    hk_calendar = Calendar(False)
    result = run_personal_close(
        **kwargs(
            tmp_path,
            calendars={"cn": cn_calendar, "hk": hk_calendar},
            price_loader=forbidden,
        )
    )

    assert result.status == "skipped_markets_closed"
    assert result.report_path is None
    assert called is False
    assert cn_calendar.previous_calls == []
    assert hk_calendar.previous_calls == []


def test_same_day_rerun_overwrites_report_but_does_not_repush(tmp_path):
    notifier = RecordingNotifier()
    run_kwargs = kwargs(tmp_path, notifier=notifier)

    first = run_personal_close(**run_kwargs)
    first.report_path.write_text("stale", encoding="utf-8")
    second = run_personal_close(**run_kwargs)

    assert len(notifier.calls) == 1
    assert second.report_path.read_text(encoding="utf-8") == second.content_md
    assert second.push_status == "already_accepted"
    assert "中际旭创（300308.SZ）" in second.content_md
    assert "腾讯控股（00700.HK）" in second.content_md


def test_force_push_resends_and_updates_acceptance_timestamp(tmp_path):
    notifier = RecordingNotifier()
    run_personal_close(**kwargs(tmp_path, notifier=notifier))

    later = NOW + timedelta(minutes=5)
    result = run_personal_close(
        **kwargs(tmp_path, notifier=notifier, force_push=True, now=later)
    )

    assert len(notifier.calls) == 2
    assert result.push_status == "accepted"
    assert later.isoformat() in (tmp_path / "state.json").read_text(encoding="utf-8")


def test_historical_and_no_push_runs_never_send_or_write_state(tmp_path):
    notifier = RecordingNotifier()
    historical = run_personal_close(
        **kwargs(
            tmp_path,
            notifier=notifier,
            report_date=REPORT_DATE - timedelta(days=3),
        )
    )
    no_push = run_personal_close(
        **kwargs(tmp_path, notifier=notifier, no_push=True)
    )

    assert historical.push_status == "historical_read_only"
    assert no_push.push_status == "disabled"
    assert notifier.calls == []
    assert not (tmp_path / "state.json").exists()


def test_price_failure_isolated_and_report_still_contains_stock(tmp_path):
    def flaky(symbol, market, report_date, period):
        if symbol == "300308.SZ":
            raise RuntimeError("source down")
        return prices(symbol, market, report_date, period)

    result = run_personal_close(**kwargs(tmp_path, price_loader=flaky, no_push=True))

    assert "中际旭创（300308.SZ）" in result.content_md
    assert "行情获取失败：source down" in result.content_md
    assert "腾讯控股（00700.HK）" in result.content_md


def test_open_market_stale_price_is_reported_as_incomplete(tmp_path):
    def stale(symbol, market, report_date, period):
        frame = prices(symbol, market, report_date, period)
        frame["trade_date"] = [day - timedelta(days=3) for day in frame["trade_date"]]
        return frame

    personal_config = PersonalWatchConfig(
        holdings=(PersonalStockConfig("300308.SZ", "cn", "中际旭创"),),
        watchlist=(),
        hk_experimental_spring=HkExperimentalSpringConfig(),
    )

    result = run_personal_close(
        **kwargs(
            tmp_path,
            config_loader=lambda path: personal_config,
            calendars={"cn": Calendar(True)},
            price_loader=stale,
            action_providers={"cn": CompleteProvider()},
            no_push=True,
        )
    )

    assert "开市日行情未更新：最新 2026-08-07" in result.content_md
    assert "部分数据不完整，详见数据质量" in result.content_md


def test_closed_market_price_must_match_most_recent_session(tmp_path):
    expected = REPORT_DATE - timedelta(days=3)

    def stale(symbol, market, report_date, period):
        frame = prices(symbol, market, report_date, period)
        if market == "hk":
            frame["trade_date"] = [day - timedelta(days=4) for day in frame["trade_date"]]
        return frame

    personal_config = PersonalWatchConfig(
        holdings=(PersonalStockConfig("300308.SZ", "cn", "中际旭创"),),
        watchlist=(PersonalStockConfig("00700.HK", "hk", "腾讯控股"),),
        hk_experimental_spring=HkExperimentalSpringConfig(),
    )

    result = run_personal_close(
        **kwargs(
            tmp_path,
            config_loader=lambda path: personal_config,
            calendars={"hk": Calendar(False, previous=expected), "cn": Calendar(True)},
            price_loader=stale,
            action_providers={"cn": CompleteProvider(), "hk": CompleteProvider()},
            no_push=True,
        )
    )

    assert "休市市场行情未对齐：应截止 2026-08-07，最新 2026-08-06" in result.content_md


def test_notification_failure_keeps_report_and_does_not_mark_state(tmp_path):
    notifier = RecordingNotifier(RuntimeError("rejected"))

    with pytest.raises(RuntimeError, match="rejected"):
        run_personal_close(**kwargs(tmp_path, notifier=notifier))

    assert (tmp_path / "reports" / "2026-08-10.md").exists()
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"period": "1y"}, "period must equal 2y"),
        ({"no_push": True, "force_push": True}, "mutually exclusive"),
        (
            {"report_date": REPORT_DATE - timedelta(days=1), "force_push": True},
            "historical",
        ),
        ({"report_date": REPORT_DATE + timedelta(days=1)}, "future"),
    ],
)
def test_invalid_parameters_fail_before_market_data(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        run_personal_close(**kwargs(tmp_path, **changes))
