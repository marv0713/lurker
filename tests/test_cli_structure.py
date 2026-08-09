from pathlib import Path


def test_cli_reexports_extracted_parser():
    from lurker.cli import build_parser as compatibility_parser
    from lurker.cli_parser import build_parser

    assert compatibility_parser is build_parser


def test_dispatch_uses_existing_cli_command_for_list_reports(
    monkeypatch,
    capsys,
    tmp_path: Path,
):
    from lurker.cli import build_parser
    from lurker.cli_dispatch import dispatch_command

    monkeypatch.setattr(
        "lurker.cli.list_reports",
        lambda **kwargs: "reports",
    )
    parser = build_parser()
    args = parser.parse_args(["list-reports", "--report-dir", str(tmp_path), "--limit", "1"])

    assert dispatch_command(parser, args) is True
    assert capsys.readouterr().out == "reports\n"


def test_dispatch_returns_false_for_demo_fallback():
    from lurker.cli import build_parser
    from lurker.cli_dispatch import dispatch_command

    parser = build_parser()
    args = parser.parse_args([])

    assert dispatch_command(parser, args) is False


def test_dispatch_personal_close_passes_every_argument(monkeypatch, tmp_path, capsys):
    from lurker import cli
    from lurker.cli_dispatch import dispatch_command
    from lurker.cli_parser import build_parser

    captured = {}

    def fake_personal_close(**kwargs):
        captured.update(kwargs)
        return "personal complete"

    monkeypatch.setattr(cli, "personal_close_report", fake_personal_close)
    parser = build_parser()
    args = parser.parse_args(
        [
            "personal-close-report",
            "--config",
            str(tmp_path / "scope.yaml"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--state-file",
            str(tmp_path / "state.json"),
            "--date",
            "2026-08-10",
            "--period",
            "2y",
            "--force-push",
        ]
    )

    assert dispatch_command(parser, args) is True
    assert captured == {
        "config_path": tmp_path / "scope.yaml",
        "report_dir": tmp_path / "reports",
        "state_file": tmp_path / "state.json",
        "report_date": "2026-08-10",
        "period": "2y",
        "no_push": False,
        "force_push": True,
    }
    assert capsys.readouterr().out.strip() == "personal complete"
