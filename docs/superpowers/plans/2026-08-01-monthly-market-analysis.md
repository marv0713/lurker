# Monthly Market Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用确定性规则把宏观流动性月报升级为包含日、周、月交叉验证的市场分析月报，并保持现有月度快照、cron 和推送契约兼容。

**Architecture:** 从现有周报聚合逻辑提取不可变 `WeeklyFlowSummary`，让周报 Markdown 和月报共同消费结构化汇总；新增无 I/O 的月度市场分析器合成宏观与最新五日资金证据；月报渲染器只消费结构化分析。CLI 以报告月最后一个中国交易日为窗口上界，并继续用原宏观 `market_state` 决定是否推送。

**Tech Stack:** Python 3.11、dataclasses、pytest、Ruff、现有交易日历与通知基础设施。

---

## 文件职责

- `src/lurker/application/weekly_flow_report.py`：加载最近五日快照并构造 `WeeklyFlowSummary`；兼容渲染现有周报。
- `src/lurker/application/monthly_market_analysis.py`：纯规则合成最新层方向、市场立场、阶段、证据链和观察条件。
- `src/lurker/application/strategy_runner.py`：把预先构建的周度汇总传入月报策略。
- `src/lurker/reports/monthly_macro_flow_report.py`：在现有宏观事实章节之前渲染五个市场分析章节。
- `src/lurker/cli.py`、`src/lurker/cli_parser.py`、`src/lurker/cli_dispatch.py`：新增资金快照目录参数，以月末交易日为窗口上界组装月报。
- `tests/test_weekly_flow_report.py`：结构化汇总与周报兼容测试。
- `tests/test_monthly_market_analysis.py`：纯规则真值表与文案证据测试。
- `tests/test_monthly_macro_flow_report.py`：新增章节、降级和原始章节兼容测试。
- `tests/test_cli.py`、`tests/test_strategy_runner.py`：日期基准、参数传递与推送边界测试。

### Task 1: 提取结构化周度汇总

**Files:**
- Modify: `src/lurker/application/weekly_flow_report.py`
- Modify: `tests/test_weekly_flow_report.py`

- [ ] **Step 1: 写失败测试**

新增测试，构造 3–5 份快照并断言公开函数：

```python
from lurker.application.weekly_flow_report import build_weekly_flow_summary

summary = build_weekly_flow_summary(
    flow_snapshot_dir=flow_dir,
    report_date="2026-07-31",
    lookback_days=5,
    is_trading_day=lambda day: True,
)
assert summary.availability == "available"
assert summary.snapshot_count == 5
assert summary.main_net_inflow_sum == pytest.approx(-46_268_411_904.0)
assert summary.super_large_net_inflow_sum == pytest.approx(14_686_212_096.0)
assert summary.temperature_counts == {"进攻": 0, "观察": 3, "防守": 2}
```

同时覆盖 1–2 份为 `partial`、0 份为 `unavailable`、损坏 JSON 记入质量说明、月末之后的快照被排除，以及持续/新增/退潮板块列表。

- [ ] **Step 2: 运行测试确认 RED**

Run: `../../.venv/bin/python -m pytest tests/test_weekly_flow_report.py -q`

Expected: FAIL，原因是 `build_weekly_flow_summary` 尚不存在。

- [ ] **Step 3: 实现最小结构化边界**

在周度模块新增：

```python
@dataclass(frozen=True)
class WeeklyFlowSummary:
    availability: Literal["available", "partial", "unavailable"]
    start_date: str | None
    end_date: str | None
    snapshot_count: int
    temperature_counts: dict[str, int]
    main_net_inflow_sum: float
    super_large_net_inflow_sum: float
    latest_etf_status: str
    latest_margin_signal: str
    continued_sectors: tuple[str, ...]
    new_sectors: tuple[str, ...]
    ebb_sectors: tuple[str, ...]
    failure_count: int
    quality_notes: tuple[str, ...]
```

新增 `build_weekly_flow_summary(...)`，复用 `_load_latest_snapshots()`、`_status_counts()`、`_aggregate_named_flows()` 与 `prepare_temperature_inputs()`；最近一份快照的 ETF/两融状态必须经过时效判断。`build_weekly_flow_report()` 改为先构建汇总，再使用同一份已加载聚合结果渲染，保持现有 Markdown 内容和候选数量不变。

- [ ] **Step 4: 运行周报测试确认 GREEN**

Run: `../../.venv/bin/python -m pytest tests/test_weekly_flow_report.py -q`

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/lurker/application/weekly_flow_report.py tests/test_weekly_flow_report.py
git commit -m "refactor: expose weekly flow summary"
```

### Task 2: 实现纯规则月度市场分析

**Files:**
- Create: `src/lurker/application/monthly_market_analysis.py`
- Create: `tests/test_monthly_market_analysis.py`

- [ ] **Step 1: 写最新层四态失败测试**

```python
@pytest.mark.parametrize(
    ("etf", "margin", "expected"),
    [
        ("active", "unknown", "supportive"),
        ("unknown", "weakening", "weakening"),
        ("active", "weakening", "mixed"),
        ("unknown", "unknown", "unknown"),
    ],
)
def test_latest_direction_is_conflict_safe(etf, margin, expected):
    assert combine_latest_direction(etf, margin) == expected
```

- [ ] **Step 2: 运行确认 RED**

Run: `../../.venv/bin/python -m pytest tests/test_monthly_market_analysis.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现四态合成并确认 GREEN**

实现 ETF `active/inactive/unknown` 与两融 `supportive/weakening/unknown` 到 `supportive/weakening/mixed/unknown` 的唯一映射。`mixed` 和 `unknown` 都不提供方向确认。

- [ ] **Step 4: 写市场阶段真值表失败测试**

覆盖：杠杆过热优先、宏观或周度数据不足、三层同向增量确认、三层负向减量防守、宏观支持但高频负向仍战术防守、冲突/零值/最新层 mixed 均为存量结构，以及 2026-07 一负一正累计得到观察。

```python
result = analyze_monthly_market(monthly_analysis, weekly_summary)
assert result.stance == "观察"
assert result.market_stage == "存量结构"
assert result.main_contradiction == "场外资金已经松动，但机构承接和资金活化尚未确认。"
assert not set(result.supporting_evidence) & set(result.constraining_evidence)
```

- [ ] **Step 5: 运行确认 RED，再实现最小规则对象**

新增不可变 `MonthlyMarketAnalysis`，字段严格对应规格：立场、阶段、理由、支持/制约证据、主矛盾、日周月视图、板块结构、转强/转弱/失效条件和质量说明。所有文本使用有限模板，`healthy` 杠杆只作中性背景，`mixed/unknown` 不进入正负证据。

- [ ] **Step 6: 运行分析器测试确认 GREEN 并提交**

Run: `../../.venv/bin/python -m pytest tests/test_monthly_market_analysis.py -q`

```bash
git add src/lurker/application/monthly_market_analysis.py tests/test_monthly_market_analysis.py
git commit -m "feat: add rule based monthly market analysis"
```

### Task 3: 扩展月报渲染与策略边界

**Files:**
- Modify: `src/lurker/application/strategy_runner.py`
- Modify: `src/lurker/reports/monthly_macro_flow_report.py`
- Modify: `tests/test_strategy_runner.py`
- Modify: `tests/test_monthly_macro_flow_report.py`

- [ ] **Step 1: 写失败测试**

扩展 `StrategyContext`，允许传入 `weekly_flow_summary`。断言完整月报包含：

```python
for heading in (
    "## 本月市场判断",
    "## 资金证据链",
    "## 日周月交叉验证",
    "## 当前市场结构",
    "## 下月观察条件",
):
    assert heading in report.content_md
assert "本月立场：观察；市场处于存量结构。" in report.content_md
assert "## 居民存款趋势" in report.content_md
```

覆盖 `mixed` 显示“ETF 与两融分化”但不同时出现在支持/制约证据；过期状态统一显示“暂不判断”；`data_observation` 不产生方向性结论。

- [ ] **Step 2: 运行确认 RED**

Run: `../../.venv/bin/python -m pytest tests/test_strategy_runner.py tests/test_monthly_macro_flow_report.py -q`

Expected: FAIL，新增上下文和章节尚不存在。

- [ ] **Step 3: 实现报告集成**

`MonthlyMacroFlowStrategy.run()` 先执行现有宏观分析，再调用 `analyze_monthly_market()`；renderer 新增第三个参数并在原始宏观章节之前输出五个章节。`WeeklyFlowSummary` 不写回月度 snapshot，策略 metadata 同时保留 `analysis` 和 `market_analysis`。

- [ ] **Step 4: 运行确认 GREEN 并提交**

Run: `../../.venv/bin/python -m pytest tests/test_strategy_runner.py tests/test_monthly_macro_flow_report.py -q`

```bash
git add src/lurker/application/strategy_runner.py src/lurker/reports/monthly_macro_flow_report.py tests/test_strategy_runner.py tests/test_monthly_macro_flow_report.py
git commit -m "feat: render monthly market analysis"
```

### Task 4: CLI 月末窗口与兼容推送

**Files:**
- Modify: `src/lurker/cli.py`
- Modify: `src/lurker/cli_parser.py`
- Modify: `src/lurker/cli_dispatch.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

断言 parser 默认值为 `data/processed/flow_snapshots`，dispatch 传参；月报在 8 月补跑 7 月时以 7 月最后交易日调用周度汇总，排除 8 月快照；宏观 classified + 增强层数据不足仍发送，宏观 `data_observation` 仍跳过推送。

```python
assert args.flow_snapshot_dir.parts[-3:] == ("processed", "flow_snapshots")
assert captured_report_date == "2026-07-31"
assert "push=skipped(data_observation)" in message
```

- [ ] **Step 2: 运行确认 RED**

Run: `../../.venv/bin/python -m pytest tests/test_cli.py -k monthly_macro -q`

- [ ] **Step 3: 实现参数和组装**

新增 `--flow-snapshot-dir`；`monthly_macro_flow_job()` 无论是否启用 `--month-end-only` 都解析一次报告月最后交易日，构建 `WeeklyFlowSummary` 后放入 `StrategyContext`。保留 `analysis["market_state"] is None` 的既有推送门槛，不以增强层 stance 拦截。

- [ ] **Step 4: 运行确认 GREEN 并提交**

Run: `../../.venv/bin/python -m pytest tests/test_cli.py -k monthly_macro -q`

```bash
git add src/lurker/cli.py src/lurker/cli_parser.py src/lurker/cli_dispatch.py tests/test_cli.py
git commit -m "feat: wire monthly market context into cli"
```

### Task 5: 全量验证、合并、部署和重推

**Files:**
- Modify: `docs/superpowers/plans/2026-08-01-monthly-market-analysis.md`（勾选执行状态）

- [ ] **Step 1: 静态和全量验证**

Run:

```bash
../../.venv/bin/ruff check src tests
../../.venv/bin/python -m pytest -q
git diff --check main...HEAD
```

Expected: Ruff 无错误；pytest 全绿；diff check 无错误。

- [ ] **Step 2: 提交计划状态并审查分支**

```bash
git add docs/superpowers/plans/2026-08-01-monthly-market-analysis.md
git commit -m "docs: record monthly market analysis rollout"
git log --oneline main..HEAD
```

- [ ] **Step 3: 合并并推送 main**

在主检出确认只存在用户原有 `progress.md`、`task_plan.md` 修改后，以 `--ff-only` 合并功能分支并推送 `origin main`；不暂存或覆盖用户文件。

- [ ] **Step 4: 更新 VPS 并运行聚焦测试**

```bash
ssh root@64.186.233.134 'cd /root/lurker && git pull --ff-only origin main && .venv/bin/python -m pytest tests/test_weekly_flow_report.py tests/test_monthly_market_analysis.py tests/test_monthly_macro_flow_report.py -q'
```

Expected: VPS HEAD 与 origin/main 一致，聚焦测试全绿。

- [ ] **Step 5: 重跑并推送 2026-07 月报**

```bash
ssh root@64.186.233.134 'cd /root/lurker && set -a && . ./.env && set +a && PYTHONPATH=src .venv/bin/lurker monthly-macro-flow --month 2026-07'
```

Expected: 同月 snapshot/report 原子覆盖，输出 `push=sent`。

- [ ] **Step 6: 核验产物**

读取 VPS `data/reports/monthly_macro_flow/2026-07.md`，确认五个新增章节、`观察 / 存量结构`、一负一正周度累计、统一“暂不判断”措辞；核对通知命令返回成功，VPS 与 origin/main SHA 相同。
