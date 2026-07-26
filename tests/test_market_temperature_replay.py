from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from lurker.application.temperature_replay import (
    build_rollout_artifact,
    current_rules_fingerprint,
    load_truth_table,
    replay_temperature_records,
    summarize_replay,
)
from lurker.cli import _check_temperature_gate
from lurker.trading_calendar import is_cn_trading_day


FIXTURES = Path(__file__).parent / "fixtures"


def test_synthetic_truth_table_all_cases_match():
    cases = load_truth_table(FIXTURES / "etf_synthetic_truth_table.json")

    assert len(cases) == 25
    failures = [
        case["case_id"]
        for case in cases
        if case["actual"] != case["expected"]
    ]
    assert failures == []


def test_replay_outputs_per_day_raw_input_rule_diff_and_reason():
    records = [
        {
            "date": "2026-04-24",
            "market_flow": {
                "trade_date": "2026-04-24",
                "main_net_inflow": -10.0,
                "super_large_net_inflow": -5.0,
                "availability": "fresh",
            },
            "core_etfs": _complete_batch("2026-04-24", expansion=1.0),
            "margin": {
                "trade_date": "20260424",
                "margin_balance_change": -1.0,
                "availability": "fresh",
            },
        }
    ]

    rows = replay_temperature_records(records)

    assert rows == [
        {
            "date": "2026-04-24",
            "raw_input": records[0],
            "old_status": "防守",
            "new_status": "防守",
            "change_reason": "新旧规则一致",
            "etf_status": "inactive",
            "margin_signal": "weakening",
            "quality_notes": [],
        }
    ]


def test_replay_counts_status_distribution_and_unknown_degradation_days():
    records = [
        _replay_record("2026-04-24", 10.0, 5.0, expansion=1.3, margin_change=1.0),
        _replay_record("2026-04-27", -10.0, -5.0, expansion=1.0, margin_change=-1.0),
        {
            "date": "2026-04-28",
            "market_flow": {
                "trade_date": "2026-04-28",
                "main_net_inflow": 10.0,
                "super_large_net_inflow": 5.0,
                "availability": "fresh",
            },
            "core_etfs": {
                "configured_symbols": ["510300.SH"],
                "items": [],
                "failures": [{"symbol": "510300.SH", "reason": "timeout"}],
                "generated_at": "2026-04-28T08:00:00+00:00",
                "schema_version": 1,
            },
            "margin": {
                "trade_date": "20260428",
                "margin_balance_change": None,
                "availability": "fresh",
            },
        },
    ]

    summary = summarize_replay(replay_temperature_records(records))

    assert summary["trading_days"] == 3
    assert summary["distribution"] == {"进攻": 1, "观察": 1, "防守": 1}
    assert summary["max_ratio"] == pytest.approx(1 / 3)
    assert summary["unknown_degradation_days"] == 1


def test_real_60d_replay_has_auditable_source_provenance():
    records = json.loads(
        (FIXTURES / "etf_60d_replay.json").read_text(encoding="utf-8")
    )

    summary = summarize_replay(replay_temperature_records(records))

    assert len(records) == 60
    assert records[0]["date"] == "2026-04-24"
    assert records[-1]["date"] == "2026-07-22"
    assert summary == {
        "trading_days": 60,
        "distribution": {"进攻": 0, "观察": 60, "防守": 0},
        "max_ratio": 1.0,
        "unknown_degradation_days": 60,
    }
    assert {
        item["source"]
        for record in records
        for item in record["core_etfs"]["items"]
    } == {"akshare_fund_etf_hist_sina"}
    assert {record["margin"]["source"] for record in records} == {
        "akshare_jin10_margin_sh_sz"
    }
    assert {record["market_flow"]["source"] for record in records} == {
        "unavailable"
    }


def test_build_rollout_artifact_is_unapproved_and_hashes_replay(tmp_path):
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(json.dumps([{"date": "2026-04-24"}]), encoding="utf-8")
    rows = [
        {"date": f"2026-05-{day:02d}", "new_status": "观察", "quality_notes": []}
        for day in range(1, 31)
    ] + [
        {"date": f"2026-06-{day:02d}", "new_status": "进攻", "quality_notes": []}
        for day in range(1, 31)
    ]

    artifact = build_rollout_artifact(
        replay_path=replay_path,
        replay_rows=rows,
        replay_start="2026-05-01",
        replay_end="2026-06-30",
    )

    expected_hash = hashlib.sha256(replay_path.read_bytes()).hexdigest()
    assert artifact["approved"] is False
    assert artifact["approved_by"] is None
    assert artifact["approved_at"] is None
    assert artifact["trading_days"] == 60
    assert artifact["distribution"] == {"进攻": 30, "观察": 30, "防守": 0}
    assert artifact["rules_fingerprint"] == current_rules_fingerprint()
    assert artifact["replay_sha256"] == f"sha256:{expected_hash}"


@pytest.mark.parametrize(
    ("mutation", "reason_fragment"),
    [
        ({"approved": False}, "尚未通过人工审查"),
        ({"rules_version": "old"}, "规则版本已变更"),
        ({"rules_fingerprint": "sha256:bad"}, "规则指纹不一致"),
        ({"trading_days": 59}, "历史不足60日"),
        ({"approved_by": None}, "审批信息不完整"),
        ({"approved_at": None}, "审批信息不完整"),
    ],
)
def test_temperature_gate_blocks_invalid_artifacts(
    tmp_path,
    mutation,
    reason_fragment,
):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    artifact.update(mutation)
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert reason_fragment in reason


def test_temperature_gate_blocks_actual_distribution_over_80_percent(tmp_path):
    replay_path, artifact_path, _ = _approved_artifact(
        tmp_path,
        distribution={"进攻": 49, "观察": 10, "防守": 1},
    )

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "超过80%" in reason


def test_temperature_gate_blocks_missing_artifact(tmp_path):
    allowed, reason = _check_temperature_gate(
        tmp_path / "missing.json",
        replay_path=tmp_path / "replay.json",
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "缺少 rollout artifact" in reason


def test_temperature_gate_blocks_changed_replay_hash(tmp_path):
    replay_path, artifact_path, _ = _approved_artifact(tmp_path)
    replay_path.write_text("changed", encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "回放文件已变化" in reason


def test_temperature_gate_blocks_changed_replay_path(tmp_path):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    artifact["replay_path"] = "other.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "回放路径不一致" in reason


def test_temperature_gate_resolves_project_relative_replay_path(
    tmp_path,
    monkeypatch,
):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    project_relative = replay_path.relative_to(tmp_path)
    artifact["replay_path"] = str(project_relative)
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr("lurker.cli.ROOT", tmp_path)

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is True
    assert reason == ""


def test_temperature_gate_recomputes_distribution_total_and_ratio(tmp_path):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    artifact["max_ratio"] = 0.1
    artifact["distribution"] = {"进攻": 20, "观察": 20, "防守": 19}
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "分布与交易日数不一致" in reason


def test_temperature_gate_allows_approved_distribution_under_80(tmp_path):
    replay_path, artifact_path, _ = _approved_artifact(tmp_path)

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is True
    assert reason == ""


def test_temperature_gate_warns_at_exactly_80_percent(tmp_path):
    replay_path, artifact_path, _ = _approved_artifact(
        tmp_path,
        distribution={"进攻": 48, "观察": 12, "防守": 0},
    )

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is True
    assert "恰好80%" in reason


def test_temperature_gate_rejects_forged_summary_for_empty_replay(tmp_path):
    replay_path = tmp_path / "replay.json"
    replay_path.write_text("[]", encoding="utf-8")
    artifact = _approved_artifact_payload(
        replay_path,
        trading_days=60,
        distribution={"进攻": 20, "观察": 20, "防守": 20},
        replay_start="2026-04-24",
        replay_end="2026-07-22",
    )
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "回放统计与 artifact 不一致" in reason


def test_temperature_gate_rejects_forged_distribution_with_same_total(tmp_path):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    artifact["distribution"] = {"进攻": 21, "观察": 19, "防守": 20}
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "回放统计与 artifact 不一致" in reason


def test_temperature_gate_rejects_forged_replay_dates(tmp_path):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    artifact["replay_start"] = "2026-01-01"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "回放日期范围与 artifact 不一致" in reason


def test_temperature_gate_rejects_non_integer_distribution_counts(tmp_path):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    artifact["distribution"] = {"进攻": 20.0, "观察": 20, "防守": 20}
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "状态分布格式错误" in reason


def test_temperature_gate_rejects_duplicate_or_unsorted_replay_dates(tmp_path):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    records = json.loads(replay_path.read_text(encoding="utf-8"))
    records[1]["date"] = records[0]["date"]
    replay_path.write_text(json.dumps(records), encoding="utf-8")
    artifact["replay_sha256"] = (
        "sha256:" + hashlib.sha256(replay_path.read_bytes()).hexdigest()
    )
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "回放日期必须严格递增且不重复" in reason


def test_temperature_gate_rejects_non_trading_replay_dates(tmp_path):
    replay_path, artifact_path, _ = _approved_artifact(
        tmp_path,
        include_non_trading=True,
    )

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "回放包含非交易日" in reason


def test_temperature_gate_fails_closed_when_replay_path_is_directory(tmp_path):
    replay_path, artifact_path, artifact = _approved_artifact(tmp_path)
    replay_directory = tmp_path / "replay-directory"
    replay_directory.mkdir()
    artifact["replay_path"] = str(replay_directory)
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_directory,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is False
    assert "回放文件无法读取" in reason


def test_temperature_gate_hashes_and_executes_same_replay_bytes(
    tmp_path,
    monkeypatch,
):
    replay_path, artifact_path, _ = _approved_artifact(tmp_path)
    original_read_text = Path.read_text

    def guarded_read_text(path, *args, **kwargs):
        if path.resolve() == replay_path.resolve():
            raise AssertionError("replay path must not be read a second time")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )

    assert allowed is True
    assert reason == ""


def _complete_batch(trade_date: str, *, expansion: float) -> dict:
    status = "active" if expansion >= 1.2 else "inactive"
    return {
        "configured_symbols": ["510300.SH"],
        "items": [
            {
                "symbol": "510300.SH",
                "name": "沪深300ETF",
                "trade_date": trade_date,
                "current_turnover": 100.0,
                "avg_turnover_20d": 100.0 / expansion,
                "turnover_expansion": expansion,
                "shares": None,
                "shares_date": None,
                "status": status,
                "source": "fixture",
                "availability": "turnover_only",
                "error": None,
            }
        ],
        "failures": [],
        "generated_at": f"{trade_date}T08:00:00+00:00",
        "schema_version": 1,
    }


def _replay_record(
    trade_date: str,
    main_flow: float,
    super_flow: float,
    *,
    expansion: float,
    margin_change: float | None,
) -> dict:
    return {
        "date": trade_date,
        "market_flow": {
            "trade_date": trade_date,
            "main_net_inflow": main_flow,
            "super_large_net_inflow": super_flow,
            "availability": "fresh",
        },
        "core_etfs": _complete_batch(trade_date, expansion=expansion),
        "margin": {
            "trade_date": trade_date.replace("-", ""),
            "margin_balance_change": margin_change,
            "availability": "fresh",
        },
    }


def _approved_artifact(
    tmp_path,
    *,
    distribution: dict[str, int] | None = None,
    include_non_trading: bool = False,
):
    counts = distribution or {"进攻": 20, "观察": 20, "防守": 20}
    records = _gate_records(
        counts,
        include_non_trading=include_non_trading,
    )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(json.dumps(records), encoding="utf-8")
    artifact = _approved_artifact_payload(
        replay_path,
        trading_days=len(records),
        distribution=counts,
        replay_start=records[0]["date"],
        replay_end=records[-1]["date"],
    )
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return replay_path, artifact_path, artifact


def _approved_artifact_payload(
    replay_path: Path,
    *,
    trading_days: int,
    distribution: dict[str, int],
    replay_start: str,
    replay_end: str,
) -> dict:
    replay_hash = hashlib.sha256(replay_path.read_bytes()).hexdigest()
    return {
        "rules_version": "2026-07-23",
        "rules_fingerprint": current_rules_fingerprint(),
        "replay_path": str(replay_path),
        "replay_start": replay_start,
        "replay_end": replay_end,
        "trading_days": trading_days,
        "distribution": distribution,
        "max_ratio": max(distribution.values()) / trading_days,
        "approved": True,
        "approved_by": "reviewer",
        "approved_at": "2026-07-25T12:00:00+08:00",
        "replay_sha256": f"sha256:{replay_hash}",
        "notes": "approved",
    }


def _gate_records(
    distribution: dict[str, int],
    *,
    include_non_trading: bool,
) -> list[dict]:
    factories = {
        "进攻": lambda day: _replay_record(
            day, 10.0, 5.0, expansion=1.3, margin_change=1.0
        ),
        "观察": lambda day: _replay_record(
            day, 10.0, 5.0, expansion=1.0, margin_change=None
        ),
        "防守": lambda day: _replay_record(
            day, -10.0, -5.0, expansion=1.0, margin_change=-1.0
        ),
    }
    records = []
    cursor = date(2026, 4, 24)
    for status in ("进攻", "观察", "防守"):
        for _ in range(distribution[status]):
            while not include_non_trading and not is_cn_trading_day(cursor):
                cursor += timedelta(days=1)
            records.append(factories[status](cursor.isoformat()))
            cursor += timedelta(days=1)
    return records
