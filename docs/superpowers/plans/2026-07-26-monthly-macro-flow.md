# Monthly Macro Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立的 `monthly_macro_flow` 月度策略，用央行居民/非银存款直接余额、M1-M2、沪深融资余额和交易所 A 股流通市值回答“存款搬家与杠杆水位是否支持牛市进程”。

**Architecture:** ingest 层分别负责央行表、货币供应、两融和交易所市值的严格规范化，再生成带来源、截止日期和失败原因的月度快照；`application/monthly_macro_flow.py` 是无 I/O 的状态机；报告层只渲染。独立 CLI 原子覆盖同月快照和报告，只在形成四档状态时使用日报接收人推送。

**Tech Stack:** Python 3.11、pandas、requests、AkShare、PyYAML、openpyxl、xlrd、pytest、ruff。

---

## 实施约束

1. 居民和非银存款只读取央行《金融机构人民币信贷收支表》的直接余额。
2. M1-M2 是独立维度，不得补齐非银存款。
3. 流通市值只汇总上交所主板 A/科创板和深交所主板 A/创业板 A。
4. 融资余额与流通市值必须是同一交易日；沪深融资余额也必须同日。
5. 已确认过热证据可以单独触发 `过热警报`；未触发红线但另一条杠杆指标缺失时，杠杆仍为 `unknown`。
6. 非过热结论要求居民、非银、M1-M2、杠杆四个维度全部有效。
7. 外部源错误降级 `unknown`；配置、类型、序列化、原子写入和渲染错误向上抛出。
8. CI 不访问网络。真实验收使用历史月份 `2025-01`，避免依赖尚未配置的 2026 央行年度 URL。

## 文件结构

| 文件 | 职责 |
|---|---|
| `configs/macro_monthly.yaml` | 央行 URL、host 白名单、阈值和新鲜度 |
| `src/lurker/config.py` | 严格加载 `MonthlyMacroConfig` |
| `src/lurker/ingest/pboc_deposits.py` | 安全下载、缓存、解析和合并央行余额表 |
| `src/lurker/ingest/macro_monthly.py` | M1/M2、两融、市值、日期对齐和快照存储 |
| `src/lurker/application/monthly_macro_flow.py` | 四维分类与综合状态 |
| `src/lurker/reports/monthly_macro_flow_report.py` | 独立 Markdown 月报 |
| `src/lurker/application/strategy_runner.py` | 注册 `monthly_macro_flow` |
| `src/lurker/cli.py` | 独立 job、parser、原子报告落盘和推送 |
| `tests/test_config.py` | 月报配置契约 |
| `tests/test_pboc_deposits.py` | 央行 HTML/XLS/XLSX、下载和缓存 |
| `tests/test_macro_monthly_ingest.py` | M1/M2、两融、市值、月份与日期对齐 |
| `tests/test_monthly_macro_flow.py` | 领域真值表 |
| `tests/test_monthly_macro_flow_report.py` | 报告与数据质量 |
| `tests/test_strategy_runner.py` | 策略注册 |
| `tests/test_cli.py` | 独立命令、幂等和通知边界 |

---

### Task 1: 严格月报配置

**Files:**
- Create: `configs/macro_monthly.yaml`
- Modify: `src/lurker/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: 写配置 RED 测试**

在 `tests/test_config.py` 增加：

```python
from pathlib import Path

import pytest

from lurker.config import MonthlyMacroConfig, load_monthly_macro_config


def _monthly_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "macro_monthly.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_monthly_macro_config_is_strict_and_typed(tmp_path):
    path = _monthly_yaml(
        tmp_path,
        """
schema_version: 1
pboc:
  credit_table_urls:
    "2024": "https://www.pbc.gov.cn/2024.htm"
    "2025": "https://www.pbc.gov.cn/2025.htm"
  allowed_hosts: [www.pbc.gov.cn]
  timeout_seconds: 30
  max_response_bytes: 10000000
thresholds:
  household_deposit_yoy_pct: 12
  leverage_ratio_pct: 4
  financing_monthly_growth_pct: 20
freshness:
  macro_max_lag_months: 2
  leverage_max_lag_trading_days: 3
""",
    )

    config = load_monthly_macro_config(path)

    assert config == MonthlyMacroConfig(
        credit_table_urls={
            2024: "https://www.pbc.gov.cn/2024.htm",
            2025: "https://www.pbc.gov.cn/2025.htm",
        },
        allowed_hosts=("www.pbc.gov.cn",),
        timeout_seconds=30,
        max_response_bytes=10_000_000,
        household_deposit_yoy_pct=12.0,
        leverage_ratio_pct=4.0,
        financing_monthly_growth_pct=20.0,
        macro_max_lag_months=2,
        leverage_max_lag_trading_days=3,
    )


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("https://www.pbc.gov.cn/2025.htm", "https"),
        ("https://evil.example/2025.htm", "allowed_hosts"),
        ("2025", "four-digit year"),
        ("false", "timeout_seconds"),
        ("-1", "macro_max_lag_months"),
    ],
)
def test_monthly_macro_config_rejects_invalid_values(tmp_path, replacement, message):
    base = """
schema_version: 1
pboc:
  credit_table_urls:
    YEAR_KEY: URL_VALUE
  allowed_hosts: [www.pbc.gov.cn]
  timeout_seconds: TIMEOUT
  max_response_bytes: 10000000
thresholds:
  household_deposit_yoy_pct: 12
  leverage_ratio_pct: 4
  financing_monthly_growth_pct: 20
freshness:
  macro_max_lag_months: LAG
  leverage_max_lag_trading_days: 3
"""
    if message == "https":
        text = base.replace("YEAR_KEY", '"2025"').replace(
            "URL_VALUE", '"http://www.pbc.gov.cn/2025.htm"'
        ).replace("TIMEOUT", "30").replace("LAG", "2")
    elif message == "allowed_hosts":
        text = base.replace("YEAR_KEY", '"2025"').replace(
            "URL_VALUE", f'"{replacement}"'
        ).replace("TIMEOUT", "30").replace("LAG", "2")
    elif message == "four-digit year":
        text = base.replace("YEAR_KEY", '"25"').replace(
            "URL_VALUE", '"https://www.pbc.gov.cn/2025.htm"'
        ).replace("TIMEOUT", "30").replace("LAG", "2")
    elif message == "timeout_seconds":
        text = base.replace("YEAR_KEY", '"2025"').replace(
            "URL_VALUE", '"https://www.pbc.gov.cn/2025.htm"'
        ).replace("TIMEOUT", replacement).replace("LAG", "2")
    else:
        text = base.replace("YEAR_KEY", '"2025"').replace(
            "URL_VALUE", '"https://www.pbc.gov.cn/2025.htm"'
        ).replace("TIMEOUT", "30").replace("LAG", replacement)
    with pytest.raises(ValueError, match=message):
        load_monthly_macro_config(_monthly_yaml(tmp_path, text))


def test_monthly_macro_config_rejects_unknown_fields(tmp_path):
    path = _monthly_yaml(
        tmp_path,
        """
schema_version: 1
unknown: true
pboc:
  credit_table_urls:
    "2025": "https://www.pbc.gov.cn/2025.htm"
  allowed_hosts: [www.pbc.gov.cn]
  timeout_seconds: 30
  max_response_bytes: 10000000
thresholds:
  household_deposit_yoy_pct: 12
  leverage_ratio_pct: 4
  financing_monthly_growth_pct: 20
freshness:
  macro_max_lag_months: 2
  leverage_max_lag_trading_days: 3
""",
    )
    with pytest.raises(ValueError, match="unknown monthly macro top-level field"):
        load_monthly_macro_config(path)
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py -k monthly_macro -v
```

Expected: collection fails because `MonthlyMacroConfig` and
`load_monthly_macro_config` do not exist.

- [ ] **Step 3: 实现 dataclass 和严格 loader**

在 `src/lurker/config.py` 增加：

```python
from urllib.parse import urlparse


@dataclass(frozen=True)
class MonthlyMacroConfig:
    credit_table_urls: dict[int, str]
    allowed_hosts: tuple[str, ...]
    timeout_seconds: int
    max_response_bytes: int
    household_deposit_yoy_pct: float
    leverage_ratio_pct: float
    financing_monthly_growth_pct: float
    macro_max_lag_months: int
    leverage_max_lag_trading_days: int


def _non_negative_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite and non-negative")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite and non-negative") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return result


def _integer(value: Any, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def load_monthly_macro_config(path: str | Path) -> MonthlyMacroConfig:
    data = load_yaml(path)
    _reject_unknown_fields(
        data,
        {"schema_version", "pboc", "thresholds", "freshness"},
        "monthly macro top-level",
    )
    if data.get("schema_version") != 1:
        raise ValueError("macro_monthly schema_version must equal 1")

    pboc = _mapping(data.get("pboc"), "monthly macro pboc")
    _reject_unknown_fields(
        pboc,
        {
            "credit_table_urls",
            "allowed_hosts",
            "timeout_seconds",
            "max_response_bytes",
        },
        "monthly macro pboc",
    )
    hosts = pboc.get("allowed_hosts")
    if not isinstance(hosts, list) or not hosts:
        raise ValueError("allowed_hosts must be a non-empty list")
    allowed_hosts = tuple(str(host).strip().lower() for host in hosts)
    if any(not host or "/" in host for host in allowed_hosts):
        raise ValueError("allowed_hosts contains an invalid host")

    raw_urls = _mapping(pboc.get("credit_table_urls"), "credit_table_urls")
    if not raw_urls:
        raise ValueError("credit_table_urls must be non-empty")
    urls: dict[int, str] = {}
    for raw_year, raw_url in raw_urls.items():
        year_text = str(raw_year)
        if len(year_text) != 4 or not year_text.isdigit():
            raise ValueError("credit_table_urls key must be a four-digit year")
        url = str(raw_url).strip()
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("credit table URL must use https")
        if parsed.hostname not in allowed_hosts:
            raise ValueError("credit table URL host is not in allowed_hosts")
        urls[int(year_text)] = url

    thresholds = _mapping(data.get("thresholds"), "monthly macro thresholds")
    _reject_unknown_fields(
        thresholds,
        {
            "household_deposit_yoy_pct",
            "leverage_ratio_pct",
            "financing_monthly_growth_pct",
        },
        "monthly macro threshold",
    )
    freshness = _mapping(data.get("freshness"), "monthly macro freshness")
    _reject_unknown_fields(
        freshness,
        {"macro_max_lag_months", "leverage_max_lag_trading_days"},
        "monthly macro freshness",
    )
    return MonthlyMacroConfig(
        credit_table_urls=urls,
        allowed_hosts=allowed_hosts,
        timeout_seconds=_integer(
            pboc.get("timeout_seconds"), "timeout_seconds", minimum=1
        ),
        max_response_bytes=_integer(
            pboc.get("max_response_bytes"), "max_response_bytes", minimum=1
        ),
        household_deposit_yoy_pct=_non_negative_float(
            thresholds.get("household_deposit_yoy_pct"),
            "household_deposit_yoy_pct",
        ),
        leverage_ratio_pct=_non_negative_float(
            thresholds.get("leverage_ratio_pct"), "leverage_ratio_pct"
        ),
        financing_monthly_growth_pct=_non_negative_float(
            thresholds.get("financing_monthly_growth_pct"),
            "financing_monthly_growth_pct",
        ),
        macro_max_lag_months=_integer(
            freshness.get("macro_max_lag_months"),
            "macro_max_lag_months",
            minimum=0,
        ),
        leverage_max_lag_trading_days=_integer(
            freshness.get("leverage_max_lag_trading_days"),
            "leverage_max_lag_trading_days",
            minimum=0,
        ),
    )
```

创建 `configs/macro_monthly.yaml`，内容使用已验证的两个官方 URL：

```yaml
schema_version: 1
pboc:
  credit_table_urls:
    "2024": "https://www.pbc.gov.cn/eportal/fileDir/diaochatongjisi/resource/cms/2025/01/2025011417071510290.htm"
    "2025": "https://www.pbc.gov.cn/eportal/fileDir/diaochatongjisi/resource/cms/2025/02/2025021418100389332.htm"
  allowed_hosts:
    - www.pbc.gov.cn
  timeout_seconds: 30
  max_response_bytes: 10000000
thresholds:
  household_deposit_yoy_pct: 12.0
  leverage_ratio_pct: 4.0
  financing_monthly_growth_pct: 20.0
freshness:
  macro_max_lag_months: 2
  leverage_max_lag_trading_days: 3
```

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py -k monthly_macro -v
.venv/bin/ruff check src/lurker/config.py tests/test_config.py
git add configs/macro_monthly.yaml src/lurker/config.py tests/test_config.py
git commit -m "feat: add strict monthly macro configuration"
```

---

### Task 2: 央行 HTML/XLS/XLSX 严格解析

**Files:**
- Modify: `pyproject.toml`
- Create: `src/lurker/ingest/pboc_deposits.py`
- Create: `tests/test_pboc_deposits.py`

- [ ] **Step 1: 声明解析依赖**

在 `pyproject.toml` 的 dependencies 增加：

```toml
  "lxml>=5.0.0",
  "openpyxl>=3.1.0",
  "xlrd>=2.0.1",
```

在 `project.optional-dependencies.dev` 增加用于构造固定 XLS 测试输入的：

```toml
  "xlwt>=1.3.0",
```

运行：

```bash
.venv/bin/pip install -e '.[dev]'
```

Expected: install succeeds and imports `lxml`, `openpyxl`, `xlrd`.

- [ ] **Step 2: 写 parser RED**

创建 `tests/test_pboc_deposits.py`：

```python
from io import BytesIO

import pandas as pd
import pytest
import xlwt

from lurker.ingest.pboc_deposits import PbocSchemaError, parse_pboc_credit_table


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
    pd.DataFrame(rows).to_excel(output, index=False, header=False, engine="openpyxl")
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
        (_credit_rows("单位：万元"), "亿元"),
        (_credit_rows() + [_credit_rows()[3]], "multiple household"),
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
```

- [ ] **Step 3: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_pboc_deposits.py -k parse -v
```

Expected: module import fails.

- [ ] **Step 4: 实现严格 parser**

创建 `src/lurker/ingest/pboc_deposits.py`，包含：

```python
from __future__ import annotations

import math
import re
from io import BytesIO
from typing import Any

import pandas as pd


class PbocSourceError(RuntimeError):
    pass


class PbocSchemaError(PbocSourceError):
    pass


_MONTH = re.compile(r"^(20\d{2})[.\-/年](0[1-9]|1[0-2])(?:月份?)?$")
_HOUSEHOLD = re.compile(
    r"^(?:\d+[.、])?住户存款DepositsofHouseholds$",
    re.IGNORECASE,
)
_NONBANK = re.compile(
    r"^(?:\d+[.、])?非银行业金融机构存款"
    r"DepositsofNon-bankingFinancialInstitutions$",
    re.IGNORECASE,
)


def _compact(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", "", str(value)).replace("（", "(").replace("）", ")")


def _tables(payload: bytes, content_type: str) -> list[pd.DataFrame]:
    normalized = content_type.split(";", 1)[0].strip().lower()
    try:
        if normalized in {"text/html", "application/xhtml+xml"}:
            return pd.read_html(BytesIO(payload), header=None)
        if normalized in {
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/octet-stream",
        }:
            return [pd.read_excel(BytesIO(payload), header=None)]
    except (ValueError, ImportError, OSError) as exc:
        raise PbocSchemaError(f"cannot parse PBOC table: {exc}") from exc
    raise PbocSchemaError(f"unsupported content type: {content_type}")


def _target_row(table: pd.DataFrame, pattern: re.Pattern[str], label: str) -> int:
    matches = [
        int(index)
        for index, value in table.iloc[:, 0].items()
        if pattern.fullmatch(_compact(value))
    ]
    if len(matches) != 1:
        qualifier = "multiple " if len(matches) > 1 else ""
        raise PbocSchemaError(f"{qualifier}{label} row")
    return matches[0]


def parse_pboc_credit_table(
    payload: bytes,
    *,
    content_type: str,
    source_url: str,
) -> dict[str, dict[str, float]]:
    candidates: list[pd.DataFrame] = []
    for table in _tables(payload, content_type):
        compact_cells = {_compact(value) for value in table.to_numpy().ravel()}
        has_unit = any(
            "单位:亿元" in value
            or "单位：亿元" in value
            or "Unit:100MillionYuan" in value
            for value in compact_cells
        )
        if has_unit:
            candidates.append(table)
    if len(candidates) != 1:
        raise PbocSchemaError(
            f"expected one RMB 100-million-yuan table, got {len(candidates)}"
        )
    table = candidates[0]
    household_row = _target_row(table, _HOUSEHOLD, "住户存款")
    nonbank_row = _target_row(table, _NONBANK, "非银行业金融机构存款")

    month_columns: dict[int, str] = {}
    for row_index in range(len(table)):
        found: dict[int, str] = {}
        for column_index, value in enumerate(table.iloc[row_index].tolist()):
            match = _MONTH.fullmatch(_compact(value))
            if match:
                found[column_index] = f"{match.group(1)}-{match.group(2)}"
        if len(found) >= 1:
            if month_columns:
                raise PbocSchemaError("multiple month header rows")
            month_columns = found
    if not month_columns:
        raise PbocSchemaError("month header row is missing")

    result = {"household": {}, "nonbank": {}}
    for name, row_index in (
        ("household", household_row),
        ("nonbank", nonbank_row),
    ):
        for column_index, month in month_columns.items():
            value = table.iat[row_index, column_index]
            if pd.isna(value) or str(value).strip() == "":
                continue
            try:
                number = float(str(value).replace(",", ""))
            except (TypeError, ValueError) as exc:
                raise PbocSchemaError(
                    f"{name} {month} must be finite positive"
                ) from exc
            if not math.isfinite(number) or number <= 0:
                raise PbocSchemaError(f"{name} {month} must be finite positive")
            result[name][month] = number
    if not result["household"] or not result["nonbank"]:
        raise PbocSchemaError(f"no published deposit values in {source_url}")
    return result
```

- [ ] **Step 5: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_pboc_deposits.py -k parse -v
.venv/bin/ruff check src/lurker/ingest/pboc_deposits.py tests/test_pboc_deposits.py
git add pyproject.toml src/lurker/ingest/pboc_deposits.py tests/test_pboc_deposits.py
git commit -m "feat: parse official PBOC deposit tables"
```

---

### Task 3: 安全下载、原始缓存和跨年度合并

**Files:**
- Modify: `src/lurker/ingest/pboc_deposits.py`
- Modify: `tests/test_pboc_deposits.py`

- [ ] **Step 1: 写 transport/cache RED**

在 `tests/test_pboc_deposits.py` 增加：

```python
from dataclasses import replace
from pathlib import Path

from lurker.config import MonthlyMacroConfig
from lurker.ingest.pboc_deposits import (
    RawHttpResponse,
    collect_pboc_deposits,
    merge_deposit_tables,
)


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
            {"household": {"2025-01": 100.0}, "nonbank": {"2025-01": 20.0}},
            {"household": {"2025-01": 101.0}, "nonbank": {"2025-01": 20.0}},
        ]
    )
    assert "2025-01" not in result["balances"]["household"]
    assert result["balances"]["nonbank"]["2025-01"] == 20.0
    assert result["failures"] == [
        "conflicting revision for household 2025-01: 100.0 != 101.0"
    ]
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_pboc_deposits.py -k "collect or merge" -v
```

Expected: missing symbols.

- [ ] **Step 3: 实现安全 transport 和原子缓存**

在 `src/lurker/ingest/pboc_deposits.py` 增加：

```python
import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from lurker.config import MonthlyMacroConfig


@dataclass(frozen=True)
class RawHttpResponse:
    status_code: int
    content_type: str
    body: bytes


HttpFetcher = Callable[[str, int, int], RawHttpResponse]


def _requests_fetch(url: str, timeout: int, max_bytes: int) -> RawHttpResponse:
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        response.raise_for_status()
        declared = response.headers.get("content-length")
        if declared:
            try:
                declared_size = int(declared)
            except ValueError as exc:
                raise PbocSourceError("invalid PBOC content-length") from exc
            if declared_size > max_bytes:
                raise PbocSourceError("PBOC response exceeds max_response_bytes")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise PbocSourceError("PBOC response exceeds max_response_bytes")
            chunks.append(chunk)
    except requests.RequestException as exc:
        raise PbocSourceError(f"PBOC request failed: {exc}") from exc
    return RawHttpResponse(
        status_code=response.status_code,
        content_type=response.headers.get("content-type", ""),
        body=b"".join(chunks),
    )


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def merge_deposit_tables(
    tables: list[dict[str, dict[str, float]]],
) -> dict[str, object]:
    merged: dict[str, dict[str, float]] = {"household": {}, "nonbank": {}}
    conflicts: set[tuple[str, str]] = set()
    failures: list[str] = []
    for table in tables:
        for name in ("household", "nonbank"):
            for month, value in table[name].items():
                if (name, month) in conflicts:
                    continue
                previous = merged[name].get(month)
                if previous is not None and previous != value:
                    failures.append(
                        f"conflicting revision for {name} {month}: "
                        f"{previous} != {value}"
                    )
                    conflicts.add((name, month))
                    del merged[name][month]
                    continue
                merged[name][month] = value
    return {"balances": merged, "failures": failures}


def _extension(content_type: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in {"text/html", "application/xhtml+xml"}:
        return "html"
    if normalized == "application/vnd.ms-excel":
        return "xls"
    if normalized in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    }:
        return "xlsx"
    raise PbocSchemaError(f"unsupported content type: {content_type}")


def collect_pboc_deposits(
    config: MonthlyMacroConfig,
    *,
    raw_dir: str | Path,
    fetcher: HttpFetcher = _requests_fetch,
    now_iso: Callable[[], str] | None = None,
) -> dict[str, object]:
    clock = now_iso or (
        lambda: pd.Timestamp.now(tz="UTC").isoformat()
    )
    tables: list[dict[str, dict[str, float]]] = []
    sources: list[dict[str, object]] = []
    for year, url in sorted(config.credit_table_urls.items()):
        response = fetcher(
            url,
            config.timeout_seconds,
            config.max_response_bytes,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise PbocSourceError(f"PBOC HTTP status {response.status_code}")
        if len(response.body) > config.max_response_bytes:
            raise PbocSourceError("PBOC response exceeds max_response_bytes")
        digest = hashlib.sha256(response.body).hexdigest()
        path = Path(raw_dir) / f"{year}-{digest}.{_extension(response.content_type)}"
        if not path.exists():
            _atomic_write_bytes(path, response.body)
        parsed = parse_pboc_credit_table(
            response.body,
            content_type=response.content_type,
            source_url=url,
        )
        unexpected = {
            month for values in parsed.values() for month in values
            if not month.startswith(f"{year}-")
        }
        if unexpected:
            raise PbocSchemaError(
                f"configured year {year} contains month {sorted(unexpected)[0]}"
            )
        tables.append(parsed)
        sources.append(
            {
                "year": year,
                "url": url,
                "data_date": max(
                    month
                    for values in parsed.values()
                    for month in values
                ),
                "retrieved_at": clock(),
                "status_code": response.status_code,
                "content_type": response.content_type,
                "sha256": f"sha256:{digest}",
                "cache_path": str(path.resolve()),
            }
        )
    merged = merge_deposit_tables(tables)
    return {
        "balances": merged["balances"],
        "sources": sources,
        "failures": merged["failures"],
    }
```

TLS verification remains enabled. For a local trust chain, use the standard
`REQUESTS_CA_BUNDLE` environment variable; do not add `verify=False`.

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_pboc_deposits.py -v
.venv/bin/ruff check src/lurker/ingest/pboc_deposits.py tests/test_pboc_deposits.py
git add src/lurker/ingest/pboc_deposits.py tests/test_pboc_deposits.py
git commit -m "feat: cache and merge official PBOC deposit data"
```

---

### Task 4: M1-M2 规范化与宏观共同月份

**Files:**
- Create: `src/lurker/ingest/macro_monthly.py`
- Create: `tests/test_macro_monthly_ingest.py`

- [ ] **Step 1: 写 money/alignment RED**

创建 `tests/test_macro_monthly_ingest.py`：

```python
import pandas as pd

from lurker.ingest.macro_monthly import (
    MonthlySchemaError,
    build_macro_facts,
    normalize_money_supply,
    select_common_macro_month,
)


def test_normalize_money_supply_uses_yoy_columns_only():
    frame = pd.DataFrame(
        [
            {
                "月份": "2025年01月份",
                "货币和准货币(M2)-同比增长": 7.0,
                "货币(M1)-同比增长": 0.4,
            },
            {
                "月份": "2024年12月份",
                "货币和准货币(M2)-同比增长": 7.3,
                "货币(M1)-同比增长": -1.4,
            },
        ]
    )
    assert normalize_money_supply(frame) == {
        "2025-01": {"m1_yoy_pct": 0.4, "m2_yoy_pct": 7.0},
        "2024-12": {"m1_yoy_pct": -1.4, "m2_yoy_pct": 7.3},
    }


def test_select_common_macro_month_uses_latest_non_future_month():
    deposits = {
        "household": {"2024-12": 90.0, "2025-01": 100.0, "2025-02": 101.0},
        "nonbank": {"2024-12": 20.0, "2025-01": 21.0, "2025-02": 22.0},
    }
    money = {
        "2024-12": {"m1_yoy_pct": -1.4, "m2_yoy_pct": 7.3},
        "2025-01": {"m1_yoy_pct": 0.4, "m2_yoy_pct": 7.0},
    }
    assert select_common_macro_month(
        deposits,
        money,
        report_month="2025-02",
        max_lag_months=2,
    ) == "2025-01"


def test_build_macro_facts_requires_all_four_household_points():
    deposits = {
        "household": {
            "2023-12": 80.0,
            "2024-01": 85.0,
            "2024-12": 90.0,
            "2025-01": 100.0,
        },
        "nonbank": {"2024-12": 20.0, "2025-01": 21.0},
    }
    money = {
        "2024-12": {"m1_yoy_pct": -1.4, "m2_yoy_pct": 7.3},
        "2025-01": {"m1_yoy_pct": 0.4, "m2_yoy_pct": 7.0},
    }
    facts = build_macro_facts(
        deposits,
        money,
        report_month="2025-01",
        max_lag_months=2,
    )
    assert facts["macro_month"] == "2025-01"
    assert facts["household"]["current"] == 100.0
    assert facts["household"]["previous_month"] == 90.0
    assert facts["household"]["previous_year"] == 85.0
    assert facts["household"]["previous_year_previous_month"] == 80.0
    assert facts["money_supply"]["current_m1_yoy_pct"] == 0.4
```

```python
def test_normalize_money_supply_rejects_missing_column():
    with pytest.raises(MonthlySchemaError, match="missing columns"):
        normalize_money_supply(pd.DataFrame([{"月份": "2025年01月份"}]))


def test_normalize_money_supply_rejects_duplicate_month():
    row = {
        "月份": "2025年01月份",
        "货币和准货币(M2)-同比增长": 7.0,
        "货币(M1)-同比增长": 0.4,
    }
    with pytest.raises(MonthlySchemaError, match="duplicate money supply month"):
        normalize_money_supply(pd.DataFrame([row, row]))


def test_normalize_money_supply_rejects_non_finite_value():
    with pytest.raises(MonthlySchemaError, match="must be finite"):
        normalize_money_supply(
            pd.DataFrame(
                [
                    {
                        "月份": "2025年01月份",
                        "货币和准货币(M2)-同比增长": float("nan"),
                        "货币(M1)-同比增长": 0.4,
                    }
                ]
            )
        )


def test_stale_common_macro_month_returns_unknown():
    facts = build_macro_facts(
        {
            "household": {"2024-01": 100.0},
            "nonbank": {"2024-01": 20.0},
        },
        {"2024-01": {"m1_yoy_pct": 1.0, "m2_yoy_pct": 2.0}},
        report_month="2025-01",
        max_lag_months=2,
    )
    assert facts["macro_month"] is None
    assert facts["failures"] == ["no fresh common macro month"]


def test_household_gap_does_not_erase_nonbank_or_money_facts():
    facts = build_macro_facts(
        {
            "household": {"2024-12": 90.0, "2025-01": 100.0},
            "nonbank": {"2024-12": 20.0, "2025-01": 21.0},
        },
        {
            "2024-12": {"m1_yoy_pct": -1.4, "m2_yoy_pct": 7.3},
            "2025-01": {"m1_yoy_pct": 0.4, "m2_yoy_pct": 7.0},
        },
        report_month="2025-01",
        max_lag_months=2,
    )
    assert facts["household"] is None
    assert facts["nonbank"]["current"] == 21.0
    assert facts["money_supply"]["current_m1_yoy_pct"] == 0.4
    assert facts["failures"] == [
        "household missing months ['2024-01', '2023-12']"
    ]
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_monthly_ingest.py -k "money or macro" -v
```

Expected: module import fails.

- [ ] **Step 3: 实现月份工具和宏观 facts**

创建 `src/lurker/ingest/macro_monthly.py`，先加入：

```python
from __future__ import annotations

import math
import re
from calendar import monthrange
from datetime import date
from typing import Any

import pandas as pd


class MonthlySourceError(RuntimeError):
    pass


class MonthlySchemaError(MonthlySourceError):
    pass


_MONEY_COLUMNS = {
    "月份",
    "货币和准货币(M2)-同比增长",
    "货币(M1)-同比增长",
}


def _month(value: str) -> str:
    match = re.fullmatch(r"(20\d{2})年?(0[1-9]|1[0-2])(?:月份?)?", str(value).strip())
    if not match:
        raise MonthlySchemaError(f"invalid month: {value}")
    return f"{match.group(1)}-{match.group(2)}"


def _shift_month(value: str, offset: int) -> str:
    year, month = map(int, value.split("-"))
    ordinal = year * 12 + month - 1 + offset
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def _month_distance(later: str, earlier: str) -> int:
    ly, lm = map(int, later.split("-"))
    ey, em = map(int, earlier.split("-"))
    return (ly - ey) * 12 + lm - em


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MonthlySchemaError(f"{field} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MonthlySchemaError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise MonthlySchemaError(f"{field} must be finite")
    return result


def normalize_money_supply(frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    missing = _MONEY_COLUMNS - set(frame.columns)
    if missing:
        raise MonthlySchemaError(f"money supply missing columns {sorted(missing)}")
    result: dict[str, dict[str, float]] = {}
    for row in frame.to_dict(orient="records"):
        month = _month(str(row["月份"]))
        if month in result:
            raise MonthlySchemaError(f"duplicate money supply month {month}")
        result[month] = {
            "m1_yoy_pct": _finite(
                row["货币(M1)-同比增长"], f"{month} m1_yoy_pct"
            ),
            "m2_yoy_pct": _finite(
                row["货币和准货币(M2)-同比增长"], f"{month} m2_yoy_pct"
            ),
        }
    return result


def select_common_macro_month(
    deposits: dict[str, dict[str, float]],
    money: dict[str, dict[str, float]],
    *,
    report_month: str,
    max_lag_months: int,
) -> str | None:
    common = (
        set(deposits.get("household", {}))
        & set(deposits.get("nonbank", {}))
        & set(money)
    )
    eligible = sorted(month for month in common if month <= report_month)
    if not eligible:
        return None
    selected = eligible[-1]
    if _month_distance(report_month, selected) > max_lag_months:
        return None
    return selected


def build_macro_facts(
    deposits: dict[str, dict[str, float]],
    money: dict[str, dict[str, float]],
    *,
    report_month: str,
    max_lag_months: int,
) -> dict[str, object]:
    selected = select_common_macro_month(
        deposits,
        money,
        report_month=report_month,
        max_lag_months=max_lag_months,
    )
    if selected is None:
        return {
            "macro_month": None,
            "household": None,
            "nonbank": None,
            "money_supply": None,
            "failures": ["no fresh common macro month"],
        }
    previous = _shift_month(selected, -1)
    previous_year = _shift_month(selected, -12)
    previous_year_previous = _shift_month(selected, -13)
    failures: list[str] = []

    household_months = (
        selected,
        previous,
        previous_year,
        previous_year_previous,
    )
    household_missing = [
        month for month in household_months
        if month not in deposits["household"]
    ]
    if household_missing:
        household = None
        failures.append(f"household missing months {household_missing}")
    else:
        household = {
            "current": deposits["household"][selected],
            "previous_month": deposits["household"][previous],
            "previous_year": deposits["household"][previous_year],
            "previous_year_previous_month": deposits["household"][
                previous_year_previous
            ],
        }

    nonbank_missing = [
        month for month in (selected, previous)
        if month not in deposits["nonbank"]
    ]
    if nonbank_missing:
        nonbank = None
        failures.append(f"nonbank missing months {nonbank_missing}")
    else:
        nonbank = {
            "current": deposits["nonbank"][selected],
            "previous_month": deposits["nonbank"][previous],
        }

    if previous not in money:
        money_supply = None
        failures.append(f"money supply missing month {previous}")
    else:
        money_supply = {
            "current_m1_yoy_pct": money[selected]["m1_yoy_pct"],
            "current_m2_yoy_pct": money[selected]["m2_yoy_pct"],
            "previous_m1_yoy_pct": money[previous]["m1_yoy_pct"],
            "previous_m2_yoy_pct": money[previous]["m2_yoy_pct"],
        }
    return {
        "macro_month": selected,
        "household": household,
        "nonbank": nonbank,
        "money_supply": money_supply,
        "failures": failures,
    }
```

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_monthly_ingest.py -k "money or macro" -v
.venv/bin/ruff check src/lurker/ingest/macro_monthly.py tests/test_macro_monthly_ingest.py
git add src/lurker/ingest/macro_monthly.py tests/test_macro_monthly_ingest.py
git commit -m "feat: align monthly deposits and money supply"
```

---

### Task 5: 同日融资余额与交易所 A 股流通市值

**Files:**
- Modify: `src/lurker/ingest/macro_monthly.py`
- Modify: `tests/test_macro_monthly_ingest.py`

实施前预检已经用当前锁定环境的 AkShare 实际返回值确认：

- `macro_china_market_margin_sh()` 与 `macro_china_market_margin_sz()` 的
  `融资余额` 均为人民币元；
- `stock_sse_deal_daily()` 的 `主板A`、`科创板` 流通市值为亿元；
- `stock_szse_summary()` 的 `主板A股`、`创业板A股` 流通市值为人民币元。

因此快照统一保存人民币元；任何列名或单位契约变化都必须失败关闭，不能猜测换算。

- [ ] **Step 1: 写 leverage RED**

在 `tests/test_macro_monthly_ingest.py` 增加：

```python
from datetime import date

from lurker.ingest.macro_monthly import (
    ExchangeCircMvResult,
    build_leverage_facts,
    normalize_exchange_circ_mv,
    normalize_margin_history,
)


def exchange_result(value: float) -> ExchangeCircMvResult:
    return ExchangeCircMvResult(value_yuan=value, sources=())


def test_normalize_exchange_circ_mv_excludes_b_shares_and_converts_units():
    sse = pd.DataFrame(
        [
            {"单日情况": "流通市值", "主板A": 500000.0, "主板B": 700.0, "科创板": 100000.0},
        ]
    )
    szse = pd.DataFrame(
        [
            {"证券类别": "主板A股", "流通市值": 15_000_000_000_000.0},
            {"证券类别": "主板B股", "流通市值": 40_000_000_000.0},
            {"证券类别": "创业板A股", "流通市值": 7_000_000_000_000.0},
        ]
    )
    result = normalize_exchange_circ_mv(sse, szse)
    assert result == 82_000_000_000_000.0


def test_build_leverage_facts_uses_latest_common_date_and_previous_month_end():
    sh = pd.DataFrame(
        [
            {"日期": date(2025, 1, 30), "融资余额": 100.0},
            {"日期": date(2025, 1, 27), "融资余额": 90.0},
            {"日期": date(2024, 12, 31), "融资余额": 80.0},
        ]
    )
    sz = pd.DataFrame(
        [
            {"日期": date(2025, 1, 30), "融资余额": 50.0},
            {"日期": date(2024, 12, 31), "融资余额": 40.0},
        ]
    )
    result = build_leverage_facts(
        sh,
        sz,
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        trading_day_checker=lambda value: value.weekday() < 5,
        today=date(2026, 7, 26),
    )
    assert result == {
        "trade_date": "2025-01-30",
        "current_financing_balance": 150.0,
        "previous_trade_date": "2024-12-31",
        "previous_financing_balance": 120.0,
        "a_share_circ_mv": 10_000.0,
        "circ_mv_sources": [],
        "failure": None,
    }


def test_build_leverage_facts_fails_closed_when_exchange_date_is_missing():
    empty = pd.DataFrame(columns=["日期", "融资余额"])
    result = build_leverage_facts(
        empty,
        empty,
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        trading_day_checker=lambda value: True,
        today=date(2026, 7, 26),
    )
    assert result["trade_date"] is None
    assert result["failure"] == "no common Shanghai/Shenzhen margin date"
```

```python
def test_leverage_ignores_non_common_newer_date():
    sh = pd.DataFrame(
        [
            {"日期": date(2025, 1, 31), "融资余额": 101.0},
            {"日期": date(2025, 1, 30), "融资余额": 100.0},
            {"日期": date(2024, 12, 31), "融资余额": 80.0},
        ]
    )
    sz = pd.DataFrame(
        [
            {"日期": date(2025, 1, 30), "融资余额": 50.0},
            {"日期": date(2024, 12, 31), "融资余额": 40.0},
        ]
    )
    result = build_leverage_facts(
        sh,
        sz,
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        trading_day_checker=lambda value: value.weekday() < 5,
        today=date(2026, 7, 26),
    )
    assert result["trade_date"] == "2025-01-30"


def test_leverage_rejects_stale_common_date():
    frame = pd.DataFrame(
        [
            {"日期": date(2025, 1, 20), "融资余额": 100.0},
            {"日期": date(2024, 12, 31), "融资余额": 80.0},
        ]
    )
    result = build_leverage_facts(
        frame,
        frame,
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        trading_day_checker=lambda value: value.weekday() < 5,
        today=date(2026, 7, 26),
    )
    assert result["failure"] == "margin data is stale"


def test_leverage_rejects_missing_previous_month():
    frame = pd.DataFrame(
        [{"日期": date(2025, 1, 30), "融资余额": 100.0}]
    )
    result = build_leverage_facts(
        frame,
        frame,
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        trading_day_checker=lambda value: value.weekday() < 5,
        today=date(2026, 7, 26),
    )
    assert result["failure"] == "no previous-month common margin date"


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan")])
def test_leverage_rejects_invalid_market_cap(value):
    frame = pd.DataFrame(
        [
            {"日期": date(2025, 1, 30), "融资余额": 100.0},
            {"日期": date(2024, 12, 31), "融资余额": 80.0},
        ]
    )
    with pytest.raises(MonthlySchemaError, match="market cap"):
        build_leverage_facts(
            frame,
            frame,
            report_month="2025-01",
            max_lag_trading_days=3,
            circ_mv_fetcher=lambda trade_date: exchange_result(value),
            trading_day_checker=lambda day: day.weekday() < 5,
            today=date(2026, 7, 26),
        )


def test_exchange_market_cap_requires_exact_a_share_categories():
    sse = pd.DataFrame(
        [{"单日情况": "流通市值", "主板A": 1.0, "主板B": 1.0}]
    )
    szse = pd.DataFrame(
        [{"证券类别": "股票", "流通市值": 1.0}]
    )
    with pytest.raises(MonthlySchemaError, match="missing column"):
        normalize_exchange_circ_mv(sse, szse)


def test_historical_report_month_never_uses_later_margin_date():
    frame = pd.DataFrame(
        [
            {"日期": date(2026, 7, 24), "融资余额": 999.0},
            {"日期": date(2025, 1, 30), "融资余额": 100.0},
            {"日期": date(2024, 12, 31), "融资余额": 80.0},
        ]
    )
    result = build_leverage_facts(
        frame,
        frame,
        report_month="2025-01",
        max_lag_trading_days=3,
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        trading_day_checker=lambda value: value.weekday() < 5,
        today=date(2026, 7, 26),
    )
    assert result["trade_date"] == "2025-01-30"
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_monthly_ingest.py -k leverage -v
```

Expected: missing functions.

- [ ] **Step 3: 实现同日对齐与市值规范化**

在 `src/lurker/ingest/macro_monthly.py` 增加：

```python
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from lurker.trading_calendar import is_cn_trading_day


@dataclass(frozen=True)
class ExchangeCircMvResult:
    value_yuan: float
    sources: tuple[dict[str, str], ...]


def normalize_margin_history(frame: pd.DataFrame) -> dict[date, float]:
    required = {"日期", "融资余额"}
    missing = required - set(frame.columns)
    if missing:
        raise MonthlySchemaError(f"margin missing columns {sorted(missing)}")
    result: dict[date, float] = {}
    for row in frame.to_dict(orient="records"):
        parsed = pd.to_datetime(row["日期"], errors="coerce")
        if pd.isna(parsed):
            raise MonthlySchemaError("margin date must be valid")
        trade_date = pd.Timestamp(parsed).date()
        if trade_date in result:
            raise MonthlySchemaError(f"duplicate margin date {trade_date}")
        value = _finite(row["融资余额"], f"margin {trade_date}")
        if value < 0:
            raise MonthlySchemaError(f"margin {trade_date} must be non-negative")
        result[trade_date] = value
    return result


def _single_row(frame: pd.DataFrame, column: str, value: str) -> dict[str, Any]:
    if column not in frame.columns:
        raise MonthlySchemaError(f"exchange summary missing column {column}")
    rows = frame.loc[frame[column].astype(str).str.strip() == value]
    if len(rows) != 1:
        raise MonthlySchemaError(f"expected one {value} row")
    return rows.iloc[0].to_dict()


def normalize_exchange_circ_mv(
    sse: pd.DataFrame,
    szse: pd.DataFrame,
) -> float:
    sse_row = _single_row(sse, "单日情况", "流通市值")
    for column in ("主板A", "科创板"):
        if column not in sse_row:
            raise MonthlySchemaError(
                f"exchange summary missing column {column}"
            )
    sse_yuan = (
        _finite(sse_row["主板A"], "SSE 主板A 流通市值")
        + _finite(sse_row["科创板"], "SSE 科创板 流通市值")
    ) * 100_000_000
    sz_main = _single_row(szse, "证券类别", "主板A股")
    sz_chinext = _single_row(szse, "证券类别", "创业板A股")
    if "流通市值" not in sz_main or "流通市值" not in sz_chinext:
        raise MonthlySchemaError("exchange summary missing column 流通市值")
    szse_yuan = _finite(
        sz_main["流通市值"], "SZSE 主板A股 流通市值"
    ) + _finite(sz_chinext["流通市值"], "SZSE 创业板A股 流通市值")
    total = sse_yuan + szse_yuan
    if total <= 0:
        raise MonthlySchemaError("A-share circulating market cap must be positive")
    return total


def _month_end(report_month: str, today: date) -> date:
    year, month = map(int, report_month.split("-"))
    last = date(year, month, monthrange(year, month)[1])
    return min(last, today)


def _trading_day_lag(
    start: date,
    end: date,
    checker: Callable[[date], bool],
) -> int:
    cursor = start + timedelta(days=1)
    count = 0
    while cursor <= end:
        if checker(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def build_leverage_facts(
    sh_frame: pd.DataFrame,
    sz_frame: pd.DataFrame,
    *,
    report_month: str,
    max_lag_trading_days: int,
    circ_mv_fetcher: Callable[[str], ExchangeCircMvResult],
    trading_day_checker: Callable[[date], bool] = is_cn_trading_day,
    today: date | None = None,
) -> dict[str, object]:
    current_day = today or date.today()
    cutoff = _month_end(report_month, current_day)
    sh = normalize_margin_history(sh_frame)
    sz = normalize_margin_history(sz_frame)
    common = sorted(day for day in set(sh) & set(sz) if day <= cutoff)
    if not common:
        return {
            "trade_date": None,
            "current_financing_balance": None,
            "previous_trade_date": None,
            "previous_financing_balance": None,
            "a_share_circ_mv": None,
            "circ_mv_sources": [],
            "failure": "no common Shanghai/Shenzhen margin date",
        }
    selected = common[-1]
    if _trading_day_lag(selected, cutoff, trading_day_checker) > max_lag_trading_days:
        return {
            "trade_date": selected.isoformat(),
            "current_financing_balance": None,
            "previous_trade_date": None,
            "previous_financing_balance": None,
            "a_share_circ_mv": None,
            "circ_mv_sources": [],
            "failure": "margin data is stale",
        }
    previous_month = _shift_month(report_month, -1)
    py, pm = map(int, previous_month.split("-"))
    previous_end = date(py, pm, monthrange(py, pm)[1])
    previous_common = [
        day for day in common
        if day.year == py and day.month == pm and day <= previous_end
    ]
    if not previous_common:
        return {
            "trade_date": selected.isoformat(),
            "current_financing_balance": sh[selected] + sz[selected],
            "previous_trade_date": None,
            "previous_financing_balance": None,
            "a_share_circ_mv": None,
            "circ_mv_sources": [],
            "failure": "no previous-month common margin date",
        }
    previous = previous_common[-1]
    circ_mv_result = circ_mv_fetcher(selected.strftime("%Y%m%d"))
    circ_mv = _finite(
        circ_mv_result.value_yuan,
        f"circ_mv {selected}",
    )
    if circ_mv <= 0:
        raise MonthlySchemaError("A-share circulating market cap must be positive")
    return {
        "trade_date": selected.isoformat(),
        "current_financing_balance": sh[selected] + sz[selected],
        "previous_trade_date": previous.isoformat(),
        "previous_financing_balance": sh[previous] + sz[previous],
        "a_share_circ_mv": circ_mv,
        "circ_mv_sources": list(circ_mv_result.sources),
        "failure": None,
    }
```

再提供真实 AkShare wrapper：

```python
def _frame_source(
    *,
    source: str,
    url: str,
    data_date: str,
    frame: pd.DataFrame,
) -> dict[str, str]:
    payload = frame.to_json(
        orient="records",
        date_format="iso",
        force_ascii=False,
    ).encode("utf-8")
    return {
        "source": source,
        "url": url,
        "data_date": data_date,
        "sha256": f"sha256:{hashlib.sha256(payload).hexdigest()}",
        "hash_scope": "normalized_frame",
    }


def fetch_exchange_circ_mv(trade_date: str) -> ExchangeCircMvResult:
    import akshare as ak
    import requests

    try:
        sse = ak.stock_sse_deal_daily(date=trade_date)
        szse = ak.stock_szse_summary(date=trade_date)
    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
        raise MonthlySourceError(
            f"exchange market-cap source failed for {trade_date}: {exc}"
        ) from exc
    return ExchangeCircMvResult(
        value_yuan=normalize_exchange_circ_mv(sse, szse),
        sources=(
            _frame_source(
                source="akshare.stock_sse_deal_daily",
                url="https://query.sse.com.cn/commonQuery.do",
                data_date=trade_date,
                frame=sse,
            ),
            _frame_source(
                source="akshare.stock_szse_summary",
                url="http://www.szse.cn/api/report/ShowReport",
                data_date=trade_date,
                frame=szse,
            ),
        ),
    )
```

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_monthly_ingest.py -k leverage -v
.venv/bin/ruff check src/lurker/ingest/macro_monthly.py tests/test_macro_monthly_ingest.py
git add src/lurker/ingest/macro_monthly.py tests/test_macro_monthly_ingest.py
git commit -m "feat: align monthly margin and A-share market cap"
```

---

### Task 6: 月度快照采集与原子覆盖

**Files:**
- Modify: `src/lurker/ingest/macro_monthly.py`
- Modify: `tests/test_macro_monthly_ingest.py`

- [ ] **Step 1: 写 snapshot RED**

在 `tests/test_macro_monthly_ingest.py` 增加：

```python
import json

from lurker.config import MonthlyMacroConfig
from lurker.ingest.macro_monthly import (
    MonthlyMacroSnapshotStore,
    collect_monthly_macro_snapshot,
)


def monthly_config() -> MonthlyMacroConfig:
    return MonthlyMacroConfig(
        credit_table_urls={2024: "https://www.pbc.gov.cn/2024.htm"},
        allowed_hosts=("www.pbc.gov.cn",),
        timeout_seconds=30,
        max_response_bytes=1_000_000,
        household_deposit_yoy_pct=12.0,
        leverage_ratio_pct=4.0,
        financing_monthly_growth_pct=20.0,
        macro_max_lag_months=2,
        leverage_max_lag_trading_days=3,
    )


def margin_frame(current: float, previous: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"日期": date(2025, 1, 30), "融资余额": current},
            {"日期": date(2024, 12, 31), "融资余额": previous},
        ]
    )


def test_collect_monthly_snapshot_preserves_sources_thresholds_and_failures(
    tmp_path,
):
    snapshot = collect_monthly_macro_snapshot(
        report_month="2025-01",
        config=monthly_config(),
        raw_dir=tmp_path / "raw",
        pboc_collector=lambda config, raw_dir: {
            "balances": {
                "household": {
                    "2023-12": 80.0,
                    "2024-01": 85.0,
                    "2024-12": 90.0,
                    "2025-01": 100.0,
                },
                "nonbank": {"2024-12": 20.0, "2025-01": 21.0},
            },
            "sources": [
                {
                    "source": "PBOC credit table",
                    "url": "https://www.pbc.gov.cn/table",
                    "data_date": "2025-01",
                    "retrieved_at": "2026-07-26T12:00:00+00:00",
                    "sha256": "sha256:a",
                }
            ],
            "failures": [],
        },
        money_fetcher=lambda: pd.DataFrame(
            [
                {
                    "月份": "2025年01月份",
                    "货币和准货币(M2)-同比增长": 7.0,
                    "货币(M1)-同比增长": 0.4,
                },
                {
                    "月份": "2024年12月份",
                    "货币和准货币(M2)-同比增长": 7.3,
                    "货币(M1)-同比增长": -1.4,
                },
            ]
        ),
        margin_sh_fetcher=lambda: margin_frame(100.0, 80.0),
        margin_sz_fetcher=lambda: margin_frame(50.0, 40.0),
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        generated_at=lambda: "2026-07-26T12:00:00+00:00",
        today=date(2026, 7, 26),
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["report_month"] == "2025-01"
    assert snapshot["macro"]["macro_month"] == "2025-01"
    assert snapshot["leverage"]["trade_date"] == "2025-01-30"
    assert snapshot["thresholds"]["leverage_ratio_pct"] == 4.0
    assert snapshot["sources"][0]["sha256"] == "sha256:a"
    assert all(
        {"url", "data_date", "retrieved_at", "sha256"} <= set(source)
        for source in snapshot["sources"]
    )
    assert snapshot["failures"] == []


def test_monthly_snapshot_store_overwrites_same_month_atomically(tmp_path):
    store = MonthlyMacroSnapshotStore(tmp_path)
    first = {"schema_version": 1, "report_month": "2025-01", "value": 1}
    second = {"schema_version": 1, "report_month": "2025-01", "value": 2}

    path = store.save(first)
    assert store.save(second) == path
    assert json.loads(path.read_text(encoding="utf-8"))["value"] == 2
    assert list(tmp_path.glob("*.json")) == [path]
    assert list(tmp_path.glob("*.tmp")) == []
```

```python
from lurker.ingest.macro_monthly import MonthlySourceError
from lurker.ingest.pboc_deposits import PbocSourceError


def _raise_pboc(config, raw_dir):
    raise PbocSourceError("PBOC timeout")


def _raise_monthly():
    raise MonthlySourceError("provider timeout")


def _valid_pboc(config, raw_dir):
    return {
        "balances": {
            "household": {
                "2023-12": 80.0,
                "2024-01": 85.0,
                "2024-12": 90.0,
                "2025-01": 100.0,
            },
            "nonbank": {"2024-12": 20.0, "2025-01": 21.0},
        },
        "sources": [],
        "failures": [],
    }


def _money_frame():
    return pd.DataFrame(
        [
            {
                "月份": "2025年01月份",
                "货币和准货币(M2)-同比增长": 7.0,
                "货币(M1)-同比增长": 0.4,
            },
            {
                "月份": "2024年12月份",
                "货币和准货币(M2)-同比增长": 7.3,
                "货币(M1)-同比增长": -1.4,
            },
        ]
    )


def test_pboc_failure_only_degrades_macro(tmp_path):
    snapshot = collect_monthly_macro_snapshot(
        report_month="2025-01",
        config=monthly_config(),
        raw_dir=tmp_path,
        pboc_collector=_raise_pboc,
        margin_sh_fetcher=lambda: margin_frame(100.0, 80.0),
        margin_sz_fetcher=lambda: margin_frame(50.0, 40.0),
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        today=date(2026, 7, 26),
    )
    assert snapshot["macro"]["household"] is None
    assert snapshot["leverage"]["trade_date"] == "2025-01-30"
    assert snapshot["failures"][0]["reason"] == "PBOC timeout"


def test_money_failure_only_degrades_macro(tmp_path):
    snapshot = collect_monthly_macro_snapshot(
        report_month="2025-01",
        config=monthly_config(),
        raw_dir=tmp_path,
        pboc_collector=_valid_pboc,
        money_fetcher=_raise_monthly,
        margin_sh_fetcher=lambda: margin_frame(100.0, 80.0),
        margin_sz_fetcher=lambda: margin_frame(50.0, 40.0),
        circ_mv_fetcher=lambda trade_date: exchange_result(10_000.0),
        today=date(2026, 7, 26),
    )
    assert snapshot["macro"]["money_supply"] is None
    assert snapshot["leverage"]["trade_date"] == "2025-01-30"


def test_margin_failure_only_degrades_leverage(tmp_path):
    snapshot = collect_monthly_macro_snapshot(
        report_month="2025-01",
        config=monthly_config(),
        raw_dir=tmp_path,
        pboc_collector=_valid_pboc,
        money_fetcher=_money_frame,
        margin_sh_fetcher=_raise_monthly,
        margin_sz_fetcher=lambda: margin_frame(50.0, 40.0),
        today=date(2026, 7, 26),
    )
    assert snapshot["macro"]["macro_month"] == "2025-01"
    assert snapshot["leverage"]["trade_date"] is None


def test_programming_type_error_propagates(tmp_path):
    with pytest.raises(TypeError, match="programmer error"):
        collect_monthly_macro_snapshot(
            report_month="2025-01",
            config=monthly_config(),
            raw_dir=tmp_path,
            pboc_collector=lambda config, raw_dir: (_ for _ in ()).throw(
                TypeError("programmer error")
            ),
        )


def test_snapshot_store_cleans_temp_file_on_json_failure(
    monkeypatch,
    tmp_path,
):
    store = MonthlyMacroSnapshotStore(tmp_path)
    monkeypatch.setattr(
        json,
        "dump",
        lambda value, handle, **kwargs: (_ for _ in ()).throw(
            TypeError("json failure")
        ),
    )
    with pytest.raises(TypeError, match="json failure"):
        store.save({"schema_version": 1, "report_month": "2025-01"})
    assert list(tmp_path.glob("*.tmp")) == []
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_monthly_ingest.py -k snapshot -v
```

Expected: missing collector/store.

- [ ] **Step 3: 实现 collector 和 store**

在 `src/lurker/ingest/macro_monthly.py` 增加明确的 fetcher 类型别名和：

```python
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import requests

from lurker.config import MonthlyMacroConfig
from lurker.ingest.pboc_deposits import (
    PbocSourceError,
    collect_pboc_deposits,
)


def _normalized_source(
    source: str,
    url: str,
    data_date: str | None,
    payload: object,
    retrieved_at: str,
) -> dict[str, str]:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return {
        "source": source,
        "url": url,
        "data_date": data_date or "unknown",
        "retrieved_at": retrieved_at,
        "sha256": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "hash_scope": "normalized_frame",
    }


def _default_money_fetcher() -> pd.DataFrame:
    import akshare as ak

    try:
        return ak.macro_china_money_supply()
    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
        raise MonthlySourceError(f"money supply source failed: {exc}") from exc


def _default_margin_sh_fetcher() -> pd.DataFrame:
    import akshare as ak

    try:
        return ak.macro_china_market_margin_sh()
    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
        raise MonthlySourceError(f"Shanghai margin source failed: {exc}") from exc


def _default_margin_sz_fetcher() -> pd.DataFrame:
    import akshare as ak

    try:
        return ak.macro_china_market_margin_sz()
    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
        raise MonthlySourceError(f"Shenzhen margin source failed: {exc}") from exc


def collect_monthly_macro_snapshot(
    *,
    report_month: str,
    config: MonthlyMacroConfig,
    raw_dir: str | Path,
    pboc_collector=collect_pboc_deposits,
    money_fetcher: Callable[[], pd.DataFrame] = _default_money_fetcher,
    margin_sh_fetcher: Callable[[], pd.DataFrame] = _default_margin_sh_fetcher,
    margin_sz_fetcher: Callable[[], pd.DataFrame] = _default_margin_sz_fetcher,
    circ_mv_fetcher: Callable[
        [str], ExchangeCircMvResult
    ] = fetch_exchange_circ_mv,
    generated_at: Callable[[], str] | None = None,
    today: date | None = None,
) -> dict[str, object]:
    clock = generated_at or (lambda: pd.Timestamp.now(tz="UTC").isoformat())
    collected_at = clock()
    failures: list[dict[str, str]] = []
    sources: list[dict[str, object]] = []
    macro = {
        "macro_month": None,
        "household": None,
        "nonbank": None,
        "money_supply": None,
        "failures": ["macro sources unavailable"],
    }
    try:
        deposits = pboc_collector(config, raw_dir=raw_dir)
        sources.extend(deposits["sources"])
        failures.extend(
            {"source": "pboc_revision", "reason": reason}
            for reason in deposits["failures"]
        )
        money = normalize_money_supply(money_fetcher())
        macro = build_macro_facts(
            deposits["balances"],
            money,
            report_month=report_month,
            max_lag_months=config.macro_max_lag_months,
        )
        sources.append(
            _normalized_source(
                "akshare.macro_china_money_supply",
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                macro["macro_month"],
                money,
                collected_at,
            )
        )
        failures.extend(
            {"source": "macro_alignment", "reason": reason}
            for reason in macro["failures"]
        )
    except (PbocSourceError, MonthlySourceError) as exc:
        failures.append({"source": "macro", "reason": str(exc)})

    leverage = {
        "trade_date": None,
        "current_financing_balance": None,
        "previous_trade_date": None,
        "previous_financing_balance": None,
        "a_share_circ_mv": None,
        "circ_mv_sources": [],
        "failure": "leverage sources unavailable",
    }
    try:
        sh_frame = margin_sh_fetcher()
        sz_frame = margin_sz_fetcher()
        leverage = build_leverage_facts(
            sh_frame,
            sz_frame,
            report_month=report_month,
            max_lag_trading_days=config.leverage_max_lag_trading_days,
            circ_mv_fetcher=circ_mv_fetcher,
            today=today,
        )
        if leverage["failure"]:
            failures.append(
                {"source": "leverage_alignment", "reason": leverage["failure"]}
            )
        sources.extend(
            [
                _normalized_source(
                    "akshare.macro_china_market_margin_sh",
                    "https://cdn.jin10.com/data_center/reports/fs_1.json",
                    leverage["trade_date"],
                    sh_frame.to_dict(orient="records"),
                    collected_at,
                ),
                _normalized_source(
                    "akshare.macro_china_market_margin_sz",
                    "https://cdn.jin10.com/data_center/reports/fs_2.json",
                    leverage["trade_date"],
                    sz_frame.to_dict(orient="records"),
                    collected_at,
                ),
            ]
        )
        sources.extend(
            {**source, "retrieved_at": collected_at}
            for source in leverage["circ_mv_sources"]
        )
    except MonthlySourceError as exc:
        failures.append({"source": "leverage", "reason": str(exc)})

    return {
        "schema_version": 1,
        "report_month": report_month,
        "generated_at": collected_at,
        "macro": macro,
        "leverage": leverage,
        "thresholds": {
            "household_deposit_yoy_pct": config.household_deposit_yoy_pct,
            "leverage_ratio_pct": config.leverage_ratio_pct,
            "financing_monthly_growth_pct": (
                config.financing_monthly_growth_pct
            ),
        },
        "sources": sources,
        "failures": failures,
    }


class MonthlyMacroSnapshotStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def save(self, snapshot: dict[str, object]) -> Path:
        report_month = str(snapshot["report_month"])
        path = self.directory / f"{report_month}.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=self.directory,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return path
```

不要捕获 `TypeError`、`AttributeError` 或 JSON/文件错误。

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_macro_monthly_ingest.py -v
.venv/bin/ruff check src/lurker/ingest/macro_monthly.py tests/test_macro_monthly_ingest.py
git add src/lurker/ingest/macro_monthly.py tests/test_macro_monthly_ingest.py
git commit -m "feat: collect auditable monthly macro snapshots"
```

---

### Task 7: 四维状态机和综合结论

**Files:**
- Create: `src/lurker/application/monthly_macro_flow.py`
- Create: `tests/test_monthly_macro_flow.py`

- [ ] **Step 1: 写完整真值表 RED**

创建 `tests/test_monthly_macro_flow.py`：

```python
import pytest

from lurker.application.monthly_macro_flow import analyze_monthly_macro_flow


def complete_snapshot() -> dict:
    return {
        "schema_version": 1,
        "report_month": "2025-01",
        "generated_at": "2026-07-26T12:00:00+00:00",
        "macro": {
            "macro_month": "2025-01",
            "household": {
                "current": 111.0,
                "previous_month": 109.0,
                "previous_year": 100.0,
                "previous_year_previous_month": 100.0,
            },
            "nonbank": {"current": 21.0, "previous_month": 20.0},
            "money_supply": {
                "current_m1_yoy_pct": 5.0,
                "current_m2_yoy_pct": 7.0,
                "previous_m1_yoy_pct": 4.0,
                "previous_m2_yoy_pct": 7.0,
            },
            "failures": [],
        },
        "leverage": {
            "trade_date": "2025-01-30",
            "current_financing_balance": 200.0,
            "previous_trade_date": "2024-12-31",
            "previous_financing_balance": 190.0,
            "a_share_circ_mv": 10_000.0,
            "failure": None,
        },
        "thresholds": {
            "household_deposit_yoy_pct": 12.0,
            "leverage_ratio_pct": 4.0,
            "financing_monthly_growth_pct": 20.0,
        },
        "sources": [],
        "failures": [],
    }


@pytest.mark.parametrize(
    ("household_current", "nonbank_current", "current_m1", "expected"),
    [
        (111.0, 21.0, 5.0, "牛市加速"),
        (113.0, 21.0, 5.0, "慢牛蓄力"),
        (113.0, 19.0, 5.0, "震荡磨底"),
        (113.0, 19.0, 3.0, "震荡磨底"),
    ],
)
def test_complete_state_matrix(
    household_current,
    nonbank_current,
    current_m1,
    expected,
):
    snapshot = complete_snapshot()
    snapshot["macro"]["household"]["current"] = household_current
    snapshot["macro"]["nonbank"]["current"] = nonbank_current
    snapshot["macro"]["money_supply"]["current_m1_yoy_pct"] = current_m1

    result = analyze_monthly_macro_flow(snapshot)

    assert result["report_mode"] == "classified"
    assert result["market_state"] == expected


def test_ratio_overheat_has_priority_when_macro_is_missing():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"] = None
    snapshot["leverage"]["current_financing_balance"] = 401.0
    result = analyze_monthly_macro_flow(snapshot)
    assert result["market_state"] == "过热警报"
    assert result["leverage"]["status"] == "overheated"


def test_growth_overheat_has_priority_when_market_cap_is_missing():
    snapshot = complete_snapshot()
    snapshot["leverage"]["current_financing_balance"] = 121.0
    snapshot["leverage"]["previous_financing_balance"] = 100.0
    snapshot["leverage"]["a_share_circ_mv"] = None
    result = analyze_monthly_macro_flow(snapshot)
    assert result["market_state"] == "过热警报"


def test_non_triggering_partial_leverage_is_unknown():
    snapshot = complete_snapshot()
    snapshot["leverage"]["a_share_circ_mv"] = None
    result = analyze_monthly_macro_flow(snapshot)
    assert result["report_mode"] == "data_observation"
    assert result["market_state"] is None
    assert result["leverage"]["status"] == "unknown"


@pytest.mark.parametrize(
    ("ratio", "growth"),
    [(4.0, 20.0), (3.99, 19.99)],
)
def test_exact_leverage_boundaries_are_not_overheated(ratio, growth):
    snapshot = complete_snapshot()
    snapshot["leverage"]["a_share_circ_mv"] = 10_000.0
    snapshot["leverage"]["current_financing_balance"] = ratio * 100.0
    snapshot["leverage"]["previous_financing_balance"] = (
        snapshot["leverage"]["current_financing_balance"] / (1 + growth / 100)
    )
    result = analyze_monthly_macro_flow(snapshot)
    assert result["leverage"]["status"] == "healthy"


def test_exact_twelve_percent_is_deposit_dominant():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"]["current"] = 112.0
    result = analyze_monthly_macro_flow(snapshot)
    assert result["household"]["yoy_pct"] == pytest.approx(12.0)
    assert result["household"]["status"] == "deposit_dominant"
```

```python
@pytest.mark.parametrize(
    ("current", "expected"),
    [(21.0, "rising"), (20.0, "flat"), (19.0, "falling")],
)
def test_nonbank_direction(current, expected):
    snapshot = complete_snapshot()
    snapshot["macro"]["nonbank"]["current"] = current
    assert analyze_monthly_macro_flow(snapshot)["nonbank"]["status"] == expected


@pytest.mark.parametrize(
    ("current_m1", "expected"),
    [(5.0, "improving"), (4.0, "flat"), (3.0, "worsening")],
)
def test_money_spread_direction(current_m1, expected):
    snapshot = complete_snapshot()
    snapshot["macro"]["money_supply"]["current_m1_yoy_pct"] = current_m1
    assert (
        analyze_monthly_macro_flow(snapshot)["money_supply"]["status"]
        == expected
    )


@pytest.mark.parametrize("denominator", [0.0, float("nan"), float("inf")])
def test_invalid_market_cap_yields_observation(denominator):
    snapshot = complete_snapshot()
    snapshot["leverage"]["a_share_circ_mv"] = denominator
    result = analyze_monthly_macro_flow(snapshot)
    assert result["leverage"]["status"] == "unknown"
    assert result["market_state"] is None


def test_macro_failure_does_not_become_negative_score():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"] = None
    snapshot["failures"] = [
        {"source": "macro", "reason": "PBOC timeout"}
    ]
    result = analyze_monthly_macro_flow(snapshot)
    assert result["report_mode"] == "data_observation"
    assert result["market_state"] is None
    assert result["failures"][0]["reason"] == "PBOC timeout"


def test_missing_positive_dimension_cannot_produce_slow_bull():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"] = None
    snapshot["macro"]["nonbank"]["current"] = 21.0
    snapshot["macro"]["money_supply"]["current_m1_yoy_pct"] = 5.0
    result = analyze_monthly_macro_flow(snapshot)
    assert result["market_state"] is None
    assert result["report_mode"] == "data_observation"


def test_unknown_snapshot_schema_is_rejected():
    snapshot = complete_snapshot()
    snapshot["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        analyze_monthly_macro_flow(snapshot)
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_monthly_macro_flow.py -v
```

Expected: module import fails.

- [ ] **Step 3: 实现纯状态机**

创建 `src/lurker/application/monthly_macro_flow.py`：

```python
from __future__ import annotations

import math
from typing import Any


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _greater_than(value: float, threshold: float) -> bool:
    return value > threshold and not math.isclose(
        value,
        threshold,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def analyze_monthly_macro_flow(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("schema_version") != 1:
        raise ValueError("unsupported monthly macro snapshot schema_version")
    thresholds = snapshot["thresholds"]
    macro = snapshot.get("macro") or {}
    household_raw = macro.get("household")
    nonbank_raw = macro.get("nonbank")
    money_raw = macro.get("money_supply")
    leverage_raw = snapshot.get("leverage") or {}

    household = {"status": "unknown"}
    if household_raw:
        current = _positive(household_raw.get("current"))
        previous = _positive(household_raw.get("previous_month"))
        previous_year = _positive(household_raw.get("previous_year"))
        previous_year_previous = _positive(
            household_raw.get("previous_year_previous_month")
        )
        if all(
            value is not None
            for value in (current, previous, previous_year, previous_year_previous)
        ):
            yoy = (current / previous_year - 1) * 100
            previous_yoy = (previous / previous_year_previous - 1) * 100
            household_threshold = float(
                thresholds["household_deposit_yoy_pct"]
            )
            household = {
                "status": (
                    "relocation_signal"
                    if yoy < household_threshold
                    and not math.isclose(
                        yoy,
                        household_threshold,
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    else "deposit_dominant"
                ),
                "current": current,
                "previous_month": previous,
                "yoy_pct": yoy,
                "previous_yoy_pct": previous_yoy,
                "yoy_change_pp": yoy - previous_yoy,
            }

    nonbank = {"status": "unknown"}
    if nonbank_raw:
        current = _positive(nonbank_raw.get("current"))
        previous = _positive(nonbank_raw.get("previous_month"))
        if current is not None and previous is not None:
            amount = current - previous
            flat = math.isclose(amount, 0.0, rel_tol=0.0, abs_tol=1e-12)
            nonbank = {
                "status": "flat" if flat else "rising" if amount > 0 else "falling",
                "current": current,
                "previous_month": previous,
                "mom_amount": amount,
                "mom_pct": (current / previous - 1) * 100,
            }

    money = {"status": "unknown"}
    if money_raw:
        values = [
            money_raw.get("current_m1_yoy_pct"),
            money_raw.get("current_m2_yoy_pct"),
            money_raw.get("previous_m1_yoy_pct"),
            money_raw.get("previous_m2_yoy_pct"),
        ]
        try:
            numbers = [float(value) for value in values]
        except (TypeError, ValueError):
            numbers = []
        if len(numbers) == 4 and all(math.isfinite(value) for value in numbers):
            current_spread = numbers[0] - numbers[1]
            previous_spread = numbers[2] - numbers[3]
            delta = current_spread - previous_spread
            flat = math.isclose(delta, 0.0, rel_tol=0.0, abs_tol=1e-12)
            money = {
                "status": "flat" if flat else "improving" if delta > 0 else "worsening",
                "current_m1_yoy_pct": numbers[0],
                "current_m2_yoy_pct": numbers[1],
                "previous_m1_yoy_pct": numbers[2],
                "previous_m2_yoy_pct": numbers[3],
                "current_spread_pp": current_spread,
                "previous_spread_pp": previous_spread,
                "spread_delta_pp": delta,
            }

    current_financing = _positive(leverage_raw.get("current_financing_balance"))
    previous_financing = _positive(leverage_raw.get("previous_financing_balance"))
    circ_mv = _positive(leverage_raw.get("a_share_circ_mv"))
    ratio = (
        current_financing / circ_mv * 100
        if current_financing is not None and circ_mv is not None
        else None
    )
    growth = (
        (current_financing / previous_financing - 1) * 100
        if current_financing is not None and previous_financing is not None
        else None
    )
    ratio_hot = (
        ratio is not None
        and _greater_than(ratio, float(thresholds["leverage_ratio_pct"]))
    )
    growth_hot = (
        growth is not None
        and _greater_than(
            growth,
            float(thresholds["financing_monthly_growth_pct"]),
        )
    )
    if ratio_hot or growth_hot:
        leverage_status = "overheated"
    elif ratio is not None and growth is not None:
        leverage_status = "healthy"
    else:
        leverage_status = "unknown"
    leverage = {
        "status": leverage_status,
        "trade_date": leverage_raw.get("trade_date"),
        "previous_trade_date": leverage_raw.get("previous_trade_date"),
        "current_financing_balance": current_financing,
        "previous_financing_balance": previous_financing,
        "a_share_circ_mv": circ_mv,
        "ratio_pct": ratio,
        "monthly_growth_pct": growth,
    }

    if leverage_status == "overheated":
        report_mode = "classified"
        market_state = "过热警报"
    elif (
        household["status"] == "unknown"
        or nonbank["status"] == "unknown"
        or money["status"] == "unknown"
        or leverage_status != "healthy"
    ):
        report_mode = "data_observation"
        market_state = None
    else:
        positive_count = sum(
            (
                household["status"] == "relocation_signal",
                nonbank["status"] == "rising",
                money["status"] == "improving",
            )
        )
        report_mode = "classified"
        market_state = (
            "牛市加速"
            if positive_count == 3
            else "慢牛蓄力"
            if positive_count == 2
            else "震荡磨底"
        )
    return {
        "report_month": snapshot["report_month"],
        "macro_month": macro.get("macro_month"),
        "report_mode": report_mode,
        "market_state": market_state,
        "household": household,
        "nonbank": nonbank,
        "money_supply": money,
        "leverage": leverage,
        "failures": list(snapshot.get("failures", [])),
        "sources": list(snapshot.get("sources", [])),
    }
```

- [ ] **Step 4: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_monthly_macro_flow.py -v
.venv/bin/ruff check src/lurker/application/monthly_macro_flow.py tests/test_monthly_macro_flow.py
git add src/lurker/application/monthly_macro_flow.py tests/test_monthly_macro_flow.py
git commit -m "feat: classify monthly macro flow states"
```

---

### Task 8: 报告渲染与策略注册

**Files:**
- Create: `src/lurker/reports/monthly_macro_flow_report.py`
- Create: `tests/test_monthly_macro_flow_report.py`
- Modify: `src/lurker/application/strategy_runner.py`
- Modify: `configs/strategies.yaml`
- Modify: `tests/test_strategy_runner.py`

- [ ] **Step 1: 写报告 RED**

创建 `tests/test_monthly_macro_flow_report.py`：

```python
from lurker.application.monthly_macro_flow import analyze_monthly_macro_flow
from lurker.reports.monthly_macro_flow_report import (
    render_monthly_macro_flow_report,
)


def complete_snapshot() -> dict:
    return {
        "schema_version": 1,
        "report_month": "2025-01",
        "generated_at": "2026-07-26T12:00:00+00:00",
        "macro": {
            "macro_month": "2025-01",
            "household": {
                "current": 111.0,
                "previous_month": 109.0,
                "previous_year": 100.0,
                "previous_year_previous_month": 100.0,
            },
            "nonbank": {"current": 21.0, "previous_month": 20.0},
            "money_supply": {
                "current_m1_yoy_pct": 5.0,
                "current_m2_yoy_pct": 7.0,
                "previous_m1_yoy_pct": 4.0,
                "previous_m2_yoy_pct": 7.0,
            },
            "failures": [],
        },
        "leverage": {
            "trade_date": "2025-01-30",
            "current_financing_balance": 200.0,
            "previous_trade_date": "2024-12-31",
            "previous_financing_balance": 190.0,
            "a_share_circ_mv": 10_000.0,
            "failure": None,
        },
        "thresholds": {
            "household_deposit_yoy_pct": 12.0,
            "leverage_ratio_pct": 4.0,
            "financing_monthly_growth_pct": 20.0,
        },
        "sources": [],
        "failures": [],
    }


def test_report_discloses_dates_values_sources_and_quality():
    snapshot = complete_snapshot()
    snapshot["sources"] = [
        {
            "url": "https://www.pbc.gov.cn/table",
            "sha256": "sha256:abc",
            "retrieved_at": "2026-07-26T12:00:00+00:00",
        }
    ]
    analysis = analyze_monthly_macro_flow(snapshot)
    report = render_monthly_macro_flow_report(snapshot, analysis)

    assert "# 宏观流动性月报" in report.content_md
    assert "报告月份：2025-01" in report.content_md
    assert "生成时间：2026-07-26T12:00:00+00:00" in report.content_md
    assert "宏观数据截止月：2025-01" in report.content_md
    assert "杠杆数据截止日：2025-01-30" in report.content_md
    assert "## 居民存款趋势" in report.content_md
    assert "## 非银存款" in report.content_md
    assert "## M1-M2 活钱指标" in report.content_md
    assert "## 杠杆水位" in report.content_md
    assert "来源：中国人民银行《金融机构人民币信贷收支表》" in report.content_md
    assert "来源：AkShare macro_china_money_supply" in report.content_md
    assert "来源：沪深融资余额与沪深交易所市场概况" in report.content_md
    assert "上月杠杆基准日：2024-12-31" in report.content_md
    assert "sha256:abc" in report.content_md


def test_data_observation_report_does_not_claim_trend():
    snapshot = complete_snapshot()
    snapshot["macro"]["household"] = None
    analysis = analyze_monthly_macro_flow(snapshot)
    report = render_monthly_macro_flow_report(snapshot, analysis)
    assert "数据不足，仅展示观察事实，不形成趋势结论。" in report.content_md
    assert "牛市加速" not in report.content_md
    assert "慢牛蓄力" not in report.content_md


def test_report_lists_each_failure_reason():
    snapshot = complete_snapshot()
    snapshot["failures"] = [
        {"source": "macro", "reason": "PBOC timeout"},
        {"source": "leverage", "reason": "margin stale"},
    ]
    report = render_monthly_macro_flow_report(
        snapshot,
        analyze_monthly_macro_flow(snapshot),
    )
    assert "macro：PBOC timeout" in report.content_md
    assert "leverage：margin stale" in report.content_md
```

- [ ] **Step 2: 写策略 RED**

在 `tests/test_strategy_runner.py` 增加：

```python
import pytest

from lurker.application.strategy_runner import DEFAULT_STRATEGIES


def test_monthly_macro_flow_strategy_is_registered():
    assert "monthly_macro_flow" in DEFAULT_STRATEGIES


def test_monthly_macro_flow_strategy_requires_its_own_snapshot():
    context = StrategyContext(
        snapshot_batch={"snapshots": []},
        theme_mapping={},
        report_date="2025-01",
        attributor=None,
        suppressed_symbols=set(),
    )
    with pytest.raises(ValueError, match="monthly_macro_snapshot"):
        DEFAULT_STRATEGIES["monthly_macro_flow"].run(
            context,
            StrategyConfig(name="monthly_macro_flow", cadence="monthly"),
        )
```

- [ ] **Step 3: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest \
  tests/test_monthly_macro_flow_report.py \
  tests/test_strategy_runner.py -k "monthly_macro" -v
```

Expected: renderer and strategy are missing.

- [ ] **Step 4: 实现 renderer**

创建 `src/lurker/reports/monthly_macro_flow_report.py`。renderer 接收已经计算好的
`analysis`，不能再次分类：

```python
from __future__ import annotations

from typing import Any

from lurker.reports.models import DailyReport


def _number(value: Any, suffix: str = "") -> str:
    return "unknown" if value is None else f"{float(value):.2f}{suffix}"


def render_monthly_macro_flow_report(
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
) -> DailyReport:
    observation = analysis["report_mode"] == "data_observation"
    conclusion = (
        "数据不足，仅展示观察事实，不形成趋势结论。"
        if observation
        else f"本月状态：{analysis['market_state']}。"
    )
    household = analysis["household"]
    nonbank = analysis["nonbank"]
    money = analysis["money_supply"]
    leverage = analysis["leverage"]
    failures = analysis["failures"]
    quality_lines = (
        [f"- {item['source']}：{item['reason']}" for item in failures]
        if failures
        else ["- 所有必要数据均通过契约校验。"]
    )
    source_lines = [
        f"- {item.get('url', item.get('source', 'unknown'))}；"
        f"数据截止：{item.get('data_date', 'unknown')}；"
        f"获取：{item.get('retrieved_at', 'unknown')}；"
        f"哈希：{item.get('sha256', 'unknown')}"
        for item in analysis["sources"]
    ]
    if not source_lines:
        source_lines = ["- 无可用来源元数据。"]
    content = "\n".join(
        [
            "# 宏观流动性月报",
            "",
            f"报告月份：{analysis['report_month']}",
            f"生成时间：{snapshot['generated_at']}",
            f"宏观数据截止月：{analysis['macro_month'] or 'unknown'}",
            f"杠杆数据截止日：{leverage.get('trade_date') or 'unknown'}",
            "",
            "## 一句话结论",
            "",
            conclusion,
            "",
            "## 牛市进度条",
            "",
            f"- 报告模式：{analysis['report_mode']}",
            f"- 市场状态：{analysis['market_state'] or 'unknown'}",
            "",
            "## 居民存款趋势",
            "",
            f"- 截止月：{analysis['macro_month'] or 'unknown'}",
            "- 来源：中国人民银行《金融机构人民币信贷收支表》",
            f"- 状态：{household['status']}",
            f"- 当前余额：{_number(household.get('current'), '亿元')}",
            f"- 上月余额：{_number(household.get('previous_month'), '亿元')}",
            f"- 同比：{_number(household.get('yoy_pct'), '%')}",
            f"- 同比变化：{_number(household.get('yoy_change_pp'), '个百分点')}",
            "",
            "## 非银存款",
            "",
            f"- 截止月：{analysis['macro_month'] or 'unknown'}",
            "- 来源：中国人民银行《金融机构人民币信贷收支表》",
            f"- 状态：{nonbank['status']}",
            f"- 当前余额：{_number(nonbank.get('current'), '亿元')}",
            f"- 上月余额：{_number(nonbank.get('previous_month'), '亿元')}",
            f"- 环比变化额：{_number(nonbank.get('mom_amount'), '亿元')}",
            f"- 环比：{_number(nonbank.get('mom_pct'), '%')}",
            "",
            "## M1-M2 活钱指标",
            "",
            f"- 截止月：{analysis['macro_month'] or 'unknown'}",
            "- 来源：AkShare macro_china_money_supply",
            f"- 状态：{money['status']}",
            f"- 当前 M1 同比：{_number(money.get('current_m1_yoy_pct'), '%')}",
            f"- 当前 M2 同比：{_number(money.get('current_m2_yoy_pct'), '%')}",
            f"- 上月 M1 同比：{_number(money.get('previous_m1_yoy_pct'), '%')}",
            f"- 上月 M2 同比：{_number(money.get('previous_m2_yoy_pct'), '%')}",
            f"- 当前剪刀差：{_number(money.get('current_spread_pp'), '个百分点')}",
            f"- 较上月变化：{_number(money.get('spread_delta_pp'), '个百分点')}",
            "",
            "## 杠杆水位",
            "",
            f"- 截止日：{leverage.get('trade_date') or 'unknown'}",
            f"- 上月杠杆基准日："
            f"{leverage.get('previous_trade_date') or 'unknown'}",
            "- 来源：沪深融资余额与沪深交易所市场概况",
            f"- 状态：{leverage['status']}",
            f"- 当前融资余额："
            f"{_number(leverage.get('current_financing_balance'), '元')}",
            f"- 上月融资余额："
            f"{_number(leverage.get('previous_financing_balance'), '元')}",
            f"- A 股流通市值："
            f"{_number(leverage.get('a_share_circ_mv'), '元')}",
            f"- 融资余额/流通市值：{_number(leverage.get('ratio_pct'), '%')}",
            f"- 融资余额月增速：{_number(leverage.get('monthly_growth_pct'), '%')}",
            "",
            "## 数据质量",
            "",
            *quality_lines,
            "",
            "### 来源",
            "",
            *source_lines,
            "",
        ]
    )
    return DailyReport(
        report_date=analysis["report_month"],
        main_candidates_count=0,
        content_md=content,
    )
```

- [ ] **Step 5: 注册策略**

在 `StrategyContext` 增加：

```python
monthly_macro_snapshot: dict[str, Any] | None = None
```

在 `strategy_runner.py` 增加并注册：

```python
class MonthlyMacroFlowStrategy:
    name = "monthly_macro_flow"

    def run(self, context: StrategyContext, config: StrategyConfig) -> StrategyResult:
        from lurker.application.monthly_macro_flow import analyze_monthly_macro_flow
        from lurker.reports.monthly_macro_flow_report import (
            render_monthly_macro_flow_report,
        )

        if context.monthly_macro_snapshot is None:
            raise ValueError("monthly_macro_flow requires monthly_macro_snapshot")
        analysis = analyze_monthly_macro_flow(context.monthly_macro_snapshot)
        report = render_monthly_macro_flow_report(
            context.monthly_macro_snapshot,
            analysis,
        )
        return StrategyResult(
            name=self.name,
            title=config.title or "宏观流动性月报",
            report=report,
            metadata={
                "cadence": config.cadence,
                "universe": config.universe,
                "analysis": analysis,
            },
        )
```

在 `DEFAULT_STRATEGIES` 字典定义完成后注册，保留已有日报、Legacy 和周报项：

```python
DEFAULT_STRATEGIES[MonthlyMacroFlowStrategy.name] = MonthlyMacroFlowStrategy()
```

在 `configs/strategies.yaml` 增加：

```yaml
  monthly_macro_flow:
    enabled: true
    cadence: monthly
    universe: macro
    title: 宏观流动性月报
    params: {}
```

- [ ] **Step 6: GREEN 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest \
  tests/test_monthly_macro_flow_report.py \
  tests/test_strategy_runner.py -k "monthly_macro" -v
.venv/bin/ruff check \
  src/lurker/reports/monthly_macro_flow_report.py \
  src/lurker/application/strategy_runner.py \
  tests/test_monthly_macro_flow_report.py \
  tests/test_strategy_runner.py
git add \
  src/lurker/reports/monthly_macro_flow_report.py \
  src/lurker/application/strategy_runner.py \
  configs/strategies.yaml \
  tests/test_monthly_macro_flow_report.py \
  tests/test_strategy_runner.py
git commit -m "feat: render and register monthly macro flow"
```

---

### Task 9: 独立 CLI、推送门与真实数据验收

**Files:**
- Modify: `src/lurker/cli.py`
- Modify: `tests/test_cli.py`
- Verify: `configs/macro_monthly.yaml`

- [ ] **Step 1: 写 parser/job RED**

在 `tests/test_cli.py` 增加：

```python
from pathlib import Path

import pytest

from lurker.cli import build_parser, monthly_macro_flow_job


class FakeNotifier:
    def __init__(self, sends: list[tuple[str, str]]) -> None:
        self.sends = sends

    def send(self, title: str, markdown_content: str) -> None:
        self.sends.append((title, markdown_content))


def fixture_monthly_config(tmp_path: Path) -> Path:
    path = tmp_path / "macro_monthly.yaml"
    path.write_text(
        """
schema_version: 1
pboc:
  credit_table_urls:
    "2024": "https://www.pbc.gov.cn/2024.htm"
  allowed_hosts: [www.pbc.gov.cn]
  timeout_seconds: 30
  max_response_bytes: 1000000
thresholds:
  household_deposit_yoy_pct: 12
  leverage_ratio_pct: 4
  financing_monthly_growth_pct: 20
freshness:
  macro_max_lag_months: 2
  leverage_max_lag_trading_days: 3
""",
        encoding="utf-8",
    )
    return path


def fixture_monthly_strategy(tmp_path: Path, *, enabled: bool = True) -> Path:
    path = tmp_path / "strategies.yaml"
    path.write_text(
        "\n".join(
            [
                "strategies:",
                "  monthly_macro_flow:",
                f"    enabled: {str(enabled).lower()}",
                "    cadence: monthly",
                "    universe: macro",
                "    title: 宏观流动性月报",
                "    params: {}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def complete_snapshot() -> dict:
    return {
        "schema_version": 1,
        "report_month": "2025-01",
        "generated_at": "2026-07-26T12:00:00+00:00",
        "macro": {
            "macro_month": "2025-01",
            "household": {
                "current": 111.0,
                "previous_month": 109.0,
                "previous_year": 100.0,
                "previous_year_previous_month": 100.0,
            },
            "nonbank": {"current": 21.0, "previous_month": 20.0},
            "money_supply": {
                "current_m1_yoy_pct": 5.0,
                "current_m2_yoy_pct": 7.0,
                "previous_m1_yoy_pct": 4.0,
                "previous_m2_yoy_pct": 7.0,
            },
            "failures": [],
        },
        "leverage": {
            "trade_date": "2025-01-30",
            "current_financing_balance": 200.0,
            "previous_trade_date": "2024-12-31",
            "previous_financing_balance": 190.0,
            "a_share_circ_mv": 10_000.0,
            "failure": None,
        },
        "thresholds": {
            "household_deposit_yoy_pct": 12.0,
            "leverage_ratio_pct": 4.0,
            "financing_monthly_growth_pct": 20.0,
        },
        "sources": [],
        "failures": [],
    }


def test_parser_has_monthly_macro_flow_command():
    args = build_parser().parse_args(
        ["monthly-macro-flow", "--month", "2025-01", "--no-push"]
    )
    assert args.command == "monthly-macro-flow"
    assert args.month == "2025-01"
    assert args.no_push is True
    assert args.config.name == "macro_monthly.yaml"
    assert args.snapshot_dir.name == "monthly_macro_flow_snapshots"
    assert args.report_dir.name == "monthly_macro_flow"


def test_monthly_macro_no_push_never_builds_notifier(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lurker.cli.build_notifier_from_env",
        lambda: (_ for _ in ()).throw(AssertionError("must not build notifier")),
    )
    message = monthly_macro_flow_job(
        report_month="2025-01",
        config_path=fixture_monthly_config(tmp_path),
        snapshot_dir=tmp_path / "snapshots",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        strategy_config_path=fixture_monthly_strategy(tmp_path),
        push=False,
        snapshot_collector=lambda **kwargs: complete_snapshot(),
    )
    assert "push=skipped(--no-push)" in message


def test_monthly_macro_observation_does_not_push(monkeypatch, tmp_path):
    sends = []
    monkeypatch.setattr(
        "lurker.cli.build_notifier_from_env",
        lambda: FakeNotifier(sends),
    )
    snapshot = complete_snapshot()
    snapshot["macro"]["household"] = None
    message = monthly_macro_flow_job(
        report_month="2025-01",
        config_path=fixture_monthly_config(tmp_path),
        snapshot_dir=tmp_path / "snapshots",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        strategy_config_path=fixture_monthly_strategy(tmp_path),
        push=True,
        snapshot_collector=lambda **kwargs: snapshot,
    )
    assert sends == []
    assert "push=skipped(data_observation)" in message


def test_monthly_macro_classified_pushes_daily_recipient(monkeypatch, tmp_path):
    sends = []
    monkeypatch.setattr(
        "lurker.cli.build_notifier_from_env",
        lambda: FakeNotifier(sends),
    )
    monthly_macro_flow_job(
        report_month="2025-01",
        config_path=fixture_monthly_config(tmp_path),
        snapshot_dir=tmp_path / "snapshots",
        raw_dir=tmp_path / "raw",
        report_dir=tmp_path / "reports",
        strategy_config_path=fixture_monthly_strategy(tmp_path),
        push=True,
        snapshot_collector=lambda **kwargs: complete_snapshot(),
    )
    assert sends[0][0] == "Lurker 宏观流动性月报 (2025-01)"


def test_monthly_macro_rejects_future_month(tmp_path):
    with pytest.raises(ValueError, match="future report month"):
        monthly_macro_flow_job(
            report_month="2099-01",
            config_path=tmp_path / "config.yaml",
            snapshot_dir=tmp_path / "snapshots",
            raw_dir=tmp_path / "raw",
            report_dir=tmp_path / "reports",
            strategy_config_path=tmp_path / "strategies.yaml",
            push=False,
        )


def test_monthly_macro_same_month_overwrites_report(tmp_path):
    kwargs = {
        "report_month": "2025-01",
        "config_path": fixture_monthly_config(tmp_path),
        "snapshot_dir": tmp_path / "snapshots",
        "raw_dir": tmp_path / "raw",
        "report_dir": tmp_path / "reports",
        "strategy_config_path": fixture_monthly_strategy(tmp_path),
        "push": False,
    }
    monthly_macro_flow_job(
        **kwargs,
        snapshot_collector=lambda **values: complete_snapshot(),
    )
    changed = complete_snapshot()
    changed["macro"]["household"]["current"] = 113.0
    monthly_macro_flow_job(
        **kwargs,
        snapshot_collector=lambda **values: changed,
    )
    reports = list((tmp_path / "reports").glob("*.md"))
    assert len(reports) == 1
    assert "13.00%" in reports[0].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "strategy_text",
    [
        "strategies: {}\n",
        """
strategies:
  monthly_macro_flow:
    enabled: false
    cadence: monthly
""",
        """
strategies:
  monthly_macro_flow:
    enabled: true
    cadence: weekly
""",
    ],
)
def test_monthly_macro_requires_enabled_monthly_strategy(
    tmp_path,
    strategy_text,
):
    strategy_path = tmp_path / "strategies.yaml"
    strategy_path.write_text(strategy_text, encoding="utf-8")
    with pytest.raises(ValueError, match="enabled for monthly cadence"):
        monthly_macro_flow_job(
            report_month="2025-01",
            config_path=fixture_monthly_config(tmp_path),
            snapshot_dir=tmp_path / "snapshots",
            raw_dir=tmp_path / "raw",
            report_dir=tmp_path / "reports",
            strategy_config_path=strategy_path,
            push=False,
            snapshot_collector=lambda **values: complete_snapshot(),
        )


def test_monthly_macro_collector_programming_error_propagates(tmp_path):
    with pytest.raises(TypeError, match="programmer error"):
        monthly_macro_flow_job(
            report_month="2025-01",
            config_path=fixture_monthly_config(tmp_path),
            snapshot_dir=tmp_path / "snapshots",
            raw_dir=tmp_path / "raw",
            report_dir=tmp_path / "reports",
            strategy_config_path=fixture_monthly_strategy(tmp_path),
            push=False,
            snapshot_collector=lambda **values: (_ for _ in ()).throw(
                TypeError("programmer error")
            ),
        )
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py -k monthly_macro -v
```

Expected: missing command/job.

- [ ] **Step 3: 实现原子报告写入与 job**

在 `src/lurker/cli.py` 增加：

```python
import os
import tempfile

from lurker.config import load_monthly_macro_config
from lurker.ingest.macro_monthly import (
    MonthlyMacroSnapshotStore,
    collect_monthly_macro_snapshot,
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
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _validate_report_month(value: str | None) -> str:
    resolved = value or date.today().strftime("%Y-%m")
    try:
        parsed = date.fromisoformat(f"{resolved}-01")
    except ValueError as exc:
        raise ValueError("report month must use YYYY-MM") from exc
    if parsed > date.today().replace(day=1):
        raise ValueError("future report month is not allowed")
    return resolved


def monthly_macro_flow_job(
    *,
    report_month: str | None,
    config_path: Path,
    snapshot_dir: Path,
    raw_dir: Path,
    report_dir: Path,
    strategy_config_path: Path,
    push: bool,
    snapshot_collector=collect_monthly_macro_snapshot,
) -> str:
    resolved = _validate_report_month(report_month)
    monthly_config = load_monthly_macro_config(config_path)
    snapshot = snapshot_collector(
        report_month=resolved,
        config=monthly_config,
        raw_dir=raw_dir,
    )
    snapshot_path = MonthlyMacroSnapshotStore(snapshot_dir).save(snapshot)

    configured = load_strategy_configs(strategy_config_path)
    strategy = configured.get("monthly_macro_flow")
    if (
        strategy is None
        or not strategy.enabled
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
    result = run_strategies(context=context, configs=[strategy])[0]
    analysis = result.metadata["analysis"]
    report_path = report_dir / f"{resolved}.md"
    _atomic_write_text(report_path, result.report.content_md.rstrip() + "\n")

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
        f"state={analysis['market_state'] or 'unknown'}; push={push_status}"
    )
```

Parser：

```python
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
    default=ROOT / "data" / "processed" / "monthly_macro_flow_snapshots",
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
    "--strategy-config",
    type=Path,
    default=ROOT / "configs" / "strategies.yaml",
)
monthly_macro.add_argument("--no-push", action="store_true")
```

在 `main()` 的其他命令之前增加：

```python
if args.command == "monthly-macro-flow":
    print(
        monthly_macro_flow_job(
            report_month=args.month,
            config_path=args.config,
            snapshot_dir=args.snapshot_dir,
            raw_dir=args.raw_dir,
            report_dir=args.report_dir,
            strategy_config_path=args.strategy_config,
            push=not args.no_push,
        )
    )
    return
```

- [ ] **Step 4: GREEN、全量回归和 lint**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py -k monthly_macro -v
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check src tests
git diff --check
```

Expected: all tests pass, ruff and diff check clean.

- [ ] **Step 5: 提交 CLI 集成**

```bash
git add src/lurker/cli.py tests/test_cli.py
git commit -m "feat: add independent monthly macro flow CLI"
```

- [ ] **Step 6: 真实历史数据验收**

先演练，不推送：

```bash
PYTHONPATH=src .venv/bin/python -m lurker.cli monthly-macro-flow \
  --month 2025-01 \
  --config configs/macro_monthly.yaml \
  --no-push
```

如果本机证书链需要显式 CA，只设置可信 CA：

```bash
REQUESTS_CA_BUNDLE=/absolute/path/to/trusted-ca.pem \
PYTHONPATH=src .venv/bin/python -m lurker.cli monthly-macro-flow \
  --month 2025-01 \
  --config configs/macro_monthly.yaml \
  --no-push
```

禁止使用 `verify=False`。

核对 `data/processed/monthly_macro_flow_snapshots/2025-01.json`：

```text
住户存款 2024-01：1395218.75 亿元
住户存款 2024-12：1512509.36 亿元
住户存款 2025-01：1567675.44 亿元
居民存款同比：12.36054848%
非银存款 2024-12：281865.20 亿元
非银存款 2025-01：270772.45 亿元
非银环比变化额：-11092.75 亿元
非银环比：-3.93548051%
M1 同比 2024-12：-1.4%
M2 同比 2024-12：7.3%
M1 同比 2025-01：0.4%
M2 同比 2025-01：7.0%
M1-M2 剪刀差变化：+2.1 个百分点
```

核对杠杆：

1. 快照中沪深融资余额截止日相同；
2. 上月基准是 2024-12 的最后共同交易日；
3. 上交所仅计主板 A 和科创板；
4. 深交所仅计主板 A 股和创业板 A 股；
5. 手算 `financing_balance / a_share_circ_mv * 100` 与报告一致；
6. 报告状态预期为 `震荡磨底`，前提是两条杠杆红线均未触发；
7. 命令输出 `push=skipped(--no-push)`。

保留原始央行 payload、规范化快照和报告作为人工验收证据，但不要提交
`data/raw/`、`data/processed/` 或 `data/reports/`。

- [ ] **Step 7: 最终验证提交**

```bash
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check src tests
git status --short
git log --oneline -10
```

Expected:

- 全量测试通过；
- lint 通过；
- 除 gitignore 下的验收产物外工作树干净；
- 九个任务均有独立提交；
- `monthly-macro-flow --no-push` 不构建通知器；
- 数据观察报告不推送；
- 不存在 M1-M2 代理非银或总市值估算分母。

---

## Spec 覆盖矩阵

| 设计要求 | 实施任务 |
|---|---|
| 央行直接居民/非银余额 | Task 2、3 |
| HTML、XLS、XLSX；拒绝 PDF | Task 2 |
| URL host、大小、hash、原始缓存 | Task 1、3 |
| 跨年 14 个月与修订冲突 | Task 3、4 |
| M1-M2 独立且月份对齐 | Task 4 |
| 沪深融资同日 | Task 5 |
| 交易所 A 股流通市值、排除 B 股 | Task 5 |
| 4%/20% 边界和过热优先 | Task 7 |
| 缺失为 observation、不计零分 | Task 6、7、8 |
| 快照与报告同月原子覆盖 | Task 6、9 |
| 独立 CLI、日报接收人、`--no-push` | Task 9 |
| 四档状态与数据质量披露 | Task 7、8 |
| 真实数据验收 | Task 9 |
