import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lurker")
    subparsers = parser.add_subparsers(dest="command")

    snapshot = subparsers.add_parser("data-snapshot")
    snapshot.add_argument("--markets", default="cn")
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
    snapshot.add_argument(
        "--markets-path",
        type=Path,
        default=ROOT / "configs" / "markets.yaml",
    )

    resolve_seeds = subparsers.add_parser("resolve-seeds")
    resolve_seeds.add_argument("--themes", type=Path, default=ROOT / "configs" / "themes.yaml")
    resolve_seeds.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "processed" / "resolved_seed_pool.json",
    )
    resolve_seeds.add_argument(
        "--markets-path",
        type=Path,
        default=ROOT / "configs" / "markets.yaml",
    )
    resolve_seeds.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "data" / "lurker.sqlite",
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
        "--flow-snapshots",
        type=Path,
        default=ROOT / "data" / "processed" / "flow_snapshots",
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
        "--scoring-config",
        type=Path,
        default=ROOT / "configs" / "scoring.yaml",
        help="打分配置 YAML（默认 configs/scoring.yaml）",
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
    run_daily_cmd.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "data" / "lurker.sqlite",
    )

    refresh = subparsers.add_parser("refresh-prices")
    refresh.add_argument("--markets", default="cn")
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
    refresh.add_argument(
        "--markets-path",
        type=Path,
        default=ROOT / "configs" / "markets.yaml",
    )
    refresh.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "data" / "lurker.sqlite",
    )

    refresh_flow = subparsers.add_parser("refresh-flows")
    refresh_flow.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "processed" / "flow_snapshots",
    )
    refresh_flow.add_argument("--date", default=None)
    refresh_flow.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "data" / "lurker.sqlite",
    )

    temperature_replay = subparsers.add_parser(
        "build-temperature-replay",
        help="采集历史资金事实并生成市场温度 60 日回放与未审批上线产物",
    )
    temperature_replay.add_argument("--etf-start", required=True)
    temperature_replay.add_argument("--margin-start", required=True)
    temperature_replay.add_argument("--output-start", required=True)
    temperature_replay.add_argument("--output-end", required=True)
    temperature_replay.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "etf_60d_replay.json",
    )
    temperature_replay.add_argument(
        "--artifact",
        type=Path,
        default=ROOT / "data" / "processed" / "temperature_rollout.json",
    )

    approve_rollout = subparsers.add_parser(
        "approve-temperature-rollout",
        help="完整校验并审批市场温度 rollout artifact",
    )
    approve_rollout.add_argument(
        "--replay",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "etf_60d_replay.json",
    )
    approve_rollout.add_argument(
        "--artifact",
        type=Path,
        default=ROOT / "data" / "processed" / "temperature_rollout.json",
    )
    approve_rollout.add_argument("--approved-by", required=True)

    daily = subparsers.add_parser(
        "daily-job",
        help="刷新本地行情快照，生成并落盘每日 Markdown 日报",
    )
    daily.add_argument("--markets", default="cn")
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
        "--flow-snapshots",
        type=Path,
        default=ROOT / "data" / "processed" / "flow_snapshots",
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
    daily.add_argument(
        "--scoring-config",
        type=Path,
        default=ROOT / "configs" / "scoring.yaml",
        help="打分配置 YAML（默认 configs/scoring.yaml）",
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
    daily.add_argument(
        "--markets-path",
        type=Path,
        default=ROOT / "configs" / "markets.yaml",
    )
    daily.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "data" / "lurker.sqlite",
    )
    daily.add_argument("--no-push", action="store_true")
    daily.add_argument(
        "--temperature-artifact",
        type=Path,
        default=ROOT / "data" / "processed" / "temperature_rollout.json",
    )
    daily.add_argument(
        "--temperature-replay",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "etf_60d_replay.json",
    )

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

    weekly_cmd = subparsers.add_parser(
        "weekly-report",
        help="生成周报（从本地资金快照聚合）",
    )
    weekly_cmd.add_argument(
        "--flow-snapshots",
        type=Path,
        default=ROOT / "data" / "processed" / "flow_snapshots",
    )
    weekly_cmd.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "data" / "reports",
    )
    weekly_cmd.add_argument("--date", default=None, help="报告日期，默认 today")
    weekly_cmd.add_argument("--lookback", type=int, default=5, help="回溯天数，默认 5")
    weekly_cmd.add_argument("--sector-limit", type=int, default=10, help="周报板块数量上限")
    weekly_cmd.add_argument("--stock-limit", type=int, default=20, help="周报个股数量上限")
    weekly_cmd.add_argument("--push", action="store_true", help="推送周报到已配置通知通道")
    weekly_cmd.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "data" / "lurker.sqlite",
    )

    monthly_macro = subparsers.add_parser(
        "monthly-macro-flow",
        help="生成独立宏观流动性月报",
    )
    monthly_macro.add_argument("--month", default=None)
    monthly_macro.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "macro_monthly.yaml",
    )
    monthly_macro.add_argument(
        "--snapshot-dir",
        type=Path,
        default=(ROOT / "data" / "processed" / "monthly_macro_flow_snapshots"),
    )
    monthly_macro.add_argument(
        "--raw-dir",
        type=Path,
        default=ROOT / "data" / "raw" / "pboc_credit_tables",
    )
    monthly_macro.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "data" / "reports" / "monthly_macro_flow",
    )
    monthly_macro.add_argument(
        "--flow-snapshot-dir",
        type=Path,
        default=ROOT / "data" / "processed" / "flow_snapshots",
    )
    monthly_macro.add_argument(
        "--strategy-config",
        type=Path,
        default=ROOT / "configs" / "strategies.yaml",
    )
    monthly_macro.add_argument(
        "--month-end-only",
        action="store_true",
        help="仅在本月最后一个中国交易日运行（供定时任务使用）",
    )
    monthly_macro.add_argument("--no-push", action="store_true")

    watchlist_cmd = subparsers.add_parser(
        "watchlist-checkup",
        help="独立运行自选股异常体检并使用 WATCHLIST_* 接收人",
    )
    watchlist_cmd.add_argument(
        "--watchlist",
        type=Path,
        default=ROOT / "configs" / "watchlist.yaml",
    )
    watchlist_cmd.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "data" / "reports" / "watchlist",
    )
    watchlist_cmd.add_argument(
        "--state-file",
        type=Path,
        default=ROOT / "data" / "processed" / "watchlist_alert_state.json",
    )
    watchlist_cmd.add_argument("--date", default=None)
    watchlist_cmd.add_argument("--period", default="2y")
    watchlist_cmd.add_argument("--no-push", action="store_true")

    personal_cmd = subparsers.add_parser(
        "personal-close-report",
        help="生成独立的个人持仓与观察池盘后简报",
    )
    personal_cmd.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "personal_watch.yaml",
    )
    personal_cmd.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "data" / "reports" / "personal_close",
    )
    personal_cmd.add_argument(
        "--state-file",
        type=Path,
        default=ROOT / "data" / "processed" / "personal_close_push_state.json",
    )
    personal_cmd.add_argument("--date", default=None)
    personal_cmd.add_argument("--period", choices=("2y",), default="2y")
    push_group = personal_cmd.add_mutually_exclusive_group()
    push_group.add_argument("--no-push", action="store_true")
    push_group.add_argument("--force-push", action="store_true")

    return parser
