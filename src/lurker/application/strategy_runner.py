from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml

from lurker.reports.models import DailyReport


StrategyLifecycle = Literal["active", "planned", "deprecated"]


@dataclass
class StrategyConfig:
    name: str
    enabled: bool = True
    lifecycle: StrategyLifecycle = "active"
    cadence: str = "daily"
    universe: str = "resolved_seed_pool"
    title: str | None = None
    limitations: tuple[str, ...] = ()
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
    db_session: Any = None
    monthly_macro_snapshot: dict[str, Any] | None = None


@dataclass
class StrategyResult:
    name: str
    title: str
    report: DailyReport
    metadata: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    name: str

    def run(self, context: StrategyContext, config: StrategyConfig) -> StrategyResult: ...


def _strategy_config(name: str, raw: Any) -> StrategyConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"strategy {name} must be a mapping")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"strategy {name} enabled must be a boolean")
    lifecycle = raw.get("lifecycle", "active")
    if lifecycle not in {"active", "planned", "deprecated"}:
        raise ValueError(f"strategy {name} has invalid lifecycle: {lifecycle}")
    raw_limitations = raw.get("limitations", [])
    if not isinstance(raw_limitations, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_limitations
    ):
        raise ValueError(f"strategy {name} limitations must be non-empty strings")
    limitations = tuple(item.strip() for item in raw_limitations)
    if lifecycle == "deprecated" and enabled:
        raise ValueError(f"deprecated strategy must be disabled: {name}")
    if lifecycle == "deprecated" and not limitations:
        raise ValueError(f"deprecated strategy requires limitations: {name}")
    if lifecycle == "planned" and enabled:
        raise ValueError(f"planned strategy must be disabled: {name}")
    if lifecycle == "planned" and not limitations:
        raise ValueError(f"planned strategy requires limitations: {name}")
    if lifecycle == "active" and limitations:
        raise ValueError(f"active strategy cannot declare limitations: {name}")
    return StrategyConfig(
        name=name,
        enabled=enabled,
        lifecycle=lifecycle,
        cadence=str(raw.get("cadence", "daily")),
        universe=str(raw.get("universe", "resolved_seed_pool")),
        title=raw.get("title"),
        limitations=limitations,
        params=dict(raw.get("params", {}) or {}),
    )


def load_strategy_configs(path: Path | None) -> dict[str, StrategyConfig]:
    if path is None or not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    strategy_items = data.get("strategies", data)
    if not isinstance(strategy_items, dict):
        raise ValueError("strategies must be a mapping")
    return {
        str(name): _strategy_config(str(name), raw)
        for name, raw in strategy_items.items()
    }


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
    if names is not None:
        for name in names:
            config = configs.get(name)
            if config is not None and config.lifecycle == "planned":
                limitations = "；".join(config.limitations)
                raise ValueError(
                    f"planned strategy cannot run: {name}; "
                    f"limitations: {limitations}"
                )
    selected: list[StrategyConfig] = []
    name_set = set(names or [])
    for config in configs.values():
        if names is None and (
            not config.enabled or config.lifecycle == "deprecated"
        ):
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


def _strategy_metadata(config: StrategyConfig) -> dict[str, Any]:
    return {
        "cadence": config.cadence,
        "universe": config.universe,
        "lifecycle": config.lifecycle,
        "limitations": list(config.limitations),
    }


def _deprecated_notice(result: StrategyResult) -> str | None:
    if result.metadata.get("lifecycle") != "deprecated":
        return None
    limitations = [
        str(item).strip()
        for item in result.metadata.get("limitations", [])
        if str(item).strip()
    ]
    return (
        f"> ⚠️ 弃用策略：`{result.name}` 仅供历史兼容，不代表当前推荐信号。\n"
        f"> 能力缺口：{'；'.join(limitations)}。"
    )


def _decorate_result(result: StrategyResult) -> StrategyResult:
    notice = _deprecated_notice(result)
    if notice is None:
        return result
    lines = result.report.content_md.rstrip().splitlines()
    insertion = (
        4
        if len(lines) >= 3
        and lines[0].startswith("# ")
        and lines[2].startswith("日期：")
        else 0
    )
    decorated = [*lines[:insertion], notice, "", *lines[insertion:]]
    return StrategyResult(
        name=result.name,
        title=result.title,
        report=DailyReport(
            report_date=result.report.report_date,
            main_candidates_count=result.report.main_candidates_count,
            content_md="\n".join(decorated).rstrip() + "\n",
        ),
        metadata=result.metadata,
    )


def render_strategy_results(report_date: str, results: list[StrategyResult]) -> DailyReport:
    if not results:
        return DailyReport(
            report_date=report_date,
            main_candidates_count=0,
            content_md=f"# 多策略雷达日报\n\n日期：{report_date}\n\n今日无启用策略。\n",
        )

    results = [_decorate_result(result) for result in results]

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
            scoring_config=params.get("scoring_config"),
            db_session=context.db_session,
        )
        return StrategyResult(
            name=self.name,
            title=config.title or "中长期趋势雷达",
            report=report,
            metadata=_strategy_metadata(config),
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
            temperature_rollout_approved=bool(
                context.runtime_params.get(
                    "temperature_rollout_approved",
                    True,
                )
            ),
        )
        return StrategyResult(
            name=self.name,
            title=config.title or "职业资金雷达日报",
            report=report,
            metadata=_strategy_metadata(config),
        )


class MonthlyMacroFlowStrategy:
    name = "monthly_macro_flow"

    def run(
        self,
        context: StrategyContext,
        config: StrategyConfig,
    ) -> StrategyResult:
        from lurker.application.monthly_macro_flow import (
            analyze_monthly_macro_flow,
        )
        from lurker.reports.monthly_macro_flow_report import (
            render_monthly_macro_flow_report,
        )

        if context.monthly_macro_snapshot is None:
            raise ValueError(
                "monthly_macro_flow requires monthly_macro_snapshot"
            )
        analysis = analyze_monthly_macro_flow(
            context.monthly_macro_snapshot
        )
        report = render_monthly_macro_flow_report(
            context.monthly_macro_snapshot,
            analysis,
        )
        return StrategyResult(
            name=self.name,
            title=config.title or "宏观流动性月报",
            report=report,
            metadata={
                **_strategy_metadata(config),
                "analysis": analysis,
            },
        )


DEFAULT_STRATEGIES: dict[str, Strategy] = {
    ProfessionalFlowDailyStrategy.name: ProfessionalFlowDailyStrategy(),
    LongTermTrendStrategy.name: LongTermTrendStrategy(),
    MonthlyMacroFlowStrategy.name: MonthlyMacroFlowStrategy(),
}


def run_strategies(
    *,
    context: StrategyContext,
    configs: list[StrategyConfig],
    registry: dict[str, Strategy] | None = None,
) -> list[StrategyResult]:
    strategy_registry = (
        DEFAULT_STRATEGIES if registry is None else registry
    )
    results: list[StrategyResult] = []
    for config in configs:
        strategy = strategy_registry.get(config.name)
        if strategy is None:
            raise ValueError(
                f"{config.lifecycle} strategy is not registered: "
                f"{config.name}"
            )
        results.append(strategy.run(context, config))
    return results
