from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from lurker.application.professional_flow_daily import classify_market_temperature
from lurker.reports.models import DailyReport


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 999) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_amount(value: float) -> str:
    sign = "-" if value < 0 else ""
    abs_value = abs(value)
    if abs_value >= 100_000_000:
        return f"{sign}{abs_value / 100_000_000:.2f}亿"
    if abs_value >= 10_000:
        return f"{sign}{abs_value / 10_000:.2f}万"
    return f"{value:.2f}"


def _is_noisy_stock(name: str) -> bool:
    normalized = name.strip().upper()
    return normalized.startswith("*ST") or normalized.startswith("ST") or "退市" in name


def _load_latest_snapshots(
    flow_snapshot_dir: Path | str,
    report_date: str,
    lookback_days: int,
) -> tuple[list[tuple[Any, dict[str, Any]]], list[dict[str, Any]]]:
    report_dt = datetime.strptime(report_date, "%Y-%m-%d").date()
    flow_dir = Path(flow_snapshot_dir)
    snapshot_files: list[tuple[Any, Path]] = []
    if flow_dir.exists():
        for path in flow_dir.glob("*.json"):
            if path.name == "latest.json":
                continue
            match = re.match(r"^(\d{4}-\d{2}-\d{2})\.json$", path.name)
            if not match:
                continue
            try:
                file_dt = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_dt <= report_dt:
                snapshot_files.append((file_dt, path))

    snapshot_files.sort(key=lambda item: item[0])
    if lookback_days > 0:
        snapshot_files = snapshot_files[-lookback_days:]

    loaded: list[tuple[Any, dict[str, Any]]] = []
    failures: list[dict[str, Any]] = []
    for file_dt, path in snapshot_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append({"source": path.name, "error": str(exc)})
            continue
        loaded.append((file_dt, data))
        failures.extend(data.get("failures", []))
    return loaded, failures


def _status_counts(loaded_snapshots: list[tuple[Any, dict[str, Any]]]) -> dict[str, int]:
    counts = {"进攻": 0, "观察": 0, "防守": 0}
    for _, data in loaded_snapshots:
        temperature = classify_market_temperature(
            market_flow=data.get("market_flow", {}),
            margin=data.get("margin", {}),
            core_etfs=data.get("core_etfs", []),
        )
        counts[temperature] = counts.get(temperature, 0) + 1
    return counts


def _aggregate_named_flows(
    loaded_snapshots: list[tuple[Any, dict[str, Any]]],
    key: str,
    *,
    skip_stock_noise: bool = False,
) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    for file_dt, data in loaded_snapshots:
        seen_positive_today: set[str] = set()
        for rank_index, item in enumerate(data.get(key, []), start=1):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if skip_stock_noise and _is_noisy_stock(name):
                continue
            inflow = _as_float(item.get("main_net_inflow"))
            rank = _as_int(item.get("rank"), rank_index)
            symbol = str(item.get("symbol") or "").upper()
            row = aggregate.setdefault(
                name,
                {
                    "name": name,
                    "symbol": symbol,
                    "positive_days": 0,
                    "cumulative_inflow": 0.0,
                    "latest_inflow": 0.0,
                    "latest_rank": 999,
                    "latest_date": None,
                },
            )
            if symbol and not row["symbol"]:
                row["symbol"] = symbol
            row["cumulative_inflow"] += inflow
            row["latest_inflow"] = inflow
            row["latest_rank"] = rank
            row["latest_date"] = file_dt
            if inflow > 0 and name not in seen_positive_today:
                row["positive_days"] += 1
                seen_positive_today.add(name)

    rows = [row for row in aggregate.values() if row["positive_days"] > 0]
    rows.sort(
        key=lambda row: (
            -row["positive_days"],
            -row["cumulative_inflow"],
            row["latest_rank"],
            row["name"],
        )
    )
    return rows


def _sector_label(row: dict[str, Any]) -> str:
    if row["latest_inflow"] <= 0:
        return "退潮"
    if row["positive_days"] >= 2:
        return "延续"
    return "新主线"


def build_weekly_flow_report(
    flow_snapshot_dir: Path | str,
    report_date: str,
    lookback_days: int = 5,
    sector_limit: int = 10,
    stock_limit: int = 20,
) -> DailyReport:
    """Aggregates the latest N available A-share flow snapshots into a weekly report."""
    loaded_snapshots, failures = _load_latest_snapshots(
        flow_snapshot_dir=flow_snapshot_dir,
        report_date=report_date,
        lookback_days=lookback_days,
    )

    if not loaded_snapshots:
        return DailyReport(
            report_date=report_date,
            main_candidates_count=0,
            content_md="# 职业资金雷达周报\n\n没有可用资金快照。\n",
        )

    start_date_str = str(loaded_snapshots[0][0])
    end_date_str = str(loaded_snapshots[-1][0])
    status = _status_counts(loaded_snapshots)
    sectors = _aggregate_named_flows(loaded_snapshots, "sector_flows")[:sector_limit]
    stocks = _aggregate_named_flows(
        loaded_snapshots,
        "stock_flows",
        skip_stock_noise=True,
    )[:stock_limit]

    continued = [row["name"] for row in sectors if _sector_label(row) == "延续"]
    new = [row["name"] for row in sectors if _sector_label(row) == "新主线"]
    ebb = [row["name"] for row in sectors if _sector_label(row) == "退潮"]

    lines = [
        "# 职业资金雷达周报",
        "",
        f"**周期范围**: {start_date_str} 至 {end_date_str}",
        f"**快照口径**: 最近 {len(loaded_snapshots)} 个可用交易日资金快照",
        "",
        "## 一句话结论",
    ]
    if sectors:
        leader = sectors[0]
        lines.append(
            f"{leader['name']}是本周最强资金主线，"
            f"正流入 {leader['positive_days']} 天，"
            f"累计主力净流入 {_format_amount(leader['cumulative_inflow'])}。"
        )
    else:
        lines.append("本周没有形成可跟踪的持续资金主线。")

    lines.extend(
        [
            "",
            "## 本周市场状态",
            f"- 进攻 {status.get('进攻', 0)} 天 / 观察 {status.get('观察', 0)} 天 / 防守 {status.get('防守', 0)} 天",
            "",
            "## 本周资金主线",
        ]
    )
    if sectors:
        for row in sectors:
            lines.append(
                f"- {row['name']}：{_sector_label(row)}，"
                f"正流入 {row['positive_days']} 天，"
                f"连续 {row['positive_days']} 天，"
                f"累计 {_format_amount(row['cumulative_inflow'])}，"
                f"最新 {_format_amount(row['latest_inflow'])}"
            )
    else:
        lines.append("- 无活跃行业板块")

    lines.extend(
        [
            "",
            "## 主线变化",
            f"延续：{'、'.join(continued) if continued else '无'}",
            f"新主线：{'、'.join(new) if new else '无'}",
            f"退潮：{'、'.join(ebb) if ebb else '无'}",
            "",
            "## 核心股票资金流向",
        ]
    )
    if stocks:
        for row in stocks:
            symbol = f"（{row['symbol']}）" if row["symbol"] else ""
            lines.append(
                f"- {row['name']}{symbol}："
                f"正流入 {row['positive_days']} 天，"
                f"连续 {row['positive_days']} 天，"
                f"累计 {_format_amount(row['cumulative_inflow'])}，"
                f"最新 {_format_amount(row['latest_inflow'])}"
            )
    else:
        lines.append("- 无活跃股票")

    lines.extend(
        [
            "",
            "## 数据质量",
            f"使用资金快照 {len(loaded_snapshots)} 份；快照内失败记录 {len(failures)} 条。",
        ]
    )
    if len(loaded_snapshots) < lookback_days:
        lines.append(f"可用交易日少于目标 {lookback_days} 份，周报按现有数据生成。")

    return DailyReport(
        report_date=report_date,
        main_candidates_count=len(sectors),
        content_md="\n".join(lines) + "\n",
    )
