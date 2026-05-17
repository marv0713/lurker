# 进度日志

## 会话：2026-05-17

### 阶段 1：需求与发现
- **状态：** complete
- **开始时间：** 2026-05-17
- 执行的操作：
  - 阅读 `大趋势投资雷达系统需求文档.md`。
  - 与用户确认第一版先做自用研究工具，但保留产品化升级空间。
  - 与用户确认优先验证发现能力。
  - 与用户确认“个股强度 + 板块联动”混合触发。
  - 与用户确认采用行业/概念分类扩池。
  - 与用户确认 AI 早介入，采用新闻解释异动、公告/财报验证。
  - 与用户确认 AI 不能静默吞掉已触发信号，必须保留次级线索。
- 创建/修改的文件：
  - `docs/superpowers/specs/2026-05-17-trend-radar-design.md`

### 阶段 2：规划与结构
- **状态：** in_progress
- 执行的操作：
  - 将产品方向扩展成可落地设计稿。
  - 将设计稿拆成 MVP 实施任务清单。
  - 安装 GitHub skill `OthmanAdi/planning-with-files` 的中文版本 `planning-with-files-zh`。
  - 根据新技能约定创建文件化进度系统。
  - 使用 Homebrew 的 `/opt/homebrew/bin/python3.12` 创建项目虚拟环境 `.venv`。
  - 创建 `.gitignore`，忽略 `.venv`、缓存、运行数据和本地数据库。
- 创建/修改的文件：
  - `docs/superpowers/plans/2026-05-17-trend-radar-mvp.md`
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
  - `.gitignore`

### 阶段 3：项目骨架实现
- **状态：** complete
- 执行的操作：
  - 创建 `pyproject.toml`、`README.md`、`src/lurker` 包结构和 `data` 目录。
  - 使用 `.venv` 以 editable 模式安装项目和开发依赖。
  - 创建配置文件和配置加载器。
  - 创建 SQLite 存储模型和数据库初始化函数。
- 创建/修改的文件：
  - `pyproject.toml`
  - `README.md`
  - `configs/themes.yaml`
  - `configs/markets.yaml`
  - `configs/scoring.yaml`
  - `configs/push.yaml.example`
  - `src/lurker/config.py`
  - `src/lurker/storage/db.py`
  - `src/lurker/storage/models.py`
  - `tests/test_config.py`
  - `tests/test_storage.py`

### 阶段 4：信号、评分和候选流水线
- **状态：** complete
- 执行的操作：
  - 实现种子池构建。
  - 实现美股和港股基础过滤。
  - 实现窗口收益、翻倍股分类和个股强度分。
  - 实现板块联动分和候选总分/可见层级。
- 创建/修改的文件：
  - `src/lurker/universe/seed_pool.py`
  - `src/lurker/universe/filters.py`
  - `src/lurker/signals/double_baggers.py`
  - `src/lurker/signals/stock_strength.py`
  - `src/lurker/signals/sector_breadth.py`
  - `src/lurker/scoring/candidate_score.py`
  - `tests/test_universe.py`
  - `tests/test_signals.py`
  - `tests/test_scoring.py`

### 阶段 5：AI 归因、日报和推送
- **状态：** complete
- 执行的操作：
  - 实现 AI 归因结构化 schema、prompt 模板和确定性评分。
  - 实现趋势卡片和日报 Markdown 渲染。
  - 实现 CLI demo pipeline，运行 `lurker` 可输出模拟日报。
  - 实现 yfinance 行情 normalization 入口和配置驱动的成分股入口。
  - 实现候选排序 pipeline，保证主候选溢出进入次级线索。
  - 实现 PushPlus payload 和发送适配器。
- 创建/修改的文件：
  - `src/lurker/ai/schemas.py`
  - `src/lurker/ai/prompts.py`
  - `src/lurker/ai/attribution.py`
  - `src/lurker/reports/trend_card.py`
  - `src/lurker/reports/daily_report.py`
  - `src/lurker/reports/pushplus.py`
  - `src/lurker/ingest/prices.py`
  - `src/lurker/ingest/constituents.py`
  - `src/lurker/pipeline.py`
  - `src/lurker/cli.py`
  - `tests/test_ai_schema.py`
  - `tests/test_reports.py`
  - `tests/test_cli.py`
  - `tests/test_ingest.py`
  - `tests/test_pipeline.py`

### 阶段 6：测试与验证
- **状态：** complete
- 执行的操作：
  - 运行完整测试。
  - 运行完整 lint。
  - 运行本地 CLI 并确认日报包含主候选、次级线索、观察池变化和过热/证伪提醒。

### 阶段 7：轻量领域层重构
- **状态：** complete
- 执行的操作：
  - 新增 `domain/`，承载纯领域模型、信号规则、候选策略和归因评分。
  - 新增 `application/`，承载候选排序用例。
  - 将旧 `signals/`、`scoring/` 和 `pipeline.py` 保留为兼容入口，降低调用方迁移成本。
  - 增加 `tests/test_domain_architecture.py`，验证领域语言可直接使用，并约束领域层不依赖基础设施库。
  - 更新 README 说明轻量领域架构。
- 创建/修改的文件：
  - `src/lurker/domain/__init__.py`
  - `src/lurker/domain/models.py`
  - `src/lurker/domain/signals.py`
  - `src/lurker/domain/policies.py`
  - `src/lurker/domain/attribution.py`
  - `src/lurker/application/__init__.py`
  - `src/lurker/application/rank_candidates.py`
  - `src/lurker/signals/double_baggers.py`
  - `src/lurker/signals/sector_breadth.py`
  - `src/lurker/signals/stock_strength.py`
  - `src/lurker/scoring/candidate_score.py`
  - `src/lurker/pipeline.py`
  - `src/lurker/ai/attribution.py`
  - `tests/test_domain_architecture.py`
  - `README.md`

### 阶段 8：真实行情数据接入第一刀
- **状态：** complete
- 执行的操作：
  - 新增 `application.price_snapshot`，从主题种子池收集美股/港股价格快照。
  - 新增 `lurker data-snapshot` CLI 子命令，支持 `--markets`、`--limit`、`--period`、`--windows`。
  - 修复 yfinance 单标的也返回 MultiIndex 列时的 normalization 问题。
  - 增加港股 5 位代码适配：例如 `01801.HK` 查询时转换为 `1801.HK`，报告仍保留原始代码。
  - 用真实 yfinance 数据跑通 `us,hk` 小样本快照。
- 创建/修改的文件：
  - `src/lurker/application/price_snapshot.py`
  - `src/lurker/ingest/prices.py`
  - `src/lurker/cli.py`
  - `tests/test_price_snapshot.py`
  - `tests/test_ingest.py`
  - `tests/test_cli.py`
  - `README.md`

### 阶段 9：A 股行情接入和种子来源边界
- **状态：** complete
- 执行的操作：
  - 对照设计稿确认当前 YAML 示例与设计样例一致，但实际扫描只消费 `seed_symbols`，尚未展开 `seed_indexes` / `seed_etfs`。
  - 明确边界：`seed_symbols` 是可直接扫描股票代码；`seed_indexes` 是待展开指数来源；`seed_etfs` 是待展开 ETF 来源。
  - 新增 A 股 akshare 日线行情适配，将 `300308.SZ` / `688235.SH` 转为 akshare 查询代码。
  - 新增 A 股行情归一化，输出现有 `PRICE_COLUMNS`。
  - 新增市场级 fetcher 分派，`cn` 使用 akshare，`us` / `hk` 继续使用 yfinance。
  - 新增 `collect_seed_sources()` 和 `load_theme_seed_sources()`，为后续指数/ETF 成分股解析保留明确接口。
  - 将 `lurker data-snapshot` 默认市场改为 `cn,us,hk`。
  - 用真实 akshare 数据跑通 A 股小样本快照。
- 创建/修改的文件：
  - `src/lurker/ingest/prices.py`
  - `src/lurker/application/price_snapshot.py`
  - `src/lurker/universe/seed_pool.py`
  - `src/lurker/ingest/constituents.py`
  - `src/lurker/cli.py`
  - `tests/test_ingest.py`
  - `tests/test_price_snapshot.py`
  - `tests/test_universe.py`
  - `tests/test_cli.py`
  - `README.md`
  - `task_plan.md`
  - `progress.md`

### 阶段 10：A 股指数成分股展开
- **状态：** complete
- 执行的操作：
  - 新增 A 股指数名称映射，支持沪深 300、中证 1000、科创 50、创业板指/创业板核心指数。
  - 新增指数成分股归一化，兼容中证官网字段 `成分券代码` / `交易所` 和通用字段 `品种代码`。
  - 新增 `load_resolved_theme_seed_symbols()`，在加载主题种子池时展开 A 股指数成分股。
  - `lurker data-snapshot` 改为使用解析后的种子池。
  - 保持 `seed_etfs` 为明确但未展开的来源边界，留到下一阶段处理。
  - 为行情抓取增加单标的异常隔离，单个请求失败不会中断整批快照。
  - 空快照渲染为 `No available data`，避免误认为正常空标的。
  - 联网验证当前配置可解析为：`cn 151`、`us 6`、`hk 5`。
- 创建/修改的文件：
  - `src/lurker/ingest/constituents.py`
  - `src/lurker/application/price_snapshot.py`
  - `src/lurker/cli.py`
  - `tests/test_ingest.py`
  - `tests/test_price_snapshot.py`
  - `tests/test_cli.py`
  - `README.md`
  - `task_plan.md`
  - `progress.md`

### 阶段 11：Resolved Universe 持久化
- **状态：** complete
- 执行的操作：
  - 明确系统本质是中长期趋势发现和研究样本积累工具，不是按日生成交易信号的工具。
  - 调整开发方向：先持久化 resolved universe，让日常行情扫描复用稳定研究宇宙；ETF 成分股解析后续补充。
  - 设计刷新节奏：手动或定期运行 `resolve-seeds`，日常 `data-snapshot` 优先读取缓存。
  - 新增 resolved seed pool JSON 结构，记录生成时间、各市场 symbols 和 manual/indexes/etfs/unresolved 来源。
  - 新增 `lurker resolve-seeds` 命令，默认写入 `data/processed/resolved_seed_pool.json`。
  - `lurker data-snapshot` 新增缓存优先读取逻辑，缓存不存在时回退到实时解析。
  - 运行真实 `resolve-seeds`，生成 `cn=151, hk=5, us=6` 的缓存。
  - 用缓存运行 `data-snapshot --markets cn --limit 1`，命令不断流；当前 Eastmoney 行情接口返回空数据，输出 `No available data`。
- 创建/修改的文件：
  - `findings.md`
  - `task_plan.md`
  - `progress.md`
  - `README.md`
  - `src/lurker/universe/resolved_seed_pool.py`
  - `src/lurker/cli.py`
  - `tests/test_resolved_seed_pool.py`
  - `tests/test_cli.py`
  - `data/processed/resolved_seed_pool.json`

## 测试结果
| 测试 | 输入 | 预期结果 | 实际结果 | 状态 |
|------|------|---------|---------|------|
| 设计稿占位符检查 | `rg -n "TBD|TODO|待定"` | 无占位符 | 未发现占位符 | pass |
| MVP 计划占位符检查 | `rg -n "TBD|TODO|待定|fill in|implement later"` | 无占位符 | 未发现占位符 | pass |
| skill 安装验证 | 安装 `skills/planning-with-files-zh` | 安装到 Codex skills 目录 | 安装到 `/Users/marv/.codex/skills/planning-with-files-zh` | pass |
| Python 虚拟环境验证 | `.venv/bin/python --version` | Python 3.12 | Python 3.12.10 | pass |
| pip 验证 | `.venv/bin/python -m pip --version` | pip 可运行 | pip 25.1.1 | pass |
| 配置测试 | `.venv/bin/python -m pytest tests/test_config.py -q` | 通过 | 3 passed | pass |
| 存储测试 | `.venv/bin/python -m pytest tests/test_storage.py -q` | 通过 | 2 passed | pass |
| 股票池测试 | `.venv/bin/python -m pytest tests/test_universe.py -q` | 通过 | 3 passed | pass |
| 信号测试 | `.venv/bin/python -m pytest tests/test_signals.py -q` | 通过 | 3 passed | pass |
| 评分测试 | `.venv/bin/python -m pytest tests/test_scoring.py -q` | 通过 | 3 passed | pass |
| Ruff 检查 | `.venv/bin/python -m ruff check src tests` | 通过 | All checks passed | pass |
| AI schema 测试 | `.venv/bin/python -m pytest tests/test_ai_schema.py -q` | 通过 | 2 passed | pass |
| 报告测试 | `.venv/bin/python -m pytest tests/test_reports.py -q` | 通过 | 3 passed | pass |
| CLI 测试 | `.venv/bin/python -m pytest tests/test_cli.py -q` | 通过 | 1 passed | pass |
| ingest 测试 | `.venv/bin/python -m pytest tests/test_ingest.py -q` | 通过 | 1 passed | pass |
| pipeline 测试 | `.venv/bin/python -m pytest tests/test_pipeline.py -q` | 通过 | 1 passed | pass |
| 全量测试 | `.venv/bin/python -m pytest -q` | 通过 | 22 passed | pass |
| 全量 Ruff | `.venv/bin/python -m ruff check .` | 通过 | All checks passed | pass |
| CLI 输出 | `.venv/bin/lurker` | 输出日报 Markdown | 包含主候选、次级线索、观察池变化、风险提醒 | pass |
| 领域架构测试 | `.venv/bin/python -m pytest tests/test_domain_architecture.py -q` | 通过 | 3 passed | pass |
| 领域重构相关测试 | `.venv/bin/python -m pytest tests/test_scoring.py tests/test_signals.py tests/test_ai_schema.py tests/test_pipeline.py tests/test_cli.py -q` | 通过 | 10 passed | pass |
| 真实数据快照测试 | `.venv/bin/lurker data-snapshot --markets us,hk --limit 1 --period 6mo --windows 20,60,120` | 输出真实行情快照 | 输出 `ANET` 和 `01801.HK` 收益表 | pass |
| A 股真实数据快照测试 | `.venv/bin/lurker data-snapshot --markets cn --limit 1 --period 6mo --windows 20,60,120` | 输出真实 A 股行情快照 | 输出 `300308.SZ` 收益表 | pass |
| A 股指数展开验证 | `load_resolved_theme_seed_symbols(Path("configs/themes.yaml"))` | 展开 A 股指数来源 | `cn 151`，前 4 个为手工锚点，后续为指数成分 | pass |
| Resolved universe 刷新 | `.venv/bin/lurker resolve-seeds` | 写入 resolved seed pool 缓存 | `cn=151, hk=5, us=6` | pass |
| 缓存优先快照 | `.venv/bin/lurker data-snapshot --markets cn --limit 1 --period 6mo --windows 20,60,120` | 使用缓存并输出快照表 | 命令成功，行情接口空数据时输出 `No available data` | pass |
| 行情快照文件刷新 | `.venv/bin/lurker refresh-prices --markets cn --limit 1 --period 6mo --windows 20,60,120 --date 2026-05-17` | 写入价格快照 JSON | 写入成功，`snapshots=0, failures=1` | pass |

### 阶段 12：本地行情快照文件存储
- **状态：** complete
- 执行的操作：
  - 确认第一版不上数据库，但保留行情快照存储抽象，方便后续切换 SQLite/Postgres。
  - 新增 `PriceSnapshotStore` 协议和 `FilePriceSnapshotStore` 文件实现。
  - 新增价格快照批次结构，记录 `generated_at`、`seed_pool_generated_at`、`markets`、`windows`、`snapshots` 和 `failures`。
  - 新增 `lurker refresh-prices` 命令，读取 resolved seed pool 后刷新本地行情快照文件。
  - `data-snapshot` 优先读取 `data/processed/price_snapshots` 中最近一次 JSON 快照，避免每次都实时请求上游行情。
  - 单标的上游失败会记录到 `failures`，不影响同批其他标的。
  - 运行真实 `refresh-prices --markets cn --limit 1`，写入 `data/processed/price_snapshots/2026-05-17.json`。
  - 本次 Eastmoney 行情请求失败被记录为 `failures=1`，随后 `data-snapshot` 从本地快照输出 `No available data`。
- 创建/修改的文件：
  - `src/lurker/application/price_snapshot.py`
  - `src/lurker/cli.py`
  - `tests/test_price_snapshot.py`
  - `tests/test_cli.py`
  - `README.md`
  - `task_plan.md`
  - `progress.md`
  - `findings.md`
  - `data/processed/price_snapshots/2026-05-17.json`

### 阶段 13：真实链路打通（快照→信号→归因→排序→日报）
- **状态：** complete
- 执行的操作：
  - 明确整条链路的缺口：快照已存文件，但信号计算、AI 归因、候选排序和日报渲染尚未串联。
  - 新增 `application/signal_scan.py`：从快照批量计算个股强度信号，分位数按市场独立计算，输出 `StockSignal` 列表。
  - 新增 `ai/attributor.py`：定义 `Attributor` Protocol；内置 `StubAttributor`（返回「证据不足型」，ai_score 固定 30），用于无 LLM Key 时跑通链路；后续替换真实 LLM 实现只需满足 Protocol，无需修改其他模块。
  - 新增 `application/run_daily.py`：完整每日 pipeline 用例，链路为「快照 → 信号 → 归因 → 候选排序 → 日报」；当时 sector_score 第一版固定占位 50，后续已在阶段 16 替换为动态板块联动分。
  - 新增 `lurker run-daily` CLI 子命令，支持 `--price-snapshots / --date / --signal-threshold / --main-limit`。
  - 修复 `run_daily` 中 failures 提醒在无信号时提前 return 导致丢失的 bug。
  - 全量 64 个测试通过，Lint 全绿。
  - 联网运行 `refresh-prices --markets us,hk --limit 3`（snapshots=6, failures=0），再运行 `run-daily`，日报正常输出。
- 创建/修改的文件：
  - `src/lurker/application/signal_scan.py`
  - `src/lurker/ai/attributor.py`
  - `src/lurker/application/run_daily.py`
  - `src/lurker/cli.py`
  - `tests/test_run_daily.py`
  - `progress.md`

### 阶段 14：接入真实 LLM 归因（Gemini OpenAI-compatible 接口）
- **状态：** complete
- 执行的操作：
  - 确定无需用假数据测试：通过降低 `signal-threshold=0`，真实快照数据自然会生成信号；只要 LLM 给出合理的归因和推荐等级，候选就会自动流向主候选（main）。
  - 在 `pyproject.toml` 中增加 `openai` SDK 依赖，支持以 OpenAI-compatible 的方式调用任意兼容模型接口。
  - 在 `ai/prompts.py` 中新增 `build_attribution_prompt_from_signal`：当系统尚未接入新闻数据时，可基于股票代码、所属市场、多倍股分类及各项涨幅分位数等价格特征生成给 LLM 的基础归因 Prompt，并附有指示："注意：当前没有新闻或公告数据，请基于市场知识对该股票做初步归因判断"。
  - 在 `ai/attributor.py` 中实现 `GeminiAttributor`，通过环境变量 `GEMINI_API_KEY` 或参数传递密钥调用 LLM。支持结构化 JSON 解析并包含容错回退机制（失败时自动回退给 `StubAttributor`）。
  - 更新 CLI 的 `run-daily` 命令，新增 `--api-key`, `--model`, `--base-url` 参数；在 `main` 处理时，只要有 API 密钥存在，即实例化 `GeminiAttributor` 并注入流水线。
  - 编写 `tests/test_gemini_attributor.py` 用 `unittest.mock` 深度测试 LLM 响应处理及错误回退行为（不消耗真实 API Key），73/73 测试通过，Lint 全绿。
- 创建/修改的文件：
  - `pyproject.toml`
  - `src/lurker/ai/prompts.py`
  - `src/lurker/ai/attributor.py`
  - `src/lurker/cli.py`
  - `tests/test_gemini_attributor.py`
  - `progress.md`

### 阶段 15：新闻摘要抓取喂料（Ingest News）
- **状态：** complete
- 执行的操作：
  - 新增 `src/lurker/ingest/news.py` 模块，为 A 股使用 `akshare`，为美港股使用 `yfinance`，抓取最新的新闻标题和摘要。
  - 在 `src/lurker/application/signal_scan.py` 的 `StockSignal` 中增加 `extra_sources` 字段。
  - 在 `run_daily` 中，先调用新闻接口，抓取到的数据传给 `GeminiAttributor`，让大模型能在无风控触发时阅读真实文本。
- 创建/修改的文件：
  - `src/lurker/ingest/news.py`
  - `src/lurker/application/signal_scan.py`
  - `src/lurker/application/run_daily.py`

### 阶段 16：板块联动扫描（Sector Breadth）
- **状态：** complete
- 执行的操作：
  - 在 `src/lurker/universe/resolved_seed_pool.py` 中更新解析逻辑，直接缓存全局的 `theme_mapping: {symbol: [theme_id]}`。
  - 新增 `src/lurker/application/sector_scan.py`，遍历扫描到的信号，按主题分组并计算各个主题的强势股数量及跨市场情况，最终生成各个主线的联动分（0-100）。
  - 在 `run_daily.py` 中移除写死的 50 分逻辑，为每个个股分派所属的最强主题的得分。
  - CLI `run-daily` 支持传入解析好的 `resolved_seed_pool.json`，供 `run_daily` 取用。
- 创建/修改的文件：
  - `src/lurker/universe/resolved_seed_pool.py`
  - `src/lurker/application/sector_scan.py`
  - `src/lurker/application/run_daily.py`
  - `src/lurker/cli.py`

### 阶段 17：A 股 ETF 成分股展开和 Gemini Key 文件接入
- **状态：** complete
- 执行的操作：
  - 确认 A 股主题 ETF 已通过 `resolve_cn_etf_constituents()` 接入 akshare 公募基金持仓接口，并映射了通信 ETF、人工智能 ETF、创新药 ETF、生物医药 ETF。
  - 为 ETF 解析补充测试，覆盖“取最新季度前 N 大重仓股并规范化为 `.SH` / `.SZ` 后缀”的行为。
  - 将 `load_resolved_theme_seed_symbols()` 的 fallback 路径补齐为同时展开 A 股指数和 A 股 ETF，避免无缓存时漏掉 ETF 重仓股。
  - `run-daily` 新增 `--api-key-file`，默认读取项目根目录 `key` 文件；优先级为命令行 `--api-key` > `key` 文件 > `GEMINI_API_KEY` 环境变量。
  - 将 `key` 和 `tests/key` 加入 `.gitignore`，避免本地 Gemini 密钥进入版本库。
  - 更新 README、`task_plan.md` 和 `findings.md`，同步 Gemini 默认模型为 `gemini-2.5-flash`，并说明 A 股 ETF 成分股已展开。
  - 运行 `lurker resolve-seeds` 刷新本地 resolved universe，当前结果为 `cn=173, hk=5, us=6`。
  - 运行全量测试和 lint，`77 passed`，`ruff` 全绿。
- 创建/修改的文件：
  - `.gitignore`
  - `README.md`
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
  - `src/lurker/ingest/constituents.py`
  - `src/lurker/cli.py`
  - `src/lurker/ai/attributor.py`
  - `tests/test_ingest.py`
  - `tests/test_cli.py`

### 阶段 18：每日运行闭环验证
- **状态：** complete
- 执行的操作：
  - 新增 `daily_job()` 应用入口，串联 resolved seed pool、行情快照刷新、Gemini/Stub 归因、日报渲染和报告落盘。
  - 新增 `lurker daily-job` CLI 命令，支持 `--markets`、`--period`、`--windows`、`--limit`、`--date`、`--signal-threshold`、`--main-limit`、`--api-key-file`、`--model`、`--base-url`。
  - 新增测试覆盖每日闭环：读取 seed pool、刷新快照、传入 theme mapping、生成 Markdown 日报并写入报告目录。
  - 将 README 的本地定时任务示例更新为 `lurker daily-job`。
  - 使用 `tests/key` 作为本地 Gemini key 文件路径跑真实小样本闭环：`cn,us,hk` 每市场 1 只，`period=6mo`，`windows=20,60,120`，`signal_threshold=0`。
  - 真实运行结果：写入 `data/processed/price_snapshots/2026-05-17.json` 和 `data/reports/2026-05-17.md`；本次 `snapshots=2, failures=1`，失败来自 A 股 `300308.SZ` 的 Eastmoney 连接断开。
  - 日报成功落盘，当前小样本无主候选，风险提醒记录了 1 只标的行情获取失败。
  - 运行全量测试和 lint，`79 passed`，`ruff` 全绿。
- 创建/修改的文件：
  - `README.md`
  - `task_plan.md`
  - `progress.md`
  - `src/lurker/cli.py`
  - `tests/test_cli.py`
  - `data/processed/price_snapshots/2026-05-17.json`
  - `data/reports/2026-05-17.md`

### 阶段 19：A 股行情多源稳定性硬化
- **状态：** complete
- 执行的操作：
  - 用户确认“不需要快”，因此 A 股行情抓取策略切换为稳定优先。
  - 新增 Tushare 日线适配：读取 `TUSHARE_TOKEN`，使用 `pro_bar` 拉取前复权日线；无 token 时显式跳过。
  - 新增 BaoStock 日线适配：作为免费兜底源，格式化 `300308.SZ` 为 `sz.300308`，拉取前复权日线。
  - 新增 `fetch_cn_prices()`，默认按 `Tushare -> AkShare/Eastmoney -> BaoStock` 顺序尝试，并在源之间加入短暂停顿。
  - 将 `application.price_snapshot.DEFAULT_FETCHERS["cn"]` 从 AkShare 单源切换为 `fetch_cn_prices` 多源。
  - 新增单元测试覆盖 Tushare/BaoStock 字段归一化、BaoStock 代码格式转换和多源 fallback 顺序。
  - 安装新增依赖 `tushare`、`baostock`。
  - 真实验证 `fetch_cn_prices("300308.SZ", "6mo")`：Tushare 无 token 跳过，BaoStock 兜底成功返回 118 行。
  - 重新运行 `daily-job` 小样本，结果从此前 `snapshots=2, failures=1` 提升为 `snapshots=3, failures=0`。
  - 运行全量测试和 lint，`83 passed`，`ruff` 全绿。
- 创建/修改的文件：
  - `README.md`
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
  - `pyproject.toml`
  - `src/lurker/ingest/prices.py`
  - `src/lurker/application/price_snapshot.py`
  - `tests/test_ingest.py`
  - `data/processed/price_snapshots/2026-05-17.json`
  - `data/reports/2026-05-17.md`

## 错误日志
| 时间戳 | 错误 | 尝试次数 | 解决方案 |
|--------|------|---------|---------|
| 2026-05-17 | `python` command not found | 1 | 改用 `python3` |
| 2026-05-17 | 安装仓库根目录时报 `Invalid skill name` | 1 | 查看仓库结构，改为安装 `skills/planning-with-files-zh` |
| 2026-05-17 | `download` 模式对仓库根目录返回 HTTP 404 | 1 | 使用 `master` 分支和明确技能子路径 |
| 2026-05-17 | 升级 pip/setuptools/wheel 时清华 PyPI 镜像证书校验失败 | 1 | 虚拟环境已可用；后续安装依赖时改用官方 PyPI 或修复本机证书配置 |
| 2026-05-17 | 官方 PyPI 也触发本机证书校验失败 | 1 | 临时使用 `--trusted-host pypi.org --trusted-host files.pythonhosted.org` 安装依赖，后续单独修证书配置 |
| 2026-05-17 | yfinance 返回 MultiIndex 列导致 `adj_close` 变成 DataFrame | 1 | 在 `normalize_price_frame` 中扁平化 MultiIndex 列，并设置 `multi_level_index=False` |
| 2026-05-17 | Yahoo 查不到 5 位港股代码 `01801.HK` | 1 | 对超过 4 位的港股数字代码去前导零查询，报告保留原始代码 |
| 2026-05-17 | 沙箱内无法解析 akshare 使用的 `push2his.eastmoney.com` | 1 | 按权限流程联网重跑 A 股快照，验证通过 |
| 2026-05-17 | Eastmoney 行情接口偶发代理连接断开 | 1 | 对单标的行情失败做跳过处理，空快照显式显示 `No available data` |
| 2026-05-17 | `run_daily` failures 提醒在无信号时提前 return 导致丢失 | 1 | 将 failures/risk_alerts 计算移到 early return 之前 |
| 2026-05-17 | 使用 Gemini API 跑出 `models/gemini-2.0-flash is no longer available` HTTP 404 | 1 | `gemini-2.0-flash` 模型由于过旧被弃用，修改 `attributor.py` 默认使用 `gemini-2.5-flash` |
| 2026-05-17 | `daily-job` 小样本真实运行时 A 股 `300308.SZ` 行情连接断开 | 1 | 闭环不中断，写入 `failures=1` 和日报风险提醒；后续可加重试、降速或备用行情源 |
| 2026-05-17 | AkShare/Eastmoney 对 `300308.SZ` API 请求返回 empty reply / remote disconnected | 1 | 新增 Tushare 和 BaoStock 多源 fallback；真实小样本复跑后 `failures=0` |

## 测试结果（续，阶段 13）
| 测试 | 输入 | 预期结果 | 实际结果 | 状态 |
|------|------|---------|---------|------|
| signal_scan 排序 | 10 条快照，threshold=0 | 信号列表非空且按分降序 | 通过 | pass |
| signal_scan 阈值过滤 | 极低涨幅快照，threshold=60 | 返回空列表 | 通过 | pass |
| signal_scan 分市场分位数 | cn/us 各 2 条快照 | 各市场独立排名 | 通过 | pass |
| StubAttributor 归因 | 任意 StockSignal | 返回「证据不足型」，ai_score=30 | 通过 | pass |
| run_daily 无信号 | 空快照 | 日报含「无个股触发」提示 | 通过 | pass |
| run_daily 有强势信号 | 高涨幅快照，threshold=0 | 日报含「主候选」区 | 通过 | pass |
| run_daily failures 提醒 | 含 failures 的批次 | 风险提醒含失败数量 | 通过 | pass |
| 全量测试 | `.venv/bin/python -m pytest -q` | 通过 | 83 passed | pass |
| Lint 检查 | `.venv/bin/python -m ruff check src tests` | 通过 | All checks passed | pass |
| 真实端到端链路 | `refresh-prices --markets us,hk --limit 3` 后运行 `run-daily` | 日报正常输出 | snapshots=6, failures=0，日报正常 | pass |
| 每日闭环小样本 | `lurker daily-job --markets cn,us,hk --limit 1 --period 6mo --windows 20,60,120 --signal-threshold 0 --main-limit 3 --api-key-file tests/key --date 2026-05-17` | 写入快照和日报，失败进入风险提醒 | snapshots=2, failures=1，日报已落盘 | pass |
| A 股多源兜底验证 | `fetch_cn_prices("300308.SZ", "6mo")` | Eastmoney 失败时仍返回日线 | BaoStock 兜底成功，返回 118 行 | pass |
| 多源每日闭环复跑 | `lurker daily-job --markets cn,us,hk --limit 1 --period 6mo --windows 20,60,120 --signal-threshold 0 --main-limit 3 --api-key-file tests/key --date 2026-05-17` | A 股失败被兜底 | snapshots=3, failures=0 | pass |

## 五问重启检查
| 问题 | 答案 |
|------|------|
| 我在哪里？ | 阶段 19 完成。A 股行情已从 AkShare 单源升级为 `Tushare -> AkShare/Eastmoney -> BaoStock` 多源 fallback；每日小样本闭环已做到 `failures=0`。 |
| 我要去哪里？ | 下一步优先：配置 Tushare token 作为稳定主源，或继续做推送和候选历史复盘。 |
| 目标是什么？ | 构建本地自用的大趋势投资雷达 MVP，先验证发现异动趋势候选能力 |
| 我学到了什么？ | 见 `findings.md` |
| 我做了什么？ | 见上方阶段日志 |

---
*每个阶段完成后或遇到错误时更新此文件*
