# 任务计划：大趋势投资雷达 MVP

## 目标
构建一个本地自用的大趋势投资雷达系统，先验证“发现异动趋势候选”的能力，并保留后续产品化升级空间。

## 当前阶段
阶段 24

## 各阶段

### 阶段 1：需求与发现
- [x] 阅读原始需求文档
- [x] 明确第一版定位为自用研究工具
- [x] 明确优先验证发现能力，而非短期收益能力
- [x] 明确三市场分工、信号分层、AI 早介入和用户可见性原则
- [x] 将发现记录到 `findings.md`
- **状态：** complete

### 阶段 2：规划与结构
- [x] 编写产品设计稿
- [x] 编写 MVP 实施任务清单
- [x] 安装 `planning-with-files-zh` 用于文件化进度跟踪
- [x] 创建 `task_plan.md`、`findings.md`、`progress.md`
- [x] 使用 Homebrew Python 3.12 创建项目本地虚拟环境 `.venv`
- [x] 用户选择执行方式：Inline Execution
- **状态：** complete

### 阶段 3：项目骨架实现
- [x] 创建 Python 项目结构
- [x] 创建配置文件：`themes.yaml`、`markets.yaml`、`scoring.yaml`
- [x] 创建 SQLite 存储模型
- [x] 创建基础测试
- **状态：** complete

### 阶段 4：信号、评分和候选流水线
- [x] 实现种子池构建和市场过滤
- [x] 实现个股强度和翻倍股信号
- [x] 实现板块联动分
- [x] 实现候选排序和主候选/次级线索分层
- **状态：** complete

### 阶段 5：AI 归因、日报和推送
- [x] 实现 AI 归因 schema 和 prompt
- [x] 实现趋势卡片和日报 Markdown
- [x] 实现 CLI demo pipeline
- [x] 实现 PushPlus 或 Server 酱推送适配
- **状态：** complete

### 阶段 6：测试与验证
- [x] 运行完整测试
- [x] 运行 lint
- [x] 运行本地 CLI
- [x] 验证日报包含主候选、次级线索、观察池变化、风险提醒
- **状态：** complete

### 阶段 7：轻量领域层重构
- [x] 新增 `domain/` 保存纯领域模型、信号规则、评分策略和归因策略
- [x] 新增 `application/` 保存候选排序用例
- [x] 将旧 `signals/`、`scoring/`、`pipeline.py` 改为兼容入口
- [x] 增加领域层架构测试，约束 `domain/` 不依赖 pandas、SQLAlchemy、requests、yfinance、akshare
- [x] 更新 README 架构说明
- **状态：** complete

### 阶段 8：真实行情数据接入第一刀
- [x] 新增价格快照用例，从主题种子池读取美股/港股代码
- [x] 接入 yfinance 日线数据并计算窗口收益
- [x] 处理 yfinance MultiIndex 列格式
- [x] 处理 5 位港股代码到 Yahoo 4 位代码的查询适配
- [x] 新增 `lurker data-snapshot` CLI 子命令
- [x] 用真实 yfinance 数据跑通小样本快照
- **状态：** complete

### 阶段 9：A 股行情接入和种子来源边界
- [x] 明确 `seed_symbols`、`seed_indexes`、`seed_etfs` 的职责边界
- [x] 新增 A 股 akshare 日线行情适配
- [x] 新增 A 股代码到 akshare 查询代码的转换
- [x] `data-snapshot` 默认开放 `cn,us,hk`
- [x] 保留指数/ETF 来源为可解析边界，后续再展开成成分股
- [x] 用真实 akshare 数据跑通 A 股小样本快照
- **状态：** complete

### 阶段 10：A 股指数成分股展开
- [x] 新增 A 股指数名称到 akshare 接口参数的映射
- [x] 支持沪深 300、中证 1000、科创 50、创业板指/创业板核心指数
- [x] 将指数成分股代码规范化为 `.SH` / `.SZ` / `.BJ`
- [x] `data-snapshot` 使用解析后的 seed symbols
- [x] 单个标的行情失败时跳过，避免整批快照中断
- [x] 保持 `seed_etfs` 为未展开来源边界
- **状态：** complete

### 阶段 11：Resolved Universe 持久化
- [x] 明确系统不是日内/短线交易工具，Universe 刷新和行情扫描解耦
- [x] 新增 resolved seed pool JSON 结构
- [x] 新增 `lurker resolve-seeds` 命令，手动刷新 `data/processed/resolved_seed_pool.json`
- [x] `data-snapshot` 优先读取已缓存的 resolved seed pool
- [x] 在缓存中保留 manual、indexes、etfs、unresolved 来源
- **状态：** complete

### 阶段 12：本地行情快照文件存储
- [x] 保留行情快照存储抽象，当前先使用文件实现
- [x] 新增价格快照批次结构，记录 snapshots 和 failures
- [x] 新增 `FilePriceSnapshotStore`
- [x] 新增 `lurker refresh-prices` 命令
- [x] 将行情快照写入 `data/processed/price_snapshots/YYYY-MM-DD.json`
- [x] `data-snapshot` 优先读取最近一次本地行情快照
- **状态：** complete

### 阶段 13：真实链路打通（快照→信号→归因→排序→日报）
- [x] 新增 `application/signal_scan.py` 批量计算个股信号
- [x] 新增 `ai/attributor.py` 定义 `Attributor` Protocol 及 `StubAttributor`
- [x] 新增 `application/run_daily.py` 串联每日 pipeline
- [x] 新增 `lurker run-daily` CLI 子命令
- [x] 验证整条端到端无真实 API Key 时的流转逻辑
- **状态：** complete

### 阶段 14：接入真实 LLM 归因
- [x] 在 `pyproject.toml` 增加 `openai` 依赖
- [x] 实现 `GeminiAttributor` 支持 OpenAI-compatible 接口调用
- [x] 实现基础归因 Prompt 构建
- [x] 更新 `run-daily` CLI 参数，支持 `--api-key`、`--model`、`--base-url`
- [x] 编写使用 mock 的单元测试，涵盖容错回退等边界
- **状态：** complete

### 阶段 15：新闻摘要抓取喂料（Ingest News）
- [x] 新增 `src/lurker/ingest/news.py` 适配 akshare 和 yfinance 新闻抓取
- [x] 在 `StockSignal` 中增加 `extra_sources`
- [x] 整合进入 `run_daily.py`，调用真实 LLM 前抓取新闻
- **状态：** complete

### 阶段 16：板块联动扫描（Sector Breadth）
- [x] 修改 `build_resolved_seed_pool` 缓存主题映射（theme_mapping）
- [x] 新增 `sector_scan.py` 按主题统计强势股及跨市场特征
- [x] 替换 `run_daily.py` 中写死的 50 分，使用动态板块联动分
- **状态：** complete

### 阶段 17：A 股 ETF 成分股展开和 Gemini Key 文件接入
- [x] 为 A 股主题 ETF 增加重仓股解析映射
- [x] `build_resolved_seed_pool` 将 ETF 重仓股写入 resolved universe，并保留来源归因
- [x] `load_resolved_theme_seed_symbols` 在无缓存 fallback 时也展开 A 股 ETF 重仓股
- [x] `run-daily` 支持默认读取项目根目录 `key` 文件作为 Gemini API Key
- [x] 更新 README、计划和进度文档，说明 Gemini 默认模型为 `gemini-2.5-flash`
- **状态：** complete

### 阶段 18：每日运行闭环验证
- [x] 新增 `lurker daily-job` 一键命令
- [x] 每日命令刷新价格快照并写入 `data/processed/price_snapshots/YYYY-MM-DD.json`
- [x] 每日命令生成 Markdown 日报并写入 `data/reports/YYYY-MM-DD.md`
- [x] 每日命令复用 Gemini/key 文件归因路径
- [x] 用真实小样本跑通 `cn,us,hk` 闭环，记录 snapshots/failures
- [x] 更新 README 本地定时任务示例
- **状态：** complete

### 阶段 19：A 股行情多源稳定性硬化
- [x] 新增 Tushare A 股日线适配，支持 `TUSHARE_TOKEN`
- [x] 新增 BaoStock A 股日线兜底适配
- [x] 新增 `fetch_cn_prices` 稳定优先 fallback：Tushare -> AkShare/Eastmoney -> BaoStock
- [x] 将每日行情默认 A 股 fetcher 切换为多源 fallback
- [x] 用真实小样本验证 A 股由 BaoStock 兜底成功，`daily-job` 从 `failures=1` 降为 `failures=0`
- [x] 更新 README、计划和进度文档
- **状态：** complete

### 阶段 20：低分观察样本与候选历史
- [x] 日报增加“低分观察样本”，展示 archive 中最值得复盘的早期线索
- [x] 保存每日候选/观察样本结构化 JSON
- [x] 为后续重复推送控制保留历史字段
- [x] 更新 README、计划和进度文档
- **状态：** complete

### 阶段 21：本地屏蔽列表
- [x] 新增 `configs/suppressed_symbols.yaml`，记录不再展示的标的
- [x] `run_daily` 支持按本地屏蔽列表过滤主候选、次级线索和低分观察样本
- [x] CLI 支持 `--suppressed-symbols`
- [x] 日报在观察池变化中提示本地屏蔽已生效
- [x] 更新 README、计划和进度文档
- **状态：** complete

### 阶段 22：多策略框架
- [x] 新增策略配置 `configs/strategies.yaml`
- [x] 新增 `StrategyConfig`、`StrategyContext`、`StrategyResult` 和策略运行器
- [x] 将当前中长期趋势日报包成 `long_term_trend` 策略
- [x] CLI 支持 `--strategy-config`、`--strategies`、`--cadence`
- [x] 注册短期交易、卖点提醒、深度复核占位策略
- [x] 更新 README、计划和进度文档
- **状态：** complete

### 阶段 24：日报归档索引
- [x] `daily-job` 更新 `data/reports/index.json`
- [x] 索引记录日期、报告路径、候选历史路径、快照路径、策略、市场、窗口和失败数量
- [x] 新增 `lurker list-reports` 命令
- [x] 索引按日期 upsert，重复跑同一天会更新同一条记录
- [x] 更新 README、计划和进度文档
- **状态：** complete

## 关键问题
1. A 股 ETF 成分股展开已完成第一版，但仍需持续观察 akshare 持仓接口字段稳定性和更新频率。
2. 每日闭环已接入 A 股多源 fallback；后续如配置 Tushare token，可把 Tushare 作为更稳定主源。
3. 当前日报已接入多策略框架和按日归档索引；短期交易雷达、卖点提醒和深度复核已有配置占位，但尚未实现各自股票池和扫描规则。

## 已做决策
| 决策 | 理由 |
|------|------|
| 第一版先做自用研究工具 | 短期不以收益证明系统，而是验证发现质量和复盘质量 |
| 优先验证发现能力 | 用户希望先捕捉异动，再分析验证行情质量，确认后长期跟踪 |
| 使用“规则主导 + AI 早介入” | 规则保证可解释和可复盘，AI 负责早期归因和降噪 |
| AI 不能静默吞掉触发信号 | 用户需要感知被降级的线索，避免黑箱错杀 |
| A 股负责主发现，美股负责锚点，港股强过滤后做映射补充 | 三个市场信息质量和噪音结构不同，不适合一套规则硬套 |
| A 股从沪深 300、中证 1000、科创 50、创业板核心指数和主题 ETF 成分股起步 | 控制噪音，同时保留主线发现能力 |
| A 股第一刀先扫描 `seed_symbols`，`seed_indexes` 和 `seed_etfs` 先作为未展开来源边界 | 先验证真实行情链路，再处理 akshare 成分股接口差异 |
| 第二刀先展开 A 股核心指数，ETF 成分股继续作为下一步 | 指数接口更稳定，ETF 持仓字段和可得性更不统一 |
| 第三刀展开 A 股主题 ETF 重仓股 | 提升细分主题覆盖率，同时通过 sources.etfs 保留可追溯来源 |
| A 股行情采用 Tushare -> AkShare/Eastmoney -> BaoStock 多源 fallback | 用户不需要快，稳定产出日报更重要；Eastmoney 断开时 BaoStock 可兜底 |
| archive 中的早期线索单独作为“低分观察样本”展示 | 不放松主候选门槛，但让低阈值扫描下的弱信号可被人工复盘 |
| `daily-job` 同时落盘 `.candidates.json` | 为后续重复推送控制、手动屏蔽和历史复盘保留结构化输入 |
| 先用本地 YAML 做“不再展示” | 用户当前不需要 agent/IM/实时反馈，手动维护列表成本最低且可追溯 |
| 先搭多策略框架，再做短期雷达 | 中长期趋势、短期交易、卖点提醒的股票池、频率和报告结构不同，不应继续堆进单一 `run_daily` |
| 日报目录增加 `index.json` | 文件按日命名已经能归档，但索引能支持最近日报查询、后续重复出现统计和跨日复盘 |
| Universe 刷新与行情扫描解耦 | 系统用于中长期趋势发现和研究样本积累，不需要每次扫描都实时重建股票池 |
| 行情快照先用文件存储，但保留 `PriceSnapshotStore` 抽象 | 当前避免过早引入数据库，后续可替换为 SQLite/Postgres 存储 |
| 第一版采用行业/概念分类扩池 | 用户熟悉股票有限，系统需要自动扩池，但第一版要保持可解释 |
| 每日主候选约 10 条，并保留 5-10 条次级线索 | 平衡信息密度和用户感知 |
| 使用 `planning-with-files-zh` 文件化记录进度 | 支持跨会话恢复和长期项目状态管理 |

## 遇到的错误
| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| `python` command not found | 1 | 改用 `python3` 运行技能安装脚本 |
| 安装仓库根目录时报 `Invalid skill name` | 1 | 查看仓库结构后改为安装 `skills/planning-with-files-zh` |
| `download` 模式对根目录返回 HTTP 404 | 1 | 使用 `master` 分支和明确技能子路径安装 |

## 重要文件
| 文件 | 用途 |
|------|------|
| `大趋势投资雷达系统需求文档.md` | 原始需求讨论文档 |
| `docs/superpowers/specs/2026-05-17-trend-radar-design.md` | 已确认的产品设计稿 |
| `docs/superpowers/plans/2026-05-17-trend-radar-mvp.md` | MVP 实施任务清单 |
| `task_plan.md` | 当前文件化任务计划 |
| `findings.md` | 需求、研究和技术决策记录 |
| `progress.md` | 会话进度、测试和错误日志 |

## 备注
- 做重大决策前重新读取 `task_plan.md`、`findings.md`、`progress.md`。
- 每完成阶段后更新阶段状态。
- 每次遇到错误都记录，避免重复失败。
