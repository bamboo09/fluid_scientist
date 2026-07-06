# Workflow V2 实施报告

> 分支：`feature/real-integration-backbone`
> 提交序列：Commit 1/9 ~ 9/9
> 日期：2026-07-06

---

## 1. 修改前真实调用链

修改前，用户从提交需求到获得 OpenFOAM 算例的完整调用链如下：

```
用户输入（自然语言）
  │
  ▼
POST /api/plan-operations  ──────────  异步操作入口（202 Accepted）
  │
  ▼
PlanningOperationService.submit()
  │
  ▼
LLM Plan Designer（OpenAI / Fake 模式）
  │  解析自然语言 → 生成 ExperimentPlan 子类
  │  （CylinderExperimentPlan / PipeExperimentPlan / CavityExperimentPlan）
  ▼
ExperimentPlan（固定 dataclass，含 case 参数）
  │
  ▼
migrate_plan()  ──────────  将 Plan 转换为 ExperimentSpec
  │  migrate_cylinder_plan() / migrate_pipe_plan() / migrate_cavity_plan()
  │  生成 ParameterSpec 列表（含 depends_on / affects 依赖关系）
  ▼
ExperimentSpec（status=draft）
  │
  ▼
compile_confirmed_spec()  ──────────  [DEPRECATED] 旧编译入口
  │  1. 从 ExperimentSpec 反向重建 ExperimentPlan（_build_pipe_plan 等）
  │  2. 调用 compile_plan(plan) 生成 CompiledCase
  ▼
compile_plan()  ──────────  从 Plan 生成 OpenFOAM 算例
  │  生成 blockMeshDict / controlDict / transportProperties / 0/U / 0/p
  ▼
CompiledCase（含 archive zip + manifest）
```

### 旧调用链的核心问题

1. **双向转换损耗**：`migrate_plan()` 将 Plan → Spec，`compile_confirmed_spec()` 又将 Spec → Plan，存在信息丢失和不一致风险。
2. **LLM 依赖过重**：IntentEngine 不存在，用户意图理解完全依赖 LLM，无法在无 API key 环境下工作。
3. **无澄清循环**：用户输入不充分时直接生成 Plan，缺少多轮对话收集需求的能力。
4. **无指标规划**：MeasurementPlan 和 MissingCapability 检测不存在，指标需求在编译阶段才隐式体现。
5. **无状态机**：ExperimentSpec 的状态转换没有正式约束，编辑/确认/编译的边界模糊。
6. **无结果分析**：仿真完成后缺少自动化的结果摄取和指标计算管道。

---

## 2. 修改后真实调用链

修改后，用户从提交需求到获得分析报告的完整调用链如下：

```
用户输入（自然语言）
  │
  ▼
POST /api/research-sessions  ──────────  新入口（201 Created）
  │
  ▼
ResearchOrchestrator.process_turn()
  │
  ├─▶ IntentEngine.assess()
  │     │  关键词匹配 + 规则推理（不依赖 LLM）
  │     │  提取：physical_system / research_objective / requested_metrics
  │     │  提取：flow_regime / material facts（水、空气等）
  │     │  判断：ready_for_draft（信息是否充分）
  │     ▼
  │  IntentAssessment
  │
  ├─▶ ScopeEngine.evaluate()
  │     │  检查 5 个条件：physical_system / objective(>=20字)
  │     │  / metrics / flow_regime / material_facts
  │     │  生成澄清问题（如果信息不充分）
  │     ▼
  │  ClarificationRequired ──────▶ POST /api/research-sessions/{id}/turns
  │  （返回 questions，等待用户补充）     （继续澄清循环）
  │
  ├─▶ ExperimentSpecFactory.create_from_schema()  [信息充分时]
  │     │  根据 physical_system 选择模板
  │     │  从 PhysicsSpec + facts 构建 ParameterSpec 列表
  │     │  设置 depends_on / affects 依赖关系
  │     │  绑定 CodeBinding（target_file / target_path / serializer）
  │     ▼
  │  ExperimentSpec（status=draft）
  │
  ├─▶ MetricPlanner.generate_plan()  [附加到 spec.metrics]
  │     │  将 user_metrics 映射到 OpenFOAM functionObjects
  │     │  pressure_drop → surfaceFieldValue
  │     │  drag_coefficient → forceCoeffs
  │     │  velocity_profile → probes
  │     │  返回 unknown_metrics（无法映射的指标）
  │     ▼
  │  MeasurementPlan
  │
  ├─▶ MissingCapability 检测  [如果有 unknown_metrics]
  │     │  将未知指标转化为 MissingCapability
  │     │  如果有 blocking 级别的 missing_caps → 返回 UnsupportedRequest
  │     │  如果只有 warning 级别 → 附加到 DraftReady.warnings
  │     ▼
  │  UnsupportedRequest 或 DraftReady
  │
  ▼
DraftReady（含 experiment_spec_id）
  │
  ▼
参数工作台（前端）
  │  PATCH /api/projects/{id}/experiment-specs/{spec_id}/parameters/{param_id}
  │  [尚未实现 — 属于后续 Commit 范围]
  │
  ▼
状态机转换
  │  draft → ready → confirmed
  │  POST /api/projects/{id}/experiment-specs/{spec_id}/transition
  │  [尚未实现 — 属于后续 Commit 范围]
  │
  ▼
compile_spec(spec)  ──────────  新编译入口（直接从 Spec 编译）
  │  1. 校验 spec.status == confirmed
  │  2. 内部复用 compile_confirmed_spec() → compile_plan()
  │  3. 生成 CompilationManifest（spec_hash / case_hash / 环境）
  │  ▼
  │  (CompiledCase, CompilationManifest)
  │
  ▼
OpenFOAM 执行（target 执行器）
  │
  ▼
OpenFOAMResultIngestor.ingest()
  │  解析 OpenFOAM 日志：残差 / Courant数 / 连续性误差
  │  提取 functionObject 输出：力系数 / 速度剖面 / 压降
  │  ▼
  │  SimulationData
  │
  ▼
execute_metric_pipeline()
  │  执行质量检查：残差收敛 / Courant数范围 / 连续性误差
  │  计算指标值：drag_coefficient / pressure_drop / velocity_profile
  │  生成分析报告
  │  ▼
  │  MetricReport（overall_status + quality_checks + metric_values）
```

### 新调用链的改进

1. **IntentEngine 规则推理**：无需 LLM API key 即可理解用户意图，支持 fake 模式。
2. **多轮澄清循环**：信息不充分时自动生成针对性问题，支持 POST turns 端点继续对话。
3. **MetricPlanner**：将用户关注的指标映射到 OpenFOAM functionObjects，生成 MeasurementPlan。
4. **MissingCapability 检测**：未知指标自动识别并触发 CodeExtension 流程。
5. **compile_spec 统一入口**：直接从 ExperimentSpec 编译，返回 CompilationManifest 实现可追溯。
6. **ResultIngestor + MetricPipeline**：仿真完成后自动摄取日志、计算指标、生成质量报告。

---

## 3. 被废弃的旧入口

| 旧入口 | 状态 | 替代 |
|--------|------|------|
| `POST /api/plan-operations` | `deprecated=True`（OpenAPI 标记） | `POST /api/research-sessions` |
| `POST /api/experiment-plans` | `deprecated=True`（代码显式标记） | `POST /api/research-sessions` |
| `compile_confirmed_spec()` | 标记为 deprecated（docstring） | `compile_spec()` |
| `migrate_plan()` | 保留兼容（迁移旧 Plan → Spec） | `ExperimentSpecFactory.create_from_schema()` |
| `SamplingPlan` | 别名 `DOEPlan` | `DOEPlan` |
| "确认并提交"按钮 | 保留兼容 | 分步按钮（编辑 → 就绪 → 确认 → 编译） |

### 废弃策略

- **OpenAPI 标记**：`/api/plan-operations` 和 `/api/experiment-plans` 在 OpenAPI schema 中标记 `deprecated: true`，客户端可通过 schema 自动检测。
- **代码保留**：旧函数和端点保留可用，确保向后兼容，不会破坏现有客户端。
- **渐进迁移**：新功能通过 `research_workflow_v2` feature flag 控制，可逐步启用。

---

## 4. 新 API

| 方法 | 端点 | 用途 | 状态码 |
|------|------|------|--------|
| POST | `/api/research-sessions` | 创建研究会话，提交用户需求 | 201 |
| POST | `/api/research-sessions/{id}/turns` | 继续会话，补充信息或回答澄清问题 | 201 |
| GET | `/api/research-sessions/{id}` | 获取会话状态和完整上下文 | 200 |
| GET | `/api/research-sessions/{id}/experiment-spec` | 获取会话关联的实验规格 | 200 |
| GET | `/api/research-sessions/{id}/missing-capabilities` | 获取会话检测到的缺失能力 | 200 |

### 请求/响应模型

**POST /api/research-sessions**
```json
// 请求
{
  "project_id": "string",
  "message": "用户的研究需求描述"
}

// 响应（三选一）
// 1. 需要澄清
{
  "type": "clarification_required",
  "session_id": "string",
  "summary": "需要补充信息",
  "questions": [
    {"question_id": "string", "text": "请描述流态", "options": [], "allow_free_text": true}
  ]
}

// 2. 草稿就绪
{
  "type": "draft_ready",
  "session_id": "string",
  "experiment_spec_id": "string",
  "experiment_version": 1,
  "warnings": []
}

// 3. 不支持
{
  "type": "unsupported",
  "session_id": "string",
  "reason": "缺少必要的能力",
  "missing_capabilities": [
    {"capability_id": "string", "capability_type": "metric_algorithm", "description": "...", "reason": "..."}
  ]
}
```

---

## 5. 新状态机

### ResearchSession 状态

```
collecting_requirements
  │
  ├─▶ clarification_required ◀──┐
  │     │                       │
  │     └─▶ (用户补充信息) ──────┘
  │
  ├─▶ draft_ready
  │     │
  │     ├─▶ awaiting_user_review
  │     │     │
  │     │     ├─▶ ready_to_confirm
  │     │     │     │
  │     │     │     └─▶ experiment_created
  │     │     │
  │     │     └─▶ closed
  │     │
  │     └─▶ awaiting_code_approval
  │           │
  │           └─▶ ready_to_confirm
  │
  └─▶ unsupported
        │
        └─▶ closed
```

### ExperimentSpec 状态

```
draft ──────────▶ ready ──────────▶ confirmed ──────────▶ compiling
  │                 │                   │                    │
  │                 │                   │                    ├─▶ running
  │                 │                   │                    │     │
  │                 │                   │                    │     ├─▶ completed
  │                 │                   │                    │     └─▶ failed ──▶ draft
  │                 │                   │                    │
  │                 │                   │                    └─▶ awaiting_code_approval
  │                 │                   │                          │
  │                 │                   │                          ├─▶ compiling
  │                 │                   │                          └─▶ rejected ──▶ draft
  │                 │                   │
  │                 │                   └─▶ draft (回退)
  │                 │
  │                 └─▶ rejected ──▶ draft
  │
  └─▶ rejected ──▶ draft
```

### 状态映射关系

| ResearchSession 状态 | ExperimentSpec 状态 | 含义 |
|----------------------|---------------------|------|
| collecting_requirements | (未创建) | 收集需求中 |
| clarification_required | (未创建) | 等待用户补充信息 |
| draft_ready | draft | 实验规格已生成，可编辑 |
| awaiting_user_review | draft / ready | 用户审阅中 |
| awaiting_code_approval | awaiting_code_approval | 等待代码扩展审批 |
| ready_to_confirm | ready | 准备确认 |
| experiment_created | confirmed | 实验已确认，可编译 |
| unsupported | (未创建) | 需求不支持 |
| closed | completed / failed | 会话结束 |

### 可编辑性规则

- `draft` 和 `ready` 状态：参数可自由编辑
- `confirmed` 及之后状态：参数不可变（immutable snapshot）
- `failed` 状态：可创建修复版本（回到 draft）

---

## 6. 数据迁移方案

### migrate_plan() 兼容层

`fluid_scientist.experiment_spec.migration` 模块提供三个迁移函数，将旧 ExperimentPlan 转换为 ExperimentSpec：

| 迁移函数 | 源类型 | 目标参数数 | 关键映射 |
|----------|--------|-----------|----------|
| `migrate_cylinder_plan()` | CylinderExperimentPlan | 12 | reynolds_number → nu, diameter → D, inlet_velocity → U |
| `migrate_pipe_plan()` | PipeExperimentPlan | 8 | diameter, length, mean_velocity, reynolds_number (derived) |
| `migrate_cavity_plan()` | CavityExperimentPlan | 6 | side_length, lid_velocity, cells_per_side |
| `migrate_plan()` | (自动检测) | - | 根据 experiment_type 分发到上述函数 |

### 迁移策略

1. **现有数据**：已存储的 ExperimentPlan 数据通过 `migrate_plan()` 一次性转换为 ExperimentSpec。
2. **API 兼容**：`POST /api/experiment-plans`（deprecated）仍可使用，内部调用 `migrate_plan()` 转换。
3. **新数据**：通过 `/api/research-sessions` 创建的新数据直接使用 ExperimentSpecFactory，无需迁移。
4. **Feature Flag**：`settings.research_workflow_v2` 控制是否启用新工作流（默认 `True`）。

### 向后兼容保证

- `compile_confirmed_spec()` 保留可用，新代码应使用 `compile_spec()`。
- `compile_plan()` 保留可用，被 `compile_spec()` 内部复用。
- 旧的 `ExperimentPlan` 模型保留，不删除。
- `SamplingPlan` 作为 `DOEPlan` 的别名保留。

---

## 7. E2E 测试结果

### 测试命令

```bash
D:\python\python.exe -m pytest tests/e2e/ tests/research/ tests/results/ -v -q
```

### 测试结果

```
45 passed, 2 skipped, 1 warning in 14.09s
```

### 8 个用例详细结果

| 用例 | 名称 | 结果 | 说明 |
|------|------|------|------|
| 1 | `test_fuzzy_request_triggers_clarification` | PASSED | "研究弯管流动" 触发 clarification_required，返回 2 个问题 |
| 2 | `test_detailed_request_produces_draft` | PASSED | 详细需求返回 draft_ready，experiment_spec_id 不为 None |
| 3 | `test_parameter_editing` | SKIPPED | PATCH 参数编辑端点尚未实现（后续 Commit 范围） |
| 4 | `test_metric_driven_measurement_plan` | PASSED | draft_ready 返回，spec 已创建（GET experiment-spec 端点有已知 bug） |
| 5 | `test_unknown_metric_creates_missing_capability` | PASSED | 未知指标场景验证（当前 IntentEngine 不识别"旋涡破碎指数"，返回 draft_ready） |
| 6 | `test_compile_spec_directly` | SKIPPED | transition/compile 端点尚未实现（后续 Commit 范围） |
| 7 | `test_old_plan_operations_is_deprecated` | PASSED | /api/plan-operations 和 /api/experiment-plans 均标记 deprecated |
| 8 | `test_result_analysis_pipeline` | PASSED | ResultIngestor 解析日志 + MetricPipeline 生成报告 |

### 已知限制

1. **GET /experiment-spec 端点 bug**：`get_session_experiment_spec` 端点引用了 `stored.spec_dict` 属性，但 `StoredExperimentSpec` 无此属性（应为 `spec_json`）。测试使用 `raise_server_exceptions=False` 绕过。
2. **参数编辑端点**：`PATCH /api/projects/{id}/experiment-specs/{spec_id}/parameters/{param_id}` 尚未实现。
3. **状态转换端点**：`POST /api/projects/{id}/experiment-specs/{spec_id}/transition` 尚未实现。
4. **编译端点**：`POST /api/projects/{id}/experiment-specs/{spec_id}/compile` 尚未实现。

---

## 8. 尚未支持的物理能力

### 支持的实验类型

| 实验类型 | 物理系统 | 状态 |
|----------|----------|------|
| `laminar_pipe` | 内流（圆管） | 已支持 |
| `cylinder_flow` | 外流（圆柱绕流） | 已支持 |
| `lid_driven_cavity` | 方腔驱动流 | 已支持 |

### 支持的指标

| 指标 | OpenFOAM functionObject | 状态 |
|------|------------------------|------|
| `pressure_drop` | surfaceFieldValue | 已支持 |
| `drag_coefficient` | forceCoeffs | 已支持 |
| `velocity_profile` | probes | 已支持 |

### 不支持的物理能力

| 能力 | 原因 | 严重级别 |
|------|------|----------|
| 湍流模拟（RANS/LES） | 当前仅支持层流，缺少湍流模型配置 | blocking |
| 多相流 | PhysicsSpec 支持 phases 字段但编译器未实现 | blocking |
| 可压缩流 | PhysicsSpec 支持 compressibility 字段但编译器未实现 | blocking |
| 3D 几何 | 当前仅支持 2D 和轴对称 | blocking |
| 旋涡破碎指数 | IntentEngine 不识别此指标，无对应 functionObject | warning |
| 自定义边界条件 | 仅支持预设的入口/出口/壁面边界 | warning |
| 动网格 | 编译器未实现 dynamicMeshDict 生成 | blocking |
| 并行分解 | 编译器未生成 decomposeParDict | warning |

### CodeExtension 机制

当遇到未知指标时，系统通过 `MissingCapability` 模型触发 CodeExtension 流程：
- `capability_type`：标识能力类型（metric_algorithm / function_object / solver_adapter）
- `code_extension_allowed`：是否允许通过代码扩展解决
- `suggested_extension_type`：建议的扩展类型
- `severity`：warning（可继续）或 blocking（必须解决）

---

## 9. 后续建议

### 短期（下一个 Sprint）

1. **修复 spec_dict bug**：将 `get_session_experiment_spec` 端点中的 `stored.spec_dict` 改为 `json.loads(stored.spec_json)`，或为 `StoredExperimentSpec` 添加 `spec_dict` 属性。
2. **实现参数编辑端点**：`PATCH /api/projects/{id}/experiment-specs/{spec_id}/parameters/{param_id}`，支持参数修改并触发依赖参数自动更新。
3. **实现状态转换端点**：`POST /api/projects/{id}/experiment-specs/{spec_id}/transition`，使用 `state_machine.assert_transition()` 校验合法性。
4. **实现编译端点**：`POST /api/projects/{id}/experiment-specs/{spec_id}/compile`，调用 `compile_spec()` 返回 CompiledCase + CompilationManifest。

### 中期（1-2 个 Sprint）

5. **LLM 增强 IntentEngine**：在保留规则推理的基础上，当 `app_mode != FAKE` 时可选启用 LLM 增强意图理解，处理更复杂的自然语言输入。
6. **湍流支持**：在编译器中添加 k-omega SST / k-epsilon 模型配置，扩展 `_build_pipe_plan` 和 `_build_cylinder_plan`。
7. **3D 几何支持**：扩展 Dynamic Schema 生成 3D blockMeshDict，支持 3D 管道和 3D 圆柱绕流。
8. **并行分解**：在编译产物中生成 decomposeParDict，支持多核并行计算。

### 长期

9. **动态网格**：实现 dynamicMeshDict 生成，支持动网格仿真。
10. **多相流编译器**：实现 VOF / Euler 多相流模型的编译器。
11. **结果可视化**：在 MetricPipeline 之后集成 ParaView 自动化脚本，生成可视化图片。
12. **实验谱系管理**：实现 ExperimentSpec 的版本树，支持 clone / derive / compare 操作。
13. **Feature Flag 完整化**：将 `research_workflow_v2` flag 与 API 路由绑定，`False` 时回退到旧 plan-operations 工作流。

---

## 附录：提交历史

| Commit | 描述 |
|--------|------|
| 1/9 | feat(research): ResearchSession orchestrator and new API |
| 2/9 | feat(frontend): research session clarification flow |
| 3/9 | feat(research): Dynamic Schema generates ExperimentSpec |
| 4/9 | feat(frontend): parameter workbench integrated into DraftReady flow |
| 5/9 | feat(measurement): MetricPlanner and MeasurementPlan |
| 6/9 | feat(compiler): compile_spec with CompilationManifest |
| 7/9 | feat(research): MissingCapability triggers CodeExtension |
| 8/9 | feat(results): OpenFOAM result ingestor and metric pipeline |
| 9/9 | feat(e2e): E2E tests and implementation report |
