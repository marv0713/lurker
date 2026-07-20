from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from lurker.signals.anomaly import AlertType, AnomalyAlert


AlertState = dict[str, dict[str, Any]]


def state_key(symbol: str, alert_type: AlertType) -> str:
    return f"{symbol.upper()}::{alert_type.value}"


def decide_notification(
    alert: AnomalyAlert,
    state: AlertState,
    *,
    trading_days_since_notification: int | None,
    cooldown: int,
    worsening_step: float,
) -> bool:
    record = state.get(state_key(alert.symbol, alert.alert_type))
    if not record or not record.get("active") or not record.get("last_notified_date"):
        return True
    if alert.alert_type is AlertType.ABNORMAL_VOLUME:
        return record.get("last_notified_date") != alert.observed_on
    if trading_days_since_notification is not None and trading_days_since_notification >= cooldown:
        return True
    previous = float(record.get("last_notified_severity", 0.0))
    return alert.severity - previous >= worsening_step - 1e-12


def mark_detected(alert: AnomalyAlert, state: AlertState) -> None:
    record = state.setdefault(state_key(alert.symbol, alert.alert_type), {})
    record.update(
        {
            "active": True,
            "last_detected_date": alert.observed_on,
            "last_detected_severity": alert.severity,
        }
    )


def mark_notified(alert: AnomalyAlert, state: AlertState) -> None:
    record = state.setdefault(state_key(alert.symbol, alert.alert_type), {})
    record.update(
        {
            "active": True,
            "last_notified_date": alert.observed_on,
            "last_notified_severity": alert.severity,
        }
    )


def mark_recovered(
    symbol: str,
    alert_type: AlertType,
    state: AlertState,
    observed_on: str,
) -> None:
    record = state.get(state_key(symbol, alert_type))
    if record:
        record.update({"active": False, "last_detected_date": observed_on})


class AlertStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AlertState:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("watchlist alert state must be a JSON object")
        return raw

    def save(self, state: AlertState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
