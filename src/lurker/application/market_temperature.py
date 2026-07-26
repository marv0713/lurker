"""Market temperature classification — tristate ETF, four-state margin, freshness preparation.

Pure domain logic with no I/O. All functions are deterministic and testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, timedelta
from typing import Any, Callable

from lurker.ingest.etf_flows import CoreEtfBatch


# ---------------------------------------------------------------------------
# Flow direction
# ---------------------------------------------------------------------------


def _flow_direction(value: Any) -> str:
    """positive | negative | neutral | unknown (NaN/None/inf → unknown)."""
    if value is None:
        return "unknown"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if math.isnan(v) or math.isinf(v):
        return "unknown"
    if v > 0:
        return "positive"
    if v < 0:
        return "negative"
    return "neutral"


# ---------------------------------------------------------------------------
# Margin signal (four-state)
# ---------------------------------------------------------------------------


def classify_margin_signal(margin: dict[str, Any]) -> str:
    """supportive | weakening | overheated | unknown.

    - Empty dict / missing change / non-finite change → unknown
    - availability == "stale_cache" → unknown (stale data provides no evidence)
    - change > 0 → supportive
    - change < 0 → weakening
    - change == 0 → unknown (flat balance provides no direction evidence)
    - overheated is always "unknown" in this phase (denominator data pending replay)
    """
    if not isinstance(margin, dict) or not margin:
        return "unknown"

    # Stale cache data cannot provide direction evidence
    if margin.get("availability") == "stale_cache":
        return "unknown"

    change = margin.get("margin_balance_change")
    if change is None:
        return "unknown"

    try:
        v = float(change)
    except (TypeError, ValueError):
        return "unknown"

    if math.isnan(v) or math.isinf(v):
        return "unknown"

    # overheated: not implemented in this phase
    # Future: financing_balance / sum(circ_mv) > threshold

    if v > 0:
        return "supportive"
    if v < 0:
        return "weakening"
    # v == 0
    return "unknown"


# ---------------------------------------------------------------------------
# ETF aggregate tristate
# ---------------------------------------------------------------------------


def classify_etf_status(
    batch: CoreEtfBatch,
    *,
    threshold: float = 1.2,
) -> str:
    """active | inactive | unknown.

    Precondition: batch.is_complete() == True, otherwise returns "unknown".

    - Any valid item with turnover_expansion >= threshold → active
    - All configured ETFs valid and all below threshold → inactive
    - Any failures, incompleteness, or no valid data → unknown
    """
    # No configured symbols at all
    if not batch.configured_symbols:
        return "unknown"

    # Incomplete batch (fetcher missed some configured symbols)
    if not batch.is_complete():
        return "unknown"

    # All failed
    if not batch.items and batch.failures:
        return "unknown"

    # No items and no failures → never collected
    if not batch.items and not batch.failures:
        return "unknown"

    # Check for any active items
    has_any_failure = len(batch.failures) > 0
    all_valid = True

    for item in batch.items:
        is_valid = (
            item.status in {"active", "inactive"}
            and item.availability == "turnover_only"
            and item.turnover_expansion is not None
            and math.isfinite(item.turnover_expansion)
        )
        if not is_valid:
            all_valid = False
            continue
        if item.turnover_expansion >= threshold:
            # Even one active item decides the result
            return "active"

    # If there are failures and no active item, we cannot conclude "inactive"
    if has_any_failure:
        return "unknown"

    # If not all configured ETFs have valid items, cannot conclude "inactive"
    if not all_valid:
        return "unknown"

    # All ETFs valid and none above threshold → inactive
    return "inactive"


# ---------------------------------------------------------------------------
# Market temperature truth table (总设计 §5.3)
# ---------------------------------------------------------------------------


def classify_market_temperature(
    *,
    market_flow: dict[str, Any],
    etf_status: str,
    margin_signal: str,
) -> str:
    """三档市场温度：进攻 / 观察 / 防守.

    Truth table (总设计 §5.3):
    | 大盘主力+超大单   | ETF/两融确认                      | 结果 |
    | 同为正           | ETF active 或 两融 supportive      | 进攻 |
    | 同为负           | ETF inactive 或 两融 weakening     | 防守 |
    | 同为正或负       | ETF、两融均 unknown               | 观察 |
    | 方向不一致       | 任意                              | 观察 |
    | 任意             | 两融 overheated                   | 防守 |
    """
    # --- Overheated always defense ---
    if margin_signal == "overheated":
        return "防守"

    # --- Determine flow direction consensus ---
    main_dir = _flow_direction(market_flow.get("main_net_inflow"))
    super_dir = _flow_direction(market_flow.get("super_large_net_inflow"))

    both_positive = (main_dir == "positive" and super_dir == "positive")
    both_negative = (main_dir == "negative" and super_dir == "negative")

    # --- Dual positive ---
    if both_positive:
        if etf_status == "active" or margin_signal == "supportive":
            return "进攻"
        # Both unknown → observe (missing data is not positive evidence)
        return "观察"

    # --- Dual negative ---
    if both_negative:
        if etf_status == "inactive" or margin_signal == "weakening":
            return "防守"
        # Both unknown → observe (missing data is not negative evidence)
        return "观察"

    # --- Direction mismatch, neutral, or unknown → observe ---
    return "观察"


# ---------------------------------------------------------------------------
# Freshness / expected trade date
# ---------------------------------------------------------------------------


def resolve_expected_trade_date(
    report_date: str,
    *,
    is_trading_day: Callable[[date], bool],
    now: datetime,
    market_close_cutoff: time = time(15, 30),
) -> str:
    """Resolve the most recent completed trading day for the given report date.

    - Historical trading day → that day
    - Today + now >= cutoff (Asia/Shanghai) → today
    - Today + now < cutoff → previous trading day
    - Weekend/holiday → most recent trading day before
    - Future date → ValueError
    """
    # Parse report_date
    try:
        report_dt = date.fromisoformat(report_date)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid report_date: {report_date}")

    # Convert now to Asia/Shanghai
    cst = timezone(timedelta(hours=8))
    now_cst = now.astimezone(cst)
    today_cst = now_cst.date()

    # Future date
    if report_dt > today_cst:
        raise ValueError(f"report_date {report_date} is in the future")

    # Historical date or today after cutoff
    if report_dt < today_cst:
        if is_trading_day(report_dt):
            return report_dt.isoformat()
        # Walk back to most recent trading day
        cursor = report_dt
        while not is_trading_day(cursor):
            cursor = cursor - timedelta(days=1)
        return cursor.isoformat()

    # Today: check cutoff
    if now_cst.time() >= market_close_cutoff:
        if is_trading_day(today_cst):
            return today_cst.isoformat()
        # Today is not a trading day → walk back
        cursor = today_cst
        while not is_trading_day(cursor):
            cursor = cursor - timedelta(days=1)
        return cursor.isoformat()

    # Before cutoff → previous trading day
    cursor = today_cst - timedelta(days=1)
    while not is_trading_day(cursor):
        cursor = cursor - timedelta(days=1)
    return cursor.isoformat()


def _normalize_trade_date(date_str: str) -> str:
    """Normalize trade date to ISO format YYYY-MM-DD.

    Accepts: "2026-07-23", "20260723", "2026-07-23T00:00:00", etc.
    """
    if not date_str:
        return ""
    # Already ISO
    if len(date_str) == 10 and date_str[4] == "-":
        return date_str
    # YYYYMMDD
    if len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    # Try to parse as ISO date with optional time
    try:
        return date.fromisoformat(date_str[:10]).isoformat()
    except (ValueError, TypeError):
        return date_str


# ---------------------------------------------------------------------------
# Preparation layer: unify freshness + classification into single entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedTemperatureInputs:
    """Normalized inputs for market temperature classification."""

    market_flow: dict[str, Any]
    etf_status: str
    margin_signal: str
    expected_trade_date: str
    quality_notes: tuple[str, ...]


def prepare_temperature_inputs(
    *,
    market_flow: dict[str, Any],
    core_etfs_batch: CoreEtfBatch,
    margin: dict[str, Any],
    report_date: str,
    is_trading_day: Callable[[date], bool],
    now: datetime,
    market_close_cutoff: time = time(15, 30),
) -> PreparedTemperatureInputs:
    """Unified preparation: resolve expected trade date, check freshness, classify.

    Stale data is degraded to "unknown" before temperature classification:
    - Stale flow values → set to None (→ _flow_direction returns "unknown")
    - Stale ETF items → filtered out before etf_status classification
    - Stale margin → margin_signal forced to "unknown"
    """
    expected_trade_date = resolve_expected_trade_date(
        report_date=report_date,
        is_trading_day=is_trading_day,
        now=now,
        market_close_cutoff=market_close_cutoff,
    )

    # --- Market flow freshness ---
    flow_trade_date = _normalize_trade_date(market_flow.get("trade_date", ""))
    flow = dict(market_flow)
    if flow_trade_date > expected_trade_date:
        raise ValueError(
            f"Market flow trade_date {flow_trade_date} is after "
            f"expected_trade_date {expected_trade_date}"
        )
    if not flow_trade_date or flow_trade_date < expected_trade_date:
        # Stale or missing date → nullify the flow values so they become "unknown"
        flow["main_net_inflow"] = None
        flow["super_large_net_inflow"] = None
        flow["availability"] = "stale"
    else:
        flow["availability"] = "fresh"

    # --- ETF freshness ---
    for item in core_etfs_batch.items:
        item_trade_date = _normalize_trade_date(item.trade_date)
        if item_trade_date > expected_trade_date:
            raise ValueError(
                f"ETF {item.symbol} trade_date {item_trade_date} is after "
                f"expected_trade_date {expected_trade_date}"
            )

    # Keep only items whose trade_date matches expected_trade_date
    fresh_items = [
        item
        for item in core_etfs_batch.items
        if _normalize_trade_date(item.trade_date) == expected_trade_date
    ]
    if fresh_items:
        fresh_batch = CoreEtfBatch(
            configured_symbols=core_etfs_batch.configured_symbols,
            items=fresh_items,
            failures=core_etfs_batch.failures,
            generated_at=core_etfs_batch.generated_at,
            schema_version=core_etfs_batch.schema_version,
        )
        etf_status = classify_etf_status(fresh_batch)
    elif core_etfs_batch.items:
        # All items are stale → unknown
        etf_status = "unknown"
    else:
        # No items at all → classify as-is (will be unknown)
        etf_status = classify_etf_status(core_etfs_batch)

    # --- Margin freshness ---
    margin_trade_date = _normalize_trade_date(margin.get("trade_date", ""))
    margin_availability = margin.get("availability", "")
    if margin_trade_date > expected_trade_date:
        raise ValueError(
            f"Margin trade_date {margin_trade_date} is after "
            f"expected_trade_date {expected_trade_date}"
        )
    if (
        margin_availability == "stale_cache"
        or not margin_trade_date
        or margin_trade_date < expected_trade_date
    ):
        margin_signal = "unknown"
    else:
        margin_signal = classify_margin_signal(margin)

    market_status = str(flow.get("availability") or "unknown")
    market_cutoff = flow_trade_date or "-"

    etf_dates = [
        _normalize_trade_date(item.trade_date)
        for item in core_etfs_batch.items
        if _normalize_trade_date(item.trade_date)
    ]
    etf_cutoff = max(etf_dates, default="-")
    if core_etfs_batch.failures and fresh_items:
        etf_freshness = "partial"
    elif core_etfs_batch.failures:
        etf_freshness = "unknown"
    elif fresh_items and len(fresh_items) == len(core_etfs_batch.configured_symbols):
        etf_freshness = "fresh"
    elif core_etfs_batch.items:
        etf_freshness = "stale"
    else:
        etf_freshness = "unknown"

    if margin_availability == "stale_cache":
        margin_status = "stale_cache"
    elif margin_trade_date == expected_trade_date:
        margin_status = str(margin_availability or "fresh")
    elif margin_trade_date:
        margin_status = "stale"
    else:
        margin_status = "unknown"
    margin_cutoff = margin_trade_date or "-"

    quality_notes = [
        f"大盘资金：截止 {market_cutoff}，状态 {market_status}",
        f"核心 ETF：截止 {etf_cutoff}，状态 {etf_freshness}",
        f"两融：截止 {margin_cutoff}，状态 {margin_status}",
    ]
    if any(
        status != "fresh"
        for status in (market_status, etf_freshness, margin_status)
    ):
        quality_notes.append("⚠️ 部分数据非当日或采集不完整")

    return PreparedTemperatureInputs(
        market_flow=flow,
        etf_status=etf_status,
        margin_signal=margin_signal,
        expected_trade_date=expected_trade_date,
        quality_notes=tuple(quality_notes),
    )
