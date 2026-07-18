# Pre-Experiment 全链路代码走查审计报告

> 测试计划 §6 严格代码走查审计
> 审计范围：`src/fluid_scientist/**`、`apps/web/**`
> 审计日期：2026-07-17
> 审计方式：只读静态分析，未修改任何源代码
> 基线：当前工作区（含 `.worktrees/codex-v5-integrated-unknown-capability` 分支成果已合并入主 `src`）

---

## 0. 审计结论摘要（先读这段）

| 维度 | 结论 | 风险等级 |
|------|------|----------|
| 是否存在唯一 canonical spec | **否**。当前并存 5+ 种 spec 类型，分布在 4 条独立链路上 | 高 |
| 主活跃链 | v5 Workflow Pipeline（`/api/v5/sessions/*/messages`）是 pre-experiment 的活跃主链，但 model-editing 链（`/api/v5/model-editing`）才是架构最完整、强制无静默回退的"新规范"链 | 高 |
| 双/多主链 | ExperimentPlan / ExperimentSpec / SimulationStudySpec / CylinderFlow2DExperimentSpecV1 / ObstacleFlowExperimentSpecV1 同时存活且各自有编译器 | 高 |
| 静默回退 | v5 主链在 LLM 失败时静默回退到正则/关键词确定性提取器，不阻断、不告知用户；model-editing 链强制无回退 | 高 |
| 中文关键词驱动业务逻辑 | 大量 `if "压力出口" in text`、`if "三角形" in text` 形式的硬编码出现在核心业务代码中（非仅 prompt） | 高 |
| 持久化 | session 落盘（JSON）；draft / proposal / case_plan / compiled_case 均为进程内 dict，重启即丢失 | 中 |
| PatchEngine / DependencyEngine | 仅 model-editing 链有完整 PatchEngine；v5 主链没有 PatchEngine，修改走 ApplyProposalExecutor + 正则 `_parse_modification` | 高 |

**最严重问题**：pre-experiment 活跃主链（v5 pipeline）与架构规范链（model-editing）不是同一条链。前者用正则+关键词+静默回退，后者用结构化输出+PatchEngine+强制失败。两条链的 spec 模型互不兼容，没有任何"单一 canonical spec"。

---

## 1. 项目链路全景（4 条并存链路）

通过追踪路由挂载（`src/fluid_scientist/api/app.py`）与各 router 的 `APIRouter(prefix=...)`，确认当前并存 4 条端到端链路：

| 链路 | API 前缀 | 入口 router 文件 | canonical spec | 是否活跃 | 编译器 |
|------|----------|------------------|----------------|----------|--------|
| A. v5 Workflow Pipeline | `/api/v5` | `api/v5_router.py` | `PipelineState`→`ExperimentDraft`（无单一 spec） | 是（pre-experiment 主链） | `NativeCaseCompiler` / `OpenFOAMCaseWriter` |
| B. model-editing | `/api/v5/model-editing` | `api/model_editing_router.py` | `SimulationStudySpec` | 是（架构最规范） | `OpenFOAMCompiler`（存在但未从该 router 接线编译） |
| C. legacy app.py | `/api/experiment-plans`、`/api/projects/{}/experiment-specs` | `api/app.py` | `ExperimentPlan` / `ExperimentSpec` | 是（旧 REST） | `compile_plan` / `compile_spec`→`native_compiler` |
| D. cylinder-flow-2d | `/api/v5/cylinder-flow` | `api/cylinder_flow_router.py` | `CylinderFlow2DExperimentSpecV1`→`ObstacleFlowExperimentSpecV1` | 是（专用） | `ObstacleFlowCompiler` |

路由挂载证据（`app.py` 中 `include_router`，且 B/D 用 `try/except` 包裹，失败仅 log 不阻断启动）：
- `v5_router` → `/api/v5`
- `model_editing_router` → `/api/v5/model-editing`
- `cylinder_flow_router` → `/api/v5/cylinder-flow`

---

## 2. §6.1 真实调用链（Frontend → Compiler 逐阶段表）

下表覆盖测试计划列举的全部 19 个阶段。**注意：没有任何单条链路同时包含全部 19 个阶段**。表格"所属链路"列标注该阶段实际所在链路；当某阶段在主链 A 缺失时，明确标注"主链缺失，仅存在于链路 X"。

### 2.1 阶段总表

| # | 阶段 | 所属链路 | 文件路径 | 类/函数 | 输入 | 输出 | 数据存储 | 错误处理 |
|---|------|----------|----------|---------|------|------|----------|----------|
| 1 | Frontend message submit | A | `apps/web/v5-app.js` | `sendUserMessage(text)` | 用户文本 + UI 状态 | fetch `/api/v5/sessions/{id}/messages` | 浏览器内存 | 前端先用正则 `/^(确认\|confirm\|yes\|应用\|好的)/i` 判定 confirm/cancel；非 confirm 走 `API.sendMessage`→`processAction` |
| 2 | API route | A | `src/fluid_scientist/api/v5_router.py:760` | `send_message()` | `{session_id, message}` | `{session, actions:[...]}` | 无 | 404 if session 不存在；路由分发后聚合 actions |
| 3 | Session loader | A | `src/fluid_scientist/api/v5_router.py:765` + `draft_session/session_store.py` | `DraftSessionStore.get_session` → `JsonSessionPersistence.load_session` | session_id | `DraftSession` | JSON 文件落盘（`data/sessions/*.json`，懒加载） | 内存未命中时从磁盘懒加载；无持久化则纯内存 |
| 4 | Skill Router | A | `src/fluid_scientist/draft_session/input_router.py` | `InputRouter.route` | session + user_message | `RouteResult(input_type, intent, confidence)` | 无 | **先关键词规则判定**，规则置信度低才调 LLM；LLM 失败静默回退到规则结果（见 §6.3） |
| 5 | Prompt Builder | A/B | A: `workflow_pipeline/pipeline.py` 内联 prompt；B: `prompts/spec_editor.py` `build_spec_editor_prompt` + `prompts/two_call_strategy.py` `TwoCallStrategy` | A: 内联字符串拼接；B: `TwoCallStrategy.execute` | context + user_message + schema | prompt 字符串 | 无 | A 无独立 prompt builder，字符串内联在 pipeline 各阶段 |
| 6 | Model Client | A | `src/fluid_scientist/llm/client.py` | `LLMClient.call` / `LLMClient.generate` | prompt + output_schema | `(parsed, record)` 或 `(text, record)` | `LLMRecord` 内存（`get_records`） | **provider=="mock" 时返回伪造响应**；真实 provider 失败抛 `RuntimeError`，但调用方 `except Exception` 静默吞掉（见 §6.3） |
| 6' | Model Client | B | 复用 A 的 `LLMClient`，经 `model_editing_router._make_model_client_callable` 包装 | 同上 | prompt | dict | 同上 | **强制无回退**：若 `record.fallback_used==True` 则 `raise RuntimeError`（line 121-125） |
| 7 | Structured Output parser | B | `src/fluid_scientist/model_runtime/structured_output.py` | `StructuredOutputValidator.parse` | raw_response + schema | `(dict, None)` 或 `(None, ModelInvocationError)` | 无 | **从不静默强制**：非 JSON→`MODEL_OUTPUT_INVALID`；schema 不符→`MODEL_SCHEMA_MISMATCH`；硬失败 |
| 7' | Structured Output parser | A | `v5_router.py:688` 内联 `json.loads` + 手工字段提取 | 无独立 validator | LLM 文本 | dict | 无 | `except Exception` 后回退到规则路由（line 703-706）。**主链 A 没有用 StructuredOutputValidator** |
| 8 | Fact merge | A | `workflow_pipeline/pipeline.py` `_stage_closing` + `workbench/design_closure_engine.py` | `DesignClosureEngine` | 理解阶段 facts + 设计候选 | 合并后的 `PipelineState.view` | PipelineState 内存 | 缺失必填能力"record but do NOT block"（pipeline.py:1244-1252） |
| 9 | Ambiguity/conflict engine | A（cylinder）/ D | `intent/conflict_resolver.py` `ConflictResolver` + `RegexCandidateExtractor`/`LLMCandidateExtractor` | regex vs LLM 候选仲裁 | 两路候选 | `ResolvedField[]` + 冲突原因 | 无 | 规则：两者冲突→NEEDS_CLARIFICATION；原则"从不静默选一边"。**仅 cylinder-flow 链使用，v5 主链 A 未接线** |
| 10 | Patch Engine | B | `src/fluid_scientist/spec_editing/patch_engine.py` | `PatchEngine.process_patch` | `SimulationSpecPatch` + `SimulationStudySpec` | `PatchResult(new_spec, diff, impact, clarifications)` | `PatchHistory`（append-only 内存账本） | 阻塞性 clarification 不应用；验证失败返回 errors。**主链 A 无 PatchEngine** |
| 10' | 修改路径（主链 A 替代物） | A | `workflow_pipeline/pipeline.py:316` `_parse_modification` + `draft_session/apply_proposal_executor.py` | `ApplyProposalExecutor` | user_message + draft | `ChangeProposal` | `_proposal_store` dict（内存） | **正则 + 中文关键词解析修改意图**（见 §6.4）；无 schema 校验 |
| 11 | Dependency Engine | B | `src/fluid_scientist/spec_editing/impact_analyzer.py` + `case_ir/dependency_graph.py` | `ImpactAnalyzer` / `DependencyGraph` | patch + spec | `ImpactReport`（派生重算 + 产物失效） | 无 | 主链 A 无独立 dependency engine；cylinder 链有 `physics_dependency.py` |
| 12 | Spec repository | A: `_draft_store` dict；B: `SessionManager._spec_store`；C: `workflow_repository` | A: `v5_router.py:85`；B: `session_state/session_manager.py` | 进程内 dict / SpecStore | spec/draft | 持久化对象 | A: **纯内存 dict，重启丢失**；B: 内存版本化 SpecStore；C: workflow_repository（落盘） | A 无持久化；B 支持版本回滚 |
| 13 | Confirmation API | A | `v5_router.py` `apply_proposal`/`cancel_proposal` 端点 + 状态机 `DraftSessionStateMachine` | `ApplyProposalExecutor.apply` | proposal_id + confirm/cancel | 更新后的 draft | `_draft_store` 内存 | `TransitionError` 被 `except` 吞掉（line 843-844, 898-899） |
| 14 | CaseIR builder | A | `workflow_pipeline/pipeline.py` `_stage_generate_with_new_arch` → `case_generation/` | `OpenFOAMCaseWriter` | PipelineState.view | OpenFOAM case 文件结构 | `_case_store` 内存 dict | 生成失败回退到 legacy draft path |
| 15 | Capability resolver | A | `workflow_pipeline/pipeline.py:1140` `_stage_resolve_capabilities` + `capabilities/registry.py` | `CapabilityRegistry` | view 中的 capability 需求 | 已解析能力列表 | 内存 | **缺失能力仅记录不阻断**（pipeline.py:1244-1252） |
| 16 | Case planner | A | `case_plan/compiler.py` `NativeCaseCompiler` + `case_plan/generator.py` `CasePlanGenerator` | `NativeCaseCompiler.compile` | ExperimentDraft / CasePlan | `CompiledCase` | `_case_plan_store`/`_case_store` 内存 | 无 |
| 17 | OpenFOAM compiler | A: `case_generation` writer；B: `openfoam_compiler/compiler.py:79` `OpenFOAMCompiler.compile(spec: SimulationStudySpec)`；C: `experiment_spec/native_compiler.py`；D: `ObstacleFlowCompiler` | 多个 | 结构化 spec/draft | case 目录 / tar.gz | 文件系统 | A 用 `OpenFOAMCaseWriter`；B 的 `OpenFOAMCompiler` 存在但**未从 model-editing router 接线** |
| 18 | Static validators | A | `case_ir/validators/__init__.py`（5 个 validator）经 `CompileReadinessValidator` 调用 | `SchemaValidator`/`ReferenceValidator`/`ScientificConsistencyValidator`/`CapabilityFeasibilityValidator`/`DimensionalConsistencyValidator` | `RequestedCaseIR` | `CaseIRValidationReport` | 无 | 5 项全过才 pass；但 v5 主链 VALIDATING 阶段对失败的处理为"记录不阻断" |
| 19 | Artifact repository | A: `_case_store` dict（内存）；D: 文件系统 + 远程工作站 | A: `v5_router.py:90`；D: `cylinder_flow_2d/execution.py` `WorkstationExecutor` | dict / SSH+SCP | compiled case | case_dir / 远程产物 | A: **纯内存**；D: 远程 OpenFOAM 工作站 | A 无持久化；D 通过 SSH 上传/拉取 |

### 2.2 主链 A（v5 Pipeline）端到端时序

```
前端 sendUserMessage(text)
  └─(confirm/cancel 正则判定)──┐
  └─ POST /api/v5/sessions/{id}/messages          [v5_router.send_message:760]
       ├─ SessionLoader: _session_store.get_session   [DraftSessionStore + JsonSessionPersistence]
       ├─ _session_store.add_message(user)
       ├─ SkillRouter: _input_router.route(session, msg)   [InputRouter]
       │     └─ _classify_with_llm(...)   [v5_router:653]  ← LLM 失败静默回退规则 (line 703)
       ├─ 分支:
       │   ├─ new_research_request → StudySplitter + PhysicsFrameExtractor
       │   │     └─ _decompose_single_study(...)   [v5_router:484]
       │   │           └─ _run_compile_ready_pipeline_for_study   [v5_router:1966]
       │   │                 └─ V5WorkflowPipeline.run   [workflow_pipeline/pipeline.py:152]
       │   │                       ├─ UNDERSTANDING: _extract_intent / _extract_intent_deterministic (回退)
       │   │                       ├─ DESIGNING:    ExperimentDesignSynthesizer
       │   │                       ├─ CLOSING:      DesignClosureEngine (fact merge)
       │   │                       ├─ RESOLVING:    CapabilityRegistry (缺失不阻断)
       │   │                       ├─ GENERATING:   OpenFOAMCaseWriter → case 文件
       │   │                       └─ VALIDATING:   CompileReadinessValidator (5 validators)
       │   │                 → ExperimentDraft → _draft_store (内存 dict)
       │   ├─ draft_change_request → ApplyProposalExecutor + _parse_modification (正则+中文关键词)
       │   │     → ChangeProposal → _proposal_store (内存 dict)
       │   ├─ proposal_confirmation → ApplyProposalExecutor.apply → 更新 draft
       │   ├─ clarification_answer → _answer_draft_question (关键词匹配回答)
       │   └─ study_selection → _select_study
       └─ _session_store.update_session(session)
  ← {session, actions:[{action, draft/proposal/...}]}
```

### 2.3 关键发现（§6.1）

1. **主链 A 没有使用 `StructuredOutputValidator`**。LLM 输出在 `v5_router.py:688` 用内联 `json.loads` + 手工字段提取解析，失败后 `except Exception` 静默回退。而架构规范的 `StructuredOutputValidator`（强制硬失败）只被链路 B 使用。
2. **主链 A 没有 PatchEngine / DependencyEngine**。修改意图由 `_parse_modification`（正则 + 中文关键词，`pipeline.py:316`）解析，无 schema 校验、无 diff、无 impact 分析、无 undo。
3. **主链 A 没有 ConflictResolver**。`intent/conflict_resolver.py` 的"从不静默选一边"仲裁器只服务 cylinder-flow 链（D）。
4. **持久化断层**：session 落盘，但 draft / proposal / case_plan / compiled_case 全是进程内 dict（`v5_router.py:85-90`），服务重启后 pre-experiment 产物全部丢失。
5. **能力缺失不阻断**：`_stage_resolve_capabilities` 对缺失的必填能力"record but do NOT block"（`pipeline.py:1244`），随后 GENERATING 仍继续。

---

## 3. §6.2 双主链检查（canonical spec 识别）

### 3.1 搜索命令与命中

```
rg -n "ExperimentPlan|ExperimentSpec|SimulationStudySpec|legacy|old_plan" src
rg -n "plan_to_spec|spec_to_plan|compile_.*plan" src
```

### 3.2 Spec 类型清单与定级

| # | Spec 类型 | 定义文件 | 使用链路 | 编译入口 | 定级 | 证据 |
|---|-----------|----------|----------|----------|------|------|
| 1 | `ExperimentPlan`（含 `PipeExperimentPlan`/`CylinderExperimentPlan`/`CavityExperimentPlan`） | `experiment_planning/models.py` | C（legacy） | `compile_plan`→`compile_pipe_plan`/`compile_cylinder_plan`/`compile_cavity_plan`（`experiment_planning/compilers/registry.py`） | **遗留/迁移源** | `app.py:1179` `compile_plan(plan)` 仍在 `/api/experiment-plans/{id}/compile` 活跃调用 |
| 2 | `ExperimentSpec` | `experiment_spec/models.py` | C（legacy） | `compile_spec`→`native_compiler.PipeFlowCompiler.compile` 等（`experiment_spec/compilation.py`） | **遗留 canonical（C 链内）** | `app.py:2736` `compile_spec(spec)` 在 `/experiment-specs/{id}/compile` 活跃；`compile_confirmed_spec` 已标 deprecated 但仍导出 |
| 3 | `SimulationStudySpec` | `study_spec/models.py` | B（model-editing） | `openfoam_compiler/compiler.py:96` `OpenFOAMCompiler.compile(spec: SimulationStudySpec)` | **架构目标 canonical（未全链接线）** | 仅 `model_editing_router` 使用；`OpenFOAMCompiler.compile` 存在但该 router 未调用编译 |
| 4 | `CylinderFlow2DExperimentSpecV1` | `cylinder_flow_2d/models.py` | D（专用） | `ObstacleFlowCompiler`（经 `SpecAdapter` 转 `ObstacleFlowExperimentSpecV1`） | **专用活跃（D 链）** | `cylinder_flow_router.py` 全链活跃，含 draft/compile/execute |
| 5 | `ObstacleFlowExperimentSpecV1` | `experiment_planning/`（obstacle） | D（执行） | `ObstacleFlowCompiler` | **D 链内部中间表示** | `execution.py:7` `SpecAdapter` 转换 |
| 6 | `PipelineState`（含 `view`） | `workflow_pipeline/pipeline.py` | A（主链） | `OpenFOAMCaseWriter` + `NativeCaseCompiler` | **A 链内部中间表示（非持久化 spec）** | 主链 A 不产出上述任何 spec，仅产出 `ExperimentDraft` |
| 7 | `ExperimentDraft` | `draft_session/models.py` | A（主链产物） | `NativeCaseCompiler` | **A 链最终产物** | `_draft_store` 内存 dict |

### 3.3 迁移代码识别

| 迁移器 | 文件 | 方向 | 是否活跃 |
|--------|------|------|----------|
| `LegacyMigrator` | `study_spec/migration.py` | `ExperimentPlan`→`SimulationStudySpec` | 是。`model_editing_router.py:42` 导入并在创建会话时把 legacy plan 迁移为 `SimulationStudySpec` |
| `ExperimentPlan→ExperimentSpec` 迁移 | `experiment_spec/migration.py` | `ExperimentPlan`→`ExperimentSpec` | 是。`app.py` experiment-specs 链路用于从旧 plan 生成统一 spec |
| `compile_confirmed_spec` | `experiment_spec/compilation.py:408` | `ExperimentSpec`→`compile_plan(ExperimentPlan)` | **DEAD_CODE 倾向**。docstring 已声明"fallback 已移除"，`compile_spec` 不再调用它，但仍被导出；仅外部直接调用才触发 |

### 3.4 canonical spec 判定

**结论：当前不存在唯一 canonical spec。**

- 若以"架构最规范、强制无静默回退、有完整 PatchEngine/DependencyEngine/Undo/History"为标准，`SimulationStudySpec`（链路 B）是**目标 canonical**，但它尚未接线到编译执行（`OpenFOAMCompiler.compile` 存在却未被 model-editing router 调用），无法端到端跑通。
- 若以"pre-experiment 实际活跃、能端到端产出到编译"为标准，链路 A（v5 pipeline）是**事实主链**，但它的产物是 `ExperimentDraft`（内存中间态），不是上述任何 spec，且全程正则+关键词+静默回退。
- 链路 C（`ExperimentSpec`）是 legacy REST 的 canonical，仍在 `/api/projects/{}/experiment-specs` 大量活跃（30+ 端点）。
- 链路 D（`CylinderFlow2DExperimentSpecV1`）是 cylinder 场景的专用 canonical，独立完整。

### 3.5 仍在活跃主链的项

- `compile_plan` / `compile_pipe_plan` / `compile_cylinder_plan` / `compile_cavity_plan`：**活跃**（`app.py:1179` + registry）。
- `compile_spec` / `native_compiler.*Compiler.compile`：**活跃**（`app.py:2736`）。
- `SimulationStudySpec` + `PatchEngine`：**活跃但半接线**（编辑可用，编译未接）。
- `CylinderFlow2DExperimentSpecV1` 全链：**活跃**。
- `compile_confirmed_spec`：**DEAD_CODE 倾向**（已弃用仍导出，主路径不再调用）。

### 3.6 风险

四链四 spec 并存导致：同一用户意图可能被路由到不同链路、产出不同 spec、走不同编译器、得到不同校验严格度。没有任何"单一事实源"约束四链 spec 互转一致性。

---

## 4. §6.3 fake 与 fallback 检查

### 4.1 搜索命令与命中

```
rg -n "fake|fallback|default_plan|mock|regex|template" src
rg -n "except Exception" src/fluid_scientist
```

### 4.2 分类标准

- **TEST_ONLY**：仅测试/CI/Demo 触发，生产路径不可达。
- **SAFE_RECOVERY**：非关键路径（可视化、日志、可选增强）的降级，失败不影响科学正确性，且有日志。
- **DANGEROUS_FALLBACK**：在主链科学/决策路径上静默回退，掩盖失败、降低结果质量且不告知用户。
- **DEAD_CODE**：已声明弃用/主路径不再调用但仍存在于代码库。

### 4.3 fake / mock 清单

| 位置 | 代码片段 | 分类 | 说明 |
|------|----------|------|------|
| `adapters/fakes.py` | Fake adapters（`FakeMeshAdapter` 等） | **TEST_ONLY** | 文件即声明用于 CI/Demo；生产由真实 adapter 替换 |
| `llm/client.py` | `provider == "mock"` 分支返回伪造响应 | **TEST_ONLY（本身）→ DANGEROUS_FALLBACK（当被主链误用）** | mock provider 本意测试；但 `_classify_with_llm`/`_decompose_single_study` 调用链在真实 provider 失败后，`LLMClient` 内部 `use_mock=True` 回退会置 `record.fallback_used=True`。链路 B 显式拒绝（`model_editing_router:121`）；**链路 A 不检查 `fallback_used`，照单全收** |
| `v5_router.py:1806` | `test_code_extension` "Run tests on generated code (mock)" | **TEST_ONLY** | CodeExtension 的测试运行为 mock，属可选增强功能，非 pre-experiment 主链 |
| `app.py:1806` 附近 | `FLUID_APP_MODE=fake` 环境变量分支 | **TEST_ONLY** | 仅 Demo 启动模式 |

### 4.4 fallback 清单

| 位置 | 代码片段 | 分类 | 说明 |
|------|----------|------|------|
| `v5_router.py:703-706` | `_classify_with_llm` 中 `except Exception: return route` | **DANGEROUS_FALLBACK** | LLM 失败后静默回退到关键词规则路由，继续走 pipeline，不阻断不告知。主链入口级回退 |
| `v5_router.py:548`（`_decompose_single_study`） | LLM 解析研究分解失败回退 | **DANGEROUS_FALLBACK** | 研究拆解失败后用规则补全，影响后续物理帧提取质量 |
| `workflow_pipeline/pipeline.py:427` `_extract_intent_deterministic` | LLM 理解失败的整体回退路径 | **DANGEROUS_FALLBACK** | 数百行正则+中文关键词确定性提取器，作为 UNDERSTANDING 阶段的 LLM 回退。这是主链质量降级的最大单点 |
| `workflow_pipeline/pipeline.py:316` `_parse_modification` | 修改意图用正则解析 | **DANGEROUS_FALLBACK** | 无 PatchEngine，修改意图靠正则+关键词（"雷诺数""结束时间"）提取 |
| `v5_router.py:139-140, 149-150` | `_save_llm_config`/`_load_llm_config` `except Exception: pass` | **DANGEROUS_FALLBACK** | LLM 配置落盘/读盘失败被静默吞掉，可能导致重启后配置丢失而无告警 |
| `v5_router.py:843-844, 898-899` | `except TransitionError: pass` | **SAFE_RECOVERY** | 状态机转移失败忽略，非科学路径 |
| `cylinder_flow_2d/execution.py:1754` | `delta_t = 0.001  # fallback assumption` | **SAFE_RECOVERY** | 仅用于动画时间轴映射（可视化），非科学计算 |
| `cylinder_flow_2d/execution.py:1646` | `foamListTimes returned no times — trying ls fallback` | **SAFE_RECOVERY** | 时间步列举的 shell 降级，有日志 |
| `cylinder_flow_2d/execution.py:2269` `_fetch_field_data_fallback` | foamToVTK 失败后用 postProcess surfaces 降级 | **SAFE_RECOVERY** | 场数据获取降级，可视化用，失败返回 None 有日志 |
| `cylinder_flow_2d/execution.py` 多处 `except Exception`（1442, 1594, 1791, 1964, 2069, 2088, 2353, 2408, 2476, 2583） | 动画/绘图/VTK 解析失败 | **SAFE_RECOVERY** | 全部位于 Postprocessor 可视化层，失败跳过单个图/动画，不影响仿真正确性 |
| `dynamic_schema/schema_engine.py` | "using simpleFoam as system fallback" | **SAFE_RECOVERY** | 求解器推断失败回退到 simpleFo，有日志 |
| `model_runtime/` 各处 | 显式 "no silent fallback" 强制 | **正面范例** | model_runtime 层明确禁止静默回退，是规范实现 |

### 4.5 `except Exception` 在 src/fluid_scientist 的统计性结论

`except Exception` 命中遍布 `v5_router.py`（10+ 处）、`cylinder_flow_2d/execution.py`（15+ 处）、`app.py`、`experiment_spec/` 等。分类：

- **可视化/后处理层（execution.py）**：SAFE_RECOVERY，约占 60%，均有 `logger.warning/error`，失败跳过单图。
- **主链决策层（v5_router.py 的 LLM 调用）**：DANGEROUS_FALLBACK，约占 30%，静默回退继续执行。
- **配置/状态机（v5_router.py 配置持久化、TransitionError）**：SAFE_RECOVERY，约占 10%。

### 4.6 DEAD_CODE

| 位置 | 代码 | 分类 | 说明 |
|------|------|------|------|
| `experiment_spec/compilation.py:408` `compile_confirmed_spec` | 已弃用仍导出 | **DEAD_CODE** | docstring 声明 fallback 已移除；`compile_spec` 不再调用；仅保留向后兼容，主路径不可达 |
| `experiment_planning/compilers/registry.py` 中 `compile_pipe_plan` 等 | 仍活跃 | 非 DEAD | 被 `compile_plan` 与 native_compiler 复用 |

### 4.7 §6.3 结论

主链 A 存在系统性 DANGEROUS_FALLBACK：LLM 在理解、路由、研究分解、修改解析四个关键决策点失败后，全部静默回退到正则/关键词确定性路径，且不检查 `record.fallback_used`。这与链路 B（model_editing）的"强制无回退"形成鲜明对比。`StructuredOutputValidator` 的硬失败设计在主链 A 完全未被采用。

---

## 5. §6.4 参数专用补丁检查（中文关键词驱动业务逻辑）

### 5.1 搜索命令与命中

```
rg -n '仿真时间|三角形|矩形|改成水|入口速度|压力出口' src
```

命中集中在：`api/cylinder_flow_router.py`、`cylinder_flow_2d/pipeline.py`、`workflow_pipeline/pipeline.py`、`intent/conflict_resolver.py`、`api/v5_router.py`、`apps/web/v5-app.js`。

### 5.2 分类原则

测试计划要求"业务代码中出现时必须解释"。将命中分为两类：
- **PROMPT_ONLY（可接受）**：关键词仅出现在发给 LLM 的 prompt 文本/示例中，用于指导模型，不参与代码分支判定。
- **BUSINESS_LOGIC（危险，必须解释）**：关键词出现在 `if ... in text`、字段名映射、跳过逻辑等代码分支中，直接决定业务行为。

### 5.3 BUSINESS_LOGIC 命中逐项解释

| 位置 | 代码 | 关键词 | 业务行为 | 风险解释 |
|------|------|--------|----------|----------|
| `workflow_pipeline/pipeline.py:316` `_parse_modification` | 正则匹配 `"雷诺数"`、`"结束时间"`、`"终止时间"` 等驱动修改意图解析 | 雷诺数/结束时间 | 决定 `ChangeProposal` 修改哪个字段 | 用正则把自然语言映射到字段，是"参数专用补丁"典型：每加一个可改参数就要加一条正则，无法泛化，且与 `SimulationSpecPatch` 的 schema 驱动修补完全相悖 |
| `workflow_pipeline/pipeline.py:427` `_extract_intent_deterministic` | 数百行 `if "关键词" in text` 提取几何/边界/物性 | 三角形/矩形/入口速度/压力出口 等 | UNDERSTANDING 阶段 LLM 回退路径，直接生成 `PipelineState.view` | 当 LLM 失败时，这些关键词补丁接管全部理解逻辑。关键词集合是封闭的，用户用同义词或英文将不被识别，结果静默退化 |
| `api/v5_router.py:730` | `any(k in user_message.lower() for k in ("新建","另一个","new","another"))` | 新建/另一个 | 判定是否真要新建研究（覆盖 LLM 的 NEW_RESEARCH 判断） | 用中文关键词否决 LLM 分类结果，是关键词覆盖模型决策 |
| `api/v5_router.py:749` `_answer_draft_question` | `if "自由滑移" in lower or "free slip" in lower` | 自由滑移 | 返回关于自由滑移边界的固定回答 | 关键词触发硬编码答疑，无法回答未列入关键词的问题 |
| `api/cylinder_flow_router.py:1106` | `if "压力出口" in answer or "pressure" in answer.lower()` | 压力出口 | 把用户澄清答案映射为 `SemanticBoundaryType.PRESSURE_OUTLET` | 边界类型由中文关键词子串匹配决定；"压力出口"的任意变体（如"压力出"）都会误命中，非中文表述需英文兜底 |
| `api/cylinder_flow_router.py:1641-1644` | `if field_lower in ("end_time","仿真时间","delta_t","时间步长",...)` | 仿真时间/时间步长 | 跳过"可选"仿真参数的缺失校验 | 用中文字段名白名单决定哪些字段可不填，混用中英文键，维护脆弱 |
| `api/cylinder_flow_router.py:1666` | `facts_log = f"LLM提取仿真时间: {et_val}s"` | 仿真时间 | 仅日志 | 日志文本，影响低 |
| `api/cylinder_flow_router.py:1782-1810` | `INLET_VELOCITY_NULL`/`RECTANGLE_WIDTH_NULL`/`TRIANGLE_BASE_WIDTH_NULL` 等校验码 | 入口速度/矩形/三角形 | 校验 spec 字段非空 | 校验码命名含中文概念，但逻辑基于 spec 字段，可接受；问题是这些字段本身由前述关键词提取填充 |
| `api/cylinder_flow_router.py:1859-1860` | `user_says_cosine = any(kw in text_lower for kw in ["余弦",...])`；`user_says_rectangle = any(... ["矩形","rectangle",...])` | 矩形/三角形 | 语义几何一致性校验：检测用户文本是否提到某形状，与 spec 中实际形状比对 | 用关键词子串匹配做"用户说了什么"的判定，漏掉同义词即误判为一致，可能放行几何替换错误 |
| `api/cylinder_flow_router.py:1525-1526` | `f"请明确障碍物的几何形状（如：矩形、三角形、梯形、圆柱等）"` | 矩形/三角形 | 澄清提示文本 | PROMPT_ONLY，可接受 |
| `intent/conflict_resolver.py:32-39` `GEOMETRY_SYNONYMS` | `"triangle":["三角","三角形","三角障碍",...]` 等同义词表 | 三角形/矩形 | `_normalize_geometry_type` + `_find_in_text` 用于候选仲裁 | 同义词表是硬编码字典，新形状需改代码；仲裁逻辑本身设计合理（"从不静默选一边"），但词典覆盖是补丁式的 |
| `apps/web/v5-app.js` | `/^(确认\|confirm\|yes\|y\|应用\|apply\|好的\|可以)/i` | 确认/应用/好的 | 前端判定 confirm/cancel | 前端用正则拦截确认意图，与后端状态机并行，可能前后端判定不一致 |

### 5.4 PROMPT_ONLY 命中（可接受，列出备查）

| 位置 | 说明 |
|------|------|
| `api/cylinder_flow_router.py:183-208` | LLM 系统 prompt 中的几何类型提取规则与示例（"用户说'矩形'→type='rectangle'"），属 prompt 指令，不参与代码分支 |

### 5.5 §6.4 结论

中文关键词在**业务逻辑**中大量出现，集中在主链 A 的 `_extract_intent_deterministic`、`_parse_modification`，以及链路 D 的 `cylinder_flow_router` 边界/几何判定。这些是典型的"参数专用补丁"：
1. 每个可识别参数/几何/边界都对应一条硬编码关键词规则，无法泛化；
2. 同义词覆盖不全时静默降级（识别为"未提及"），与 §6.3 的 DANGEROUS_FALLBACK 叠加；
3. 与链路 B 的 schema 驱动 `SimulationSpecPatch`（由 LLM 产出结构化 patch、`PatchEngine` 校验）设计哲学直接冲突。

`改成水` 在 src 中无业务逻辑命中（仅在测试/数据样例可能出现），未发现危险用法。

---

## 6. 综合风险登记簿与整改建议

### 6.1 风险登记簿

| ID | 风险 | 涉及 § | 严重度 | 证据 |
|----|------|--------|--------|------|
| R1 | 无唯一 canonical spec，4 链 5+ spec 并存 | §6.2 | 高 | §3.2 |
| R2 | 主链 A 未用 StructuredOutputValidator，LLM 输出内联解析 | §6.1/§6.3 | 高 | `v5_router.py:688` |
| R3 | 主链 A 四决策点 DANGEROUS_FALLBACK，不检查 fallback_used | §6.3 | 高 | `v5_router.py:703,548`；`pipeline.py:427,316` |
| R4 | 主链 A 无 PatchEngine/DependencyEngine，修改靠正则+中文关键词 | §6.1/§6.4 | 高 | `pipeline.py:316` |
| R5 | 中文关键词驱动业务分支，无法泛化且静默降级 | §6.4 | 高 | §5.3 |
| R6 | draft/proposal/case_plan/compiled_case 纯内存，重启丢失 | §6.1 | 中 | `v5_router.py:85-90` |
| R7 | 能力缺失/校验失败"记录不阻断"，GENERATING 仍继续 | §6.1 | 中 | `pipeline.py:1244` |
| R8 | model-editing 链（B）编译未接线，目标 canonical 无法端到端 | §6.2 | 中 | `OpenFOAMCompiler.compile` 未被 router 调用 |
| R9 | `compile_confirmed_spec` DEAD_CODE 仍导出 | §6.3 | 低 | `compilation.py:408` |
| R10 | 前端正则 confirm/cancel 与后端状态机并行判定 | §6.4 | 低 | `v5-app.js` |

### 6.2 整改建议（优先级排序）

1. **[P0] 收敛到单一 canonical spec**：以 `SimulationStudySpec` 为唯一 canonical，将主链 A 的 `PipelineState`/`ExperimentDraft` 与 legacy `ExperimentSpec`/`ExperimentPlan` 通过既有 `LegacyMigrator` 统一迁入；废弃 `compile_confirmed_spec`。
2. **[P0] 主链 A 接入 `StructuredOutputValidator` 与 PatchEngine**：让 LLM 产出 `SimulationSpecPatch`，经 `PatchEngine.process_patch` 校验应用，替换 `_parse_modification` 正则路径。
3. **[P0] 消除主链 DANGEROUS_FALLBACK**：在 `_classify_with_llm`/`_decompose_single_study`/`_extract_intent` 中检查 `record.fallback_used`，失败时硬失败或显式向用户返回 `MODEL_UNAVAILABLE`，对齐链路 B 契约。
4. **[P1] 接线 model-editing 编译**：在 `model_editing_router` 增加 compile 端点调用 `OpenFOAMCompiler.compile(SimulationStudySpec)`，打通 B 链端到端。
5. **[P1] 持久化补齐**：将 `_draft_store`/`_proposal_store`/`_case_store` 改为落盘 repository（复用 `JsonSessionPersistence` 模式）。
6. **[P2] 关键词补丁外移**：将 `GEOMETRY_SYNONYMS` 等同义词表迁入 schema/capability 注册表，业务分支改为基于 spec 字段枚举判定，而非文本子串匹配。
7. **[P2] 能力/校验失败改为阻断**：`_stage_resolve_capabilities` 与 VALIDATING 阶段对 mandatory 缺失/校验失败应阻断 GENERATING，而非仅记录。

---

## 7. 审计方法与证据可追溯性

- 本报告所有文件路径、行号均基于当前工作区 `d:\desktop\AI FOR SCIENCE\src\fluid_scientist\**` 与 `apps\web\**` 实测。
- 搜索命令与测试计划 §6.2/§6.3/§6.4 要求一致（`rg` 等价于本工具的 Grep）。
- 全程只读：未修改任何源代码，仅创建本审计文档。

> 报告结束。


