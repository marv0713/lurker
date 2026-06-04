from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml


@dataclass
class StrategyConfig:
    name: str
    enabled: bool = True
    cadence: str = "daily"
    universe: str = "resolved_seed_pool"
    title: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyContext:
    snapshot_batch: dict[str, Any]
    theme_mapping: dict[str, list[str]]
    report_date: str | None
    attributor: Any
    suppressed_symbols: set[str]
    flow_snapshot: dict[str, Any] | None = None
    symbol_names: dict[str, str] = field(default_factory=dict)
    runtime_params: dict[str, Any] = field(default_factory=dict)


from lurker.reports.models import DailyReport

@dataclass
class StrategyResult:
    name: str
    title: str
    report: DailyReport
    metadata: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    name: str

    def run(self, context: StrategyContext, config: StrategyConfig) -> StrategyResult: ...


def load_strategy_configs(path: Path | None) -> dict[str, StrategyConfig]:
    if path is None or not path.exists():
        return {}

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    strategy_items = data.get("strategies", data)
    configs: dict[str, StrategyConfig] = {}
    for name, raw_config in strategy_items.items():
        raw_config = raw_config or {}
        configs[name] = StrategyConfig(
            name=name,
            enabled=bool(raw_config.get("enabled", True)),
            cadence=str(raw_config.get("cadence", "daily")),
            universe=str(raw_config.get("universe", "resolved_seed_pool")),
            title=raw_config.get("title"),
            params=dict(raw_config.get("params", {}) or {}),
        )
    return configs


def build_default_strategy_configs(names: list[str]) -> dict[str, StrategyConfig]:
    return {
        name: StrategyConfig(name=name)
        for name in names
    }


def parse_strategy_names(value: str | None) -> list[str] | None:
    if not value:
        return None
    names = [name.strip() for name in value.split(",") if name.strip()]
    return names or None


def select_strategy_configs(
    configs: dict[str, StrategyConfig],
    *,
    names: list[str] | None,
    cadence: str | None,
) -> list[StrategyConfig]:
    selected: list[StrategyConfig] = []
    name_set = set(names or [])
    for config in configs.values():
        if names is None and not config.enabled:
            continue
        if names is not None and config.name not in name_set:
            continue
        if cadence is not None and config.cadence != cadence:
            continue
        selected.append(config)
    return selected


def merge_strategy_params(config: StrategyConfig, runtime_params: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config.params)
    for key, value in runtime_params.items():
        if value is not None:
            merged[key] = value
    return merged


def _strip_report_title(markdown: str) -> str:
    lines = markdown.strip().splitlines()
    if len(lines) >= 3 and lines[0].startswith("# ") and lines[2].startswith("日期："):
        return "\n".join(lines[4:]).strip()
    return markdown.strip()


def render_strategy_results(report_date: str, results: list[StrategyResult]) -> DailyReport:
    if not results:
        return DailyReport(
            report_date=report_date,
            main_candidates_count=0,
            content_md=f"# 多策略雷达日报\n\n日期：{report_date}\n\n今日无启用策略。\n",
        )

    if len(results) == 1 and results[0].name == "long_term_trend":
        return results[0].report

    total_candidates = sum(r.report.main_candidates_count for r in results)
    sections = []
    for result in results:
        body = _strip_report_title(result.report.content_md)
        sections.append(f"## {result.title}\n\n{body}")

    content = f"# 多策略雷达日报\n\n日期：{report_date}\n\n{chr(10).join(sections)}\n"
    return DailyReport(
        report_date=report_date,
        main_candidates_count=total_candidates,
        content_md=content,
    )


class LongTermTrendStrategy:
    name = "long_term_trend"

    def run(self, context: StrategyContext, config: StrategyConfig) -> StrategyResult:
        from lurker.application.run_daily import run_daily

        params = merge_strategy_params(config, context.runtime_params)
        report = run_daily(
            snapshot_batch=context.snapshot_batch,
            theme_mapping=context.theme_mapping,
            symbol_names=context.symbol_names,
            attributor=context.attributor,
            report_date=context.report_date,
            signal_threshold=int(params.get("signal_threshold", 60)),
            main_limit=int(params.get("main_limit", 10)),
            low_score_watch_limit=int(params.get("low_score_watch_limit", 5)),
            suppressed_symbols=context.suppressed_symbols,
        )
        return StrategyResult(
            name=self.name,
            title=config.title or "中长期趋势雷达",
            report=report,
            metadata={"cadence": config.cadence, "universe": config.universe},
        )


class ProfessionalFlowDailyStrategy:
    name = "professional_flow_daily"

    def run(self, context: StrategyContext, config: StrategyConfig) -> StrategyResult:
        from lurker.application.professional_flow_daily import run_professional_flow_daily

        report = run_professional_flow_daily(
            price_snapshot=context.snapshot_batch,
            flow_snapshot=context.flow_snapshot,
            theme_mapping=context.theme_mapping,
            symbol_names=context.symbol_names,
            report_date=context.report_date or "",
        )
        return StrategyResult(
            name=self.name,
            title=config.title or "职业资金雷达日报",
            report=report,
            metadata={"cadence": config.cadence, "universe": config.universe},
        )


DEFAULT_STRATEGIES: dict[str, Strategy] = {
    ProfessionalFlowDailyStrategy.name: ProfessionalFlowDailyStrategy(),
    LongTermTrendStrategy.name: LongTermTrendStrategy(),
}


def run_strategies(
    *,
    context: StrategyContext,
    configs: list[StrategyConfig],
    registry: dict[str, Strategy] | None = None,
) -> list[StrategyResult]:
    strategy_registry = registry or DEFAULT_STRATEGIES
    results: list[StrategyResult] = []
    for config in configs:
        strategy = strategy_registry.get(config.name)
        if strategy is None:
            results.append(
                StrategyResult(
                    name=config.name,
                    title=config.title or config.name,
                    report=DailyReport(
                        report_date=context.report_date or "",
                        main_candidates_count=0,
                        content_md=f"策略 `{config.name}` 尚未实现。"
                    ),
                    metadata={"status": "missing"},
                )
            )
            continue
        results.append(strategy.run(context, config))
    return results
