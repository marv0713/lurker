import argparse
from datetime import date
from pathlib import Path

from lurker.application.price_snapshot import (
    FilePriceSnapshotStore,
    collect_price_snapshot_batch,
    collect_price_snapshots,
    render_price_snapshot,
    select_price_snapshot_rows,
)
from lurker.application.run_daily import run_daily
from lurker.ingest.constituents import load_resolved_theme_seed_symbols
from lurker.pipeline import rank_candidates
from lurker.reports.daily_report import render_daily_report
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


def build_demo_report(report_date: str) -> str:
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
    return render_daily_report(
        report_date=report_date,
        main_cards=[card],
        secondary_leads=[
            f"{candidate['theme']}：{candidate['ai_recommendation']}，保留观察"
            for candidate in ranked["secondary"]
        ],
        watchlist_changes=["数据中心电力进入观察池"],
        risk_alerts=["部分光模块标的短期交易拥挤"],
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
    report = run_daily(
        snapshot_batch=snapshot_batch,
        theme_mapping=seed_pool.get("theme_mapping", {}),
        attributor=build_attributor(api_key, model, base_url),
        report_date=job_date,
        signal_threshold=signal_threshold,
        main_limit=main_limit,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{job_date}.md"
    report_path.write_text(report.rstrip() + "\n", encoding="utf-8")

    return (
        f"Wrote price snapshot to {snapshot_path} "
        f"(snapshots={len(snapshot_batch['snapshots'])}, failures={len(snapshot_batch['failures'])})\n"
        f"Wrote daily report to {report_path}"
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
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    store = FilePriceSnapshotStore(price_snapshot_dir)
    snapshot_batch = store.load_latest()
    if snapshot_batch is None:
        return "没有找到本地行情快照，请先运行 `lurker refresh-prices`。"

    theme_mapping = {}
    if seed_pool and seed_pool.exists():
        import json
        pool_data = json.loads(seed_pool.read_text(encoding="utf-8"))
        theme_mapping = pool_data.get("theme_mapping", {})

    return run_daily(
        snapshot_batch=snapshot_batch,
        attributor=build_attributor(api_key, model, base_url),
        theme_mapping=theme_mapping,
        report_date=report_date,
        signal_threshold=signal_threshold,
        main_limit=main_limit,
    )


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
    daily.add_argument("--api-key", default=None)
    daily.add_argument(
        "--api-key-file",
        type=Path,
        default=ROOT / "key",
    )
    daily.add_argument("--model", default=None)
    daily.add_argument("--base-url", default=None)

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
                api_key=api_key,
                model=args.model,
                base_url=args.base_url,
            )
        )
        return

    print(build_demo_report(report_date="2026-05-17"))
