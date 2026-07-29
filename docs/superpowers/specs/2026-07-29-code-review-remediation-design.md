# 代码审查问题修复设计

日期：2026-07-29

## 目标

修复 `docs/code-review-2026-07-29.md` 中经运行验证确认的问题，同时保持现有 CLI、应用层调用和 `lurker.pipeline` 兼容入口可用。

## 范围

本轮处理：

1. 消除不生效的 `ai_attribution.weights` 配置。
2. 让 AI 归因只保留一套结构定义和一套容错解析规则。
3. 为尚未实现的策略建立 `planned` 生命周期，并阻止其被运行。
4. 删除旧评分函数中生产不可达的扩展分支。
5. 为基础报告渲染器增加直接边界测试。
6. 将 `tests/` 变成显式 Python 包，硬化跨测试模块导入。
7. 将 CLI 参数构造与命令分发从 `cli.py` 提取到小型模块，保持命令行行为不变。

本轮不处理：

- 删除 `lurker.pipeline` 兼容入口。
- 全面重写 CLI 或改变命令参数。
- 实现 `short_term_setup`、`exit_alert`、`deep_research` 的业务逻辑。
- 为纯风格问题批量翻译注释。

## 设计

### 策略生命周期

`StrategyLifecycle` 扩展为 `active | planned | deprecated`。

- `active`：必须有注册实现，不得声明 `limitations`。
- `planned`：必须 `enabled: false`，必须声明非空 `limitations`，不可被默认或显式选择运行。
- `deprecated`：必须 `enabled: false`，必须声明非空 `limitations`，允许显式选择以保留历史兼容行为。

配置中的三个占位策略改为 `planned`。选择 planned 策略时抛出包含策略名和能力缺口的 `ValueError`；运行器对任何未注册的 active 策略也直接报错，不再生成看似成功的占位日报。

### AI 归因

当前硬编码评分规则是实际产品行为，因此删除 `configs/scoring.yaml` 中未生效的 `ai_attribution.weights`。`load_scoring()` 增加顶层字段白名单，防止该配置或其他未知配置重新静默进入。

领域层 `AttributionResult` 继续作为唯一结果类型。允许值集合与容错构造函数移入 `domain/attribution.py`；Gemini attributor 调用该构造函数。删除未被生产使用的 Pydantic `AIAttributionResult` 和旧 `ai.attribution` 包装模块，并把测试迁移到领域接口。

容错语义保持不变：未知分类和建议降级为“证据不足”，未知证据被过滤，文本字段被字符串化，摘要最多 200 字。

### 旧评分分支

`score_stock_strength()` 只保留生产扫描实际传入的四项指标；`score_sector_breadth()` 只保留三项实际指标。删除七个隐藏扩展权重及其条件分支，更新直接领域测试，确保默认最高分仍分别为 60 和 55。

### 报告测试与测试导入

新增 `tests/__init__.py`，保持现有 `from tests...` 导入不变。为 `daily_report.py` 和 `trend_card.py` 增加直接测试，覆盖空列表、单条内容、Markdown 特殊字符和超长名称不丢失。现有 professional/monthly 直接测试继续保留。

### CLI 拆分

保持 `lurker.cli:main` 作为 console script 入口。提取两类纯结构代码：

- `cli_parser.py`：创建 parser 与子命令参数。
- `cli_dispatch.py`：根据已解析 namespace 调用现有命令函数。

业务用例函数暂留在 `cli.py`，避免本轮形成跨模块大搬迁。`main()` 只负责加载 `.env`、解析参数和调用分发函数。现有 CLI 测试作为兼容契约；新增测试验证入口会委托给提取后的模块。

## 错误处理

- planned 策略：配置加载时验证状态约束，选择时清晰拒绝。
- active 策略缺少注册实现：运行前失败，不产出伪成功报告。
- scoring 顶层未知字段：加载时失败并指出首个未知字段。
- AI payload：继续在 LLM 边界容错，调用异常仍回退 Stub。

## 测试策略

每项行为修改遵循红—绿—重构：

1. 先增加能复现当前错误行为的测试并确认失败。
2. 实现最小修改并运行对应测试。
3. 运行相关模块测试。
4. 最终运行全量 pytest、Ruff 和 `git diff --check`。

验收标准：

- planned 策略不能默认或显式运行。
- 未注册 active 策略不能生成占位日报。
- scoring 配置不存在无效 AI 权重且拒绝未知顶层字段。
- AI 归因只有一个结果类型和一个容错构造入口。
- 旧评分函数不包含生产不可达扩展字段。
- 四个报告渲染器均有直接测试。
- `tests` 是显式包。
- CLI 现有命令测试全部通过，入口分发职责缩小。
- 全量测试和 Ruff 通过。
