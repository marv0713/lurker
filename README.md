# lurker

Local trend discovery radar for self-use investment research.

The first MVP scans seeded A-share, US, and HK universes, detects stock and sector anomalies, performs bounded AI attribution, and generates a daily Markdown report.

## Setup

Create a virtual environment and install the project in editable mode:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Run the test suite:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
```

## Local Secrets

No API keys or local run outputs are committed to git.

- Gemini: set `GEMINI_API_KEY`, pass `--api-key`, or place a local `key` file in the project root.
- Tushare: set `TUSHARE_TOKEN` when available. If it is not set, A-share prices fall back to AkShare/Eastmoney and then BaoStock.
- PushPlus: set `PUSHPLUS_TOKEN` before wiring the push adapter into a scheduled job.

The local `key` file, `.env`, generated snapshots, and generated reports are ignored by `.gitignore`.

## MVP scope

- A-share core index and theme ETF constituents as the main discovery market
- US and HK curated pools for anchor validation and cross-market mapping
- Stock strength, double-bagger, sector breadth, and candidate ranking
- AI attribution only after deterministic rules trigger a candidate
- Daily report with main candidates, secondary leads, and watchlist changes

## Run demo report

```bash
.venv/bin/lurker
```

## Run live data snapshot

Refresh the resolved research universe when `configs/themes.yaml` changes, or on a weekly/monthly cadence:

```bash
.venv/bin/lurker resolve-seeds
```

Fetch a small live seed snapshot. The command prefers `data/processed/resolved_seed_pool.json` when it exists, and falls back to resolving `configs/themes.yaml` directly:

```bash
.venv/bin/lurker data-snapshot --markets cn,us,hk --limit 1 --period 6mo --windows 20,60,120
```

A-share seed symbols, supported seed indexes, and mapped theme ETFs use `akshare`; US and HK seed symbols use `yfinance`. A-share `seed_indexes` currently supports core indexes such as 沪深 300, 中证 1000, 科创 50, and 创业板指. A-share `seed_etfs` currently resolves mapped ETF heavy holdings such as 通信 ETF、人工智能 ETF、创新药 ETF、and 生物医药 ETF into stock symbols for the resolved universe.

A-share daily price fetching is stability-first, not speed-first. The default CN fetcher tries `Tushare -> AkShare/Eastmoney -> BaoStock`. Set `TUSHARE_TOKEN` when available; without it, the job skips Tushare and still falls back through AkShare and BaoStock.

This is a medium/long-term trend research workflow, not a daily trading signal loop. The resolved universe is meant to stay stable between refreshes so reports remain traceable to a specific research universe.

Refresh local price snapshots into files:

```bash
.venv/bin/lurker refresh-prices --markets cn,us,hk --limit 5 --period 6mo --windows 20,60,120
```

Price snapshots are stored under `data/processed/price_snapshots/YYYY-MM-DD.json`. The application layer uses a `PriceSnapshotStore` boundary; the first implementation is file-backed so a later SQLite/Postgres store can replace it without changing the snapshot workflow.

Generate the daily report from local snapshots. If `--api-key` and `GEMINI_API_KEY` are not set, the CLI reads a local `key` file in the project root and uses Gemini's OpenAI-compatible endpoint with `gemini-2.5-flash` by default:

```bash
.venv/bin/lurker run-daily --signal-threshold 0 --main-limit 10
```

The `key` file is local-only and ignored by git.

Run the full local daily loop, refreshing price snapshots and writing the Markdown report to `data/reports/YYYY-MM-DD.md`:

```bash
.venv/bin/lurker daily-job --markets cn,us,hk --limit 5 --period 6mo --windows 20,60,120
```

## Local schedule

For local self-use, run the daily job with cron after market data is available:

```bash
0 22 * * 1-5 cd /Users/marv/Documents/lurker && .venv/bin/lurker daily-job --markets cn,us,hk --limit 5 --period 6mo --windows 20,60,120
```

PushPlus can be enabled by setting `PUSHPLUS_TOKEN` and calling the push adapter from the final pipeline step.

## Architecture

The project uses a lightweight domain-oriented layout:

- `domain/` keeps pure trend-radar language and policies such as candidate scoring, visibility tiers, signal rules, and attribution scoring.
- `application/` coordinates use cases such as candidate ranking.
- `ingest/`, `storage/`, `ai/`, and `reports/` contain external adapters and presentation concerns.

Legacy functional entry points such as `signals/`, `scoring/`, and `pipeline.py` remain as thin compatibility modules while the domain layer stabilizes.
