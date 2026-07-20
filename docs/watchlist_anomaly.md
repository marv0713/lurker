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

没有配置这些变量时只落盘，不推送。只配置部分邮件变量、或 `WATCHLIST_EMAIL_TO` 解析后没有有效地址时会立即报错，避免把未实际送达的报警标成“已通知”。通知失败时不会写入“已通知”状态，下一次运行会重试。

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

`--date` 同时是报告日期和行情截止日期，晚于该日期的行情不会参与检测。同一日期重复运行时，新的运行结果会追加到当日报告并带运行时间；原始报警和后续静默、降级结果都会保留。推送正文只包含本次运行的新报警。
