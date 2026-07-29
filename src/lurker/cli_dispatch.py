from __future__ import annotations

import argparse
from collections.abc import Callable

from lurker.application.strategy_runner import parse_strategy_names


CommandHandler = Callable[
    [argparse.ArgumentParser, argparse.Namespace],
    None,
]


def _monthly_macro_flow(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    cli._print_with_calendar_errors(
        parser,
        lambda: cli.monthly_macro_flow_job(
            report_month=args.month,
            config_path=args.config,
            snapshot_dir=args.snapshot_dir,
            raw_dir=args.raw_dir,
            report_dir=args.report_dir,
            strategy_config_path=args.strategy_config,
            push=not args.no_push,
            month_end_only=args.month_end_only,
        ),
    )


def _watchlist_checkup(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    print(
        cli.watchlist_checkup(
            watchlist_path=args.watchlist,
            report_dir=args.report_dir,
            state_file=args.state_file,
            report_date=args.date,
            period=args.period,
            push=not args.no_push,
        )
    )


def _data_snapshot(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    print(
        cli.build_data_snapshot(
            themes_path=args.themes,
            seed_pool_path=args.seed_pool,
            price_snapshot_dir=args.price_snapshots,
            markets=cli.parse_markets(args.markets),
            windows=[
                int(window)
                for window in cli.parse_markets(args.windows)
            ],
            period=args.period,
            limit_per_market=args.limit,
            markets_path=args.markets_path,
        )
    )


def _resolve_seeds(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    print(
        cli.resolve_seed_pool(
            themes_path=args.themes,
            output_path=args.output,
            markets_path=args.markets_path,
            db_path=args.db_path,
        )
    )


def _run_daily(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    api_key = args.api_key or cli.read_api_key_file(args.api_key_file)
    cli._print_with_calendar_errors(
        parser,
        lambda: cli.build_run_daily(
            price_snapshot_dir=args.price_snapshots,
            flow_snapshot_dir=args.flow_snapshots,
            seed_pool=args.seed_pool,
            report_date=args.date,
            signal_threshold=args.signal_threshold,
            main_limit=args.main_limit,
            low_score_watch_limit=args.low_score_watch_limit,
            suppressed_symbols_path=args.suppressed_symbols,
            strategy_config_path=args.strategy_config,
            strategy_names=parse_strategy_names(args.strategies),
            strategy_cadence=(
                None if args.cadence == "all" else args.cadence
            ),
            api_key=api_key,
            model=args.model,
            base_url=args.base_url,
            scoring_config_path=args.scoring_config,
            db_path=args.db_path,
        ),
    )


def _refresh_prices(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    print(
        cli.refresh_prices(
            seed_pool_path=args.seed_pool,
            output_dir=args.output_dir,
            markets=cli.parse_markets(args.markets),
            windows=[
                int(window)
                for window in cli.parse_markets(args.windows)
            ],
            period=args.period,
            limit_per_market=args.limit,
            snapshot_date=args.date,
            markets_path=args.markets_path,
            db_path=args.db_path,
        )
    )


def _refresh_flows(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    print(
        cli.refresh_flows(
            output_dir=args.output_dir,
            snapshot_date=args.date,
            db_path=args.db_path,
        )
    )


def _build_temperature_replay(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    print(
        cli.build_temperature_replay(
            etf_start=args.etf_start,
            margin_start=args.margin_start,
            output_start=args.output_start,
            output_end=args.output_end,
            output_path=args.output,
            artifact_path=args.artifact,
        )
    )


def _approve_temperature_rollout(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    artifact = cli.approve_temperature_rollout(
        artifact_path=args.artifact,
        replay_path=args.replay,
        approved_by=args.approved_by,
    )
    print(
        "Approved temperature rollout "
        f"(trading_days={artifact['trading_days']}, "
        f"distribution={artifact['distribution']}, "
        f"approved_by={artifact['approved_by']})"
    )


def _daily_job(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    api_key = args.api_key or cli.read_api_key_file(args.api_key_file)
    cli._print_with_calendar_errors(
        parser,
        lambda: cli.run_daily_job_with_failure_notification(
            action=lambda: cli.daily_job(
                seed_pool_path=args.seed_pool,
                price_snapshot_dir=args.price_snapshots,
                flow_snapshot_dir=args.flow_snapshots,
                report_dir=args.report_dir,
                markets=cli.parse_markets(args.markets),
                windows=[
                    int(window)
                    for window in cli.parse_markets(args.windows)
                ],
                period=args.period,
                limit_per_market=args.limit,
                report_date=args.date,
                signal_threshold=args.signal_threshold,
                main_limit=args.main_limit,
                low_score_watch_limit=args.low_score_watch_limit,
                suppressed_symbols_path=args.suppressed_symbols,
                strategy_config_path=args.strategy_config,
                strategy_names=parse_strategy_names(
                    args.strategies
                ),
                strategy_cadence=(
                    None if args.cadence == "all" else args.cadence
                ),
                api_key=api_key,
                model=args.model,
                base_url=args.base_url,
                scoring_config_path=args.scoring_config,
                markets_path=args.markets_path,
                db_path=args.db_path,
                push=not args.no_push,
                temperature_artifact_path=args.temperature_artifact,
                temperature_replay_path=args.temperature_replay,
            ),
            report_date=(
                args.date or cli.shanghai_today().isoformat()
            ),
            push=not args.no_push,
            notifier=cli.build_notifier_from_env(),
        ),
    )


def _weekly_report(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    cli._print_with_calendar_errors(
        parser,
        lambda: cli.weekly_report(
            flow_snapshot_dir=args.flow_snapshots,
            report_dir=args.report_dir,
            report_date=args.date,
            lookback_days=args.lookback,
            sector_limit=args.sector_limit,
            stock_limit=args.stock_limit,
            push=args.push,
            db_path=args.db_path,
        ),
    )


def _list_reports(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    from lurker import cli

    print(
        cli.list_reports(
            report_dir=args.report_dir,
            limit=args.limit,
        )
    )


COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "monthly-macro-flow": _monthly_macro_flow,
    "watchlist-checkup": _watchlist_checkup,
    "data-snapshot": _data_snapshot,
    "resolve-seeds": _resolve_seeds,
    "run-daily": _run_daily,
    "refresh-prices": _refresh_prices,
    "refresh-flows": _refresh_flows,
    "build-temperature-replay": _build_temperature_replay,
    "approve-temperature-rollout": _approve_temperature_rollout,
    "daily-job": _daily_job,
    "weekly-report": _weekly_report,
    "list-reports": _list_reports,
}


def dispatch_command(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> bool:
    handler = COMMAND_HANDLERS.get(args.command)
    if handler is None:
        return False
    handler(parser, args)
    return True
