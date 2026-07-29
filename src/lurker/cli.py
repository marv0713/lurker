import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from typing import Any
import yaml

from lurker.application.price_snapshot import (
    FilePriceSnapshotStore,
    collect_price_snapshot_batch,
    collect_price_snapshots,
    render_price_snapshot,
    select_price_snapshot_rows,
)
from lurker.application.flow_snapshot import (
    FileFlowSnapshotStore,
    collect_flow_snapshot,
)
from lurker.application.run_daily import run_daily
from lurker.application.strategy_runner import (
    StrategyContext,
    build_default_strategy_configs,
    load_strategy_configs,
    render_strategy_results,
    run_strategies,
    select_strategy_configs,
)
from lurker.cli_dispatch import dispatch_command
from lurker.cli_parser import build_parser
from lurker.config import load_monthly_macro_config
from lurker.ingest.constituents import load_resolved_theme_seed_symbols
from lurker.ingest.macro_monthly import (
    MonthlyMacroSnapshotStore,
    collect_monthly_macro_snapshot,
)
from lurker.pipeline import rank_candidates
from lurker.reports.daily_report import render_daily_report
from lurker.reports.models import DailyReport
from lurker.reports.trend_card import render_trend_card
from lurker.trading_calendar import (
    CnTradingCalendar,
    FutureReportDateError,
    ReportDateResolution,
    TradingCalendarUnavailable,
    all_markets_are_cn,
    build_default_cn_calendar,
    is_cn_trading_day,
    parse_iso_date,
    resolve_daily_date,
    resolve_weekly_date,
    shanghai_today,
)
from lurker.universe.resolved_seed_pool import (
    build_resolved_seed_pool,
    extract_seed_symbols,
    load_resolved_seed_pool,
    save_resolved_seed_pool,
)


ROOT = Path(__file__).resolve().parents[2]
NON_BLOCKING_FLOW_SOURCES = frozenset({"stock_flows", "margin", "core_etfs"})


class DailyJobFailed(RuntimeError):
    """Daily job failed after emitting an operator-visible status."""


def _send_daily_failure_notification(
    *,
    report_date: str,
    stage: str,
    reason: str,
    notifier: Any,
) -> None:
    notifier.send(
        title=f"[故障] 职业资金雷达日报 {report_date}",
        markdown_content=(
            "# 日报任务故障\n\n"
            f"- 日期：{report_date}\n"
            f"- 阶段：{stage}\n"
            f"- 原因：{reason}\n"
        ),
    )


def run_daily_job_with_failure_notification(
    *,
    action: Any,
    report_date: str,
    push: bool,
    notifier: Any,
) -> str:
    try:
        return action()
    except DailyJobFailed:
        raise
    except Exception as exc:
        if push:
            try:
                _send_daily_failure_notification(
                    report_date=report_date,
                    stage="daily_job",
                    reason=f"{type(exc).__name__}: {exc}",
                    notifier=notifier,
                )
            except Exception:
                pass
        raise


def _flow_degradation_reasons(flow_snapshot: dict | None) -> list[str]:
    snapshot = flow_snapshot or {}
    reasons: list[str] = []
    failures = snapshot.get("failures", [])
    if any(
        isinstance(failure, dict)
        and str(failure.get("source", "")) in NON_BLOCKING_FLOW_SOURCES
        for failure in failures
    ):
        reasons.append("部分非关键资金源不可用")

    core_etfs = snapshot.get("core_etfs")
    if isinstance(core_etfs, dict) and core_etfs.get("failures"):
        reasons.append("核心 ETF 采集不完整")

    margin = snapshot.get("margin")
    if (
        isinstance(margin, dict)
        and margin.get("availability") not in {None, "fresh"}
    ):
        reasons.append("两融数据非当日")
    return reasons


def _check_temperature_gate(
    artifact_path: Path,
    *,
    replay_path: Path,
    current_rules_fingerprint: str,
    max_ratio: float = 0.80,
    require_approval: bool = True,
) -> tuple[bool, str]:
    """Validate the approved 60-day replay artifact before report push."""
    if not artifact_path.exists():
        return False, "缺少 rollout artifact"
    if not replay_path.exists():
        return False, "缺少回放文件"

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"rollout artifact 无法读取: {exc}"
    if not isinstance(artifact, dict):
        return False, "rollout artifact 格式错误"

    if artifact.get("rules_version") != "2026-07-23":
        return False, "规则版本已变更，需重新回放"
    if artifact.get("rules_fingerprint") != current_rules_fingerprint:
        return False, "规则指纹不一致"

    configured_replay = Path(str(artifact.get("replay_path", "")))
    if not configured_replay.is_absolute():
        configured_replay = (ROOT / configured_replay).resolve()
    if configured_replay != replay_path.resolve():
        return False, "回放路径不一致"

    try:
        replay_bytes = replay_path.read_bytes()
    except OSError as exc:
        return False, f"回放文件无法读取: {exc}"
    replay_digest = "sha256:" + hashlib.sha256(replay_bytes).hexdigest()
    if artifact.get("replay_sha256") != replay_digest:
        return False, "回放文件已变化"

    if require_approval:
        if artifact.get("approved") is not True:
            return False, "回放尚未通过人工审查"
        if not artifact.get("approved_by") or not artifact.get("approved_at"):
            return False, "审批信息不完整"

    trading_days = artifact.get("trading_days")
    if isinstance(trading_days, bool) or not isinstance(trading_days, int):
        return False, "交易日数格式错误"
    if trading_days < 60:
        return False, "历史不足60日"

    distribution = artifact.get("distribution")
    if not isinstance(distribution, dict):
        return False, "状态分布格式错误"
    statuses = ("进攻", "观察", "防守")
    if set(distribution) != set(statuses):
        return False, "状态分布格式错误"
    if any(
        isinstance(distribution[status], bool)
        or not isinstance(distribution[status], int)
        for status in statuses
    ):
        return False, "状态分布格式错误"
    counts = {status: distribution[status] for status in statuses}
    if any(count < 0 for count in counts.values()):
        return False, "状态分布格式错误"
    if sum(counts.values()) != trading_days:
        return False, "分布与交易日数不一致"

    try:
        replay_records = json.loads(replay_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, f"回放文件无法读取: {exc}"
    if not isinstance(replay_records, list):
        return False, "回放文件格式错误"
    try:
        replay_dates = [
            date.fromisoformat(str(record["date"]))
            for record in replay_records
            if isinstance(record, dict)
        ]
    except (KeyError, TypeError, ValueError):
        return False, "回放日期格式错误"
    if len(replay_dates) != len(replay_records):
        return False, "回放日期格式错误"
    if any(
        current <= previous
        for previous, current in zip(replay_dates, replay_dates[1:])
    ):
        return False, "回放日期必须严格递增且不重复"
    non_trading_dates = [
        replay_date.isoformat()
        for replay_date in replay_dates
        if not is_cn_trading_day(replay_date)
    ]
    if non_trading_dates:
        return False, f"回放包含非交易日: {non_trading_dates[0]}"

    from lurker.application.temperature_replay import (
        replay_temperature_records,
        summarize_replay,
    )

    try:
        replay_rows = replay_temperature_records(replay_records)
        actual_summary = summarize_replay(replay_rows)
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"回放文件内容无效: {exc}"

    actual_days = int(actual_summary["trading_days"])
    actual_distribution = actual_summary["distribution"]
    if actual_days != trading_days or actual_distribution != counts:
        return False, "回放统计与 artifact 不一致"

    if replay_rows:
        actual_start = replay_rows[0]["date"]
        actual_end = replay_rows[-1]["date"]
        if (
            artifact.get("replay_start") != actual_start
            or artifact.get("replay_end") != actual_end
        ):
            return False, "回放日期范围与 artifact 不一致"

    leading_status = max(actual_distribution, key=actual_distribution.get)
    leading_ratio = actual_distribution[leading_status] / actual_days
    if leading_ratio > max_ratio:
        return False, f"状态{leading_status}占比{leading_ratio:.1%}超过80%"
    if math.isclose(leading_ratio, max_ratio, rel_tol=0.0, abs_tol=1e-12):
        return True, f"状态{leading_status}恰好80%，请人工复核"
    return True, ""


def _validate_rollout_provenance(replay_path: Path) -> None:
    try:
        records = json.loads(replay_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"回放文件无法读取: {exc}") from exc
    if not isinstance(records, list) or not records:
        raise ValueError("回放文件格式错误")
    for record in records:
        market_flow = (
            record.get("market_flow")
            if isinstance(record, dict)
            else None
        )
        if (
            not isinstance(market_flow, dict)
            or market_flow.get("source")
            != "eastmoney_market_flow_history"
        ):
            raise ValueError("大盘历史资金来源不可审计")


def approve_temperature_rollout(
    *,
    artifact_path: Path,
    replay_path: Path,
    approved_by: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate, atomically approve, and revalidate a rollout artifact."""
    approver = approved_by.strip()
    if not approver:
        raise ValueError("approved_by 不能为空")
    from lurker.application.temperature_replay import (
        current_rules_fingerprint,
    )

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
        require_approval=False,
    )
    if not allowed:
        raise ValueError(reason)
    _validate_rollout_provenance(replay_path)

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"rollout artifact 无法读取: {exc}") from exc
    if not isinstance(artifact, dict):
        raise ValueError("rollout artifact 格式错误")
    approval_time = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    artifact.update(
        {
            "approved": True,
            "approved_by": approver,
            "approved_at": approval_time.isoformat(),
            "notes": "通过完整回放、来源、哈希与状态集中度校验",
        }
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=artifact_path.parent,
            prefix=f".{artifact_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(artifact, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, artifact_path)
    finally:
        if temporary_name and Path(temporary_name).exists():
            Path(temporary_name).unlink()

    allowed, reason = _check_temperature_gate(
        artifact_path,
        replay_path=replay_path,
        current_rules_fingerprint=current_rules_fingerprint(),
    )
    if not allowed:
        raise ValueError(f"审批写入后复核失败: {reason}")
    return artifact


def build_temperature_replay(
    *,
    etf_start: str,
    margin_start: str,
    output_start: str,
    output_end: str,
    output_path: Path,
    artifact_path: Path,
    replay_collector=None,
) -> str:
    """Collect, replay, and persist an unapproved rollout artifact."""
    from lurker.application.temperature_replay import (
        build_rollout_artifact,
        replay_temperature_records,
        summarize_replay,
    )
    from lurker.config import load_core_etfs
    from lurker.ingest.temperature_history import collect_temperature_replay

    collector = replay_collector or collect_temperature_replay
    output_path = output_path.resolve()
    artifact_path = artifact_path.resolve()
    records = collector(
        etf_configs=load_core_etfs(ROOT / "configs" / "core_etfs.yaml"),
        etf_start=etf_start,
        margin_start=margin_start,
        output_start=output_start,
        output_end=output_end,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    replay_rows = replay_temperature_records(records)
    artifact = build_rollout_artifact(
        replay_path=output_path,
        replay_rows=replay_rows,
        replay_start=output_start,
        replay_end=output_end,
    )
    try:
        artifact["replay_path"] = str(output_path.relative_to(ROOT))
    except ValueError:
        pass
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = summarize_replay(replay_rows)
    return (
        f"Wrote temperature replay to {output_path} "
        f"(trading_days={summary['trading_days']}, "
        f"distribution={summary['distribution']}, "
        f"unknown_degradation_days={summary['unknown_degradation_days']})\n"
        f"Wrote unapproved rollout artifact to {artifact_path}"
    )


def _annotate_temperature_gate(content: str, reason: str, *, blocked: bool) -> str:
    label = "⚠️" if blocked else "ℹ️"
    note = f"- {label} 市场温度上线闸门：{reason}"
    marker = "## 数据质量\n"
    if marker in content:
        return content.replace(marker, f"{marker}\n{note}\n", 1)
    return content.rstrip() + f"\n\n## 数据质量\n\n{note}\n"


def parse_markets(value: str) -> list[str]:
    return [market.strip() for market in value.split(",") if market.strip()]


def read_api_key_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    key = path.read_text(encoding="utf-8").strip()
    return key or None


def load_suppressed_symbols(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(data, list):
        raw_symbols = data
    elif isinstance(data, dict):
        raw_symbols = data.get("symbols", [])
    else:
        raw_symbols = []

    return {
        str(symbol).strip().upper()
        for symbol in raw_symbols
        if str(symbol).strip()
    }


def build_demo_report(report_date: str) -> DailyReport:
    ranked = rank_candidates(
        [
            {
                "theme": "AI 算力基础设施",
                "stock_score": 86,
                "sector_score": 76,
                "ai_score": 80,
                "trigger_type": "stock_first",
                "ai_recommendation": "升级",
            },
            {
                "theme": "创新药出海",
                "stock_score": 62,
                "sector_score": 55,
                "ai_score": 50,
                "trigger_type": "stock_first",
                "ai_recommendation": "证据不足",
            },
        ]
    )
    main_candidate = ranked["main"][0]
    card = render_trend_card(
        theme=main_candidate["theme"],
        status="主候选",
        stage="扩散",
        total_score=main_candidate["total_score"],
        triggers=["A 股光模块多只个股 60 日强度进入前 10%"],
        attribution="云厂商资本开支带动高速互联需求。",
        evidence=["新闻", "公告"],
        risks=["估值偏高"],
        next_checks=["跟踪订单是否进入财报"],
    )
    content = render_daily_report(
        report_date=report_date,
        main_cards=[card],
        secondary_leads=["中际旭创 (300308.SZ, CN)：总分 75，【升级】推荐，保留观察"],
        low_score_watch_samples=["北方华创 (002371.SZ, CN)：总分 50，个股分 40，【观察】，低分观察"],
        watchlist_changes=[],
        risk_alerts=[],
    )
    return DailyReport(
        report_date=report_date,
        main_candidates_count=1,
        content_md=content,
    )


def save_symbols_to_db(seed_pool: dict, session) -> None:
    from lurker.storage.models import Symbol
    markets = seed_pool.get("markets", {})
    symbol_names = seed_pool.get("symbol_names", {})
    for market_code, market_pool in markets.items():
        symbols = market_pool.get("symbols", [])
        for sym in symbols:
            name = symbol_names.get(sym, sym)
            db_symbol = Symbol(
                symbol=sym,
                name=name,
                market=market_code,
                asset_type="stock",
                is_active=True
            )
            session.merge(db_symbol)


def build_data_snapshot(
    *,
    themes_path: Path,
    seed_pool_path: Path,
    price_snapshot_dir: Path | None = None,
    markets: list[str],
    windows: list[int],
    period: str,
    limit_per_market: int | None,
    markets_path: Path | None = None,
) -> str:
    if price_snapshot_dir is not None:
        store = FilePriceSnapshotStore(price_snapshot_dir)
        latest_snapshot = store.load_latest()
        if latest_snapshot is not None:
            snapshots = select_price_snapshot_rows(latest_snapshot, markets=markets)
            return render_price_snapshot(snapshots, windows=windows)

    if seed_pool_path.exists():
        seed_pool = load_resolved_seed_pool(seed_pool_path)
        seed_symbols = extract_seed_symbols(seed_pool)
    else:
        seed_symbols = load_resolved_theme_seed_symbols(themes_path)

    from lurker.config import load_markets
    markets_cfg = load_markets(markets_path) if markets_path else None

    snapshots = collect_price_snapshots(
        seed_symbols=seed_symbols,
        markets=markets,
        windows=windows,
        period=period,
        limit_per_market=limit_per_market,
        markets_config=markets_cfg,
    )
    return render_price_snapshot(snapshots, windows=windows)


def refresh_prices(
    *,
    seed_pool_path: Path,
    output_dir: Path,
    markets: list[str],
    windows: list[int],
    period: str,
    limit_per_market: int | None,
    snapshot_date: str | None = None,
    markets_path: Path | None = None,
    db_path: Path | None = None,
) -> str:
    seed_pool = load_resolved_seed_pool(seed_pool_path)
    from lurker.config import load_markets
    markets_cfg = load_markets(markets_path) if markets_path else None

    session = None
    if db_path:
        from lurker.storage.db import init_db, create_session
        engine = init_db(db_path)
        session = create_session(engine)
        save_symbols_to_db(seed_pool, session)

    try:
        batch = collect_price_snapshot_batch(
            seed_symbols=extract_seed_symbols(seed_pool),
            markets=markets,
            windows=windows,
            period=period,
            limit_per_market=limit_per_market,
            seed_pool_generated_at=seed_pool.get("generated_at"),
            markets_config=markets_cfg,
            db_session=session,
        )
    finally:
        if session:
            session.close()

    output_path = FilePriceSnapshotStore(output_dir).save(
        batch,
        snapshot_date=snapshot_date or date.today().isoformat(),
    )
    return (
        f"Wrote price snapshot to {output_path} "
        f"(snapshots={len(batch['snapshots'])}, failures={len(batch['failures'])})"
    )


def refresh_flows(
    *,
    output_dir: Path,
    snapshot_date: str | None = None,
    db_path: Path | None = None,
) -> str:
    if db_path:
        from lurker.storage.db import init_db
        init_db(db_path)
    batch = collect_flow_snapshot()
    output_path = FileFlowSnapshotStore(output_dir).save(
        batch,
        snapshot_date=snapshot_date or date.today().isoformat(),
    )
    return (
        f"Wrote flow snapshot to {output_path} "
        f"(failures={len(batch.get('failures', []))})"
    )


def build_attributor(api_key: str | None, model: str | None, base_url: str | None):
    from lurker.ai.attributor import GEMINI_BASE_URL, GEMINI_DEFAULT_MODEL, GeminiAttributor, StubAttributor

    if api_key:
        return GeminiAttributor(
            api_key=api_key,
            model=model or GEMINI_DEFAULT_MODEL,
            base_url=base_url or GEMINI_BASE_URL,
        )

    import os
    from lurker.ai.attributor import GEMINI_API_KEY_ENV

    env_key = os.environ.get(GEMINI_API_KEY_ENV, "")
    if env_key:
        return GeminiAttributor(
            model=model or GEMINI_DEFAULT_MODEL,
            base_url=base_url or GEMINI_BASE_URL,
        )
    return StubAttributor()


def build_candidate_history(
    *,
    report_date: str,
    snapshot_path: Path,
    report_path: Path,
    snapshot_batch: dict,
    symbol_names: dict[str, str] | None = None,
) -> dict:
    observed_symbols = [
        {
            "symbol": snapshot.get("symbol"),
            "name": (symbol_names or {}).get(str(snapshot.get("symbol", "")).upper()),
            "market": snapshot.get("market"),
            "latest_close": snapshot.get("latest_close"),
            "returns": {
                key: value
                for key, value in snapshot.items()
                if key.startswith("return_")
            },
        }
        for snapshot in snapshot_batch.get("snapshots", [])
    ]
    return {
        "schema_version": 1,
        "report_date": report_date,
        "snapshot_path": str(snapshot_path),
        "report_path": str(report_path),
        "markets": snapshot_batch.get("markets", []),
        "windows": snapshot_batch.get("windows", []),
        "observed_symbols": observed_symbols,
        "failures": snapshot_batch.get("failures", []),
    }


def append_report_archive_index(
    *,
    report_dir: Path,
    report_date: str,
    report_path: Path,
    candidates_path: Path,
    snapshot_path: Path,
    strategies: list[str],
    markets: list[str],
    windows: list[int],
    snapshot_count: int,
    failure_count: int,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    index_path = report_dir / "index.json"
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index_data = {"schema_version": 1, "reports": []}

    entry = {
        "date": report_date,
        "report_path": str(report_path),
        "candidates_path": str(candidates_path),
        "snapshot_path": str(snapshot_path),
        "strategies": strategies,
        "markets": markets,
        "windows": windows,
        "snapshot_count": snapshot_count,
        "failure_count": failure_count,
    }
    reports = [
        report
        for report in index_data.get("reports", [])
        if report.get("date") != report_date
    ]
    reports.append(entry)
    reports.sort(key=lambda report: report.get("date", ""))
    index_data["schema_version"] = 1
    index_data["reports"] = reports
    index_path.write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return index_path


def list_reports(*, report_dir: Path, limit: int = 10) -> str:
    index_path = report_dir / "index.json"
    if not index_path.exists():
        return f"没有找到日报索引：{index_path}"

    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    reports = sorted(
        index_data.get("reports", []),
        key=lambda report: report.get("date", ""),
        reverse=True,
    )[:limit]
    if not reports:
        return "日报索引为空。"

    lines = ["| Date | Strategies | Snapshots | Failures | Report |", "|---|---|---:|---:|---|"]
    for report in reports:
        strategies = ", ".join(report.get("strategies", [])) or "-"
        lines.append(
            f"| {report.get('date', '-')} | {strategies} | "
            f"{report.get('snapshot_count', 0)} | {report.get('failure_count', 0)} | "
            f"{report.get('report_path', '-')} |"
        )
    return "\n".join(lines)


def build_strategy_report(
    *,
    snapshot_batch: dict,
    theme_mapping: dict[str, list[str]],
    symbol_names: dict[str, str],
    attributor,
    report_date: str,
    signal_threshold: int,
    main_limit: int,
    low_score_watch_limit: int,
    suppressed_symbols: set[str],
    strategy_config_path: Path | None,
    strategy_names: list[str] | None,
    strategy_cadence: str | None,
    flow_snapshot: dict | None = None,
    scoring_config: dict | None = None,
    db_session: Any = None,
    temperature_rollout_approved: bool = True,
) -> DailyReport:
    configs = load_strategy_configs(strategy_config_path)
    if not configs and strategy_names:
        configs = build_default_strategy_configs(strategy_names)
    selected_configs = select_strategy_configs(
        configs,
        names=strategy_names,
        cadence=strategy_cadence,
    )
    context = StrategyContext(
        snapshot_batch=snapshot_batch,
        flow_snapshot=flow_snapshot,
        theme_mapping=theme_mapping,
        symbol_names=symbol_names,
        report_date=report_date,
        attributor=attributor,
        suppressed_symbols=suppressed_symbols,
        runtime_params={
            "signal_threshold": signal_threshold,
            "main_limit": main_limit,
            "low_score_watch_limit": low_score_watch_limit,
            "scoring_config": scoring_config,
            "temperature_rollout_approved": temperature_rollout_approved,
        },
        db_session=db_session,
    )
    results = run_strategies(context=context, configs=selected_configs)
    return render_strategy_results(report_date=report_date, results=results)



def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_notifier_from_env():
    import os

    notifiers = []
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN")
    if pushplus_token:
        from lurker.notification.pushplus_notifier import PushPlusNotifier

        notifiers.append(PushPlusNotifier(token=pushplus_token))

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_from = os.environ.get("SMTP_FROM")
    email_to = os.environ.get("EMAIL_TO")
    if smtp_host and smtp_from and email_to:
        from lurker.notification.email_notifier import EmailNotifier

        recipients = [recipient.strip() for recipient in email_to.split(",") if recipient.strip()]
        notifiers.append(
            EmailNotifier(
                host=smtp_host,
                port=int(os.environ.get("SMTP_PORT", "587")),
                username=os.environ.get("SMTP_USER"),
                password=os.environ.get("SMTP_PASSWORD"),
                sender=smtp_from,
                recipients=recipients,
                use_tls=_env_bool(os.environ.get("SMTP_USE_TLS"), default=True),
                use_ssl=_env_bool(os.environ.get("SMTP_USE_SSL"), default=False),
            )
        )

    if not notifiers:
        from lurker.notification.notifier import StubNotifier

        return StubNotifier()
    if len(notifiers) == 1:
        return notifiers[0]

    from lurker.notification.notifier import CompositeNotifier

    return CompositeNotifier(notifiers)


def build_watchlist_notifier_from_env():
    import os

    notifiers = []
    token = os.environ.get("WATCHLIST_PUSHPLUS_TOKEN")
    if token:
        from lurker.notification.pushplus_notifier import PushPlusNotifier

        notifiers.append(PushPlusNotifier(token=token))

    smtp_host = os.environ.get("WATCHLIST_SMTP_HOST")
    smtp_from = os.environ.get("WATCHLIST_SMTP_FROM")
    email_to = os.environ.get("WATCHLIST_EMAIL_TO")
    email_environment_names = (
        "WATCHLIST_SMTP_HOST",
        "WATCHLIST_SMTP_PORT",
        "WATCHLIST_SMTP_USER",
        "WATCHLIST_SMTP_PASSWORD",
        "WATCHLIST_SMTP_FROM",
        "WATCHLIST_EMAIL_TO",
        "WATCHLIST_SMTP_USE_TLS",
        "WATCHLIST_SMTP_USE_SSL",
    )
    email_configured = any(os.environ.get(name) for name in email_environment_names)
    if email_configured and not (smtp_host and smtp_from and email_to):
        raise ValueError("incomplete WATCHLIST email configuration")
    if email_configured:
        from lurker.notification.email_notifier import EmailNotifier

        recipients = [value.strip() for value in email_to.split(",") if value.strip()]
        if not recipients:
            raise ValueError("WATCHLIST_EMAIL_TO has no recipients")
        notifiers.append(
            EmailNotifier(
                host=smtp_host,
                port=int(os.environ.get("WATCHLIST_SMTP_PORT", "587")),
                username=os.environ.get("WATCHLIST_SMTP_USER"),
                password=os.environ.get("WATCHLIST_SMTP_PASSWORD"),
                sender=smtp_from,
                recipients=recipients,
                use_tls=_env_bool(
                    os.environ.get("WATCHLIST_SMTP_USE_TLS"),
                    default=True,
                ),
                use_ssl=_env_bool(
                    os.environ.get("WATCHLIST_SMTP_USE_SSL"),
                    default=False,
                ),
            )
        )

    if not notifiers:
        return None
    if len(notifiers) == 1:
        return notifiers[0]

    from lurker.notification.notifier import CompositeNotifier

    return CompositeNotifier(notifiers)


def _validated_report_date(
    report_date: str | None,
    *,
    today: date,
) -> date:
    requested = parse_iso_date(report_date) if report_date else today
    if requested > today:
        raise FutureReportDateError(
            f"future report date {requested.isoformat()} exceeds "
            f"Shanghai today {today.isoformat()}"
        )
    return requested


def _resolve_daily_job_date(
    report_date: str | None,
    *,
    today: date,
    markets: list[str],
    calendar: CnTradingCalendar | None,
) -> ReportDateResolution:
    requested = _validated_report_date(report_date, today=today)
    if not all_markets_are_cn(markets):
        return ReportDateResolution(requested, requested, False)
    resolved_calendar = calendar or build_default_cn_calendar()
    return resolve_daily_date(
        requested.isoformat(),
        today,
        resolved_calendar,
    )


def daily_job(
    *,
    seed_pool_path: Path,
    price_snapshot_dir: Path,
    report_dir: Path,
    flow_snapshot_dir: Path | None = None,
    markets: list[str],
    windows: list[int],
    period: str,
    limit_per_market: int | None,
    report_date: str | None = None,
    signal_threshold: int = 60,
    main_limit: int = 10,
    low_score_watch_limit: int = 5,
    suppressed_symbols_path: Path | None = None,
    strategy_config_path: Path | None = None,
    strategy_names: list[str] | None = None,
    strategy_cadence: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    scoring_config_path: Path | None = None,
    markets_path: Path | None = None,
    db_path: Path | None = None,
    push: bool = True,
    temperature_artifact_path: Path | None = None,
    temperature_replay_path: Path | None = None,
    today: date | None = None,
    calendar: CnTradingCalendar | None = None,
) -> str:
    resolved_today = today or shanghai_today()
    resolution = _resolve_daily_job_date(
        report_date,
        today=resolved_today,
        markets=markets,
        calendar=calendar,
    )
    if resolution.effective is None:
        return (
            "Skipped daily job: cn market closed on "
            f"{resolution.requested.isoformat()}."
        )
    job_date = resolution.effective.isoformat()

    seed_pool = load_resolved_seed_pool(seed_pool_path)

    from lurker.config import load_markets
    markets_cfg = load_markets(markets_path) if markets_path else None

    session = None
    if db_path:
        from lurker.storage.db import init_db, create_session
        engine = init_db(db_path)
        session = create_session(engine)
        # Populate symbols from seed pool
        save_symbols_to_db(seed_pool, session)

    try:
        snapshot_batch = collect_price_snapshot_batch(
            seed_symbols=extract_seed_symbols(seed_pool),
            markets=markets,
            windows=windows,
            period=period,
            limit_per_market=limit_per_market,
            seed_pool_generated_at=seed_pool.get("generated_at"),
            markets_config=markets_cfg,
            db_session=session,
        )
    except BaseException:
        if session is not None:
            session.close()
        raise

    snapshot_path = FilePriceSnapshotStore(price_snapshot_dir).save(
        snapshot_batch,
        snapshot_date=job_date,
    )
    flow_snapshot = None
    flow_snapshot_path = None
    if strategy_config_path is not None or strategy_names is not None:
        flow_snapshot = collect_flow_snapshot()
        resolved_flow_snapshot_dir = flow_snapshot_dir or ROOT / "data" / "processed" / "flow_snapshots"
        flow_snapshot_path = FileFlowSnapshotStore(resolved_flow_snapshot_dir).save(
            flow_snapshot,
            snapshot_date=job_date,
        )
    attributor = build_attributor(api_key, model, base_url)
    suppressed_symbols = load_suppressed_symbols(suppressed_symbols_path)

    from lurker.config import load_scoring
    scoring = {}
    if scoring_config_path and scoring_config_path.exists():
        scoring = load_scoring(scoring_config_path)

    if strategy_names:
        selected_strategies = strategy_names
    elif strategy_config_path is not None:
        selected_strategies = [
            config.name
            for config in select_strategy_configs(
                load_strategy_configs(strategy_config_path),
                names=None,
                cadence=strategy_cadence,
            )
        ]
    else:
        selected_strategies = ["long_term_trend"]

    temperature_gate_applies = "professional_flow_daily" in selected_strategies
    temperature_gate_allowed = True
    temperature_gate_reason = ""
    if temperature_gate_applies:
        from lurker.application.temperature_replay import current_rules_fingerprint

        resolved_artifact_path = (
            temperature_artifact_path
            or ROOT / "data" / "processed" / "temperature_rollout.json"
        )
        resolved_replay_path = (
            temperature_replay_path
            or ROOT / "tests" / "fixtures" / "etf_60d_replay.json"
        )
        temperature_gate_allowed, temperature_gate_reason = _check_temperature_gate(
            resolved_artifact_path,
            replay_path=resolved_replay_path,
            current_rules_fingerprint=current_rules_fingerprint(),
        )

    symbol_names = seed_pool.get("symbol_names", {})
    if strategy_config_path is None and strategy_names is None:
        report = run_daily(
            snapshot_batch=snapshot_batch,
            theme_mapping=seed_pool.get("theme_mapping", {}),
            symbol_names=symbol_names,
            attributor=attributor,
            report_date=job_date,
            signal_threshold=signal_threshold,
            main_limit=main_limit,
            low_score_watch_limit=low_score_watch_limit,
            suppressed_symbols=suppressed_symbols,
            scoring_config=scoring,
            db_session=session,
        )
    else:
        report = build_strategy_report(
            snapshot_batch=snapshot_batch,
            flow_snapshot=flow_snapshot,
            theme_mapping=seed_pool.get("theme_mapping", {}),
            symbol_names=symbol_names,
            attributor=attributor,
            report_date=job_date,
            signal_threshold=signal_threshold,
            main_limit=main_limit,
            low_score_watch_limit=low_score_watch_limit,
            suppressed_symbols=suppressed_symbols,
            strategy_config_path=strategy_config_path,
            strategy_names=strategy_names,
            strategy_cadence=strategy_cadence,
            scoring_config=scoring,
            db_session=session,
            temperature_rollout_approved=temperature_gate_allowed,
        )

    if temperature_gate_applies:
        if not temperature_gate_allowed or temperature_gate_reason:
            report.content_md = _annotate_temperature_gate(
                report.content_md,
                temperature_gate_reason,
                blocked=not temperature_gate_allowed,
            )

    # Save final report to Report table
    if session:
        try:
            from lurker.storage.models import Report
            import datetime
            t_date = datetime.datetime.strptime(job_date, "%Y-%m-%d").date()
            db_report = session.query(Report).filter_by(
                report_date=t_date,
                report_type="daily",
            ).first()
            if db_report:
                db_report.content = report.content_md
            else:
                db_report = Report(
                    report_date=t_date,
                    report_type="daily",
                    content=report.content_md,
                )
                session.add(db_report)
            session.commit()
        finally:
            session.close()

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{job_date}.md"

    report_path.write_text(report.content_md.rstrip() + "\n", encoding="utf-8")
    candidates_path = report_dir / f"{job_date}.candidates.json"
    candidates_path.write_text(
        json.dumps(
            build_candidate_history(
                report_date=job_date,
                snapshot_path=snapshot_path,
                report_path=report_path,
                snapshot_batch=snapshot_batch,
                symbol_names=symbol_names,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    index_path = append_report_archive_index(
        report_dir=report_dir,
        report_date=job_date,
        report_path=report_path,
        candidates_path=candidates_path,
        snapshot_path=snapshot_path,
        strategies=selected_strategies,
        markets=snapshot_batch.get("markets", markets),
        windows=snapshot_batch.get("windows", windows),
        snapshot_count=len(snapshot_batch["snapshots"]),
        failure_count=len(snapshot_batch["failures"]),
    )

    # 校验数据完整性与数据质量 (Verify data integrity and quality before pushing)
    price_count = len(snapshot_batch.get("snapshots", []))
    has_flow_data = bool(
        (flow_snapshot or {}).get("market_flow")
        or (flow_snapshot or {}).get("sector_flows")
        or (flow_snapshot or {}).get("stock_flows")
    )
    flow_failures = (flow_snapshot or {}).get("failures", [])

    # 检查是否有严重的资金流抓取报错（排除频率超限等非致命错误）
    has_critical_flow_failure = False
    critical_reasons = []
    for f in flow_failures:
        reason = f.get("reason", "")
        source = str(f.get("source", ""))
        if (
            source not in NON_BLOCKING_FLOW_SOURCES
            and "频率超限" not in reason
            and "limit" not in reason.lower()
        ):
            has_critical_flow_failure = True
            critical_reasons.append(f"{source}: {reason}")

    is_valid = True
    validation_error = ""
    if price_count == 0:
        is_valid = False
        validation_error = "价格数据快照为空，数据加载失败"
    elif flow_snapshot_path is not None and not has_flow_data:
        is_valid = False
        validation_error = "资金流快照为空，抓取失败"
    elif flow_snapshot_path is not None and has_critical_flow_failure:
        is_valid = False
        validation_error = f"资金流抓取存在致命错误 ({', '.join(critical_reasons)})"

    degradation_reasons = _flow_degradation_reasons(flow_snapshot)
    if temperature_gate_applies and temperature_gate_reason:
        degradation_reasons.append(temperature_gate_reason)
    delivery_status = "DEGRADED" if degradation_reasons else "SUCCESS"
    push_msg = ""

    if is_valid and push:
        notifier = build_notifier_from_env()
        try:
            push_title = report.push_title
            if delivery_status == "DEGRADED":
                push_title = f"[降级] {push_title}"
            notifier.send(title=push_title, markdown_content=report.content_md)
            if type(notifier).__name__ != "StubNotifier":
                push_msg = (
                    "\nPushed degraded report successfully."
                    if delivery_status == "DEGRADED"
                    else "\nPushed report successfully."
                )
        except Exception as exc:
            raise DailyJobFailed(
                "DAILY_JOB_STATUS=FAILED "
                'stage="notification" '
                f'reason="{type(exc).__name__}: {exc}"'
            ) from exc
    elif not is_valid:
        if push:
            notifier = build_notifier_from_env()
            try:
                _send_daily_failure_notification(
                    report_date=job_date,
                    stage="validation",
                    reason=validation_error,
                    notifier=notifier,
                )
            except Exception:
                pass
        raise DailyJobFailed(
            "DAILY_JOB_STATUS=FAILED "
            f'stage="validation" reason="{validation_error}"'
        )
    else:
        push_msg = "\nSkipped pushing report (--no-push)."
        if temperature_gate_applies:
            gate_state = "allowed" if temperature_gate_allowed else "blocked"
            detail = f": {temperature_gate_reason}" if temperature_gate_reason else ""
            push_msg += f"\nTemperature gate {gate_state}{detail}."

    return (
        f"Wrote price snapshot to {snapshot_path} "
        f"(snapshots={len(snapshot_batch['snapshots'])}, failures={len(snapshot_batch['failures'])})\n"
        + (
            f"\nWrote flow snapshot to {flow_snapshot_path} "
            f"(failures={len((flow_snapshot or {}).get('failures', []))})"
            if flow_snapshot_path is not None
            else ""
        )
        + "\n"
        f"Wrote daily report to {report_path}\n"
        f"Wrote candidate history to {candidates_path}\n"
        f"Updated report archive index at {index_path}"
        + push_msg
        + f"\nDAILY_JOB_STATUS={delivery_status}"
    )


def resolve_seed_pool(*, themes_path: Path, output_path: Path, markets_path: Path | None = None, db_path: Path | None = None) -> str:
    import inspect
    sig = inspect.signature(build_resolved_seed_pool)
    if "markets_path" in sig.parameters:
        pool = build_resolved_seed_pool(themes_path, markets_path=markets_path)
    else:
        pool = build_resolved_seed_pool(themes_path)
    save_resolved_seed_pool(pool, output_path)

    if db_path:
        from lurker.storage.db import init_db, create_session
        engine = init_db(db_path)
        with create_session(engine) as session:
            save_symbols_to_db(pool, session)

    markets = pool.get("markets", {})
    counts = ", ".join(
        f"{market}={len(market_pool.get('symbols', []))}"
        for market, market_pool in sorted(markets.items())
    )
    return f"Wrote resolved seed pool to {output_path} ({counts})"


def build_run_daily(
    *,
    price_snapshot_dir: Path,
    flow_snapshot_dir: Path | None = None,
    seed_pool: Path | None = None,
    report_date: str | None = None,
    signal_threshold: int = 60,
    main_limit: int = 10,
    low_score_watch_limit: int = 5,
    suppressed_symbols_path: Path | None = None,
    strategy_config_path: Path | None = None,
    strategy_names: list[str] | None = None,
    strategy_cadence: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    scoring_config_path: Path | None = None,
    db_path: Path | None = None,
    today: date | None = None,
    calendar: CnTradingCalendar | None = None,
) -> str:
    store = FilePriceSnapshotStore(price_snapshot_dir)
    resolved_today = today or shanghai_today()
    requested = _validated_report_date(report_date, today=resolved_today)
    snapshot_batch = store.load_on_or_before(requested.isoformat())
    if snapshot_batch is None:
        return (
            f"没有找到 {requested.isoformat()} 或更早的本地行情快照，"
            "请先运行 `lurker refresh-prices`。"
        )
    resolution = _resolve_daily_job_date(
        requested.isoformat(),
        today=resolved_today,
        markets=[str(item) for item in snapshot_batch.get("markets", [])],
        calendar=calendar,
    )
    if resolution.effective is None:
        return (
            "Skipped run-daily: cn market closed on "
            f"{resolution.requested.isoformat()}."
        )
    job_date = resolution.effective.isoformat()

    theme_mapping = {}
    symbol_names = {}
    if seed_pool and seed_pool.exists():
        import json
        pool_data = json.loads(seed_pool.read_text(encoding="utf-8"))
        theme_mapping = pool_data.get("theme_mapping", {})
        symbol_names = pool_data.get("symbol_names", {})

    attributor = build_attributor(api_key, model, base_url)
    suppressed_symbols = load_suppressed_symbols(suppressed_symbols_path)

    from lurker.config import load_scoring
    scoring = {}
    if scoring_config_path and scoring_config_path.exists():
        scoring = load_scoring(scoring_config_path)

    session = None
    if db_path:
        from lurker.storage.db import init_db, create_session
        engine = init_db(db_path)
        session = create_session(engine)
        # Populate symbols just in case
        if theme_mapping:
            save_symbols_to_db({"markets": {"cn": {"symbols": list(theme_mapping.keys())}}, "symbol_names": symbol_names}, session)

    try:
        if strategy_config_path is None and strategy_names is None:
            return run_daily(
                snapshot_batch=snapshot_batch,
                attributor=attributor,
                theme_mapping=theme_mapping,
                symbol_names=symbol_names,
                report_date=job_date,
                signal_threshold=signal_threshold,
                main_limit=main_limit,
                low_score_watch_limit=low_score_watch_limit,
                suppressed_symbols=suppressed_symbols,
                scoring_config=scoring,
                db_session=session,
            ).content_md
        flow_snapshot = None
        if flow_snapshot_dir is not None:
            flow_snapshot = FileFlowSnapshotStore(
                flow_snapshot_dir
            ).load_on_or_before(job_date)
        return build_strategy_report(
            snapshot_batch=snapshot_batch,
            flow_snapshot=flow_snapshot,
            theme_mapping=theme_mapping,
            symbol_names=symbol_names,
            attributor=attributor,
            report_date=job_date,
            signal_threshold=signal_threshold,
            main_limit=main_limit,
            low_score_watch_limit=low_score_watch_limit,
            suppressed_symbols=suppressed_symbols,
            strategy_config_path=strategy_config_path,
            strategy_names=strategy_names,
            strategy_cadence=strategy_cadence,
            scoring_config=scoring,
            db_session=session,
        ).content_md
    finally:
        if session:
            session.close()



def weekly_report(
    *,
    flow_snapshot_dir: Path,
    report_dir: Path,
    report_date: str | None = None,
    lookback_days: int = 5,
    sector_limit: int = 10,
    stock_limit: int = 20,
    push: bool = False,
    db_path: Path | None = None,
    today: date | None = None,
    calendar: CnTradingCalendar | None = None,
) -> str:
    from lurker.application.weekly_flow_report import build_weekly_flow_report
    resolved_today = today or shanghai_today()
    resolved_calendar = calendar or build_default_cn_calendar()
    resolution = resolve_weekly_date(
        report_date,
        resolved_today,
        resolved_calendar,
    )
    if resolution.effective is None:
        raise TradingCalendarUnavailable("weekly report has no effective date")
    requested_date = resolution.requested.isoformat()
    job_date = resolution.effective.isoformat()

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_snapshot_dir,
        report_date=job_date,
        requested_date=requested_date if resolution.adjusted else None,
        lookback_days=lookback_days,
        sector_limit=sector_limit,
        stock_limit=stock_limit,
        is_trading_day=resolved_calendar.is_trading_day,
    )

    # Save to report directory
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"weekly_{job_date}.md"
    report_path.write_text(report.content_md.rstrip() + "\n", encoding="utf-8")

    # Save to database
    if db_path:
        from lurker.storage.db import init_db, create_session
        from lurker.storage.models import Report
        import datetime
        t_date = datetime.datetime.strptime(job_date, "%Y-%m-%d").date()
        engine = init_db(db_path)
        with create_session(engine) as session:
            db_report = session.query(Report).filter_by(report_date=t_date, report_type="weekly").first()
            if db_report:
                db_report.content = report.content_md
            else:
                db_report = Report(
                    report_date=t_date,
                    report_type="weekly",
                    content=report.content_md,
                )
                session.add(db_report)
            session.commit()

    push_msg = ""
    if push:
        try:
            notifier = build_notifier_from_env()
            notifier.send(
                title=f"Lurker 周报 ({job_date})",
                markdown_content=report.content_md,
            )
            if type(notifier).__name__ != "StubNotifier":
                push_msg = "\nPushed weekly report successfully."
        except Exception as e:
            push_msg = f"\nFailed to push weekly report: {e}"

    return f"Wrote weekly flow report to {report_path}{push_msg}\n\n{report.content_md}"


def watchlist_checkup(
    *,
    watchlist_path: Path,
    report_dir: Path,
    state_file: Path,
    report_date: str | None = None,
    period: str = "2y",
    push: bool = True,
) -> str:
    from lurker.application.watchlist_alert_state import AlertStateStore
    from lurker.application.watchlist_anomaly import run_watchlist_anomaly
    from lurker.config import load_watchlist

    resolved_date = report_date or date.today().isoformat()
    result = run_watchlist_anomaly(
        config=load_watchlist(watchlist_path),
        report_date=resolved_date,
        report_dir=report_dir,
        state_store=AlertStateStore(state_file),
        notifier=build_watchlist_notifier_from_env(),
        push=push,
        period=period,
    )
    return (
        f"Wrote watchlist anomaly report to {result.report_path} "
        f"(checked={result.checked_count}, alerts={result.new_alert_count}, "
        f"failures={result.failure_count}, pushed={result.pushed})"
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _validate_report_month(
    value: str | None,
    *,
    today: date,
) -> str:
    resolved = value or today.strftime("%Y-%m")
    try:
        parsed = date.fromisoformat(f"{resolved}-01")
    except ValueError as exc:
        raise ValueError("report month must use YYYY-MM") from exc
    if parsed.strftime("%Y-%m") != resolved:
        raise ValueError("report month must use YYYY-MM")
    if parsed > today.replace(day=1):
        raise ValueError("future report month is not allowed")
    return resolved


def _last_cn_trading_day(
    report_month: str,
    calendar: CnTradingCalendar,
) -> date:
    year, month = map(int, report_month.split("-"))
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    sessions = calendar.sessions_in_range(start, end)
    if not sessions:
        raise TradingCalendarUnavailable(
            f"no confirmed CN trading session in {report_month}"
        )
    return sessions[-1]


def monthly_macro_flow_job(
    *,
    report_month: str | None,
    config_path: Path,
    snapshot_dir: Path,
    raw_dir: Path,
    report_dir: Path,
    strategy_config_path: Path,
    push: bool,
    month_end_only: bool = False,
    snapshot_collector=collect_monthly_macro_snapshot,
    today: date | None = None,
    calendar: CnTradingCalendar | None = None,
) -> str:
    resolved_today = today or shanghai_today()
    resolved = _validate_report_month(
        report_month,
        today=resolved_today,
    )
    if month_end_only:
        resolved_calendar = calendar or build_default_cn_calendar()
        last_session = _last_cn_trading_day(
            resolved,
            resolved_calendar,
        )
        if resolved_today != last_session:
            return (
                "Skipped monthly macro flow: "
                f"{resolved_today.isoformat()} is not the last CN "
                f"trading day of {resolved} "
                f"({last_session.isoformat()})."
            )

    monthly_config = load_monthly_macro_config(config_path)
    snapshot = snapshot_collector(
        report_month=resolved,
        config=monthly_config,
        raw_dir=raw_dir,
        today=resolved_today,
    )
    snapshot_path = MonthlyMacroSnapshotStore(snapshot_dir).save(
        snapshot
    )

    configured = load_strategy_configs(strategy_config_path)
    strategy = configured.get("monthly_macro_flow")
    if (
        strategy is None
        or not strategy.enabled
        or strategy.lifecycle != "active"
        or strategy.cadence != "monthly"
    ):
        raise ValueError(
            "monthly_macro_flow strategy is not enabled for monthly cadence"
        )
    context = StrategyContext(
        snapshot_batch={"snapshots": []},
        theme_mapping={},
        report_date=resolved,
        attributor=None,
        suppressed_symbols=set(),
        monthly_macro_snapshot=snapshot,
    )
    result = run_strategies(
        context=context,
        configs=[strategy],
    )[0]
    analysis = result.metadata["analysis"]
    report_path = report_dir / f"{resolved}.md"
    _atomic_write_text(
        report_path,
        result.report.content_md.rstrip() + "\n",
    )

    if not push:
        push_status = "skipped(--no-push)"
    elif analysis["market_state"] is None:
        push_status = "skipped(data_observation)"
    else:
        build_notifier_from_env().send(
            title=f"Lurker 宏观流动性月报 ({resolved})",
            markdown_content=result.report.content_md,
        )
        push_status = "sent"
    return (
        f"Wrote monthly macro snapshot to {snapshot_path}\n"
        f"Wrote monthly macro report to {report_path}\n"
        f"state={analysis['market_state'] or 'unknown'}; "
        f"push={push_status}"
    )





def _print_with_calendar_errors(parser: argparse.ArgumentParser, action: Any) -> None:
    try:
        print(action())
    except (FutureReportDateError, TradingCalendarUnavailable) as exc:
        parser.error(str(exc))
    except DailyJobFailed as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


def _load_project_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"\"", "'"}
        ):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    _load_project_env()
    parser = build_parser()
    args = parser.parse_args()
    if dispatch_command(parser, args):
        return
    print(build_demo_report(report_date="2026-05-17"))
