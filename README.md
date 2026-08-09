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
- Daily report with main candidates, secondary leads, low-score watch samples, and watchlist changes

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
.venv/bin/lurker run-daily --signal-threshold 0 --main-limit 10 --low-score-watch-limit 5
```

The `key` file is local-only and ignored by git.

To hide symbols you no longer want to see in the report, add them to `configs/suppressed_symbols.yaml`:

```yaml
symbols:
  - 300308.SZ
```

The daily report will remove those symbols from main candidates, secondary leads, and low-score watch samples, while noting that local suppression was applied.

Run the full local daily loop, refreshing price snapshots and writing the Markdown report to `data/reports/YYYY-MM-DD.md` plus a structured observation history at `data/reports/YYYY-MM-DD.candidates.json`:

```bash
.venv/bin/lurker daily-job --markets cn,us,hk --limit 5 --period 6mo --windows 20,60,120 --low-score-watch-limit 5 --suppressed-symbols configs/suppressed_symbols.yaml
```

When the signal threshold is lowered, weak early clues that still fail candidate ranking are shown under `低分观察样本`. They are for manual review and later duplicate-control history, not buy recommendations.

`daily-job` also updates `data/reports/index.json`, which is a lightweight archive index for daily review.

List recent archived reports:

```bash
.venv/bin/lurker list-reports --limit 10
```

## Strategies

Daily report generation now goes through `configs/strategies.yaml` when using the CLI. The first implemented strategy is `long_term_trend`; short-term setup, exit alerts, and deep research are registered as disabled placeholders so they can be added without rewriting the daily pipeline.

Run only selected strategies:

```bash
.venv/bin/lurker run-daily --strategies long_term_trend --cadence daily
```

Ignore cadence and run selected strategies directly:

```bash
.venv/bin/lurker run-daily --strategies long_term_trend --cadence all
```

### Watchlist anomaly checkup

The watchlist checkup is scheduled and notified independently from the daily radar:

```bash
PYTHONPATH=src .venv/bin/lurker watchlist-checkup --no-push
```

See [`docs/watchlist_anomaly.md`](docs/watchlist_anomaly.md) for thresholds, dedicated `WATCHLIST_*` notification variables, state, and scheduling.

### Personal close report

`personal-close-report` is a separate, personal-use close report for A-share and Hong Kong holdings and watch items. It does not feed the daily, weekly, monthly, or watchlist-anomaly reports. Every invocation reloads `configs/personal_watch.yaml`, and every generated report includes every configured stock name and symbol in YAML order.

Configure the scope like this:

```yaml
defaults:
  hk_experimental_spring:
    min_avg_turnover_hkd_20d: 10000000
    min_positive_volume_ratio_60d: 0.95

holdings:
  - symbol: 300308.SZ
    market: cn
    name: 中际旭创

watchlist:
  - symbol: 00700.HK
    market: hk
    name: 腾讯控股
```

Only `cn` and `hk` are supported in v1. Hong Kong symbols may use one to five digits before `.HK` (for example, `700.HK` or `00700.HK`) and are normalized for each data provider. A stock cannot appear twice across the two groups, and `name` is required. Edit this file at any time; the next run reads the new scope without restarting a service.

Run locally without notification:

```bash
PYTHONPATH=src .venv/bin/lurker personal-close-report \
  --config configs/personal_watch.yaml \
  --period 2y \
  --no-push
```

Reports overwrite the current complete view for that date at `data/reports/personal_close/YYYY-MM-DD.md`. At least one configured market must be open; when both configured markets are closed, the command skips cleanly and writes nothing. A stock from a closed market still appears when the other market is open, using its latest price on or before the report date and an explicit closed-market note.

The report starts with a one-line conclusion, then shows all holdings, all watch items, and data-quality notes. It includes adjusted MA5/20/200 direction, the formal A-share `ma20-v1` spring state, the separately labelled experimental HK spring state, and corporate actions in the inclusive 14-calendar-day window `[report date, report date + 13 days]`. An unknown spring result is included in the data-quality summary. Event types that a provider does not support are listed once per market as a capability boundary; a fetch failure is separately marked as incomplete coverage. Neither case is presented as proof that no event exists.

Personal notification settings use only these nine variables; they never fall back to daily or watchlist credentials:

- `PERSONAL_PUSHPLUS_TOKEN`
- `PERSONAL_SMTP_HOST`
- `PERSONAL_SMTP_PORT` (default `587`)
- `PERSONAL_SMTP_USER` (optional)
- `PERSONAL_SMTP_PASSWORD` (optional)
- `PERSONAL_SMTP_FROM`
- `PERSONAL_EMAIL_TO` (comma-separated)
- `PERSONAL_SMTP_USE_TLS` (default enabled)
- `PERSONAL_SMTP_USE_SSL` (default disabled)

PushPlus and email may be used alone or together. If any personal email variable is present, `PERSONAL_SMTP_HOST`, `PERSONAL_SMTP_FROM`, and `PERSONAL_EMAIL_TO` must all be non-empty. With no personal channel configured, the command still writes the report and says `push=no_channel`. If prices fail for every configured stock, the diagnostic report is still written but notification and push-state updates are skipped.

The independent acceptance state is stored at `data/processed/personal_close_push_state.json`. A normal same-day rerun rebuilds the report but does not resend after all configured channels have accepted it. Use `--force-push` to resend today's rebuilt report. `--no-push` never changes state. An explicit historical `--date` is a read-only replay and cannot be combined with `--force-push`. V1 accepts only `--period 2y`.

## Local schedule

For local self-use, run the daily job with cron after market data is available:

```bash
0 22 * * 1-5 cd /Users/marv/Documents/lurker && .venv/bin/lurker daily-job --markets cn,us,hk --limit 5 --period 6mo --windows 20,60,120
```

PushPlus can be enabled by setting `PUSHPLUS_TOKEN` and calling the push adapter from the final pipeline step.

Run the personal report after both A-share and Hong Kong close data should be available:

```bash
30 18 * * 1-5 cd /Users/marv/Documents/lurker && PYTHONPATH=src .venv/bin/lurker personal-close-report
```

Cron wakes the command Monday through Friday, but the XSHG/XHKG exchange calendars decide whether a report is due and suppress weekends and exchange holidays.

## Architecture

The project uses a lightweight domain-oriented layout:

- `domain/` keeps pure trend-radar language and policies such as candidate scoring, visibility tiers, signal rules, and attribution scoring.
- `application/` coordinates use cases such as candidate ranking.
- `ingest/`, `storage/`, `ai/`, and `reports/` contain external adapters and presentation concerns.

Legacy functional entry points such as `signals/`, `scoring/`, and `pipeline.py` remain as thin compatibility modules while the domain layer stabilizes.
