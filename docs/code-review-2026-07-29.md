# Lurker 代码审查报告

日期：2026-07-29

## 一、审查范围

对照 `docs/` 下所有设计文档（5 篇核心设计 + 20 篇规划/规格文档），审查提交 `b51a93f` 的 `src/lurker/` 全部源码（64 个 Python 文件，其中 53 个非 `__init__.py` 文件，共 11,561 行）和 `tests/` 全部测试（34 个 Python 文件，共 10,429 行）。文件数按 `git ls-tree -r --name-only` 中的 `*.py` 统计，行数按对应提交逐文件 `wc -l` 汇总；后续修复导致的文件数和行数变化不属于本审查快照。

---

## 二、已实现功能

### 完整实现

| 功能 | 来源文档 |
|------|---------|
| MVP 项目骨架（Task 1-12） | `2026-05-17-trend-radar-mvp.md` |
| 严格配置校验（themes / markets / scoring / watchlist / macro / core_etfs） | MVP Task 2 |
| SQLite 存储（7 表 + ORM） | MVP Task 3 |
| 种子池构造 + 市场过滤器 + ETF 成分股展开 | MVP Task 4 |
| 股票强度信号 + 翻倍股分类 | MVP Task 5 |
| 板块广度 + 候选评分 | MVP Task 6 |
| AI 归因（Gemini + Stub fallback） | MVP Task 7 |
| Markdown 日报渲染 | MVP Task 8 |
| CLI 管道（10+ 命令） | MVP Task 9-11 |
| PushPlus + Email 通知推送 | MVP Task 12 |
| A 股职业资金雷达日报（默认策略） | `professional_flow_radar.md` |
| 资金流快照（多周期合并、两融变化差值） | 同上 |
| 市场温度三态（进攻/防守/观察） | 同上 + 温度修复 spec |
| 弹簧买点观察（有筛选门槛） | 同上 |
| 资金流周报（板块/个股聚合） | 同上 |
| 自选股异常体检（独立命令 + 独立通知） | `watchlist_anomaly.md` |
| 3 类异常检测器（巨量异动/高位回撤/持续跑输） | 同上 |
| 警报去重/冷却状态机（原子写） | 同上 |
| 通知隔离（WATCHLIST_* 变量不读日报变量） | 同上 |
| 月度宏观流动性（央行存款/M1-M2/两融/流通市值） | `monthly-macro-flow` plan |
| PBOC 信贷收支表解析（HTML/XLS/XLSX） | 同上 |
| 温度上线门控（指纹校验 + 人工审批） | 温度修复 spec/plan |
| 日报降级推送（SUCCESS/DEGRADED/FAILED） | `daily-report-delivery-degradation` plan |
| 面向用户的数据质量标签 | `customer-facing-data-quality-labels` plan |
| 交易日历（exchange_calendars + JSON 缓存） | Legacy 收口 plan |
| 核心 ETF 采集（含 Sinai fallback） | ETF 市场温度 plan |
| 60 天温度快照回放 + 审批 | 温度修复 plan |

### 部分实现

| 功能 | 差距 |
|------|------|
| `scoring.yaml` 中 `ai_attribution.weights`（6 维度） | 代码 `domain/attribution.py` 使用另一套 5 因子评分，未读配置 |
| `ai/schemas.py` 的 `AIAttributionResult` Pydantic 模型 | 定义了但 attributor 用自己的 `_valid_*` 集合做校验，两套逻辑并存 |
| 宏观资金周报 | 当前 `weekly_flow_report.py` 是资金流周报，缺少 spec 设计的 ETF 净申购、两融周度状态、超大单+指数对比 |
| `long_term_trend` 旧策略 | 已 deprecated，代码中有"Domain-only extension points"但配置未启用对应维度 |

### 未实现（文档明确标记）

| 功能 | 说明 |
|------|------|
| `short_term_setup` 策略 | `strategies.yaml` 中定义但 `strategy_runner.py` 未注册实现 |
| `exit_alert` 策略 | 同上 |
| `deep_research` 策略 | 同上 |
| 港股壳属性/频繁资本运作识别 | 设计标注"不在本轮实现" |
| 海外社交媒体情绪 | `design_discussions.md` 明确放弃 |
| 美股/港股完整职业资金接入 | `professional_flow_radar.md` 明确"暂不接入" |

---

## 三、代码质量

### 优点

1. **分层清晰**：`ingest → domain → application → reports`，domain 层零 I/O，可测试性强。
2. **测试量大**：约 10,429 行测试代码，源码比约 0.9:1，核心逻辑覆盖充分。
3. **配置校验严格**：`_reject_unknown_fields()` 模式防止配置漂移；PBOC URL 做 https + allowed_hosts 校验。
4. **数据质量多层降级**：stale → unknown，缺失不补零，失败追踪到快照，温度门控。
5. **原子写入**：状态文件、快照用 `mkstemp` + `os.replace`，防进程中断。
6. **通知隔离**：watchlist 专属 `WATCHLIST_*` 变量，不读日报变量。
7. **Ruff lint 零警告**。
8. **降级设计**：日报 SUCCESS/DEGRADED/FAILED 三态，降级时仍推送但标注。

---

## 四、问题清单

### ⚪ 核验更正 — 原 P0 结论不成立

#### 1. `tests` 命名空间包存在环境兼容风险，但未阻塞测试

**文件**：`tests/test_monthly_macro_flow_report.py:7`

```python
from tests.test_monthly_macro_flow import complete_snapshot
```

原审查认为 `tests/` 缺少 `__init__.py` 会导致 pytest collection 失败：

```
ImportError: No module named 'tests'
```

**2026-07-29 核验更正**：该结论不成立。基线仓库执行
`PYTHONPATH=src .venv/bin/python -m pytest -q` 得到 `468 passed`；当前环境把本仓库的 `tests/` 解析为命名空间包，跨测试模块导入可以工作。CI 并未因此失去回归能力，详见根目录 `findings.md`。

真正存在的是低优先级环境兼容风险：若 Python 环境中安装了同名常规 `tests` 包，本仓库命名空间包可能被遮蔽。后续修复添加 `tests/__init__.py` 作为防御性硬化，不是 P0 故障修复。

---

### 🟡 P1 — 高优先级

#### 2. AI 归因评分权重配置未被使用

**文件**：`configs/scoring.yaml`

```yaml
ai_attribution:
  weights:
    reason_clarity: 20
    industry_level: 20
    news_consistency: 15
    hard_evidence: 25
    risk_identification: 10
    counter_evidence: 10
```

**实际**：`domain/attribution.py` 的 `score_ai_attribution()` 使用硬编码的 5 因子逻辑，不读取这个配置段。

**影响**：调整配置无效果，维护者误以为可以调参。

#### 3. Pydantic Schema 定义但未使用

**文件**：`ai/schemas.py`

定义了 `AIAttributionResult(BaseModel)`，但 `ai/attributor.py` 的 attributor 使用自己的 `_valid_*` 集合做校验。两套校验逻辑并存。

**影响**：修改 attributor 时容易只改一处，导致两套逻辑分歧。

#### 4. 三个占位策略无实现但有配置

**文件**：`configs/strategies.yaml`

`short_term_setup`、`exit_alert`、`deep_research` 在配置中定义，`strategy_runner.py` 的 `DEFAULT_STRATEGIES` 未注册。运行时产生"策略尚未实现"但用户可能已在配置中启用。

**影响**：用户误认为这些策略可用。

---

### 🟢 P2 — 中优先级

#### 5. `pipeline.py` 几乎为空

**文件**：`src/lurker/pipeline.py`

MVP 计划中的核心模块，现在只剩薄代理，实际逻辑在 `application/` 中。

**影响**：代码导航时误导，增加不必要的间接层。

#### 6. 旧策略死代码未清理

**文件**：`domain/signals.py`

`long_term_trend` 策略中标注为"Domain-only extension points"的维度（`near_52w_high`、`relative_market_strength`、`relative_sector_strength`、`turnover_expansion`）在代码中存在但配置中未启用。

**影响**：增加了阅读负担和维护复杂度。

#### 7. 部分 `reports/` 渲染器缺少直接单元测试

- `daily_report.py`
- `trend_card.py`

基线提交中，`professional_flow_report.py` 与 `monthly_macro_flow_report.py` 已有直接测试；`daily_report.py` 只有高层集成测试间接覆盖，`trend_card.py` 缺少直接测试。后续修复已为两者补充直接测试。

---

### 🔵 P3 — 低优先级

#### 8. 中英文注释混用

- docstrings 和报告以中文为主
- 部分 ingest 模块注释为英文
- 变量命名以英文为主

建议统一风格。

#### 9. CLI 命令分发函数过长

基线提交中的 `cli.py` 文件共 2,289 行，但 `main()` 实际约 214 行。真正的问题是解析、调度和多个命令处理器集中在同一模块，而不是单个 `main()` 函数有 2,289 行。后续修复已提取 `cli_parser.py` 与 `cli_dispatch.py`。

---

## 五、修复建议

### 原 P0 — 核验后降级为防御性硬化

#### 修复 1：稳定 `tests` 包解析

核验确认现有导入可运行，不需要把它改成顶层模块导入。实际采用的兼容优先方案是在 `tests/` 下添加 `__init__.py`，避免本仓库命名空间包被环境里的同名常规包遮蔽。

**验证**：
```bash
PYTHONPATH=src .venv/bin/pytest -q
```

---

### P1 — 短期修复（1-2 周）

#### 修复 2：清理或实现 AI 权重配置

**方案 A**（推荐）：从 `scoring.yaml` 中删除 `ai_attribution.weights` 段。当前硬编码逻辑清晰，不需要配置化。

**方案 B**：让 `score_ai_attribution()` 读取配置权重并应用。工作量较大，收益有限。

#### 修复 3：统一 AI 校验逻辑

**方案 A**（推荐）：删除 `ai/schemas.py` 中的 Pydantic 模型定义，仅保留 attributor 内部的校验逻辑。

**方案 B**：让 attributor 使用 Pydantic 模型做校验，移除 `_valid_*` 集合。

#### 修复 4：占位策略增加明确限制

在 `configs/strategies.yaml` 中为未实现策略添加 `limitations`：

```yaml
- name: short_term_setup
  lifecycle: active
  enabled: false
  cadence: daily
  limitations:
    - "尚未实现"
```

同时在 `strategy_runner.py` 中增加检查：未注册但 `enabled: true` 的策略在启动时 warning 而非静默生成占位结果。

### P2 — 中期修复（1-3 个月）

#### 修复 6：清理 `pipeline.py`

评估是否还有调用方引用 `pipeline.py`，如无则删除；如有则内联到 `application/`。

#### 修复 7：清理旧策略扩展点

从 `domain/signals.py` 移除 `near_52w_high`、`relative_market_strength`、`relative_sector_strength`、`turnover_expansion` 相关的"Domain-only extension points"代码和注释。如需恢复，从 git history 找回即可。

#### 修复 8：为 `reports/` 添加快照测试

对每个渲染器至少添加：
- 空输入测试（无候选、无报警）
- 单条输入测试
- 多条输入测试
- 特殊字符测试（名称含括号、百分号等）

#### 修复 9：提取 CLI 命令注册

将 CLI 命令提取为注册表模式：

```python
COMMANDS: dict[str, Callable] = {
    "run-daily": run_daily_command,
    "daily-job": daily_job_command,
    "watchlist-checkup": watchlist_checkup_command,
    # ...
}
```

#### 修复 10：统一中英文注释

决定主要语言（建议中文——报告面向中文用户），逐步统一注释和 docstring。

---

## 六、总体评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ★★★★☆ | 分层清晰，domain 零 I/O 是可测试的设计 |
| 功能完成度 | ★★★★☆ | 核心双引擎全部实现，3 个占位策略明确标记未实现 |
| 代码质量 | ★★★★☆ | Ruff 零警告，原子写入、配置校验等细节到位 |
| 测试覆盖 | ★★★★☆ | 基线测试可完整运行；部分报告渲染器当时缺少直接测试，后续修复已补齐 |
| 文档一致性 | ★★★☆☆ | AI 权重配置和 Pydantic schema 与实现不一致 |
| 可维护性 | ★★★☆☆ | CLI 过长、死代码残留，但整体结构可理解 |

**核验后的核心结论**：系统功能完整、架构合理，测试套件可以运行；原 P0 为误报。真实的高优先级问题是配置契约与运行策略不一致，后续已采用兼容优先的定点修复。报告渲染器直接测试、CLI 模块拆分和旧评分扩展点清理也已在后续修复中完成。
