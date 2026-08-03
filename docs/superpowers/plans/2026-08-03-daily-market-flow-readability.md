# Daily Market Flow Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让日报市场温度区的大盘主力和超大单资金以带正负号的一位小数亿元显示。

**Architecture:** 只在 `professional_flow_daily._market_notes()` 的显示边界格式化两个原始元数；原始快照、市场温度判定和其他报告格式化函数保持不变。

**Tech Stack:** Python 3.11、pytest、Ruff。

---

### Task 1: 大盘资金展示格式化

**Files:**
- Modify: `src/lurker/application/professional_flow_daily.py:438-446`
- Modify: `tests/test_professional_flow_daily.py`

- [ ] **Step 1: 写失败测试**

在真实量级日报回归测试中断言：

```python
assert "大盘资金：主力净流入 +625.4亿元；超大单净流入 +699.9亿元" in report.content_md
assert "62535737344" not in report.content_md
assert "69993807872" not in report.content_md
```

新增负数与零值测试：

```python
notes = _market_notes(
    {"main_net_inflow": -41_870_618_624.0, "super_large_net_inflow": 0.0},
    {},
    "观察",
)
assert "大盘资金：主力净流入 -418.7亿元；超大单净流入 +0.0亿元" in notes
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `../../.venv/bin/python -m pytest tests/test_professional_flow_daily.py -q`

Expected: FAIL，当前输出为没有单位的原始元数。

- [ ] **Step 3: 最小实现**

在 `_market_notes()` 用本地格式化表达式替换原始 `:.0f`：

```python
main_inflow = _as_float(market_flow.get("main_net_inflow"))
super_large_inflow = _as_float(market_flow.get("super_large_net_inflow"))
notes.append(
    "大盘资金："
    f"主力净流入 {main_inflow / 100_000_000:+.1f}亿元；"
    f"超大单净流入 {super_large_inflow / 100_000_000:+.1f}亿元"
)
```

- [ ] **Step 4: 运行聚焦测试确认 GREEN**

Run:

```bash
../../.venv/bin/python -m pytest tests/test_professional_flow_daily.py -q
../../.venv/bin/ruff check src/lurker/application/professional_flow_daily.py tests/test_professional_flow_daily.py
```

Expected: 全部 PASS，Ruff 无错误。

- [ ] **Step 5: 提交**

```bash
git add src/lurker/application/professional_flow_daily.py tests/test_professional_flow_daily.py docs/superpowers/plans/2026-08-03-daily-market-flow-readability.md
git commit -m "fix: format daily market flow in billions"
```

### Task 2: 合并、部署和 2026-08-03 重推

**Files:**
- No source changes

- [ ] **Step 1: 全量验证**

Run:

```bash
../../.venv/bin/ruff check src tests
../../.venv/bin/python -m pytest -q
git diff --check main...HEAD
```

Expected: Ruff 无错误、pytest 全绿、diff check 无错误。

- [ ] **Step 2: 合并并推送**

在主检出执行 `git merge --ff-only codex/daily-flow-readability` 和 `git push origin main`；不暂存或修改用户的 `progress.md`、`task_plan.md`。

- [ ] **Step 3: 更新 VPS 并重推日报**

```bash
ssh root@64.186.233.134 'cd /root/lurker && git pull --ff-only origin main && .venv/bin/python -m pytest tests/test_professional_flow_daily.py -q'
ssh root@64.186.233.134 'cd /root/lurker && set -a && . ./.env && set +a && PYTHONPATH=src .venv/bin/lurker daily-job --markets cn --limit 5 --period 1y --windows 20,60,120,180 --date 2026-08-03'
```

Expected: 生成并推送同日降级日报；正文显示亿元格式，仍披露 `stock_flows` 数据质量失败。
