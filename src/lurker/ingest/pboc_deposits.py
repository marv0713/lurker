from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from lurker.config import MonthlyMacroConfig


class PbocSourceError(RuntimeError):
    pass


class PbocSchemaError(PbocSourceError):
    pass


@dataclass(frozen=True)
class RawHttpResponse:
    status_code: int
    content_type: str
    body: bytes


HttpFetcher = Callable[[str, int, int], RawHttpResponse]

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
            charset_match = re.search(
                r"charset=([A-Za-z0-9_-]+)",
                content_type,
                re.IGNORECASE,
            )
            encodings = (
                [charset_match.group(1)]
                if charset_match
                else ["utf-8", "gb18030"]
            )
            for encoding in encodings:
                try:
                    text = payload.decode(encoding)
                    return pd.read_html(StringIO(text), header=None)
                except UnicodeDecodeError:
                    continue
            raise PbocSchemaError("cannot decode PBOC HTML")
        if normalized in {
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/octet-stream",
        }:
            return [pd.read_excel(BytesIO(payload), header=None)]
    except (ValueError, ImportError, OSError) as exc:
        raise PbocSchemaError(f"cannot parse PBOC table: {exc}") from exc
    raise PbocSchemaError(f"unsupported content type: {content_type}")


def _target_row(
    table: pd.DataFrame,
    pattern: re.Pattern[str],
    label: str,
) -> int:
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
    first_target_row = min(household_row, nonbank_row)
    for row_index in range(first_target_row):
        found: dict[int, str] = {}
        for column_index, value in enumerate(table.iloc[row_index].tolist()):
            match = _MONTH.fullmatch(_compact(value))
            if match:
                found[column_index] = f"{match.group(1)}-{match.group(2)}"
        if found:
            if month_columns:
                raise PbocSchemaError("multiple month header rows")
            month_columns = found
    if not month_columns:
        raise PbocSchemaError("month header row is missing")

    result: dict[str, dict[str, float]] = {
        "household": {},
        "nonbank": {},
    }
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
                raise PbocSchemaError(
                    f"{name} {month} must be finite positive"
                )
            result[name][month] = number
    if not result["household"] or not result["nonbank"]:
        raise PbocSchemaError(f"no published deposit values in {source_url}")
    return result


def _requests_fetch(url: str, timeout: int, max_bytes: int) -> RawHttpResponse:
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        declared = response.headers.get("content-length")
        if declared:
            try:
                declared_size = int(declared)
            except ValueError as exc:
                raise PbocSourceError(
                    "invalid PBOC content-length"
                ) from exc
            if declared_size > max_bytes:
                raise PbocSourceError(
                    "PBOC response exceeds max_response_bytes"
                )
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise PbocSourceError(
                    "PBOC response exceeds max_response_bytes"
                )
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
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def merge_deposit_tables(
    tables: list[dict[str, dict[str, float]]],
) -> dict[str, object]:
    merged: dict[str, dict[str, float]] = {
        "household": {},
        "nonbank": {},
    }
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
    clock = now_iso or (lambda: pd.Timestamp.now(tz="UTC").isoformat())
    tables: list[dict[str, dict[str, float]]] = []
    sources: list[dict[str, object]] = []
    for year, url in sorted(config.credit_table_urls.items()):
        response = fetcher(
            url,
            config.timeout_seconds,
            config.max_response_bytes,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise PbocSourceError(
                f"PBOC HTTP status {response.status_code}"
            )
        if len(response.body) > config.max_response_bytes:
            raise PbocSourceError(
                "PBOC response exceeds max_response_bytes"
            )
        digest = hashlib.sha256(response.body).hexdigest()
        path = (
            Path(raw_dir)
            / f"{year}-{digest}.{_extension(response.content_type)}"
        )
        if not path.exists():
            _atomic_write_bytes(path, response.body)
        parsed = parse_pboc_credit_table(
            response.body,
            content_type=response.content_type,
            source_url=url,
        )
        unexpected = {
            month
            for values in parsed.values()
            for month in values
            if not month.startswith(f"{year}-")
        }
        if unexpected:
            raise PbocSchemaError(
                f"configured year {year} contains month "
                f"{sorted(unexpected)[0]}"
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
