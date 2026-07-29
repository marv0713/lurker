from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from lurker.config import MonthlyMacroConfig
from lurker.ingest.pboc_deposits import (
    PbocSourceError,
    collect_pboc_deposits,
)
from lurker.trading_calendar import is_cn_trading_day


class MonthlySourceError(RuntimeError):
    pass


class MonthlySchemaError(MonthlySourceError):
    pass


@dataclass(frozen=True)
class ExchangeCircMvResult:
    value_yuan: float
    sources: tuple[dict[str, str], ...]


_MONEY_COLUMNS = {
    "月份",
    "货币和准货币(M2)-同比增长",
    "货币(M1)-同比增长",
}


def _month(value: str) -> str:
    match = re.fullmatch(
        r"(20\d{2})年?(0[1-9]|1[0-2])(?:月份?)?",
        str(value).strip(),
    )
    if not match:
        raise MonthlySchemaError(f"invalid month: {value}")
    return f"{match.group(1)}-{match.group(2)}"


def _shift_month(value: str, offset: int) -> str:
    year, month = map(int, value.split("-"))
    ordinal = year * 12 + month - 1 + offset
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def _month_distance(later: str, earlier: str) -> int:
    later_year, later_month = map(int, later.split("-"))
    earlier_year, earlier_month = map(int, earlier.split("-"))
    return (
        (later_year - earlier_year) * 12
        + later_month
        - earlier_month
    )


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MonthlySchemaError(f"{field} must be finite")
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise MonthlySchemaError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise MonthlySchemaError(f"{field} must be finite")
    return result


def normalize_money_supply(
    frame: pd.DataFrame,
) -> dict[str, dict[str, float]]:
    missing = _MONEY_COLUMNS - set(frame.columns)
    if missing:
        raise MonthlySchemaError(
            f"money supply missing columns {sorted(missing)}"
        )
    result: dict[str, dict[str, float]] = {}
    for row in frame.to_dict(orient="records"):
        month = _month(str(row["月份"]))
        if month in result:
            raise MonthlySchemaError(
                f"duplicate money supply month {month}"
            )
        result[month] = {
            "m1_yoy_pct": _finite(
                row["货币(M1)-同比增长"],
                f"{month} m1_yoy_pct",
            ),
            "m2_yoy_pct": _finite(
                row["货币和准货币(M2)-同比增长"],
                f"{month} m2_yoy_pct",
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
    eligible = sorted(
        month for month in common if month <= report_month
    )
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
        month
        for month in household_months
        if month not in deposits["household"]
    ]
    if household_missing:
        household = None
        failures.append(
            f"household missing months {household_missing}"
        )
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
        month
        for month in (selected, previous)
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


def normalize_margin_history(frame: pd.DataFrame) -> dict[date, float]:
    required = {"日期", "融资余额"}
    missing = required - set(frame.columns)
    if missing:
        raise MonthlySchemaError(
            f"margin missing columns {sorted(missing)}"
        )
    result: dict[date, float] = {}
    for row in frame.to_dict(orient="records"):
        parsed = pd.to_datetime(row["日期"], errors="coerce")
        if pd.isna(parsed):
            raise MonthlySchemaError("margin date must be valid")
        trade_date = pd.Timestamp(parsed).date()
        if trade_date in result:
            raise MonthlySchemaError(
                f"duplicate margin date {trade_date}"
            )
        value = _finite(row["融资余额"], f"margin {trade_date}")
        if value < 0:
            raise MonthlySchemaError(
                f"margin {trade_date} must be non-negative"
            )
        result[trade_date] = value
    return result


def _single_row(
    frame: pd.DataFrame,
    column: str,
    value: str,
) -> dict[str, Any]:
    if column not in frame.columns:
        raise MonthlySchemaError(
            f"exchange summary missing column {column}"
        )
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
        raise MonthlySchemaError(
            "exchange summary missing column 流通市值"
        )
    szse_yuan = _finite(
        sz_main["流通市值"],
        "SZSE 主板A股 流通市值",
    ) + _finite(
        sz_chinext["流通市值"],
        "SZSE 创业板A股 流通市值",
    )
    total = sse_yuan + szse_yuan
    if total <= 0:
        raise MonthlySchemaError(
            "A-share circulating market cap must be positive"
        )
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


def _empty_leverage(failure: str) -> dict[str, object]:
    return {
        "trade_date": None,
        "current_financing_balance": None,
        "previous_trade_date": None,
        "previous_financing_balance": None,
        "a_share_circ_mv": None,
        "circ_mv_sources": [],
        "failure": failure,
    }


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
    common = sorted(
        day for day in set(sh) & set(sz) if day <= cutoff
    )
    if not common:
        return _empty_leverage(
            "no common Shanghai/Shenzhen margin date"
        )
    selected = common[-1]
    if (
        _trading_day_lag(selected, cutoff, trading_day_checker)
        > max_lag_trading_days
    ):
        stale = _empty_leverage("margin data is stale")
        stale["trade_date"] = selected.isoformat()
        return stale

    previous_month = _shift_month(report_month, -1)
    previous_year, previous_number = map(
        int,
        previous_month.split("-"),
    )
    previous_common = [
        day
        for day in common
        if day.year == previous_year and day.month == previous_number
    ]
    if not previous_common:
        missing = _empty_leverage(
            "no previous-month common margin date"
        )
        missing["trade_date"] = selected.isoformat()
        missing["current_financing_balance"] = (
            sh[selected] + sz[selected]
        )
        return missing
    previous = previous_common[-1]

    circ_mv_result = circ_mv_fetcher(selected.strftime("%Y%m%d"))
    circ_mv = _finite(
        circ_mv_result.value_yuan,
        f"A-share market cap {selected}",
    )
    if circ_mv <= 0:
        raise MonthlySchemaError(
            "A-share market cap must be positive"
        )
    return {
        "trade_date": selected.isoformat(),
        "current_financing_balance": sh[selected] + sz[selected],
        "previous_trade_date": previous.isoformat(),
        "previous_financing_balance": sh[previous] + sz[previous],
        "a_share_circ_mv": circ_mv,
        "circ_mv_sources": list(circ_mv_result.sources),
        "failure": None,
    }


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

    try:
        sse = ak.stock_sse_deal_daily(date=trade_date)
        szse = ak.stock_szse_summary(date=trade_date)
    except (
        requests.RequestException,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
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
                url="https://www.szse.cn/api/report/ShowReport",
                data_date=trade_date,
                frame=szse,
            ),
        ),
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
    except (
        requests.RequestException,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        raise MonthlySourceError(
            f"money supply source failed: {exc}"
        ) from exc


def _default_margin_sh_fetcher() -> pd.DataFrame:
    import akshare as ak

    try:
        return ak.macro_china_market_margin_sh()
    except (
        requests.RequestException,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        raise MonthlySourceError(
            f"Shanghai margin source failed: {exc}"
        ) from exc


def _default_margin_sz_fetcher() -> pd.DataFrame:
    import akshare as ak

    try:
        return ak.macro_china_market_margin_sz()
    except (
        requests.RequestException,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        raise MonthlySourceError(
            f"Shenzhen margin source failed: {exc}"
        ) from exc


def collect_monthly_macro_snapshot(
    *,
    report_month: str,
    config: MonthlyMacroConfig,
    raw_dir: str | Path,
    pboc_collector=collect_pboc_deposits,
    money_fetcher: Callable[[], pd.DataFrame] = _default_money_fetcher,
    margin_sh_fetcher: Callable[[], pd.DataFrame] = (
        _default_margin_sh_fetcher
    ),
    margin_sz_fetcher: Callable[[], pd.DataFrame] = (
        _default_margin_sz_fetcher
    ),
    circ_mv_fetcher: Callable[[str], ExchangeCircMvResult] = (
        fetch_exchange_circ_mv
    ),
    generated_at: Callable[[], str] | None = None,
    today: date | None = None,
) -> dict[str, object]:
    clock = generated_at or (
        lambda: pd.Timestamp.now(tz="UTC").isoformat()
    )
    collected_at = clock()
    failures: list[dict[str, str]] = []
    sources: list[dict[str, object]] = []
    macro: dict[str, object] = {
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
                str(macro["macro_month"])
                if macro["macro_month"]
                else None,
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

    leverage = _empty_leverage("leverage sources unavailable")
    try:
        sh_frame = margin_sh_fetcher()
        sz_frame = margin_sz_fetcher()
        leverage = build_leverage_facts(
            sh_frame,
            sz_frame,
            report_month=report_month,
            max_lag_trading_days=(
                config.leverage_max_lag_trading_days
            ),
            circ_mv_fetcher=circ_mv_fetcher,
            today=today,
        )
        if leverage["failure"]:
            failures.append(
                {
                    "source": "leverage_alignment",
                    "reason": str(leverage["failure"]),
                }
            )
        sources.extend(
            [
                _normalized_source(
                    "akshare.macro_china_market_margin_sh",
                    "https://cdn.jin10.com/data_center/reports/fs_1.json",
                    str(leverage["trade_date"])
                    if leverage["trade_date"]
                    else None,
                    sh_frame.to_dict(orient="records"),
                    collected_at,
                ),
                _normalized_source(
                    "akshare.macro_china_market_margin_sz",
                    "https://cdn.jin10.com/data_center/reports/fs_2.json",
                    str(leverage["trade_date"])
                    if leverage["trade_date"]
                    else None,
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
            "household_deposit_yoy_pct": (
                config.household_deposit_yoy_pct
            ),
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
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    snapshot,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return path
