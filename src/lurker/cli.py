import argparse
import json
from datetime import date
from pathlib import Path

import yaml

from lurker.application.price_snapshot import (
    FilePriceSnapshotStore,
    collect_price_snapshot_batch,
    collect_price_snapshots,
    render_price_snapshot,
    select_price_snapshot_rows,
)
from lurker.application.run_daily import run_daily
from lurker.application.strategy_runner import (
    StrategyContext,
    build_default_strategy_configs,
    load_strategy_configs,
    parse_strategy_names,
    render_strategy_results,
    run_strategies,
    select_strategy_configs,
)
from lurker.ingest.constituents import load_resolved_theme_seed_symbols
from lurker.pipeline import rank_candidates
from lurker.reports.daily_report import render_daily_report
from lurker.reports.models import DailyReport
from lurker.reports.trend_card import render_trend_card
from lurker.universe.resolved_seed_pool import (
    build_resolved_seed_pool,
    extract_seed_symbols,
    load_resolved_seed_pool,
    save_resolved_seed_pool,
)


ROOT = Path(__file__).resolve().parents[2]


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


def build_data_snapshot(
    *,
    themes_path: Path,
    seed_pool_path: Path,
    price_snapshot_dir: Path | None = None,
    markets: list[str],
    windows: list[int],
    period: str,
    limit_per_market: int | None,
) -> str:
    if price_snapshot_dir is not None:
        store = FilePriceSnapshotStore(price_snapshot_dir)
        latest_snapshot = store.load_latest()
        if latest_snapshot is not None:
            snapshots = select_price_snapshot_rows(latest_snapshot, markets=markets)
            return render_price_snapshot(snapshots, windows=windows)

    if seed_pool_path.exists():
        seed_symbols = extract_seed_symbols(load_resolved_seed_pool(seed_pool_path))
    else:
        seed_symbols = load_resolved_theme_seed_symbols(themes_path)
    snapshots = collect_price_snapshots(
        seed_symbols=seed_symbols,
        markets=markets,
        windows=windows,
        period=period,
        limit_per_market=limit_per_market,
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
) -> str:
    seed_pool = load_resolved_seed_pool(seed_pool_path)
    batch = collect_price_snapshot_batch(
        seed_symbols=extract_seed_symbols(seed_pool),
        markets=markets,
        windows=windows,
        period=period,
        limit_per_market=limit_per_market,
        seed_pool_generated_at=seed_pool.get("generated_at"),
    )
    output_path = FilePriceSnapshotStore(output_dir).save(
        batch,
        snapshot_date=snapshot_date or date.today().isoformat(),
    )
    return (
        f"Wrote price snapshot to {output_path} "
        f"(snapshots={len(batch['snapshots'])}, failures={len(batch['failures'])})"
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
) -> str:
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
        theme_mapping=theme_mapping,
        symbol_names=symbol_names,
        report_date=report_date,
        attributor=attributor,
        suppressed_symbols=suppressed_symbols,
        runtime_params={
            "signal_threshold": signal_threshold,
            "main_limit": main_limit,
            "low_score_watch_limit": low_score_watch_limit,
        },
    )
    results = run_strategies(context=context, configs=selected_configs)
    return render_strategy_results(report_date=report_date, results=results)


def daily_job(
    *,
    seed_pool_path: Path,
    price_snapshot_dir: Path,
    report_dir: Path,
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
) -> str:
    job_date = report_date or date.today().isoformat()
    seed_pool = load_resolved_seed_pool(seed_pool_path)
    snapshot_batch = collect_price_snapshot_batch(
        seed_symbols=extract_seed_symbols(seed_pool),
        markets=markets,
        windows=windows,
        period=period,
        limit_per_market=limit_per_market,
        seed_pool_generated_at=seed_pool.get("generated_at"),
    )
    snapshot_path = FilePriceSnapshotStore(price_snapshot_dir).save(
        snapshot_batch,
        snapshot_date=job_date,
    )
    attributor = build_attributor(api_key, model, base_url)
    suppressed_symbols = load_suppressed_symbols(suppressed_symbols_path)
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
        )
    else:
        report = build_strategy_report(
            snapshot_batch=snapshot_batch,
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
        )
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
    selected_strategies = strategy_names or ["long_term_trend"]
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

    push_msg = ""
    import os
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN")
    
    # Simple factory logic for Notifier
    notifier = None
    if pushplus_token:
        from lurker.notification.pushplus_notifier import PushPlusNotifier
        notifier = PushPlusNotifier(token=pushplus_token)
    else:
        from lurker.notification.notifier import StubNotifier
        notifier = StubNotifier()

    try:
        notifier.send(title=report.push_title, markdown_content=report.content_md)
        if pushplus_token:
            push_msg = "\nPushed report to PushPlus successfully."
    except Exception as e:
        push_msg = f"\nFailed to push report: {e}"

    return (
        f"Wrote price snapshot to {snapshot_path} "
        f"(snapshots={len(snapshot_batch['snapshots'])}, failures={len(snapshot_batch['failures'])})\n"
        f"Wrote daily report to {report_path}\n"
        f"Wrote candidate history to {candidates_path}\n"
        f"Updated report archive index at {index_path}"
        + push_msg
    )


def resolve_seed_pool(*, themes_path: Path, output_path: Path) -> str:
    pool = build_resolved_seed_pool(themes_path)
    save_resolved_seed_pool(pool, output_path)
    markets = pool.get("markets", {})
    counts = ", ".join(
        f"{market}={len(market_pool.get('symbols', []))}"
        for market, market_pool in sorted(markets.items())
    )
    return f"Wrote resolved seed pool to {output_path} ({counts})"


def build_run_daily(
    *,
    price_snapshot_dir: Path,
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
) -> str:
    store = FilePriceSnapshotStore(price_snapshot_dir)
    snapshot_batch = store.load_latest()
    if snapshot_batch is None:
        return "没有找到本地行情快照，请先运行 `lurker refresh-prices`。"

    theme_mapping = {}
    symbol_names = {}
    if seed_pool and seed_pool.exists():
        import json
        pool_data = json.loads(seed_pool.read_text(encoding="utf-8"))
        theme_mapping = pool_data.get("theme_mapping", {})
        symbol_names = pool_data.get("symbol_names", {})

    attributor = build_attributor(api_key, model, base_url)
    suppressed_symbols = load_suppressed_symbols(suppressed_symbols_path)
    if strategy_config_path is None and strategy_names is None:
        return run_daily(
            snapshot_batch=snapshot_batch,
            attributor=attributor,
            theme_mapping=theme_mapping,
            symbol_names=symbol_names,
            report_date=report_date,
            signal_threshold=signal_threshold,
            main_limit=main_limit,
            low_score_watch_limit=low_score_watch_limit,
            suppressed_symbols=suppressed_symbols,
        ).content_md
    return build_strategy_report(
        snapshot_batch=snapshot_batch,
        theme_mapping=theme_mapping,
        symbol_names=symbol_names,
        attributor=attributor,
        report_date=report_date or date.today().isoformat(),
        signal_threshold=signal_threshold,
        main_limit=main_limit,
        low_score_watch_limit=low_score_watch_limit,
        suppressed_symbols=suppressed_symbols,
        strategy_config_path=strategy_config_path,
        strategy_names=strategy_names,
        strategy_cadence=strategy_cadence,
    ).content_md


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lurker")
    subparsers = parser.add_subparsers(dest="command")

    snapshot = subparsers.add_parser("data-snapshot")
    snapshot.add_argument("--markets", default="cn,us,hk")
    snapshot.add_argument("--period", default="1y")
    snapshot.add_argument("--windows", default="20,60,120,180")
    snapshot.add_argument("--limit", type=int, default=5)
    snapshot.add_argument("--themes", type=Path, default=ROOT / "configs" / "themes.yaml")
    snapshot.add_argument(
        "--seed-pool",
        type=Path,
        default=ROOT / "data" / "processed" / "resolved_seed_pool.json",
    )
    snapshot.add_argument(
        "--price-snapshots",
        type=Path,
        default=ROOT / "data" / "processed" / "price_snapshots",
    )

    resolve_seeds = subparsers.add_parser("resolve-seeds")
    resolve_seeds.add_argument("--themes", type=Path, default=ROOT / "configs" / "themes.yaml")
    resolve_seeds.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "processed" / "resolved_seed_pool.json",
    )

    run_daily_cmd = subparsers.add_parser(
        "run-daily",
        help="从本地行情快照生成完整每日日报（信号→归因→排序→Markdown）",
    )
    run_daily_cmd.add_argument(
        "--price-snapshots",
        type=Path,
        default=ROOT / "data" / "processed" / "price_snapshots",
    )
    run_daily_cmd.add_argument(
        "--seed-pool",
        type=Path,
        default=ROOT / "data" / "processed" / "resolved_seed_pool.json",
    )
    run_daily_cmd.add_argument("--date", default=None, help="报告日期，默认 today")
    run_daily_cmd.add_argument(
        "--signal-threshold",
        type=int,
        default=60,
        help="个股信号分过滤阈值（默认 60）",
    )
    run_daily_cmd.add_argument(
        "--main-limit",
        type=int,
        default=10,
        help="主候选最大条数（默认 10）",
    )
    run_daily_cmd.add_argument(
        "--low-score-watch-limit",
        type=int,
        default=5,
        help="低分观察样本最大条数（默认 5）",
    )
    run_daily_cmd.add_argument(
        "--suppressed-symbols",
        type=Path,
        default=ROOT / "configs" / "suppressed_symbols.yaml",
        help="本地屏蔽标的 YAML（默认 configs/suppressed_symbols.yaml）",
    )
    run_daily_cmd.add_argument(
        "--strategy-config",
        type=Path,
        default=ROOT / "configs" / "strategies.yaml",
        help="策略配置 YAML（默认 configs/strategies.yaml）",
    )
    run_daily_cmd.add_argument(
        "--strategies",
        default=None,
        help="只运行指定策略，逗号分隔；默认运行配置中启用且符合 cadence 的策略",
    )
    run_daily_cmd.add_argument(
        "--cadence",
        default="daily",
        help="运行指定频率的策略；传 all 可忽略频率过滤",
    )
    run_daily_cmd.add_argument(
        "--api-key",
        default=None,
        help="LLM API Key（也可通过 GEMINI_API_KEY 环境变量设置）",
    )
    run_daily_cmd.add_argument(
        "--api-key-file",
        type=Path,
        default=ROOT / "key",
        help="本地 LLM API Key 文件（默认读取项目根目录 key；命令行或环境变量优先）",
    )
    run_daily_cmd.add_argument(
        "--model",
        default=None,
        help="LLM 模型名称（默认 gemini-2.5-flash）",
    )
    run_daily_cmd.add_argument(
        "--base-url",
        default=None,
        help="LLM API base_url（默认 Gemini OpenAI-compatible 端点）",
    )

    refresh = subparsers.add_parser("refresh-prices")
    refresh.add_argument("--markets", default="cn,us,hk")
    refresh.add_argument("--period", default="1y")
    refresh.add_argument("--windows", default="20,60,120,180")
    refresh.add_argument("--limit", type=int, default=5)
    refresh.add_argument(
        "--seed-pool",
        type=Path,
        default=ROOT / "data" / "processed" / "resolved_seed_pool.json",
    )
    refresh.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "processed" / "price_snapshots",
    )
    refresh.add_argument("--date", default=None)

    daily = subparsers.add_parser(
        "daily-job",
        help="刷新本地行情快照，生成并落盘每日 Markdown 日报",
    )
    daily.add_argument("--markets", default="cn,us,hk")
    daily.add_argument("--period", default="1y")
    daily.add_argument("--windows", default="20,60,120,180")
    daily.add_argument("--limit", type=int, default=5)
    daily.add_argument(
        "--seed-pool",
        type=Path,
        default=ROOT / "data" / "processed" / "resolved_seed_pool.json",
    )
    daily.add_argument(
        "--price-snapshots",
        type=Path,
        default=ROOT / "data" / "processed" / "price_snapshots",
    )
    daily.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "data" / "reports",
    )
    daily.add_argument("--date", default=None, help="报告日期，默认 today")
    daily.add_argument("--signal-threshold", type=int, default=60)
    daily.add_argument("--main-limit", type=int, default=10)
    daily.add_argument("--low-score-watch-limit", type=int, default=5)
    daily.add_argument(
        "--suppressed-symbols",
        type=Path,
        default=ROOT / "configs" / "suppressed_symbols.yaml",
    )
    daily.add_argument(
        "--strategy-config",
        type=Path,
        default=ROOT / "configs" / "strategies.yaml",
    )
    daily.add_argument("--strategies", default=None)
    daily.add_argument("--cadence", default="daily")
    daily.add_argument("--api-key", default=None)
    daily.add_argument(
        "--api-key-file",
        type=Path,
        default=ROOT / "key",
    )
    daily.add_argument("--model", default=None)
    daily.add_argument("--base-url", default=None)

    list_reports_cmd = subparsers.add_parser(
        "list-reports",
        help="列出已归档的每日日报",
    )
    list_reports_cmd.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "data" / "reports",
    )
    list_reports_cmd.add_argument("--limit", type=int, default=10)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "data-snapshot":
        print(
            build_data_snapshot(
                themes_path=args.themes,
                seed_pool_path=args.seed_pool,
                price_snapshot_dir=args.price_snapshots,
                markets=parse_markets(args.markets),
                windows=[int(window) for window in parse_markets(args.windows)],
                period=args.period,
                limit_per_market=args.limit,
            )
        )
        return

    if args.command == "resolve-seeds":
        print(resolve_seed_pool(themes_path=args.themes, output_path=args.output))
        return

    if args.command == "run-daily":
        api_key = args.api_key or read_api_key_file(args.api_key_file)
        print(
            build_run_daily(
                price_snapshot_dir=args.price_snapshots,
                seed_pool=args.seed_pool,
                report_date=args.date,
                signal_threshold=args.signal_threshold,
                main_limit=args.main_limit,
                low_score_watch_limit=args.low_score_watch_limit,
                suppressed_symbols_path=args.suppressed_symbols,
                strategy_config_path=args.strategy_config,
                strategy_names=parse_strategy_names(args.strategies),
                strategy_cadence=None if args.cadence == "all" else args.cadence,
                api_key=api_key,
                model=args.model,
                base_url=args.base_url,
            )
        )
        return

    if args.command == "refresh-prices":
        print(
            refresh_prices(
                seed_pool_path=args.seed_pool,
                output_dir=args.output_dir,
                markets=parse_markets(args.markets),
                windows=[int(window) for window in parse_markets(args.windows)],
                period=args.period,
                limit_per_market=args.limit,
                snapshot_date=args.date,
            )
        )
        return

    if args.command == "daily-job":
        api_key = args.api_key or read_api_key_file(args.api_key_file)
        print(
            daily_job(
                seed_pool_path=args.seed_pool,
                price_snapshot_dir=args.price_snapshots,
                report_dir=args.report_dir,
                markets=parse_markets(args.markets),
                windows=[int(window) for window in parse_markets(args.windows)],
                period=args.period,
                limit_per_market=args.limit,
                report_date=args.date,
                signal_threshold=args.signal_threshold,
                main_limit=args.main_limit,
                low_score_watch_limit=args.low_score_watch_limit,
                suppressed_symbols_path=args.suppressed_symbols,
                strategy_config_path=args.strategy_config,
                strategy_names=parse_strategy_names(args.strategies),
                strategy_cadence=None if args.cadence == "all" else args.cadence,
                api_key=api_key,
                model=args.model,
                base_url=args.base_url,
            )
        )
        return

    if args.command == "list-reports":
        print(list_reports(report_dir=args.report_dir, limit=args.limit))
        return

    print(build_demo_report(report_date="2026-05-17"))
