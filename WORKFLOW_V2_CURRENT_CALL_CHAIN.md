# WORKFLOW_V2_CURRENT_CALL_CHAIN.md

> 基于代码实际调用分析，非提交标题判断。
> 分析时间：2026-07-08
> 分支：main (b9c5e70)

---

## 1. 研究需求入口链

### 1.1 实际调用链

```
前端 POST /api/research-sessions
  → app.py: create_app() 内部注册的 research_session_start()
    → ResearchOrchestrator.start_session(project_id, message)
      → SessionStore.create(session)
      → ResearchOrchestrator._process_turn(session, user_message)
        → IntentEngine.assess_intent(user_message, accumulated_context, confirmed_facts)
          → [real模式] _assess_intent_with_llm(user_message, accumulated_context, confirmed_facts)
            → llm_client.chat.completions.create(messages=[system_prompt, user_message])
            → IntentAssessment.model_validate(json_response)
          → [fake模式] _assess_intent_fake(user_message, accumulated_context, confirmed_facts)
            → 关键词规则匹配
        → ResearchOrchestrator._extract_facts(user_message, intent, turn_id)
        → ResearchOrchestrator._merge_facts(session.confirmed_facts, extracted_facts)
        → ResearchOrchestrator._build_physics_spec(session, all_facts)
        → ResearchOrchestrator._build_research_context(session, user_message, turn_id, intent, all_facts)
        → SessionStore.update(...)
        → [如果需要澄清] ScopeEngine.evaluate_scope(intent, updated_session)
          → 返回 ClarificationRequired
        → [如果 ready_for_draft]
          → ExperimentSpecFactory.create_from_schema(session, intent, physics_spec)
          → ResearchOrchestrator._attach_measurement_plan(spec, intent, session)
            → MetricPlanner.propose_metrics(research_objective, physics_spec, user_metrics, experiment_type)
            → CapabilityResolver.resolve(metric_plan, ...)
            → 返回 (updated_spec, unknown_metrics)
          → ResearchOrchestrator._detect_missing_capabilities(unknown_metrics, session)
          → StoredExperimentSpec → workflow_repository.save_experiment_spec(stored_spec)
          → 返回 DraftReady
```

### 1.2 断点分析

| # | 断点位置 | 问题描述 | 严重程度 |
|---|---------|---------|---------|
| B1-1 | `intent_engine.py:144-147` | **LLM 只接收当前消息**：`_assess_intent_with_llm()` 构造 messages 时只放入 `system_prompt` 和 `user_message`，没有注入 `accumulated_context`（all_messages）和 `confirmed_facts`。多轮对话中 LLM 看不到历史上下文。 | 高 |
| B1-2 | `intent_engine.py:150-163` | **LLM 失败静默回退**：LLM 调用异常或校验失败时，自动回退到 fake 模式，用户无感知。`fallback_reason` 存在但未传播到前端。 | 中 |
| B1-3 | `orchestrator.py:155-159` | **confirmed_facts 传给 IntentEngine 但未被 LLM 使用**：`assess_intent()` 接收 `confirmed_facts` 参数，但 `_assess_intent_with_llm()` 完全忽略它。 | 高 |
| B1-4 | `orchestrator.py:166` | **事实合并不检测冲突**：`_merge_facts()` 按 (category, key) 去重，新值直接覆盖旧值，不检测语义冲突（如用户先说"水"后说"空气"）。 | 中 |

---

## 2. 编译入口链

### 2.1 实际调用链

```
前端 POST /api/projects/{project_id}/experiment-specs/{experiment_id}/compile
  → app.py: compile_experiment_spec()
    → workflow_repository.load_experiment_spec(experiment_id)
    → ExperimentSpec.model_validate_json(stored.spec_json)
    → compile_spec(spec)  [from compilation.py]
      → CompilerRegistry().resolve(spec)  [from native_compiler.py]
        → PipeFlowCompiler.can_compile(spec): 检查 "length" in ids and "axial_cells" in ids
        → CylinderFlowCompiler.can_compile(spec): 检查 "cells_wake" in ids and "reynolds_number" in ids
        → CavityFlowCompiler.can_compile(spec): 检查 "side_length" in ids and "lid_velocity" in ids
      → [如果找到 native compiler]
        → compile_spec_native(spec, registry)
          → compiler.compile(spec)
            → PipeFlowCompiler.compile():
              ★ 构造 LaminarPipeCase + PipeExperimentPlan
              ★ 调用 compile_pipe_plan(plan)  ← 旧函数！
            → CylinderFlowCompiler.compile():
              ★ 构造 CylinderFlowCase + CylinderExperimentPlan
              ★ 调用 compile_cylinder_plan(plan)  ← 旧函数！
            → CavityFlowCompiler.compile():
              ★ 构造 LidDrivenCavityCase + CavityExperimentPlan
              ★ 调用 compile_cavity_plan(plan)  ← 旧函数！
      → [如果没有 native compiler]
        → compile_confirmed_spec(spec)  [deprecated]
          → _build_pipe_plan / _build_cylinder_plan / _build_cavity_plan
          → compile_plan(plan)  ← 旧函数！
    → workflow_repository.store_compiled_experiment(StoredCompiledExperiment)
    → workflow_repository.replace_experiment_spec(...) [status → compiling]
```

### 2.2 断点分析

| # | 断点位置 | 问题描述 | 严重程度 |
|---|---------|---------|---------|
| B2-1 | `native_compiler.py:72-104` | **PipeFlowCompiler 仍调用 compile_pipe_plan**：虽然标记为"native"，但内部构造 `PipeExperimentPlan` 并调用 `compile_pipe_plan(plan)`，完全走了旧路径。 | 致命 |
| B2-2 | `native_compiler.py:117-173` | **CylinderFlowCompiler 仍调用 compile_cylinder_plan**：同上，构造 `CylinderExperimentPlan` 并调用旧函数。 | 致命 |
| B2-3 | `native_compiler.py:186-217` | **CavityFlowCompiler 仍调用 compile_cavity_plan**：同上。 | 致命 |
| B2-4 | `compilation.py:298-342` | **compile_spec 有 fallback 到旧路径**：如果 `CompilerRegistry.resolve()` 返回 None，回退到 `compile_confirmed_spec()` → `compile_plan()`。 | 高 |
| B2-5 | `native_compiler.py:47-57` | **_float/_int 使用硬编码默认值**：`_float(v, "diameter", 0.05)` 等，当参数值为 None 时使用默认值，不报错。 | 高 |
| B2-6 | `compilation.py:119-216` | **_build_pipe_plan 等使用 ConvergenceTargets 默认值**：`_DEFAULT_CONVERGENCE` 硬编码 `residual_tolerance=1e-4`，不从 spec 读取。 | 中 |
| B2-7 | `app.py:1917-2007` | **compile 端点不验证参数完整性**：直接调用 `compile_spec(spec)`，不检查 required parameters 是否为 None/unknown。 | 高 |
| B2-8 | `native_compiler.py` 全文 | **MeasurementPlan 未接入编译**：native compiler 完全不调用 `compile_measurement_plan()`，不读取 spec.metrics 中的 MeasurementPlan。 | 致命 |

---

## 3. 运行/提交入口链

### 3.1 实际调用链

```
旧路径（仍在使用）:
前端 POST /api/projects/{project_id}/experiment-plans/{plan_id}/submit
  → app.py: submit_planned_experiment()
    → project_service.prepare_bound_experiment_submission(project_id, plan_id, case_id, archive_sha256)
    → ExperimentPlan.model_validate_json(stored_plan.plan_json)
    → target.submit_custom(job_id, compiled.archive)
    → project_service.record_pilot_submission(...)

新路径: 不存在
  → 没有 /api/projects/{project_id}/experiment-specs/{experiment_id}/submit 端点
  → 没有 /api/runs 端点
```

### 3.2 断点分析

| # | 断点位置 | 问题描述 | 严重程度 |
|---|---------|---------|---------|
| B3-1 | `app.py:1146-1204` | **submit 端点仍使用 ExperimentPlan**：`submit_planned_experiment()` 从 `stored_plan.plan_json` 加载 `ExperimentPlan`，不从 ExperimentSpec 加载。 | 高 |
| B3-2 | app.py 全文 | **缺少 ExperimentSpec 提交端点**：没有 `/api/projects/{project_id}/experiment-specs/{experiment_id}/submit` 或类似的端点将编译后的 case 提交到执行目标。 | 致命 |
| B3-3 | app.py 全文 | **缺少 RunRecord 概念**：没有独立的运行记录，运行状态绑定在 Project 的 approval/case 上。 | 高 |

---

## 4. 结果分析入口链

### 4.1 实际调用链

```
旧路径（仍在使用）:
前端 GET /api/projects/{project_id}/experiment-plans/{plan_id}/results
  → app.py: planned_experiment_results()
    → target.collect(job_id)
    → ExperimentPlan.model_validate_json(stored_plan.plan_json)
    → ExperimentResultSummary(experiment_type=plan.root.experiment_type, ...)
    → project_service.verify_pilot(...)
    → 返回 PlannedExperimentResultsView

前端 POST /api/projects/{project_id}/experiment-plans/{plan_id}/analysis
  → app.py: analyze_planned_experiment()
    → target.collect(job_id)
    → ExperimentPlan.model_validate_json(stored_plan.plan_json)
    → ExperimentResultSummary(...)
    → analyst.analyze(evidence, evidence_keys)  ← LLM 分析
    → 返回 ExperimentAnalysisView

新路径（模块存在但未接入 API）:
  OpenFOAMResultIngestor.ingest(case_path, result_manifest, measurement_plan)
    → _ingest_from_case()
      → _find_solver_log() → _parse_solver_log()
      → post_dir / "forceCoeffs"  ← 硬编码目录名
      → post_dir / "forces"       ← 硬编码目录名
      → post_dir / "probes"       ← 硬编码目录名
      → post_dir / "surfaceFieldValue" ← 硬编码目录名
      → post_dir / "fieldAverage" ← 硬编码目录名

  MetricExecutor.execute(metric_id, simulation_data, metric_definition, parameters)
    → _calc_pressure_drop(): 使用 inlet_vals[-1] - outlet_vals[-1]  ← 取最后值，非时间平均
    → _calc_strouhal_number(): dt = parameters.get("time_step", 0.01)  ← 使用参数 dt，非真实时间列
    → _calc_velocity_uniformity(): variance = mag_u**2 - mean_u**2  ← 近似计算
    → _calc_drag_coefficient(): cd = cd_values[-1]  ← 取最后值

  ScientificAnalyzer.analyze(metric_results, simulation_data, experiment_context)
    → [存在但从未被 API 调用]
```

### 4.2 断点分析

| # | 断点位置 | 问题描述 | 严重程度 |
|---|---------|---------|---------|
| B4-1 | app.py 全文 | **缺少结果摄入 API 端点**：没有 `/api/runs/{id}/ingest` 或类似端点调用 `OpenFOAMResultIngestor`。 | 致命 |
| B4-2 | app.py 全文 | **缺少指标计算 API 端点**：没有 `/api/runs/{id}/analyze` 或类似端点调用 `MetricExecutor`。 | 致命 |
| B4-3 | app.py 全文 | **缺少科学报告 API 端点**：没有 `/api/runs/{id}/scientific-report` 或类似端点调用 `ScientificAnalyzer`。 | 致命 |
| B4-4 | `ingestor.py:286-313` | **Ingestor 硬编码目录名**：`_parse_post_processing()` 直接使用 `post_dir / "forceCoeffs"` 等硬编码路径，不根据 MeasurementPlan 中的 functionObject ID 读取。 | 高 |
| B4-5 | `metric_executor.py:166-168` | **压降取最后值**：`_calc_pressure_drop()` 使用 `inlet_vals[-1] - outlet_vals[-1]`，不做时间平均，不计算标准差和置信区间。 | 高 |
| B4-6 | `metric_executor.py:332` | **Strouhal 使用参数 dt**：`dt = parameters.get("time_step", parameters.get("interval", 0.01))`，不从真实数据的时间列读取。 | 高 |
| B4-7 | `metric_executor.py:449-456` | **速度均匀性近似计算**：使用 `variance = mag_u**2 - mean_u**2`，不是面积加权的变异系数。 | 高 |
| B4-8 | `metric_executor.py:210` | **阻力系数取最后值**：`cd = cd_values[-1]`，不做时间平均。 | 中 |
| B4-9 | `metric_executor.py:545` | **摩擦因子取最后值**：`dp = surface_field_values[inlet_key][-1] - surface_field_values[outlet_key][-1]`。 | 中 |
| B4-10 | app.py 全文 | **旧分析链路未接入新模块**：`analyze_planned_experiment()` 使用 LLM `analyst.analyze()`，完全不调用 `MetricExecutor` 或 `ScientificAnalyzer`。 | 致命 |

---

## 5. 三条核心链路连接状态总结

### 5.1 MeasurementPlan → OpenFOAM Case

```
状态：未连接 ❌

MeasurementPlan 存在于 spec.metrics JSON 中
  → compile_measurement_plan() 生成 dict 表示
  → dict 存在于 MeasurementCompilationResult.control_dict_additions
  → ❌ 没有任何代码将 dict 写入实际的 system/controlDict 文件
  → ❌ native compiler 不调用 compile_measurement_plan()
  → ❌ 旧 compiler 也不调用 compile_measurement_plan()
```

### 5.2 ExperimentSpec → Native Compiler

```
状态：假连接 ❌

compile_spec(spec)
  → CompilerRegistry.resolve(spec) → 找到 PipeFlowCompiler
  → compile_spec_native(spec, registry)
  → PipeFlowCompiler.compile(spec)
    → ❌ 构造 PipeExperimentPlan（旧模型）
    → ❌ 调用 compile_pipe_plan(plan)（旧函数）
    → 实际生成路径：ExperimentSpec → PipeExperimentPlan → compile_pipe_plan → CompiledCase
    → 而非：ExperimentSpec → 直接生成 Case 文件 → CompiledCase
```

### 5.3 Remote results → MetricResult → ScientificReport

```
状态：未连接 ❌

target.collect(job_id) → collection (mesh, solver, observables)
  → ❌ 不调用 OpenFOAMResultIngestor
  → ❌ 不调用 MetricExecutor
  → ❌ 不调用 ScientificAnalyzer
  → 直接走 LLM analyst.analyze(evidence)

OpenFOAMResultIngestor 存在但：
  → 硬编码目录名读取
  → 无 API 端点触发

MetricExecutor 存在但：
  → 数学计算有缺陷
  → 无 API 端点触发

ScientificAnalyzer 存在但：
  → 无 API 端点触发
  → 从未被任何代码调用
```

---

## 6. 旧链路依赖清单

以下旧函数/模型仍在被新代码调用：

| 旧函数/模型 | 调用位置 | 状态 |
|------------|---------|------|
| `compile_pipe_plan()` | `native_compiler.py:104` | 活跃 |
| `compile_cylinder_plan()` | `native_compiler.py:173` | 活跃 |
| `compile_cavity_plan()` | `native_compiler.py:217` | 活跃 |
| `compile_plan()` | `compilation.py:295` | 活跃 (fallback) |
| `PipeExperimentPlan` | `native_compiler.py:89`, `compilation.py:131` | 活跃 |
| `CylinderExperimentPlan` | `native_compiler.py:159`, `compilation.py:182` | 活跃 |
| `CavityExperimentPlan` | `native_compiler.py:203`, `compilation.py:206` | 活跃 |
| `LaminarPipeCase` | `native_compiler.py:80`, `compilation.py:122` | 活跃 |
| `CylinderFlowCase` | `native_compiler.py:144`, `compilation.py:167` | 活跃 |
| `LidDrivenCavityCase` | `native_compiler.py:195`, `compilation.py:198` | 活跃 |
| `ExperimentPlan` | `app.py:1174,1229,1284` | 活跃 (submit/results/analysis) |
| `ConvergenceTargets` | `native_compiler.py:97,167,211`, `compilation.py:113` | 活跃 |

---

## 7. 数据库层 plan_id → experiment_id 状态

```
StoredCompiledExperiment 使用 plan_id 字段（app.py:1972）
  → plan_id=experiment_id  ← 字段名仍是 plan_id

workflow_repository.store_compiled_experiment(StoredCompiledExperiment)
  → 存储时用 plan_id 作为 key

旧表结构：
  experiment_plans 表：plan_id, plan_json, ...
  compiled_experiments 表：plan_id, plan_version, archive_sha256, archive, ...

新表结构：
  experiment_specs 表：experiment_id, spec_json, status, ...
  compiled_experiments 表：plan_id (实际存 experiment_id) ← 命名不一致
```

---

## 8. 前端调用清单

前端 `apps/web/app.js` 中的 API 调用：

| 前端函数 | API 端点 | 使用旧链路？ |
|---------|---------|------------|
| `startResearchSession()` | POST /api/research-sessions | 否（新链路） |
| `handleTurn()` | POST /api/research-sessions/{id}/turns | 否（新链路） |
| `getExperimentSpec()` | GET /api/research-sessions/{id}/experiment-spec | 否（新链路） |
| `updateSpecParameter()` | PATCH /api/projects/{id}/experiment-specs/{id}/parameters/{id} | 否（新链路） |
| `transitionSpec()` | POST /api/projects/{id}/experiment-specs/{id}/transition | 否（新链路） |
| `compileSpec()` | POST /api/projects/{id}/experiment-specs/{id}/compile | 否（新链路，但后端走旧函数） |
| `submitPlan()` | POST /api/projects/{id}/experiment-plans/{id}/submit | **是** |
| `getResults()` | GET /api/projects/{id}/experiment-plans/{id}/results | **是** |
| `getAnalysis()` | POST /api/projects/{id}/experiment-plans/{id}/analysis | **是** |
| `loadBenchmarks()` | GET /api/projects/{id}/benchmarks | **是** |

---

## 9. 整改优先级

基于断点分析，12 个 Commit 的整改优先级：

1. **Commit 1** (B1-1, B1-3, B1-4)：多轮 ResearchState — 修复 LLM 上下文注入
2. **Commit 2** (B2-5, B2-7)：编译参数硬门禁 — 删除默认值，检查 required 参数
3. **Commit 3** (B2-1)：原生 PipeFlowCompiler — 不调用 compile_pipe_plan
4. **Commit 4** (B2-2, B2-3)：原生 Cylinder/Cavity Compiler — 不调用旧函数
5. **Commit 5** (B2-8)：MeasurementPlan 接入 Compiler — 写入真实 Case 文件
6. **Commit 6** (B4-5, B4-6, B4-7, B4-8, B4-9)：指标数学修复
7. **Commit 7** (B4-4)：ResultManifest 和真实 Ingestor — 按 functionObject ID 读取
8. **Commit 8** (B4-1, B4-2, B4-3, B4-10)：分析主流程 API — 连接 Ingestor → Executor → Analyzer
9. **Commit 9**：CodeExtension 用户闭环
10. **Commit 10**：真实 OpenFOAM E2E
11. **Commit 11** (B3-1, 第6节, 第7节, 第8节)：旧链路清理和数据库迁移
12. **Commit 12** (B2-4)：合并与部署 — 删除 compile_spec fallback
