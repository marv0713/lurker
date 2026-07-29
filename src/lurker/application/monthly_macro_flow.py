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


def analyze_monthly_macro_flow(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    if snapshot.get("schema_version") != 1:
        raise ValueError(
            "unsupported monthly macro snapshot schema_version"
        )
    thresholds = snapshot["thresholds"]
    macro = snapshot.get("macro") or {}
    household_raw = macro.get("household")
    nonbank_raw = macro.get("nonbank")
    money_raw = macro.get("money_supply")
    leverage_raw = snapshot.get("leverage") or {}

    household: dict[str, Any] = {"status": "unknown"}
    if household_raw:
        current = _positive(household_raw.get("current"))
        previous = _positive(household_raw.get("previous_month"))
        previous_year = _positive(
            household_raw.get("previous_year")
        )
        previous_year_previous = _positive(
            household_raw.get("previous_year_previous_month")
        )
        values = (
            current,
            previous,
            previous_year,
            previous_year_previous,
        )
        if all(value is not None for value in values):
            assert current is not None
            assert previous is not None
            assert previous_year is not None
            assert previous_year_previous is not None
            yoy = (current / previous_year - 1) * 100
            previous_yoy = (
                previous / previous_year_previous - 1
            ) * 100
            threshold = float(
                thresholds["household_deposit_yoy_pct"]
            )
            below = yoy < threshold and not math.isclose(
                yoy,
                threshold,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            household = {
                "status": (
                    "relocation_signal"
                    if below
                    else "deposit_dominant"
                ),
                "current": current,
                "previous_month": previous,
                "yoy_pct": yoy,
                "previous_yoy_pct": previous_yoy,
                "yoy_change_pp": yoy - previous_yoy,
            }

    nonbank: dict[str, Any] = {"status": "unknown"}
    if nonbank_raw:
        current = _positive(nonbank_raw.get("current"))
        previous = _positive(nonbank_raw.get("previous_month"))
        if current is not None and previous is not None:
            amount = current - previous
            flat = math.isclose(
                amount,
                0.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            nonbank = {
                "status": (
                    "flat"
                    if flat
                    else "rising"
                    if amount > 0
                    else "falling"
                ),
                "current": current,
                "previous_month": previous,
                "mom_amount": amount,
                "mom_pct": (current / previous - 1) * 100,
            }

    money: dict[str, Any] = {"status": "unknown"}
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
        if len(numbers) == 4 and all(
            math.isfinite(value) for value in numbers
        ):
            current_spread = numbers[0] - numbers[1]
            previous_spread = numbers[2] - numbers[3]
            delta = current_spread - previous_spread
            flat = math.isclose(
                delta,
                0.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            money = {
                "status": (
                    "flat"
                    if flat
                    else "improving"
                    if delta > 0
                    else "worsening"
                ),
                "current_m1_yoy_pct": numbers[0],
                "current_m2_yoy_pct": numbers[1],
                "previous_m1_yoy_pct": numbers[2],
                "previous_m2_yoy_pct": numbers[3],
                "current_spread_pp": current_spread,
                "previous_spread_pp": previous_spread,
                "spread_delta_pp": delta,
            }

    current_financing = _positive(
        leverage_raw.get("current_financing_balance")
    )
    previous_financing = _positive(
        leverage_raw.get("previous_financing_balance")
    )
    circ_mv = _positive(leverage_raw.get("a_share_circ_mv"))
    ratio = (
        current_financing / circ_mv * 100
        if current_financing is not None and circ_mv is not None
        else None
    )
    growth = (
        (current_financing / previous_financing - 1) * 100
        if current_financing is not None
        and previous_financing is not None
        else None
    )
    ratio_hot = (
        ratio is not None
        and _greater_than(
            ratio,
            float(thresholds["leverage_ratio_pct"]),
        )
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
        "previous_trade_date": leverage_raw.get(
            "previous_trade_date"
        ),
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
