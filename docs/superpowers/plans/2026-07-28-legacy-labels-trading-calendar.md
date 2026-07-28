# Legacy Labels and Trading Calendar Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 显式退役旧趋势策略、收口未接入的 Legacy 评分维度、澄清日报/周报标签与降级语义，并用可注入、原子缓存、失败关闭的 XSHG 日历统一日报和周报日期。

**Architecture:** `strategy_runner` 负责生命周期和结构化弃用披露，领域评分函数维护 weight key 到 metric key 的唯一映射，报告应用层只补时间语义和数据质量。`trading_calendar.py` 提供 provider、缓存、日期解析和兼容入口；CLI 在任何写入前解析 requested/effective date，并把同一个 effective date 注入快照、报告、数据库和通知。

**Tech Stack:** Python 3.11、dataclasses、typing Protocol、zoneinfo、exchange_calendars 4.13.2、PyYAML、SQLAlchemy、pytest、ruff。

---

## 执行环境

从 `/Users/marv/Documents/lurker/.worktrees/core-etf-rollout` 执行。若 worktree 没有
`.venv`，复用主仓库环境：

```bash
ln -s /Users/marv/Documents/lurker/.venv .venv
```

`.venv` 已被 `.gitignore` 忽略，不得提交链接。

安装和标准验收必须使用精确 constraints：

```bash
.venv/bin/python -m pip install -c requirements/ci-constraints.txt -e ".[dev]"
```

## 实施约束

1. `long_term_trend` 保持 disabled；自动选择同时排除 disabled 和 deprecated。
2. 显式选择允许运行 disabled/deprecated，但 deprecated 报告必须列出完整能力缺口。
3. 不给 Legacy 死指标补数据、不重分配权重、不降低阈值；个股/板块最高分保持 60/55。
4. `return_180d` 是评分和 double-bagger 分类的唯一长周期输入；120 日只保留展示。
5. 日报/周报标签算法不变，只补 `当日资金状态` / `周度持续状态`。
6. `stock_flows` 失败仍允许推送，但候选空列表必须披露“不完整”。
7. 有效且覆盖充分的日历缓存直接使用，provider 版本变化本身不触发刷新。
8. 日历缓存不足或损坏才调用 provider；provider 不可用且无法证明日期时失败关闭。
9. 未传日期只调用一次 `datetime.now(ZoneInfo("Asia/Shanghai")).date()`。
10. 周报回退到最近 session；日报非交易日跳过；未来日期在所有副作用前拒绝。
11. 不新增远程日历服务，不新建 CI 平台，不修改报告分类公式。
12. 每个行为变更先运行 RED，再写最小实现，最后提交。

## 文件结构

| 文件 | 职责 |
|---|---|
| `configs/strategies.yaml` | active/deprecated 生命周期和能力缺口 |
| `configs/scoring.yaml` | 只暴露七个已接入评分维度 |
| `src/lurker/application/strategy_runner.py` | 严格策略配置、选择矩阵、弃用元数据和报告警告 |
| `src/lurker/config.py` | 严格评分配置键和值校验 |
| `src/lurker/domain/signals.py` | weight key → metric key 映射与评分 |
| `src/lurker/application/signal_scan.py` | 只传已接入个股指标、180 日分类 |
| `src/lurker/application/sector_scan.py` | 只传已接入板块指标 |
| `src/lurker/reports/professional_flow_report.py` | 日报标签时间前缀 |
| `src/lurker/application/professional_flow_daily.py` | 个股资金流覆盖状态和降级披露 |
| `src/lurker/application/weekly_flow_report.py` | 周度标签、日历注入、requested/effective 披露 |
| `src/lurker/trading_calendar.py` | XSHG provider、缓存、日期解析和兼容入口 |
| `src/lurker/cli.py` | 日报/周报日期门、幂等落盘、DB、通知和可读错误 |
| `pyproject.toml` | 生产依赖范围 |
| `requirements/ci-constraints.txt` | 验收基线 `exchange_calendars==4.13.2` |
| `.gitignore` | 运行时日历缓存 |
| `tests/test_strategy_runner.py` | 生命周期和弃用披露 |
| `tests/test_config.py` | 发行版评分配置 |
| `tests/test_legacy_scoring.py` | 映射、占位清理、最高分和 180 日窗口 |
| `tests/test_professional_flow_daily.py` | 日报标签与个股流降级 |
| `tests/test_weekly_flow_report.py` | 周度标签与日历注入 |
| `tests/test_trading_calendar.py` | provider、缓存、跨年、失败关闭和日期解析 |
| `tests/test_cli.py` | 日期全链路、零副作用、DB、通知和 CLI 错误 |
| `tests/fixtures/legacy_calendar/` | 两次 `--no-push`/无推送固定数据演练 |
| `README.md` | 安装、生命周期和交易日行为 |
| `docs/professional_flow_radar.md` | 标签、降级和周报回退语义 |

---

### Task 1: 策略生命周期与弃用披露

**Files:**
- Modify: `configs/strategies.yaml`
- Modify: `src/lurker/application/strategy_runner.py`
- Modify: `tests/test_strategy_runner.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写生命周期配置 RED**

在 `tests/test_strategy_runner.py` 增加：

```python
from pathlib import Path
from textwrap import indent

import pytest


def _strategy_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "strategies.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_strategy_lifecycle_and_limitations(tmp_path):
    configs = load_strategy_configs(
        _strategy_yaml(
            tmp_path,
            """
strategies:
  active:
    enabled: true
    lifecycle: active
  legacy:
    enabled: false
    lifecycle: deprecated
    limitations: [52 周高点距离未接入, 成交量扩张未接入]
""",
        )
    )

    assert configs["active"].lifecycle == "active"
    assert configs["active"].limitations == ()
    assert configs["legacy"].lifecycle == "deprecated"
    assert configs["legacy"].limitations == (
        "52 周高点距离未接入",
        "成交量扩张未接入",
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("enabled: yes", "enabled must be a boolean"),
        ("enabled: true\nlifecycle: retired", "lifecycle"),
        (
            "enabled: true\nlifecycle: deprecated\nlimitations: [缺口]",
            "deprecated strategy must be disabled",
        ),
        (
            "enabled: false\nlifecycle: deprecated\nlimitations: []",
            "deprecated strategy requires limitations",
        ),
        (
            "enabled: true\nlifecycle: active\nlimitations: [不应存在]",
            "active strategy cannot declare limitations",
        ),
    ],
)
def test_load_strategy_config_rejects_invalid_lifecycle(tmp_path, body, message):
    path = _strategy_yaml(
        tmp_path,
        f"strategies:\n  sample:\n{indent(body, '    ')}\n",
    )
    with pytest.raises(ValueError, match=message):
        load_strategy_configs(path)
```

- [ ] **Step 2: 运行配置 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_strategy_runner.py -k "lifecycle or limitations" -v
```

Expected: FAIL because `StrategyConfig` has no lifecycle/limitations and `enabled` still uses
`bool(...)`.

- [ ] **Step 3: 实现严格 lifecycle loader**

在 `src/lurker/application/strategy_runner.py` 更新类型和 loader：

```python
from typing import Any, Literal, Protocol


StrategyLifecycle = Literal["active", "deprecated"]


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


def _strategy_config(name: str, raw: Any) -> StrategyConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"strategy {name} must be a mapping")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"strategy {name} enabled must be a boolean")
    lifecycle = raw.get("lifecycle", "active")
    if lifecycle not in {"active", "deprecated"}:
        raise ValueError(f"strategy {name} has invalid lifecycle: {lifecycle}")
    raw_limitations = raw.get("limitations", [])
    if not isinstance(raw_limitations, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in raw_limitations
    ):
        raise ValueError(f"strategy {name} limitations must be non-empty strings")
    limitations = tuple(item.strip() for item in raw_limitations)
    if lifecycle == "deprecated" and enabled:
        raise ValueError(f"deprecated strategy must be disabled: {name}")
    if lifecycle == "deprecated" and not limitations:
        raise ValueError(f"deprecated strategy requires limitations: {name}")
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
```

- [ ] **Step 4: 写选择矩阵和报告警告 RED**

在 `tests/test_strategy_runner.py` 增加：

```python
def test_strategy_selection_matrix_excludes_deprecated_automatically():
    configs = {
        "active_on": StrategyConfig("active_on", enabled=True),
        "active_off": StrategyConfig("active_off", enabled=False),
        "legacy": StrategyConfig(
            "legacy",
            enabled=True,
            lifecycle="deprecated",
            limitations=("52 周高点距离未接入",),
        ),
    }

    assert [
        item.name
        for item in select_strategy_configs(configs, names=None, cadence=None)
    ] == ["active_on"]
    assert [
        item.name
        for item in select_strategy_configs(
            configs,
            names=["active_off", "legacy"],
            cadence=None,
        )
    ] == ["active_off", "legacy"]


def test_deprecated_warning_is_rendered_for_single_and_multi_reports():
    legacy = StrategyResult(
        name="long_term_trend",
        title="中长期趋势雷达（Legacy）",
        report=DailyReport(
            report_date="2026-07-28",
            main_candidates_count=0,
            content_md="# 大趋势雷达日报\n\n日期：2026-07-28\n\n## 今日主候选\n\n- 无",
        ),
        metadata={
            "lifecycle": "deprecated",
            "limitations": ["52 周高点距离未接入", "成交量扩张未接入"],
        },
    )
    single = render_strategy_results("2026-07-28", [legacy])
    assert "⚠️ 弃用策略：`long_term_trend`" in single.content_md
    assert "52 周高点距离未接入；成交量扩张未接入" in single.content_md

    active = StrategyResult(
        name="professional_flow_daily",
        title="职业资金雷达日报",
        report=DailyReport("2026-07-28", 0, "## 数据质量\n\n- 正常"),
        metadata={"lifecycle": "active", "limitations": []},
    )
    combined = render_strategy_results("2026-07-28", [legacy, active])
    assert combined.content_md.count("⚠️ 弃用策略") == 1
    assert "## 职业资金雷达日报" in combined.content_md
```

- [ ] **Step 5: 运行选择/渲染 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_strategy_runner.py -k "selection_matrix or deprecated_warning" -v
```

Expected: selection includes an enabled deprecated config if constructed directly; warning is absent.

- [ ] **Step 6: 实现防御性选择与结构化警告**

在 `src/lurker/application/strategy_runner.py` 更新：

```python
def select_strategy_configs(
    configs: dict[str, StrategyConfig],
    *,
    names: list[str] | None,
    cadence: str | None,
) -> list[StrategyConfig]:
    selected: list[StrategyConfig] = []
    requested = set(names or [])
    for config in configs.values():
        if names is None and (
            not config.enabled or config.lifecycle == "deprecated"
        ):
            continue
        if names is not None and config.name not in requested:
            continue
        if cadence is not None and config.cadence != cadence:
            continue
        selected.append(config)
    return selected


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
    insertion = 4 if (
        len(lines) >= 3
        and lines[0].startswith("# ")
        and lines[2].startswith("日期：")
    ) else 0
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
```

在 `LongTermTrendStrategy.run()`、`ProfessionalFlowDailyStrategy.run()` 和 missing
strategy 结果中分别使用：

```python
metadata=_strategy_metadata(config)
```

```python
metadata={
    **_strategy_metadata(config),
    "status": "missing",
}
```

在
`render_strategy_results()` 首行执行：

```python
results = [_decorate_result(result) for result in results]
```

- [ ] **Step 7: 更新发行版策略配置**

在 `configs/strategies.yaml` 给所有策略写 `lifecycle: active`，并把
`long_term_trend` 写成：

```yaml
  long_term_trend:
    enabled: false
    lifecycle: deprecated
    cadence: daily
    universe: resolved_seed_pool
    title: 中长期趋势雷达（Legacy）
    limitations:
      - 52 周高点距离未接入
      - 相对大盘和相对板块强度未接入
      - 成交量扩张未接入
      - 板块新高、产业链扩散和持续放量未接入
    params:
      signal_threshold: 60
      main_limit: 10
      low_score_watch_limit: 5
```

更新现有测试内显式启用的 `long_term_trend` fixture 为
`lifecycle: active`；仅弃用行为测试使用 deprecated。

- [ ] **Step 8: 运行 GREEN**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_strategy_runner.py tests/test_cli.py -k "strategy or deprecated or lifecycle" -v
```

Expected: PASS.

- [ ] **Step 9: 提交**

```bash
git add configs/strategies.yaml src/lurker/application/strategy_runner.py tests/test_strategy_runner.py tests/test_cli.py
git commit -m "feat: enforce strategy lifecycle"
```

---

### Task 2: Legacy 评分配置、指标映射和 180 日窗口

**Files:**
- Modify: `configs/scoring.yaml`
- Modify: `src/lurker/config.py`
- Modify: `src/lurker/domain/signals.py`
- Modify: `src/lurker/application/signal_scan.py`
- Modify: `src/lurker/application/sector_scan.py`
- Modify: `src/lurker/cli.py`
- Create: `tests/test_legacy_scoring.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写发行版配置和旧键 RED**

在 `tests/test_config.py` 增加：

```python
def test_shipped_scoring_exposes_only_wired_dimensions():
    scoring = load_scoring(ROOT / "configs" / "scoring.yaml")
    assert set(scoring["stock_signal"]["weights"]) == {
        "return_20d",
        "return_60d",
        "return_180d",
        "double_bagger",
    }
    assert set(scoring["sector_signal"]["weights"]) == {
        "sector_strength",
        "strong_stock_count",
        "cross_market_mapping",
    }


def test_load_scoring_rejects_old_and_unknown_weight_keys(tmp_path):
    old = tmp_path / "old.yaml"
    old.write_text(
        """
stock_signal:
  thresholds: {candidate: 70, high_priority: 85}
  weights: {return_120_180d: 15}
sector_signal:
  thresholds: {candidate: 65, main_candidate: 75, watchlist_pending: 85}
  weights: {sector_strength: 20}
ai_attribution: {weights: {}}
candidate_weights:
  stock_first: {stock_score: 0.35, sector_score: 0.35, ai_score: 0.30}
  sector_first: {stock_score: 0.25, sector_score: 0.45, ai_score: 0.30}
""",
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError,
        match="return_120_180d.*return_180d",
    ):
        load_scoring(old)
```

- [ ] **Step 2: 写指标输入、映射和最高分 RED**

创建 `tests/test_legacy_scoring.py`：

```python
from pathlib import Path

import pytest

import lurker.application.sector_scan as sector_scan
import lurker.application.signal_scan as signal_scan
from lurker.application.signal_scan import StockSignal
from lurker.config import load_scoring
from lurker.domain.signals import score_sector_breadth, score_stock_strength


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("weight", "metrics", "expected"),
    [
        ("return_20d", {"return_20d_percentile": 0.90}, 1),
        ("return_60d", {"return_60d_percentile": 0.90}, 2),
        ("return_180d", {"return_180d": 0.30}, 4),
        ("double_bagger", {"return_180d": 0.80}, 8),
    ],
)
def test_stock_weight_to_metric_mapping(weight, metrics, expected):
    weights = dict.fromkeys(
        ("return_20d", "return_60d", "return_180d", "double_bagger"),
        0,
    )
    weights[weight] = expected
    config = {"stock_signal": {"weights": weights}}
    assert score_stock_strength(metrics, config=config) == expected


@pytest.mark.parametrize(
    ("weight", "metrics", "expected"),
    [
        ("sector_strength", {"sector_outperformance": True}, 16),
        ("strong_stock_count", {"strong_stock_count": 3}, 32),
        ("cross_market_mapping", {"cross_market_count": 2}, 64),
    ],
)
def test_sector_weight_to_metric_mapping(weight, metrics, expected):
    weights = dict.fromkeys(
        ("sector_strength", "strong_stock_count", "cross_market_mapping"),
        0,
    )
    weights[weight] = expected
    config = {"sector_signal": {"weights": weights}}
    assert score_sector_breadth(metrics, config=config) == expected


def test_shipped_legacy_score_ceilings_stay_60_and_55():
    config = load_scoring(ROOT / "configs" / "scoring.yaml")
    assert score_stock_strength(
        {
            "return_20d_percentile": 1.0,
            "return_60d_percentile": 1.0,
            "return_180d": 2.0,
        },
        config=config,
    ) == 60
    assert score_sector_breadth(
        {
            "sector_outperformance": True,
            "strong_stock_count": 5,
            "cross_market_count": 2,
        },
        config=config,
    ) == 55


def test_signal_scan_passes_only_wired_metrics_and_classifies_from_180d(monkeypatch):
    captured = {}

    def fake_score(metrics, config=None):
        captured.update(metrics)
        return 60

    monkeypatch.setattr(signal_scan, "score_stock_strength", fake_score)
    rows = signal_scan.scan_signals(
        [
            {
                "symbol": "TEST.SZ",
                "market": "cn",
                "return_20d": 0.10,
                "return_60d": 0.20,
                "return_120d": 2.50,
                "return_180d": 0.85,
            }
        ],
        windows=[20, 60, 120, 180],
        threshold=0,
    )

    assert set(captured) == {
        "return_20d_percentile",
        "return_60d_percentile",
        "return_180d",
    }
    assert rows[0].returns["return_120d"] == 2.50
    assert rows[0].double_bagger_class == "near_double"


def test_sector_scan_passes_only_wired_metrics(monkeypatch):
    captured = {}

    def fake_score(metrics, config=None):
        captured.update(metrics)
        return 55

    monkeypatch.setattr(sector_scan, "score_sector_breadth", fake_score)
    signal = StockSignal("TEST.SZ", "cn", 60, "none")
    sector_scan.compute_theme_scores(
        [signal],
        {"TEST.SZ": ["theme"]},
        strong_threshold=45,
    )
    assert set(captured) == {
        "sector_outperformance",
        "strong_stock_count",
        "cross_market_count",
    }
```

每个参数用例只启用一个权重，用来锁定配置键到代码指标键的映射；最高分测试再覆盖
`return_180d` 与 `double_bagger` 同时触发。

在 `tests/test_cli.py` 增加，锁定错误配置不得静默回退：

```python
def test_build_run_daily_propagates_invalid_scoring_config(tmp_path):
    snapshot_dir = tmp_path / "prices"
    FilePriceSnapshotStore(snapshot_dir).save(
        {
            "generated_at": "2026-07-28T08:00:00+00:00",
            "markets": ["cn"],
            "windows": [20, 60, 120, 180],
            "snapshots": [],
            "failures": [],
        },
        snapshot_date="2026-07-28",
    )
    scoring_path = tmp_path / "scoring.yaml"
    scoring_path.write_text(
        """
stock_signal:
  weights: {return_120_180d: 15}
sector_signal:
  weights: {sector_strength: 20}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="return_120_180d.*return_180d"):
        build_run_daily(
            price_snapshot_dir=snapshot_dir,
            report_date="2026-07-28",
            scoring_config_path=scoring_path,
        )
```

同时在文件顶部增加：

```python
from lurker.application.price_snapshot import FilePriceSnapshotStore
```

- [ ] **Step 3: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py tests/test_legacy_scoring.py tests/test_cli.py -k "scoring or weight or legacy or classifies" -v
```

Expected: dead keys仍存在、旧键未拒绝、调用方仍传七个占位键，180 日权重读取失败。

- [ ] **Step 4: 实现严格评分配置**

在 `src/lurker/config.py` 替换 `load_scoring`：

```python
_STOCK_WEIGHT_KEYS = {
    "return_20d",
    "return_60d",
    "return_180d",
    "double_bagger",
}
_SECTOR_WEIGHT_KEYS = {
    "sector_strength",
    "strong_stock_count",
    "cross_market_mapping",
}


def _validate_weight_mapping(
    values: Any,
    allowed: set[str],
    context: str,
) -> None:
    mapping = _mapping(values, context)
    if "return_120_180d" in mapping:
        raise ValueError(
            "unsupported scoring weight return_120_180d; use return_180d"
        )
    _reject_unknown_fields(mapping, allowed, context)
    for key, value in mapping.items():
        if isinstance(value, bool):
            raise ValueError(f"{context}.{key} must be finite and non-negative")
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{context}.{key} must be finite and non-negative")


def load_scoring(path: str | Path) -> dict[str, Any]:
    data = load_yaml(path)
    stock = _mapping(data.get("stock_signal"), "stock_signal")
    sector = _mapping(data.get("sector_signal"), "sector_signal")
    _validate_weight_mapping(
        stock.get("weights"),
        _STOCK_WEIGHT_KEYS,
        "stock_signal.weights",
    )
    _validate_weight_mapping(
        sector.get("weights"),
        _SECTOR_WEIGHT_KEYS,
        "sector_signal.weights",
    )
    return data
```

不要在 `daily_job()` / `build_run_daily()` 中吞掉该 `ValueError` 并回退空配置；删除
两处宽泛 `try/except`，让错误在采集或数据库写入前暴露。

- [ ] **Step 5: 更新配置、评分键和调用方**

`configs/scoring.yaml` 的两个 weights 改为：

```yaml
  weights:
    return_20d: 15
    return_60d: 15
    return_180d: 15
    double_bagger: 15
```

```yaml
  weights:
    sector_strength: 20
    strong_stock_count: 20
    cross_market_mapping: 15
```

在 `src/lurker/domain/signals.py` 改为：

```python
w_180d_mid = weights.get("return_180d", 15)
```

保留领域纯函数对显式扩展 metrics 的安全 `.get()` 分支，但发行版 config 不再允许死
weight key。

在 `src/lurker/application/signal_scan.py` 使用：

```python
metrics: dict[str, float | bool] = {
    **pcts,
    "return_180d": raw_returns.get("return_180d", 0.0),
}
score = score_stock_strength(metrics, config=scoring_config)
db_class = classify_double_bagger(raw_returns.get("return_180d", 0.0))
```

在 `src/lurker/application/sector_scan.py` 使用：

```python
metrics: dict[str, float | int | bool] = {
    "strong_stock_count": strong_count,
    "cross_market_count": len(markets_with_strong),
    "sector_outperformance": strong_count >= 5,
}
```

- [ ] **Step 6: 运行 GREEN 和现有领域兼容测试**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py tests/test_legacy_scoring.py tests/test_signals.py tests/test_scoring.py tests/test_domain_architecture.py tests/test_cli.py -k "scoring or weight or legacy or classifies or domain_architecture" -v
```

Expected: PASS；完整 metrics 的领域兼容测试仍通过，发行版最高分固定 60/55。

- [ ] **Step 7: 提交**

```bash
git add configs/scoring.yaml src/lurker/config.py src/lurker/domain/signals.py src/lurker/application/signal_scan.py src/lurker/application/sector_scan.py src/lurker/cli.py tests/test_config.py tests/test_legacy_scoring.py tests/test_cli.py
git commit -m "refactor: expose only wired legacy scores"
```

---

### Task 3: 日报/周报标签与个股资金流降级

**Files:**
- Modify: `src/lurker/reports/professional_flow_report.py`
- Modify: `src/lurker/application/professional_flow_daily.py`
- Modify: `src/lurker/application/weekly_flow_report.py`
- Modify: `tests/test_professional_flow_daily.py`
- Modify: `tests/test_weekly_flow_report.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写标签时间口径 RED**

在 `tests/test_professional_flow_daily.py` 增加：

```python
from lurker.reports.professional_flow_report import render_professional_flow_report


@pytest.mark.parametrize("label", ["主线", "扩散", "分化", "退潮"])
def test_daily_sector_labels_include_time_scope(label):
    report = render_professional_flow_report(
        report_date="2026-07-28",
        market_temperature="观察",
        market_notes=[],
        sector_leaders=[
            {"name": "测试板块", "main_net_inflow": 1.0, "label": label}
        ],
        stock_flow_leaders=[],
        two_percent_candidates=[],
        setup_watch=[],
        invalidation_alerts=[],
        data_quality=[],
    )
    assert f"当日资金状态：{label}" in report
```

在 `tests/test_weekly_flow_report.py` 的聚合 fixture 后增加断言：

```python
assert "周度持续状态：延续" in report.content_md
assert "周度持续状态—延续：" in report.content_md
assert "周度持续状态—新主线：" in report.content_md
assert "周度持续状态—退潮：" in report.content_md
```

再增加完整的三分类测试；不要改变 `_sector_label()` 的输入和返回值：

```python
def test_weekly_sector_labels_keep_algorithm_and_add_time_scope(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    _write_flow(
        flow_dir / "2026-06-04.json",
        "2026-06-04",
        temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
        sectors=[
            {"name": "延续板块", "main_net_inflow": 20.0, "rank": 1},
            {"name": "退潮板块", "main_net_inflow": 10.0, "rank": 2},
        ],
        stocks=[],
    )
    _write_flow(
        flow_dir / "2026-06-05.json",
        "2026-06-05",
        temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
        sectors=[
            {"name": "延续板块", "main_net_inflow": 30.0, "rank": 1},
            {"name": "新主线板块", "main_net_inflow": 15.0, "rank": 2},
            {"name": "退潮板块", "main_net_inflow": -5.0, "rank": 3},
        ],
        stocks=[],
    )

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-05",
    )

    assert "- 延续板块：周度持续状态：延续，" in report.content_md
    assert "- 新主线板块：周度持续状态：新主线，" in report.content_md
    assert "- 退潮板块：周度持续状态：退潮，" in report.content_md
    assert "周度持续状态—延续：延续板块" in report.content_md
    assert "周度持续状态—新主线：新主线板块" in report.content_md
    assert "周度持续状态—退潮：退潮板块" in report.content_md
```

- [ ] **Step 2: 写 stock_flows 覆盖状态 RED**

在 `tests/test_professional_flow_daily.py` 增加：

```python
@pytest.mark.parametrize(
    "flow_patch",
    [
        {},
        {"stock_flows": "invalid"},
        {
            "stock_flows": [],
            "failures": [
                {"source": "stock_flows", "reason": "ReadTimeout"}
            ],
        },
    ],
)
def test_stock_flow_unavailable_warns_candidate_lists_are_incomplete(flow_patch):
    flow = {
        "market_flow": {"main_net_inflow": 1, "super_large_net_inflow": 1},
        "sector_flows": [],
        "margin": {},
        "core_etfs": [],
        "failures": [],
    }
    flow.update(flow_patch)
    report = run_professional_flow_daily(
        price_snapshot={"snapshots": []},
        flow_snapshot=flow,
        theme_mapping={},
        report_date="2026-07-28",
    )
    assert "个股资金流不可用" in report.content_md
    assert "空列表不代表确认没有机会" in report.content_md


def test_successful_empty_stock_flow_is_distinct_from_failure():
    report = run_professional_flow_daily(
        price_snapshot={"snapshots": []},
        flow_snapshot={
            "market_flow": {"main_net_inflow": 1, "super_large_net_inflow": 1},
            "sector_flows": [],
            "stock_flows": [],
            "margin": {},
            "core_etfs": [],
            "failures": [],
        },
        theme_mapping={},
        report_date="2026-07-28",
    )
    assert "本次个股资金流来源返回 0 条记录" in report.content_md
    assert "个股资金流不可用" not in report.content_md
```

把 `tests/test_cli.py::test_daily_job_pushes_professional_report_when_only_stock_flows_fail`
的正文断言加强为：

```python
assert sends
assert "个股资金流不可用" in sends[0][1]
assert "空列表不代表确认没有机会" in sends[0][1]
assert "Pushed report successfully" in message
```

- [ ] **Step 3: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_professional_flow_daily.py tests/test_weekly_flow_report.py tests/test_cli.py -k "time_scope or stock_flow or weekly" -v
```

Expected: labels lack time prefixes; missing/wrong `stock_flows` is not disclosed and may iterate
invalid data.

- [ ] **Step 4: 实现标签文案**

在 `src/lurker/reports/professional_flow_report.py`：

```python
sector_lines = [
    (
        f"{item['name']}：主力净流入 "
        f"{_format_money(item.get('main_net_inflow'))}，"
        f"当日资金状态：{item.get('label', '主线')}"
    )
    for item in sector_leaders
]
```

在 `src/lurker/application/weekly_flow_report.py`：

```python
lines.append(
    f"- {row['name']}：周度持续状态：{_sector_label(row)}，"
    f"正流入 {row['positive_days']} 天，"
    f"连续 {row['positive_days']} 天，"
    f"累计 {_format_amount(row['cumulative_inflow'])}，"
    f"最新 {_format_amount(row['latest_inflow'])}"
)
```

以及：

```python
lines.extend(
    [
        "",
        "## 主线变化",
        f"周度持续状态—延续：{'、'.join(continued) if continued else '无'}",
        f"周度持续状态—新主线：{'、'.join(new) if new else '无'}",
        f"周度持续状态—退潮：{'、'.join(ebb) if ebb else '无'}",
        "",
        "## 核心股票资金流向",
    ]
)
```

- [ ] **Step 5: 实现个股资金流覆盖状态**

在 `src/lurker/application/professional_flow_daily.py` 增加：

```python
def _stock_flow_coverage(
    flow_snapshot: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    failures = flow_snapshot.get("failures", [])
    failed = any(
        isinstance(item, dict) and item.get("source") == "stock_flows"
        for item in failures
    )
    if "stock_flows" not in flow_snapshot:
        return "degraded", []
    raw = flow_snapshot["stock_flows"]
    if not isinstance(raw, list):
        return "degraded", []
    if failed:
        return "degraded", raw
    if not raw:
        return "available_empty", []
    return "available", raw
```

在主入口使用：

```python
stock_flow_coverage, stock_flows = _stock_flow_coverage(flow_snapshot)
```

在 `data_quality` 中加入：

```python
if stock_flow_coverage == "degraded":
    data_quality.append(
        "⚠️ 个股资金流不可用，2%候选、资金确认和核心股票资金流向"
        "列表不完整；空列表不代表确认没有机会。"
    )
elif stock_flow_coverage == "available_empty":
    data_quality.append("本次个股资金流来源返回 0 条记录。")
```

保留 failures 原始原因循环，不改 CLI 的
`non_blocking_flow_sources={"stock_flows", "margin", "core_etfs"}`。

- [ ] **Step 6: 运行 GREEN**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_professional_flow_daily.py tests/test_weekly_flow_report.py tests/test_cli.py -k "time_scope or stock_flow or weekly" -v
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/lurker/reports/professional_flow_report.py src/lurker/application/professional_flow_daily.py src/lurker/application/weekly_flow_report.py tests/test_professional_flow_daily.py tests/test_weekly_flow_report.py tests/test_cli.py
git commit -m "fix: clarify flow report quality and labels"
```

---

### Task 4: 精确依赖与 XSHG provider

**Files:**
- Modify: `pyproject.toml`
- Create: `requirements/ci-constraints.txt`
- Modify: `src/lurker/trading_calendar.py`
- Modify: `tests/test_trading_calendar.py`

- [ ] **Step 1: 写依赖和 provider RED**

在 `tests/test_trading_calendar.py` 替换硬编码年度测试的 import，并增加：

```python
from importlib.metadata import version
from pathlib import Path

from lurker.trading_calendar import ExchangeCalendarsCnProvider


ROOT = Path(__file__).resolve().parents[1]


def test_ci_constraint_pins_exchange_calendars_baseline():
    text = (ROOT / "requirements" / "ci-constraints.txt").read_text(
        encoding="utf-8"
    )
    assert "exchange_calendars==4.13.2" in text.splitlines()


def test_exchange_calendar_provider_contract():
    provider = ExchangeCalendarsCnProvider()
    sessions = provider.sessions_in_range(
        date(2026, 6, 18),
        date(2026, 6, 22),
    )
    assert provider.provider_name == "exchange_calendars"
    assert provider.provider_version == version("exchange-calendars")
    assert sessions == (date(2026, 6, 18), date(2026, 6, 22))
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_trading_calendar.py -k "constraint or provider_contract" -v
```

Expected: constraints file and adapter do not exist; dependency is not installed.

- [ ] **Step 3: 声明并安装依赖**

在 `pyproject.toml` dependencies 加：

```toml
"exchange_calendars>=4.13.2,<5",
```

创建 `requirements/ci-constraints.txt`：

```text
exchange_calendars==4.13.2
```

安装：

```bash
.venv/bin/python -m pip install -c requirements/ci-constraints.txt -e ".[dev]"
```

Expected: command exits 0 and installs exactly 4.13.2.

- [ ] **Step 4: 实现 provider 接口和适配器**

在 `src/lurker/trading_calendar.py` 保留 `parse_iso_date` 和
`all_markets_are_cn`，删除 `CN_MARKET_CLOSED_RANGES_2026`，加入：

```python
from importlib.metadata import version
from typing import Protocol


class TradingCalendarUnavailable(RuntimeError):
    pass


class CnTradingCalendarProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def provider_version(self) -> str: ...

    def sessions_in_range(
        self,
        start: date,
        end: date,
    ) -> tuple[date, ...]: ...


class ExchangeCalendarsCnProvider:
    @property
    def provider_name(self) -> str:
        return "exchange_calendars"

    @property
    def provider_version(self) -> str:
        return version("exchange-calendars")

    def sessions_in_range(
        self,
        start: date,
        end: date,
    ) -> tuple[date, ...]:
        try:
            import exchange_calendars as xcals
            import pandas as pd

            calendar = xcals.get_calendar("XSHG")
            sessions = calendar.sessions_in_range(
                pd.Timestamp(start),
                pd.Timestamp(end),
            )
            normalized = tuple(item.date() for item in sessions)
        except Exception as exc:
            raise TradingCalendarUnavailable(
                f"XSHG calendar unavailable: {exc}"
            ) from exc
        if tuple(sorted(set(normalized))) != normalized:
            raise TradingCalendarUnavailable(
                "XSHG sessions are not strictly increasing and unique"
            )
        return normalized
```

- [ ] **Step 5: 运行 provider GREEN 和版本断言**

```bash
.venv/bin/python -c "from importlib.metadata import version; assert version('exchange-calendars') == '4.13.2'"
PYTHONPATH=src .venv/bin/pytest tests/test_trading_calendar.py -k "constraint or provider_contract" -v
```

Expected: both commands exit 0.

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml requirements/ci-constraints.txt src/lurker/trading_calendar.py tests/test_trading_calendar.py
git commit -m "feat: add pinned XSHG calendar provider"
```

---

### Task 5: 原子日历缓存与失败关闭

**Files:**
- Modify: `.gitignore`
- Modify: `src/lurker/trading_calendar.py`
- Modify: `tests/test_trading_calendar.py`

- [ ] **Step 1: 写缓存 RED**

在 `tests/test_trading_calendar.py` 增加：

```python
import json

import pytest

from lurker.trading_calendar import CnTradingCalendar


class FakeProvider:
    def __init__(self, sessions, *, provider_version="4.13.2", error=None):
        self._sessions = tuple(sessions)
        self._version = provider_version
        self._error = error
        self.calls = []

    @property
    def provider_name(self):
        return "fake"

    @property
    def provider_version(self):
        return self._version

    def sessions_in_range(self, start, end):
        self.calls.append((start, end))
        if self._error is not None:
            raise self._error
        return tuple(day for day in self._sessions if start <= day <= end)


def _cache(path, *, start, end, sessions, provider_version="old"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calendar": "XSHG",
                "timezone": "Asia/Shanghai",
                "provider": "fake",
                "provider_version": provider_version,
                "generated_at": "2026-07-28T10:00:00+08:00",
                "coverage_start": start.isoformat(),
                "coverage_end": end.isoformat(),
                "sessions": [item.isoformat() for item in sessions],
            }
        ),
        encoding="utf-8",
    )


def test_sufficient_old_cache_never_initializes_provider(tmp_path):
    cache_path = tmp_path / "calendar.json"
    _cache(
        cache_path,
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        sessions=[date(2026, 6, 18), date(2026, 6, 22)],
    )
    initialized = []

    def factory():
        initialized.append(True)
        return FakeProvider([])

    calendar = CnTradingCalendar(cache_path, provider_factory=factory)
    assert calendar.is_trading_day(date(2026, 6, 18)) is True
    assert calendar.is_trading_day(date(2026, 6, 19)) is False
    assert initialized == []


def test_insufficient_cache_requeries_full_union_and_upgrades_version(tmp_path):
    cache_path = tmp_path / "calendar.json"
    _cache(
        cache_path,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        sessions=[date(2025, 12, 31)],
    )
    provider = FakeProvider(
        [date(2025, 12, 31), date(2026, 1, 5)],
        provider_version="new",
    )
    calendar = CnTradingCalendar(
        cache_path,
        provider_factory=lambda: provider,
    )
    assert calendar.is_trading_day(date(2026, 1, 5)) is True
    assert provider.calls == [(date(2025, 1, 1), date(2026, 12, 31))]
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["provider_version"] == "new"
    assert saved["coverage_start"] == "2025-01-01"
    assert saved["coverage_end"] == "2026-12-31"


def test_corrupt_cache_rebuilds_requested_year_without_merging(tmp_path):
    cache_path = tmp_path / "calendar.json"
    cache_path.write_text("{broken", encoding="utf-8")
    provider = FakeProvider([date(2027, 1, 4)])
    calendar = CnTradingCalendar(
        cache_path,
        provider_factory=lambda: provider,
    )
    assert calendar.is_trading_day(date(2027, 1, 4)) is True
    assert provider.calls == [(date(2027, 1, 1), date(2027, 12, 31))]


def test_provider_failure_with_insufficient_cache_fails_closed(tmp_path):
    calendar = CnTradingCalendar(
        tmp_path / "missing.json",
        provider_factory=lambda: FakeProvider(
            [],
            error=TradingCalendarUnavailable("offline"),
        ),
    )
    with pytest.raises(TradingCalendarUnavailable, match="offline"):
        calendar.is_trading_day(date(2027, 1, 4))


def test_atomic_write_failure_preserves_previous_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "calendar.json"
    _cache(
        cache_path,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        sessions=[date(2025, 12, 31)],
    )
    original = cache_path.read_bytes()
    provider = FakeProvider([date(2025, 12, 31), date(2026, 1, 5)])

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr("lurker.trading_calendar.os.replace", fail_replace)
    calendar = CnTradingCalendar(
        cache_path,
        provider_factory=lambda: provider,
    )
    with pytest.raises(OSError, match="replace failed"):
        calendar.is_trading_day(date(2026, 1, 5))
    assert cache_path.read_bytes() == original
```

- [ ] **Step 2: 运行缓存 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_trading_calendar.py -k "cache or provider_failure or atomic" -v
```

Expected: `CnTradingCalendar` does not exist.

- [ ] **Step 3: 实现缓存模型、校验和原子写**

在 `src/lurker/trading_calendar.py` 增加：

```python
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class CalendarCache:
    provider: str
    provider_version: str
    generated_at: str
    coverage_start: date
    coverage_end: date
    sessions: tuple[date, ...]

    @classmethod
    def from_dict(cls, raw: object) -> "CalendarCache":
        if not isinstance(raw, dict):
            raise ValueError("calendar cache must be a mapping")
        if raw.get("schema_version") != 1:
            raise ValueError("unsupported calendar cache schema")
        if raw.get("calendar") != "XSHG":
            raise ValueError("calendar cache must use XSHG")
        if raw.get("timezone") != "Asia/Shanghai":
            raise ValueError("calendar cache timezone mismatch")
        start = parse_iso_date(str(raw["coverage_start"]))
        end = parse_iso_date(str(raw["coverage_end"]))
        sessions = tuple(parse_iso_date(str(item)) for item in raw["sessions"])
        if start > end:
            raise ValueError("calendar cache coverage is reversed")
        if tuple(sorted(set(sessions))) != sessions:
            raise ValueError("calendar cache sessions must be sorted and unique")
        if any(item < start or item > end for item in sessions):
            raise ValueError("calendar cache session outside coverage")
        return cls(
            provider=str(raw["provider"]),
            provider_version=str(raw["provider_version"]),
            generated_at=str(raw["generated_at"]),
            coverage_start=start,
            coverage_end=end,
            sessions=sessions,
        )

    def covers(self, start: date, end: date) -> bool:
        return self.coverage_start <= start and self.coverage_end >= end

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "calendar": "XSHG",
            "timezone": "Asia/Shanghai",
            "provider": self.provider,
            "provider_version": self.provider_version,
            "generated_at": self.generated_at,
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "sessions": [item.isoformat() for item in self.sessions],
        }


def _read_cache(path: Path) -> CalendarCache | None:
    if not path.exists():
        return None
    try:
        return CalendarCache.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, cache: CalendarCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(cache.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
```

- [ ] **Step 4: 实现按自然年扩展和兼容查询**

继续在 `src/lurker/trading_calendar.py` 增加：

```python
ProviderFactory = Callable[[], CnTradingCalendarProvider]


class CnTradingCalendar:
    def __init__(
        self,
        cache_path: Path,
        *,
        provider_factory: ProviderFactory = ExchangeCalendarsCnProvider,
    ):
        self.cache_path = Path(cache_path)
        self.provider_factory = provider_factory

    def _ensure(self, start: date, end: date) -> CalendarCache:
        requested_start = date(start.year, 1, 1)
        requested_end = date(end.year, 12, 31)
        cache = _read_cache(self.cache_path)
        if cache is not None and cache.covers(requested_start, requested_end):
            return cache
        if cache is None:
            query_start, query_end = requested_start, requested_end
        else:
            query_start = min(cache.coverage_start, requested_start)
            query_end = max(cache.coverage_end, requested_end)
        provider = self.provider_factory()
        sessions = provider.sessions_in_range(query_start, query_end)
        rebuilt = CalendarCache(
            provider=provider.provider_name,
            provider_version=provider.provider_version,
            generated_at=datetime.now(SHANGHAI_TZ).isoformat(),
            coverage_start=query_start,
            coverage_end=query_end,
            sessions=sessions,
        )
        _write_cache(self.cache_path, rebuilt)
        return rebuilt

    def sessions_in_range(
        self,
        start: date,
        end: date,
    ) -> tuple[date, ...]:
        if start > end:
            raise ValueError("calendar range start must not exceed end")
        cache = self._ensure(start, end)
        return tuple(item for item in cache.sessions if start <= item <= end)

    def is_trading_day(self, day: date | str) -> bool:
        resolved = parse_iso_date(day) if isinstance(day, str) else day
        cache = self._ensure(resolved, resolved)
        return resolved in set(cache.sessions)

    def previous_or_same_session(self, day: date | str) -> date:
        resolved = parse_iso_date(day) if isinstance(day, str) else day
        cursor_year = resolved.year
        while True:
            start = date(cursor_year, 1, 1)
            end = resolved if cursor_year == resolved.year else date(cursor_year, 12, 31)
            sessions = self.sessions_in_range(start, end)
            if sessions:
                return sessions[-1]
            cursor_year -= 1


DEFAULT_CALENDAR_CACHE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "cache"
    / "trading_calendars"
    / "xshg_sessions.json"
)


def build_default_cn_calendar(
    cache_path: Path | None = None,
) -> CnTradingCalendar:
    return CnTradingCalendar(cache_path or DEFAULT_CALENDAR_CACHE)


def is_cn_trading_day(
    day: date | str,
    *,
    calendar: CnTradingCalendar | None = None,
) -> bool:
    resolved_calendar = calendar or build_default_cn_calendar()
    return resolved_calendar.is_trading_day(day)
```

在 `.gitignore` 增加：

```text
data/cache/trading_calendars/
```

- [ ] **Step 5: 运行缓存 GREEN**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_trading_calendar.py -k "cache or provider_failure or atomic" -v
```

Expected: PASS；cache hit 测试中 provider factory 调用次数为零。

- [ ] **Step 6: 提交**

```bash
git add .gitignore src/lurker/trading_calendar.py tests/test_trading_calendar.py
git commit -m "feat: add atomic trading calendar cache"
```

---

### Task 6: 上海日期与日报/周报解析

**Files:**
- Modify: `src/lurker/trading_calendar.py`
- Modify: `tests/test_trading_calendar.py`

- [ ] **Step 1: 写日期解析 RED**

在 `tests/test_trading_calendar.py` 增加：

```python
from datetime import UTC, datetime

from lurker.trading_calendar import (
    FutureReportDateError,
    ReportDateResolution,
    resolve_daily_date,
    resolve_weekly_date,
    shanghai_today,
)


def test_shanghai_today_converts_utc_across_local_midnight():
    assert shanghai_today(
        datetime(2026, 7, 27, 16, 30, tzinfo=UTC)
    ) == date(2026, 7, 28)


def test_future_requested_date_is_rejected():
    calendar = CnTradingCalendar(
        Path("unused"),
        provider_factory=lambda: FakeProvider([]),
    )
    with pytest.raises(FutureReportDateError, match="2026-07-29.*2026-07-28"):
        resolve_daily_date("2026-07-29", date(2026, 7, 28), calendar)


def test_daily_non_session_skips_without_backfill(tmp_path):
    provider = FakeProvider([date(2026, 6, 18)])
    calendar = CnTradingCalendar(
        tmp_path / "calendar.json",
        provider_factory=lambda: provider,
    )
    assert resolve_daily_date(
        "2026-06-19",
        date(2026, 7, 28),
        calendar,
    ) == ReportDateResolution(
        requested=date(2026, 6, 19),
        effective=None,
        adjusted=False,
        reason="cn market closed",
    )


def test_weekly_holiday_and_cross_year_fall_back_to_confirmed_session(tmp_path):
    provider = FakeProvider(
        [date(2025, 12, 31), date(2026, 1, 5), date(2026, 6, 18)]
    )
    calendar = CnTradingCalendar(
        tmp_path / "calendar.json",
        provider_factory=lambda: provider,
    )
    holiday = resolve_weekly_date(
        "2026-06-21",
        date(2026, 7, 28),
        calendar,
    )
    assert holiday.effective == date(2026, 6, 18)
    cross_year = resolve_weekly_date(
        "2026-01-04",
        date(2026, 7, 28),
        calendar,
    )
    assert cross_year.effective == date(2025, 12, 31)
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_trading_calendar.py -k "shanghai_today or future_requested or daily_non_session or cross_year" -v
```

Expected: date resolution types/functions do not exist.

- [ ] **Step 3: 实现单次 today 和解析结果**

在 `src/lurker/trading_calendar.py` 增加：

```python
@dataclass(frozen=True)
class ReportDateResolution:
    requested: date
    effective: date | None
    adjusted: bool
    reason: str | None = None


class FutureReportDateError(ValueError):
    pass


def shanghai_today(now: datetime | None = None) -> date:
    if now is None:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(SHANGHAI_TZ).date()


def _requested_date(value: str | None, today: date) -> date:
    requested = parse_iso_date(value) if value is not None else today
    if requested > today:
        raise FutureReportDateError(
            f"future report date {requested.isoformat()} exceeds "
            f"Shanghai today {today.isoformat()}"
        )
    return requested


def resolve_daily_date(
    value: str | None,
    today: date,
    calendar: CnTradingCalendar,
) -> ReportDateResolution:
    requested = _requested_date(value, today)
    if not calendar.is_trading_day(requested):
        return ReportDateResolution(
            requested=requested,
            effective=None,
            adjusted=False,
            reason="cn market closed",
        )
    return ReportDateResolution(requested, requested, False)


def resolve_weekly_date(
    value: str | None,
    today: date,
    calendar: CnTradingCalendar,
) -> ReportDateResolution:
    requested = _requested_date(value, today)
    effective = calendar.previous_or_same_session(requested)
    return ReportDateResolution(
        requested=requested,
        effective=effective,
        adjusted=effective != requested,
        reason="previous confirmed CN trading session"
        if effective != requested
        else None,
    )
```

- [ ] **Step 4: 运行 GREEN**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_trading_calendar.py -v
```

Expected: PASS，包括 provider、缓存、未来日期、节假日和跨年。

- [ ] **Step 5: 提交**

```bash
git add src/lurker/trading_calendar.py tests/test_trading_calendar.py
git commit -m "feat: resolve Shanghai report dates"
```

---

### Task 7: 应用与 CLI 日期全链路

**Files:**
- Modify: `src/lurker/application/weekly_flow_report.py`
- Modify: `src/lurker/application/run_daily.py`
- Modify: `src/lurker/cli.py`
- Modify: `tests/test_weekly_flow_report.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写周报 effective date 全链路 RED**

把现有“非交易日跳过周报”测试替换为：

```python
def test_weekly_report_falls_back_and_uses_effective_date_everywhere(
    monkeypatch,
    tmp_path,
):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    _write_flow_snapshot(
        flow_dir / "2026-06-18.json",
        snapshot_date="2026-06-18",
        sector_name="有效板块",
    )
    _write_flow_snapshot(
        flow_dir / "2026-06-19.json",
        snapshot_date="2026-06-19",
        sector_name="未来污染",
    )
    calendar = FakeCalendar(
        sessions=[date(2026, 6, 18)],
    )
    sends = []

    class FakeNotifier:
        def send(self, title, markdown_content):
            sends.append((title, markdown_content))

    monkeypatch.setattr("lurker.cli.build_notifier_from_env", lambda: FakeNotifier())
    db_path = tmp_path / "reports.sqlite"
    message = weekly_report(
        flow_snapshot_dir=flow_dir,
        report_dir=tmp_path / "reports",
        report_date="2026-06-21",
        today=date(2026, 7, 28),
        calendar=calendar,
        push=True,
        db_path=db_path,
    )

    report_path = tmp_path / "reports" / "weekly_2026-06-18.md"
    assert report_path.exists()
    assert not (tmp_path / "reports" / "weekly_2026-06-21.md").exists()
    text = report_path.read_text(encoding="utf-8")
    assert "请求日期 2026-06-21，按最近交易日 2026-06-18 生成" in text
    assert "2026-06-19" not in text
    assert sends[0][0] == "Lurker 周报 (2026-06-18)"

    engine = init_db(db_path)
    with create_session(engine) as session:
        rows = session.query(Report).filter_by(report_type="weekly").all()
        assert [row.report_date.isoformat() for row in rows] == ["2026-06-18"]
    assert "weekly_2026-06-18.md" in message
```

在 `tests/test_cli.py` 定义：

```python
class FakeCalendar:
    def __init__(self, sessions):
        self.sessions = tuple(sessions)

    def is_trading_day(self, day):
        return day in self.sessions

    def previous_or_same_session(self, day):
        candidates = [item for item in self.sessions if item <= day]
        if not candidates:
            raise TradingCalendarUnavailable("no confirmed prior session")
        return candidates[-1]


def _write_flow_snapshot(path, *, snapshot_date, sector_name):
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": f"{snapshot_date}T08:00:00+00:00",
                "market": "cn",
                "market_flow": {
                    "main_net_inflow": 1.0,
                    "super_large_net_inflow": 1.0,
                },
                "sector_flows": [
                    {
                        "name": sector_name,
                        "main_net_inflow": 100.0,
                        "rank": 1,
                    }
                ],
                "stock_flows": [],
                "margin": {},
                "core_etfs": {
                    "schema_version": 1,
                    "configured_symbols": [],
                    "items": [],
                    "failures": [],
                    "generated_at": f"{snapshot_date}T08:00:00+00:00",
                },
                "failures": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
```

文件顶部同时 import `TradingCalendarUnavailable`、`FutureReportDateError`、
`FilePriceSnapshotStore`、`init_db`、`create_session` 和 `Report`。19 日 fixture 使用唯一
板块名 `未来污染`，并断言正文不存在该名称。

- [ ] **Step 2: 写日报跳过、未来日期零副作用和 CLI 错误 RED**

在 `tests/test_cli.py` 增加：

```python
def test_daily_job_skips_non_session_without_backfill(tmp_path):
    message = daily_job(
        seed_pool_path=tmp_path / "missing.json",
        price_snapshot_dir=tmp_path / "prices",
        report_dir=tmp_path / "reports",
        markets=["cn"],
        windows=[20],
        period="6mo",
        limit_per_market=1,
        report_date="2026-06-19",
        today=date(2026, 7, 28),
        calendar=FakeCalendar([date(2026, 6, 18)]),
    )
    assert message == "Skipped daily job: cn market closed on 2026-06-19."
    assert not (tmp_path / "prices").exists()
    assert not (tmp_path / "reports").exists()


def test_future_daily_and_weekly_dates_have_zero_side_effects(tmp_path):
    calendar = FakeCalendar([date(2026, 7, 28)])
    with pytest.raises(FutureReportDateError):
        daily_job(
            seed_pool_path=tmp_path / "missing.json",
            price_snapshot_dir=tmp_path / "prices",
            report_dir=tmp_path / "daily",
            markets=["cn"],
            windows=[20],
            period="6mo",
            limit_per_market=1,
            report_date="2026-07-29",
            today=date(2026, 7, 28),
            calendar=calendar,
        )
    with pytest.raises(FutureReportDateError):
        weekly_report(
            flow_snapshot_dir=tmp_path / "flows",
            report_dir=tmp_path / "weekly",
            report_date="2026-07-29",
            today=date(2026, 7, 28),
            calendar=calendar,
        )
    assert not (tmp_path / "prices").exists()
    assert not (tmp_path / "daily").exists()
    assert not (tmp_path / "weekly").exists()


def test_main_reports_calendar_error_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["lurker", "weekly-report", "--date", "2026-07-29"],
    )
    monkeypatch.setattr(
        "lurker.cli.weekly_report",
        lambda **kwargs: (_ for _ in ()).throw(
            FutureReportDateError("future report date")
        ),
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "future report date" in capsys.readouterr().err
```

- [ ] **Step 3: 运行 CLI RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py tests/test_weekly_flow_report.py -k "effective_date or non_session or future_daily or calendar_error" -v
```

Expected: functions do not accept `today`/`calendar`; weekly report skips rather than falls back.

- [ ] **Step 4: 注入周报日历并披露回退**

在 `src/lurker/application/weekly_flow_report.py`：

```python
from collections.abc import Callable
from datetime import date


TradingDayPredicate = Callable[[date], bool]
```

给 `_load_latest_snapshots()`、`_status_counts()` 和
`build_weekly_flow_report()` 增加 `is_trading_day: TradingDayPredicate`，替换所有
直接 `is_cn_trading_day(...)` 调用。`build_weekly_flow_report()` 再增加
`requested_date: str | None = None`。

在数据质量区：

```python
if requested_date is not None and requested_date != report_date:
    lines.append(
        f"请求日期 {requested_date}，按最近交易日 {report_date} 生成。"
    )
```

为保持现有直接调用兼容，公开函数默认参数使用：

```python
is_trading_day: TradingDayPredicate = is_cn_trading_day
```

- [ ] **Step 5: 在 CLI 统一解析一次日期**

在 `src/lurker/cli.py` import：

```python
from lurker.trading_calendar import (
    CnTradingCalendar,
    FutureReportDateError,
    ReportDateResolution,
    TradingCalendarUnavailable,
    all_markets_are_cn,
    build_default_cn_calendar,
    parse_iso_date,
    resolve_daily_date,
    resolve_weekly_date,
    shanghai_today,
)
```

给 `daily_job()`、`build_run_daily()`、`weekly_report()` 增加：

```python
today: date | None = None,
calendar: CnTradingCalendar | None = None,
```

先增加一个供两个日报入口共用的纯解析 helper：

```python
def _resolve_daily_job_date(
    report_date: str | None,
    *,
    today: date,
    markets: list[str],
    calendar: CnTradingCalendar | None,
) -> ReportDateResolution:
    requested = parse_iso_date(report_date) if report_date else today
    if requested > today:
        raise FutureReportDateError(
            f"future report date {requested.isoformat()} exceeds "
            f"Shanghai today {today.isoformat()}"
        )
    if not all_markets_are_cn(markets):
        return ReportDateResolution(requested, requested, False)
    resolved_calendar = calendar or build_default_cn_calendar()
    return resolve_daily_date(
        requested.isoformat(),
        today,
        resolved_calendar,
    )


def _require_effective_date(
    resolution: ReportDateResolution,
) -> str | None:
    if resolution.effective is None:
        return None
    return resolution.effective.isoformat()
```

`daily_job()` 在读取 seed pool 前执行：

```python
resolved_today = today or shanghai_today()
resolution = _resolve_daily_job_date(
    report_date,
    today=resolved_today,
    markets=markets,
    calendar=calendar,
)
job_date = _require_effective_date(resolution)
if job_date is None:
    return (
        "Skipped daily job: cn market closed on "
        f"{resolution.requested.isoformat()}."
    )
```

`build_run_daily()` 在加载 snapshot 后执行：

```python
resolved_today = today or shanghai_today()
snapshot_markets = [
    str(item) for item in snapshot_batch.get("markets", [])
]
resolution = _resolve_daily_job_date(
    report_date,
    today=resolved_today,
    markets=snapshot_markets,
    calendar=calendar,
)
job_date = _require_effective_date(resolution)
if job_date is None:
    return (
        "Skipped run-daily: cn market closed on "
        f"{resolution.requested.isoformat()}."
    )
```

把 `job_date` 传入 `run_daily()` / `build_strategy_report()`，删除
`build_run_daily()` 的 `date.today()` fallback。`src/lurker/application/run_daily.py`
的直接 fallback 改为：

```python
today = report_date or shanghai_today().isoformat()
```

`weekly_report()` 在创建目录前执行：

```python
resolved_today = today or shanghai_today()
resolved_calendar = calendar or build_default_cn_calendar()
resolution = resolve_weekly_date(
    report_date,
    resolved_today,
    resolved_calendar,
)
assert resolution.effective is not None
requested_date = resolution.requested.isoformat()
job_date = resolution.effective.isoformat()
```

调用报告时传：

```python
report = build_weekly_flow_report(
    flow_snapshot_dir=flow_snapshot_dir,
    report_date=job_date,
    requested_date=requested_date if resolution.adjusted else None,
    lookback_days=lookback_days,
    sector_limit=sector_limit,
    stock_limit=stock_limit,
    is_trading_day=resolved_calendar.is_trading_day,
)
```

之后所有 snapshot cutoff、文件名、`DailyReport.report_date`、DB `t_date`、通知标题和
返回消息只使用 `job_date`。

- [ ] **Step 6: 添加 CLI 可读错误边界**

在 `src/lurker/cli.py` 增加：

```python
def _print_with_calendar_errors(parser, action) -> None:
    try:
        print(action())
    except (FutureReportDateError, TradingCalendarUnavailable) as exc:
        parser.error(str(exc))
```

`main()` 的 `run-daily`、`daily-job`、`weekly-report` 三个分支改为把原调用放进
零参数 lambda：

```python
_print_with_calendar_errors(
    parser,
    lambda: weekly_report(
        flow_snapshot_dir=args.flow_snapshots,
        report_dir=args.report_dir,
        report_date=args.date,
        lookback_days=args.lookback,
        sector_limit=args.sector_limit,
        stock_limit=args.stock_limit,
        push=args.push,
        db_path=args.db_path,
    ),
)
return
```

日报两个分支使用同一 helper，不捕获其他异常。

- [ ] **Step 7: 运行日期全链路 GREEN**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py tests/test_weekly_flow_report.py tests/test_trading_calendar.py -v
```

Expected: PASS；周末/节假日报告的文件、DB、正文、通知标题全部使用同一 effective date。

- [ ] **Step 8: 提交**

```bash
git add src/lurker/application/weekly_flow_report.py src/lurker/application/run_daily.py src/lurker/cli.py tests/test_weekly_flow_report.py tests/test_cli.py
git commit -m "feat: resolve report dates through XSHG calendar"
```

---

### Task 8: 固定数据演练、文档和总验收

**Files:**
- Create: `tests/fixtures/legacy_calendar/strategies.yaml`
- Create: `tests/fixtures/legacy_calendar/resolved_seed_pool.json`
- Create: `tests/fixtures/legacy_calendar/price_snapshots/2026-06-18.json`
- Create: `tests/fixtures/legacy_calendar/flow_snapshots/2026-06-18.json`
- Create: `tests/fixtures/legacy_calendar/flow_snapshots/2026-06-19.json`
- Modify: `README.md`
- Modify: `docs/professional_flow_radar.md`

- [ ] **Step 1: 创建可重复 fixture**

`tests/fixtures/legacy_calendar/strategies.yaml`：

```yaml
strategies:
  professional_flow_daily:
    enabled: true
    lifecycle: active
    cadence: daily
    universe: resolved_seed_pool
    title: 职业资金雷达日报
    params: {}
```

`resolved_seed_pool.json`：

```json
{
  "generated_at": "2026-06-18T00:00:00+00:00",
  "theme_mapping": {"300308.SZ": ["通信设备"]},
  "symbol_names": {"300308.SZ": "中际旭创"},
  "markets": {
    "cn": {
      "symbols": ["300308.SZ"],
      "sources": {}
    }
  }
}
```

`price_snapshots/2026-06-18.json`：

```json
{
  "generated_at": "2026-06-18T08:00:00+00:00",
  "markets": ["cn"],
  "windows": [20, 60, 120, 180],
  "snapshots": [
    {
      "symbol": "300308.SZ",
      "market": "cn",
      "return_20d": 0.10,
      "return_60d": 0.20,
      "return_120d": 0.30,
      "return_180d": 0.40
    }
  ],
  "failures": []
}
```

`flow_snapshots/2026-06-18.json`：

```json
{
  "schema_version": 2,
  "generated_at": "2026-06-18T08:00:00+00:00",
  "market": "cn",
  "market_flow": {"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
  "sector_flows": [
    {"name": "通信设备", "main_net_inflow": 100.0, "rank": 1}
  ],
  "stock_flows": [],
  "margin": {},
  "core_etfs": {
    "schema_version": 1,
    "configured_symbols": [],
    "items": [],
    "failures": [],
    "generated_at": "2026-06-18T08:00:00+00:00"
  },
  "failures": [
    {"source": "stock_flows", "reason": "fixture timeout"}
  ]
}
```

`flow_snapshots/2026-06-19.json`：

```json
{
  "schema_version": 2,
  "generated_at": "2026-06-19T08:00:00+00:00",
  "market": "cn",
  "market_flow": {
    "main_net_inflow": 9999.0,
    "super_large_net_inflow": 9999.0
  },
  "sector_flows": [
    {"name": "未来污染", "main_net_inflow": 9999.0, "rank": 1}
  ],
  "stock_flows": [],
  "margin": {},
  "core_etfs": {
    "schema_version": 1,
    "configured_symbols": [],
    "items": [],
    "failures": [],
    "generated_at": "2026-06-19T08:00:00+00:00"
  },
  "failures": []
}
```

该文件用于证明 effective-date cutoff 不会读入 6 月 19 日的数据。

- [ ] **Step 2: 更新用户文档**

在 `README.md`：

- 安装命令增加 `-c requirements/ci-constraints.txt`；
- 把“首个策略是 long_term_trend”改为默认 `professional_flow_daily`；
- 说明 `long_term_trend` 是 disabled/deprecated，显式运行会显示能力缺口；
- 增加周报周末/节假日回退、日报跳过、未来日期拒绝；
- 说明日历缓存路径和失败关闭。

在 `docs/professional_flow_radar.md`：

- 定义 `2%候选` 是最高置信观察层，不是收益率或仓位建议；
- 日报四档前缀为 `当日资金状态`；
- 周报三档前缀为 `周度持续状态`；
- `stock_flows` 失败时仍推送但候选不完整；
- 周报 requested/effective date 规则和统一影响范围；
- 日历 provider、cache 和 constraints 版本。

- [ ] **Step 3: 执行固定数据日报降级演练**

```bash
PYTHONPATH=src .venv/bin/lurker run-daily \
  --price-snapshots tests/fixtures/legacy_calendar/price_snapshots \
  --flow-snapshots tests/fixtures/legacy_calendar/flow_snapshots \
  --seed-pool tests/fixtures/legacy_calendar/resolved_seed_pool.json \
  --strategy-config tests/fixtures/legacy_calendar/strategies.yaml \
  --strategies professional_flow_daily \
  --date 2026-06-18 \
  --db-path /tmp/lurker-legacy-calendar-daily.sqlite
```

Expected: exit 0；stdout 含 `个股资金流不可用` 和
`空列表不代表确认没有机会`。`run-daily` 本身不发送通知。

- [ ] **Step 4: 执行固定数据周报回退演练**

```bash
PYTHONPATH=src .venv/bin/lurker weekly-report \
  --flow-snapshots tests/fixtures/legacy_calendar/flow_snapshots \
  --report-dir /tmp/lurker-legacy-calendar-reports \
  --db-path /tmp/lurker-legacy-calendar-weekly.sqlite \
  --date 2026-06-21
```

Expected: exit 0；生成
`/tmp/lurker-legacy-calendar-reports/weekly_2026-06-18.md`；正文含
`请求日期 2026-06-21，按最近交易日 2026-06-18 生成`，不含 `未来污染`。未传
`--push`，不会发送通知。

- [ ] **Step 5: 验证版本、目标测试、全量测试和 lint**

```bash
.venv/bin/python -c "from importlib.metadata import version; assert version('exchange-calendars') == '4.13.2'"
PYTHONPATH=src .venv/bin/pytest tests/test_strategy_runner.py tests/test_legacy_scoring.py tests/test_professional_flow_daily.py tests/test_weekly_flow_report.py tests/test_trading_calendar.py tests/test_cli.py -v
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check src tests
git diff --check
```

Expected:

- version assertion exits 0；
- focused tests all pass；
- full suite reports 0 failed；
- ruff reports no errors；
- `git diff --check` prints nothing。

- [ ] **Step 6: 搜索完成定义**

```bash
rg -n "CN_MARKET_CLOSED_RANGES_2026|return_120_180d|near_52w_high.: False|relative_market_strength.: 0|relative_sector_strength.: 0|turnover_expansion.: 0|new_high_ratio.: 0|chain_segments.: 0|turnover_persistent.: False" src configs
```

Expected: no matches.

```bash
rg -n "当日资金状态|周度持续状态|个股资金流不可用|ZoneInfo\\(\"Asia/Shanghai\"\\)|exchange_calendars" src configs requirements README.md docs/professional_flow_radar.md
```

Expected: each required contract has at least one implementation or documentation match.

- [ ] **Step 7: 提交**

```bash
git add tests/fixtures/legacy_calendar README.md docs/professional_flow_radar.md
git commit -m "docs: document legacy calendar rollout"
```

- [ ] **Step 8: 最终分支证据**

```bash
git status --short --branch
git log --oneline -8
```

Expected: feature worktree has no tracked modifications；日志包含本计划八个任务各自的提交，
用户已有未跟踪文件不得加入提交。
