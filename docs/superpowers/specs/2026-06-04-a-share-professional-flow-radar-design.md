# A-Share Professional Flow Radar Design

Date: 2026-06-04

## Goal

Replace the default daily Lurker report with an A-share-only professional flow radar inspired by Xu Zhuoxun's operating framework. The report should stop behaving like a broad momentum screener and instead answer:

- Is A-share market liquidity supportive today?
- Which sectors are receiving real money?
- Which individual stocks have both trend strength and fund-flow confirmation?
- Which candidates deserve "2% target" watch status?
- Which signals are overheated, contradicted, or not actionable yet?

Hong Kong and US markets are out of scope for this phase.

## Non-Goals

- Do not keep the old candidate scoring as the default decision mechanism.
- Do not add HK/US flow data in this phase.
- Do not require ETF creation/redemption, index-futures basis, or options IV in the first implementation.
- Do not provide buy/sell instructions for specific stocks. The report is a research radar, not trading advice.

## Operating Model

The new default daily report uses four layers:

1. Market flow temperature
2. Sector flow leadership
3. Individual stock flow confirmation
4. Setup and invalidation review

The old `long_term_trend` strategy can remain available as a legacy strategy, but it should not be the default daily push once the new radar is implemented.

## Data Sources

### Required

Use A-share data only:

- AkShare market fund flow: broad market main/large/small order flow.
- AkShare sector fund flow: industry and concept money-flow rankings.
- AkShare individual stock fund flow: 1-day, 5-day, and 10-day main money flow where available.
- Existing price snapshots: 20D/60D/120D/180D returns, latest close, volume when available.
- Core A-share ETF price/turnover proxies: at minimum 510300, 510500, 159915, and configurable A500 ETFs.

### Optional

Use Tushare only when `TUSHARE_TOKEN` is configured:

- Margin summary via `margin`: financing balance, securities lending balance, and total margin balance.
- Margin detail via `margin_detail` can be added later if needed.

If optional data fails or is unavailable, the report should degrade gracefully and explicitly show the missing data in the risk/data-quality section.

## New Artifacts

### Flow Snapshot

Add a new snapshot type stored under:

`data/processed/flow_snapshots/YYYY-MM-DD.json`

Suggested schema:

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-04T00:00:00+00:00",
  "market": "cn",
  "market_flow": {},
  "sector_flows": [],
  "stock_flows": [],
  "margin": {},
  "core_etfs": [],
  "failures": []
}
```

The flow snapshot should be independent from the existing price snapshot so the pipeline can debug data-source failures separately.

### Professional Report

Add a new report path through a strategy named:

`professional_flow_daily`

The report title should be:

`职业资金雷达日报`

## Scoring

The new score is independent from the old `stock_score + sector_score + ai_score` formula.

Suggested candidate score:

```text
total = market_regime_adjustment
      + sector_flow_score * 0.30
      + stock_flow_score * 0.35
      + trend_score * 0.20
      + setup_score * 0.15
```

### Market Temperature

Classify the market as:

- `进攻`: broad market flow positive, core ETFs active, margin not overheated.
- `观察`: mixed flow or incomplete confirmation.
- `防守`: market flow negative, broad weakness, or margin stress/overheating.

Market temperature should affect visibility:

- `进攻`: main candidates can be promoted normally.
- `观察`: candidates require stronger stock-flow confirmation.
- `防守`: candidates are downgraded to watch unless exceptionally strong.

### Sector Flow Score

Signals:

- Sector ranked near the top by main money inflow.
- Sector has positive 5-day or 10-day flow, if available.
- Multiple stocks in the same sector show flow confirmation.
- Sector trend is not contradicted by price weakness.

Labels:

- `主线`
- `扩散`
- `分化`
- `退潮`

### Stock Flow Score

Signals:

- Individual stock main money net inflow.
- Large/super-large order inflow when available.
- 5-day or 10-day fund-flow persistence.
- Stock appears in fund-flow ranking.
- Flow confirmation agrees with sector leadership.

Contradictions:

- Strong price but net outflow: `强势未获资金确认`
- Net inflow but weak trend: `资金试探`
- Sector strong but stock weak: `跟风不足`

### Trend Score

Signals from existing price data:

- 20D and 60D return percentile within A-share universe.
- 120D/180D positive trend.
- Relative strength versus the candidate pool.
- Controlled drawdown after a strong move.

### Setup Score

First phase uses approximations from daily price data:

- Pullback after prior strength.
- Price stabilizes after pullback.
- Recent return turns positive.
- Stop/invalidation distance is reasonable.

Later phases can add true MA20/MA40 support, turnover contraction, and first-positive-candle detection using full OHLCV history.

## "2% Target" Classification

A stock can be labeled `2%候选` only when all are true:

- It belongs to a sector with strong flow leadership.
- It has individual fund-flow confirmation.
- It is in the top tier of trend strength in the A-share candidate pool.
- It is among the leaders in its mapped theme/sector.
- There is no major contradiction such as strong net outflow or broken trend.

This label should be rare. If too many names qualify, only the highest-confidence names appear in the main section and the rest go to watch.

## Report Format

Daily report sections:

```text
# 职业资金雷达日报

日期：YYYY-MM-DD

## 一句话结论

## 市场资金温度

## 今日资金主线

## 2%候选

## 弹簧买点观察

## 证伪/退潮提醒

## 数据质量
```

## Email Push

Add email as a first-class notification provider.

### Notifier Changes

- Keep the existing `Notifier` protocol.
- Add `EmailNotifier`.
- Add `CompositeNotifier` to send through multiple providers.
- Keep PushPlus as optional.

### Configuration

Support environment variables:

```text
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
SMTP_FROM
EMAIL_TO
```

`EMAIL_TO` may contain one or more comma-separated recipients.

Optional variables:

```text
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

### Email Rendering

Send both:

- Plain text Markdown body.
- HTML body converted from Markdown.

The project already depends on `markdown-it-py`, so the implementation can reuse that dependency instead of adding a new renderer.

## CLI Behavior

Default daily job should eventually run:

```text
professional_flow_daily
```

Instead of the old long-term trend strategy.

Add or reuse commands so these can be run independently:

- Refresh price snapshots.
- Refresh flow snapshots.
- Generate professional flow report from local snapshots.
- Run daily job and send enabled notifications.

## Error Handling

- Any data-source failure is collected in `flow_snapshot.failures`.
- Missing optional Tushare data does not fail the whole job.
- Missing required AkShare flow data should not crash report generation if cached flow snapshots exist.
- The report must show data gaps in `数据质量`.
- Notification failures should not delete or prevent local report files from being written.

## Tests

Add focused tests for:

- Flow snapshot normalization.
- Flow scoring and market temperature classification.
- `2%候选` classification.
- Report rendering with complete data.
- Report rendering with missing optional margin data.
- Email notifier message construction using a fake SMTP client or monkeypatch.
- Composite notifier calls multiple providers and reports failures without hiding local output.

## Rollout

Phase 1:

- Add email and composite notification.
- Add flow snapshot fetch/store/load.
- Add professional flow scoring and report rendering.
- Make `professional_flow_daily` available but still manually selectable.

Phase 2:

- Switch default daily job to `professional_flow_daily`.
- Keep legacy trend report selectable.

Phase 3:

- Add ETF creation/redemption, index-futures basis, and options IV if reliable sources are confirmed.
