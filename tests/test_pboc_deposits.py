from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
import xlwt

from lurker.config import MonthlyMacroConfig
from lurker.ingest.pboc_deposits import (
    PbocSchemaError,
    PbocSourceError,
    RawHttpResponse,
    collect_pboc_deposits,
    merge_deposit_tables,
    parse_pboc_credit_table,
)


def _credit_rows(unit: str = "单位：亿元") -> list[list[object]]:
    return [
        ["金融机构人民币信贷收支表", None, None],
        [unit, None, None],
        ["项目 Item", "2025.01", "2025.02"],
        ["1.住户存款 Deposits of Households", 1567675.44, 1573761.53],
        [
            "5.非银行业金融机构存款 "
            "Deposits of Non-banking Financial Institutions",
            270772.45,
            299057.93,
        ],
    ]


def _html_payload(rows: list[list[object]]) -> bytes:
    return pd.DataFrame(rows).to_html(index=False, header=False).encode("utf-8")


def _xlsx_payload(rows: list[list[object]]) -> bytes:
    output = BytesIO()
    pd.DataFrame(rows).to_excel(
        output,
        index=False,
        header=False,
        engine="openpyxl",
    )
    return output.getvalue()


def _xls_payload(rows: list[list[object]]) -> bytes:
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("table")
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if value is not None:
                sheet.write(row_index, column_index, value)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


@pytest.mark.parametrize(
    ("content_type", "payload_builder"),
    [
        ("text/html; charset=utf-8", _html_payload),
        ("application/vnd.ms-excel", _xls_payload),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _xlsx_payload,
        ),
    ],
)
def test_parse_pboc_credit_table_extracts_only_direct_balances(
    content_type,
    payload_builder,
):
    result = parse_pboc_credit_table(
        payload_builder(_credit_rows()),
        content_type=content_type,
        source_url="https://www.pbc.gov.cn/table",
    )

    assert result["household"] == {
        "2025-01": 1567675.44,
        "2025-02": 1573761.53,
    }
    assert result["nonbank"] == {
        "2025-01": 270772.45,
        "2025-02": 299057.93,
    }


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                ["单位：亿元", None],
                ["项目 Item", "2025.01"],
                ["新增储蓄存款", 1567675.44],
                ["新增其他存款", 270772.45],
            ],
            "住户存款",
        ),
        (_credit_rows("单位：万元"), "100-million-yuan"),
        (_credit_rows() + [_credit_rows()[3]], "multiple 住户存款"),
        (
            [
                ["单位：亿元", None],
                ["项目 Item", "2025.01"],
                ["1.住户存款 Deposits of Households", float("inf")],
                [
                    "5.非银行业金融机构存款 "
                    "Deposits of Non-banking Financial Institutions",
                    1.0,
                ],
            ],
            "finite positive",
        ),
    ],
)
def test_parse_pboc_credit_table_fails_closed(rows, message):
    with pytest.raises(PbocSchemaError, match=message):
        parse_pboc_credit_table(
            _html_payload(rows),
            content_type="text/html",
            source_url="https://www.pbc.gov.cn/table",
        )


def test_parse_pboc_credit_table_rejects_pdf():
    with pytest.raises(PbocSchemaError, match="unsupported content type"):
        parse_pboc_credit_table(
            b"%PDF-1.7",
            content_type="application/pdf",
            source_url="https://www.pbc.gov.cn/table.pdf",
        )


def test_parse_pboc_credit_table_skips_unpublished_empty_month():
    rows = _credit_rows()
    rows[3][2] = None
    rows[4][2] = None
    result = parse_pboc_credit_table(
        _html_payload(rows),
        content_type="text/html",
        source_url="https://www.pbc.gov.cn/table",
    )
    assert result["household"] == {"2025-01": 1567675.44}
    assert result["nonbank"] == {"2025-01": 270772.45}


def _config() -> MonthlyMacroConfig:
    return MonthlyMacroConfig(
        credit_table_urls={
            2024: "https://www.pbc.gov.cn/2024.htm",
            2025: "https://www.pbc.gov.cn/2025.htm",
        },
        allowed_hosts=("www.pbc.gov.cn",),
        timeout_seconds=30,
        max_response_bytes=1_000_000,
        household_deposit_yoy_pct=12.0,
        leverage_ratio_pct=4.0,
        financing_monthly_growth_pct=20.0,
        macro_max_lag_months=2,
        leverage_max_lag_trading_days=3,
    )


def test_collect_pboc_deposits_caches_bytes_and_hashes_sources(tmp_path):
    payloads = {
        "https://www.pbc.gov.cn/2024.htm": _html_payload(
            [
                ["单位：亿元", None],
                ["项目 Item", "2024.01"],
                ["1.住户存款 Deposits of Households", 100.0],
                [
                    "5.非银行业金融机构存款 "
                    "Deposits of Non-banking Financial Institutions",
                    20.0,
                ],
            ]
        ),
        "https://www.pbc.gov.cn/2025.htm": _html_payload(_credit_rows()),
    }

    result = collect_pboc_deposits(
        _config(),
        raw_dir=tmp_path,
        fetcher=lambda url, timeout, max_bytes: RawHttpResponse(
            status_code=200,
            content_type="text/html; charset=utf-8",
            body=payloads[url],
        ),
        now_iso=lambda: "2026-07-26T12:00:00+00:00",
    )

    assert result["balances"]["household"]["2024-01"] == 100.0
    assert result["balances"]["nonbank"]["2025-01"] == 270772.45
    assert result["failures"] == []
    assert len(result["sources"]) == 2
    for source in result["sources"]:
        assert source["status_code"] == 200
        assert source["sha256"].startswith("sha256:")
        assert source["data_date"] in {"2024-01", "2025-02"}
        assert Path(source["cache_path"]).exists()


def test_collect_pboc_deposits_rejects_oversized_response(tmp_path):
    config = replace(_config(), max_response_bytes=3)
    with pytest.raises(PbocSourceError, match="exceeds max_response_bytes"):
        collect_pboc_deposits(
            config,
            raw_dir=tmp_path,
            fetcher=lambda url, timeout, max_bytes: RawHttpResponse(
                status_code=200,
                content_type="text/html",
                body=b"1234",
            ),
        )


def test_collect_pboc_deposits_rejects_non_success_status(tmp_path):
    with pytest.raises(PbocSourceError, match="HTTP status 503"):
        collect_pboc_deposits(
            _config(),
            raw_dir=tmp_path,
            fetcher=lambda url, timeout, max_bytes: RawHttpResponse(
                status_code=503,
                content_type="text/html",
                body=b"unavailable",
            ),
        )


def test_merge_deposit_tables_marks_only_conflicting_metric_month_unknown():
    result = merge_deposit_tables(
        [
            {
                "household": {"2025-01": 100.0},
                "nonbank": {"2025-01": 20.0},
            },
            {
                "household": {"2025-01": 101.0},
                "nonbank": {"2025-01": 20.0},
            },
        ]
    )
    assert "2025-01" not in result["balances"]["household"]
    assert result["balances"]["nonbank"]["2025-01"] == 20.0
    assert result["failures"] == [
        "conflicting revision for household 2025-01: 100.0 != 101.0"
    ]
