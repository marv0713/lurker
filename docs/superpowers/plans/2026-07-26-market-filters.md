# Market Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `markets.yaml` 中的 ST、北交所、成交额、港股价格和美股市值过滤改造成严格配置、时间对齐、可审计且不会静默降级的两阶段过滤引擎。

**Architecture:** 种子池阶段执行北交所和 ST 预过滤，价格快照阶段组合规范化行情与独立美股 metadata，调用无 I/O 纯过滤器生成终态决定。seed pool 与 price snapshot 都保存配置哈希、决定、摘要和来源；CLI、缓存和日报只消费规范化结果。

**Tech Stack:** Python 3.11、dataclasses、pandas、yfinance、PyYAML、pytest、ruff。

---

## 执行环境

从 feature worktree 根目录执行。若 worktree 没有 `.venv`，先复用主仓库环境：

```bash
ln -s /Users/marv/Documents/lurker/.venv .venv
```

该链接必须保持未跟踪；不要提交 `.venv`。

## 实施约束

1. `missing_data_policy` 只控制过滤辅助字段缺失，核心价格不可用始终不能形成快照。
2. 预期阈值排除进入 `filter_decisions`，不进入 `failures`。
3. provider/schema 失败进入 `failures`，并产生对应的缺失过滤原因。
4. 美股当前市值不能回填历史 `snapshot_date`。
5. 成交额固定最近 20 个不同交易日、至少 15 个有效观测，不随 `period` 改变。
6. A 股只使用 provider 直接成交额；美股和港股使用未复权 close × volume。
7. 配置 hash 不匹配的 seed pool 不能仅靠重新过滤恢复曾被排除的 symbol。
8. `exclude_shell_like`、`exclude_frequent_capital_actions` 为 true 时在网络访问前失败。
9. 不捕获宽泛 `Exception` 来伪装程序错误。

## 文件结构

| 文件 | 职责 |
|---|---|
| `configs/markets.yaml` | schema v1、全局过滤策略、三市场 profile |
| `src/lurker/config.py` | typed config、严格校验、canonical hash |
| `src/lurker/universe/market_filters.py` | reason codes、决定、摘要、纯过滤规则 |
| `src/lurker/ingest/equity_metadata.py` | yfinance 美股市值 metadata adapter |
| `src/lurker/ingest/prices.py` | 规范化 turnover、来源 attrs、声明式 source error |
| `src/lurker/ingest/constituents.py` | 可审计 A 股名称解析 |
| `src/lurker/universe/resolved_seed_pool.py` | 预过滤、schema v2、原子保存 |
| `src/lurker/application/price_snapshot.py` | 量化过滤、schema v2、缓存和原子保存 |
| `src/lurker/application/professional_flow_daily.py` | 日报过滤质量披露 |
| `src/lurker/cli.py` | hash 门、计数、缓存重建/失败边界 |
| `tests/test_config.py` | 配置与 hash |
| `tests/test_market_filters.py` | 纯规则真值表 |
| `tests/test_ingest.py` | turnover 与 price source |
| `tests/test_equity_metadata.py` | US metadata |
| `tests/test_resolved_seed_pool.py` | 预过滤与原子 seed pool |
| `tests/test_price_snapshot.py` | 量化编排与 schema v2 |
| `tests/test_cli.py` | hash 门、缓存和 CLI |
| `tests/test_professional_flow_daily.py` | 日报披露 |

---

### Task 1: 严格 typed 市场配置

**Files:**
- Modify: `configs/markets.yaml`
- Modify: `src/lurker/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: 写 typed config RED**

在 `tests/test_config.py` 增加：

```python
from dataclasses import replace

from lurker.config import (
    CnMarketFilters,
    HkMarketFilters,
    MarketFilterPolicy,
    MarketsConfig,
    UsMarketFilters,
    load_markets,
)


def _market_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "markets.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _valid_market_yaml() -> str:
    return """
schema_version: 1
filter_policy:
  missing_data_policy: exclude
  turnover_window_trading_days: 20
  min_turnover_observations: 15
  us_market_cap_max_age_days: 7
markets:
  cn:
    name: A 股
    role: primary_discovery
    universe_sources: [沪深 300]
    filters:
      exclude_st: true
      exclude_beijing_exchange: true
      min_avg_turnover_cny: 50000000
  us:
    name: 美股
    role: global_anchor
    universe_sources: [主题字典核心龙头]
    filters:
      min_market_cap_usd: 2000000000
      min_avg_turnover_usd: 10000000
  hk:
    name: 港股
    role: mapping_supplement
    universe_sources: [主题字典核心映射股]
    filters:
      min_price_hkd: 1
      min_avg_turnover_hkd: 20000000
      exclude_shell_like: false
      exclude_frequent_capital_actions: false
"""


def test_load_markets_returns_strict_typed_config(tmp_path):
    config = load_markets(_market_yaml(tmp_path, _valid_market_yaml()))

    assert isinstance(config, MarketsConfig)
    assert config.policy == MarketFilterPolicy(
        missing_data_policy="exclude",
        turnover_window_trading_days=20,
        min_turnover_observations=15,
        us_market_cap_max_age_days=7,
    )
    assert config.profiles["cn"].filters == CnMarketFilters(
        exclude_st=True,
        exclude_beijing_exchange=True,
        min_avg_turnover_cny=50_000_000.0,
    )
    assert config.profiles["us"].filters == UsMarketFilters(
        min_market_cap_usd=2_000_000_000.0,
        min_avg_turnover_usd=10_000_000.0,
    )
    assert config.profiles["hk"].filters == HkMarketFilters(
        min_price_hkd=1.0,
        min_avg_turnover_hkd=20_000_000.0,
        exclude_shell_like=False,
        exclude_frequent_capital_actions=False,
    )
    assert config.filter_config_hash.startswith("sha256:")
    assert len(config.filter_config_hash) == len("sha256:") + 64


def test_market_filter_hash_ignores_descriptive_profile_fields(tmp_path):
    first = load_markets(_market_yaml(tmp_path, _valid_market_yaml()))
    changed = _valid_market_yaml().replace("name: A 股", "name: 中国股票")
    second = load_markets(_market_yaml(tmp_path, changed))
    assert first.filter_config_hash == second.filter_config_hash


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("schema_version: 1", "schema_version: 2", "schema_version"),
        ("missing_data_policy: exclude", "missing_data_policy: keep", "missing_data_policy"),
        ("turnover_window_trading_days: 20", "turnover_window_trading_days: true", "integer"),
        ("min_turnover_observations: 15", "min_turnover_observations: 21", "cannot exceed"),
        ("min_market_cap_usd: 2000000000", "min_market_cap_usd: .nan", "finite positive"),
        ("exclude_st: true", "exclude_st: 1", "boolean"),
        ("exclude_shell_like: false", "exclude_shell_like: true", "unsupported market filter"),
        (
            "exclude_frequent_capital_actions: false",
            "exclude_frequent_capital_actions: true",
            "unsupported market filter",
        ),
    ],
)
def test_load_markets_rejects_invalid_contract(tmp_path, old, new, message):
    with pytest.raises(ValueError, match=message):
        load_markets(_market_yaml(tmp_path, _valid_market_yaml().replace(old, new)))


def test_load_markets_rejects_unknown_and_wrong_market_fields(tmp_path):
    unknown = _valid_market_yaml().replace(
        "schema_version: 1",
        "schema_version: 1\nunknown: true",
    )
    with pytest.raises(ValueError, match="unknown market config top-level field"):
        load_markets(_market_yaml(tmp_path, unknown))

    wrong_market = _valid_market_yaml().replace(
        "exclude_st: true",
        "exclude_st: true\n      min_market_cap_usd: 10",
    )
    with pytest.raises(ValueError, match="unknown cn filter field"):
        load_markets(_market_yaml(tmp_path, wrong_market))
```

更新现有 `test_load_markets_has_three_market_profiles`：

```python
def test_load_markets_has_three_market_profiles():
    config = load_markets(ROOT / "configs" / "markets.yaml")
    assert set(config.profiles) == {"cn", "us", "hk"}
    assert config.profiles["cn"].role == "primary_discovery"
    assert (
        config.profiles["hk"].filters.min_avg_turnover_hkd
        == 20_000_000
    )
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py -k "market" -v
```

Expected: typed classes do not exist and old YAML shape fails expectations.

- [ ] **Step 3: 实现 dataclasses、严格 loader 和 hash**

在 `src/lurker/config.py` 增加：

```python
import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MarketFilterPolicy:
    missing_data_policy: str
    turnover_window_trading_days: int
    min_turnover_observations: int
    us_market_cap_max_age_days: int


@dataclass(frozen=True)
class CnMarketFilters:
    exclude_st: bool
    exclude_beijing_exchange: bool
    min_avg_turnover_cny: float | None


@dataclass(frozen=True)
class UsMarketFilters:
    min_market_cap_usd: float | None
    min_avg_turnover_usd: float | None


@dataclass(frozen=True)
class HkMarketFilters:
    min_price_hkd: float | None
    min_avg_turnover_hkd: float | None
    exclude_shell_like: bool
    exclude_frequent_capital_actions: bool


MarketFilters = CnMarketFilters | UsMarketFilters | HkMarketFilters


@dataclass(frozen=True)
class MarketProfile:
    name: str
    role: str
    universe_sources: tuple[str, ...]
    filters: MarketFilters


@dataclass(frozen=True)
class MarketsConfig:
    schema_version: int
    policy: MarketFilterPolicy
    profiles: dict[str, MarketProfile]
    filter_config_hash: str


def _strict_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    return value


def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _strict_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _optional_positive(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite positive")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite positive") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be finite positive")
    return result


def _strict_fields(
    mapping: dict[str, Any],
    allowed: set[str],
    field: str,
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"unknown {field} field: {unknown[0]}")


def _profile(raw: Any, market: str) -> tuple[MarketProfile, dict[str, Any]]:
    profile = _strict_mapping(raw, f"{market} profile")
    _strict_fields(
        profile,
        {"name", "role", "universe_sources", "filters"},
        f"{market} profile",
    )
    name = str(profile.get("name", "")).strip()
    role = str(profile.get("role", "")).strip()
    sources = profile.get("universe_sources")
    if not name or not role:
        raise ValueError(f"{market} name and role are required")
    if (
        not isinstance(sources, list)
        or not sources
        or any(not isinstance(item, str) or not item.strip() for item in sources)
    ):
        raise ValueError(f"{market} universe_sources must be non-empty strings")
    filters = _strict_mapping(profile.get("filters"), f"{market} filters")
    if market == "cn":
        _strict_fields(
            filters,
            {"exclude_st", "exclude_beijing_exchange", "min_avg_turnover_cny"},
            "cn filter",
        )
        typed: MarketFilters = CnMarketFilters(
            exclude_st=_strict_bool(filters.get("exclude_st"), "exclude_st"),
            exclude_beijing_exchange=_strict_bool(
                filters.get("exclude_beijing_exchange"),
                "exclude_beijing_exchange",
            ),
            min_avg_turnover_cny=_optional_positive(
                filters.get("min_avg_turnover_cny"),
                "min_avg_turnover_cny",
            ),
        )
    elif market == "us":
        _strict_fields(
            filters,
            {"min_market_cap_usd", "min_avg_turnover_usd"},
            "us filter",
        )
        typed = UsMarketFilters(
            min_market_cap_usd=_optional_positive(
                filters.get("min_market_cap_usd"),
                "min_market_cap_usd",
            ),
            min_avg_turnover_usd=_optional_positive(
                filters.get("min_avg_turnover_usd"),
                "min_avg_turnover_usd",
            ),
        )
    else:
        _strict_fields(
            filters,
            {
                "min_price_hkd",
                "min_avg_turnover_hkd",
                "exclude_shell_like",
                "exclude_frequent_capital_actions",
            },
            "hk filter",
        )
        shell = _strict_bool(
            filters.get("exclude_shell_like"),
            "exclude_shell_like",
        )
        actions = _strict_bool(
            filters.get("exclude_frequent_capital_actions"),
            "exclude_frequent_capital_actions",
        )
        for enabled, field in (
            (shell, "exclude_shell_like"),
            (actions, "exclude_frequent_capital_actions"),
        ):
            if enabled:
                raise ValueError(f"unsupported market filter: {field}")
        typed = HkMarketFilters(
            min_price_hkd=_optional_positive(
                filters.get("min_price_hkd"),
                "min_price_hkd",
            ),
            min_avg_turnover_hkd=_optional_positive(
                filters.get("min_avg_turnover_hkd"),
                "min_avg_turnover_hkd",
            ),
            exclude_shell_like=shell,
            exclude_frequent_capital_actions=actions,
        )
    return (
        MarketProfile(
            name=name,
            role=role,
            universe_sources=tuple(item.strip() for item in sources),
            filters=typed,
        ),
        asdict(typed),
    )


def load_markets(path: str | Path) -> MarketsConfig:
    data = load_yaml(path)
    _strict_fields(
        data,
        {"schema_version", "filter_policy", "markets"},
        "market config top-level",
    )
    if data.get("schema_version") != 1:
        raise ValueError("markets schema_version must equal 1")
    policy_raw = _strict_mapping(data.get("filter_policy"), "filter_policy")
    _strict_fields(
        policy_raw,
        {
            "missing_data_policy",
            "turnover_window_trading_days",
            "min_turnover_observations",
            "us_market_cap_max_age_days",
        },
        "filter_policy",
    )
    missing_policy = policy_raw.get("missing_data_policy")
    if missing_policy not in {"exclude", "include_with_warning"}:
        raise ValueError(
            "missing_data_policy must be exclude or include_with_warning"
        )
    window = _strict_positive_int(
        policy_raw.get("turnover_window_trading_days"),
        "turnover_window_trading_days",
    )
    observations = _strict_positive_int(
        policy_raw.get("min_turnover_observations"),
        "min_turnover_observations",
    )
    if observations > window:
        raise ValueError(
            "min_turnover_observations cannot exceed "
            "turnover_window_trading_days"
        )
    policy = MarketFilterPolicy(
        missing_data_policy=missing_policy,
        turnover_window_trading_days=window,
        min_turnover_observations=observations,
        us_market_cap_max_age_days=_strict_positive_int(
            policy_raw.get("us_market_cap_max_age_days"),
            "us_market_cap_max_age_days",
        ),
    )
    raw_profiles = _strict_mapping(data.get("markets"), "markets")
    if set(raw_profiles) != {"cn", "us", "hk"}:
        raise ValueError("markets must contain exactly cn, us, hk")
    profiles: dict[str, MarketProfile] = {}
    canonical_filters: dict[str, dict[str, Any]] = {}
    for market in ("cn", "us", "hk"):
        profiles[market], canonical_filters[market] = _profile(
            raw_profiles[market],
            market,
        )
    canonical = {
        "schema_version": 1,
        "filter_policy": asdict(policy),
        "filters": canonical_filters,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return MarketsConfig(
        schema_version=1,
        policy=policy,
        profiles=profiles,
        filter_config_hash=(
            f"sha256:{hashlib.sha256(encoded).hexdigest()}"
        ),
    )
```

将 `configs/markets.yaml` 替换为：

```yaml
schema_version: 1
filter_policy:
  missing_data_policy: exclude
  turnover_window_trading_days: 20
  min_turnover_observations: 15
  us_market_cap_max_age_days: 7
markets:
  cn:
    name: A 股
    role: primary_discovery
    universe_sources:
      - 沪深 300
      - 中证 1000
      - 科创 50
      - 创业板核心指数
      - 重点行业 ETF 成分股
    filters:
      exclude_st: true
      exclude_beijing_exchange: true
      min_avg_turnover_cny: 50000000
  us:
    name: 美股
    role: global_anchor
    universe_sources:
      - 主题字典核心龙头
      - 行业 ETF
      - 主题 ETF
    filters:
      min_market_cap_usd: 2000000000
      min_avg_turnover_usd: 10000000
  hk:
    name: 港股
    role: mapping_supplement
    universe_sources:
      - 主题字典核心映射股
      - A/H 映射股
      - 中概和创新药核心公司
    filters:
      min_price_hkd: 1.0
      min_avg_turnover_hkd: 20000000
      exclude_shell_like: false
      exclude_frequent_capital_actions: false
```

- [ ] **Step 4: GREEN、lint 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py -k "market" -v
.venv/bin/ruff check src/lurker/config.py tests/test_config.py
git add configs/markets.yaml src/lurker/config.py tests/test_config.py
git commit -m "feat: add strict typed market filter config"
```

---

### Task 2: 过滤领域模型、预过滤规则与终态摘要

**Files:**
- Create: `src/lurker/universe/market_filters.py`
- Create: `tests/test_market_filters.py`

- [ ] **Step 1: 写决定、ST/BJ 和摘要 RED**

创建 `tests/test_market_filters.py`：

```python
import pytest

from lurker.config import CnMarketFilters, MarketFilterPolicy
from lurker.universe.market_filters import (
    FilterDecision,
    SourceEvidence,
    evaluate_seed_prefilter,
    summarize_filter_decisions,
)


POLICY_EXCLUDE = MarketFilterPolicy(
    missing_data_policy="exclude",
    turnover_window_trading_days=20,
    min_turnover_observations=15,
    us_market_cap_max_age_days=7,
)
POLICY_WARN = MarketFilterPolicy(
    missing_data_policy="include_with_warning",
    turnover_window_trading_days=20,
    min_turnover_observations=15,
    us_market_cap_max_age_days=7,
)
CN_FILTERS = CnMarketFilters(
    exclude_st=True,
    exclude_beijing_exchange=True,
    min_avg_turnover_cny=50_000_000.0,
)


@pytest.mark.parametrize(
    "symbol",
    ["430001.BJ", "830001.bj"],
)
def test_seed_prefilter_excludes_beijing_symbols(symbol):
    decision = evaluate_seed_prefilter(
        symbol=symbol,
        market="cn",
        name="普通公司",
        filters=CN_FILTERS,
        policy=POLICY_EXCLUDE,
    )
    assert decision.status == "excluded"
    assert decision.reason_codes == ("beijing_exchange_excluded",)


@pytest.mark.parametrize(
    "name",
    ["ST公司", "*ST公司", "SST公司", "S*ST公司", "  *st 公司"],
)
def test_seed_prefilter_normalizes_and_excludes_st_names(name):
    decision = evaluate_seed_prefilter(
        symbol="600001.SH",
        market="cn",
        name=name,
        filters=CN_FILTERS,
        policy=POLICY_EXCLUDE,
    )
    assert decision.status == "excluded"
    assert decision.reason_codes == ("st_name_excluded",)


@pytest.mark.parametrize(
    ("policy", "status"),
    [(POLICY_EXCLUDE, "excluded"), (POLICY_WARN, "included_with_warning")],
)
def test_seed_prefilter_applies_missing_name_policy(policy, status):
    decision = evaluate_seed_prefilter(
        symbol="600001.SH",
        market="cn",
        name=None,
        filters=CN_FILTERS,
        policy=policy,
    )
    assert decision.status == status
    assert decision.reason_codes == ("symbol_name_missing",)


def test_filter_decision_serializes_source_evidence():
    decision = FilterDecision(
        symbol="NVDA",
        market="us",
        stage="quantitative",
        status="included",
        reason_codes=(),
        metrics={"market_cap_usd": 5_000_000_000.0},
        thresholds={"min_market_cap_usd": 2_000_000_000.0},
        sources=(
            SourceEvidence(
                source="fixture",
                data_date="2026-07-24",
                retrieved_at="2026-07-26T12:00:00+00:00",
                sha256="sha256:" + "0" * 64,
                hash_scope="normalized_metadata",
            ),
        ),
    )
    assert decision.to_dict()["sources"][0]["source"] == "fixture"


def test_summary_uses_one_terminal_decision_per_symbol():
    decisions = [
        FilterDecision("A", "cn", "seed_prefilter", "included", (), {}, {}, ()),
        FilterDecision("A", "cn", "quantitative", "excluded",
                       ("turnover_below_minimum",), {}, {}, ()),
        FilterDecision("B", "cn", "seed_prefilter", "excluded",
                       ("st_name_excluded",), {}, {}, ()),
        FilterDecision("C", "us", "seed_prefilter", "included", (), {}, {}, ()),
        FilterDecision("C", "us", "quantitative", "included_with_warning",
                       ("market_cap_missing",), {}, {}, ()),
    ]
    assert summarize_filter_decisions(decisions) == {
        "included": 0,
        "excluded": 2,
        "included_with_warning": 1,
        "reason_counts": {
            "market_cap_missing": 1,
            "st_name_excluded": 1,
            "turnover_below_minimum": 1,
        },
    }


def test_summary_rejects_duplicate_quantitative_terminal_decision():
    duplicated = [
        FilterDecision("A", "cn", "quantitative", "included", (), {}, {}, ()),
        FilterDecision("A", "cn", "quantitative", "excluded",
                       ("turnover_below_minimum",), {}, {}, ()),
    ]
    with pytest.raises(ValueError, match="duplicate quantitative"):
        summarize_filter_decisions(duplicated)
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_market_filters.py -v
```

Expected: module import fails.

- [ ] **Step 3: 实现领域对象、预过滤和终态摘要**

创建 `src/lurker/universe/market_filters.py`：

```python
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from lurker.config import CnMarketFilters, MarketFilterPolicy


FilterStage = Literal["seed_prefilter", "quantitative"]
FilterStatus = Literal["included", "excluded", "included_with_warning"]


@dataclass(frozen=True)
class SourceEvidence:
    source: str
    data_date: str
    retrieved_at: str
    sha256: str
    hash_scope: str

    def __post_init__(self) -> None:
        if not self.source or not self.data_date or not self.retrieved_at:
            raise ValueError("source evidence fields are required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.sha256):
            raise ValueError("source evidence sha256 is invalid")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class FilterDecision:
    symbol: str
    market: str
    stage: FilterStage
    status: FilterStatus
    reason_codes: tuple[str, ...]
    metrics: dict[str, Any]
    thresholds: dict[str, Any]
    sources: tuple[SourceEvidence, ...]

    def __post_init__(self) -> None:
        if self.stage not in {"seed_prefilter", "quantitative"}:
            raise ValueError("invalid filter stage")
        if self.status not in {
            "included",
            "excluded",
            "included_with_warning",
        }:
            raise ValueError("invalid filter status")
        if tuple(sorted(set(self.reason_codes))) != self.reason_codes:
            raise ValueError("reason_codes must be unique and sorted")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "stage": self.stage,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "metrics": dict(self.metrics),
            "thresholds": dict(self.thresholds),
            "sources": [source.to_dict() for source in self.sources],
        }


def _missing_status(policy: MarketFilterPolicy) -> FilterStatus:
    return (
        "excluded"
        if policy.missing_data_policy == "exclude"
        else "included_with_warning"
    )


def _normalized_name(name: str) -> str:
    return re.sub(r"\s+", "", name).upper()


def evaluate_seed_prefilter(
    *,
    symbol: str,
    market: str,
    name: str | None,
    filters: CnMarketFilters,
    policy: MarketFilterPolicy,
    sources: tuple[SourceEvidence, ...] = (),
) -> FilterDecision:
    normalized_symbol = symbol.strip().upper()
    reasons: list[str] = []
    status: FilterStatus = "included"
    if (
        filters.exclude_beijing_exchange
        and normalized_symbol.endswith(".BJ")
    ):
        reasons.append("beijing_exchange_excluded")
        status = "excluded"
    if filters.exclude_st:
        if not name:
            reasons.append("symbol_name_missing")
            if status != "excluded":
                status = _missing_status(policy)
        elif re.match(r"^(?:\*ST|S\*ST|SST|ST)", _normalized_name(name)):
            reasons.append("st_name_excluded")
            status = "excluded"
    return FilterDecision(
        symbol=normalized_symbol,
        market=market,
        stage="seed_prefilter",
        status=status,
        reason_codes=tuple(sorted(set(reasons))),
        metrics={"name": name},
        thresholds={
            "exclude_beijing_exchange": float(
                filters.exclude_beijing_exchange
            ),
            "exclude_st": float(filters.exclude_st),
        },
        sources=sources,
    )


def summarize_filter_decisions(
    decisions: list[FilterDecision],
) -> dict[str, Any]:
    by_symbol: dict[tuple[str, str], FilterDecision] = {}
    quantitative_seen: set[tuple[str, str]] = set()
    for decision in decisions:
        key = (decision.market, decision.symbol)
        if decision.stage == "quantitative":
            if key in quantitative_seen:
                raise ValueError(
                    f"duplicate quantitative decision for {decision.symbol}"
                )
            quantitative_seen.add(key)
            by_symbol[key] = decision
        elif key not in quantitative_seen:
            by_symbol[key] = decision
    counts = {
        "included": 0,
        "excluded": 0,
        "included_with_warning": 0,
    }
    reason_counts: dict[str, int] = {}
    for decision in by_symbol.values():
        counts[decision.status] += 1
        for reason in decision.reason_codes:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        **counts,
        "reason_counts": dict(sorted(reason_counts.items())),
    }
```

- [ ] **Step 4: GREEN、lint 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_market_filters.py -v
.venv/bin/ruff check src/lurker/universe/market_filters.py tests/test_market_filters.py
git add src/lurker/universe/market_filters.py tests/test_market_filters.py
git commit -m "feat: add auditable market filter decisions"
```

---

### Task 3: 规范化逐日成交额和价格来源

**Files:**
- Modify: `src/lurker/ingest/prices.py`
- Modify: `tests/test_ingest.py`
- Modify: `src/lurker/storage/models.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: 写三类 A 股单位、yfinance turnover 和异常传播 RED**

在 `tests/test_ingest.py` 增加：

```python
from lurker.ingest.prices import (
    PriceSourceError,
    normalize_baostock_cn_price_frame,
    normalize_cn_price_frame,
    normalize_price_frame,
    normalize_tushare_cn_price_frame,
)


def test_akshare_cn_turnover_keeps_yuan_amount():
    raw = pd.DataFrame(
        [{
            "日期": "2026-07-24",
            "开盘": 10,
            "最高": 11,
            "最低": 9,
            "收盘": 10,
            "成交量": 100,
            "成交额": 123456.0,
        }]
    )
    result = normalize_cn_price_frame(raw, "600001.SH")
    assert result.iloc[0]["turnover"] == 123456.0


def test_tushare_amount_converts_thousand_yuan_to_yuan():
    raw = pd.DataFrame(
        [{
            "trade_date": "20260724",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "vol": 100,
            "amount": 123.456,
        }]
    )
    result = normalize_tushare_cn_price_frame(raw, "600001.SH")
    assert result.iloc[0]["turnover"] == pytest.approx(123456.0)


def test_baostock_amount_keeps_yuan():
    raw = pd.DataFrame(
        [{
            "date": "2026-07-24",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10",
            "volume": "100",
            "amount": "123456",
        }]
    )
    result = normalize_baostock_cn_price_frame(raw, "600001.SH")
    assert result.iloc[0]["turnover"] == 123456.0


def test_yfinance_turnover_uses_unadjusted_close():
    raw = pd.DataFrame(
        {
            "Date": [pd.Timestamp("2026-07-24")],
            "Open": [10.0],
            "High": [11.0],
            "Low": [9.0],
            "Close": [10.0],
            "Adj Close": [5.0],
            "Volume": [100.0],
        }
    )
    result = normalize_price_frame(raw, "NVDA")
    assert result.iloc[0]["turnover"] == 1000.0


def test_cn_fallback_only_catches_declared_source_errors():
    def provider_error(symbol, period):
        raise PriceSourceError("provider down")

    def programming_error(symbol, period):
        raise TypeError("programmer error")

    with pytest.raises(TypeError, match="programmer error"):
        fetch_cn_prices(
            "600001.SH",
            fetchers=[provider_error, programming_error],
            sleep_seconds=0,
        )
```

在 `tests/test_storage.py` 的 `PriceDaily` 测试中增加：

```python
assert stored.amount == 123456.0
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_ingest.py tests/test_storage.py -k "turnover or amount or declared" -v
```

Expected: `turnover` column and `PriceSourceError` are missing.

- [ ] **Step 3: 实现 canonical price frame**

在 `src/lurker/ingest/prices.py`：

```python
PRICE_COLUMNS = [
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "turnover",
]


class PriceSourceError(RuntimeError):
    pass


class PriceSchemaError(PriceSourceError):
    pass


def _required_columns(
    frame: pd.DataFrame,
    required: set[str],
    source: str,
) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise PriceSchemaError(
            f"{source} missing columns {sorted(missing)}"
        )


def _numeric(frame: pd.DataFrame, columns: list[str], source: str) -> None:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        invalid = frame[column].map(
            lambda value: pd.notna(value) and not math.isfinite(float(value))
        )
        if invalid.any():
            raise PriceSchemaError(f"{source} {column} must be finite")


def _source_attrs(
    frame: pd.DataFrame,
    *,
    source: str,
    retrieved_at: str | None = None,
) -> pd.DataFrame:
    frame.attrs["source"] = source
    frame.attrs["retrieved_at"] = (
        retrieved_at or pd.Timestamp.now(tz="UTC").isoformat()
    )
    return frame
```

将四个 normalizer 替换为：

```python
def normalize_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.copy()
        raw.columns = raw.columns.get_level_values(0)
    normalized = raw.rename(
        columns={
            "Date": "trade_date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
    ).copy()
    if "trade_date" not in normalized.columns:
        normalized = normalized.reset_index(names="trade_date")
    _required_columns(
        normalized,
        {"trade_date", "open", "high", "low", "close", "adj_close", "volume"},
        "yfinance",
    )
    _numeric(
        normalized,
        ["open", "high", "low", "close", "adj_close", "volume"],
        "yfinance",
    )
    normalized["turnover"] = normalized["close"] * normalized["volume"]
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"],
        errors="coerce",
    ).dt.date
    return normalized[PRICE_COLUMNS]


def normalize_cn_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = raw.rename(
        columns={
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "turnover",
        }
    ).copy()
    _required_columns(
        normalized,
        {"trade_date", "open", "high", "low", "close", "volume", "turnover"},
        "akshare",
    )
    _numeric(
        normalized,
        ["open", "high", "low", "close", "volume", "turnover"],
        "akshare",
    )
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"],
        errors="coerce",
    ).dt.date
    normalized["adj_close"] = normalized["close"]
    return normalized[PRICE_COLUMNS]


def normalize_tushare_cn_price_frame(
    raw: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame:
    normalized = raw.rename(
        columns={"vol": "volume", "amount": "turnover"}
    ).copy()
    _required_columns(
        normalized,
        {"trade_date", "open", "high", "low", "close", "volume", "turnover"},
        "tushare",
    )
    _numeric(
        normalized,
        ["open", "high", "low", "close", "volume", "turnover"],
        "tushare",
    )
    normalized["turnover"] = normalized["turnover"] * 1000.0
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"],
        errors="coerce",
    ).dt.date
    normalized["adj_close"] = normalized["close"]
    return normalized[PRICE_COLUMNS].sort_values(
        "trade_date"
    ).reset_index(drop=True)


def normalize_baostock_cn_price_frame(
    raw: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame:
    normalized = raw.rename(
        columns={"date": "trade_date", "amount": "turnover"}
    ).copy()
    _required_columns(
        normalized,
        {"trade_date", "open", "high", "low", "close", "volume", "turnover"},
        "baostock",
    )
    _numeric(
        normalized,
        ["open", "high", "low", "close", "volume", "turnover"],
        "baostock",
    )
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"],
        errors="coerce",
    ).dt.date
    normalized["adj_close"] = normalized["close"]
    return normalized[PRICE_COLUMNS].dropna(
        subset=["close"]
    ).reset_index(drop=True)
```

同步修改 `normalize_cn_index_price_frame`，指数不参与股票流动性过滤，但必须满足统一
frame schema：

```python
def normalize_cn_index_price_frame(
    raw: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame:
    normalized = raw.rename(columns=_resolve_cn_index_columns(raw)).copy()
    _numeric(
        normalized,
        ["open", "high", "low", "close", "volume"],
        "akshare CN index",
    )
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"],
        errors="coerce",
    ).dt.date
    normalized["adj_close"] = normalized["close"]
    normalized["turnover"] = normalized["close"] * normalized["volume"]
    return normalized[PRICE_COLUMNS].sort_values(
        "trade_date"
    ).reset_index(drop=True)
```

适配器成功后设置来源：

```python
return _source_attrs(
    normalize_price_frame(raw, symbol=symbol),
    source="yfinance.download",
)
```

Tushare、AkShare、Baostock 返回前分别调用：

```python
return _source_attrs(
    normalize_tushare_cn_price_frame(raw, symbol),
    source="tushare.pro_bar",
)
```

```python
return _source_attrs(
    normalize_cn_price_frame(raw, symbol),
    source="akshare.stock_zh_a_hist",
)
```

```python
return _source_attrs(
    normalize_baostock_cn_price_frame(raw, symbol),
    source="baostock.query_history_k_data_plus",
)
```
Baostock 请求字段改为：

```python
"date,code,open,high,low,close,volume,amount"
```

`fetch_cn_prices` 只捕获 `PriceSourceError`：

```python
for index, fetcher in enumerate(providers):
    try:
        result = fetcher(symbol, period)
        if result.empty:
            raise PriceSourceError("empty price data")
        return result
    except PriceSourceError as exc:
        errors.append(f"{fetcher.__name__}: {exc}")
        if sleep_seconds > 0 and index < len(providers) - 1:
            time.sleep(sleep_seconds)
raise PriceSourceError("; ".join(errors))
```

增加统一、明确的 provider 调用边界，并从 `typing` 引入 `TypeVar`：

```python
ProviderResult = TypeVar("ProviderResult")


def _call_price_provider(
    *,
    provider_name: str,
    symbol: str,
    call: Callable[[], ProviderResult],
) -> ProviderResult:
    try:
        return call()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        raise PriceSourceError(
            f"{provider_name} failed for {symbol}: {exc}"
        ) from exc
```

四个 adapter 把 provider 调用放进同一边界，调用参数固定如下。

yfinance：

```python
raw = _call_price_provider(
    provider_name="yfinance.download",
    symbol=symbol,
    call=lambda: yf.download(
        to_yfinance_symbol(symbol),
        period=period,
        progress=False,
        auto_adjust=False,
        multi_level_index=False,
    ),
)
```

Tushare（缺少 token 也转换为可回退的 `PriceSourceError`）：

```python
if not resolved_token:
    raise PriceSourceError("TUSHARE_TOKEN is not set")
raw = _call_price_provider(
    provider_name="tushare.pro_bar",
    symbol=symbol,
    call=lambda: ts.pro_bar(
        ts_code=symbol,
        adj="qfq",
        start_date=period_to_start_date(period),
        end_date=today_yyyymmdd(),
        token=resolved_token,
    ),
)
if raw is None or raw.empty:
    raise PriceSourceError("tushare.pro_bar returned empty price data")
```

AkShare：

```python
raw = _call_price_provider(
    provider_name="akshare.stock_zh_a_hist",
    symbol=symbol,
    call=lambda: ak.stock_zh_a_hist(
        symbol=to_akshare_symbol(symbol),
        period="daily",
        start_date=period_to_start_date(period),
        adjust="qfq",
    ),
)
```

Baostock：

```python
login = _call_price_provider(
    provider_name="baostock.login",
    symbol=symbol,
    call=bs.login,
)
if getattr(login, "error_code", "0") != "0":
    raise PriceSourceError(
        "baostock login failed: "
        f"{getattr(login, 'error_msg', '')}"
    )
try:
    result = _call_price_provider(
        provider_name="baostock.query_history_k_data_plus",
        symbol=symbol,
        call=lambda: bs.query_history_k_data_plus(
            to_baostock_symbol(symbol),
            "date,code,open,high,low,close,volume,amount",
            start_date=pd.to_datetime(
                period_to_start_date(period)
            ).strftime("%Y-%m-%d"),
            end_date=pd.Timestamp.today().strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag="2",
        ),
    )
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise PriceSourceError(
            f"baostock query failed: {result.error_msg}"
        )
    raw = pd.DataFrame(rows, columns=result.fields)
    if raw.empty:
        raise PriceSourceError("baostock returned empty price data")
finally:
    bs.logout()
```

normalizer 的 `PriceSchemaError` 原样向上传播；不要捕获 `TypeError` 或
`AttributeError`。

保存 `PriceDaily` 时把：

```python
amount=(
    float(row["turnover"])
    if pd.notna(row.get("turnover"))
    else None
),
```

传入现有 `amount` 字段。

- [ ] **Step 4: GREEN、回归、lint 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_ingest.py tests/test_storage.py -v
.venv/bin/ruff check src/lurker/ingest/prices.py src/lurker/storage/models.py tests/test_ingest.py tests/test_storage.py
git add src/lurker/ingest/prices.py src/lurker/storage/models.py tests/test_ingest.py tests/test_storage.py
git commit -m "feat: normalize daily turnover across price providers"
```

---

### Task 4: 独立美股市值 metadata adapter

**Files:**
- Create: `src/lurker/ingest/equity_metadata.py`
- Create: `tests/test_equity_metadata.py`

- [ ] **Step 1: 写 metadata RED**

创建 `tests/test_equity_metadata.py`：

```python
from datetime import UTC, datetime

import pytest

from lurker.ingest.equity_metadata import (
    EquityMetadataSchemaError,
    EquityMetadataSourceError,
    fetch_us_equity_metadata,
    normalize_us_equity_metadata,
)


def _raw(**overrides):
    data = {
        "marketCap": 5_000_000_000,
        "currency": "USD",
        "quoteType": "EQUITY",
        "regularMarketTime": 1753372800,
        "exchangeTimezoneName": "America/New_York",
    }
    data.update(overrides)
    return data


def test_normalize_us_metadata_has_date_hash_and_units():
    result = normalize_us_equity_metadata(
        "NVDA",
        _raw(),
        retrieved_at="2026-07-26T12:00:00+00:00",
    )
    assert result.symbol == "NVDA"
    assert result.market_cap_usd == 5_000_000_000.0
    assert result.currency == "USD"
    assert result.quote_type == "EQUITY"
    assert result.data_date == "2025-07-24"
    assert result.source_hash.startswith("sha256:")


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"marketCap": None}, "market_cap_missing"),
        ({"marketCap": float("nan")}, "market_cap_missing"),
        ({"marketCap": 0}, "market_cap_missing"),
        ({"currency": "EUR"}, "market_cap_currency_invalid"),
        ({"quoteType": "ETF"}, "market_cap_quote_type_invalid"),
        ({"regularMarketTime": "bad"}, "market_cap_timestamp_invalid"),
    ],
)
def test_normalize_us_metadata_fails_with_stable_reason(overrides, reason_code):
    with pytest.raises(EquityMetadataSchemaError) as raised:
        normalize_us_equity_metadata(
            "NVDA",
            _raw(**overrides),
            retrieved_at="2026-07-26T12:00:00+00:00",
        )
    assert raised.value.reason_code == reason_code


def test_missing_exchange_timezone_uses_utc_and_warns():
    result = normalize_us_equity_metadata(
        "NVDA",
        _raw(exchangeTimezoneName=None),
        retrieved_at="2026-07-26T12:00:00+00:00",
    )
    assert result.warnings == ("market_cap_timezone_missing",)


def test_fetch_us_metadata_wraps_provider_failure_but_not_type_error():
    class ProviderFailureTicker:
        def get_info(self):
            raise ValueError("provider down")

    with pytest.raises(EquityMetadataSourceError, match="provider down"):
        fetch_us_equity_metadata(
            "NVDA",
            ticker_factory=lambda symbol: ProviderFailureTicker(),
        )

    class ProgrammingFailureTicker:
        def get_info(self):
            raise TypeError("programmer error")

    with pytest.raises(TypeError, match="programmer error"):
        fetch_us_equity_metadata(
            "NVDA",
            ticker_factory=lambda symbol: ProgrammingFailureTicker(),
        )
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_equity_metadata.py -v
```

Expected: module import fails.

- [ ] **Step 3: 实现 adapter**

创建 `src/lurker/ingest/equity_metadata.py`：

```python
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yfinance as yf


class EquityMetadataSourceError(RuntimeError):
    pass


class EquityMetadataSchemaError(EquityMetadataSourceError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class EquityMetadata:
    symbol: str
    market_cap_usd: float
    currency: str
    quote_type: str
    data_date: str
    retrieved_at: str
    source: str
    source_hash: str
    warnings: tuple[str, ...] = ()


def _finite_positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def normalize_us_equity_metadata(
    symbol: str,
    raw: dict[str, Any],
    *,
    retrieved_at: str,
) -> EquityMetadata:
    market_cap = _finite_positive(raw.get("marketCap"))
    if market_cap is None:
        raise EquityMetadataSchemaError(
            "market_cap_missing",
            f"{symbol} marketCap must be finite positive",
        )
    currency = str(raw.get("currency", "")).upper()
    if currency != "USD":
        raise EquityMetadataSchemaError(
            "market_cap_currency_invalid",
            f"{symbol} marketCap currency must be USD",
        )
    quote_type = str(raw.get("quoteType", "")).upper()
    if quote_type != "EQUITY":
        raise EquityMetadataSchemaError(
            "market_cap_quote_type_invalid",
            f"{symbol} quoteType must be EQUITY",
        )
    timestamp = _finite_positive(raw.get("regularMarketTime"))
    if timestamp is None:
        raise EquityMetadataSchemaError(
            "market_cap_timestamp_invalid",
            f"{symbol} regularMarketTime is invalid",
        )
    warnings: tuple[str, ...] = ()
    timezone_name = raw.get("exchangeTimezoneName")
    if timezone_name:
        try:
            timezone = ZoneInfo(str(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise EquityMetadataSchemaError(
                "market_cap_timestamp_invalid",
                f"{symbol} exchange timezone is invalid",
            ) from exc
    else:
        timezone = UTC
        warnings = ("market_cap_timezone_missing",)
    data_date = datetime.fromtimestamp(timestamp, tz=timezone).date().isoformat()
    normalized = {
        "symbol": symbol.upper(),
        "market_cap_usd": market_cap,
        "currency": currency,
        "quote_type": quote_type,
        "data_date": data_date,
    }
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return EquityMetadata(
        **normalized,
        retrieved_at=retrieved_at,
        source="yfinance.quote_metadata",
        source_hash=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        warnings=warnings,
    )


TickerFactory = Callable[[str], Any]


def fetch_us_equity_metadata(
    symbol: str,
    *,
    ticker_factory: TickerFactory = yf.Ticker,
    clock: Callable[[], str] | None = None,
) -> EquityMetadata:
    retrieved_at = (
        clock() if clock else datetime.now(UTC).isoformat()
    )
    try:
        raw = ticker_factory(symbol).get_info()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        raise EquityMetadataSourceError(
            f"{symbol} quote metadata failed: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise EquityMetadataSchemaError(
            "market_cap_missing",
            f"{symbol} quote metadata must be a mapping",
        )
    return normalize_us_equity_metadata(
        symbol,
        raw,
        retrieved_at=retrieved_at,
    )
```

- [ ] **Step 4: GREEN、lint 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_equity_metadata.py -v
.venv/bin/ruff check src/lurker/ingest/equity_metadata.py tests/test_equity_metadata.py
git add src/lurker/ingest/equity_metadata.py tests/test_equity_metadata.py
git commit -m "feat: collect dated US equity market cap metadata"
```

---

### Task 5: 可审计名称解析和 seed pool 预过滤

**Files:**
- Modify: `src/lurker/ingest/constituents.py`
- Modify: `src/lurker/universe/resolved_seed_pool.py`
- Modify: `tests/test_resolved_seed_pool.py`
- Modify: `tests/test_ingest.py`

- [ ] **Step 1: 写名称结果、ST/BJ、来源同步和原子保存 RED**

在 `tests/test_ingest.py` 增加：

```python
from lurker.ingest.constituents import (
    CnNameResolution,
    SymbolNameSourceError,
    resolve_cn_symbol_names,
)


def test_resolve_cn_symbol_names_returns_auditable_result():
    raw = pd.DataFrame(
        [
            {"code": "600001", "name": "普通公司"},
            {"code": "600002", "name": "ST风险"},
        ]
    )
    result = resolve_cn_symbol_names(
        ["600001.SH", "600002.SH", "600003.SH"],
        fetcher=lambda: raw,
        clock=lambda: "2026-07-26T12:00:00+00:00",
    )
    assert isinstance(result, CnNameResolution)
    assert result.names == {
        "600001.SH": "普通公司",
        "600002.SH": "ST风险",
    }
    assert result.missing_symbols == ("600003.SH",)
    assert result.source_hash.startswith("sha256:")


def test_resolve_cn_symbol_names_wraps_provider_error_not_type_error():
    with pytest.raises(SymbolNameSourceError, match="provider down"):
        resolve_cn_symbol_names(
            ["600001.SH"],
            fetcher=lambda: (_ for _ in ()).throw(ValueError("provider down")),
        )
    with pytest.raises(TypeError, match="programmer error"):
        resolve_cn_symbol_names(
            ["600001.SH"],
            fetcher=lambda: (_ for _ in ()).throw(TypeError("programmer error")),
        )
```

在 `tests/test_resolved_seed_pool.py` 增加完整 fixture：

```python
from lurker.config import load_markets
from lurker.ingest.constituents import CnNameResolution


def _markets_path(tmp_path, policy="exclude"):
    path = tmp_path / "markets.yaml"
    path.write_text(
        f"""
schema_version: 1
filter_policy:
  missing_data_policy: {policy}
  turnover_window_trading_days: 20
  min_turnover_observations: 15
  us_market_cap_max_age_days: 7
markets:
  cn:
    name: A 股
    role: primary_discovery
    universe_sources: [测试]
    filters:
      exclude_st: true
      exclude_beijing_exchange: true
      min_avg_turnover_cny: 50000000
  us:
    name: 美股
    role: global_anchor
    universe_sources: [测试]
    filters:
      min_market_cap_usd: 2000000000
      min_avg_turnover_usd: 10000000
  hk:
    name: 港股
    role: mapping_supplement
    universe_sources: [测试]
    filters:
      min_price_hkd: 1
      min_avg_turnover_hkd: 20000000
      exclude_shell_like: false
      exclude_frequent_capital_actions: false
""",
        encoding="utf-8",
    )
    return path


def _names(names):
    return CnNameResolution(
        names=names,
        missing_symbols=(),
        source="akshare.stock_info_a_code_name",
        retrieved_at="2026-07-26T12:00:00+00:00",
        source_hash="sha256:" + "1" * 64,
    )


def test_seed_pool_filters_all_sources_and_keeps_decisions(tmp_path):
    themes = tmp_path / "themes.yaml"
    themes.write_text(
        """
themes:
  - id: demo
    markets:
      cn:
        seed_symbols: [600001.SH, 600002.SH, 430001.BJ]
        seed_indexes: [测试指数]
        seed_etfs: [测试 ETF]
""",
        encoding="utf-8",
    )
    pool = build_resolved_seed_pool(
        themes,
        markets_path=_markets_path(tmp_path),
        generated_at="2026-07-26T12:00:00+00:00",
        cn_index_resolver=lambda name: ["600003.SH", "830001.BJ"],
        cn_etf_resolver=lambda name: ["600004.SH"],
        cn_symbol_name_resolver=lambda symbols: _names(
            {
                "600001.SH": "普通一",
                "600002.SH": "*ST风险",
                "600003.SH": "普通三",
                "600004.SH": "SST风险",
                "430001.BJ": "北交所一",
                "830001.BJ": "北交所二",
            }
        ),
    )
    assert pool["schema_version"] == 2
    assert pool["markets"]["cn"]["symbols"] == ["600001.SH", "600003.SH"]
    assert pool["markets"]["cn"]["sources"]["manual"] == ["600001.SH"]
    assert pool["markets"]["cn"]["sources"]["indexes"] == {
        "测试指数": ["600003.SH"]
    }
    assert pool["markets"]["cn"]["sources"]["etfs"] == {"测试 ETF": []}
    assert "600002.SH" not in pool["theme_mapping"]
    assert "430001.BJ" not in pool["theme_mapping"]
    assert pool["filter_summary"]["excluded"] == 4
    reasons = {
        reason
        for item in pool["filter_decisions"]
        for reason in item["reason_codes"]
    }
    assert reasons == {"beijing_exchange_excluded", "st_name_excluded"}


@pytest.mark.parametrize(
    ("policy", "expected_symbols", "status"),
    [
        ("exclude", [], "excluded"),
        ("include_with_warning", ["600001.SH"], "included_with_warning"),
    ],
)
def test_seed_pool_applies_missing_name_policy(
    tmp_path,
    policy,
    expected_symbols,
    status,
):
    themes = tmp_path / "themes.yaml"
    themes.write_text(
        """
themes:
  - id: demo
    markets:
      cn:
        seed_symbols: [600001.SH]
""",
        encoding="utf-8",
    )
    pool = build_resolved_seed_pool(
        themes,
        markets_path=_markets_path(tmp_path, policy),
        cn_symbol_name_resolver=lambda symbols: _names({}),
    )
    assert pool["markets"]["cn"]["symbols"] == expected_symbols
    assert pool["filter_decisions"][0]["status"] == status


def test_seed_pool_save_is_atomic_and_cleans_temp_on_failure(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "pool.json"
    monkeypatch.setattr(
        json,
        "dump",
        lambda value, handle, **kwargs: (_ for _ in ()).throw(
            TypeError("json failure")
        ),
    )
    with pytest.raises(TypeError, match="json failure"):
        save_resolved_seed_pool(
            {"schema_version": 2, "markets": {}},
            path,
        )
    assert not path.exists()
    assert list(tmp_path.glob("*.tmp")) == []
```

把现有 `test_build_resolved_seed_pool_includes_symbol_names` 的 resolver 改为：

```python
cn_symbol_name_resolver=lambda symbols: _names(
    {
        "300308.SZ": "中际旭创",
        "300502.SZ": "新易盛",
    }
),
```

把现有 `test_build_resolved_seed_pool_keeps_source_attribution` 增加：

```python
markets_path=_markets_path(tmp_path),
cn_symbol_name_resolver=lambda symbols: _names(
    {
        "300308.SZ": "中际旭创",
        "300502.SZ": "新易盛",
        "002230.SZ": "科大讯飞",
    }
),
```

`test_build_resolved_seed_pool_includes_symbol_names` 同样增加
`markets_path=_markets_path(tmp_path)`。

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_ingest.py tests/test_resolved_seed_pool.py -k "name or filter or atomic" -v
```

Expected: typed name resolution and seed filter fields are missing.

- [ ] **Step 3: 实现名称解析结果**

在 `src/lurker/ingest/constituents.py` 增加：

```python
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime


class SymbolNameSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class CnNameResolution:
    names: dict[str, str]
    missing_symbols: tuple[str, ...]
    source: str
    retrieved_at: str
    source_hash: str
```

用以下实现替换 `resolve_cn_symbol_names`：

```python
def resolve_cn_symbol_names(
    symbols: list[str],
    *,
    fetcher: Callable[[], pd.DataFrame] = ak.stock_info_a_code_name,
    clock: Callable[[], str] | None = None,
) -> CnNameResolution:
    wanted = {symbol.upper() for symbol in symbols}
    retrieved_at = (
        clock() if clock else datetime.now(UTC).isoformat()
    )
    if not wanted:
        return CnNameResolution(
            names={},
            missing_symbols=(),
            source="akshare.stock_info_a_code_name",
            retrieved_at=retrieved_at,
            source_hash=f"sha256:{hashlib.sha256(b'[]').hexdigest()}",
        )
    try:
        raw = fetcher()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        raise SymbolNameSourceError(
            f"CN symbol name provider failed: {exc}"
        ) from exc
    if raw.empty:
        raise SymbolNameSourceError("CN symbol name provider returned empty data")
    code_column = (
        "code" if "code" in raw.columns
        else "代码" if "代码" in raw.columns
        else None
    )
    name_column = (
        "name" if "name" in raw.columns
        else "名称" if "名称" in raw.columns
        else None
    )
    if code_column is None or name_column is None:
        raise SymbolNameSourceError(
            "CN symbol name provider missing code/name columns"
        )
    names: dict[str, str] = {}
    for row in raw.to_dict(orient="records"):
        symbol = format_cn_stock_symbol(str(row[code_column])).upper()
        name = str(row[name_column]).strip()
        if symbol in wanted and name:
            names[symbol] = name
    canonical = [
        {"symbol": symbol, "name": names[symbol]}
        for symbol in sorted(names)
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CnNameResolution(
        names=names,
        missing_symbols=tuple(sorted(wanted - set(names))),
        source="akshare.stock_info_a_code_name",
        retrieved_at=retrieved_at,
        source_hash=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    )
```

- [ ] **Step 4: 实现 seed pool schema v2 和同步过滤**

在 `resolved_seed_pool.py` 增加原子写入和过滤依赖：

```python
import hashlib
import os
import tempfile

from lurker.config import load_markets
from lurker.ingest.constituents import (
    CnNameResolution,
    SymbolNameSourceError,
)
from lurker.universe.market_filters import (
    FilterDecision,
    SourceEvidence,
    evaluate_seed_prefilter,
    summarize_filter_decisions,
)
```

并增加：

```python
def _name_evidence(
    resolution: CnNameResolution,
) -> tuple[SourceEvidence, ...]:
    return (
        SourceEvidence(
            source=resolution.source,
            data_date=resolution.retrieved_at[:10],
            retrieved_at=resolution.retrieved_at,
            sha256=resolution.source_hash,
            hash_scope="normalized_symbol_names",
        ),
    )


def _retain_source_symbols(
    sources: dict[str, Any],
    retained: set[str],
) -> None:
    sources["manual"] = [
        symbol for symbol in sources["manual"] if symbol in retained
    ]
    for group in ("indexes", "etfs"):
        for name, symbols in sources[group].items():
            sources[group][name] = [
                symbol for symbol in symbols if symbol in retained
            ]
```

`build_resolved_seed_pool` 启动时加载一次：

```python
markets_config = load_markets(markets_path)
```

合并全部 CN symbol 后调用名称 resolver；若它抛 `SymbolNameSourceError`，记录：

```python
failures.append(
    {
        "source": "akshare.stock_info_a_code_name",
        "market": "cn",
        "reason": str(exc),
    }
)
resolution = CnNameResolution(
    names={},
    missing_symbols=tuple(sorted(cn_symbols)),
    source="akshare.stock_info_a_code_name",
    retrieved_at=generated_at_value,
    source_hash=f"sha256:{hashlib.sha256(b'[]').hexdigest()}",
)
```

对每个 CN symbol 调用：

```python
decision = evaluate_seed_prefilter(
    symbol=symbol,
    market="cn",
    name=resolution.names.get(symbol),
    filters=markets_config.profiles["cn"].filters,
    policy=markets_config.policy,
    sources=_name_evidence(resolution),
)
```

US/HK symbol 生成 `stage=seed_prefilter`、`status=included` 的空原因决定。只保留
`included`、`included_with_warning`，调用 `_retain_source_symbols` 同步来源。构建
theme mapping 时只加入 retained set 中的 symbol。

返回值为：

```python
return {
    "schema_version": 2,
    "generated_at": generated_at_value,
    "filter_config_hash": markets_config.filter_config_hash,
    "markets": markets,
    "theme_mapping": theme_mapping,
    "symbol_names": resolution.names,
    "filter_decisions": [item.to_dict() for item in decisions],
    "filter_summary": summarize_filter_decisions(decisions),
    "failures": failures,
}
```

把 `save_resolved_seed_pool` 改为：

```python
def save_resolved_seed_pool(
    pool: ResolvedSeedPool,
    path: str | Path,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                pool,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
```

- [ ] **Step 5: GREEN、全文件回归、lint 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_ingest.py tests/test_resolved_seed_pool.py -v
.venv/bin/ruff check src/lurker/ingest/constituents.py src/lurker/universe/resolved_seed_pool.py tests/test_ingest.py tests/test_resolved_seed_pool.py
git add src/lurker/ingest/constituents.py src/lurker/universe/resolved_seed_pool.py tests/test_ingest.py tests/test_resolved_seed_pool.py
git commit -m "feat: apply auditable seed pool prefilters"
```

---

### Task 6: 固定窗口指标和量化过滤真值表

**Files:**
- Modify: `src/lurker/universe/market_filters.py`
- Modify: `tests/test_market_filters.py`

- [ ] **Step 1: 写窗口、边界、时间和缺失策略 RED**

在 `tests/test_market_filters.py` 增加：

```python
from datetime import date, timedelta

import pandas as pd

from lurker.config import HkMarketFilters, UsMarketFilters
from lurker.ingest.equity_metadata import EquityMetadata
from lurker.universe.market_filters import (
    FilterInputSchemaError,
    calculate_price_filter_metrics,
    evaluate_quantitative_filters,
)


def _prices(days=20, close=10.0, turnover=100.0):
    start = date(2026, 7, 1)
    frame = pd.DataFrame(
        [
            {
                "trade_date": start + timedelta(days=index),
                "close": close,
                "turnover": turnover,
            }
            for index in range(days)
        ]
    )
    frame.attrs.update(
        source="fixture.prices",
        retrieved_at="2026-07-26T12:00:00+00:00",
    )
    return frame


def _metadata(
    cap=5_000_000_000.0,
    data_date="2026-07-20",
    warnings=(),
):
    return EquityMetadata(
        symbol="NVDA",
        market_cap_usd=cap,
        currency="USD",
        quote_type="EQUITY",
        data_date=data_date,
        retrieved_at="2026-07-26T12:00:00+00:00",
        source="fixture.metadata",
        source_hash="sha256:" + "2" * 64,
        warnings=warnings,
    )


def test_metrics_use_last_twenty_not_whole_period():
    frame = _prices(days=25, turnover=100.0)
    frame.loc[:4, "turnover"] = 10_000.0
    metrics, evidence = calculate_price_filter_metrics(
        frame,
        snapshot_date=date(2026, 7, 25),
        window=20,
        minimum_observations=15,
    )
    assert metrics["avg_turnover"] == 100.0
    assert metrics["turnover_observations"] == 20
    assert evidence.data_date == "2026-07-25"


def test_metrics_count_zero_but_ignore_non_finite_observations():
    frame = _prices(days=20, turnover=100.0)
    frame.loc[0, "turnover"] = 0.0
    frame.loc[1, "turnover"] = float("nan")
    metrics, _ = calculate_price_filter_metrics(
        frame,
        snapshot_date=date(2026, 7, 25),
        window=20,
        minimum_observations=15,
    )
    assert metrics["turnover_observations"] == 19
    assert metrics["avg_turnover"] == pytest.approx(1800.0 / 19)


def test_metrics_reject_duplicate_dates_and_negative_turnover():
    duplicate = pd.concat([_prices(), _prices().iloc[[0]]], ignore_index=True)
    with pytest.raises(FilterInputSchemaError, match="duplicate trade date"):
        calculate_price_filter_metrics(
            duplicate,
            snapshot_date=date(2026, 7, 25),
            window=20,
            minimum_observations=15,
        )
    negative = _prices()
    negative.loc[0, "turnover"] = -1
    with pytest.raises(FilterInputSchemaError, match="negative turnover"):
        calculate_price_filter_metrics(
            negative,
            snapshot_date=date(2026, 7, 25),
            window=20,
            minimum_observations=15,
        )


def test_hk_exact_price_and_turnover_thresholds_pass():
    decision = evaluate_quantitative_filters(
        symbol="0700.HK",
        market="hk",
        metrics={
            "latest_close": 1.0,
            "avg_turnover": 20_000_000.0,
            "turnover_observations": 20,
        },
        price_source=SourceEvidence(
            "fixture", "2026-07-24", "2026-07-26T12:00:00+00:00",
            "sha256:" + "0" * 64, "normalized_filter_window",
        ),
        filters=HkMarketFilters(1.0, 20_000_000.0, False, False),
        policy=POLICY_EXCLUDE,
        snapshot_date=date(2026, 7, 26),
    )
    assert decision.status == "included"


def test_us_exact_market_cap_threshold_passes():
    decision = evaluate_quantitative_filters(
        symbol="NVDA",
        market="us",
        metrics={
            "latest_close": 10.0,
            "avg_turnover": 10_000_000.0,
            "turnover_observations": 20,
        },
        price_source=SourceEvidence(
            "fixture", "2026-07-24", "2026-07-26T12:00:00+00:00",
            "sha256:" + "0" * 64, "normalized_filter_window",
        ),
        filters=UsMarketFilters(2_000_000_000.0, 10_000_000.0),
        policy=POLICY_EXCLUDE,
        snapshot_date=date(2026, 7, 26),
        metadata=_metadata(cap=2_000_000_000.0),
    )
    assert decision.status == "included"


@pytest.mark.parametrize(
    ("metadata", "metadata_error", "reason"),
    [
        (None, None, "market_cap_missing"),
        (_metadata(data_date="2026-07-27"), None, "market_cap_from_future"),
        (_metadata(data_date="2026-07-18"), None, "market_cap_stale"),
        (None, "market_cap_currency_invalid", "market_cap_currency_invalid"),
    ],
)
def test_us_market_cap_missing_and_time_reasons(
    metadata,
    metadata_error,
    reason,
):
    decision = evaluate_quantitative_filters(
        symbol="NVDA",
        market="us",
        metrics={
            "latest_close": 10.0,
            "avg_turnover": 20_000_000.0,
            "turnover_observations": 20,
        },
        price_source=SourceEvidence(
            "fixture", "2026-07-24", "2026-07-26T12:00:00+00:00",
            "sha256:" + "0" * 64, "normalized_filter_window",
        ),
        filters=UsMarketFilters(2_000_000_000.0, 10_000_000.0),
        policy=POLICY_EXCLUDE,
        snapshot_date=date(2026, 7, 26),
        metadata=metadata,
        metadata_error_reason=metadata_error,
    )
    assert decision.status == "excluded"
    assert reason in decision.reason_codes


def test_known_threshold_failure_wins_over_warning_policy():
    decision = evaluate_quantitative_filters(
        symbol="NVDA",
        market="us",
        metrics={
            "latest_close": 10.0,
            "avg_turnover": 1.0,
            "turnover_observations": 20,
        },
        price_source=SourceEvidence(
            "fixture", "2026-07-24", "2026-07-26T12:00:00+00:00",
            "sha256:" + "0" * 64, "normalized_filter_window",
        ),
        filters=UsMarketFilters(2_000_000_000.0, 10_000_000.0),
        policy=POLICY_WARN,
        snapshot_date=date(2026, 7, 26),
        metadata=None,
    )
    assert decision.status == "excluded"
    assert set(decision.reason_codes) == {
        "market_cap_missing",
        "turnover_below_minimum",
    }
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_market_filters.py -k "metrics or quantitative or threshold or market_cap" -v
```

Expected: metric and quantitative functions are missing.

- [ ] **Step 3: 实现固定窗口 metrics**

在 `market_filters.py` 增加：

```python
import hashlib
import json
import math
from datetime import date

import pandas as pd

from lurker.config import (
    CnMarketFilters,
    HkMarketFilters,
    MarketFilters,
    UsMarketFilters,
)
from lurker.ingest.equity_metadata import EquityMetadata


class FilterInputSchemaError(RuntimeError):
    pass


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def calculate_price_filter_metrics(
    frame: pd.DataFrame,
    *,
    snapshot_date: date,
    window: int,
    minimum_observations: int,
) -> tuple[dict[str, Any], SourceEvidence]:
    required = {"trade_date", "close", "turnover"}
    missing = required - set(frame.columns)
    if missing:
        raise FilterInputSchemaError(
            f"price filter input missing columns {sorted(missing)}"
        )
    normalized = frame.copy()
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"],
        errors="coerce",
    ).dt.date
    if normalized["trade_date"].isna().any():
        raise FilterInputSchemaError("invalid trade date")
    if normalized["trade_date"].duplicated().any():
        raise FilterInputSchemaError("duplicate trade date")
    normalized = normalized.loc[
        normalized["trade_date"] <= snapshot_date
    ].sort_values("trade_date")
    normalized["close"] = pd.to_numeric(
        normalized["close"], errors="coerce"
    )
    normalized["turnover"] = pd.to_numeric(
        normalized["turnover"], errors="coerce"
    )
    negative = normalized["turnover"].map(
        lambda value: _finite(value) is not None and float(value) < 0
    )
    if negative.any():
        raise FilterInputSchemaError("negative turnover")
    valid_closes = [
        number
        for raw_close in normalized["close"].tolist()
        if (
            (number := _finite(raw_close)) is not None
            and number > 0
        )
    ]
    latest_close = valid_closes[-1] if valid_closes else None
    valid = normalized.loc[
        normalized["turnover"].map(
            lambda value: (
                (number := _finite(value)) is not None and number >= 0
            )
        )
    ].tail(window)
    observation_count = len(valid)
    average = (
        float(valid["turnover"].mean())
        if observation_count >= minimum_observations
        else None
    )
    canonical = [
        {
            "trade_date": row["trade_date"].isoformat(),
            "close": _finite(row["close"]),
            "turnover": _finite(row["turnover"]),
        }
        for row in valid.to_dict(orient="records")
    ]
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    source = str(frame.attrs.get("source", "")).strip()
    retrieved_at = str(frame.attrs.get("retrieved_at", "")).strip()
    if not source or not retrieved_at:
        raise FilterInputSchemaError("price source metadata is missing")
    data_date = (
        normalized["trade_date"].max().isoformat()
        if not normalized.empty
        else snapshot_date.isoformat()
    )
    return (
        {
            "latest_close": latest_close,
            "avg_turnover": average,
            "turnover_observations": observation_count,
        },
        SourceEvidence(
            source=source,
            data_date=data_date,
            retrieved_at=retrieved_at,
            sha256=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
            hash_scope="normalized_filter_window",
        ),
    )
```

- [ ] **Step 4: 实现量化真值表**

继续增加：

```python
def evaluate_quantitative_filters(
    *,
    symbol: str,
    market: str,
    metrics: dict[str, Any],
    price_source: SourceEvidence,
    filters: MarketFilters,
    policy: MarketFilterPolicy,
    snapshot_date: date,
    metadata: EquityMetadata | None = None,
    metadata_error_reason: str | None = None,
) -> FilterDecision:
    known_failures: set[str] = set()
    missing_reasons: set[str] = set()
    warning_reasons: set[str] = set()
    thresholds: dict[str, Any] = {}
    output_metrics: dict[str, Any] = dict(metrics)
    sources = [price_source]

    average = _finite(metrics.get("avg_turnover"))
    observations = int(metrics.get("turnover_observations") or 0)
    if isinstance(filters, CnMarketFilters):
        minimum_turnover = filters.min_avg_turnover_cny
        turnover_key = "avg_turnover_cny"
    elif isinstance(filters, UsMarketFilters):
        minimum_turnover = filters.min_avg_turnover_usd
        turnover_key = "avg_turnover_usd"
    else:
        minimum_turnover = filters.min_avg_turnover_hkd
        turnover_key = "avg_turnover_hkd"
    output_metrics[turnover_key] = average
    if minimum_turnover is not None:
        thresholds[f"min_{turnover_key}"] = minimum_turnover
        if observations < policy.min_turnover_observations:
            missing_reasons.add("turnover_observations_insufficient")
        elif average is None:
            missing_reasons.add("turnover_data_missing")
        elif average < minimum_turnover:
            known_failures.add("turnover_below_minimum")

    if isinstance(filters, HkMarketFilters):
        latest = _finite(metrics.get("latest_close"))
        output_metrics["latest_close_hkd"] = latest
        if filters.min_price_hkd is not None:
            thresholds["min_price_hkd"] = filters.min_price_hkd
            if latest is None or latest <= 0:
                missing_reasons.add("latest_price_missing")
            elif latest < filters.min_price_hkd:
                known_failures.add("latest_price_below_minimum")

    if isinstance(filters, UsMarketFilters):
        if filters.min_market_cap_usd is not None:
            thresholds["min_market_cap_usd"] = filters.min_market_cap_usd
            if metadata_error_reason:
                missing_reasons.add(metadata_error_reason)
            elif metadata is None:
                missing_reasons.add("market_cap_missing")
            else:
                sources.append(
                    SourceEvidence(
                        source=metadata.source,
                        data_date=metadata.data_date,
                        retrieved_at=metadata.retrieved_at,
                        sha256=metadata.source_hash,
                        hash_scope="normalized_metadata",
                    )
                )
                output_metrics["market_cap_usd"] = metadata.market_cap_usd
                metadata_day = date.fromisoformat(metadata.data_date)
                if metadata_day > snapshot_date:
                    missing_reasons.add("market_cap_from_future")
                elif (
                    snapshot_date - metadata_day
                ).days > policy.us_market_cap_max_age_days:
                    missing_reasons.add("market_cap_stale")
                elif metadata.market_cap_usd < filters.min_market_cap_usd:
                    known_failures.add("market_cap_below_minimum")
                warning_reasons.update(metadata.warnings)

    reasons = tuple(
        sorted(known_failures | missing_reasons | warning_reasons)
    )
    if known_failures:
        status: FilterStatus = "excluded"
    elif missing_reasons:
        status = _missing_status(policy)
    elif warning_reasons:
        status = "included_with_warning"
    else:
        status = "included"
    return FilterDecision(
        symbol=symbol.upper(),
        market=market,
        stage="quantitative",
        status=status,
        reason_codes=reasons,
        metrics=output_metrics,
        thresholds=thresholds,
        sources=tuple(sources),
    )
```

- [ ] **Step 5: GREEN、lint 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_market_filters.py -v
.venv/bin/ruff check src/lurker/universe/market_filters.py tests/test_market_filters.py
git add src/lurker/universe/market_filters.py tests/test_market_filters.py
git commit -m "feat: evaluate fixed-window quantitative market filters"
```

---

### Task 7: 价格快照 schema v2、量化编排与原子缓存

**Files:**
- Modify: `src/lurker/application/price_snapshot.py`
- Modify: `tests/test_price_snapshot.py`

- [ ] **Step 1: 写编排、summary、失败分离和原子缓存 RED**

在 `tests/test_price_snapshot.py` 增加：

```python
from dataclasses import replace
from datetime import date, timedelta

from lurker.config import load_markets
from lurker.ingest.equity_metadata import (
    EquityMetadata,
    EquityMetadataSourceError,
)
from lurker.ingest.prices import PriceSourceError
from lurker.application.price_snapshot import (
    PriceSnapshotCompatibilityError,
    load_price_snapshot_file,
)


def _filter_prices(symbol, period):
    start = date(2026, 7, 1)
    frame = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "trade_date": start + timedelta(days=index),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.0,
                "adj_close": 10.0 + index,
                "volume": 2_000_000.0,
                "turnover": 20_000_000.0,
            }
            for index in range(20)
        ]
    )
    frame.attrs.update(
        source="fixture.prices",
        retrieved_at="2026-07-26T12:00:00+00:00",
    )
    return frame


def _us_metadata(symbol):
    return EquityMetadata(
        symbol=symbol,
        market_cap_usd=5_000_000_000.0,
        currency="USD",
        quote_type="EQUITY",
        data_date="2026-07-20",
        retrieved_at="2026-07-26T12:00:00+00:00",
        source="fixture.metadata",
        source_hash="sha256:" + "3" * 64,
    )


def _write_complete_markets_yaml(
    tmp_path,
    *,
    missing_data_policy="exclude",
):
    path = tmp_path / "markets.yaml"
    path.write_text(
        f"""
schema_version: 1
filter_policy:
  missing_data_policy: {missing_data_policy}
  turnover_window_trading_days: 20
  min_turnover_observations: 15
  us_market_cap_max_age_days: 7
markets:
  cn:
    name: A 股
    role: primary_discovery
    universe_sources: [测试]
    filters:
      exclude_st: true
      exclude_beijing_exchange: true
      min_avg_turnover_cny: 50000000
  us:
    name: 美股
    role: global_anchor
    universe_sources: [测试]
    filters:
      min_market_cap_usd: 2000000000
      min_avg_turnover_usd: 10000000
  hk:
    name: 港股
    role: mapping_supplement
    universe_sources: [测试]
    filters:
      min_price_hkd: 1
      min_avg_turnover_hkd: 20000000
      exclude_shell_like: false
      exclude_frequent_capital_actions: false
""",
        encoding="utf-8",
    )
    return path


def _typed_markets(tmp_path):
    return load_markets(_write_complete_markets_yaml(tmp_path))


def test_snapshot_v2_includes_decisions_metrics_and_summary(tmp_path):
    config = _typed_markets(tmp_path)
    batch = collect_price_snapshot_batch(
        seed_symbols={"us": ["NVDA"], "hk": ["0700.HK"]},
        markets=["us", "hk"],
        windows=[5],
        period="1y",
        snapshot_date="2026-07-26",
        fetcher=_filter_prices,
        metadata_fetcher=_us_metadata,
        markets_config=config,
        symbol_names={},
        generated_at="2026-07-26T12:00:00+00:00",
    )
    assert batch["schema_version"] == 2
    assert batch["snapshot_date"] == "2026-07-26"
    assert batch["filter_config_hash"] == config.filter_config_hash
    assert batch["filter_summary"] == {
        "included": 2,
        "excluded": 0,
        "included_with_warning": 0,
        "reason_counts": {},
    }
    assert len(batch["filter_decisions"]) == 4
    assert {row["filter_status"] for row in batch["snapshots"]} == {
        "included"
    }
    nvda = next(row for row in batch["snapshots"] if row["symbol"] == "NVDA")
    assert nvda["market_cap_usd"] == 5_000_000_000.0
    assert nvda["avg_turnover_usd"] == 20_000_000.0


def test_expected_exclusion_is_not_a_failure(tmp_path):
    config = _typed_markets(tmp_path)
    batch = collect_price_snapshot_batch(
        seed_symbols={"us": ["SMALL"]},
        markets=["us"],
        windows=[5],
        period="1y",
        snapshot_date="2026-07-26",
        fetcher=_filter_prices,
        metadata_fetcher=lambda symbol: replace(
            _us_metadata(symbol),
            market_cap_usd=1.0,
        ),
        markets_config=config,
        symbol_names={},
    )
    assert batch["snapshots"] == []
    assert batch["failures"] == []
    assert batch["filter_summary"]["excluded"] == 1
    assert (
        batch["filter_decisions"][-1]["reason_codes"]
        == ["market_cap_below_minimum"]
    )


def test_metadata_failure_is_failure_and_missing_decision(tmp_path):
    config = _typed_markets(tmp_path)
    batch = collect_price_snapshot_batch(
        seed_symbols={"us": ["NVDA"]},
        markets=["us"],
        windows=[5],
        period="1y",
        snapshot_date="2026-07-26",
        fetcher=_filter_prices,
        metadata_fetcher=lambda symbol: (_ for _ in ()).throw(
            EquityMetadataSourceError("provider timeout")
        ),
        markets_config=config,
        symbol_names={},
    )
    assert batch["snapshots"] == []
    assert batch["failures"][0]["source"] == "us_equity_metadata"
    assert "market_cap_missing" in batch["filter_decisions"][-1]["reason_codes"]


def test_price_failure_always_excludes_even_warning_policy(tmp_path):
    path = _write_complete_markets_yaml(
        tmp_path,
        missing_data_policy="include_with_warning",
    )
    config = load_markets(path)
    batch = collect_price_snapshot_batch(
        seed_symbols={"us": ["NVDA"]},
        markets=["us"],
        windows=[5],
        period="1y",
        snapshot_date="2026-07-26",
        fetcher=lambda symbol, period: (_ for _ in ()).throw(
            PriceSourceError("price timeout")
        ),
        metadata_fetcher=_us_metadata,
        markets_config=config,
        symbol_names={},
    )
    assert batch["snapshots"] == []
    assert batch["filter_decisions"][-1]["status"] == "excluded"
    assert batch["filter_decisions"][-1]["reason_codes"] == [
        "price_data_unavailable"
    ]


def test_unexpected_programming_error_propagates(tmp_path):
    with pytest.raises(TypeError, match="programmer error"):
        collect_price_snapshot_batch(
            seed_symbols={"us": ["NVDA"]},
            markets=["us"],
            windows=[5],
            period="1y",
            snapshot_date="2026-07-26",
            fetcher=lambda symbol, period: (_ for _ in ()).throw(
                TypeError("programmer error")
            ),
            markets_config=_typed_markets(tmp_path),
            symbol_names={},
        )


def test_snapshot_store_atomic_and_hash_compatible(tmp_path, monkeypatch):
    config = _typed_markets(tmp_path)
    batch = {
        "schema_version": 2,
        "snapshot_date": "2026-07-26",
        "filter_config_hash": config.filter_config_hash,
        "snapshots": [],
        "filter_decisions": [],
        "filter_summary": {
            "included": 0,
            "excluded": 0,
            "included_with_warning": 0,
            "reason_counts": {},
        },
        "failures": [],
    }
    path = tmp_path / "snapshot.json"
    save_price_snapshot_file(batch, path)
    assert load_price_snapshot_file(
        path,
        expected_filter_hash=config.filter_config_hash,
    ) == batch
    with pytest.raises(PriceSnapshotCompatibilityError, match="hash"):
        load_price_snapshot_file(
            path,
            expected_filter_hash="sha256:" + "f" * 64,
        )

    monkeypatch.setattr(
        json,
        "dump",
        lambda value, handle, **kwargs: (_ for _ in ()).throw(
            TypeError("json failure")
        ),
    )
    with pytest.raises(TypeError, match="json failure"):
        save_price_snapshot_file(batch, tmp_path / "failed.json")
    assert list(tmp_path.glob("*.tmp")) == []


def test_v1_snapshot_only_loads_without_filter_expectation(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text('{"snapshots": []}', encoding="utf-8")
    assert load_price_snapshot_file(path)["snapshots"] == []
    with pytest.raises(PriceSnapshotCompatibilityError, match="schema v2"):
        load_price_snapshot_file(
            path,
            expected_filter_hash="sha256:" + "0" * 64,
        )
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_price_snapshot.py -k "v2 or exclusion or metadata or price_failure or compatible or programming" -v
```

Expected: new arguments, fields and compatibility exception are missing.

- [ ] **Step 3: 增加 dict 反序列化和失败决定**

在 `FilterDecision` 增加：

```python
@classmethod
def from_dict(cls, value: dict[str, Any]) -> FilterDecision:
    return cls(
        symbol=str(value["symbol"]),
        market=str(value["market"]),
        stage=value["stage"],
        status=value["status"],
        reason_codes=tuple(value.get("reason_codes", [])),
        metrics=dict(value.get("metrics", {})),
        thresholds={
            str(key): item
            for key, item in value.get("thresholds", {}).items()
        },
        sources=tuple(
            SourceEvidence(**source)
            for source in value.get("sources", [])
        ),
    )
```

增加：

```python
def price_failure_decision(
    symbol: str,
    market: str,
) -> FilterDecision:
    return FilterDecision(
        symbol=symbol.upper(),
        market=market,
        stage="quantitative",
        status="excluded",
        reason_codes=("price_data_unavailable",),
        metrics={},
        thresholds={},
        sources=(),
    )
```

- [ ] **Step 4: 重写 price snapshot 编排**

在 `price_snapshot.py` 增加 imports：

```python
from datetime import date

from lurker.config import (
    CnMarketFilters,
    MarketsConfig,
    UsMarketFilters,
)
from lurker.ingest.equity_metadata import (
    EquityMetadata,
    EquityMetadataSchemaError,
    EquityMetadataSourceError,
    fetch_us_equity_metadata,
)
from lurker.ingest.prices import PriceSchemaError, PriceSourceError
from lurker.universe.market_filters import (
    FilterDecision,
    FilterInputSchemaError,
    calculate_price_filter_metrics,
    evaluate_quantitative_filters,
    evaluate_seed_prefilter,
    price_failure_decision,
    summarize_filter_decisions,
)
```

`collect_price_snapshot_batch` 使用以下新增参数。`markets_config=None` 是仅供已有
直接 API 测试和无过滤工具使用的 legacy v1 路径；生产 CLI 必须传 typed config。
这样不会一次性破坏所有直接调用，但也不会让生产链路绕过过滤：

```python
snapshot_date: str | None = None,
markets_config: MarketsConfig | None = None,
symbol_names: dict[str, str] | None = None,
seed_filter_decisions: list[dict[str, Any]] | None = None,
metadata_fetcher: Callable[[str], EquityMetadata] = fetch_us_equity_metadata,
```

函数开头：

```python
if markets_config is None:
    if snapshot_date is not None or seed_filter_decisions:
        raise ValueError(
            "snapshot_date/filter decisions require typed markets_config"
        )
    return _collect_legacy_price_snapshot_batch(
        seed_symbols=seed_symbols,
        markets=markets,
        windows=windows,
        period=period,
        fetcher=fetcher,
        fetchers=fetchers,
        limit_per_market=limit_per_market,
        generated_at=generated_at,
        seed_pool_generated_at=seed_pool_generated_at,
        db_session=db_session,
    )
if snapshot_date is None:
    raise ValueError("filtered snapshot requires snapshot_date")
resolved_date = date.fromisoformat(snapshot_date)
resolved_names = symbol_names or {}
decisions: list[FilterDecision] = []
upstream_excluded = {
    (item["market"], item["symbol"].upper()): FilterDecision.from_dict(item)
    for item in (seed_filter_decisions or [])
    if item["status"] == "excluded"
}
```

每个 symbol 在价格请求前：

```python
key = (market, symbol.upper())
if key in upstream_excluded:
    decisions.append(upstream_excluded[key])
    continue
if market == "cn":
    prefilter = evaluate_seed_prefilter(
        symbol=symbol,
        market=market,
        name=resolved_names.get(symbol.upper()),
        filters=markets_config.profiles["cn"].filters,
        policy=markets_config.policy,
    )
else:
    prefilter = FilterDecision(
        symbol=symbol.upper(),
        market=market,
        stage="seed_prefilter",
        status="included",
        reason_codes=(),
        metrics={},
        thresholds={},
        sources=(),
    )
decisions.append(prefilter)
if prefilter.status == "excluded":
    continue
```

价格请求只捕获：

```python
except (PriceSourceError, PriceSchemaError) as exc:
    failures.append(
        {
            "symbol": symbol,
            "market": market,
            "source": "prices",
            "reason": str(exc),
        }
    )
    decisions.append(price_failure_decision(symbol, market))
    continue
```

随后调用 `calculate_price_filter_metrics`。其 `FilterInputSchemaError` 同样作为
`source=prices` 的 per-symbol failure，产生 `price_data_unavailable`。不要捕获
`TypeError`、`AttributeError`。

US 且启用市值时：

```python
metadata = None
metadata_error_reason = None
if market == "us":
    try:
        metadata = metadata_fetcher(symbol)
    except EquityMetadataSchemaError as exc:
        metadata_error_reason = exc.reason_code
        failures.append(
            {
                "symbol": symbol,
                "market": market,
                "source": "us_equity_metadata",
                "reason": str(exc),
            }
        )
    except EquityMetadataSourceError as exc:
        metadata_error_reason = "market_cap_missing"
        failures.append(
            {
                "symbol": symbol,
                "market": market,
                "source": "us_equity_metadata",
                "reason": str(exc),
            }
        )
```

调用：

```python
decision = evaluate_quantitative_filters(
    symbol=symbol,
    market=market,
    metrics=metrics,
    price_source=price_source,
    filters=markets_config.profiles[market].filters,
    policy=markets_config.policy,
    snapshot_date=resolved_date,
    metadata=metadata,
    metadata_error_reason=metadata_error_reason,
)
decisions.append(decision)
if decision.status == "excluded":
    continue
```

收益率只使用不晚于 `resolved_date` 的价格；snapshot row 增加：

```python
"filter_status": decision.status,
**{
    key: value
    for key, value in decision.metrics.items()
    if key in {
        "latest_close_hkd",
        "avg_turnover_cny",
        "avg_turnover_usd",
        "avg_turnover_hkd",
        "turnover_observations",
        "market_cap_usd",
    }
},
```

返回：

```python
return {
    "schema_version": 2,
    "snapshot_date": snapshot_date,
    "generated_at": generated_at or datetime.now(UTC).isoformat(),
    "seed_pool_generated_at": seed_pool_generated_at,
    "filter_config_hash": markets_config.filter_config_hash,
    "markets": market_list,
    "windows": window_list,
    "snapshots": snapshots,
    "filter_decisions": [item.to_dict() for item in decisions],
    "filter_summary": summarize_filter_decisions(decisions),
    "failures": failures,
}
```

把重构前的采集主体原样抽到 `_collect_legacy_price_snapshot_batch`；它返回 schema v1，
不接受 `markets_config`，也不执行旧的字典过滤分支。原来验证字典过滤的测试改由
Task 6/7 的 typed-config 真值表覆盖。`collect_price_snapshots` 增加并原样透传
`snapshot_date`、`markets_config`、`symbol_names`、`seed_filter_decisions` 和
`metadata_fetcher`；不传 config 时继续得到 legacy snapshots list。

- [ ] **Step 5: 原子保存和兼容读取**

增加：

```python
class PriceSnapshotCompatibilityError(RuntimeError):
    pass
```

`save_price_snapshot_file` 使用与 seed pool 相同的原子契约：

```python
def save_price_snapshot_file(
    snapshot: PriceSnapshotBatch,
    path: str | Path,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
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
        os.replace(temporary_path, output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
```

相应增加 `import os` 和 `import tempfile`。

`load_price_snapshot_file`：

```python
def load_price_snapshot_file(
    path: str | Path,
    *,
    expected_filter_hash: str | None = None,
) -> PriceSnapshotBatch:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if expected_filter_hash is None:
        return value
    if value.get("schema_version") != 2:
        raise PriceSnapshotCompatibilityError(
            "filtered cache requires price snapshot schema v2"
        )
    if value.get("filter_config_hash") != expected_filter_hash:
        raise PriceSnapshotCompatibilityError(
            "price snapshot filter config hash mismatch"
        )
    return value
```

`PriceSnapshotStore.load_latest` protocol 与
`FilePriceSnapshotStore.load_latest` 都接受 keyword-only
`expected_filter_hash: str | None = None` 并透传。现有无参调用保持兼容。

- [ ] **Step 6: GREEN、全文件回归、lint 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_market_filters.py tests/test_price_snapshot.py -v
.venv/bin/ruff check src/lurker/universe/market_filters.py src/lurker/application/price_snapshot.py tests/test_market_filters.py tests/test_price_snapshot.py
git add src/lurker/universe/market_filters.py src/lurker/application/price_snapshot.py tests/test_market_filters.py tests/test_price_snapshot.py
git commit -m "feat: apply market filters to price snapshot schema v2"
```

---

### Task 8: Seed hash 门、缓存重建、CLI 与日报披露

**Files:**
- Modify: `src/lurker/cli.py`
- Modify: `src/lurker/application/price_snapshot.py`
- Modify: `src/lurker/application/professional_flow_daily.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_price_snapshot.py`
- Modify: `tests/test_professional_flow_daily.py`

- [ ] **Step 1: 写 seed hash、缓存和 CLI RED**

在 `tests/test_cli.py` 增加：

```python
from lurker.cli import (
    SeedPoolCompatibilityError,
    validate_seed_pool_filter_hash,
)
from lurker.config import load_markets


def _write_complete_markets_yaml(
    tmp_path,
    *,
    missing_data_policy="exclude",
):
    path = tmp_path / "markets.yaml"
    path.write_text(
        f"""
schema_version: 1
filter_policy:
  missing_data_policy: {missing_data_policy}
  turnover_window_trading_days: 20
  min_turnover_observations: 15
  us_market_cap_max_age_days: 7
markets:
  cn:
    name: A 股
    role: primary_discovery
    universe_sources: [测试]
    filters:
      exclude_st: true
      exclude_beijing_exchange: true
      min_avg_turnover_cny: 50000000
  us:
    name: 美股
    role: global_anchor
    universe_sources: [测试]
    filters:
      min_market_cap_usd: 2000000000
      min_avg_turnover_usd: 10000000
  hk:
    name: 港股
    role: mapping_supplement
    universe_sources: [测试]
    filters:
      min_price_hkd: 1
      min_avg_turnover_hkd: 20000000
      exclude_shell_like: false
      exclude_frequent_capital_actions: false
""",
        encoding="utf-8",
    )
    return path


def test_seed_pool_filter_hash_must_match():
    pool = {
        "schema_version": 2,
        "filter_config_hash": "sha256:" + "1" * 64,
    }
    validate_seed_pool_filter_hash(pool, "sha256:" + "1" * 64)
    with pytest.raises(SeedPoolCompatibilityError, match="resolve-seeds"):
        validate_seed_pool_filter_hash(pool, "sha256:" + "2" * 64)
    with pytest.raises(SeedPoolCompatibilityError, match="resolve-seeds"):
        validate_seed_pool_filter_hash({}, "sha256:" + "2" * 64)


def test_refresh_prices_reports_filter_counts(monkeypatch, tmp_path):
    config_path = _write_complete_markets_yaml(tmp_path)
    config = load_markets(config_path)
    seed_path = tmp_path / "pool.json"
    seed_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-07-26T00:00:00+00:00",
                "filter_config_hash": config.filter_config_hash,
                "markets": {"us": {"symbols": ["NVDA"]}},
                "symbol_names": {},
                "filter_decisions": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lurker.cli.collect_price_snapshot_batch",
        lambda **kwargs: {
            "schema_version": 2,
            "snapshot_date": "2026-07-26",
            "filter_config_hash": config.filter_config_hash,
            "snapshots": [{"symbol": "NVDA"}],
            "filter_decisions": [],
            "filter_summary": {
                "included": 1,
                "excluded": 2,
                "included_with_warning": 1,
                "reason_counts": {},
            },
            "failures": [{"symbol": "BAD", "reason": "timeout"}],
        },
    )
    message = refresh_prices(
        seed_pool_path=seed_path,
        output_dir=tmp_path / "snapshots",
        markets=["us"],
        windows=[20],
        period="1y",
        limit_per_market=None,
        snapshot_date="2026-07-26",
        markets_path=config_path,
    )
    assert "snapshots=1, excluded=2, warnings=1, failures=1" in message


def test_data_snapshot_rebuilds_incompatible_cache(
    monkeypatch,
    tmp_path,
):
    config_path = _write_complete_markets_yaml(tmp_path)
    config = load_markets(config_path)
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "2026-07-25.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "filter_config_hash": "sha256:" + "f" * 64,
                "snapshots": [{"symbol": "STALE"}],
            }
        ),
        encoding="utf-8",
    )
    seed_path = tmp_path / "pool.json"
    seed_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "filter_config_hash": config.filter_config_hash,
                "generated_at": "2026-07-26T00:00:00+00:00",
                "markets": {"cn": {"symbols": ["600001.SH"]}},
                "symbol_names": {"600001.SH": "普通公司"},
                "filter_decisions": [],
            }
        ),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        "lurker.cli.collect_price_snapshot_batch",
        lambda **kwargs: calls.append(kwargs) or {
            "schema_version": 2,
            "snapshot_date": "2026-07-26",
            "filter_config_hash": config.filter_config_hash,
            "snapshots": [],
            "filter_decisions": [],
            "filter_summary": {
                "included": 0,
                "excluded": 0,
                "included_with_warning": 0,
                "reason_counts": {},
            },
            "failures": [],
        },
    )
    build_data_snapshot(
        themes_path=tmp_path / "themes.yaml",
        seed_pool_path=seed_path,
        price_snapshot_dir=snapshot_dir,
        markets=["cn"],
        windows=[20],
        period="1y",
        limit_per_market=1,
        markets_path=config_path,
    )
    assert len(calls) == 1


def test_data_snapshot_rebuilds_stale_seed_pool(
    monkeypatch,
    tmp_path,
):
    config_path = _write_complete_markets_yaml(tmp_path)
    config = load_markets(config_path)
    seed_path = tmp_path / "pool.json"
    seed_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "filter_config_hash": "sha256:" + "f" * 64,
                "markets": {},
            }
        ),
        encoding="utf-8",
    )
    rebuilt = {
        "schema_version": 2,
        "generated_at": "2026-07-26T00:00:00+00:00",
        "filter_config_hash": config.filter_config_hash,
        "markets": {"cn": {"symbols": ["600001.SH"]}},
        "theme_mapping": {},
        "symbol_names": {"600001.SH": "普通公司"},
        "filter_decisions": [],
        "filter_summary": {
            "included": 1,
            "excluded": 0,
            "included_with_warning": 0,
            "reason_counts": {},
        },
        "failures": [],
    }
    monkeypatch.setattr(
        "lurker.cli.build_resolved_seed_pool",
        lambda *args, **kwargs: rebuilt,
    )
    monkeypatch.setattr(
        "lurker.cli.collect_price_snapshot_batch",
        lambda **kwargs: {
            "schema_version": 2,
            "snapshot_date": "2026-07-26",
            "filter_config_hash": config.filter_config_hash,
            "snapshots": [],
            "filter_decisions": [],
            "filter_summary": {
                "included": 0,
                "excluded": 0,
                "included_with_warning": 0,
                "reason_counts": {},
            },
            "failures": [],
        },
    )
    build_data_snapshot(
        themes_path=tmp_path / "themes.yaml",
        seed_pool_path=seed_path,
        price_snapshot_dir=None,
        markets=["cn"],
        windows=[20],
        period="1y",
        limit_per_market=1,
        markets_path=config_path,
    )
    saved = json.loads(seed_path.read_text(encoding="utf-8"))
    assert saved["filter_config_hash"] == config.filter_config_hash
    assert saved["markets"]["cn"]["symbols"] == ["600001.SH"]
```

- [ ] **Step 2: 写渲染与日报 RED**

在 `tests/test_price_snapshot.py` 增加：

```python
def test_render_price_snapshot_appends_filter_summary():
    rendered = render_price_snapshot(
        [{"symbol": "NVDA", "market": "us", "latest_close": 100.0}],
        windows=[],
        filter_summary={
            "included": 1,
            "excluded": 2,
            "included_with_warning": 1,
            "reason_counts": {
                "market_cap_below_minimum": 2,
            },
        },
        failure_count=1,
    )
    assert "过滤摘要：纳入 1，排除 2，带警告纳入 1，失败 1" in rendered
    assert "market_cap_below_minimum=2" in rendered
```

在 `tests/test_professional_flow_daily.py` 增加：

```python
def test_daily_report_discloses_market_filter_degradation():
    report = run_professional_flow_daily(
        price_snapshot={
            "snapshots": [],
            "filter_summary": {
                "included": 0,
                "excluded": 2,
                "included_with_warning": 1,
                "reason_counts": {"market_cap_missing": 1},
            },
            "failures": [{"source": "us_equity_metadata"}],
        },
        flow_snapshot={},
        theme_mapping={},
        report_date="2026-07-26",
    )
    assert "市场过滤：纳入 0，排除 2，带警告纳入 1" in report.content_md
    assert "market_cap_missing=1" in report.content_md
    assert "结论包含降级过滤数据" in report.content_md
```

在 `tests/test_cli.py` 增加：

```python
def test_daily_job_all_filtered_does_not_build_notifier(
    monkeypatch,
    tmp_path,
):
    from lurker.reports.models import DailyReport

    config_path = _write_complete_markets_yaml(tmp_path)
    config = load_markets(config_path)
    seed_path = tmp_path / "pool.json"
    seed_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-07-24T00:00:00+00:00",
                "filter_config_hash": config.filter_config_hash,
                "markets": {"cn": {"symbols": ["600001.SH"]}},
                "theme_mapping": {},
                "symbol_names": {"600001.SH": "普通公司"},
                "filter_decisions": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lurker.cli.collect_price_snapshot_batch",
        lambda **kwargs: {
            "schema_version": 2,
            "snapshot_date": "2026-07-24",
            "filter_config_hash": config.filter_config_hash,
            "snapshots": [],
            "filter_decisions": [
                {
                    "symbol": "600001.SH",
                    "market": "cn",
                    "stage": "quantitative",
                    "status": "excluded",
                    "reason_codes": ["turnover_below_minimum"],
                    "metrics": {},
                    "thresholds": {},
                    "sources": [],
                }
            ],
            "filter_summary": {
                "included": 0,
                "excluded": 1,
                "included_with_warning": 0,
                "reason_counts": {"turnover_below_minimum": 1},
            },
            "failures": [],
        },
    )
    monkeypatch.setattr(
        "lurker.cli.run_daily",
        lambda **kwargs: DailyReport(
            report_date="2026-07-24",
            main_candidates_count=0,
            content_md="# 日报\n\n无可用标的。\n",
        ),
    )
    monkeypatch.setattr(
        "lurker.cli.build_notifier_from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("notifier must not be built")
        ),
    )
    message = daily_job(
        seed_pool_path=seed_path,
        price_snapshot_dir=tmp_path / "price_snapshots",
        report_dir=tmp_path / "reports",
        markets=["cn"],
        windows=[20],
        period="1y",
        limit_per_market=None,
        report_date="2026-07-24",
        markets_path=config_path,
        push=True,
    )
    assert "validation failed" in message
```

- [ ] **Step 3: 运行 RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py tests/test_price_snapshot.py tests/test_professional_flow_daily.py -k "filter_hash or filter_counts or incompatible_cache or filter_summary or filter_degradation or all_filtered" -v
```

Expected: hash gate and disclosure are missing.

- [ ] **Step 4: 实现 seed hash 门和参数透传**

在 `cli.py` 增加：

```python
class SeedPoolCompatibilityError(RuntimeError):
    pass


def validate_seed_pool_filter_hash(
    pool: dict[str, Any],
    expected_hash: str,
) -> None:
    if (
        pool.get("schema_version") != 2
        or pool.get("filter_config_hash") != expected_hash
    ):
        raise SeedPoolCompatibilityError(
            "seed pool filter config is stale; run resolve-seeds"
        )
```

`resolve_seed_pool` 加载 typed config 的工作仍由 `build_resolved_seed_pool` 完成。

`refresh_prices` 和 `daily_job`：

1. 先 `load_markets(markets_path)`；
2. 加载 seed pool；
3. 在任何价格或 metadata 调用前执行 `validate_seed_pool_filter_hash`；
4. 向 collector 传：

```python
snapshot_date=job_date,
markets_config=markets_config,
symbol_names=seed_pool.get("symbol_names", {}),
seed_filter_decisions=seed_pool.get("filter_decisions", []),
```

`build_data_snapshot` 读取缓存时调用：

```python
store.load_latest(
    expected_filter_hash=markets_config.filter_config_hash
)
```

捕获 `PriceSnapshotCompatibilityError` 后重新采集，不能返回 stale rows。
`build_data_snapshot` 用以下 helper 保证 seed pool 也同步重建：

```python
def _load_or_rebuild_seed_pool(
    *,
    seed_pool_path: Path,
    themes_path: Path,
    markets_path: Path,
    expected_filter_hash: str,
) -> dict[str, Any]:
    if seed_pool_path.exists():
        pool = load_resolved_seed_pool(seed_pool_path)
        try:
            validate_seed_pool_filter_hash(pool, expected_filter_hash)
            return pool
        except SeedPoolCompatibilityError:
            pass
    pool = build_resolved_seed_pool(
        themes_path,
        markets_path=markets_path,
    )
    validate_seed_pool_filter_hash(pool, expected_filter_hash)
    save_resolved_seed_pool(pool, seed_pool_path)
    return pool
```

`build_data_snapshot` 必须先调用此 helper，再尝试读取价格缓存；缓存不兼容则把该
seed pool 的 symbols、names 和 seed decisions 传给 collector。`refresh_prices`、
`daily_job` 没有完整 themes/resolver 上下文，seed hash 不匹配时明确失败。

CLI message：

```python
summary = batch["filter_summary"]
return (
    f"Wrote price snapshot to {output_path} "
    f"(snapshots={len(batch['snapshots'])}, "
    f"excluded={summary['excluded']}, "
    f"warnings={summary['included_with_warning']}, "
    f"failures={len(batch['failures'])})"
)
```

- [ ] **Step 5: 实现数据快照和日报摘要**

`render_price_snapshot` 增加 keyword-only 参数：

```python
filter_summary: dict[str, Any] | None = None,
failure_count: int = 0,
```

表格后追加：

```python
if filter_summary is not None:
    reasons = ", ".join(
        f"{reason}={count}"
        for reason, count in filter_summary.get("reason_counts", {}).items()
    ) or "无"
    rows.extend(
        [
            "",
            (
                "过滤摘要："
                f"纳入 {filter_summary.get('included', 0)}，"
                f"排除 {filter_summary.get('excluded', 0)}，"
                "带警告纳入 "
                f"{filter_summary.get('included_with_warning', 0)}，"
                f"失败 {failure_count}"
            ),
            f"过滤原因：{reasons}",
        ]
    )
```

所有调用方传 batch summary 和 failure count；旧 v1 无 summary 时保持原输出。

在 `professional_flow_daily.py` 增加：

```python
def _market_filter_quality_lines(
    price_snapshot: dict[str, Any],
) -> list[str]:
    summary = price_snapshot.get("filter_summary")
    if not isinstance(summary, dict):
        return []
    reasons = ", ".join(
        f"{reason}={count}"
        for reason, count in summary.get("reason_counts", {}).items()
    ) or "无"
    lines = [
        (
            "市场过滤："
            f"纳入 {summary.get('included', 0)}，"
            f"排除 {summary.get('excluded', 0)}，"
            f"带警告纳入 {summary.get('included_with_warning', 0)}；"
            f"原因 {reasons}"
        )
    ]
    if int(summary.get("included_with_warning", 0)) > 0:
        lines.append("⚠️ 结论包含降级过滤数据。")
    return lines
```

把这些行追加到传给 `render_professional_flow_report` 的现有 `data_quality`。过滤失败数量
从 `price_snapshot["failures"]` 单独披露。

- [ ] **Step 6: GREEN、全量回归、lint 并提交**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py tests/test_price_snapshot.py tests/test_professional_flow_daily.py -v
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check src tests
git diff --check
git add src/lurker/cli.py src/lurker/application/price_snapshot.py src/lurker/application/professional_flow_daily.py tests/test_cli.py tests/test_price_snapshot.py tests/test_professional_flow_daily.py
git commit -m "feat: enforce market filter cache gates and disclosure"
```

---

### Task 9: 真实数据验收、契约审计和最终回归

**Files:**
- Verify: `configs/markets.yaml`
- Verify: `data/processed/resolved_seed_pool.json`
- Verify: `data/processed/price_snapshots/YYYY-MM-DD.json`
- Verify: `data/reports/daily/YYYY-MM-DD.md`
- Modify only if a verified defect exists: files from Tasks 1–8 and their tests

- [ ] **Step 1: 配置失败必须先于网络**

创建临时配置，把：

```yaml
exclude_shell_like: false
```

改为：

```yaml
exclude_shell_like: true
```

运行：

```bash
PYTHONPATH=src .venv/bin/python -m lurker.cli resolve-seeds \
  --markets-path /absolute/path/to/invalid-markets.yaml \
  --output /tmp/lurker-invalid-seed-pool.json
```

Expected:

```text
unsupported market filter: exclude_shell_like
```

且 output 不存在。测试中用 network spy 证明 resolver 未调用。

- [ ] **Step 2: 实时 NVDA metadata 预检**

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from lurker.ingest.equity_metadata import fetch_us_equity_metadata

value = fetch_us_equity_metadata("NVDA")
print(value)
assert value.market_cap_usd > 0
assert value.currency == "USD"
assert value.quote_type == "EQUITY"
assert value.data_date
assert value.source_hash.startswith("sha256:")
PY
```

Expected: typed metadata prints successfully. Provider 不满足契约时保留错误输出，不手工填值。

- [ ] **Step 3: 重建当前 seed pool**

```bash
PYTHONPATH=src .venv/bin/python -m lurker.cli resolve-seeds \
  --themes configs/themes.yaml \
  --markets-path configs/markets.yaml \
  --output data/processed/resolved_seed_pool.json
```

核对：

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from pathlib import Path

value = json.loads(
    Path("data/processed/resolved_seed_pool.json").read_text(encoding="utf-8")
)
assert value["schema_version"] == 2
assert value["filter_config_hash"].startswith("sha256:")
assert value["filter_summary"]
assert all(
    not symbol.upper().endswith(".BJ")
    for market in value["markets"].values()
    for symbol in market.get("symbols", [])
)
print(value["filter_summary"])
print(value["failures"])
PY
```

- [ ] **Step 4: 有限标的价格刷新**

```bash
PYTHONPATH=src .venv/bin/python -m lurker.cli refresh-prices \
  --seed-pool data/processed/resolved_seed_pool.json \
  --markets us,hk,cn \
  --windows 20,60,120,180 \
  --period 1y \
  --limit 2 \
  --date 2026-07-26 \
  --markets-path configs/markets.yaml \
  --output-dir data/processed/price_snapshots
```

Expected: message includes `snapshots=`, `excluded=`, `warnings=`, `failures=`.

打开 `data/processed/price_snapshots/2026-07-26.json`，程序化核对：

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from collections import Counter
from pathlib import Path

path = Path("data/processed/price_snapshots/2026-07-26.json")
value = json.loads(path.read_text(encoding="utf-8"))
assert value["schema_version"] == 2
assert value["snapshot_date"] == "2026-07-26"
assert value["filter_config_hash"].startswith("sha256:")
terminal = {}
for item in value["filter_decisions"]:
    key = (item["market"], item["symbol"])
    if item["stage"] == "quantitative" or key not in terminal:
        terminal[key] = item
counts = Counter(item["status"] for item in terminal.values())
assert counts["included"] == value["filter_summary"]["included"]
assert counts["excluded"] == value["filter_summary"]["excluded"]
assert (
    counts["included_with_warning"]
    == value["filter_summary"]["included_with_warning"]
)
print(value["filter_summary"])
print(value["failures"])
PY
```

- [ ] **Step 5: 手算 NVDA 20 日成交额**

用 adapter 返回的同一 price frame：

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from datetime import date

from lurker.ingest.prices import fetch_yfinance_prices
from lurker.universe.market_filters import calculate_price_filter_metrics

frame = fetch_yfinance_prices("NVDA", "1y")
metrics, source = calculate_price_filter_metrics(
    frame,
    snapshot_date=date(2026, 7, 26),
    window=20,
    minimum_observations=15,
)
manual = (
    frame.loc[frame["trade_date"] <= date(2026, 7, 26)]
    .dropna(subset=["turnover"])
    .sort_values("trade_date")
    .tail(20)["turnover"]
    .mean()
)
assert abs(metrics["avg_turnover"] - manual) < 1e-6
print(metrics)
print(source)
PY
```

- [ ] **Step 6: 报告披露和空快照推送门**

运行 Task 8 固定 fixture：

```bash
PYTHONPATH=src .venv/bin/pytest \
  tests/test_professional_flow_daily.py::test_daily_report_discloses_market_filter_degradation \
  tests/test_cli.py::test_daily_job_all_filtered_does_not_build_notifier \
  -v
```

Expected: 两项通过；第一项证明 warning 披露，第二项证明全排除时 notifier 不构建且
返回 validation failed。

- [ ] **Step 7: 全量验证**

```bash
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check src tests
git diff --check
rg -n \
  "exclude_st|exclude_beijing_exchange|min_avg_turnover_cny|min_market_cap_usd|min_avg_turnover_usd|min_price_hkd|min_avg_turnover_hkd|exclude_shell_like|exclude_frequent_capital_actions" \
  configs/markets.yaml src tests
git status --short
git log --oneline -12
```

Expected:

- 全量测试和 Ruff 通过；
- 每个启用配置字段有测试和执行路径；
- 两个未支持字段为 true 的失败路径有测试；
- 除 gitignore 下的实时验收产物外工作树干净；
- Tasks 1–8 各有独立提交；
- 没有用当前市值回填历史日期；
- 没有把缺失值默认为零后静默排除。

- [ ] **Step 8: 修复验收发现的真实缺陷并提交**

只有 Step 1–7 暴露确定缺陷时才改代码。为每个缺陷先增加最小失败测试，再修复并运行
受影响测试与全量回归。提交：

```bash
git add \
  configs/markets.yaml \
  src/lurker/config.py \
  src/lurker/universe/market_filters.py \
  src/lurker/ingest/equity_metadata.py \
  src/lurker/ingest/prices.py \
  src/lurker/ingest/constituents.py \
  src/lurker/universe/resolved_seed_pool.py \
  src/lurker/application/price_snapshot.py \
  src/lurker/application/professional_flow_daily.py \
  src/lurker/cli.py \
  tests/test_config.py \
  tests/test_market_filters.py \
  tests/test_equity_metadata.py \
  tests/test_ingest.py \
  tests/test_resolved_seed_pool.py \
  tests/test_price_snapshot.py \
  tests/test_professional_flow_daily.py \
  tests/test_cli.py
git commit -m "fix: close market filter acceptance gaps"
```

如果没有缺陷，不创建空提交。

---

## Spec 覆盖矩阵

| 设计要求 | 实施任务 |
|---|---|
| 严格 schema、typed config、稳定 hash | Task 1 |
| reason codes、决定和终态 summary | Task 2 |
| A 股直接成交额与 US/HK close × volume | Task 3 |
| 独立、带日期的美股市值 metadata | Task 4 |
| 北交所、ST、名称失败、来源同步 | Task 5 |
| 固定 20/15 窗口、阈值和时间边界 | Task 6 |
| price snapshot schema v2、失败分离、原子缓存 | Task 7 |
| seed hash 门、缓存重建、CLI 与日报披露 | Task 8 |
| unsupported 配置先失败 | Task 1、9 |
| 历史日期不使用当前市值 | Task 6、7、9 |
| 空价格快照不推送 | Task 8、9 |
| 真实 NVDA 和全量验收 | Task 9 |
