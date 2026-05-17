# 大趋势投资雷达 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local self-use trend discovery system that scans seeded A-share, US, and HK universes, detects stock/sector anomalies, uses AI attribution to rank candidates, and generates a daily Markdown report.

**Architecture:** Start with a local Python package, YAML configs, SQLite storage, and deterministic scoring modules. Market data ingestion, universe construction, signal scoring, AI attribution, candidate ranking, watchlist state, and report generation are separated so later productization can swap data vendors, storage, scheduling, or UI without rewriting core rules.

**Tech Stack:** Python 3.11+, pytest, pydantic, PyYAML, pandas, SQLAlchemy, SQLite, akshare, yfinance, OpenAI-compatible structured output, PushPlus or Server Chan for optional push.

---

## File Structure

Create this structure during implementation:

```text
lurker/
  pyproject.toml
  README.md
  configs/
    themes.yaml
    markets.yaml
    scoring.yaml
    push.yaml.example
  data/
    .gitkeep
    raw/.gitkeep
    processed/.gitkeep
    reports/.gitkeep
  src/
    lurker/
      __init__.py
      cli.py
      ingest/
        __init__.py
        prices.py
        constituents.py
        news.py
      universe/
        __init__.py
        seed_pool.py
        expansion.py
        filters.py
      signals/
        __init__.py
        stock_strength.py
        sector_breadth.py
        double_baggers.py
      ai/
        __init__.py
        attribution.py
        prompts.py
        schemas.py
      scoring/
        __init__.py
        stock_score.py
        sector_score.py
        candidate_score.py
      reports/
        __init__.py
        daily_report.py
        trend_card.py
        pushplus.py
      storage/
        __init__.py
        db.py
        models.py
  tests/
    test_config.py
    test_storage.py
    test_universe.py
    test_signals.py
    test_scoring.py
    test_ai_schema.py
    test_reports.py
```

---

## Task 1: Project Skeleton And Tooling

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/lurker/__init__.py`
- Create: package `__init__.py` files under `src/lurker/*/`
- Create: `data/.gitkeep`, `data/raw/.gitkeep`, `data/processed/.gitkeep`, `data/reports/.gitkeep`

- [ ] **Step 1: Create package and data directories**

Run:

```bash
mkdir -p configs data/raw data/processed data/reports src/lurker/{ingest,universe,signals,ai,scoring,reports,storage} tests
touch data/.gitkeep data/raw/.gitkeep data/processed/.gitkeep data/reports/.gitkeep
touch src/lurker/__init__.py src/lurker/ingest/__init__.py src/lurker/universe/__init__.py src/lurker/signals/__init__.py src/lurker/ai/__init__.py src/lurker/scoring/__init__.py src/lurker/reports/__init__.py src/lurker/storage/__init__.py
```

- [ ] **Step 2: Create `pyproject.toml`**

Use:

```toml
[project]
name = "lurker"
version = "0.1.0"
description = "Local trend discovery radar for self-use investment research"
requires-python = ">=3.11"
dependencies = [
  "akshare>=1.14.0",
  "pandas>=2.2.0",
  "pydantic>=2.7.0",
  "pyyaml>=6.0.1",
  "requests>=2.31.0",
  "sqlalchemy>=2.0.0",
  "yfinance>=0.2.40"
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "ruff>=0.5.0"
]

[project.scripts]
lurker = "lurker.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 3: Create `README.md`**

Use:

```markdown
# lurker

Local trend discovery radar for self-use investment research.

The first MVP scans seeded A-share, US, and HK universes, detects stock and sector anomalies, performs bounded AI attribution, and generates a daily Markdown report.

## MVP scope

- A-share core index and theme ETF constituents as the main discovery market
- US and HK curated pools for anchor validation and cross-market mapping
- Stock strength, double-bagger, sector breadth, and candidate ranking
- AI attribution only after deterministic rules trigger a candidate
- Daily report with main candidates, secondary leads, and watchlist changes
```

- [ ] **Step 4: Install in editable mode**

Run:

```bash
python -m pip install -e ".[dev]"
```

Expected: package installs without dependency resolution errors.

- [ ] **Step 5: Run the empty test suite**

Run:

```bash
pytest -q
```

Expected: pytest reports no tests collected or all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml README.md data src tests
git commit -m "chore: initialize trend radar project"
```

---

## Task 2: Configuration Files And Loaders

**Files:**
- Create: `configs/themes.yaml`
- Create: `configs/markets.yaml`
- Create: `configs/scoring.yaml`
- Create: `configs/push.yaml.example`
- Create: `src/lurker/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

from lurker.config import load_markets, load_scoring, load_themes


ROOT = Path(__file__).resolve().parents[1]


def test_load_themes_contains_ai_infra():
    themes = load_themes(ROOT / "configs" / "themes.yaml")

    assert "ai_infra" in {theme["id"] for theme in themes}
    ai_infra = next(theme for theme in themes if theme["id"] == "ai_infra")
    assert ai_infra["markets"]["us"]["seed_symbols"] == ["NVDA", "AVGO", "ANET"]


def test_load_markets_has_three_market_profiles():
    markets = load_markets(ROOT / "configs" / "markets.yaml")

    assert set(markets) == {"cn", "us", "hk"}
    assert markets["cn"]["role"] == "primary_discovery"
    assert markets["hk"]["filters"]["min_avg_turnover_hkd"] == 20_000_000


def test_load_scoring_weights_sum_to_one():
    scoring = load_scoring(ROOT / "configs" / "scoring.yaml")

    weights = scoring["candidate_weights"]["stock_first"]
    assert sum(weights.values()) == 1.0
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_config.py -q
```

Expected: FAIL because `lurker.config` and YAML files do not exist.

- [ ] **Step 3: Create `configs/themes.yaml`**

Use:

```yaml
themes:
  - id: ai_infra
    name: AI 算力基础设施
    aliases: [AI 算力, 数据中心, 光通信, 液冷]
    markets:
      cn:
        seed_indexes: [科创 50, 创业板指]
        seed_etfs: [通信 ETF, 人工智能 ETF]
        seed_symbols: [300308.SZ, 300502.SZ]
      us:
        seed_etfs: [SMH, SOXX, AIQ]
        seed_symbols: [NVDA, AVGO, ANET]
      hk:
        seed_symbols: [0700.HK, 9988.HK]
    chain:
      upstream: [光芯片, PCB, 电源]
      midstream: [光模块, 交换机, 液冷]
      downstream: [云厂商, 数据中心运营商]
    keywords: [800G, 1.6T, CPO, GPU 集群, 云厂商资本开支]
    negative_keywords: [澄清公告, 股东减持]

  - id: innovative_drugs
    name: 创新药出海
    aliases: [创新药, BD 出海, Biotech]
    markets:
      cn:
        seed_indexes: [科创 50, 创业板指]
        seed_etfs: [创新药 ETF, 生物医药 ETF]
        seed_symbols: [688235.SH, 300760.SZ]
      us:
        seed_etfs: [XBI, IBB]
        seed_symbols: [MRNA, VRTX, REGN]
      hk:
        seed_symbols: [06160.HK, 01801.HK, 09926.HK]
    chain:
      upstream: [CXO, 原料药]
      midstream: [创新药, 临床管线]
      downstream: [授权交易, 海外商业化]
    keywords: [license out, BD, 临床数据, FDA, 里程碑付款]
    negative_keywords: [临床失败, 终止研发, 融资压力]
```

- [ ] **Step 4: Create `configs/markets.yaml`**

Use:

```yaml
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
    exclude_shell_like: true
    exclude_frequent_capital_actions: true
```

- [ ] **Step 5: Create `configs/scoring.yaml`**

Use:

```yaml
stock_signal:
  thresholds:
    candidate: 70
    high_priority: 85
  weights:
    return_20d: 15
    return_60d: 15
    return_120_180d: 15
    double_bagger: 15
    near_52w_high: 10
    relative_market_strength: 10
    relative_sector_strength: 10
    turnover_expansion: 10

sector_signal:
  thresholds:
    candidate: 65
    main_candidate: 75
    watchlist_pending: 85
  weights:
    sector_strength: 20
    strong_stock_count: 20
    new_high_ratio: 15
    chain_diffusion: 20
    cross_market_mapping: 15
    turnover_persistence: 10

ai_attribution:
  weights:
    reason_clarity: 20
    industry_level: 20
    news_consistency: 15
    hard_evidence: 25
    risk_identification: 10
    counter_evidence: 10

candidate_weights:
  stock_first:
    stock_score: 0.35
    sector_score: 0.35
    ai_score: 0.30
  sector_first:
    stock_score: 0.25
    sector_score: 0.45
    ai_score: 0.30
```

- [ ] **Step 6: Create `configs/push.yaml.example`**

Use:

```yaml
provider: pushplus
pushplus:
  token_env: PUSHPLUS_TOKEN
server_chan:
  send_key_env: SERVER_CHAN_SEND_KEY
```

- [ ] **Step 7: Implement `src/lurker/config.py`**

Use:

```python
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def load_themes(path: str | Path) -> list[dict[str, Any]]:
    data = load_yaml(path)
    themes = data.get("themes", [])
    if not isinstance(themes, list) or not themes:
        raise ValueError("themes.yaml must contain a non-empty themes list")
    return themes


def load_markets(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)


def load_scoring(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)
```

- [ ] **Step 8: Run tests**

Run:

```bash
pytest tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add configs src/lurker/config.py tests/test_config.py
git commit -m "feat: add radar configuration files"
```

---

## Task 3: SQLite Storage Models

**Files:**
- Create: `src/lurker/storage/db.py`
- Create: `src/lurker/storage/models.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_storage.py`:

```python
from datetime import date

from lurker.storage.db import create_session, init_db
from lurker.storage.models import Candidate, PriceDaily, SignalEvent, Symbol


def test_init_db_and_insert_symbol(tmp_path):
    db_path = tmp_path / "lurker.sqlite"
    engine = init_db(db_path)
    session = create_session(engine)

    session.add(Symbol(symbol="NVDA", name="NVIDIA", market="us", asset_type="stock"))
    session.commit()

    saved = session.get(Symbol, "NVDA")
    assert saved is not None
    assert saved.market == "us"


def test_insert_price_signal_and_candidate(tmp_path):
    db_path = tmp_path / "lurker.sqlite"
    engine = init_db(db_path)
    session = create_session(engine)

    session.add(Symbol(symbol="300308.SZ", name="中际旭创", market="cn", asset_type="stock"))
    session.add(
        PriceDaily(
            symbol="300308.SZ",
            trade_date=date(2026, 5, 15),
            open=100,
            high=110,
            low=98,
            close=108,
            adj_close=108,
            volume=1000000,
            amount=108000000,
            market_cap=100000000000,
        )
    )
    session.add(
        SignalEvent(
            symbol="300308.SZ",
            trade_date=date(2026, 5, 15),
            signal_type="stock_strength",
            signal_score=86,
            trigger_reason="60 日强度进入前 10%",
            related_theme_id="ai_infra",
            raw_metrics={"return_60d": 0.52},
        )
    )
    session.add(
        Candidate(
            trade_date=date(2026, 5, 15),
            theme_id="ai_infra",
            primary_symbols=["300308.SZ"],
            expanded_symbols=["300502.SZ"],
            stock_score=86,
            sector_score=76,
            ai_score=80,
            total_score=80.8,
            visibility_tier="main",
            status="active",
        )
    )
    session.commit()

    assert session.query(PriceDaily).count() == 1
    assert session.query(SignalEvent).one().raw_metrics["return_60d"] == 0.52
    assert session.query(Candidate).one().visibility_tier == "main"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_storage.py -q
```

Expected: FAIL because storage modules do not exist.

- [ ] **Step 3: Implement `src/lurker/storage/models.py`**

Use SQLAlchemy declarative models for:

- `Symbol`
- `PriceDaily`
- `SignalEvent`
- `AIAttribution`
- `Candidate`
- `WatchlistItem`
- `Report`

Required implementation details:

- `Symbol.symbol` is primary key.
- `PriceDaily` has composite primary key `symbol`, `trade_date`.
- JSON-like fields use SQLAlchemy `JSON`.
- Dates use `Date`.
- Timestamps use `DateTime`.

- [ ] **Step 4: Implement `src/lurker/storage/db.py`**

Use:

```python
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lurker.storage.models import Base


def init_db(path: str | Path):
    engine = create_engine(f"sqlite:///{Path(path)}", future=True)
    Base.metadata.create_all(engine)
    return engine


def create_session(engine) -> Session:
    return Session(engine)
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_storage.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/storage tests/test_storage.py
git commit -m "feat: add sqlite storage models"
```

---

## Task 4: Seed Pool And Market Filters

**Files:**
- Create: `src/lurker/universe/seed_pool.py`
- Create: `src/lurker/universe/filters.py`
- Test: `tests/test_universe.py`

- [ ] **Step 1: Write failing universe tests**

Create `tests/test_universe.py`:

```python
from lurker.universe.filters import passes_hk_filters, passes_us_filters
from lurker.universe.seed_pool import build_seed_symbols


def test_build_seed_symbols_deduplicates_by_market():
    themes = [
        {
            "id": "ai_infra",
            "markets": {
                "cn": {"seed_symbols": ["300308.SZ", "300502.SZ"]},
                "us": {"seed_symbols": ["NVDA", "AVGO"]},
                "hk": {"seed_symbols": ["0700.HK"]},
            },
        },
        {
            "id": "ai_infra_2",
            "markets": {
                "cn": {"seed_symbols": ["300308.SZ"]},
                "us": {"seed_symbols": ["NVDA", "ANET"]},
                "hk": {"seed_symbols": ["9988.HK"]},
            },
        },
    ]

    result = build_seed_symbols(themes)

    assert result["cn"] == ["300308.SZ", "300502.SZ"]
    assert result["us"] == ["ANET", "AVGO", "NVDA"]
    assert result["hk"] == ["0700.HK", "9988.HK"]


def test_hk_filters_remove_low_quality_names():
    assert passes_hk_filters(price_hkd=2.5, avg_turnover_hkd=30_000_000)
    assert not passes_hk_filters(price_hkd=0.8, avg_turnover_hkd=30_000_000)
    assert not passes_hk_filters(price_hkd=2.5, avg_turnover_hkd=5_000_000)


def test_us_filters_require_size_and_liquidity():
    assert passes_us_filters(market_cap_usd=5_000_000_000, avg_turnover_usd=20_000_000)
    assert not passes_us_filters(market_cap_usd=500_000_000, avg_turnover_usd=20_000_000)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_universe.py -q
```

Expected: FAIL because universe modules do not exist.

- [ ] **Step 3: Implement `src/lurker/universe/seed_pool.py`**

Use:

```python
from collections import defaultdict
from typing import Any


def build_seed_symbols(themes: list[dict[str, Any]]) -> dict[str, list[str]]:
    symbols_by_market: dict[str, set[str]] = defaultdict(set)
    for theme in themes:
        for market, market_config in theme.get("markets", {}).items():
            for symbol in market_config.get("seed_symbols", []):
                symbols_by_market[market].add(symbol)
    return {market: sorted(symbols) for market, symbols in symbols_by_market.items()}
```

- [ ] **Step 4: Implement `src/lurker/universe/filters.py`**

Use:

```python
def passes_hk_filters(
    *,
    price_hkd: float,
    avg_turnover_hkd: float,
    min_price_hkd: float = 1.0,
    min_avg_turnover_hkd: float = 20_000_000,
) -> bool:
    return price_hkd >= min_price_hkd and avg_turnover_hkd >= min_avg_turnover_hkd


def passes_us_filters(
    *,
    market_cap_usd: float,
    avg_turnover_usd: float,
    min_market_cap_usd: float = 2_000_000_000,
    min_avg_turnover_usd: float = 10_000_000,
) -> bool:
    return market_cap_usd >= min_market_cap_usd and avg_turnover_usd >= min_avg_turnover_usd
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_universe.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/universe tests/test_universe.py
git commit -m "feat: add seed universe construction"
```

---

## Task 5: Stock Strength And Double-Bagger Signals

**Files:**
- Create: `src/lurker/signals/stock_strength.py`
- Create: `src/lurker/signals/double_baggers.py`
- Test: `tests/test_signals.py`

- [ ] **Step 1: Write failing signal tests**

Create `tests/test_signals.py`:

```python
import pandas as pd

from lurker.signals.double_baggers import classify_double_bagger
from lurker.signals.stock_strength import calculate_returns, score_stock_strength


def test_calculate_returns_for_windows():
    prices = pd.Series([100, 110, 150, 190, 210], index=pd.date_range("2026-01-01", periods=5))

    result = calculate_returns(prices, windows=[1, 2, 4])

    assert round(result["return_1d"], 4) == 0.1053
    assert round(result["return_2d"], 4) == 0.4
    assert round(result["return_4d"], 4) == 1.1


def test_classify_double_bagger():
    assert classify_double_bagger(0.79) == "none"
    assert classify_double_bagger(0.85) == "near_double"
    assert classify_double_bagger(1.2) == "double"
    assert classify_double_bagger(2.1) == "multi_bagger"


def test_score_stock_strength_rewards_multiple_signals():
    metrics = {
        "return_20d_percentile": 0.95,
        "return_60d_percentile": 0.93,
        "return_180d": 1.05,
        "near_52w_high": True,
        "relative_market_strength": 0.12,
        "relative_sector_strength": 0.08,
        "turnover_expansion": 2.2,
    }

    score = score_stock_strength(metrics)

    assert score >= 85
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_signals.py -q
```

Expected: FAIL because signal modules do not exist.

- [ ] **Step 3: Implement `src/lurker/signals/double_baggers.py`**

Use:

```python
def classify_double_bagger(period_return: float) -> str:
    if period_return >= 2.0:
        return "multi_bagger"
    if period_return >= 1.0:
        return "double"
    if period_return >= 0.8:
        return "near_double"
    return "none"
```

- [ ] **Step 4: Implement `src/lurker/signals/stock_strength.py`**

Use scoring weights from the design:

- 20 日强度: 15
- 60 日强度: 15
- 120/180 日强度: 15
- 准翻倍 / 翻倍: 15
- 新高: 10
- 相对大盘: 10
- 相对行业: 10
- 成交额放大: 10

Implementation rules:

- Percentile >= 0.90 receives full strength points.
- `return_180d >= 0.8` receives near/double-bagger points.
- `near_52w_high is True` receives full new-high points.
- `relative_market_strength >= 0.05` receives full market strength points.
- `relative_sector_strength >= 0.05` receives full sector strength points.
- `turnover_expansion >= 1.5` receives full turnover points.

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_signals.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/signals tests/test_signals.py
git commit -m "feat: add stock strength signals"
```

---

## Task 6: Sector Breadth And Candidate Scoring

**Files:**
- Create: `src/lurker/signals/sector_breadth.py`
- Create: `src/lurker/scoring/candidate_score.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write failing scoring tests**

Create `tests/test_scoring.py`:

```python
from lurker.scoring.candidate_score import combine_candidate_scores, visibility_tier
from lurker.signals.sector_breadth import score_sector_breadth


def test_score_sector_breadth_for_cross_market_diffusion():
    metrics = {
        "sector_outperformance": True,
        "strong_stock_count": 5,
        "new_high_ratio": 0.22,
        "chain_segments": 2,
        "cross_market_count": 2,
        "turnover_persistent": True,
    }

    score = score_sector_breadth(metrics)

    assert score >= 75


def test_combine_candidate_scores_stock_first():
    total = combine_candidate_scores(
        stock_score=86,
        sector_score=76,
        ai_score=80,
        trigger_type="stock_first",
    )

    assert total == 80.7


def test_visibility_tier_keeps_secondary_leads_visible():
    assert visibility_tier(total_score=82, ai_recommendation="升级") == "main"
    assert visibility_tier(total_score=62, ai_recommendation="证据不足") == "secondary"
    assert visibility_tier(total_score=40, ai_recommendation="降级") == "archive"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_scoring.py -q
```

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement `src/lurker/signals/sector_breadth.py`**

Use rules:

- sector outperformance: 20 points
- strong stock count >= 3: 20 points
- new high ratio >= 0.15: 15 points
- chain segments >= 2: 20 points
- cross market count >= 2: 15 points
- turnover persistent: 10 points

- [ ] **Step 4: Implement `src/lurker/scoring/candidate_score.py`**

Use:

```python
WEIGHTS = {
    "stock_first": {"stock_score": 0.35, "sector_score": 0.35, "ai_score": 0.30},
    "sector_first": {"stock_score": 0.25, "sector_score": 0.45, "ai_score": 0.30},
}


def combine_candidate_scores(
    *,
    stock_score: float,
    sector_score: float,
    ai_score: float,
    trigger_type: str,
) -> float:
    weights = WEIGHTS[trigger_type]
    total = (
        stock_score * weights["stock_score"]
        + sector_score * weights["sector_score"]
        + ai_score * weights["ai_score"]
    )
    return round(total, 1)


def visibility_tier(*, total_score: float, ai_recommendation: str) -> str:
    if total_score >= 75 and ai_recommendation in {"升级", "观察"}:
        return "main"
    if total_score >= 50:
        return "secondary"
    return "archive"
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_scoring.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/signals/sector_breadth.py src/lurker/scoring tests/test_scoring.py
git commit -m "feat: add sector and candidate scoring"
```

---

## Task 7: AI Attribution Schema And Prompt

**Files:**
- Create: `src/lurker/ai/schemas.py`
- Create: `src/lurker/ai/prompts.py`
- Create: `src/lurker/ai/attribution.py`
- Test: `tests/test_ai_schema.py`

- [ ] **Step 1: Write failing AI schema tests**

Create `tests/test_ai_schema.py`:

```python
from lurker.ai.attribution import score_ai_attribution
from lurker.ai.schemas import AIAttributionResult


def test_ai_attribution_schema_accepts_expected_payload():
    result = AIAttributionResult(
        classification="产业趋势型",
        reason_summary="AI 数据中心资本开支上修带动光模块需求。",
        evidence=["新闻", "公告", "财报"],
        risk_flags=["估值高"],
        upgrade_recommendation="升级",
        missing_evidence=["订单是否持续进入财报"],
    )

    assert result.classification == "产业趋势型"
    assert "财报" in result.evidence


def test_score_ai_attribution_rewards_hard_evidence():
    result = AIAttributionResult(
        classification="产业趋势型",
        reason_summary="多家公司订单和财报共同验证需求。",
        evidence=["新闻", "公告", "财报", "订单"],
        risk_flags=["估值高", "客户集中"],
        upgrade_recommendation="升级",
        missing_evidence=["云厂商下一季度资本开支指引"],
    )

    assert score_ai_attribution(result) >= 80
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_ai_schema.py -q
```

Expected: FAIL because AI modules do not exist.

- [ ] **Step 3: Implement `src/lurker/ai/schemas.py`**

Use Pydantic:

```python
from typing import Literal

from pydantic import BaseModel, Field


class AIAttributionResult(BaseModel):
    classification: Literal["产业趋势型", "事件驱动型", "题材炒作型", "证据不足型"]
    reason_summary: str = Field(min_length=1)
    evidence: list[Literal["新闻", "公告", "财报", "订单", "政策"]]
    risk_flags: list[str]
    upgrade_recommendation: Literal["升级", "降级", "观察", "证据不足"]
    missing_evidence: list[str]
```

- [ ] **Step 4: Implement `src/lurker/ai/prompts.py`**

Create a prompt template that instructs the model to:

- classify the candidate into one of the four classes;
- summarize the reason in one sentence;
- list evidence types only from the allowed set;
- list risk flags;
- recommend upgrade, downgrade, observe, or insufficient evidence;
- list missing evidence.

- [ ] **Step 5: Implement `src/lurker/ai/attribution.py`**

Implement deterministic scoring for `AIAttributionResult`:

- reason summary present: 20 points
- classification is `产业趋势型`: 20 points
- at least two evidence types: 15 points
- any hard evidence among `公告`, `财报`, `订单`, `政策`: 25 points
- risk flags present: 10 points
- classification is not `题材炒作型`: 10 points

- [ ] **Step 6: Run tests**

Run:

```bash
pytest tests/test_ai_schema.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lurker/ai tests/test_ai_schema.py
git commit -m "feat: add ai attribution schema"
```

---

## Task 8: Report Generation

**Files:**
- Create: `src/lurker/reports/trend_card.py`
- Create: `src/lurker/reports/daily_report.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write failing report tests**

Create `tests/test_reports.py`:

```python
from lurker.reports.daily_report import render_daily_report
from lurker.reports.trend_card import render_trend_card


def test_render_trend_card_contains_required_sections():
    card = render_trend_card(
        theme="AI 算力基础设施",
        status="主候选",
        stage="扩散",
        total_score=82,
        triggers=["A 股光模块多只个股 60 日强度进入前 10%"],
        attribution="云厂商资本开支带动高速互联需求。",
        evidence=["新闻", "公告"],
        risks=["估值偏高"],
        next_checks=["跟踪订单是否进入财报"],
    )

    assert "### AI 算力基础设施" in card
    assert "触发信号" in card
    assert "下一步验证" in card


def test_render_daily_report_has_main_and_secondary_sections():
    report = render_daily_report(
        report_date="2026-05-17",
        main_cards=["### AI 算力基础设施\n状态：主候选"],
        secondary_leads=["创新药出海：证据不足，保留观察"],
        watchlist_changes=["数据中心电力进入观察池"],
        risk_alerts=["部分光模块标的短期交易拥挤"],
    )

    assert "# 大趋势雷达日报" in report
    assert "## 今日主候选" in report
    assert "## 次级线索" in report
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_reports.py -q
```

Expected: FAIL because report modules do not exist.

- [ ] **Step 3: Implement `src/lurker/reports/trend_card.py`**

Implement `render_trend_card(...) -> str` using the Markdown structure from the design doc.

- [ ] **Step 4: Implement `src/lurker/reports/daily_report.py`**

Implement `render_daily_report(...) -> str` with sections:

- 今日主候选
- 次级线索
- 观察池变化
- 过热或证伪提醒

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_reports.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/reports tests/test_reports.py
git commit -m "feat: add daily report rendering"
```

---

## Task 9: Local CLI Pipeline

**Files:**
- Create: `src/lurker/cli.py`
- Modify: `README.md`
- Test: add CLI smoke test to `tests/test_reports.py` or create `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI smoke test**

Create `tests/test_cli.py`:

```python
from lurker.cli import build_demo_report


def test_build_demo_report_returns_markdown():
    report = build_demo_report(report_date="2026-05-17")

    assert report.startswith("# 大趋势雷达日报")
    assert "AI 算力基础设施" in report
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: FAIL because `lurker.cli` does not exist.

- [ ] **Step 3: Implement `src/lurker/cli.py`**

First version should support a demo report without live market data:

```python
from lurker.reports.daily_report import render_daily_report
from lurker.reports.trend_card import render_trend_card


def build_demo_report(report_date: str) -> str:
    card = render_trend_card(
        theme="AI 算力基础设施",
        status="主候选",
        stage="扩散",
        total_score=82,
        triggers=["A 股光模块多只个股 60 日强度进入前 10%"],
        attribution="云厂商资本开支带动高速互联需求。",
        evidence=["新闻", "公告"],
        risks=["估值偏高"],
        next_checks=["跟踪订单是否进入财报"],
    )
    return render_daily_report(
        report_date=report_date,
        main_cards=[card],
        secondary_leads=["创新药出海：证据不足，保留观察"],
        watchlist_changes=["数据中心电力进入观察池"],
        risk_alerts=["部分光模块标的短期交易拥挤"],
    )


def main() -> None:
    print(build_demo_report(report_date="2026-05-17"))
```

- [ ] **Step 4: Update `README.md` usage**

Add:

````markdown
## Run demo report

```bash
lurker
```
````

- [ ] **Step 5: Run full tests**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 6: Run CLI**

Run:

```bash
lurker
```

Expected: Markdown report prints to stdout.

- [ ] **Step 7: Commit**

```bash
git add src/lurker/cli.py README.md tests/test_cli.py
git commit -m "feat: add local demo report cli"
```

---

## Task 10: Live Data Ingestion MVP

**Files:**
- Create: `src/lurker/ingest/prices.py`
- Create: `src/lurker/ingest/constituents.py`
- Test: extend `tests/test_signals.py` or create `tests/test_ingest.py`

- [ ] **Step 1: Write ingestion tests with mocked data**

Create `tests/test_ingest.py`:

```python
import pandas as pd

from lurker.ingest.prices import normalize_price_frame


def test_normalize_price_frame_outputs_required_columns():
    raw = pd.DataFrame(
        {
            "Date": ["2026-05-15"],
            "Open": [100],
            "High": [110],
            "Low": [98],
            "Close": [108],
            "Adj Close": [108],
            "Volume": [1000000],
        }
    )

    result = normalize_price_frame(raw, symbol="NVDA")

    assert list(result.columns) == [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]
    assert result.iloc[0]["symbol"] == "NVDA"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_ingest.py -q
```

Expected: FAIL because ingestion module does not exist.

- [ ] **Step 3: Implement `src/lurker/ingest/prices.py`**

Implement:

- `normalize_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame`
- `fetch_yfinance_prices(symbol: str, period: str = "1y") -> pd.DataFrame`

Do not hit the network in tests. Tests should validate normalization only.

- [ ] **Step 4: Implement `src/lurker/ingest/constituents.py`**

First version can expose configuration-backed functions:

- `load_theme_seed_symbols(themes_path: Path) -> dict[str, list[str]]`
- `load_market_profiles(markets_path: Path) -> dict`

Live index constituent fetching can be added after the local pipeline works.

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_ingest.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/ingest tests/test_ingest.py
git commit -m "feat: add live data ingestion adapters"
```

---

## Task 11: Candidate Pipeline Assembly

**Files:**
- Create: `src/lurker/pipeline.py`
- Modify: `src/lurker/cli.py`
- Test: create `tests/test_pipeline.py`

- [ ] **Step 1: Write pipeline test with in-memory data**

Create `tests/test_pipeline.py`:

```python
from lurker.pipeline import rank_candidates


def test_rank_candidates_splits_main_and_secondary():
    candidates = [
        {
            "theme": "AI 算力基础设施",
            "stock_score": 86,
            "sector_score": 76,
            "ai_score": 80,
            "trigger_type": "stock_first",
            "ai_recommendation": "升级",
        },
        {
            "theme": "创新药出海",
            "stock_score": 62,
            "sector_score": 55,
            "ai_score": 50,
            "trigger_type": "stock_first",
            "ai_recommendation": "证据不足",
        },
    ]

    result = rank_candidates(candidates, main_limit=10)

    assert result["main"][0]["theme"] == "AI 算力基础设施"
    assert result["secondary"][0]["theme"] == "创新药出海"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_pipeline.py -q
```

Expected: FAIL because `lurker.pipeline` does not exist.

- [ ] **Step 3: Implement `src/lurker/pipeline.py`**

Implement `rank_candidates(candidates: list[dict], main_limit: int = 10) -> dict[str, list[dict]]`.

Rules:

- compute `total_score` via `combine_candidate_scores`;
- compute `visibility_tier`;
- return `{"main": [...], "secondary": [...], "archive": [...]}`;
- sort each bucket by `total_score` descending;
- cap main bucket at `main_limit`;
- overflow from main bucket goes to secondary, not archive.

- [ ] **Step 4: Wire CLI to pipeline demo**

Modify `src/lurker/cli.py` so `build_demo_report` uses `rank_candidates` before rendering.

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_pipeline.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/pipeline.py src/lurker/cli.py tests/test_pipeline.py
git commit -m "feat: assemble candidate ranking pipeline"
```

---

## Task 12: Push Adapter And Local Scheduling Notes

**Files:**
- Create: `src/lurker/reports/pushplus.py`
- Modify: `README.md`
- Test: extend `tests/test_reports.py`

- [ ] **Step 1: Write push adapter test**

Add to `tests/test_reports.py`:

```python
from lurker.reports.pushplus import build_pushplus_payload


def test_build_pushplus_payload():
    payload = build_pushplus_payload(
        token="token-123",
        title="大趋势雷达日报",
        content="# 大趋势雷达日报\n正文",
    )

    assert payload["token"] == "token-123"
    assert payload["title"] == "大趋势雷达日报"
    assert payload["template"] == "markdown"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_reports.py -q
```

Expected: FAIL because `pushplus.py` does not exist.

- [ ] **Step 3: Implement `src/lurker/reports/pushplus.py`**

Implement:

- `build_pushplus_payload(token: str, title: str, content: str) -> dict`
- `send_pushplus(token: str, title: str, content: str) -> requests.Response`

Keep tests on payload construction only.

- [ ] **Step 4: Update `README.md` scheduling section**

Add:

````markdown
## Local schedule

For local self-use, run the daily job with cron after market data is available:

```bash
0 22 * * 1-5 cd /Users/marv/Documents/lurker && /usr/bin/env lurker > data/reports/daily.md
```

PushPlus can be enabled by setting `PUSHPLUS_TOKEN` and calling the push adapter from the final pipeline step.
````

- [ ] **Step 5: Run tests**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lurker/reports/pushplus.py README.md tests/test_reports.py
git commit -m "feat: add pushplus report adapter"
```

---

## Verification Checklist

Before claiming the MVP skeleton is complete:

- [ ] Run full tests:

```bash
pytest -q
```

- [ ] Run lint:

```bash
ruff check .
```

- [ ] Run local CLI:

```bash
lurker
```

- [ ] Confirm generated Markdown includes:

```text
今日主候选
次级线索
观察池变化
过热或证伪提醒
```

- [ ] Confirm these user principles are preserved:

```text
AI 不静默吞掉已触发信号
A 股负责主要发现
美股负责全球锚点
港股强过滤后补充映射
每天主候选约 10 条
```

---

## Execution Options

Plan complete and saved to `docs/superpowers/plans/2026-05-17-trend-radar-mvp.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
