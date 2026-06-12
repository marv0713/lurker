import json

from lurker.reports.models import DailyReport
from lurker.cli import (
    build_data_snapshot,
    build_demo_report,
    build_notifier_from_env,
    build_run_daily,
    append_report_archive_index,
    build_strategy_report,
    daily_job,
    build_parser,
    list_reports,
    load_suppressed_symbols,
    read_api_key_file,
    parse_markets,
    refresh_flows,
    refresh_prices,
    resolve_seed_pool,
    weekly_report,
)


def test_build_demo_report_returns_markdown():
    report = build_demo_report(report_date="2026-05-17")

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
  "generated_at": "2026-05-17T12:00:00+00:00",
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
            "generated_at": "2026-05-17T12:00:00+00:00",
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
            "generated_at": "2026-05-17T12:00:00+00:00",
            "seed_pool_generated_at": "2026-05-16T12:00:00+00:00",
            "markets": ["cn"],
            "windows": [20, 60],
            "snapshots": [{"symbol": "300308.SZ", "market": "cn", "latest_close": 140.0}],
            "failures": [{"symbol": "000001.SZ", "market": "cn", "reason": "empty price data"}],
        }

    def fake_run_daily(**kwargs):
        assert kwargs["snapshot_batch"]["snapshots"][0]["symbol"] == "300308.SZ"
        assert kwargs["theme_mapping"] == {"300308.SZ": ["ai_infra"]}
        assert kwargs["report_date"] == "2026-05-17"
        assert kwargs["signal_threshold"] == 55
        assert kwargs["main_limit"] == 8
        assert kwargs["suppressed_symbols"] == {"300308.SZ"}
        return DailyReport(report_date="2026-05-17", main_candidates_count=1, content_md="# 大趋势雷达日报\n\n日报内容")

    monkeypatch.setattr("lurker.cli.collect_price_snapshot_batch", fake_collector)
    monkeypatch.setattr("lurker.cli.run_daily", fake_run_daily)
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
        report_date="2026-05-17",
        signal_threshold=55,
        main_limit=8,
        suppressed_symbols_path=suppressed_symbols_path,
    )

    assert (price_snapshot_dir / "2026-05-17.json").exists()
    report_path = report_dir / "2026-05-17.md"
    assert report_path.read_text(encoding="utf-8") == "# 大趋势雷达日报\n\n日报内容\n"
    assert "snapshots=1" in message
    assert "failures=1" in message
    assert str(report_path) in message
    candidates_path = report_dir / "2026-05-17.candidates.json"
    assert candidates_path.exists()
    assert "300308.SZ" in candidates_path.read_text(encoding="utf-8")
    index_path = report_dir / "index.json"
    assert index_path.exists()
    assert "2026-05-17" in index_path.read_text(encoding="utf-8")


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
            "generated_at": "2026-05-17T12:00:00+00:00",
            "seed_pool_generated_at": "2026-05-16T12:00:00+00:00",
            "markets": ["cn"],
            "windows": [20],
            "snapshots": [{"symbol": "300308.SZ", "market": "cn", "latest_close": 140.0}],
            "failures": [],
        }

    def fake_run_daily(**kwargs):
        assert kwargs["symbol_names"] == {"300308.SZ": "中际旭创"}
        return DailyReport(report_date="2026-05-17", main_candidates_count=1, content_md="# 大趋势雷达日报\n\n日报内容")

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
        report_date="2026-05-17",
        strategy_config_path=None,
    )

    history = json.loads((report_dir / "2026-05-17.candidates.json").read_text(encoding="utf-8"))
    assert history["observed_symbols"][0]["name"] == "中际旭创"


def test_append_report_archive_index_upserts_by_date(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report_path = report_dir / "2026-05-17.md"
    candidate_path = report_dir / "2026-05-17.candidates.json"

    append_report_archive_index(
        report_dir=report_dir,
        report_date="2026-05-17",
        report_path=report_path,
        candidates_path=candidate_path,
        snapshot_path=tmp_path / "snapshots" / "2026-05-17.json",
        strategies=["long_term_trend"],
        markets=["cn"],
        windows=[20, 60],
        snapshot_count=1,
        failure_count=0,
    )
    append_report_archive_index(
        report_dir=report_dir,
        report_date="2026-05-17",
        report_path=report_path,
        candidates_path=candidate_path,
        snapshot_path=tmp_path / "snapshots" / "2026-05-17.json",
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
            "generated_at": "2026-05-17T12:00:00+00:00",
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
        snapshot_date="2026-05-17",
    )

    assert "Wrote price snapshot" in message
    assert "snapshots=1" in message
    assert "failures=0" in message
    assert (output_dir / "2026-05-17.json").exists()


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
    (snapshot_dir / "2026-05-17.json").write_text(
        """
{
  "generated_at": "2026-05-17T12:00:00+00:00",
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

    args = parser.parse_args(["refresh-prices", "--markets", "cn", "--date", "2026-05-17"])

    assert args.command == "refresh-prices"
    assert args.markets == "cn"
    assert args.date == "2026-05-17"


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
        report_date="2026-06-07",
        push=True,
        db_path=None,
    )

    assert sends
    assert "职业资金雷达周报" in sends[0][1]
    assert "Pushed weekly report successfully" in message


def test_build_notifier_from_env_can_build_composite(monkeypatch):
    monkeypatch.setenv("PUSHPLUS_TOKEN", "push-token")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "from@example.com")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")

    notifier = build_notifier_from_env()

    assert type(notifier).__name__ == "CompositeNotifier"


def test_daily_job_pushes_professional_report_when_stock_flows_are_empty(monkeypatch, tmp_path):
    seed_pool_path = tmp_path / "resolved_seed_pool.json"
    seed_pool_path.write_text(
        """
{
  "generated_at": "2026-06-06T00:00:00+00:00",
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
            "generated_at": "2026-06-06T00:00:00+00:00",
            "markets": ["cn"],
            "windows": [20],
            "snapshots": [{"symbol": "300308.SZ", "market": "cn", "return_20d": 0.1}],
            "failures": [],
        }

    def fake_flow_collector():
        return {
            "schema_version": 1,
            "generated_at": "2026-06-06T00:00:00+00:00",
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
        report_date="2026-06-06",
        strategy_config_path=strategy_config,
        strategy_cadence="daily",
    )

    assert sends
    assert "Pushed report successfully" in message


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
    )

    assert sends
    assert "stock_flows" in sends[0][1]
    assert "Pushed report successfully" in message


def test_parser_has_daily_job_command():
    parser = build_parser()

    args = parser.parse_args(["daily-job", "--markets", "cn", "--date", "2026-05-17"])

    assert args.command == "daily-job"
    assert args.markets == "cn"
    assert args.date == "2026-05-17"
    assert args.report_dir.name == "reports"


def test_parser_has_list_reports_command():
    parser = build_parser()

    args = parser.parse_args(["list-reports", "--limit", "3"])

    assert args.command == "list-reports"
    assert args.limit == 3
