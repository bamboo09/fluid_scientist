# Trae Feature Audit Matrix

## 功能审计矩阵

| 能力 | 仓库 B 文件 | 仓库 A 对应实现 | B 是否更完整 | 迁移建议 | 状态 |
|------|------------|----------------|-------------|----------|------|
| SQLite V5 持久化 | `draft_session/v5_storage.py` | 内存字典 `_draft_store` 等 | 是 | PORT_WITH_ADAPTER | 需增加 PipelineCheckpoint 表 |
| OpenFOAM 字典写入 | `case_plan/foam_writer.py` | `case_generation/writer.py` (更完整) | 否 | KEEP_CODEX | 保留 Codex writer.py |
| 三栏 UI 布局 | `apps/web/v5-app.js` + `styles.css` | 两栏 `app.js` + `v5-pipeline.js` | 是 | PORT_WITH_ADAPTER | 需适配 Codex V5 Pipeline API |
| 对话时间线 | `v5-app.js` conv-timeline | `app.js` conversation-stream | 是 | PORT_BEHAVIOR_ONLY | 需接入 Codex API |
| 只读草案查看器 | `v5-app.js` draft-viewer | 无 | 是 | PORT_DIRECTLY | 纯前端组件 |
| 提案确认 UI | `v5-app.js` proposal cards | `app.js` edit-proposal | 是 | PORT_BEHAVIOR_ONLY | 需接入 Codex Proposal API |
| Action Bar 动态按钮 | `v5-app.js` action-bar | 无 | 是 | PORT_BEHAVIOR_ONLY | 需适配 Codex 状态机 |
| InputRouter 关键字 | `draft_session/input_router.py` | 同名文件 | 是 (更多关键字) | PORT_BEHAVIOR_ONLY | 保留 Codex intent 字段 |
| LLMClient reconfigure | `llm/client.py` | 同名文件 | 是 | PORT_WITH_ADAPTER | 拒绝静默 fallback |
| LLMClient _extract_json | `llm/client.py` | 无 | 是 | PORT_DIRECTLY | 纯工具函数 |
| LLMClient 静默 fallback | `llm/client.py` | RuntimeError | 否 | REJECT_LEGACY | 静默 fallback 危险 |
| 工作站提交端点 | `v5_router.py` 4 endpoints | 无 | 是 (新功能) | PORT_DIRECTLY | 纯增量 |
| 模型配置端点 | `v5_router.py` /model-config | app.py /model-configurations | 是 (v5 专用) | PORT_DIRECTLY | 与 app.py 级配置共存 |
| Playwright E2E 测试 | `tests/e2e/test_v5_playwright_e2e.py` | 无 | 是 | PORT_DIRECTLY | 互补测试 |
| foam_writer 测试 | `tests/case_plan/test_foam_writer.py` | 无 | 是 | PORT_DIRECTLY | 需同步迁移 foam_writer |
| V5Storage 测试 | `tests/draft_session/test_v5_storage.py` | 无 | 是 | PORT_DIRECTLY | 需同步迁移 v5_storage |
| 审计日志 | `v5_storage.py` log_audit() | 无 | 是 | PORT_DIRECTLY | 纯增量 |
| LLMClient latency_ms | 无 | 有 | 否 | KEEP_CODEX | 保留延迟追踪 |
| InputRoute.intent 字段 | 无 (bug) | 有 | 否 | KEEP_CODEX | Trae 缺失此字段 |
| Pipeline 端点 | 无 | `/pipeline/run` 等 4 个 | 否 | KEEP_CODEX | Codex 独有 |
| Capability Health | 无 | `/capabilities/health` | 否 | KEEP_CODEX | Codex 独有 |
| _classify_with_llm | 无 | 有 | 否 | KEEP_CODEX | Codex 独有 |
| _complete_experiment_design | 无 | 有 | 否 | KEEP_CODEX | Codex 独有 |
| compile-ready 检查 | 无 (非阻塞) | 有 (阻塞) | 否 | KEEP_CODEX | Trae 非阻塞不安全 |

## Prompt 迁移矩阵

| Prompt 文件 | 仓库 A | 仓库 B | 差异 | 迁移建议 |
|------------|--------|--------|------|----------|
| `input_router_prompt.txt` | 有 | 有 | 逐字相同 | 无需迁移 |
| `intent_system_prompt.txt` | 有 | 有 | 未对比 | 暂不迁移 |
| 其他 prompts/*.txt | 12 个 | 12 个 | 结构相同 | 暂不迁移 |

**结论**: Prompt 体系两版本一致，无需迁移。不创建第三套 Prompt。

## Session 迁移矩阵

| 维度 | 仓库 A | 仓库 B | 迁移建议 |
|------|--------|--------|----------|
| DraftSession 模型 | 有 (含 intent 字段) | 有 (缺 intent 字段) | KEEP_CODEX |
| SessionStore | `persistence.py` (tempdir) | `persistence.py` (~/.fluid_scientist_sessions) | KEEP_CODEX (tempdir 更安全) |
| V5Repository | 无 (内存字典) | 有 (SQLite) | PORT_WITH_ADAPTER |
| PipelineCheckpoint | 有 (在 orchestrator.py) | 无 | KEEP_CODEX |
| 重启恢复 | 不支持 (内存) | 支持 (SQLite) | PORT (通过 V5Repository) |

**结论**: 迁移 V5Repository 作为持久化层，保留 Codex 的 DraftSession 模型和 PipelineCheckpoint。

## UI 迁移矩阵

| 组件 | 来源 | 迁移方式 | 适配需求 |
|------|------|----------|----------|
| 三栏布局 CSS | Trae styles.css | 提取三栏样式 | 无 |
| 三栏布局 HTML | Trae index.html | 提取结构 | 保留 Codex 的 v5-pipeline.js 入口 |
| v5-app.js 对话流程 | Trae v5-app.js | 行为迁移 | 适配 Codex `/api/v5/pipeline/*` API |
| 草案查看器 | Trae v5-app.js | 直接迁移 | 接入 Codex Draft 模型 |
| 提案确认 UI | Trae v5-app.js | 行为迁移 | 接入 Codex Proposal API |

**结论**: 迁移三栏视觉结构和草案查看器，v5-app.js 的 API 层需要适配 Codex 端点。

## 逻辑冲突清单

| 冲突 | 处理结论 |
|------|----------|
| Trae UI 调用 `/api/v5/sessions/*` | ADAPTED — 适配到 Codex 已有端点 |
| Trae Session 绕过 PipelineCheckpoint | NO_CONFLICT — V5Repository 不涉及 checkpoint |
| Trae IntentEngine 使用 Fake fallback | REJECTED — 保留 Codex 严格策略 |
| Trae Prompt 覆盖 Codex 科学规划 | NO_CONFLICT — Prompt 体系一致 |
| Trae Draft 缺少 capability 状态 | NO_CONFLICT — Codex Draft 模型保留 |
| Trae Proposal 直接写回 Draft | REJECTED — 保留 Codex 确认机制 |
| Trae Session 只存在于内存 | ADAPTED — 迁移 V5Repository |
| Trae API 创建第二套 Study | NO_CONFLICT — 端点路径相同 |
| Trae 页面恢复旧 V2 | REJECTED — v5-app.js 不加载旧 app.js |
| Trae 代码把能力缺失显示为待填写 | NO_CONFLICT — Codex capability 状态保留 |
