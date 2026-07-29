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
    args = parser.parse_args(
        ["list-reports", "--report-dir", str(tmp_path), "--limit", "1"]
    )

    assert dispatch_command(parser, args) is True
    assert capsys.readouterr().out == "reports\n"


def test_dispatch_returns_false_for_demo_fallback():
    from lurker.cli import build_parser
    from lurker.cli_dispatch import dispatch_command

    parser = build_parser()
    args = parser.parse_args([])

    assert dispatch_command(parser, args) is False
