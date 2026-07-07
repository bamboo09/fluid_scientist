# Workflow V2 Gap Analysis

## 1. 当前实际入口

### 前端入口
- `designExperimentFromPrompt()` -> `createResearchSession()` -> POST /api/research-sessions
- `confirmAndSubmitPlan()` [deprecated] -> prepareProjectForGateTwo()
- `submitPlanOperation()` [deprecated] -> POST /api/plan-operations

### API 调用链
- POST /api/research-sessions -> ResearchOrchestrator.start_session() -> _process_turn()
  - IntentEngine.assess_intent() [FAKE: keyword rules]
  - _extract_facts() [FAKE: keyword rules]
  - _build_physics_spec()
  - ScopeEngine.evaluate_scope() [RULE: 5 rules]
  - ExperimentSpecFactory.create_from_schema()
  - MetricPlanner.propose_metrics()

### 编译入口
- compile_spec() -> compile_confirmed_spec() [DEPRECATED] -> builder(spec) -> ExperimentPlan -> compile_plan(plan)

## 2. 当前双轨逻辑

| 旧入口 | 状态 | 标记 |
|--------|------|------|
| ExperimentPlan | API 层仍使用 | 保留兼容 |
| compile_plan() | compile_spec 内部调用 | 新流程禁用 |
| submitPlanOperation() | 前端 @deprecated | 计划删除 |
| confirmAndSubmitPlan() | currentResearchSession 存在时短路 | 保留兼容 |
| /api/plan-operations POST | OpenAPI deprecated | 计划删除 |

## 3. 关键断点

1. IntentEngine real 模式未实现 - 始终 fallback 到 fake
2. 高风险参数静默默认 - safe_enum() 强制默认
3. compile_spec 仍调 compile_plan - builder 重建 ExperimentPlan
4. MeasurementPlan 未编译进 Case - 独立配置
5. ResultIngestor 仅接受文本 - ingest(log_text=...)
6. 无 MetricExecutor 类 - 函数式实现
7. 功能分支未合并 main
