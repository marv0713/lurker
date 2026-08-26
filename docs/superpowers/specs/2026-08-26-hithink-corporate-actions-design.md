# HiThink Corporate Actions Reliability Design

## Goal

Fix the three review findings in today's price and spring-trigger commits, then use HiThink as the preferred A-share ex-dividend and stock-bonus source while retaining the existing AkShare earnings-disclosure and rights-issue sources.

## Data-source composition

`CnCorporateActionProvider` remains the single normalized A-share provider. Its capabilities are composed per stock:

- earnings disclosure dates: existing AkShare disclosure calendar;
- cash-dividend and stock-bonus ex-dates: HiThink `/api/a-share/corporate-actions/adjustment-factors` when an API key is available and the request succeeds;
- dividend fallback: existing AkShare distribution detail when HiThink is unavailable or fails;
- rights issues: existing AkShare allotment source.

A successful HiThink response, including an empty `item` list, is authoritative for the requested 14-calendar-day window and does not call the dividend fallback. Authentication, permission, transport, rate-limit, malformed-response, and other business errors fall back to AkShare. Coverage is incomplete only when both dividend sources fail, or when the independent disclosure or rights-issue source fails.

HiThink records map to the existing `dividend` event type because the report contract treats both cash and stock distributions as dividend events. An event is emitted when `dividend_per_share` or `per_share_bonus` is positive, uses `ex_date_ms` as `primary_date`, and is marked `confirmed`. Zero/zero records are ignored because the published response schema cannot safely distinguish a rights issue.

## Review fixes

- Convert Tushare `amount` before sorting so every turnover value remains attached to its trade date.
- For a fired spring trigger, report the shrink streak ending immediately before the trigger bar, while keeping the latest-turnover field explicit.
- If HiThink price pagination reaches the safety cap with another non-empty page possible, raise an error so the caller falls back instead of accepting truncated history.

## Security and configuration

The API key remains external configuration in `HITHINK_FINANCE_API_KEY`. It is never written to repository files or included in errors. The supplied key is used only for a redacted live request during verification.

## Tests

Regression tests cover reverse-ordered Tushare rows, fired-trigger shrink reporting, pagination-cap failure, HiThink action normalization, successful empty responses, HiThink-to-AkShare fallback, merged disclosure/dividend/rights results, and incomplete coverage when both dividend sources fail. Existing offline tests remain network-independent.
