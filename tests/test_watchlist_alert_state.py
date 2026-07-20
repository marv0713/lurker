from lurker.application.watchlist_alert_state import (
    AlertStateStore,
    decide_notification,
    mark_detected,
    mark_notified,
    mark_recovered,
)
from lurker.signals.anomaly import AlertType, AnomalyAlert


def alert(kind=AlertType.PEAK_DRAWDOWN, severity=0.20, observed_on="2026-07-20"):
    return AnomalyAlert(
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        alert_type=kind,
        observed_on=observed_on,
        severity=severity,
        metrics={},
    )


def test_first_detection_notifies_but_cooldown_suppresses_repeat():
    state = {}
    current = alert()
    assert decide_notification(
        current,
        state,
        trading_days_since_notification=None,
        cooldown=20,
        worsening_step=0.10,
    )
    mark_detected(current, state)
    mark_notified(current, state)
    assert not decide_notification(
        current,
        state,
        trading_days_since_notification=5,
        cooldown=20,
        worsening_step=0.10,
    )


def test_persistent_alert_realerts_when_severity_worsens_ten_points():
    state = {}
    original = alert(severity=0.20)
    mark_detected(original, state)
    mark_notified(original, state)

    assert decide_notification(
        alert(severity=0.30, observed_on="2026-07-21"),
        state,
        trading_days_since_notification=1,
        cooldown=20,
        worsening_step=0.10,
    )


def test_persistent_alert_realerts_after_twenty_trading_days():
    state = {}
    current = alert()
    mark_detected(current, state)
    mark_notified(current, state)

    assert decide_notification(
        alert(observed_on="2026-08-17"),
        state,
        trading_days_since_notification=20,
        cooldown=20,
        worsening_step=0.10,
    )


def test_abnormal_volume_deduplicates_only_the_same_observed_date():
    state = {}
    current = alert(kind=AlertType.ABNORMAL_VOLUME)
    mark_detected(current, state)
    mark_notified(current, state)

    assert not decide_notification(
        current,
        state,
        trading_days_since_notification=0,
        cooldown=20,
        worsening_step=0.10,
    )
    assert decide_notification(
        alert(kind=AlertType.ABNORMAL_VOLUME, observed_on="2026-07-21"),
        state,
        trading_days_since_notification=1,
        cooldown=20,
        worsening_step=0.10,
    )


def test_recovery_makes_later_crossing_a_new_event():
    state = {}
    current = alert()
    mark_detected(current, state)
    mark_notified(current, state)
    mark_recovered("300308.SZ", AlertType.PEAK_DRAWDOWN, state, "2026-07-21")

    assert decide_notification(
        alert(observed_on="2026-07-22"),
        state,
        trading_days_since_notification=2,
        cooldown=20,
        worsening_step=0.10,
    )


def test_state_store_round_trips_with_atomic_replace(tmp_path):
    path = tmp_path / "state.json"
    store = AlertStateStore(path)
    state = {}
    mark_detected(alert(), state)
    store.save(state)

    assert store.load() == state
    assert not list(tmp_path.glob("*.tmp"))
