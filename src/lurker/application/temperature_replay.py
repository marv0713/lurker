"""Deterministic replay and rollout controls for market-temperature rules."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from lurker.application.market_temperature import (
    classify_market_temperature,
    prepare_temperature_inputs,
)
from lurker.ingest.etf_flows import CoreEtfBatch


RULES_VERSION = "2026-07-23"
ETF_ACTIVE_THRESHOLD = 1.2
REQUIRED_ETF_ROLES = ("chinext", "csi300", "csi500", "csi_a500")
_STATUSES = ("进攻", "观察", "防守")
_SHANGHAI_TZ = timezone(timedelta(hours=8))


def current_rules_fingerprint() -> str:
    """Return a stable fingerprint for every rollout-sensitive rule."""
    payload = {
        "rules_version": RULES_VERSION,
        "etf_threshold": ETF_ACTIVE_THRESHOLD,
        "required_etf_roles": list(REQUIRED_ETF_ROLES),
        "attack_confirmation": "etf_active_or_margin_supportive",
        "defense_confirmation": "etf_inactive_or_margin_weakening",
        "margin_zero": "unknown",
        "market_flow_zero": "neutral",
        "overheated_threshold": None,
        "truth_table_version": 1,
        "freshness": {
            "timezone": "Asia/Shanghai",
            "market_close_cutoff": "15:30",
            "stale_result": "unknown",
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def sha256_file(path: Path) -> str:
    """Return a tagged SHA256 digest for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def load_truth_table(path: Path) -> list[dict[str, Any]]:
    """Load synthetic truth-table cases and evaluate their actual result."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("truth table fixture must be a JSON list")

    cases: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("truth table case must be an object")
        market_flow = _decode_special_numbers(row.get("market_flow", {}))
        actual = classify_market_temperature(
            market_flow=market_flow,
            etf_status=str(row.get("etf_status", "unknown")),
            margin_signal=str(row.get("margin_signal", "unknown")),
        )
        cases.append({**row, "market_flow": market_flow, "actual": actual})
    return cases


def replay_temperature_records(
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replay raw daily facts through both legacy and current temperature rules."""
    rows: list[dict[str, Any]] = []
    for record in records:
        report_date = str(record.get("date", ""))
        if not report_date:
            raise ValueError("replay record is missing date")

        batch = CoreEtfBatch.from_dict(record.get("core_etfs", {}))
        prepared = prepare_temperature_inputs(
            market_flow=record.get("market_flow", {}),
            core_etfs_batch=batch,
            margin=record.get("margin", {}),
            report_date=report_date,
            is_trading_day=lambda _: True,
            now=datetime.combine(
                datetime.fromisoformat(report_date).date(),
                time(16, 0),
                tzinfo=_SHANGHAI_TZ,
            ),
        )
        new_status = classify_market_temperature(
            market_flow=prepared.market_flow,
            etf_status=prepared.etf_status,
            margin_signal=prepared.margin_signal,
        )
        old_status = _classify_legacy_temperature(record)
        quality_notes = _quality_notes(
            record,
            etf_status=prepared.etf_status,
            margin_signal=prepared.margin_signal,
        )
        rows.append(
            {
                "date": report_date,
                "raw_input": record,
                "old_status": old_status,
                "new_status": new_status,
                "change_reason": _change_reason(
                    old_status,
                    new_status,
                    quality_notes=quality_notes,
                ),
                "etf_status": prepared.etf_status,
                "margin_signal": prepared.margin_signal,
                "quality_notes": quality_notes,
            }
        )
    return rows


def summarize_replay(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize distribution and missing-data degradations from replay rows."""
    materialized = list(rows)
    counter = Counter(str(row["new_status"]) for row in materialized)
    distribution = {status: counter.get(status, 0) for status in _STATUSES}
    trading_days = len(materialized)
    max_ratio = (
        max(distribution.values(), default=0) / trading_days
        if trading_days
        else 0.0
    )
    unknown_degradation_days = sum(
        1
        for row in materialized
        if row.get("new_status") == "观察" and row.get("quality_notes")
    )
    return {
        "trading_days": trading_days,
        "distribution": distribution,
        "max_ratio": max_ratio,
        "unknown_degradation_days": unknown_degradation_days,
    }


def build_rollout_artifact(
    *,
    replay_path: Path,
    replay_rows: Iterable[dict[str, Any]],
    replay_start: str,
    replay_end: str,
) -> dict[str, Any]:
    """Build an unapproved rollout artifact from computed replay output."""
    summary = summarize_replay(replay_rows)
    return {
        "rules_version": RULES_VERSION,
        "rules_fingerprint": current_rules_fingerprint(),
        "replay_path": str(replay_path),
        "replay_start": replay_start,
        "replay_end": replay_end,
        "trading_days": summary["trading_days"],
        "distribution": summary["distribution"],
        "max_ratio": summary["max_ratio"],
        "approved": False,
        "approved_by": None,
        "approved_at": None,
        "replay_sha256": sha256_file(replay_path),
        "notes": "待人工审查回放结果后将 approved 改为 true",
    }


def _decode_special_numbers(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _decode_special_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_special_numbers(item) for item in value]
    if value == "NaN":
        return float("nan")
    if value == "Infinity":
        return float("inf")
    if value == "-Infinity":
        return float("-inf")
    return value


def _classify_legacy_temperature(record: dict[str, Any]) -> str:
    market_flow = record.get("market_flow", {})
    main_flow = _legacy_float(market_flow.get("main_net_inflow"))
    super_flow = _legacy_float(market_flow.get("super_large_net_inflow"))

    core_etfs = record.get("core_etfs", {})
    items = core_etfs.get("items", []) if isinstance(core_etfs, dict) else core_etfs
    etf_active = any(
        _legacy_float(item.get("turnover_expansion")) >= ETF_ACTIVE_THRESHOLD
        for item in items
        if isinstance(item, dict)
    )

    margin = record.get("margin", {})
    margin_change = _legacy_float(margin.get("margin_balance_change"))
    if main_flow > 0 and super_flow > 0 and etf_active and margin_change >= 0:
        return "进攻"
    if main_flow < 0 and super_flow < 0 and not etf_active:
        return "防守"
    return "观察"


def _legacy_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _quality_notes(
    record: dict[str, Any],
    *,
    etf_status: str,
    margin_signal: str,
) -> list[str]:
    notes: list[str] = []
    market_flow = record.get("market_flow", {})
    if (
        market_flow.get("availability") != "fresh"
        or market_flow.get("main_net_inflow") is None
        or market_flow.get("super_large_net_inflow") is None
    ):
        notes.append("大盘资金缺失或非当日")

    core_etfs = record.get("core_etfs", {})
    if etf_status == "unknown":
        failures = core_etfs.get("failures", []) if isinstance(core_etfs, dict) else []
        if failures:
            notes.append("核心 ETF 采集不完整")
        else:
            notes.append("核心 ETF 状态未知")

    margin = record.get("margin", {})
    if margin_signal == "unknown" and (
        margin.get("availability") != "fresh"
        or margin.get("margin_balance_change") is None
    ):
        notes.append("两融方向未知")
    return notes


def _change_reason(
    old_status: str,
    new_status: str,
    *,
    quality_notes: list[str],
) -> str:
    if old_status == new_status:
        return "新旧规则一致"
    if quality_notes:
        return f"{old_status}→{new_status}：{'；'.join(quality_notes)}"
    return f"{old_status}→{new_status}：采用 ETF/两融独立确认真值表"
