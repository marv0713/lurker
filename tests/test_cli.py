import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from lurker.reports.models import DailyReport
from lurker.application.weekly_flow_report import WeeklyFlowSummary
from lurker.cli import (
    DailyJobFailed,
    _flow_degradation_reasons,
    build_temperature_replay,
    build_data_snapshot,
    build_demo_report,
    build_notifier_from_env,
    build_personal_notifier_from_env,
    build_watchlist_notifier_from_env,
    build_run_daily,
    append_report_archive_index,
    build_strategy_report,
    daily_job,
    build_parser,
    list_reports,
    load_suppressed_symbols,
    monthly_macro_flow_job,
    read_api_key_file,
    parse_markets,
    refresh_flows,
    refresh_prices,
    resolve_seed_pool,
    run_daily_job_with_failure_notification,
    weekly_report,
    watchlist_checkup,
    main,
)
from lurker.storage.db import create_session, init_db
from lurker.storage.models import Report
from lurker.trading_calendar import (
    FutureReportDateError,
    TradingCalendarUnavailable,
)


class FakeCalendar:
    def __init__(self, sessions):
        self.sessions = tuple(sessions)

    def is_trading_day(self, day):
        return day in self.sessions

    def previous_or_same_session(self, day):
        candidates = [item for item in self.sessions if item <= day]
        if not candidates:
            raise TradingCalendarUnavailable("no confirmed prior session")
        return candidates[-1]


class FakeMonthlyCalendar(FakeCalendar):
    def sessions_in_range(self, start, end):
        return tuple(
            item
            for item in self.sessions
            if start <= item <= end
        )


def _write_flow_snapshot(path, *, snapshot_date, sector_name):
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": f"{snapshot_date}T08:00:00+00:00",
                "market": "cn",
                "market_flow": {
                    "main_net_inflow": 1.0,
                    "super_large_net_inflow": 1.0,
                },
                "sector_flows": [
                    {
                        "name": sector_name,
                        "main_net_inflow": 100.0,
                        "rank": 1,
                    }
                ],
                "stock_flows": [],
                "margin": {},
                "core_etfs": [],
                "failures": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_demo_report_returns_markdown():
    report = build_demo_report(report_date="2026-05-18")

    assert report.content_md.startswith("# 大趋势雷达日报")
    assert "AI 算力基础设施" in report.content_md


def test_parse_markets_from_comma_separated_value():
    assert parse_markets("us,hk") == ["us", "hk"]
    assert parse_markets("us") == ["us"]


def test_data_snapshot_defaults_include_cn_market():
    parser = build_parser()

    args = parser.parse_args(["data-snapshot"])

    assert args.markets == "cn"


def test_build_data_snapshot_uses_cached_seed_pool(monkeypatch, tmp_path):
    calls = []
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-05-18T12:00:00+00:00",
  "markets": {
    "cn": {
      "symbols": ["300308.SZ", "300502.SZ"],
      "sources": {}
    }
  }
}
""",
        encoding="utf-8",
    )

    def fake_loader(themes_path):
        calls.append(("loader", themes_path))
        return {"cn": ["SHOULD_NOT_USE"]}

    def fake_collect(**kwargs):
        calls.append(("collect", kwargs["seed_symbols"], kwargs["markets"]))
        return [
            {
                "symbol": "300308.SZ",
                "market": "cn",
                "latest_close": 140.0,
                "return_20d": 0.2,
            }
        ]

    monkeypatch.setattr("lurker.cli.load_resolved_theme_seed_symbols", fake_loader)
    monkeypatch.setattr("lurker.cli.collect_price_snapshots", fake_collect)

    result = build_data_snapshot(
        themes_path=tmp_path / "themes.yaml",
        seed_pool_path=seed_pool_path,
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
    )

    assert "| 300308.SZ | cn | 140.00 | 20.00% |" in result
    assert calls == [
        ("collect", {"cn": ["300308.SZ", "300502.SZ"]}, ["cn"]),
    ]


def test_build_data_snapshot_falls_back_to_live_resolution(monkeypatch, tmp_path):
    calls = []

    def fake_loader(themes_path):
        calls.append(("loader", themes_path))
        return {"cn": ["300308.SZ"]}

    def fake_collect(**kwargs):
        calls.append(("collect", kwargs["seed_symbols"], kwargs["markets"]))
        return []

    monkeypatch.setattr("lurker.cli.load_resolved_theme_seed_symbols", fake_loader)
    monkeypatch.setattr("lurker.cli.collect_price_snapshots", fake_collect)

    result = build_data_snapshot(
        themes_path=tmp_path / "themes.yaml",
        seed_pool_path=tmp_path / "missing.json",
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
    )

    assert "No available data" in result
    assert calls == [
        ("loader", tmp_path / "themes.yaml"),
        ("collect", {"cn": ["300308.SZ"]}, ["cn"]),
    ]


def test_resolve_seed_pool_writes_cache(monkeypatch, tmp_path):
    output_path = tmp_path / "resolved_seed_pool.json"

    def fake_builder(themes_path):
        return {
            "generated_at": "2026-05-18T12:00:00+00:00",
            "markets": {"cn": {"symbols": ["300308.SZ"], "sources": {}}},
        }

    monkeypatch.setattr("lurker.cli.build_resolved_seed_pool", fake_builder)

    message = resolve_seed_pool(themes_path=tmp_path / "themes.yaml", output_path=output_path)

    assert "resolved seed pool" in message
    assert output_path.exists()
    assert "300308.SZ" in output_path.read_text(encoding="utf-8")


def test_parser_has_resolve_seeds_command():
    parser = build_parser()

    args = parser.parse_args(["resolve-seeds", "--output", "data/processed/resolved_seed_pool.json"])

    assert args.command == "resolve-seeds"
    assert str(args.output) == "data/processed/resolved_seed_pool.json"


def test_read_api_key_file_strips_whitespace(tmp_path):
    key_path = tmp_path / "key"
    key_path.write_text("gemini-secret\n", encoding="utf-8")

    assert read_api_key_file(key_path) == "gemini-secret"


def test_load_suppressed_symbols_from_yaml(tmp_path):
    path = tmp_path / "suppressed_symbols.yaml"
    path.write_text(
        """
symbols:
  - 300308.SZ
  - 300054.sz
""",
        encoding="utf-8",
    )

    assert load_suppressed_symbols(path) == {"300308.SZ", "300054.SZ"}


def test_parser_has_run_daily_api_key_file_default():
    parser = build_parser()

    args = parser.parse_args(["run-daily"])

    assert args.api_key_file.name == "key"


def test_parser_has_strategy_config_default():
    parser = build_parser()

    args = parser.parse_args(["run-daily"])

    assert args.strategy_config.name == "strategies.yaml"
    assert args.cadence == "daily"


def test_build_strategy_report_runs_enabled_long_term_strategy():
    report = build_strategy_report(
        snapshot_batch={"markets": ["cn"], "windows": [20], "snapshots": [], "failures": []},
        theme_mapping={},
        symbol_names={},
        attributor=None,
        report_date="2026-05-18",
        signal_threshold=60,
        main_limit=10,
        low_score_watch_limit=5,
        suppressed_symbols=set(),
        strategy_config_path=None,
        strategy_names=["long_term_trend"],
        strategy_cadence=None,
    )

    assert "# 大趋势雷达日报" in report.content_md
    assert "无个股触发" in report.content_md


def test_build_strategy_report_runs_professional_flow_strategy():
    report = build_strategy_report(
        snapshot_batch={
            "markets": ["cn"],
            "windows": [20, 60],
            "snapshots": [
                {
                    "symbol": "300308.SZ",
                    "market": "cn",
                    "return_20d": 0.3,
                    "return_60d": 0.6,
                    "return_120d": 0.8,
                },
                {
                    "symbol": "300054.SZ",
                    "market": "cn",
                    "return_20d": 0.1,
                    "return_60d": 0.1,
                    "return_120d": 0.1,
                },
            ],
            "failures": [],
        },
        flow_snapshot={
            "market_flow": {"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
            "sector_flows": [{"name": "ai_infra", "main_net_inflow": 100.0, "rank": 1}],
            "stock_flows": [
                {
                    "symbol": "300308.SZ",
                    "name": "中际旭创",
                    "main_net_inflow": 100.0,
                    "super_large_net_inflow": 50.0,
                    "main_net_inflow_5d": 100.0,
                    "main_net_inflow_10d": 100.0,
                }
            ],
            "margin": {"margin_balance_change": 1.0},
            "core_etfs": [],
            "failures": [],
        },
        theme_mapping={"300308.SZ": ["ai_infra"]},
        symbol_names={"300308.SZ": "中际旭创"},
        attributor=None,
        report_date="2026-06-04",
        signal_threshold=60,
        main_limit=10,
        low_score_watch_limit=5,
        suppressed_symbols=set(),
        strategy_config_path=None,
        strategy_names=["professional_flow_daily"],
        strategy_cadence=None,
    )

    assert "# 职业资金雷达日报" in report.content_md
    assert "中际旭创" in report.content_md


def test_build_run_daily_uses_strategy_config_when_provided(tmp_path):
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "2026-05-18.json").write_text(
        """
{
  "generated_at": "2026-05-18T12:00:00+00:00",
  "markets": ["cn"],
  "windows": [20],
  "snapshots": [],
  "failures": []
}
""",
        encoding="utf-8",
    )
    strategy_config = tmp_path / "strategies.yaml"
    strategy_config.write_text(
        """
strategies:
  long_term_trend:
    enabled: true
    cadence: daily
    universe: resolved_seed_pool
""",
        encoding="utf-8",
    )

    report = build_run_daily(
        price_snapshot_dir=snapshot_dir,
        report_date="2026-05-18",
        strategy_config_path=strategy_config,
        strategy_cadence="daily",
    )

    assert "# 大趋势雷达日报" in report


def test_build_run_daily_propagates_invalid_scoring_config(tmp_path):
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "2026-07-28.json").write_text(
        """
{
  "generated_at": "2026-07-28T08:00:00+00:00",
  "markets": ["cn"],
  "windows": [20, 60, 120, 180],
  "snapshots": [],
  "failures": []
}
""",
        encoding="utf-8",
    )
    scoring_path = tmp_path / "scoring.yaml"
    scoring_path.write_text(
        """
stock_signal:
  weights: {return_120_180d: 15}
sector_signal:
  weights: {sector_strength: 20}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="return_120_180d.*return_180d"):
        build_run_daily(
            price_snapshot_dir=snapshot_dir,
            report_date="2026-07-28",
            scoring_config_path=scoring_path,
        )


def test_daily_job_refreshes_prices_and_writes_report(monkeypatch, tmp_path):
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-05-16T12:00:00+00:00",
  "theme_mapping": {"300308.SZ": ["ai_infra"]},
  "markets": {
    "cn": {
      "symbols": ["300308.SZ"],
      "sources": {}
    }
  }
}
""",
        encoding="utf-8",
    )
    price_snapshot_dir = tmp_path / "price_snapshots"
    report_dir = tmp_path / "reports"

    def fake_collector(**kwargs):
        assert kwargs["seed_symbols"] == {"cn": ["300308.SZ"]}
        assert kwargs["markets"] == ["cn"]
        assert kwargs["windows"] == [20, 60]
        assert kwargs["period"] == "6mo"
        assert kwargs["limit_per_market"] == 1
        return {
            "generated_at": "2026-05-18T12:00:00+00:00",
            "seed_pool_generated_at": "2026-05-16T12:00:00+00:00",
            "markets": ["cn"],
            "windows": [20, 60],
            "snapshots": [{"symbol": "300308.SZ", "market": "cn", "latest_close": 140.0}],
            "failures": [{"symbol": "000001.SZ", "market": "cn", "reason": "empty price data"}],
        }

    def fake_run_daily(**kwargs):
        assert kwargs["snapshot_batch"]["snapshots"][0]["symbol"] == "300308.SZ"
        assert kwargs["theme_mapping"] == {"300308.SZ": ["ai_infra"]}
        assert kwargs["report_date"] == "2026-05-18"
        assert kwargs["signal_threshold"] == 55
        assert kwargs["main_limit"] == 8
        assert kwargs["suppressed_symbols"] == {"300308.SZ"}
        return DailyReport(report_date="2026-05-18", main_candidates_count=1, content_md="# 大趋势雷达日报\n\n日报内容")

    monkeypatch.setattr("lurker.cli.collect_price_snapshot_batch", fake_collector)
    monkeypatch.setattr("lurker.cli.run_daily", fake_run_daily)
    monkeypatch.setattr(
        "lurker.cli.build_notifier_from_env",
        lambda: pytest.fail("notifier must not be built when push=False"),
    )
    suppressed_symbols_path = tmp_path / "suppressed_symbols.yaml"
    suppressed_symbols_path.write_text("symbols:\n  - 300308.SZ\n", encoding="utf-8")

    message = daily_job(
        seed_pool_path=seed_pool_path,
        price_snapshot_dir=price_snapshot_dir,
        report_dir=report_dir,
        markets=["cn"],
        windows=[20, 60],
        period="6mo",
        limit_per_market=1,
        report_date="2026-05-18",
        signal_threshold=55,
        main_limit=8,
        suppressed_symbols_path=suppressed_symbols_path,
        push=False,
    )

    assert (price_snapshot_dir / "2026-05-18.json").exists()
    report_path = report_dir / "2026-05-18.md"
    assert report_path.read_text(encoding="utf-8") == "# 大趋势雷达日报\n\n日报内容\n"
    assert "snapshots=1" in message
    assert "failures=1" in message
    assert str(report_path) in message
    assert "Skipped pushing report (--no-push)." in message
    candidates_path = report_dir / "2026-05-18.candidates.json"
    assert candidates_path.exists()
    assert "300308.SZ" in candidates_path.read_text(encoding="utf-8")
    index_path = report_dir / "index.json"
    assert index_path.exists()
    assert "2026-05-18" in index_path.read_text(encoding="utf-8")


def test_daily_job_closes_db_session_when_price_collection_fails(
    monkeypatch,
    tmp_path,
):
    from sqlalchemy.orm import Session

    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-05-16T12:00:00+00:00",
  "markets": {"cn": {"symbols": ["300308.SZ"], "sources": {}}}
}
""",
        encoding="utf-8",
    )
    sessions = []

    class TrackingSession(Session):
        was_closed = False

        def close(self):
            self.was_closed = True
            super().close()

    def fake_create_session(engine):
        session = TrackingSession(engine)
        sessions.append(session)
        return session

    def fail_collection(**kwargs):
        raise RuntimeError("price collection failed")

    monkeypatch.setattr(
        "lurker.storage.db.create_session",
        fake_create_session,
    )
    monkeypatch.setattr(
        "lurker.cli.collect_price_snapshot_batch",
        fail_collection,
    )

    with pytest.raises(RuntimeError, match="price collection failed"):
        daily_job(
            seed_pool_path=seed_pool_path,
            price_snapshot_dir=tmp_path / "price_snapshots",
            report_dir=tmp_path / "reports",
            markets=["cn"],
            windows=[20],
            period="6mo",
            limit_per_market=1,
            report_date="2026-05-18",
            db_path=tmp_path / "lurker.sqlite",
            push=False,
        )

    assert len(sessions) == 1
    assert sessions[0].was_closed is True


def test_daily_job_keeps_db_session_open_until_report_is_persisted(
    monkeypatch,
    tmp_path,
):
    from sqlalchemy.orm import Session

    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-05-16T12:00:00+00:00",
  "markets": {"cn": {"symbols": ["300308.SZ"], "sources": {}}}
}
""",
        encoding="utf-8",
    )
    sessions = []

    class TrackingSession(Session):
        was_closed = False

        def close(self):
            self.was_closed = True
            super().close()

    def fake_create_session(engine):
        session = TrackingSession(engine)
        sessions.append(session)
        return session

    def fake_collector(**kwargs):
        assert kwargs["db_session"].was_closed is False
        return {
            "generated_at": "2026-05-18T12:00:00+00:00",
            "seed_pool_generated_at": "2026-05-16T12:00:00+00:00",
            "markets": ["cn"],
            "windows": [20],
            "snapshots": [
                {
                    "symbol": "300308.SZ",
                    "market": "cn",
                    "latest_close": 140.0,
                }
            ],
            "failures": [],
        }

    def fake_run_daily(**kwargs):
        assert kwargs["db_session"].was_closed is False
        return DailyReport(
            report_date="2026-05-18",
            main_candidates_count=1,
            content_md="# 日报\n",
        )

    monkeypatch.setattr(
        "lurker.storage.db.create_session",
        fake_create_session,
    )
    monkeypatch.setattr(
        "lurker.cli.collect_price_snapshot_batch",
        fake_collector,
    )
    monkeypatch.setattr("lurker.cli.run_daily", fake_run_daily)

    daily_job(
        seed_pool_path=seed_pool_path,
        price_snapshot_dir=tmp_path / "price_snapshots",
        report_dir=tmp_path / "reports",
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        report_date="2026-05-18",
        db_path=tmp_path / "lurker.sqlite",
        push=False,
    )

    assert len(sessions) == 1
    assert sessions[0].was_closed is True


def test_daily_job_candidate_history_includes_symbol_names(monkeypatch, tmp_path):
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-05-16T12:00:00+00:00",
  "theme_mapping": {"300308.SZ": ["ai_infra"]},
  "symbol_names": {"300308.SZ": "中际旭创"},
  "markets": {
    "cn": {
      "symbols": ["300308.SZ"],
      "sources": {}
    }
  }
}
""",
        encoding="utf-8",
    )
    price_snapshot_dir = tmp_path / "price_snapshots"
    report_dir = tmp_path / "reports"

    def fake_collector(**kwargs):
        return {
            "generated_at": "2026-05-18T12:00:00+00:00",
            "seed_pool_generated_at": "2026-05-16T12:00:00+00:00",
            "markets": ["cn"],
            "windows": [20],
            "snapshots": [{"symbol": "300308.SZ", "market": "cn", "latest_close": 140.0}],
            "failures": [],
        }

    def fake_run_daily(**kwargs):
        assert kwargs["symbol_names"] == {"300308.SZ": "中际旭创"}
        return DailyReport(report_date="2026-05-18", main_candidates_count=1, content_md="# 大趋势雷达日报\n\n日报内容")

    monkeypatch.setattr("lurker.cli.collect_price_snapshot_batch", fake_collector)
    monkeypatch.setattr("lurker.cli.run_daily", fake_run_daily)

    daily_job(
        seed_pool_path=seed_pool_path,
        price_snapshot_dir=price_snapshot_dir,
        report_dir=report_dir,
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        report_date="2026-05-18",
        strategy_config_path=None,
    )

    history = json.loads((report_dir / "2026-05-18.candidates.json").read_text(encoding="utf-8"))
    assert history["observed_symbols"][0]["name"] == "中际旭创"


def test_append_report_archive_index_upserts_by_date(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report_path = report_dir / "2026-05-18.md"
    candidate_path = report_dir / "2026-05-18.candidates.json"

    append_report_archive_index(
        report_dir=report_dir,
        report_date="2026-05-18",
        report_path=report_path,
        candidates_path=candidate_path,
        snapshot_path=tmp_path / "snapshots" / "2026-05-18.json",
        strategies=["long_term_trend"],
        markets=["cn"],
        windows=[20, 60],
        snapshot_count=1,
        failure_count=0,
    )
    append_report_archive_index(
        report_dir=report_dir,
        report_date="2026-05-18",
        report_path=report_path,
        candidates_path=candidate_path,
        snapshot_path=tmp_path / "snapshots" / "2026-05-18.json",
        strategies=["long_term_trend"],
        markets=["cn"],
        windows=[20, 60],
        snapshot_count=2,
        failure_count=1,
    )

    index_data = json.loads((report_dir / "index.json").read_text(encoding="utf-8"))

    assert index_data["schema_version"] == 1
    assert len(index_data["reports"]) == 1
    assert index_data["reports"][0]["snapshot_count"] == 2
    assert index_data["reports"][0]["failure_count"] == 1


def test_list_reports_renders_recent_archive_entries(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "index.json").write_text(
        """
{
  "schema_version": 1,
  "reports": [
    {
      "date": "2026-05-17",
      "report_path": "/tmp/2026-05-17.md",
      "candidates_path": "/tmp/2026-05-17.candidates.json",
      "strategies": ["long_term_trend"],
      "snapshot_count": 1,
      "failure_count": 0
    },
    {
      "date": "2026-05-18",
      "report_path": "/tmp/2026-05-18.md",
      "candidates_path": "/tmp/2026-05-18.candidates.json",
      "strategies": ["long_term_trend", "short_term_setup"],
      "snapshot_count": 2,
      "failure_count": 1
    }
  ]
}
""",
        encoding="utf-8",
    )

    output = list_reports(report_dir=report_dir, limit=1)

    assert "2026-05-18" in output
    assert "short_term_setup" in output
    assert "2026-05-17" not in output


def test_refresh_prices_writes_snapshot(monkeypatch, tmp_path):
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-05-16T12:00:00+00:00",
  "markets": {
    "cn": {
      "symbols": ["300308.SZ"],
      "sources": {}
    }
  }
}
""",
        encoding="utf-8",
    )
    output_dir = tmp_path / "price_snapshots"

    def fake_collector(**kwargs):
        assert kwargs["seed_symbols"] == {"cn": ["300308.SZ"]}
        assert kwargs["seed_pool_generated_at"] == "2026-05-16T12:00:00+00:00"
        return {
            "generated_at": "2026-05-18T12:00:00+00:00",
            "seed_pool_generated_at": "2026-05-16T12:00:00+00:00",
            "markets": ["cn"],
            "windows": [20],
            "snapshots": [{"symbol": "300308.SZ", "market": "cn", "latest_close": 140.0}],
            "failures": [],
        }

    monkeypatch.setattr("lurker.cli.collect_price_snapshot_batch", fake_collector)

    message = refresh_prices(
        seed_pool_path=seed_pool_path,
        output_dir=output_dir,
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        snapshot_date="2026-05-18",
    )

    assert "Wrote price snapshot" in message
    assert "snapshots=1" in message
    assert "failures=0" in message
    assert (output_dir / "2026-05-18.json").exists()


def test_refresh_flows_writes_snapshot(monkeypatch, tmp_path):
    output_dir = tmp_path / "flow_snapshots"

    def fake_collector(**kwargs):
        return {
            "schema_version": 1,
            "generated_at": "2026-06-04T00:00:00+00:00",
            "market": "cn",
            "market_flow": {"main_net_inflow": 1.0},
            "sector_flows": [],
            "stock_flows": [],
            "margin": {},
            "core_etfs": [],
            "failures": [],
        }

    monkeypatch.setattr("lurker.cli.collect_flow_snapshot", fake_collector)

    message = refresh_flows(output_dir=output_dir, snapshot_date="2026-06-04")

    assert "Wrote flow snapshot" in message
    assert (output_dir / "2026-06-04.json").exists()


def test_data_snapshot_uses_latest_price_snapshot(monkeypatch, tmp_path):
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        '{"generated_at": "2026-05-16T12:00:00+00:00", "markets": {}}',
        encoding="utf-8",
    )
    snapshot_dir = tmp_path / "price_snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "2026-05-18.json").write_text(
        """
{
  "generated_at": "2026-05-18T12:00:00+00:00",
  "windows": [20],
  "snapshots": [
    {"symbol": "300308.SZ", "market": "cn", "latest_close": 140.0, "return_20d": 0.2},
    {"symbol": "NVDA", "market": "us", "latest_close": 1000.0, "return_20d": 0.1}
  ]
}
""",
        encoding="utf-8",
    )

    def fail_collect(**kwargs):
        raise AssertionError("should read local price snapshot")

    monkeypatch.setattr("lurker.cli.collect_price_snapshots", fail_collect)

    result = build_data_snapshot(
        themes_path=tmp_path / "themes.yaml",
        seed_pool_path=seed_pool_path,
        price_snapshot_dir=snapshot_dir,
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
    )

    assert "| 300308.SZ | cn | 140.00 | 20.00% |" in result
    assert "NVDA" not in result


def test_parser_has_refresh_prices_command():
    parser = build_parser()

    args = parser.parse_args(["refresh-prices", "--markets", "cn", "--date", "2026-05-18"])

    assert args.command == "refresh-prices"
    assert args.markets == "cn"
    assert args.date == "2026-05-18"


def test_parser_has_refresh_flows_command():
    parser = build_parser()

    args = parser.parse_args(["refresh-flows", "--date", "2026-06-04"])

    assert args.command == "refresh-flows"
    assert args.date == "2026-06-04"


def test_parser_has_weekly_report_push_option():
    parser = build_parser()

    args = parser.parse_args(["weekly-report", "--date", "2026-06-07", "--push"])

    assert args.command == "weekly-report"
    assert args.date == "2026-06-07"
    assert args.push is True


def test_weekly_report_pushes_when_enabled(monkeypatch, tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    (flow_dir / "2026-06-05.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-06-05T00:00:00+00:00",
                "market": "cn",
                "market_flow": {"main_net_inflow": -1.0, "super_large_net_inflow": -1.0},
                "sector_flows": [{"name": "机器人", "main_net_inflow": 100.0, "rank": 1}],
                "stock_flows": [],
                "margin": {},
                "core_etfs": [],
                "failures": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    monkeypatch.setattr("lurker.cli.build_notifier_from_env", lambda: FakeNotifier())

    message = weekly_report(
        flow_snapshot_dir=flow_dir,
        report_dir=tmp_path / "reports",
        report_date="2026-06-05",
        push=True,
        db_path=None,
    )

    assert sends
    assert "职业资金雷达周报" in sends[0][1]
    assert "Pushed weekly report successfully" in message


def test_weekly_report_falls_back_and_pushes_on_cn_non_trading_day(
    monkeypatch,
    tmp_path,
):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    (flow_dir / "2026-06-18.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-06-18T00:00:00+00:00",
                "market": "cn",
                "market_flow": {"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
                "sector_flows": [{"name": "机器人", "main_net_inflow": 100.0, "rank": 1}],
                "stock_flows": [],
                "margin": {},
                "core_etfs": [],
                "failures": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    monkeypatch.setattr("lurker.cli.build_notifier_from_env", lambda: FakeNotifier())

    message = weekly_report(
        flow_snapshot_dir=flow_dir,
        report_dir=tmp_path / "reports",
        report_date="2026-06-19",
        today=date(2026, 7, 28),
        calendar=FakeCalendar([date(2026, 6, 18)]),
        push=True,
        db_path=None,
    )

    assert sends
    assert sends[0][0] == "Lurker 周报 (2026-06-18)"
    assert "请求日期 2026-06-19，按最近交易日 2026-06-18 生成" in message
    assert not (tmp_path / "reports" / "weekly_2026-06-19.md").exists()
    assert (tmp_path / "reports" / "weekly_2026-06-18.md").exists()


def test_build_notifier_from_env_can_build_composite(monkeypatch):
    monkeypatch.setenv("PUSHPLUS_TOKEN", "push-token")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "from@example.com")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")

    notifier = build_notifier_from_env()

    assert type(notifier).__name__ == "CompositeNotifier"


def test_daily_job_wrapper_sends_failure_notification_and_preserves_exception():
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    def fail():
        raise RuntimeError("collector exploded")

    with pytest.raises(RuntimeError, match="collector exploded"):
        run_daily_job_with_failure_notification(
            action=fail,
            report_date="2026-07-28",
            push=True,
            notifier=FakeNotifier(),
        )

    assert sends[0][0] == "[故障] 职业资金雷达日报 2026-07-28"
    assert "阶段：daily_job" in sends[0][1]
    assert "RuntimeError: collector exploded" in sends[0][1]


def test_daily_job_wrapper_does_not_replace_original_error_when_notification_fails():
    class FailingNotifier:
        def send(self, title, markdown_content):
            raise ConnectionError("notification unavailable")

    def fail():
        raise RuntimeError("collector exploded")

    with pytest.raises(RuntimeError, match="collector exploded"):
        run_daily_job_with_failure_notification(
            action=fail,
            report_date="2026-07-28",
            push=True,
            notifier=FailingNotifier(),
        )


def test_flow_degradation_reasons_include_nested_etf_and_stale_margin():
    reasons = _flow_degradation_reasons(
        {
            "failures": [],
            "core_etfs": {
                "failures": [{"symbol": "510300.SH", "reason": "timeout"}],
            },
            "margin": {"availability": "stale_cache"},
        }
    )

    assert "核心 ETF 采集不完整" in reasons
    assert "两融数据非当日" in reasons


def test_daily_job_validation_failure_sends_fault_instead_of_normal_report(
    monkeypatch,
    tmp_path,
):
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-07-28T00:00:00+00:00",
  "markets": {"cn": {"symbols": ["300308.SZ"], "sources": {}}}
}
""",
        encoding="utf-8",
    )
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    monkeypatch.setattr(
        "lurker.cli.collect_price_snapshot_batch",
        lambda **kwargs: {
            "generated_at": "2026-07-28T00:00:00+00:00",
            "markets": ["cn"],
            "windows": [20],
            "snapshots": [],
            "failures": [],
        },
    )
    monkeypatch.setattr("lurker.cli.build_notifier_from_env", lambda: FakeNotifier())

    with pytest.raises(DailyJobFailed, match="DAILY_JOB_STATUS=FAILED"):
        daily_job(
            seed_pool_path=seed_pool_path,
            price_snapshot_dir=tmp_path / "price_snapshots",
            report_dir=tmp_path / "reports",
            markets=["cn"],
            windows=[20],
            period="6mo",
            limit_per_market=1,
            report_date="2026-07-28",
            push=True,
        )

    assert sends[0][0] == "[故障] 职业资金雷达日报 2026-07-28"
    assert "价格数据快照为空" in sends[0][1]
    assert all("Lurker 雷达" not in title for title, _ in sends)


def test_watchlist_notifier_does_not_use_daily_recipient_environment(monkeypatch):
    monkeypatch.setenv("PUSHPLUS_TOKEN", "daily-token")
    monkeypatch.setenv("EMAIL_TO", "daily@example.com")
    monkeypatch.delenv("WATCHLIST_PUSHPLUS_TOKEN", raising=False)
    monkeypatch.delenv("WATCHLIST_SMTP_HOST", raising=False)
    monkeypatch.delenv("WATCHLIST_SMTP_FROM", raising=False)
    monkeypatch.delenv("WATCHLIST_EMAIL_TO", raising=False)

    assert build_watchlist_notifier_from_env() is None


def test_watchlist_notifier_uses_only_watchlist_email_recipient(monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "daily@example.com")
    monkeypatch.setenv("WATCHLIST_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("WATCHLIST_SMTP_FROM", "watch@example.com")
    monkeypatch.setenv("WATCHLIST_EMAIL_TO", "owner@example.com")

    notifier = build_watchlist_notifier_from_env()

    assert notifier.recipients == ["owner@example.com"]


def test_watchlist_notifier_rejects_empty_recipient_list(monkeypatch):
    monkeypatch.setenv("WATCHLIST_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("WATCHLIST_SMTP_FROM", "watch@example.com")
    monkeypatch.setenv("WATCHLIST_EMAIL_TO", " ,  , ")

    with pytest.raises(ValueError, match="WATCHLIST_EMAIL_TO has no recipients"):
        build_watchlist_notifier_from_env()


def test_watchlist_notifier_rejects_incomplete_email_configuration(monkeypatch):
    monkeypatch.setenv("WATCHLIST_SMTP_HOST", "smtp.example.com")
    monkeypatch.delenv("WATCHLIST_SMTP_FROM", raising=False)
    monkeypatch.delenv("WATCHLIST_EMAIL_TO", raising=False)

    with pytest.raises(ValueError, match="incomplete WATCHLIST email configuration"):
        build_watchlist_notifier_from_env()


PERSONAL_ENV_NAMES = (
    "PERSONAL_PUSHPLUS_TOKEN",
    "PERSONAL_SMTP_HOST",
    "PERSONAL_SMTP_PORT",
    "PERSONAL_SMTP_USER",
    "PERSONAL_SMTP_PASSWORD",
    "PERSONAL_SMTP_FROM",
    "PERSONAL_EMAIL_TO",
    "PERSONAL_SMTP_USE_TLS",
    "PERSONAL_SMTP_USE_SSL",
)


def clear_personal_environment(monkeypatch):
    for name in PERSONAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_personal_notifier_ignores_daily_and_watchlist_environment(monkeypatch):
    clear_personal_environment(monkeypatch)
    monkeypatch.setenv("PUSHPLUS_TOKEN", "daily")
    monkeypatch.setenv("WATCHLIST_PUSHPLUS_TOKEN", "watchlist")
    monkeypatch.setenv("EMAIL_TO", "daily@example.com")

    assert build_personal_notifier_from_env() is None


def test_personal_notifier_builds_pushplus_and_complete_email(monkeypatch):
    clear_personal_environment(monkeypatch)
    monkeypatch.setenv("PERSONAL_PUSHPLUS_TOKEN", "personal-token")
    monkeypatch.setenv("PERSONAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("PERSONAL_SMTP_PORT", "465")
    monkeypatch.setenv("PERSONAL_SMTP_USER", "owner")
    monkeypatch.setenv("PERSONAL_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("PERSONAL_SMTP_FROM", "owner@example.com")
    monkeypatch.setenv("PERSONAL_EMAIL_TO", "a@example.com, b@example.com")
    monkeypatch.setenv("PERSONAL_SMTP_USE_TLS", "false")
    monkeypatch.setenv("PERSONAL_SMTP_USE_SSL", "true")

    notifier = build_personal_notifier_from_env()

    assert type(notifier).__name__ == "CompositeNotifier"
    pushplus, email = notifier.notifiers
    assert pushplus.token == "personal-token"
    assert email.port == 465
    assert email.recipients == ["a@example.com", "b@example.com"]
    assert email.use_tls is False
    assert email.use_ssl is True


@pytest.mark.parametrize(
    "partial",
    [
        {"PERSONAL_SMTP_HOST": "smtp.example.com"},
        {"PERSONAL_SMTP_FROM": "owner@example.com"},
        {"PERSONAL_EMAIL_TO": "owner@example.com"},
        {"PERSONAL_SMTP_USER": "owner"},
    ],
)
def test_personal_notifier_rejects_incomplete_email_configuration(monkeypatch, partial):
    clear_personal_environment(monkeypatch)
    for name, value in partial.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="incomplete PERSONAL email configuration"):
        build_personal_notifier_from_env()


def test_personal_notifier_rejects_empty_recipients(monkeypatch):
    clear_personal_environment(monkeypatch)
    monkeypatch.setenv("PERSONAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("PERSONAL_SMTP_FROM", "owner@example.com")
    monkeypatch.setenv("PERSONAL_EMAIL_TO", " , ")

    with pytest.raises(ValueError, match="PERSONAL_EMAIL_TO has no recipients"):
        build_personal_notifier_from_env()


def test_personal_notifier_rejects_invalid_port(monkeypatch):
    clear_personal_environment(monkeypatch)
    monkeypatch.setenv("PERSONAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("PERSONAL_SMTP_FROM", "owner@example.com")
    monkeypatch.setenv("PERSONAL_EMAIL_TO", "owner@example.com")
    monkeypatch.setenv("PERSONAL_SMTP_PORT", "invalid")

    with pytest.raises(ValueError, match="PERSONAL_SMTP_PORT must be an integer"):
        build_personal_notifier_from_env()


def test_parser_has_monthly_macro_flow_defaults():
    args = build_parser().parse_args(
        ["monthly-macro-flow", "--month", "2025-01", "--no-push"]
    )
    assert args.command == "monthly-macro-flow"
    assert args.month == "2025-01"
    assert args.no_push is True
    assert args.month_end_only is False
    assert args.flow_snapshot_dir.parts[-2:] == (
        "processed",
        "flow_snapshots",
    )


def test_monthly_macro_month_end_gate_skips_before_collection(tmp_path):
    calendar = FakeMonthlyCalendar(
        [
            date(2026, 7, 29),
            date(2026, 7, 30),
            date(2026, 7, 31),
        ]
    )

    def fail_if_called(**kwargs):
        raise AssertionError("collector must not run before month end")

    message = monthly_macro_flow_job(
        report_month="2026-07",
        config_path=tmp_path / "missing.yaml",
        snapshot_dir=tmp_path / "snapshots",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        strategy_config_path=tmp_path / "strategies.yaml",
        push=True,
        month_end_only=True,
        snapshot_collector=fail_if_called,
        today=date(2026, 7, 29),
        calendar=calendar,
    )
    assert "2026-07-29 is not the last CN trading day" in message
    assert not (tmp_path / "snapshots").exists()


def test_monthly_macro_runs_on_dynamic_last_session(tmp_path):
    from tests.test_monthly_macro_flow import complete_snapshot

    calendar = FakeMonthlyCalendar(
        [
            date(2025, 1, 27),
            date(2025, 1, 28),
        ]
    )
    message = monthly_macro_flow_job(
        report_month="2025-01",
        config_path=Path("configs/macro_monthly.yaml"),
        snapshot_dir=tmp_path / "snapshots",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        strategy_config_path=Path("configs/strategies.yaml"),
        push=False,
        month_end_only=True,
        snapshot_collector=lambda **kwargs: complete_snapshot(),
        today=date(2025, 1, 28),
        calendar=calendar,
    )
    assert "state=牛市加速" in message
    assert "push=skipped(--no-push)" in message
    assert (tmp_path / "reports" / "2025-01.md").exists()


def test_monthly_macro_uses_report_month_last_session_for_weekly_context(tmp_path):
    from tests.test_monthly_macro_flow import complete_snapshot

    captured = {}

    def build_summary(**kwargs):
        captured.update(kwargs)
        return WeeklyFlowSummary(
            availability="unavailable",
            start_date=None,
            end_date=None,
            snapshot_count=0,
            temperature_counts={"进攻": 0, "观察": 0, "防守": 0},
            main_net_inflow_sum=0.0,
            super_large_net_inflow_sum=0.0,
            latest_etf_status="unknown",
            latest_margin_signal="unknown",
            continued_sectors=(),
            new_sectors=(),
            ebb_sectors=(),
            failure_count=0,
            quality_notes=(),
        )

    monthly_macro_flow_job(
        report_month="2026-07",
        config_path=Path("configs/macro_monthly.yaml"),
        snapshot_dir=tmp_path / "snapshots",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        strategy_config_path=Path("configs/strategies.yaml"),
        flow_snapshot_dir=tmp_path / "flow_snapshots",
        push=False,
        snapshot_collector=lambda **kwargs: complete_snapshot(),
        weekly_summary_builder=build_summary,
        today=date(2026, 8, 1),
        calendar=FakeMonthlyCalendar([date(2026, 7, 30), date(2026, 7, 31)]),
    )

    assert captured["report_date"] == "2026-07-31"
    assert captured["flow_snapshot_dir"] == tmp_path / "flow_snapshots"


@pytest.mark.parametrize(
    ("data_observation", "expected_push"),
    [(False, "sent"), (True, "skipped(data_observation)")],
)
def test_monthly_macro_push_gate_remains_based_on_macro_classification(
    monkeypatch,
    tmp_path,
    data_observation,
    expected_push,
):
    from tests.test_monthly_macro_flow import complete_snapshot

    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    snapshot = complete_snapshot()
    if data_observation:
        snapshot["macro"]["household"] = None
    monkeypatch.setattr("lurker.cli.build_notifier_from_env", lambda: FakeNotifier())

    message = monthly_macro_flow_job(
        report_month="2025-01",
        config_path=Path("configs/macro_monthly.yaml"),
        snapshot_dir=tmp_path / "snapshots",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        strategy_config_path=Path("configs/strategies.yaml"),
        flow_snapshot_dir=tmp_path / "empty_flow_snapshots",
        push=True,
        snapshot_collector=lambda **kwargs: snapshot,
        today=date(2025, 1, 28),
        calendar=FakeMonthlyCalendar([date(2025, 1, 27), date(2025, 1, 28)]),
    )

    assert f"push={expected_push}" in message
    assert bool(sends) is (not data_observation)


def test_monthly_macro_rejects_future_month(tmp_path):
    with pytest.raises(ValueError, match="future report month"):
        monthly_macro_flow_job(
            report_month="2026-08",
            config_path=tmp_path / "config.yaml",
            snapshot_dir=tmp_path / "snapshots",
            raw_dir=tmp_path / "raw",
            report_dir=tmp_path / "reports",
            strategy_config_path=tmp_path / "strategies.yaml",
            push=False,
            today=date(2026, 7, 29),
        )


def test_parser_has_independent_watchlist_checkup_defaults():
    args = build_parser().parse_args(["watchlist-checkup"])

    assert args.command == "watchlist-checkup"
    assert args.watchlist.name == "watchlist.yaml"
    assert args.report_dir.parts[-2:] == ("reports", "watchlist")
    assert args.state_file.name == "watchlist_alert_state.json"
    assert args.no_push is False


def test_watchlist_checkup_passes_no_push_and_returns_counts(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("lurker.config.load_watchlist", lambda path: "loaded-config")

    def fake_run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            report_path=tmp_path / "reports" / "2026-07-20.md",
            checked_count=2,
            new_alert_count=1,
            failure_count=1,
            pushed=False,
        )

    monkeypatch.setattr(
        "lurker.application.watchlist_anomaly.run_watchlist_anomaly",
        fake_run,
    )
    monkeypatch.setattr(
        "lurker.cli.build_watchlist_notifier_from_env",
        lambda: "watchlist-notifier",
    )

    message = watchlist_checkup(
        watchlist_path=tmp_path / "watchlist.yaml",
        report_dir=tmp_path / "reports",
        state_file=tmp_path / "state.json",
        report_date="2026-07-20",
        push=False,
    )

    assert calls[0]["config"] == "loaded-config"
    assert calls[0]["push"] is False
    assert calls[0]["notifier"] == "watchlist-notifier"
    assert "checked=2, alerts=1, failures=1, pushed=False" in message


def test_daily_job_pushes_degraded_report_when_rollout_artifact_is_missing(
    monkeypatch,
    tmp_path,
):
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-06-08T00:00:00+00:00",
  "theme_mapping": {},
  "markets": {
    "cn": {
      "symbols": ["300308.SZ"],
      "sources": {}
    }
  }
}
""",
        encoding="utf-8",
    )
    strategy_config = tmp_path / "strategies.yaml"
    strategy_config.write_text(
        """
strategies:
  professional_flow_daily:
    enabled: true
    cadence: daily
    universe: resolved_seed_pool
""",
        encoding="utf-8",
    )
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    def fake_price_collector(**kwargs):
        return {
            "generated_at": "2026-06-08T00:00:00+00:00",
            "markets": ["cn"],
            "windows": [20],
            "snapshots": [{"symbol": "300308.SZ", "market": "cn", "return_20d": 0.1}],
            "failures": [],
        }

    def fake_flow_collector():
        return {
            "schema_version": 1,
            "generated_at": "2026-06-08T00:00:00+00:00",
            "market": "cn",
            "market_flow": {"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
            "sector_flows": [{"name": "通信设备", "main_net_inflow": 100.0, "rank": 1}],
            "stock_flows": [],
            "margin": {},
            "core_etfs": [],
            "failures": [],
        }

    monkeypatch.setattr("lurker.cli.collect_price_snapshot_batch", fake_price_collector)
    monkeypatch.setattr("lurker.cli.collect_flow_snapshot", fake_flow_collector)
    monkeypatch.setattr("lurker.cli.build_notifier_from_env", lambda: FakeNotifier())

    message = daily_job(
        seed_pool_path=seed_pool_path,
        price_snapshot_dir=tmp_path / "price_snapshots",
        flow_snapshot_dir=tmp_path / "flow_snapshots",
        report_dir=tmp_path / "reports",
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        report_date="2026-06-08",
        strategy_config_path=strategy_config,
        strategy_cadence="daily",
        temperature_artifact_path=tmp_path / "missing-artifact.json",
        temperature_replay_path=tmp_path / "missing-replay.json",
    )

    assert len(sends) == 1
    assert sends[0][0].startswith("[降级]")
    assert "市场温度上线闸门：缺少 rollout artifact" in sends[0][1]
    assert "Pushed degraded report successfully." in message
    assert "DAILY_JOB_STATUS=DEGRADED" in message
    report_text = (tmp_path / "reports" / "2026-06-08.md").read_text(encoding="utf-8")
    assert "市场温度上线闸门：缺少 rollout artifact" in report_text

    sends.clear()
    no_push_message = daily_job(
        seed_pool_path=seed_pool_path,
        price_snapshot_dir=tmp_path / "price_snapshots",
        flow_snapshot_dir=tmp_path / "flow_snapshots",
        report_dir=tmp_path / "reports",
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        report_date="2026-06-08",
        strategy_config_path=strategy_config,
        strategy_cadence="daily",
        temperature_artifact_path=tmp_path / "missing-artifact.json",
        temperature_replay_path=tmp_path / "missing-replay.json",
        push=False,
    )

    assert sends == []
    assert "Skipped pushing report (--no-push)." in no_push_message
    assert "DAILY_JOB_STATUS=DEGRADED" in no_push_message

    class FailingNotifier:
        def send(self, title, markdown_content):
            raise ConnectionError("push channel unavailable")

    monkeypatch.setattr(
        "lurker.cli.build_notifier_from_env",
        lambda: FailingNotifier(),
    )
    with pytest.raises(
        DailyJobFailed,
        match='DAILY_JOB_STATUS=FAILED stage="notification"',
    ):
        daily_job(
            seed_pool_path=seed_pool_path,
            price_snapshot_dir=tmp_path / "price_snapshots",
            flow_snapshot_dir=tmp_path / "flow_snapshots",
            report_dir=tmp_path / "reports",
            markets=["cn"],
            windows=[20],
            period="6mo",
            limit_per_market=1,
            report_date="2026-06-08",
            strategy_config_path=strategy_config,
            strategy_cadence="daily",
            temperature_artifact_path=tmp_path / "missing-artifact.json",
            temperature_replay_path=tmp_path / "missing-replay.json",
        )


def test_daily_job_pushes_professional_report_when_only_stock_flows_fail(monkeypatch, tmp_path):
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-06-12T00:00:00+00:00",
  "theme_mapping": {},
  "markets": {
    "cn": {
      "symbols": ["300308.SZ"],
      "sources": {}
    }
  }
}
""",
        encoding="utf-8",
    )
    strategy_config = tmp_path / "strategies.yaml"
    strategy_config.write_text(
        """
strategies:
  professional_flow_daily:
    enabled: true
    cadence: daily
    universe: resolved_seed_pool
""",
        encoding="utf-8",
    )
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    def fake_price_collector(**kwargs):
        return {
            "generated_at": "2026-06-12T00:00:00+00:00",
            "markets": ["cn"],
            "windows": [20],
            "snapshots": [{"symbol": "300308.SZ", "market": "cn", "return_20d": 0.1}],
            "failures": [],
        }

    def fake_flow_collector():
        return {
            "schema_version": 1,
            "generated_at": "2026-06-12T00:00:00+00:00",
            "market": "cn",
            "market_flow": {"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
            "sector_flows": [{"name": "通信设备", "main_net_inflow": 100.0, "rank": 1}],
            "stock_flows": [],
            "margin": {},
            "core_etfs": [],
            "failures": [{"source": "stock_flows", "reason": "ReadTimeout: timed out"}],
        }

    monkeypatch.setattr("lurker.cli.collect_price_snapshot_batch", fake_price_collector)
    monkeypatch.setattr("lurker.cli.collect_flow_snapshot", fake_flow_collector)
    monkeypatch.setattr("lurker.cli.build_notifier_from_env", lambda: FakeNotifier())
    replay_path, artifact_path = _write_approved_temperature_artifact(tmp_path)

    message = daily_job(
        seed_pool_path=seed_pool_path,
        price_snapshot_dir=tmp_path / "price_snapshots",
        flow_snapshot_dir=tmp_path / "flow_snapshots",
        report_dir=tmp_path / "reports",
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        report_date="2026-06-12",
        strategy_config_path=strategy_config,
        strategy_cadence="daily",
        temperature_artifact_path=artifact_path,
        temperature_replay_path=replay_path,
    )

    assert sends
    assert sends[0][0].startswith("[降级]")
    assert "个股资金流不可用" in sends[0][1]
    assert "空列表不代表确认没有机会" in sends[0][1]
    assert "Pushed degraded report successfully" in message
    assert "DAILY_JOB_STATUS=DEGRADED" in message


def test_weekly_report_falls_back_and_uses_effective_date_everywhere(
    monkeypatch,
    tmp_path,
):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    _write_flow_snapshot(
        flow_dir / "2026-06-18.json",
        snapshot_date="2026-06-18",
        sector_name="有效板块",
    )
    _write_flow_snapshot(
        flow_dir / "2026-06-19.json",
        snapshot_date="2026-06-19",
        sector_name="未来污染",
    )
    calendar = FakeCalendar([date(2026, 6, 18)])
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    monkeypatch.setattr(
        "lurker.cli.build_notifier_from_env",
        lambda: FakeNotifier(),
    )
    db_path = tmp_path / "reports.sqlite"
    kwargs = {
        "flow_snapshot_dir": flow_dir,
        "report_dir": tmp_path / "reports",
        "report_date": "2026-06-21",
        "today": date(2026, 7, 28),
        "calendar": calendar,
        "push": True,
        "db_path": db_path,
    }
    message = weekly_report(**kwargs)
    weekly_report(**kwargs)

    report_path = tmp_path / "reports" / "weekly_2026-06-18.md"
    assert report_path.exists()
    assert not (tmp_path / "reports" / "weekly_2026-06-21.md").exists()
    text = report_path.read_text(encoding="utf-8")
    assert "请求日期 2026-06-21，按最近交易日 2026-06-18 生成" in text
    assert "未来污染" not in text
    assert sends[0][0] == "Lurker 周报 (2026-06-18)"

    engine = init_db(db_path)
    with create_session(engine) as session:
        rows = session.query(Report).filter_by(report_type="weekly").all()
        assert [row.report_date.isoformat() for row in rows] == ["2026-06-18"]
    assert "weekly_2026-06-18.md" in message


def test_daily_job_skips_non_session_without_backfill(tmp_path):
    message = daily_job(
        seed_pool_path=tmp_path / "missing.json",
        price_snapshot_dir=tmp_path / "prices",
        report_dir=tmp_path / "reports",
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        report_date="2026-06-19",
        today=date(2026, 7, 28),
        calendar=FakeCalendar([date(2026, 6, 18)]),
    )
    assert message == "Skipped daily job: cn market closed on 2026-06-19."
    assert not (tmp_path / "prices").exists()
    assert not (tmp_path / "reports").exists()


def test_future_daily_and_weekly_dates_have_zero_side_effects(tmp_path):
    calendar = FakeCalendar([date(2026, 7, 28)])
    with pytest.raises(FutureReportDateError):
        daily_job(
            seed_pool_path=tmp_path / "missing.json",
            price_snapshot_dir=tmp_path / "prices",
            report_dir=tmp_path / "daily",
            markets=["cn"],
            windows=[20],
            period="6mo",
            limit_per_market=1,
            report_date="2026-07-29",
            today=date(2026, 7, 28),
            calendar=calendar,
        )
    with pytest.raises(FutureReportDateError):
        weekly_report(
            flow_snapshot_dir=tmp_path / "flows",
            report_dir=tmp_path / "weekly",
            report_date="2026-07-29",
            today=date(2026, 7, 28),
            calendar=calendar,
        )
    assert not (tmp_path / "prices").exists()
    assert not (tmp_path / "daily").exists()
    assert not (tmp_path / "weekly").exists()


def test_build_run_daily_rejects_future_date_before_database_write(tmp_path):
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "2026-07-28.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-28T08:00:00+00:00",
                "markets": ["cn"],
                "windows": [20],
                "snapshots": [],
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "reports.sqlite"

    with pytest.raises(FutureReportDateError):
        build_run_daily(
            price_snapshot_dir=snapshot_dir,
            report_date="2026-07-29",
            today=date(2026, 7, 28),
            calendar=FakeCalendar([date(2026, 7, 28)]),
            db_path=db_path,
        )

    assert not db_path.exists()


def test_main_reports_calendar_error_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["lurker", "weekly-report", "--date", "2026-07-29"],
    )
    monkeypatch.setattr(
        "lurker.cli.weekly_report",
        lambda **kwargs: (_ for _ in ()).throw(
            FutureReportDateError("future report date")
        ),
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "future report date" in capsys.readouterr().err


def _write_approved_temperature_artifact(tmp_path):
    from lurker.application.temperature_replay import (
        build_rollout_artifact,
        replay_temperature_records,
    )
    from lurker.trading_calendar import is_cn_trading_day

    replay_path = tmp_path / "etf_60d_replay.json"
    records = []
    cursor = date(2026, 4, 24)
    states = [
        (10.0, 5.0, 1.3, 1.0),
        (10.0, 5.0, 1.0, None),
        (-10.0, -5.0, 1.0, -1.0),
    ]
    for index in range(60):
        while not is_cn_trading_day(cursor):
            cursor += timedelta(days=1)
        trade_day = cursor.isoformat()
        cursor += timedelta(days=1)
        main_flow, super_flow, expansion, margin_change = states[index % 3]
        records.append(
            {
                "date": trade_day,
                "market_flow": {
                    "trade_date": trade_day,
                    "main_net_inflow": main_flow,
                    "super_large_net_inflow": super_flow,
                    "availability": "fresh",
                },
                "core_etfs": {
                    "configured_symbols": ["510300.SH"],
                    "items": [
                        {
                            "symbol": "510300.SH",
                            "name": "沪深300ETF",
                            "trade_date": trade_day,
                            "current_turnover": 100.0,
                            "avg_turnover_20d": 100.0 / expansion,
                            "turnover_expansion": expansion,
                            "shares": None,
                            "shares_date": None,
                            "status": "active" if expansion >= 1.2 else "inactive",
                            "source": "fixture",
                            "availability": "turnover_only",
                            "error": None,
                        }
                    ],
                    "failures": [],
                    "generated_at": f"{trade_day}T08:00:00+00:00",
                    "schema_version": 1,
                },
                "margin": {
                    "trade_date": trade_day.replace("-", ""),
                    "margin_balance_change": margin_change,
                    "availability": "fresh",
                },
            }
        )
    replay_path.write_text(json.dumps(records), encoding="utf-8")
    artifact = build_rollout_artifact(
        replay_path=replay_path,
        replay_rows=replay_temperature_records(records),
        replay_start=records[0]["date"],
        replay_end=records[-1]["date"],
    )
    artifact.update(
        {
            "approved": True,
            "approved_by": "reviewer",
            "approved_at": "2026-07-25T12:00:00+08:00",
            "notes": "approved",
        }
    )
    artifact_path = tmp_path / "temperature_rollout.json"
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False),
        encoding="utf-8",
    )
    return replay_path, artifact_path


def test_daily_job_skips_cn_non_trading_day(monkeypatch, tmp_path):
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    def fail_price_collector(**kwargs):
        raise AssertionError("should not collect prices on a closed cn session")

    def fail_flow_collector():
        raise AssertionError("should not collect flows on a closed cn session")

    monkeypatch.setattr("lurker.cli.collect_price_snapshot_batch", fail_price_collector)
    monkeypatch.setattr("lurker.cli.collect_flow_snapshot", fail_flow_collector)
    monkeypatch.setattr("lurker.cli.build_notifier_from_env", lambda: FakeNotifier())

    message = daily_job(
        seed_pool_path=tmp_path / "missing_seed_pool.json",
        price_snapshot_dir=tmp_path / "price_snapshots",
        flow_snapshot_dir=tmp_path / "flow_snapshots",
        report_dir=tmp_path / "reports",
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        report_date="2026-06-19",
        strategy_names=["professional_flow_daily"],
        strategy_cadence="daily",
    )

    assert sends == []
    assert "Skipped daily job: cn market closed on 2026-06-19" in message
    assert not (tmp_path / "reports" / "2026-06-19.md").exists()


def test_parser_has_daily_job_command():
    parser = build_parser()

    args = parser.parse_args(["daily-job", "--markets", "cn", "--date", "2026-05-18"])

    assert args.command == "daily-job"
    assert args.markets == "cn"
    assert args.date == "2026-05-18"
    assert args.report_dir.name == "reports"


def test_parser_has_list_reports_command():
    parser = build_parser()

    args = parser.parse_args(["list-reports", "--limit", "3"])

    assert args.command == "list-reports"
    assert args.limit == 3


def test_parser_has_build_temperature_replay_command():
    args = build_parser().parse_args(
        [
            "build-temperature-replay",
            "--etf-start",
            "2026-03-26",
            "--margin-start",
            "2026-04-23",
            "--output-start",
            "2026-04-24",
            "--output-end",
            "2026-07-22",
        ]
    )

    assert args.command == "build-temperature-replay"
    assert args.output.name == "etf_60d_replay.json"
    assert args.artifact.name == "temperature_rollout.json"


def test_parser_has_approve_temperature_rollout_command():
    args = build_parser().parse_args(
        [
            "approve-temperature-rollout",
            "--approved-by",
            "codex-goal-2026-07-28",
        ]
    )

    assert args.command == "approve-temperature-rollout"
    assert args.approved_by == "codex-goal-2026-07-28"
    assert args.replay.name == "etf_60d_replay.json"
    assert args.artifact.name == "temperature_rollout.json"


def test_build_temperature_replay_writes_fixture_and_unapproved_artifact(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    output = Path("relative/etf_60d_replay.json")
    artifact = Path("relative/temperature_rollout.json")
    records = [
        {
            "date": f"2026-05-{day:02d}",
            "market_flow": {
                "trade_date": f"2026-05-{day:02d}",
                "main_net_inflow": 10.0,
                "super_large_net_inflow": 5.0,
                "availability": "fresh",
            },
            "core_etfs": {
                "configured_symbols": ["510300.SH"],
                "items": [
                    {
                        "symbol": "510300.SH",
                        "name": "沪深300ETF",
                        "trade_date": f"2026-05-{day:02d}",
                        "current_turnover": 130.0,
                        "avg_turnover_20d": 100.0,
                        "turnover_expansion": 1.3,
                        "shares": None,
                        "shares_date": None,
                        "status": "active",
                        "source": "fixture",
                        "availability": "turnover_only",
                        "error": None,
                    }
                ],
                "failures": [],
                "generated_at": "2026-07-25T00:00:00+00:00",
                "schema_version": 1,
            },
            "margin": {
                "trade_date": f"202605{day:02d}",
                "margin_balance_change": 1.0,
                "availability": "fresh",
            },
        }
        for day in range(1, 31)
    ] + [
        {
            "date": f"2026-06-{day:02d}",
            "market_flow": {
                "trade_date": f"2026-06-{day:02d}",
                "main_net_inflow": -10.0,
                "super_large_net_inflow": -5.0,
                "availability": "fresh",
            },
            "core_etfs": {
                "configured_symbols": ["510300.SH"],
                "items": [
                    {
                        "symbol": "510300.SH",
                        "name": "沪深300ETF",
                        "trade_date": f"2026-06-{day:02d}",
                        "current_turnover": 100.0,
                        "avg_turnover_20d": 100.0,
                        "turnover_expansion": 1.0,
                        "shares": None,
                        "shares_date": None,
                        "status": "inactive",
                        "source": "fixture",
                        "availability": "turnover_only",
                        "error": None,
                    }
                ],
                "failures": [],
                "generated_at": "2026-07-25T00:00:00+00:00",
                "schema_version": 1,
            },
            "margin": {
                "trade_date": f"202606{day:02d}",
                "margin_balance_change": -1.0,
                "availability": "fresh",
            },
        }
        for day in range(1, 31)
    ]

    message = build_temperature_replay(
        etf_start="2026-03-26",
        margin_start="2026-04-23",
        output_start="2026-05-01",
        output_end="2026-06-30",
        output_path=output,
        artifact_path=artifact,
        replay_collector=lambda **_: records,
    )

    assert len(json.loads(output.read_text(encoding="utf-8"))) == 60
    artifact_data = json.loads(artifact.read_text(encoding="utf-8"))
    assert artifact_data["approved"] is False
    assert artifact_data["distribution"] == {"进攻": 30, "观察": 0, "防守": 30}
    assert artifact_data["replay_path"] == str(output.resolve())
    assert "trading_days=60" in message
