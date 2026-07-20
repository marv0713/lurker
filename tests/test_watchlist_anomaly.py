import pandas as pd
import pytest

from lurker.application.watchlist_alert_state import AlertStateStore
from lurker.application.watchlist_anomaly import _trading_days_since, run_watchlist_anomaly
from lurker.config import WatchlistConfig, WatchlistItemConfig, WatchlistRules


def rules():
    return WatchlistRules(
        enabled_alerts=(
            "abnormal_volume",
            "peak_drawdown",
            "chronic_underperformance",
        ),
        volume_ratio=3.0,
        price_change=0.05,
        drawdown=0.20,
        underperformance_60d=0.15,
        cooldown_trading_days=20,
        worsening_step=0.10,
    )


def config():
    return WatchlistConfig(
        items=(
            WatchlistItemConfig("300308.SZ", "cn", "中际旭创", rules()),
            WatchlistItemConfig("300502.SZ", "cn", "新易盛", rules()),
        )
    )


def price_frame(*, alerting):
    dates = pd.bdate_range(end="2026-07-20", periods=250)
    closes = [100.0] * 250
    volumes = [100.0] * 250
    if alerting:
        closes[-1] = 70.0
        volumes[-1] = 400.0
    return pd.DataFrame(
        {"trade_date": dates, "adj_close": closes, "volume": volumes}
    )


def benchmark_frame():
    dates = pd.bdate_range(end="2026-07-20", periods=250)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "adj_close": [100.0] * 250,
            "volume": [100.0] * 250,
        }
    )


class RecordingNotifier:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sends = []

    def send(self, title, markdown_content):
        if self.fail:
            raise RuntimeError("notification offline")
        self.sends.append((title, markdown_content))


def test_run_watchlist_anomaly_reuses_benchmark_and_marks_successful_push(tmp_path):
    calls = []

    def fetcher(symbol, market, period, *, is_benchmark=False):
        calls.append((symbol, market, is_benchmark))
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    notifier = RecordingNotifier()
    store = AlertStateStore(tmp_path / "state.json")
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=store,
        history_fetcher=fetcher,
        notifier=notifier,
        push=True,
    )

    assert sum(is_benchmark for _, _, is_benchmark in calls) == 1
    assert result.new_alert_count == 6
    assert result.pushed is True
    assert len(notifier.sends) == 1
    saved = store.load()
    assert all(
        record.get("last_notified_date") == "2026-07-20"
        for record in saved.values()
        if record["active"]
    )


def test_no_push_records_detection_but_not_notification(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    store = AlertStateStore(tmp_path / "state.json")
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=store,
        history_fetcher=fetcher,
        notifier=RecordingNotifier(),
        push=False,
    )

    assert result.pushed is False
    assert all(record.get("last_notified_date") is None for record in store.load().values())


def test_notification_failure_leaves_alert_retryable(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    store = AlertStateStore(tmp_path / "state.json")
    with pytest.raises(RuntimeError, match="notification offline"):
        run_watchlist_anomaly(
            config=config(),
            report_date="2026-07-20",
            report_dir=tmp_path / "reports",
            state_store=store,
            history_fetcher=fetcher,
            notifier=RecordingNotifier(fail=True),
            push=True,
        )

    assert all(record.get("last_notified_date") is None for record in store.load().values())


def test_all_stock_failures_write_report_without_push(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        if is_benchmark:
            return benchmark_frame()
        raise RuntimeError("price offline")

    notifier = RecordingNotifier()
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=notifier,
        push=True,
    )

    assert result.failure_count == 2
    assert result.pushed is False
    assert notifier.sends == []
    assert "price offline" in result.content_md
    assert result.report_path.exists()


def test_silent_run_writes_report_without_push(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=False)

    notifier = RecordingNotifier()
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=notifier,
        push=True,
    )

    assert result.new_alert_count == 0
    assert result.pushed is False
    assert notifier.sends == []
    assert "本次没有需要推送的新异常" in result.content_md


def test_partial_stock_failure_still_pushes_successful_alerts(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        if is_benchmark:
            return benchmark_frame()
        if symbol == "300502.SZ":
            raise RuntimeError("one stock offline")
        return price_frame(alerting=True)

    notifier = RecordingNotifier()
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=notifier,
        push=True,
    )

    assert result.new_alert_count == 3
    assert result.failure_count == 1
    assert result.pushed is True
    assert len(notifier.sends) == 1
    assert "one stock offline" in result.content_md


def test_detector_failure_isolated_to_one_symbol(monkeypatch, tmp_path):
    import lurker.application.watchlist_anomaly as module

    original_detect = module._detect

    def sometimes_failing_detect(item, stock, benchmark):
        if item.symbol == "300502.SZ":
            raise ValueError("malformed price values")
        return original_detect(item, stock, benchmark)

    monkeypatch.setattr(module, "_detect", sometimes_failing_detect)

    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=None,
        push=False,
    )

    assert result.new_alert_count == 3
    assert result.failure_count == 1
    assert "300502.SZ detector：ValueError: malformed price values" in result.content_md


def test_item_enabled_alerts_controls_detectors_that_run(tmp_path):
    abnormal_only = WatchlistRules(
        enabled_alerts=("abnormal_volume",),
        volume_ratio=3.0,
        price_change=0.05,
        drawdown=0.20,
        underperformance_60d=0.15,
        cooldown_trading_days=20,
        worsening_step=0.10,
    )
    one_item = WatchlistConfig(
        items=(WatchlistItemConfig("300308.SZ", "cn", "中际旭创", abnormal_only),)
    )

    def fetcher(symbol, market, period, *, is_benchmark=False):
        return price_frame(alerting=True)

    result = run_watchlist_anomaly(
        config=one_item,
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=None,
        push=True,
    )

    assert result.new_alert_count == 1
    assert "巨量异动" in result.content_md
    assert "高位回撤" not in result.content_md
    assert "持续跑输" not in result.content_md


def test_repeating_same_trade_date_does_not_push_twice(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    notifier = RecordingNotifier()
    store = AlertStateStore(tmp_path / "state.json")
    arguments = {
        "config": config(),
        "report_date": "2026-07-20",
        "report_dir": tmp_path / "reports",
        "state_store": store,
        "history_fetcher": fetcher,
        "notifier": notifier,
        "push": True,
    }

    first = run_watchlist_anomaly(**arguments)
    second = run_watchlist_anomaly(**arguments)

    assert first.pushed is True
    assert second.pushed is False
    assert second.new_alert_count == 0
    assert len(notifier.sends) == 1
    content = second.report_path.read_text(encoding="utf-8")
    assert "巨量异动" in content
    assert "本次没有需要推送的新异常" in content
    assert content.count("# 自选股异常体检") == 2


def test_trading_day_cooldown_counts_unique_dates_only_through_observation():
    prices = pd.DataFrame(
        {
            "trade_date": [
                "2026-07-02",
                "2026-07-03",
                "2026-07-03",
                "2026-07-06",
            ]
        }
    )

    assert _trading_days_since(prices, "2026-07-01", "2026-07-03") == 2


def test_report_date_excludes_newer_price_rows(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-17",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=None,
        push=False,
    )

    assert result.new_alert_count == 0
    assert "数据截止日：2026-07-20" not in result.content_md
