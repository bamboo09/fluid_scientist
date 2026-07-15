# V5 智能化闭环改造架构文档

> **改造分支**: `trae/v5-intelligent-closed-loop`
> **改造目标**: 在尽量少改动现有架构的前提下，把大模型、Skill、错误诊断修复、未知能力扩展真正接入当前 cylinder-flow 生产主链，使系统从"一次 LLM JSON 补全＋规则模板执行"升级为"可审计意图理解＋确定性编译＋真实执行反馈＋受控修复＋未知能力扩展"的闭环系统。

---

## 目录

1. [改造前后流程对比](#1-改造前后流程对比)
2. [P0-P8 各阶段实现摘要](#2-p0-p8-各阶段实现摘要)
3. [实际模型调用矩阵](#3-实际模型调用矩阵)
4. [Skill 业务影响矩阵](#4-skill-业务影响矩阵)
5. [ExtensionOrchestrator 生产调用证据](#5-extensionorchestrator-生产调用证据)
6. [持久化设计](#6-持久化设计)
7. [已知风险](#7-已知风险)

---

## 1. 改造前后流程对比

### 1.1 改造前（V4 基线）

```text
用户输入
  ↓
Regex 抽取 → CylinderFlow2DExperimentSpecV1
  ↓
GLM-4-Flash JSON 补全（仅补 regex 漏掉的字段）
  ↓
规则模板编译（ObstacleFlowCompiler）
  ↓
SSH 上传到工作站
  ↓
blockMesh → snappyHexMesh → checkMesh
  ↓
Smoke Run ──失败──→ 直接返回 FAILED（无诊断、无修复、无重试）
  ↓
Full Run ──失败──→ 直接返回 FAILED
  ↓
规则计算 Cd/Cl/St
```

**核心问题**:
- regex 抽取结果优先级高于 GLM，GLM 只能补空，无法纠正错误 regex
- 4 个 Skill 只是函数调用包装器和审计记录器，不进入 Prompt，不修改 Spec，不改变 Compiler
- `ExtensionOrchestrator` 已有实现但未接入生产主链
- OpenFOAM 执行失败后无模型诊断、无 Case 修改、无重试
- smoke test 失败仍会继续 full run
- V5 核心状态使用内存字典，服务重启后流程丢失

### 1.2 改造后（V5 闭环）

```text
用户输入
  ↓
原始输入和会话持久化
  ↓
显式事实抽取器 Regex Extractor ──┐
                                 ├──→ 双候选独立保存
LLM Structured Interpreter ─────┘
  ↓
Candidate Merger / Conflict Resolver（字段级仲裁）
  ↓
Semantic Fidelity Guard（几何忠实 + 空间关系 + 相交检查 + 边界语义）
  ↓
Ambiguity & Requirement Critic（歧义检测与用户澄清）
  ↓
用户澄清（三阶段确认）
  ↓
Canonical Experiment Spec
  ↓
Skill Resolver（选择 Skills，注入 Prompt 片段，提供 Compiler Hooks）
  ↓
Capability Check（CapabilityResolver）
  ├─ 已支持
  │    ↓
  │  Deterministic Compiler（ObstacleFlowCompiler）
  │
  └─ 未支持
       ↓
     ExtensionOrchestrator（create checkpoint → extension → test → VERIFIED → resume）
  ↓
用户确认实验方案
  ↓
编译 → 静态验证 → 网格验证
  ↓
Smoke Run
  ├─ 成功 → 等待用户确认正式运行
  └─ 失败 → Error Classifier → LLM Diagnosis → Controlled Repair → Retry
  ↓
Full Run
  ├─ 成功 → 后处理与物理验证
  └─ 失败 → Error Classifier → LLM Diagnosis → Controlled Repair → Retry
  ↓
结果分析 → LLM 科学报告 + 规则指标
  ↓
持久化展示
```

**核心原则**: LLM 负责理解、仲裁、诊断和提出修改；Schema 负责契约；Compiler 负责确定性生成；Validator 和真实 OpenFOAM 负责判定正确性。

---

## 2. P0-P8 各阶段实现摘要

### P0: 可复现基线

| 项目 | 状态 | 说明 |
|------|------|------|
| 独立改造分支 | 已完成 | `trae/v5-intelligent-closed-loop` |
| 未提交修改保存 | 已完成 | `docs/audits/pre_refactor_worktree.diff`、`pre_refactor_staged.diff`、`pre_refactor_untracked.txt` |
| 运行版本指纹 | 已完成 | `GET /api/v5/runtime-info`（`src/fluid_scientist/api/runtime_info.py`） |
| 清理运行进程 | 已完成 | 工作目录 `src/fluid_scientist`，端口 8000 |

**版本指纹字段**: `repo_root`、`branch`、`commit`、`dirty`、`source_hash`、`prompt_hash`、`compiler_version`、`openfoam_distribution`、`openfoam_version`

### P1: 意图候选与冲突仲裁

| 模块 | 源文件 | 说明 |
|------|--------|------|
| 候选模型 | `src/fluid_scientist/intent/__init__.py` | `ExtractionCandidate`、`IntentCandidateSet`、`CandidateConflict`、`ResolvedField` |
| Regex 提取器 | `src/fluid_scientist/intent/conflict_resolver.py` → `RegexCandidateExtractor` | 从 pipeline 产出的 Spec 中提取 regex 候选 |
| LLM 提取器 | `src/fluid_scientist/intent/conflict_resolver.py` → `LLMCandidateExtractor` | 从 LLM JSON 响应中提取独立候选 |
| 冲突仲裁器 | `src/fluid_scientist/intent/conflict_resolver.py` → `ConflictResolver` | 字段级仲裁，6 种解决策略 |
| LLM Prompt | `src/fluid_scientist/intent/prompts.py` | Prompt A（事实提取）+ Prompt B（冲突仲裁） |

**关键改进**: regex 和 LLM 在候选阶段不再直接互相覆盖。两份结果独立保存，由 `ConflictResolver` 逐字段仲裁。

### P2: Semantic Fidelity Guard

| 检查类型 | 源文件 | 说明 |
|----------|--------|------|
| 几何忠实性 | `src/fluid_scientist/intent/semantic_fidelity_guard.py` → `_check_geometry_fidelity` | 三角不会被替换为 cosine_bell；正弦凸起不会额外创建 rectangle |
| 空间关系 | `src/fluid_scientist/intent/semantic_fidelity_guard.py` → `_check_spatial_relations` | attached_to、centered_under、贴附下壁面等空间约束保留 |
| 几何相交 | `src/fluid_scientist/intent/semantic_fidelity_guard.py` → `_check_geometry_intersections` | 圆柱与壁面/障碍物相交、障碍物超出域、矩形与底部轮廓重复 |
| 边界语义 | `src/fluid_scientist/intent/semantic_fidelity_guard.py` → `_check_boundary_semantics` | inlet/outlet 配对、周期边界成对、2D front/back 为 empty、自由出流不被错误设为 no_slip |

**执行时机**: 候选仲裁完成后、Spec 落库前、编译前，三个检查点分别执行。

### P3: Smoke 失败阻断与错误分类

| 项目 | 源文件 | 说明 |
|------|--------|------|
| Smoke 阻断 | 生产路由逻辑 | smoke FAILED → 禁止 full run → 进入错误诊断 |
| 错误分类器 | `src/fluid_scientist/repair/error_classifier.py` → `OpenFOAMErrorClassifier` | 9 类错误分类，基于正则模式匹配日志 |

### P4: 受控错误修复闭环

| 模块 | 源文件 | 说明 |
|------|--------|------|
| 修复策略 | `src/fluid_scientist/repair/repair_policy.py` → `RepairPolicy` | 3 级修复（CONFIG_ONLY → DICTIONARY_SYNTAX → PARTIAL_REGENERATION），每阶段最多 3 轮，全局最多 10 轮 |
| 上下文构建 | `src/fluid_scientist/repair/repair_context_builder.py` → `RepairContextBuilder` | 按错误类型收集最小相关文件和日志 |
| LLM 诊断 | `src/fluid_scientist/repair/llm_diagnoser.py` → `LLMDiagnoser` | LLM 输出 root_cause + fix_actions，不直接写文件 |
| 受控执行 | `src/fluid_scientist/repair/controlled_repair_executor.py` → `ControlledRepairExecutor` | 程序执行 LLM 建议的修复，每步验证 |
| 编排器 | `src/fluid_scientist/repair/repair_orchestrator.py` → `RepairOrchestrator` | 协调分类→上下文→诊断→执行→验证→重试全流程 |

**不变量**: `RETRY_WITHOUT_REPAIR` 永不被允许。每轮修复必须产生真实 diff，diff 为空则禁止重试。

### P5: Skill 真实生效

| 项目 | 源文件 | 说明 |
|------|--------|------|
| Skill Manifest | `data/skills/*.yaml` | 11 个 YAML 清单，含 stage、prompt_fragment、compiler_hooks |
| Skill Resolver | `src/fluid_scientist/skills/skill_resolver.py` → `SkillResolver` | 加载清单，按关键词/几何类型选择，构建 Prompt 注入，提供 Compiler Hooks |

**状态细分**: `REGISTERED` → `SELECTED` → `PROMPT_INJECTED` → `VALIDATOR_EXECUTED` → `COMPILER_HOOK_USED` → `OPENFOAM_VALIDATED` → `BUSINESS_OUTPUT_CHANGED`

### P6: ExtensionOrchestrator 接入

| 项目 | 源文件 | 说明 |
|------|--------|------|
| 能力检查 | `src/fluid_scientist/capabilities/capability_resolver.py` → `CapabilityResolver` | 检查 Spec 中几何/物理/观测量/边界是否全部支持 |
| 扩展编排 | `src/fluid_scientist/extensions/orchestrator.py` → `ExtensionOrchestrator` | 11 步验证管线：checkpoint → generate → static scan → component build → unit test → OpenFOAM test → smoke test → evidence → register VERIFIED → restore → re-resolve |
| 未知能力编排 | `src/fluid_scientist/capabilities/orchestrator.py` → `UnknownCapabilityOrchestrator` | 创建 ExtensionSpec 和 PipelineCheckpoint |

### P7: 持久化

| 项目 | 源文件 | 说明 |
|------|--------|------|
| SQLAlchemy 表定义 | `src/fluid_scientist/db.py` | 11 张表：projects、workflow_snapshots、operations、approvals、external_jobs、audit_events、experiment_plans、compiled_experiments、experiment_specs、candidate_templates、generated_case_drafts |
| SQL 仓储 | `src/fluid_scientist/adapters/sql_repository.py` → `SQLWorkflowRepository` | 替代内存字典，支持重启恢复 |
| 扩展检查点 | `src/fluid_scientist/capabilities/orchestrator.py` → `PipelineCheckpoint` | checkpoint 持久化到文件系统 |

### P8: LLM 科学报告和物理验证

| 项目 | 源文件 | 说明 |
|------|--------|------|
| 结果摘要构建 | `src/fluid_scientist/analysis/llm_report.py` → `ResultSummaryBuilder` | 从 forceCoeffs 提取 Cd/Cl，FFT 估算 St |
| 物理验证 | `src/fluid_scientist/analysis/llm_report.py` → `PhysicsValidator` | Cd/St 与经验公式对比、NaN 检查、网格充分性检查 |
| LLM 报告生成 | `src/fluid_scientist/analysis/llm_report.py` → `LLMReportGenerator` | LLM 基于结构化摘要生成报告，不编造数据 |

---

## 3. 实际模型调用矩阵

以下表格展示 V5 闭环中所有使用 LLM 的阶段，以及调用参数和来源代码路径。

| 阶段 | LLM 用途 | Prompt 名称 | 源文件 | temperature | response_format | 说明 |
|------|----------|-------------|--------|-------------|-----------------|------|
| 意图提取 | 事实和实体提取（独立候选） | `of_fact_extraction` | `src/fluid_scientist/intent/prompts.py` → `LLM_FACT_EXTRACTION_PROMPT` | 0 | JSON | Prompt A：只提取用户明确表达的内容，不补默认值，不映射未知几何 |
| 冲突仲裁 | 字段级冲突仲裁 | `of_conflict_arbitration` | `src/fluid_scientist/intent/prompts.py` → `LLM_CONFLICT_ARBITRATION_PROMPT` | 0 | JSON | Prompt B：仅在 regex 和 LLM 冲突时调用，输出 resolved_fields + clarification_questions |
| Skill Prompt 注入 | 领域知识注入 | — | `src/fluid_scientist/skills/skill_resolver.py` → `build_prompt_injection` | — | — | 非独立 LLM 调用，将 Skill 的 prompt_fragment 注入到 LLM system prompt 中 |
| 错误诊断 | OpenFOAM 错误根因分析 | `of_error_diagnosis` | `src/fluid_scientist/repair/llm_diagnoser.py` → `_DIAGNOSIS_SYSTEM_PROMPT` | 0 | JSON | 输出 root_cause + repair_actions，程序执行修复，模型不直接写文件 |
| 报告生成 | 科学报告生成 | `scientific_report` | `src/fluid_scientist/analysis/llm_report.py` → `_REPORT_SYSTEM_PROMPT` | 0 | JSON | 基于结构化结果摘要和物理验证生成报告，所有数值来自实际仿真 |

### 模型调用参数约束

```python
# 所有生产环境 LLM 调用必须显式配置
temperature = 0          # 确定性输出
response_format = JSON   # 结构化输出
timeout = 显式指定        # 不得使用默认值
retry = 显式指定          # 不得使用默认值
model_name = 显式指定     # 不得使用默认值
provider = 显式指定       # 生产环境禁止 provider=mock
```

**生产环境 provider 校验**: 启动时发现 `provider=mock` 直接失败，错误码 `PRODUCTION_LLM_PROVIDER_INVALID`。

### LLM 失败行为

- 保存错误日志
- 标记 `llm_status=FAILED`
- 前端显示"模型理解服务暂时失败"
- 允许用户选择"仅使用规则草案继续"
- 不得用户无感知地自动降级
- 只有用户明确选择规则模式才继续

---

## 4. Skill 业务影响矩阵

以下表格展示每个 Skill 的阶段、Prompt 片段作用、Compiler Hooks 以及业务影响。

| Skill ID | 阶段 | Prompt 片段作用 | Compiler Hooks | 业务影响 | 源文件 |
|----------|------|-----------------|----------------|----------|--------|
| `fluid.intent_to_spec` | intent | 提取用户明确物理参数、保留空间关系、标记歧义边界 | — | 意图提取阶段注入规则，防止 LLM 补默认值 | `data/skills/fluid.intent_to_spec.yaml` |
| `fluid.geometry_reasoning` | geometry | 三角→triangle、正弦→half_sine、未知→unknown、禁止几何替换 | `enforce_semantic_type`、`prevent_geometry_substitution` | 防止 triangle 变成 cosine_bell，防止 bump 被同时创建为 rectangle | `data/skills/fluid.geometry_reasoning.yaml` |
| `fluid.spatial_reasoning` | geometry | 验证空间关系（正下方、贴附、正中央）的语义保持 | — | 确保障碍物中心与圆柱对齐等空间约束 | `data/skills/fluid.spatial_reasoning.yaml` |
| `fluid.boundary_mapping` | geometry | 边界条件语义映射（自由出流→slip/symmetry、无滑移→noSlip） | — | 防止上边界自由出流被错误设为 no_slip_wall | `data/skills/fluid.boundary_mapping.yaml` |
| `fluid.physics_derivation` | physics | 运动黏度推导 nu=U*D/Re，Re/速度/直径可互相推导 | — | 防止可推导参数被误标为 missing | `data/skills/fluid.physics_derivation.yaml` |
| `fluid.solver_selection` | solver | 根据流动类型选择求解器（层流/湍流 k-omega SST） | — | 确保求解器与 Reynolds 数匹配 | `data/skills/fluid.solver_selection.yaml` |
| `fluid.mesh_strategy` | mesh | 网格策略选择（blockMesh + snappyHexMesh STL） | — | 网格生成策略受 Skill 影响 | `data/skills/fluid.mesh_strategy.yaml` |
| `fluid.metric_spec_builder` | postprocess | forceCoeffs 配置（patch 名、方向、参考量） | — | 确保 Cd/Cl/St 观测量正确配置 | `data/skills/fluid.metric_spec_builder.yaml` |
| `fluid.postprocess_config` | postprocess | 后处理配置（流线、涡量场、截面平均速度） | — | 后处理输出受 Skill 影响 | `data/skills/fluid.postprocess_config.yaml` |
| `fluid.error_diagnosis` | repair | NaN/CFL/边界不匹配/网格/语法错误诊断规则 | — | 修复阶段注入诊断知识，防止改变用户物理设置 | `data/skills/fluid.error_diagnosis.yaml` |
| `fluid.report_generation` | report | 报告结构规则（实验概述→网格→数值→结果→验证→结论） | — | 报告生成阶段注入结构约束 | `data/skills/fluid.report_generation.yaml` |

### Skill 状态追踪

每个 Skill 的生命周期状态必须细分为以下 7 个阶段（不再只返回 `PASSED`）:

```text
REGISTERED        → 清单已加载
SELECTED           → 被选中用于当前上下文
PROMPT_INJECTED    → prompt_fragment 已注入 LLM prompt
VALIDATOR_EXECUTED → validator 已执行
COMPILER_HOOK_USED → compiler_hook 已影响编译
OPENFOAM_VALIDATED → OpenFOAM 验证已通过
BUSINESS_OUTPUT_CHANGED → 业务输出确实因 Skill 而改变
```

### Skill Prompt 注入证据

模型 trace 中必须保存:

```json
{
  "selected_skills": ["fluid.geometry_reasoning", "fluid.intent_to_spec"],
  "injected_fragments": ["几何推理规则", "意图转换规则"],
  "prompt_hash": "sha256:..."
}
```

**源文件**: `src/fluid_scientist/skills/skill_resolver.py` → `build_prompt_injection()` 方法

---

## 5. ExtensionOrchestrator 生产调用证据

### 5.1 生产调用路径

```text
confirm-plan（用户确认实验方案）
  ↓
CapabilityResolver.resolve(canonical_spec)        ← src/fluid_scientist/capabilities/capability_resolver.py
  ↓
  ├─ all_supported = true → 直接进入 Deterministic Compiler
  │
  └─ all_supported = false（存在 unsupported/extendable）
       ↓
     创建 Extension Checkpoint（PipelineCheckpoint）
       ↓
     ExtensionOrchestrator.execute(checkpoint)      ← src/fluid_scientist/extensions/orchestrator.py
       ↓
     11 步验证管线
       ↓
     全部成功 → 注册 VERIFIED → 恢复原任务
```

### 5.2 CapabilityResolver 检查内容

**源文件**: `src/fluid_scientist/capabilities/capability_resolver.py`

```python
class CapabilityResolver:
    # 已支持几何类型
    SUPPORTED_GEOMETRY = {"cylinder", "triangle", "rectangle", "cosine_bell", "half_sine", "gaussian", "flat"}
    # 已支持物理模型
    SUPPORTED_PHYSICS = {"incompressible_newtonian", "laminar", "turbulent_k_omega_sst"}
    # 已支持观测量
    SUPPORTED_OBSERVABLES = {"cylinder_drag", "cylinder_lift", "wake_shedding_frequency", ...}
    # 已支持边界类型
    SUPPORTED_BOUNDARIES = {"uniform_velocity_inlet", "pressure_outlet", "no_slip_wall", ...}
```

`check()` 方法返回 `CapabilityCheckResult`:
```json
{
  "all_supported": false,
  "supported": ["geometry:cylinder", "physics:incompressible_newtonian"],
  "unsupported": ["boundary:left:shear_stress"],
  "extendable": ["observable:custom_metric"],
  "checkpoint_created": true,
  "extension_triggered": true,
  "extension_result": {...}
}
```

### 5.3 ExtensionOrchestrator 11 步验证管线

**源文件**: `src/fluid_scientist/extensions/orchestrator.py` → `_run_pipeline()`

| 步骤 | 状态 | 说明 | 失败状态 |
|------|------|------|----------|
| 1. create_checkpoint | `checkpointed` | 保存 checkpoint.json，记录当前注册能力 | — |
| 2. generate_candidate | `candidate_generated` | 物化 ExtensionSpec，写 implementation.py | `unsupported_physics` |
| 3. static_security_scan | `static_validated` | 扫描危险模式（subprocess、eval、codeStream 等） | `extension_validation_failed` |
| 4. build_candidate_component | `component_built` | CompileReadinessValidator 静态 case 校验 | `extension_validation_failed` |
| 5. atomic_unit_test | `unit_tested` | 安全沙箱执行单元测试 | `extension_validation_failed` |
| 6. minimal_openfoam_test | `openfoam_tested` | blockMesh → checkMesh → foamRun 单步运行 | `environment_blocked` / `extension_validation_failed` |
| 7. target_case_smoke_test | `smoke_tested` | 目标 case 副本 smoke 测试 | `extension_validation_failed` |
| 8. save_evidence | `evidence_saved` | 保存 verification_artifact.json + test_manifest.json | — |
| 9. register_verified_capability | `registered` | 注册到 CapabilityRegistry，状态 `VERIFIED` | `extension_validation_failed` |
| 10. restore_original_case | `restored` | 从备份恢复原始 case 目录 | — |
| 11. re_resolve | `re_resolved` | 重新分析能力需求图 | — |

### 5.4 Extension 类型优先级

```text
1. CONFIG_EXTENSION        → 修改 OpenFOAM 字典配置
2. COMPILER_HOOK_EXTENSION → 添加编译器钩子
3. VALIDATOR_EXTENSION     → 添加验证器
4. POSTPROCESS_EXTENSION   → 添加后处理器
5. CODE_EXTENSION          → 生成最小代码 Patch（仅在其他类型无法解决时）
```

**原则**: 能够用配置解决时禁止生成代码。模型只能输出 unified diff，禁止输出完整仓库或重写整个 Compiler。

### 5.5 扩展验证失败处理

失败时必须保存:
- patch 文件
- 测试日志
- OpenFOAM 日志
- 模型调用记录
- evidence manifest

**不能伪注册**。只有全部验证通过才标记 `VERIFIED`。

### 5.6 不变量: OpenFOAM 不可用时的行为

当 OpenFOAM 在运行环境中不可用时（`_detect_openfoam()` 返回 False）:
- 需要运行时验证的 spec → 状态 `environment_blocked`
- 仅需静态验证的 spec → 仍可注册

**源文件**: `src/fluid_scientist/extensions/orchestrator.py` → `_detect_openfoam()` 和 `_step_minimal_openfoam_test()`

---

## 6. 持久化设计

### 6.1 从内存字典迁移到 SQLite

V4 核心状态使用内存字典，服务重启后流程丢失。V5 迁移到 SQLAlchemy + SQLite（也可支持 PostgreSQL）。

**源文件**: `src/fluid_scientist/db.py`（表定义）、`src/fluid_scientist/adapters/sql_repository.py`（仓储实现）

### 6.2 SQLite 表结构

| 表名 | `__tablename__` | 主键 | 用途 | 源文件 |
|------|-----------------|------|------|--------|
| projects | `projects` | `project_id` | 项目根表 | `db.py` → `ProjectRow` |
| workflow_snapshots | `workflow_snapshots` | `project_id` | 工作流快照（版本化） | `db.py` → `WorkflowSnapshotRow` |
| operations | `operations` | `operation_id` | 操作记录（类型+项目+输入摘要唯一约束） | `db.py` → `OperationRow` |
| approvals | `approvals` | `id`（自增） | 确认门记录（gate、审批人、版本） | `db.py` → `ApprovalRow` |
| external_jobs | `external_jobs` | `id`（自增） | 外部 job 绑定（项目+case 唯一） | `db.py` → `ExternalJobRow` |
| audit_events | `audit_events` | `event_id` | 审计事件 | `db.py` → `AuditEventRow` |
| experiment_plans | `experiment_plans` | `plan_id` | 实验方案（provider、model、plan_json） | `db.py` → `ExperimentPlanRow` |
| compiled_experiments | `compiled_experiments` | `id`（自增） | 编译产物归档（experiment_id+plan_version 唯一） | `db.py` → `CompiledExperimentRow` |
| experiment_specs | `experiment_specs` | `experiment_id` | 实验 Spec（版本化，含 status 索引） | `db.py` → `ExperimentSpecRow` |
| candidate_templates | `candidate_templates` | `candidate_id` | 候选模板（状态机驱动） | `db.py` → `CandidateTemplateRow` |
| generated_case_drafts | `generated_case_drafts` | `draft_id` | 生成的 Case 草稿（plan_id+plan_version+version 唯一） | `db.py` → `GeneratedCaseDraftRow` |

### 6.3 关键持久化设计点

#### 会话管理

一会话一个当前草案:

```text
session_id → current_spec_id → current_spec_version
```

用户修改时更新同一个流程，不新建无关联流程。

**源文件**: `src/fluid_scientist/adapters/sql_repository.py` → `replace_experiment_spec()` 方法

#### Spec 版本化

每次修改保存:
```text
version          → 版本号递增
parent_version   → 父版本
change_summary   → 修改摘要
changed_fields   → 变更字段列表
source_message_id → 来源消息 ID
created_at       → 创建时间
```

**源文件**: `src/fluid_scientist/adapters/sql_repository.py` → `ExperimentSpecRow`

#### 重启恢复

服务重启后必须能恢复:
- 当前会话
- 当前草案
- 已确认方案
- 编译结果
- job 状态
- 结果
- 报告
- repair history

**源文件**: `src/fluid_scientist/adapters/sql_repository.py` → `list_interrupted_operations()` 方法

#### 扩展检查点持久化

```text
pipeline_checkpoint.json           → PipelineCheckpoint（session_id、study_id、pipeline_stage、哈希链）
unknown_capability_extensions.json → ExtensionRunRecord 列表
```

**源文件**: `src/fluid_scientist/capabilities/orchestrator.py` → `_persist()` 方法

### 6.4 数据完整性保障

- SQLite 外键约束已启用（`PRAGMA foreign_keys=ON`）
- 归档 SHA256 校验（`archive_sha256` 字段）
- 并发更新检测（`ConcurrentUpdateError`、乐观锁版本号）
- 不可变产物冲突检测（`ExperimentArtifactConflict`）

**源文件**: `src/fluid_scientist/adapters/sql_repository.py` → `_enable_sqlite_foreign_keys()`、`_validate_generated_draft_payload()`

---

## 7. 已知风险

### 7.1 OpenFOAM 在测试环境中不可用

**风险描述**: 当前测试环境（本地开发机）未安装 OpenFOAM Foundation v13，`_detect_openfoam()` 返回 False。

**影响**:
- `ExtensionOrchestrator` 的 Step 6（minimal OpenFOAM test）和 Step 7（target case smoke test）无法执行，需要运行时验证的扩展会进入 `environment_blocked` 状态
- `ControlledRepairExecutor` 的 `_validate_repair()` 无法执行远程验证（依赖工作站 SSH 连接）
- Test F（可控 patch 错误修复）和 Test G（数值发散修复）需要工作站连接
- 真实 OpenFOAM 端到端测试（Test A-D、H）需要在工作站环境中执行

**缓解措施**:
- 单元测试使用录制响应和 mock executor，验证逻辑正确性
- `ExtensionOrchestrator` 支持 `run_openfoam=False` 参数，静态验证仍可执行
- 生产部署时确保工作站 SSH/SCP 连接和 OpenFOAM Foundation v13 环境

**相关源文件**:
- `src/fluid_scientist/extensions/orchestrator.py` → `_detect_openfoam()`
- `src/fluid_scientist/repair/controlled_repair_executor.py` → `_validate_repair()`

### 7.2 真实 GLM 调用需要 API Key

**风险描述**: 生产环境 LLM 调用需要配置 GLM API Key。如果 Key 未配置或过期，所有 LLM 依赖的功能将降级。

**影响**:
- 意图提取（Prompt A）失败 → 前端显示"模型理解服务暂时失败"，用户可选择规则模式
- 冲突仲裁（Prompt B）失败 → 冲突字段进入 `NEEDS_CLARIFICATION`，需要用户手动澄清
- 错误诊断（LLMDiagnoser）失败 → 返回 `"LLM client not available"`，修复流程无法自动诊断
- 报告生成（LLMReportGenerator）失败 → 降级为规则报告（`report_source = "rule_based"`）

**缓解措施**:
- API Key 从环境变量读取，不硬编码到 `data/llm_config.json`
- 所有日志不得输出 Key
- 生产环境启动时校验 `provider != mock`，否则直接失败
- LLM 失败时保存错误并标记 `llm_status=FAILED`，不静默降级
- 报告生成有规则降级路径（`_rule_based_report()`）

**相关源文件**:
- `src/fluid_scientist/intent/prompts.py` → LLM Prompt 定义
- `src/fluid_scientist/repair/llm_diagnoser.py` → `LLMDiagnoser.diagnose()` 异常处理
- `src/fluid_scientist/analysis/llm_report.py` → `LLMReportGenerator._call_llm_for_report()` 异常处理

### 7.3 ExtensionOrchestrator 生产接入尚未完全打通

**风险描述**: `CapabilityResolver.extend()` 方法中对 `ExtensionOrchestrator` 的调用目前记录了 `"ExtensionOrchestrator connected but extension specs not yet generated"`，实际 ExtensionSpec 的创建和执行链路仍需在生产路由中完整接入。

**影响**:
- Test H（未知能力）需要完整的 `CapabilityResolver → ExtensionOrchestrator → VERIFIED → resume` 端到端链路
- 当前 `extend()` 方法返回的 `extension_result.success = False`

**缓解措施**:
- `ExtensionOrchestrator` 本身的 11 步管线已完整实现并经过单元测试
- `UnknownCapabilityOrchestrator` 可创建 ExtensionSpec 和 PipelineCheckpoint
- 需要在生产路由（`src/fluid_scientist/api/cylinder_flow_router.py`）中接入 `CapabilityResolver.extend()` 调用

**相关源文件**:
- `src/fluid_scientist/capabilities/capability_resolver.py` → `extend()` 方法
- `src/fluid_scientist/extensions/orchestrator.py` → `execute()` 方法
- `src/fluid_scientist/capabilities/orchestrator.py` → `orchestrate()` 方法

### 7.4 修复执行依赖远程工作站

**风险描述**: `ControlledRepairExecutor._apply_single_fix()` 中的 CONFIG_ONLY 和 DICTIONARY_SYNTAX 级别修复通过 SSH `sed` 命令修改远程文件，依赖工作站 SSH 连接。

**影响**:
- 无工作站连接时修复无法执行
- `sed` 命令中的特殊字符转义可能在边缘情况下失败

**缓解措施**:
- 修复失败时记录 `attempt.fix_applied` 和 `attempt.error_log`
- `RepairPolicy` 确保不会无限重试

**相关源文件**:
- `src/fluid_scientist/repair/controlled_repair_executor.py` → `_apply_single_fix()`
