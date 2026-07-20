# Watchlist Anomaly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independently scheduled `watchlist_anomaly` checkup that detects three high-signal watchlist anomalies, deduplicates persistent alerts, writes an auditable report, and pushes only through dedicated `WATCHLIST_*` recipients.

**Architecture:** Keep anomaly formulas as pure functions in `signals/anomaly.py`; put YAML validation in `config.py`, benchmark acquisition in `ingest/prices.py`, state transitions in a focused application helper, and orchestration in `application/watchlist_anomaly.py`. The CLI invokes this use case directly rather than registering it in the shared `daily_job`, which makes notification and failure isolation structural rather than conventional.

**Tech Stack:** Python 3.11+, pandas, PyYAML, dataclasses, pytest, existing yfinance/AkShare price adapters, existing Notifier implementations.

**Design reference:** `docs/superpowers/specs/2026-07-20-radar-remediation-roadmap-design.md`, especially sections 4, 10, 11.1, and 12.

---

## File map

| File | Responsibility |
|---|---|
| `configs/watchlist.yaml` | Global anomaly defaults plus per-symbol overrides |
| `src/lurker/config.py` | Strict watchlist configuration parsing and validation |
| `src/lurker/ingest/prices.py` | Fetch normalized benchmark and watchlist price history |
| `src/lurker/signals/anomaly.py` | Pure anomaly calculations and typed outcomes |
| `src/lurker/application/watchlist_alert_state.py` | Persistent alert state, cooldown decisions, atomic JSON storage |
| `src/lurker/reports/watchlist_alerts.py` | Standalone Markdown report rendering |
| `src/lurker/application/watchlist_anomaly.py` | End-to-end use-case orchestration with injected dependencies |
| `src/lurker/cli.py` | Dedicated notifier builder, CLI wrapper, parser, and dispatch |
| `docs/watchlist_anomaly.md` | Operator-facing configuration and scheduling guide |

## Task 1: Add strict watchlist configuration

**Files:**
- Modify: `src/lurker/config.py`
- Modify: `configs/watchlist.yaml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

Append tests that exercise real YAML parsing, override merging, duplicate rejection, and invalid alert names:

```python
import pytest

from lurker.config import load_watchlist


def test_load_watchlist_merges_global_defaults_and_item_overrides(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  enabled_alerts: [abnormal_volume, peak_drawdown, chronic_underperformance]
  volume_ratio: 3.0
  price_change: {cn: 0.05, hk: 0.05, us: 0.10}
  drawdown: 0.20
  underperformance_60d: 0.15
  cooldown_trading_days: 20
  worsening_step: 0.10
watchlist:
  - symbol: nvda
    market: us
    name: NVIDIA
    overrides:
      volume_ratio: 4.0
      enabled_alerts: [abnormal_volume]
""",
        encoding="utf-8",
    )

    config = load_watchlist(path)

    item = config.items[0]
    assert item.symbol == "NVDA"
    assert item.rules.volume_ratio == 4.0
    assert item.rules.price_change == 0.10
    assert item.rules.enabled_alerts == ("abnormal_volume",)
    assert item.rules.cooldown_trading_days == 20


def test_load_watchlist_rejects_duplicate_symbols(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults: {}
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
  - {symbol: 300308.sz, market: cn, name: 重复项}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate watchlist symbol"):
        load_watchlist(path)


def test_load_watchlist_rejects_unknown_alert_type(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  enabled_alerts: [moving_average_cross]
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown alert type"):
        load_watchlist(path)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py -q
```

Expected: collection fails because `load_watchlist` does not exist.

- [ ] **Step 3: Implement typed configuration and validation**

Add these dataclasses and loader to `src/lurker/config.py`:

```python
from dataclasses import dataclass


ALERT_TYPES = (
    "abnormal_volume",
    "peak_drawdown",
    "chronic_underperformance",
)
SUPPORTED_WATCHLIST_MARKETS = {"cn", "hk", "us"}


@dataclass(frozen=True)
class WatchlistRules:
    enabled_alerts: tuple[str, ...]
    volume_ratio: float
    price_change: float
    drawdown: float
    underperformance_60d: float
    cooldown_trading_days: int
    worsening_step: float


@dataclass(frozen=True)
class WatchlistItemConfig:
    symbol: str
    market: str
    name: str
    rules: WatchlistRules


@dataclass(frozen=True)
class WatchlistConfig:
    items: tuple[WatchlistItemConfig, ...]


_WATCHLIST_DEFAULTS = {
    "enabled_alerts": list(ALERT_TYPES),
    "volume_ratio": 3.0,
    "price_change": {"cn": 0.05, "hk": 0.05, "us": 0.10},
    "drawdown": 0.20,
    "underperformance_60d": 0.15,
    "cooldown_trading_days": 20,
    "worsening_step": 0.10,
}


def _ratio(value: Any, field: str) -> float:
    result = float(value)
    if not 0 < result <= 1:
        raise ValueError(f"{field} must be within (0, 1]")
    return result


def load_watchlist(path: str | Path) -> WatchlistConfig:
    data = load_yaml(path)
    raw_defaults = {**_WATCHLIST_DEFAULTS, **dict(data.get("defaults") or {})}
    price_changes = {
        **_WATCHLIST_DEFAULTS["price_change"],
        **dict(raw_defaults.get("price_change") or {}),
    }
    raw_items = data.get("watchlist")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("watchlist.yaml must contain a non-empty watchlist")

    seen: set[str] = set()
    items: list[WatchlistItemConfig] = []
    for raw_item in raw_items:
        market = str(raw_item.get("market", "")).strip().lower()
        if market not in SUPPORTED_WATCHLIST_MARKETS:
            raise ValueError(f"unsupported watchlist market: {market}")
        symbol = str(raw_item.get("symbol", "")).strip().upper()
        if not symbol:
            raise ValueError("watchlist symbol is required")
        if symbol in seen:
            raise ValueError(f"duplicate watchlist symbol: {symbol}")
        seen.add(symbol)

        overrides = dict(raw_item.get("overrides") or {})
        enabled = tuple(overrides.get("enabled_alerts", raw_defaults["enabled_alerts"]))
        unknown = set(enabled) - set(ALERT_TYPES)
        if unknown:
            raise ValueError(f"unknown alert type: {sorted(unknown)[0]}")
        volume_ratio = float(overrides.get("volume_ratio", raw_defaults["volume_ratio"]))
        if volume_ratio <= 0:
            raise ValueError("volume_ratio must be positive")
        cooldown = int(
            overrides.get("cooldown_trading_days", raw_defaults["cooldown_trading_days"])
        )
        if cooldown <= 0:
            raise ValueError("cooldown_trading_days must be positive")

        rules = WatchlistRules(
            enabled_alerts=enabled,
            volume_ratio=volume_ratio,
            price_change=_ratio(
                overrides.get("price_change", price_changes[market]),
                "price_change",
            ),
            drawdown=_ratio(overrides.get("drawdown", raw_defaults["drawdown"]), "drawdown"),
            underperformance_60d=_ratio(
                overrides.get(
                    "underperformance_60d",
                    raw_defaults["underperformance_60d"],
                ),
                "underperformance_60d",
            ),
            cooldown_trading_days=cooldown,
            worsening_step=_ratio(
                overrides.get("worsening_step", raw_defaults["worsening_step"]),
                "worsening_step",
            ),
        )
        items.append(
            WatchlistItemConfig(
                symbol=symbol,
                market=market,
                name=str(raw_item.get("name") or symbol).strip(),
                rules=rules,
            )
        )
    return WatchlistConfig(items=tuple(items))
```

Replace `configs/watchlist.yaml` with the approved defaults and retain `300308.SZ` as the first example item.

- [ ] **Step 4: Run configuration tests and full config regression**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py -q
```

Expected: all configuration tests pass.

- [ ] **Step 5: Commit the configuration contract**

```bash
git add src/lurker/config.py configs/watchlist.yaml tests/test_config.py
git commit -m "feat: validate watchlist anomaly configuration"
```

## Task 2: Add benchmark-aware history ingestion

**Files:**
- Modify: `src/lurker/ingest/prices.py`
- Modify: `tests/test_ingest.py`

- [ ] **Step 1: Probe the live AkShare index schema before writing the adapter**

Run one read-only, interactive request against the exact endpoint used by the implementation:

```bash
PYTHONPATH=src .venv/bin/python -c 'import akshare as ak; frame = ak.index_zh_a_hist(symbol="000300", period="daily", start_date="20260701", end_date="20260720"); print(type(frame)); print(frame.columns.tolist()); print(frame.dtypes.astype(str).to_dict()); print(frame.head(2).to_dict("records"))'
```

This is an implementation-time compatibility check, not a CI test. Record the observed column names and dtypes in the Task 2 implementation notes, then make the test fixture in Step 2 match that actual response. Confirm that the response exposes semantic fields for date, open, high, low, close, and volume. If any required field is absent, stop Task 2 and select a suitable AkShare index-history endpoint; do not silently synthesize a missing field.

Implementation note (2026-07-20): the live endpoint was attempted with direct, escalated, and project-compatible request paths, but Eastmoney closed the connection before a frame was returned. AkShare 1.18.60 source inspection confirms that `index_zh_a_hist` constructs `日期、开盘、收盘、最高、最低、成交量、成交额、振幅、涨跌幅、涨跌额、换手率`; the first six semantic fields are covered by the normalization fixture. Runtime normalization also validates every required field explicitly, so a future provider schema change fails loudly. A successful live no-push acceptance run remains required before enabling production push.

- [ ] **Step 2: Write failing benchmark normalization and dispatch tests**

Add tests using injected provider functions, never live network:

```python
import pandas as pd
import pytest

from lurker.ingest.prices import (
    PRICE_COLUMNS,
    fetch_watchlist_history,
    normalize_cn_index_price_frame,
)


def test_normalize_cn_index_price_frame_uses_adjusted_close_contract():
    # Keep these keys aligned with the live schema captured in Step 1.
    raw = pd.DataFrame(
        {
            "日期": ["2026-07-17", "2026-07-20"],
            "开盘": [4000.0, 4010.0],
            "最高": [4020.0, 4030.0],
            "最低": [3990.0, 4000.0],
            "收盘": [4010.0, 4025.0],
            "成交量": [100, 120],
        }
    )

    result = normalize_cn_index_price_frame(raw, symbol="000300.SH")

    assert list(result.columns) == PRICE_COLUMNS
    assert result.iloc[-1]["adj_close"] == 4025.0
    assert str(result.iloc[-1]["trade_date"]) == "2026-07-20"


def test_normalize_cn_index_price_frame_fails_loudly_when_required_field_is_missing():
    raw = pd.DataFrame(
        {
            "日期": ["2026-07-20"],
            "开盘": [4010.0],
            "最高": [4030.0],
            "最低": [4000.0],
            "收盘": [4025.0],
        }
    )

    with pytest.raises(ValueError, match="missing CN index price columns: volume"):
        normalize_cn_index_price_frame(raw, symbol="000300.SH")


def test_fetch_watchlist_history_dispatches_cn_benchmark_separately():
    calls = []

    def stock_fetcher(symbol, period):
        calls.append(("stock", symbol, period))
        return pd.DataFrame()

    def benchmark_fetcher(symbol, period):
        calls.append(("benchmark", symbol, period))
        return pd.DataFrame()

    fetch_watchlist_history(
        symbol="000300.SH",
        market="cn",
        period="2y",
        is_benchmark=True,
        stock_fetcher=stock_fetcher,
        cn_benchmark_fetcher=benchmark_fetcher,
    )

    assert calls == [("benchmark", "000300.SH", "2y")]
```

- [ ] **Step 3: Run the tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_ingest.py -q
```

Expected: import fails for the two new functions.

- [ ] **Step 4: Implement the normalized benchmark adapter with explicit schema validation**

Add to `src/lurker/ingest/prices.py`:

```python
BENCHMARK_SYMBOLS = {"cn": "000300.SH", "hk": "^HSI", "us": "SPY"}

CN_INDEX_COLUMN_ALIASES = {
    "trade_date": ("日期", "date", "trade_date"),
    "open": ("开盘", "open"),
    "high": ("最高", "high"),
    "low": ("最低", "low"),
    "close": ("收盘", "close"),
    "volume": ("成交量", "volume"),
}


def _resolve_cn_index_columns(raw: pd.DataFrame) -> dict[str, str]:
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for canonical, aliases in CN_INDEX_COLUMN_ALIASES.items():
        source = next((alias for alias in aliases if alias in raw.columns), None)
        if source is None:
            missing.append(canonical)
        else:
            resolved[source] = canonical
    if missing:
        raise ValueError(f"missing CN index price columns: {', '.join(missing)}")
    return resolved


def normalize_cn_index_price_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    normalized = raw.rename(columns=_resolve_cn_index_columns(raw)).copy()
    normalized["symbol"] = symbol
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    normalized["adj_close"] = normalized["close"]
    return normalized[PRICE_COLUMNS].sort_values("trade_date").reset_index(drop=True)


def fetch_cn_benchmark_prices(symbol: str = "000300.SH", period: str = "2y") -> pd.DataFrame:
    raw = ak.index_zh_a_hist(
        symbol=to_akshare_symbol(symbol),
        period="daily",
        start_date=period_to_start_date(period),
        end_date=today_yyyymmdd(),
    )
    return normalize_cn_index_price_frame(raw, symbol)


def fetch_watchlist_history(
    symbol: str,
    market: str,
    period: str = "2y",
    *,
    is_benchmark: bool = False,
    stock_fetcher: CnPriceFetcher | None = None,
    cn_benchmark_fetcher: CnPriceFetcher | None = None,
) -> pd.DataFrame:
    if market == "cn" and is_benchmark:
        fetcher = cn_benchmark_fetcher or fetch_cn_benchmark_prices
        return fetcher(symbol, period)
    if market == "cn":
        fetcher = stock_fetcher or fetch_cn_prices
        return fetcher(symbol, period)
    return fetch_yfinance_prices(symbol, period)
```

The application layer must import `BENCHMARK_SYMBOLS` from this ingest module; do not duplicate benchmark literals elsewhere.

After Step 1, trim `CN_INDEX_COLUMN_ALIASES` to the observed AkShare schema plus canonical English names already accepted by the ingest layer. Do not add speculative aliases: each accepted variant must have either live-schema evidence or a unit test.

- [ ] **Step 5: Run ingestion tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_ingest.py -q
```

Expected: all ingestion tests pass without network access.

- [ ] **Step 6: Commit benchmark ingestion**

```bash
git add src/lurker/ingest/prices.py tests/test_ingest.py
git commit -m "feat: add watchlist benchmark history ingestion"
```

## Task 3: Implement the three pure anomaly detectors

**Files:**
- Create: `src/lurker/signals/anomaly.py`
- Create: `tests/test_anomaly.py`

- [ ] **Step 1: Write failing detector tests**

Create `tests/test_anomaly.py` with deterministic frames. Include these essential tests:

```python
from datetime import date, timedelta

import pandas as pd
import pytest

from lurker.signals.anomaly import (
    AlertType,
    DetectionStatus,
    detect_abnormal_volume,
    detect_chronic_underperformance,
    detect_peak_drawdown,
)


def frame(closes, volumes=None, start=date(2025, 1, 1)):
    volumes = volumes or [100.0] * len(closes)
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=i) for i in range(len(closes))],
            "adj_close": closes,
            "volume": volumes,
        }
    )


def test_abnormal_volume_excludes_current_day_from_average():
    prices = frame(
        [100.0] * 20 + [106.0],
        [100.0] * 20 + [300.0],
    )

    result = detect_abnormal_volume(
        prices,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        volume_ratio_threshold=3.0,
        price_change_threshold=0.05,
    )

    assert result.status is DetectionStatus.ALERT
    assert result.alert.alert_type is AlertType.ABNORMAL_VOLUME
    assert result.alert.metrics["volume_ratio"] == 3.0


def test_abnormal_volume_reports_insufficient_data_for_zero_average():
    prices = frame([100.0] * 20 + [106.0], [0.0] * 20 + [300.0])

    result = detect_abnormal_volume(
        prices,
        symbol="NVDA",
        market="us",
        name="NVIDIA",
        volume_ratio_threshold=3.0,
        price_change_threshold=0.10,
    )

    assert result.status is DetectionStatus.INSUFFICIENT_DATA


def test_abnormal_volume_reports_insufficient_data_when_volume_column_is_missing():
    prices = frame([100.0] * 20 + [106.0]).drop(columns=["volume"])

    result = detect_abnormal_volume(
        prices,
        symbol="NVDA",
        market="us",
        name="NVIDIA",
        volume_ratio_threshold=3.0,
        price_change_threshold=0.10,
    )

    assert result.status is DetectionStatus.INSUFFICIENT_DATA
    assert result.reason == "missing columns: volume"


def test_peak_drawdown_uses_adjusted_close_peak():
    prices = frame([100.0] + [90.0] * 248 + [80.0])

    result = detect_peak_drawdown(
        prices,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.20,
    )

    assert result.status is DetectionStatus.ALERT
    assert result.alert.metrics["drawdown"] == pytest.approx(-0.20)
    assert result.alert.severity == pytest.approx(0.20)


def test_underperformance_aligns_stock_and_benchmark_dates():
    stock = frame([100.0] * 60 + [80.0])
    benchmark = frame([100.0] * 60 + [100.0])

    result = detect_chronic_underperformance(
        stock,
        benchmark,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.15,
    )

    assert result.status is DetectionStatus.ALERT
    assert result.alert.metrics["alpha_60d"] == pytest.approx(-0.20)


def test_underperformance_requires_61_common_dates():
    stock = frame([100.0] * 61)
    benchmark = frame([100.0] * 60, start=date(2025, 1, 2))

    result = detect_chronic_underperformance(
        stock,
        benchmark,
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        threshold=0.15,
    )

    assert result.status is DetectionStatus.INSUFFICIENT_DATA
```

- [ ] **Step 2: Run the detector tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_anomaly.py -q
```

Expected: module import fails because `lurker.signals.anomaly` does not exist.

- [ ] **Step 3: Implement typed outcomes and pure calculations**

Create `src/lurker/signals/anomaly.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd


class AlertType(str, Enum):
    ABNORMAL_VOLUME = "abnormal_volume"
    PEAK_DRAWDOWN = "peak_drawdown"
    CHRONIC_UNDERPERFORMANCE = "chronic_underperformance"


class DetectionStatus(str, Enum):
    ALERT = "alert"
    NORMAL = "normal"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class AnomalyAlert:
    symbol: str
    market: str
    name: str
    alert_type: AlertType
    observed_on: str
    severity: float
    metrics: dict[str, float]


@dataclass(frozen=True)
class DetectionOutcome:
    alert_type: AlertType
    status: DetectionStatus
    alert: AnomalyAlert | None = None
    reason: str | None = None


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.dropna(subset=["trade_date", "adj_close"]).copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    return result.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def _missing_columns(frame: pd.DataFrame, required: set[str]) -> str | None:
    missing = sorted(required - set(frame.columns))
    return f"missing columns: {', '.join(missing)}" if missing else None


def _outcome(
    alert_type: AlertType,
    *,
    status: DetectionStatus,
    alert: AnomalyAlert | None = None,
    reason: str | None = None,
) -> DetectionOutcome:
    return DetectionOutcome(alert_type=alert_type, status=status, alert=alert, reason=reason)


def detect_abnormal_volume(
    prices: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    name: str,
    volume_ratio_threshold: float,
    price_change_threshold: float,
) -> DetectionOutcome:
    kind = AlertType.ABNORMAL_VOLUME
    missing = _missing_columns(prices, {"trade_date", "adj_close", "volume"})
    if missing:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason=missing)
    rows = _ordered(prices).dropna(subset=["volume"])
    if len(rows) < 21:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason="need 21 price rows")
    previous = rows.iloc[-21:-1]
    average_volume = float(previous["volume"].mean())
    if average_volume <= 0:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason="20-day average volume is zero")
    current = rows.iloc[-1]
    prior = rows.iloc[-2]
    prior_close = float(prior["adj_close"])
    if prior_close <= 0:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason="prior close is not positive")
    volume_ratio = float(current["volume"]) / average_volume
    price_change = float(current["adj_close"]) / prior_close - 1
    if volume_ratio < volume_ratio_threshold or abs(price_change) < price_change_threshold:
        return _outcome(kind, status=DetectionStatus.NORMAL)
    alert = AnomalyAlert(
        symbol=symbol,
        market=market,
        name=name,
        alert_type=kind,
        observed_on=str(current["trade_date"].date()),
        severity=abs(price_change),
        metrics={"volume_ratio": volume_ratio, "price_change": price_change},
    )
    return _outcome(kind, status=DetectionStatus.ALERT, alert=alert)


def detect_peak_drawdown(
    prices: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    name: str,
    threshold: float,
) -> DetectionOutcome:
    kind = AlertType.PEAK_DRAWDOWN
    missing = _missing_columns(prices, {"trade_date", "adj_close"})
    if missing:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason=missing)
    rows = _ordered(prices)
    if len(rows) < 250:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason="need 250 price rows")
    window = rows.iloc[-250:]
    peak = float(window["adj_close"].max())
    current = float(window.iloc[-1]["adj_close"])
    if peak <= 0:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason="250-day peak is not positive")
    drawdown = current / peak - 1
    if drawdown > -threshold:
        return _outcome(kind, status=DetectionStatus.NORMAL)
    alert = AnomalyAlert(
        symbol=symbol,
        market=market,
        name=name,
        alert_type=kind,
        observed_on=str(window.iloc[-1]["trade_date"].date()),
        severity=abs(drawdown),
        metrics={"peak_250": peak, "drawdown": drawdown},
    )
    return _outcome(kind, status=DetectionStatus.ALERT, alert=alert)


def detect_chronic_underperformance(
    stock_prices: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    name: str,
    threshold: float,
) -> DetectionOutcome:
    kind = AlertType.CHRONIC_UNDERPERFORMANCE
    stock_missing = _missing_columns(stock_prices, {"trade_date", "adj_close"})
    benchmark_missing = _missing_columns(benchmark_prices, {"trade_date", "adj_close"})
    if stock_missing or benchmark_missing:
        reason = stock_missing or benchmark_missing
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason=reason)
    stock = _ordered(stock_prices)[["trade_date", "adj_close"]].rename(columns={"adj_close": "stock"})
    benchmark = _ordered(benchmark_prices)[["trade_date", "adj_close"]].rename(columns={"adj_close": "benchmark"})
    common = stock.merge(benchmark, on="trade_date", how="inner").sort_values("trade_date")
    if len(common) < 61:
        return _outcome(kind, status=DetectionStatus.INSUFFICIENT_DATA, reason="need 61 common price rows")
    window = common.iloc[-61:]
    stock_return = float(window.iloc[-1]["stock"]) / float(window.iloc[0]["stock"]) - 1
    benchmark_return = float(window.iloc[-1]["benchmark"]) / float(window.iloc[0]["benchmark"]) - 1
    alpha = stock_return - benchmark_return
    if alpha > -threshold:
        return _outcome(kind, status=DetectionStatus.NORMAL)
    alert = AnomalyAlert(
        symbol=symbol,
        market=market,
        name=name,
        alert_type=kind,
        observed_on=str(window.iloc[-1]["trade_date"].date()),
        severity=abs(alpha),
        metrics={
            "stock_return_60d": stock_return,
            "benchmark_return_60d": benchmark_return,
            "alpha_60d": alpha,
        },
    )
    return _outcome(kind, status=DetectionStatus.ALERT, alert=alert)
```

- [ ] **Step 4: Run detector tests and lint**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_anomaly.py -q
.venv/bin/ruff check src/lurker/signals/anomaly.py tests/test_anomaly.py
```

Expected: detector tests pass and Ruff reports no errors. Use `pytest.approx` if binary floating-point makes an exact decimal assertion unstable.

- [ ] **Step 5: Commit the anomaly engine**

```bash
git add src/lurker/signals/anomaly.py tests/test_anomaly.py
git commit -m "feat: detect watchlist price anomalies"
```

## Task 4: Implement alert state and cooldown decisions

**Files:**
- Create: `src/lurker/application/watchlist_alert_state.py`
- Create: `tests/test_watchlist_alert_state.py`

- [ ] **Step 1: Write failing state-machine tests**

Create tests for first alert, cooldown suppression, worsening re-alert, recovery, same-day event dedupe, notification failure semantics, and atomic persistence:

```python
from lurker.application.watchlist_alert_state import (
    AlertStateStore,
    decide_notification,
    mark_detected,
    mark_notified,
    mark_recovered,
)
from lurker.signals.anomaly import AlertType, AnomalyAlert


def alert(kind=AlertType.PEAK_DRAWDOWN, severity=0.20, observed_on="2026-07-20"):
    return AnomalyAlert(
        symbol="300308.SZ",
        market="cn",
        name="中际旭创",
        alert_type=kind,
        observed_on=observed_on,
        severity=severity,
        metrics={},
    )


def test_first_detection_notifies_but_cooldown_suppresses_repeat():
    state = {}
    current = alert()
    assert decide_notification(current, state, trading_days_since_notification=None, cooldown=20, worsening_step=0.10)
    mark_detected(current, state)
    mark_notified(current, state)
    assert not decide_notification(current, state, trading_days_since_notification=5, cooldown=20, worsening_step=0.10)


def test_persistent_alert_realerts_when_severity_worsens_ten_points():
    state = {}
    original = alert(severity=0.20)
    mark_detected(original, state)
    mark_notified(original, state)

    assert decide_notification(
        alert(severity=0.30, observed_on="2026-07-21"),
        state,
        trading_days_since_notification=1,
        cooldown=20,
        worsening_step=0.10,
    )


def test_persistent_alert_realerts_after_twenty_trading_days():
    state = {}
    current = alert()
    mark_detected(current, state)
    mark_notified(current, state)

    assert decide_notification(
        alert(observed_on="2026-08-17"),
        state,
        trading_days_since_notification=20,
        cooldown=20,
        worsening_step=0.10,
    )


def test_abnormal_volume_deduplicates_only_the_same_observed_date():
    state = {}
    current = alert(kind=AlertType.ABNORMAL_VOLUME)
    mark_detected(current, state)
    mark_notified(current, state)

    assert not decide_notification(
        current,
        state,
        trading_days_since_notification=0,
        cooldown=20,
        worsening_step=0.10,
    )
    assert decide_notification(
        alert(kind=AlertType.ABNORMAL_VOLUME, observed_on="2026-07-21"),
        state,
        trading_days_since_notification=1,
        cooldown=20,
        worsening_step=0.10,
    )


def test_recovery_makes_later_crossing_a_new_event():
    state = {}
    current = alert()
    mark_detected(current, state)
    mark_notified(current, state)
    mark_recovered("300308.SZ", AlertType.PEAK_DRAWDOWN, state, "2026-07-21")

    assert decide_notification(
        alert(observed_on="2026-07-22"),
        state,
        trading_days_since_notification=2,
        cooldown=20,
        worsening_step=0.10,
    )


def test_state_store_round_trips_with_atomic_replace(tmp_path):
    path = tmp_path / "state.json"
    store = AlertStateStore(path)
    state = {}
    mark_detected(alert(), state)
    store.save(state)

    assert store.load() == state
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Run state tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_watchlist_alert_state.py -q
```

Expected: module import fails.

- [ ] **Step 3: Implement explicit detected/notified state**

Create a JSON state helper with this public contract:

```python
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from lurker.signals.anomaly import AlertType, AnomalyAlert


AlertState = dict[str, dict[str, Any]]


def state_key(symbol: str, alert_type: AlertType) -> str:
    return f"{symbol.upper()}::{alert_type.value}"


def decide_notification(
    alert: AnomalyAlert,
    state: AlertState,
    *,
    trading_days_since_notification: int | None,
    cooldown: int,
    worsening_step: float,
) -> bool:
    record = state.get(state_key(alert.symbol, alert.alert_type))
    if not record or not record.get("active") or not record.get("last_notified_date"):
        return True
    if alert.alert_type is AlertType.ABNORMAL_VOLUME:
        return record.get("last_notified_date") != alert.observed_on
    if trading_days_since_notification is not None and trading_days_since_notification >= cooldown:
        return True
    previous = float(record.get("last_notified_severity", 0.0))
    return alert.severity - previous >= worsening_step - 1e-12


def mark_detected(alert: AnomalyAlert, state: AlertState) -> None:
    record = state.setdefault(state_key(alert.symbol, alert.alert_type), {})
    record.update(
        {
            "active": True,
            "last_detected_date": alert.observed_on,
            "last_detected_severity": alert.severity,
        }
    )


def mark_notified(alert: AnomalyAlert, state: AlertState) -> None:
    record = state.setdefault(state_key(alert.symbol, alert.alert_type), {})
    record.update(
        {
            "active": True,
            "last_notified_date": alert.observed_on,
            "last_notified_severity": alert.severity,
        }
    )


def mark_recovered(
    symbol: str,
    alert_type: AlertType,
    state: AlertState,
    observed_on: str,
) -> None:
    record = state.get(state_key(symbol, alert_type))
    if record:
        record.update({"active": False, "last_detected_date": observed_on})


class AlertStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AlertState:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("watchlist alert state must be a JSON object")
        return raw

    def save(self, state: AlertState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
```

The application computes `trading_days_since_notification` from the symbol's actual normalized price dates, not from calendar weekdays.

- [ ] **Step 4: Run state tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_watchlist_alert_state.py -q
```

Expected: all state tests pass.

- [ ] **Step 5: Commit state handling**

```bash
git add src/lurker/application/watchlist_alert_state.py tests/test_watchlist_alert_state.py
git commit -m "feat: persist watchlist alert cooldown state"
```

## Task 5: Render an independent watchlist report

**Files:**
- Create: `src/lurker/reports/watchlist_alerts.py`
- Create: `tests/test_watchlist_alerts_report.py`

- [ ] **Step 1: Write failing report tests**

```python
from lurker.reports.watchlist_alerts import render_watchlist_alerts
from lurker.signals.anomaly import AlertType, AnomalyAlert


def test_render_watchlist_alerts_groups_multiple_alerts_by_symbol():
    alerts = [
        AnomalyAlert("300308.SZ", "cn", "中际旭创", AlertType.PEAK_DRAWDOWN, "2026-07-20", 0.25, {"drawdown": -0.25}),
        AnomalyAlert("300308.SZ", "cn", "中际旭创", AlertType.CHRONIC_UNDERPERFORMANCE, "2026-07-20", 0.18, {"alpha_60d": -0.18}),
    ]

    report = render_watchlist_alerts(
        report_date="2026-07-20",
        alerts=alerts,
        data_issues=[],
        checked_count=1,
    )

    assert report.count("## 中际旭创（300308.SZ）") == 1
    assert "高位回撤" in report
    assert "持续跑输" in report
    assert "数据截止日：2026-07-20" in report


def test_render_watchlist_alerts_records_silent_and_degraded_runs():
    report = render_watchlist_alerts(
        report_date="2026-07-20",
        alerts=[],
        data_issues=["NVDA：行情抓取失败"],
        checked_count=1,
    )

    assert "本次没有需要推送的新异常" in report
    assert "NVDA：行情抓取失败" in report
```

- [ ] **Step 2: Run report tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_watchlist_alerts_report.py -q
```

Expected: module import fails.

- [ ] **Step 3: Implement deterministic Markdown rendering**

Create `src/lurker/reports/watchlist_alerts.py` with this complete renderer:

```python
from __future__ import annotations

from collections import defaultdict

from lurker.signals.anomaly import AlertType, AnomalyAlert


ALERT_LABELS = {
    AlertType.ABNORMAL_VOLUME: "🚨 巨量异动",
    AlertType.PEAK_DRAWDOWN: "⚠️ 高位回撤",
    AlertType.CHRONIC_UNDERPERFORMANCE: "📉 持续跑输",
}


def _alert_detail(alert: AnomalyAlert) -> str:
    if alert.alert_type is AlertType.ABNORMAL_VOLUME:
        return (
            f"今日放量 {alert.metrics['volume_ratio']:.2f} 倍，"
            f"涨跌幅 {alert.metrics['price_change'] * 100:.2f}%"
        )
    if alert.alert_type is AlertType.PEAK_DRAWDOWN:
        return f"从 250 日高点回撤 {abs(alert.metrics['drawdown']) * 100:.2f}%"
    return f"近 60 日跑输基准 {abs(alert.metrics['alpha_60d']) * 100:.2f}%"


def render_watchlist_alerts(
    *,
    report_date: str,
    alerts: list[AnomalyAlert],
    data_issues: list[str],
    checked_count: int,
) -> str:
    lines = [
        "# 自选股异常体检",
        "",
        f"报告日期：{report_date}",
        f"检查标的：{checked_count} 只",
        f"新异常：{len(alerts)} 条",
        "",
    ]
    if not alerts:
        lines.extend(["本次没有需要推送的新异常。", ""])
    else:
        grouped: dict[str, list[AnomalyAlert]] = defaultdict(list)
        for alert in alerts:
            grouped[alert.symbol.upper()].append(alert)
        for symbol in sorted(grouped):
            symbol_alerts = grouped[symbol]
            first = symbol_alerts[0]
            lines.extend([f"## {first.name}（{symbol}）", ""])
            for alert in sorted(symbol_alerts, key=lambda item: item.alert_type.value):
                lines.append(f"- {ALERT_LABELS[alert.alert_type]}：{_alert_detail(alert)}")
            observed_on = max(alert.observed_on for alert in symbol_alerts)
            lines.extend([f"- 数据截止日：{observed_on}", ""])

    lines.extend(["## 数据质量", ""])
    if data_issues:
        lines.extend(f"- {issue}" for issue in data_issues)
    else:
        lines.append("- 所有已启用检测均获得足够数据。")
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run report tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_watchlist_alerts_report.py -q
```

Expected: both report tests pass.

- [ ] **Step 5: Commit report rendering**

```bash
git add src/lurker/reports/watchlist_alerts.py tests/test_watchlist_alerts_report.py
git commit -m "feat: render independent watchlist alerts"
```

## Task 6: Build a notifier that cannot fall back to daily recipients

**Files:**
- Modify: `src/lurker/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing environment-isolation tests**

Add:

```python
def test_watchlist_notifier_does_not_use_daily_recipient_environment(monkeypatch):
    monkeypatch.setenv("PUSHPLUS_TOKEN", "daily-token")
    monkeypatch.setenv("EMAIL_TO", "daily@example.com")
    monkeypatch.delenv("WATCHLIST_PUSHPLUS_TOKEN", raising=False)
    monkeypatch.delenv("WATCHLIST_SMTP_HOST", raising=False)
    monkeypatch.delenv("WATCHLIST_SMTP_FROM", raising=False)
    monkeypatch.delenv("WATCHLIST_EMAIL_TO", raising=False)

    assert build_watchlist_notifier_from_env() is None


def test_watchlist_notifier_uses_only_watchlist_email_recipient(monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "daily@example.com")
    monkeypatch.setenv("WATCHLIST_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("WATCHLIST_SMTP_FROM", "watch@example.com")
    monkeypatch.setenv("WATCHLIST_EMAIL_TO", "owner@example.com")

    notifier = build_watchlist_notifier_from_env()

    assert notifier.recipients == ["owner@example.com"]
```

Import `build_watchlist_notifier_from_env` in the test module.

- [ ] **Step 2: Run the two tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py -k watchlist_notifier -q
```

Expected: import fails because the dedicated builder does not exist.

- [ ] **Step 3: Implement the strict builder**

Add beside `build_notifier_from_env()` in `src/lurker/cli.py`:

```python
def build_watchlist_notifier_from_env():
    import os

    notifiers = []
    token = os.environ.get("WATCHLIST_PUSHPLUS_TOKEN")
    if token:
        from lurker.notification.pushplus_notifier import PushPlusNotifier

        notifiers.append(PushPlusNotifier(token=token))

    smtp_host = os.environ.get("WATCHLIST_SMTP_HOST")
    smtp_from = os.environ.get("WATCHLIST_SMTP_FROM")
    email_to = os.environ.get("WATCHLIST_EMAIL_TO")
    if smtp_host and smtp_from and email_to:
        from lurker.notification.email_notifier import EmailNotifier

        recipients = [value.strip() for value in email_to.split(",") if value.strip()]
        notifiers.append(
            EmailNotifier(
                host=smtp_host,
                port=int(os.environ.get("WATCHLIST_SMTP_PORT", "587")),
                username=os.environ.get("WATCHLIST_SMTP_USER"),
                password=os.environ.get("WATCHLIST_SMTP_PASSWORD"),
                sender=smtp_from,
                recipients=recipients,
                use_tls=_env_bool(os.environ.get("WATCHLIST_SMTP_USE_TLS"), default=True),
                use_ssl=_env_bool(os.environ.get("WATCHLIST_SMTP_USE_SSL"), default=False),
            )
        )

    if not notifiers:
        return None
    if len(notifiers) == 1:
        return notifiers[0]
    from lurker.notification.notifier import CompositeNotifier

    return CompositeNotifier(notifiers)
```

Do not refactor this through the daily builder if that introduces unprefixed environment fallback.

- [ ] **Step 4: Run notifier and existing notification tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py -k watchlist_notifier -q
PYTHONPATH=src .venv/bin/pytest tests/test_notification_email.py -q
```

Expected: dedicated isolation tests and existing notification tests pass.

- [ ] **Step 5: Commit notifier isolation**

```bash
git add src/lurker/cli.py tests/test_cli.py
git commit -m "feat: isolate watchlist notification recipients"
```

## Task 7: Orchestrate checkup, state, report, and push

**Files:**
- Create: `src/lurker/application/watchlist_anomaly.py`
- Create: `tests/test_watchlist_anomaly.py`

- [ ] **Step 1: Write failing application tests with injected fetchers**

Create `tests/test_watchlist_anomaly.py`. Start with these helpers and tests; they cover benchmark reuse, successful notification state, no-push state, notification retry, and total data failure:

```python
import pandas as pd
import pytest

from lurker.application.watchlist_alert_state import AlertStateStore
from lurker.application.watchlist_anomaly import run_watchlist_anomaly
from lurker.config import WatchlistConfig, WatchlistItemConfig, WatchlistRules


def rules():
    return WatchlistRules(
        enabled_alerts=(
            "abnormal_volume",
            "peak_drawdown",
            "chronic_underperformance",
        ),
        volume_ratio=3.0,
        price_change=0.05,
        drawdown=0.20,
        underperformance_60d=0.15,
        cooldown_trading_days=20,
        worsening_step=0.10,
    )


def config():
    return WatchlistConfig(
        items=(
            WatchlistItemConfig("300308.SZ", "cn", "中际旭创", rules()),
            WatchlistItemConfig("300502.SZ", "cn", "新易盛", rules()),
        )
    )


def price_frame(*, alerting):
    dates = pd.bdate_range(end="2026-07-20", periods=250)
    closes = [100.0] * 250
    volumes = [100.0] * 250
    if alerting:
        closes[-1] = 70.0
        volumes[-1] = 400.0
    return pd.DataFrame(
        {"trade_date": dates, "adj_close": closes, "volume": volumes}
    )


def benchmark_frame():
    dates = pd.bdate_range(end="2026-07-20", periods=250)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "adj_close": [100.0] * 250,
            "volume": [100.0] * 250,
        }
    )


class RecordingNotifier:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sends = []

    def send(self, title, markdown_content):
        if self.fail:
            raise RuntimeError("notification offline")
        self.sends.append((title, markdown_content))


def test_run_watchlist_anomaly_reuses_benchmark_and_marks_successful_push(tmp_path):
    calls = []

    def fetcher(symbol, market, period, *, is_benchmark=False):
        calls.append((symbol, market, is_benchmark))
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    notifier = RecordingNotifier()
    store = AlertStateStore(tmp_path / "state.json")
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=store,
        history_fetcher=fetcher,
        notifier=notifier,
        push=True,
    )

    assert sum(is_benchmark for _, _, is_benchmark in calls) == 1
    assert result.new_alert_count == 6
    assert result.pushed is True
    assert len(notifier.sends) == 1
    saved = store.load()
    assert all(
        record.get("last_notified_date") == "2026-07-20"
        for record in saved.values()
        if record["active"]
    )


def test_no_push_records_detection_but_not_notification(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    store = AlertStateStore(tmp_path / "state.json")
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=store,
        history_fetcher=fetcher,
        notifier=RecordingNotifier(),
        push=False,
    )

    assert result.pushed is False
    assert all(
        record.get("last_notified_date") is None
        for record in store.load().values()
    )


def test_notification_failure_leaves_alert_retryable(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    store = AlertStateStore(tmp_path / "state.json")
    with pytest.raises(RuntimeError, match="notification offline"):
        run_watchlist_anomaly(
            config=config(),
            report_date="2026-07-20",
            report_dir=tmp_path / "reports",
            state_store=store,
            history_fetcher=fetcher,
            notifier=RecordingNotifier(fail=True),
            push=True,
        )

    assert all(
        record.get("last_notified_date") is None
        for record in store.load().values()
    )


def test_all_stock_failures_write_report_without_push(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        if is_benchmark:
            return benchmark_frame()
        raise RuntimeError("price offline")

    notifier = RecordingNotifier()
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=notifier,
        push=True,
    )

    assert result.failure_count == 2
    assert result.pushed is False
    assert notifier.sends == []
    assert "price offline" in result.content_md
    assert result.report_path.exists()


def test_silent_run_writes_report_without_push(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=False)

    notifier = RecordingNotifier()
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=notifier,
        push=True,
    )

    assert result.new_alert_count == 0
    assert result.pushed is False
    assert notifier.sends == []
    assert "本次没有需要推送的新异常" in result.content_md


def test_partial_stock_failure_still_pushes_successful_alerts(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        if is_benchmark:
            return benchmark_frame()
        if symbol == "300502.SZ":
            raise RuntimeError("one stock offline")
        return price_frame(alerting=True)

    notifier = RecordingNotifier()
    result = run_watchlist_anomaly(
        config=config(),
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=notifier,
        push=True,
    )

    assert result.new_alert_count == 3
    assert result.failure_count == 1
    assert result.pushed is True
    assert len(notifier.sends) == 1
    assert "one stock offline" in result.content_md


def test_item_enabled_alerts_controls_detectors_that_run(tmp_path):
    abnormal_only = WatchlistRules(
        enabled_alerts=("abnormal_volume",),
        volume_ratio=3.0,
        price_change=0.05,
        drawdown=0.20,
        underperformance_60d=0.15,
        cooldown_trading_days=20,
        worsening_step=0.10,
    )
    one_item = WatchlistConfig(
        items=(WatchlistItemConfig("300308.SZ", "cn", "中际旭创", abnormal_only),)
    )

    def fetcher(symbol, market, period, *, is_benchmark=False):
        return price_frame(alerting=True)

    result = run_watchlist_anomaly(
        config=one_item,
        report_date="2026-07-20",
        report_dir=tmp_path / "reports",
        state_store=AlertStateStore(tmp_path / "state.json"),
        history_fetcher=fetcher,
        notifier=None,
        push=True,
    )

    assert result.new_alert_count == 1
    assert "巨量异动" in result.content_md
    assert "高位回撤" not in result.content_md
    assert "持续跑输" not in result.content_md


def test_repeating_same_trade_date_does_not_push_twice(tmp_path):
    def fetcher(symbol, market, period, *, is_benchmark=False):
        return benchmark_frame() if is_benchmark else price_frame(alerting=True)

    notifier = RecordingNotifier()
    store = AlertStateStore(tmp_path / "state.json")
    arguments = {
        "config": config(),
        "report_date": "2026-07-20",
        "report_dir": tmp_path / "reports",
        "state_store": store,
        "history_fetcher": fetcher,
        "notifier": notifier,
        "push": True,
    }

    first = run_watchlist_anomaly(**arguments)
    second = run_watchlist_anomaly(**arguments)

    assert first.pushed is True
    assert second.pushed is False
    assert second.new_alert_count == 0
    assert len(notifier.sends) == 1
    assert "巨量异动" in second.report_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run application tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_watchlist_anomaly.py -q
```

Expected: module import fails.

- [ ] **Step 3: Implement the application use case**

Create `src/lurker/application/watchlist_anomaly.py` with this complete use case:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from lurker.application.watchlist_alert_state import (
    AlertStateStore,
    decide_notification,
    mark_detected,
    mark_notified,
    mark_recovered,
    state_key,
)
from lurker.config import WatchlistConfig, WatchlistItemConfig
from lurker.ingest.prices import BENCHMARK_SYMBOLS, fetch_watchlist_history
from lurker.notification.notifier import Notifier
from lurker.reports.watchlist_alerts import render_watchlist_alerts
from lurker.signals.anomaly import (
    AlertType,
    AnomalyAlert,
    DetectionOutcome,
    DetectionStatus,
    detect_abnormal_volume,
    detect_chronic_underperformance,
    detect_peak_drawdown,
)


HistoryFetcher = Callable[..., pd.DataFrame]


@dataclass(frozen=True)
class WatchlistCheckupResult:
    report_path: Path
    checked_count: int
    new_alert_count: int
    failure_count: int
    pushed: bool
    content_md: str


def _trading_days_since(
    prices: pd.DataFrame,
    last_notified_date: str | None,
) -> int | None:
    if not last_notified_date:
        return None
    dates = pd.to_datetime(prices["trade_date"], errors="coerce").dropna().dt.date
    cutoff = pd.Timestamp(last_notified_date).date()
    return int(sum(value > cutoff for value in dates))


def _detect(
    item: WatchlistItemConfig,
    stock: pd.DataFrame,
    benchmark: pd.DataFrame | None,
) -> list[DetectionOutcome]:
    outcomes: list[DetectionOutcome] = []
    enabled = set(item.rules.enabled_alerts)
    if AlertType.ABNORMAL_VOLUME.value in enabled:
        outcomes.append(
            detect_abnormal_volume(
                stock,
                symbol=item.symbol,
                market=item.market,
                name=item.name,
                volume_ratio_threshold=item.rules.volume_ratio,
                price_change_threshold=item.rules.price_change,
            )
        )
    if AlertType.PEAK_DRAWDOWN.value in enabled:
        outcomes.append(
            detect_peak_drawdown(
                stock,
                symbol=item.symbol,
                market=item.market,
                name=item.name,
                threshold=item.rules.drawdown,
            )
        )
    if AlertType.CHRONIC_UNDERPERFORMANCE.value in enabled and benchmark is not None:
        outcomes.append(
            detect_chronic_underperformance(
                stock,
                benchmark,
                symbol=item.symbol,
                market=item.market,
                name=item.name,
                threshold=item.rules.underperformance_60d,
            )
        )
    return outcomes


def run_watchlist_anomaly(
    *,
    config: WatchlistConfig,
    report_date: str,
    report_dir: str | Path,
    state_store: AlertStateStore,
    history_fetcher: HistoryFetcher = fetch_watchlist_history,
    notifier: Notifier | None = None,
    push: bool = True,
    period: str = "2y",
) -> WatchlistCheckupResult:
    state = state_store.load()
    data_issues: list[str] = []
    benchmarks: dict[str, pd.DataFrame] = {}
    benchmark_errors: dict[str, str] = {}
    benchmark_markets = {
        item.market
        for item in config.items
        if AlertType.CHRONIC_UNDERPERFORMANCE.value in item.rules.enabled_alerts
    }
    for market in sorted(benchmark_markets):
        symbol = BENCHMARK_SYMBOLS[market]
        try:
            benchmarks[market] = history_fetcher(
                symbol,
                market,
                period,
                is_benchmark=True,
            )
        except Exception as exc:
            benchmark_errors[market] = f"{type(exc).__name__}: {exc}"
            data_issues.append(f"{market} 基准 {symbol}：{benchmark_errors[market]}")

    new_alerts: list[AnomalyAlert] = []
    successful_stocks = 0
    stock_failures = 0
    for item in config.items:
        try:
            stock = history_fetcher(
                item.symbol,
                item.market,
                period,
                is_benchmark=False,
            )
        except Exception as exc:
            stock_failures += 1
            data_issues.append(f"{item.symbol}：{type(exc).__name__}: {exc}")
            continue
        successful_stocks += 1
        benchmark = benchmarks.get(item.market)
        if (
            AlertType.CHRONIC_UNDERPERFORMANCE.value in item.rules.enabled_alerts
            and benchmark is None
        ):
            data_issues.append(f"{item.symbol}：缺少 {item.market} 基准，未运行持续跑输检测")

        for outcome in _detect(item, stock, benchmark):
            if outcome.status is DetectionStatus.INSUFFICIENT_DATA:
                data_issues.append(
                    f"{item.symbol} {outcome.alert_type.value}：{outcome.reason}"
                )
                continue
            if outcome.status is DetectionStatus.NORMAL:
                if outcome.alert_type is not AlertType.ABNORMAL_VOLUME:
                    observed = str(pd.to_datetime(stock["trade_date"]).max().date())
                    mark_recovered(item.symbol, outcome.alert_type, state, observed)
                continue
            alert = outcome.alert
            if alert is None:
                raise RuntimeError("alert outcome is missing its alert payload")
            record = state.get(state_key(alert.symbol, alert.alert_type), {})
            trading_days = _trading_days_since(stock, record.get("last_notified_date"))
            should_notify = decide_notification(
                alert,
                state,
                trading_days_since_notification=trading_days,
                cooldown=item.rules.cooldown_trading_days,
                worsening_step=item.rules.worsening_step,
            )
            mark_detected(alert, state)
            if should_notify:
                new_alerts.append(alert)

    rendered_content = render_watchlist_alerts(
        report_date=report_date,
        alerts=new_alerts,
        data_issues=data_issues,
        checked_count=len(config.items),
    )
    resolved_report_dir = Path(report_dir)
    resolved_report_dir.mkdir(parents=True, exist_ok=True)
    report_path = resolved_report_dir / f"{report_date}.md"
    if report_path.exists() and not new_alerts:
        content = report_path.read_text(encoding="utf-8")
    else:
        content = rendered_content
        report_path.write_text(content, encoding="utf-8")
    state_store.save(state)

    pushed = False
    if push and notifier is not None and new_alerts and successful_stocks > 0:
        notifier.send(
            title=f"[{len(new_alerts)}个异常] 自选股异常体检 ({report_date})",
            markdown_content=content,
        )
        for alert in new_alerts:
            mark_notified(alert, state)
        state_store.save(state)
        pushed = True

    return WatchlistCheckupResult(
        report_path=report_path,
        checked_count=len(config.items),
        new_alert_count=len(new_alerts),
        failure_count=stock_failures + len(benchmark_errors),
        pushed=pushed,
        content_md=content,
    )
```

Notification exceptions intentionally propagate after the first state save. The implementation must not add a broad exception handler around `notifier.send`, because the unchanged notification fields are what make the alert retryable.

- [ ] **Step 4: Run application tests and focused regressions**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_watchlist_anomaly.py tests/test_watchlist_alert_state.py tests/test_anomaly.py -q
```

Expected: all anomaly application tests pass.

- [ ] **Step 5: Commit orchestration**

```bash
git add src/lurker/application/watchlist_anomaly.py tests/test_watchlist_anomaly.py
git commit -m "feat: orchestrate watchlist anomaly checkups"
```

## Task 8: Wire the CLI, operator guide, and end-to-end acceptance

**Files:**
- Modify: `src/lurker/cli.py`
- Modify: `tests/test_cli.py`
- Create: `docs/watchlist_anomaly.md`
- Modify: `README.md`

- [ ] **Step 1: Write failing parser and CLI wrapper tests**

Add parser coverage:

```python
def test_parser_has_independent_watchlist_checkup_defaults():
    args = build_parser().parse_args(["watchlist-checkup"])

    assert args.command == "watchlist-checkup"
    assert args.watchlist.name == "watchlist.yaml"
    assert args.report_dir.parts[-2:] == ("reports", "watchlist")
    assert args.state_file.name == "watchlist_alert_state.json"
    assert args.no_push is False
```

Add `watchlist_checkup` to the imports from `lurker.cli`, then add this wrapper test:

```python
from types import SimpleNamespace


def test_watchlist_checkup_passes_no_push_and_returns_counts(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("lurker.config.load_watchlist", lambda path: "loaded-config")

    def fake_run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            report_path=tmp_path / "reports" / "2026-07-20.md",
            checked_count=2,
            new_alert_count=1,
            failure_count=1,
            pushed=False,
        )

    monkeypatch.setattr(
        "lurker.application.watchlist_anomaly.run_watchlist_anomaly",
        fake_run,
    )
    monkeypatch.setattr(
        "lurker.cli.build_watchlist_notifier_from_env",
        lambda: "watchlist-notifier",
    )

    message = watchlist_checkup(
        watchlist_path=tmp_path / "watchlist.yaml",
        report_dir=tmp_path / "reports",
        state_file=tmp_path / "state.json",
        report_date="2026-07-20",
        push=False,
    )

    assert calls[0]["config"] == "loaded-config"
    assert calls[0]["push"] is False
    assert calls[0]["notifier"] == "watchlist-notifier"
    assert "checked=2, alerts=1, failures=1, pushed=False" in message
```

- [ ] **Step 2: Run CLI tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_cli.py -k watchlist -q
```

Expected: parser rejects `watchlist-checkup` and the wrapper is missing.

- [ ] **Step 3: Add the dedicated CLI wrapper**

Add:

```python
def watchlist_checkup(
    *,
    watchlist_path: Path,
    report_dir: Path,
    state_file: Path,
    report_date: str | None = None,
    period: str = "2y",
    push: bool = True,
) -> str:
    from lurker.application.watchlist_alert_state import AlertStateStore
    from lurker.application.watchlist_anomaly import run_watchlist_anomaly
    from lurker.config import load_watchlist

    resolved_date = report_date or date.today().isoformat()
    result = run_watchlist_anomaly(
        config=load_watchlist(watchlist_path),
        report_date=resolved_date,
        report_dir=report_dir,
        state_store=AlertStateStore(state_file),
        notifier=build_watchlist_notifier_from_env(),
        push=push,
        period=period,
    )
    return (
        f"Wrote watchlist anomaly report to {result.report_path} "
        f"(checked={result.checked_count}, alerts={result.new_alert_count}, "
        f"failures={result.failure_count}, pushed={result.pushed})"
    )
```

Add parser options:

```python
watchlist_cmd = subparsers.add_parser(
    "watchlist-checkup",
    help="独立运行自选股异常体检并使用 WATCHLIST_* 接收人",
)
watchlist_cmd.add_argument("--watchlist", type=Path, default=ROOT / "configs" / "watchlist.yaml")
watchlist_cmd.add_argument("--report-dir", type=Path, default=ROOT / "data" / "reports" / "watchlist")
watchlist_cmd.add_argument("--state-file", type=Path, default=ROOT / "data" / "processed" / "watchlist_alert_state.json")
watchlist_cmd.add_argument("--date", default=None)
watchlist_cmd.add_argument("--period", default="2y")
watchlist_cmd.add_argument("--no-push", action="store_true")
```

Dispatch it before the default demo branch:

```python
if args.command == "watchlist-checkup":
    print(
        watchlist_checkup(
            watchlist_path=args.watchlist,
            report_dir=args.report_dir,
            state_file=args.state_file,
            report_date=args.date,
            period=args.period,
            push=not args.no_push,
        )
    )
    return
```

- [ ] **Step 4: Write the operator documentation**

Create `docs/watchlist_anomaly.md` with this content:

````markdown
# 自选股异常体检

`watchlist-checkup` 独立检查 `configs/watchlist.yaml`，生成报告，并只向专属接收人推送。它不会进入 `daily-job`，也绝不读取日报使用的 `PUSHPLUS_TOKEN` 或 `EMAIL_TO`。

## 配置

```yaml
defaults:
  enabled_alerts:
    - abnormal_volume
    - peak_drawdown
    - chronic_underperformance
  volume_ratio: 3.0
  price_change:
    cn: 0.05
    hk: 0.05
    us: 0.10
  drawdown: 0.20
  underperformance_60d: 0.15
  cooldown_trading_days: 20
  worsening_step: 0.10

watchlist:
  - symbol: 300308.SZ
    market: cn
    name: 中际旭创
    overrides: {}
```

单个标的可以在 `overrides` 中覆盖阈值，或用 `enabled_alerts` 关闭某类检查。

## 检测口径

- 巨量异动：当天成交量除以前 20 个交易日平均成交量；当天不进入均值。放量至少 3 倍，同时中港股绝对涨跌至少 5%，或美股至少 10%。至少需要 21 个有效交易日。
- 高位回撤：当前复权收盘价相对最近 250 个交易日最高复权收盘价回撤至少 20%。至少需要 250 个有效交易日。
- 持续跑输：股票 60 日收益减去同市场基准 60 日收益不高于 -15%。股票和基准按共有日期对齐，至少需要 61 个共有交易日。

A 股基准为沪深 300，港股基准为恒生指数，美股基准为 SPY。数据不足时不报警，原因写入报告的数据质量区。

持续型报警首次越线立即通知，之后冷却 20 个交易日；冷却期内再恶化 10 个百分点会提前重报。恢复正常后再次越线视为新事件。巨量异动只按同一交易日去重。

## 独立通知变量

- `WATCHLIST_PUSHPLUS_TOKEN`
- `WATCHLIST_SMTP_HOST`
- `WATCHLIST_SMTP_PORT`
- `WATCHLIST_SMTP_USER`
- `WATCHLIST_SMTP_PASSWORD`
- `WATCHLIST_SMTP_FROM`
- `WATCHLIST_EMAIL_TO`
- `WATCHLIST_SMTP_USE_TLS`
- `WATCHLIST_SMTP_USE_SSL`

没有配置这些变量时只落盘，不推送。通知失败时不会写入“已通知”状态，下一次运行会重试。

## 运行

先执行不推送验收：

```bash
PYTHONPATH=src .venv/bin/lurker watchlist-checkup --date 2026-07-20 --no-push
```

正式运行：

```bash
PYTHONPATH=src .venv/bin/lurker watchlist-checkup
```

如果自选池跨市场，定时任务应安排在最后一个被观察市场收盘之后。例如工作日 07:30 执行，可覆盖前一交易日美股收盘：

```cron
30 7 * * 1-5 cd /absolute/path/to/lurker && PYTHONPATH=src .venv/bin/lurker watchlist-checkup
```

默认报告目录是 `data/reports/watchlist/`，默认状态文件是 `data/processed/watchlist_alert_state.json`。
````

Add this exact section to `README.md`:

````markdown
### Watchlist anomaly checkup

The watchlist checkup is scheduled and notified independently from the daily radar:

```bash
PYTHONPATH=src .venv/bin/lurker watchlist-checkup --no-push
```

See [`docs/watchlist_anomaly.md`](docs/watchlist_anomaly.md) for thresholds, dedicated `WATCHLIST_*` notification variables, state, and scheduling.
````

- [ ] **Step 5: Run focused and full verification**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_config.py tests/test_ingest.py tests/test_anomaly.py tests/test_watchlist_alert_state.py tests/test_watchlist_alerts_report.py tests/test_watchlist_anomaly.py tests/test_cli.py -q
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check src tests
```

Expected:

- all focused tests pass;
- the existing 157-test baseline plus the new tests passes with zero failures;
- Ruff reports `All checks passed!`.

- [ ] **Step 6: Run the no-push acceptance command**

```bash
PYTHONPATH=src .venv/bin/lurker watchlist-checkup --date 2026-07-20 --no-push
```

Expected: the command writes a report under `data/reports/watchlist/`, never sends a notification, and reports counts. If the live provider is unavailable, the generated report must list the failed sources and `pushed=False`; provider availability is not required for CI acceptance.

- [ ] **Step 7: Commit CLI and operator documentation**

```bash
git add src/lurker/cli.py tests/test_cli.py docs/watchlist_anomaly.md README.md
git commit -m "feat: expose independent watchlist checkup"
```

## Final phase gate

Before planning or implementing core ETF collection, verify every section 11.1 criterion in the design document against a named automated test. Record the no-push report path and full test/lint output in the implementation handoff. Do not enable real watchlist push until the user has inspected one generated report and configured at least one `WATCHLIST_*` recipient.
