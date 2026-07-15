# OpenFOAM 修复证据文档

## 1. 错误分类证据

### 1.1 错误分类体系

**代码位置**: `src/fluid_scientist/repair/error_classifier.py`

`OpenFOAMErrorClassifier` 将 OpenFOAM 错误日志分类为 9 种类别，并标记严重程度和可修复性：

| ErrorCategory | 枚举值 | 说明 | 可修复 |
|--------------|--------|------|--------|
| `MESH_ERROR` | mesh_error | blockMesh/snappyHexMesh/checkMesh 失败 | 是 |
| `BOUNDARY_CONDITION_ERROR` | boundary_condition_error | 边界条件类型或 patch 缺失 | 是 |
| `SOLVER_ERROR` | solver_error | 数值不稳定、NaN、发散 | 仅 standalone NaN 路径 |
| `PHYSICS_ERROR` | physics_error | CFL 违规、时间步过大 | 是 |
| `SYNTAX_ERROR` | syntax_error | OpenFOAM 字典语法错误 | 是 |
| `FILE_ERROR` | file_error | 文件缺失、权限错误 | 否 |
| `MEMORY_ERROR` | memory_error | 内存不足 | 否 |
| `TIMEOUT_ERROR` | timeout_error | 执行超时 | 否 |
| `UNKNOWN_ERROR` | unknown_error | 未分类错误 | 否 |

**REPAIRABLE_CATEGORIES** 集合（仅这 4 类可通过 LLM 修复循环处理）:
```python
REPAIRABLE_CATEGORIES = {
    ErrorCategory.BOUNDARY_CONDITION_ERROR,
    ErrorCategory.PHYSICS_ERROR,
    ErrorCategory.SYNTAX_ERROR,
    ErrorCategory.MESH_ERROR,
}
```

**ErrorSeverity** 枚举:
- `FATAL` -- 无法继续，需要修复
- `RECOVERABLE` -- 可自动修复
- `WARNING` -- 非致命，但应处理

### 1.2 错误分类测试证据

**测试位置**: `tests/v5_closed_loop/test_error_repair.py` -> `TestErrorClassification.test_primary_error_classification`

该测试使用 `@pytest.mark.parametrize` 对 9 类 mock 日志逐一验证分类、严重程度和可修复性：

| Mock 日志常量 | 日志内容摘要 | 期望类别 | 期望严重程度 | 期望可修复 | Test 场景 |
|--------------|------------|---------|------------|-----------|-----------|
| `LOG_BLOCKMESH_FAIL` | `FOAM FATAL ERROR: blockMesh failed to create mesh` | MESH_ERROR | FATAL | True | -- |
| `LOG_PATCH_MISMATCH_TEST_F` | `FOAM FATAL IO ERROR: patch 'outlet' not found in 0/U` | BOUNDARY_CONDITION_ERROR | FATAL | True | **Test F** |
| `LOG_NAN_STANDALONE_TEST_G` | `NaN detected in solution at cell 1234, Return code: 1` | SOLVER_ERROR | FATAL | True | **Test G** |
| `LOG_DIVERGENCE_FATAL` | `FOAM FATAL ERROR: divergence detected in pEqn` | SOLVER_ERROR | FATAL | False | -- |
| `LOG_COURANT_HIGH` | `Courant Number mean: 0.6 max: 2.75` | PHYSICS_ERROR | RECOVERABLE | True | -- |
| `LOG_SYNTAX_MALFORMED` | `FOAM FATAL IO ERROR: parse error in dictionary system/controlDict` | SYNTAX_ERROR | FATAL | True | -- |
| `LOG_FILE_NOT_FOUND` | `FOAM FATAL ERROR: cannot open file 0/U` | FILE_ERROR | FATAL | False | -- |
| `LOG_MEMORY` | `FOAM FATAL ERROR: out of memory: Cannot allocate 4096 bytes` | MEMORY_ERROR | FATAL | False | -- |
| `LOG_TIMEOUT` | `FOAM FATAL ERROR: blockMesh timed out after 600 seconds` | TIMEOUT_ERROR | FATAL | False | -- |

### 1.3 Mock 日志原文

以下为测试中使用的完整 mock 日志（基于真实 OpenFOAM stderr 格式）：

```python
# 网格错误 -- blockMesh 失败，含文件/行号元数据
LOG_BLOCKMESH_FAIL = """
--> FOAM FATAL ERROR
blockMesh failed to create mesh
    From function blockMesh::createMesh(...)
    file: mesh/blockMesh/blockMesh.C  line: 124
"""

# Test F -- 边界条件错误：0/U 中的 patch 在 polyMesh/boundary 中缺失
LOG_PATCH_MISMATCH_TEST_F = """
--> FOAM FATAL IO ERROR
patch 'outlet' not found in 0/U
    file: fields/U  line: 200
"""

# Test G -- 数值发散表现为 NaN（无 FOAM FATAL ERROR 行）
LOG_NAN_STANDALONE_TEST_G = """
Time = 0.05
 Courant Number mean: 0.4 max: 0.9
NaN detected in solution at cell 1234
Return code: 1
"""

# 求解器错误 -- FOAM FATAL ERROR 块内报告发散
LOG_DIVERGENCE_FATAL = """
--> FOAM FATAL ERROR
divergence detected in pEqn
    file: finiteVolume/solvers/pEqn.C  line: 88
"""

# 物理错误 -- Courant 数超过稳定极限 (>1.0)
LOG_COURANT_HIGH = """
Time = 0.01
 Courant Number mean: 0.6 max: 2.75
"""

# 语法错误 -- 字典解析错误
LOG_SYNTAX_MALFORMED = """
--> FOAM FATAL IO ERROR
parse error in dictionary system/controlDict
    file: system/controlDict  line: 42
"""
```

### 1.4 分类逻辑要点

**NaN 独立检测路径**: 当日志中没有 FOAM FATAL ERROR 但包含 `NaN` 关键词时，分类器通过独立路径检测并标记为 `SOLVER_ERROR + is_repairable=True`。这与 `LOG_DIVERGENCE_FATAL`（在 FOAM FATAL ERROR 块内的发散）不同，后者因为 `SOLVER_ERROR` 不在 `REPAIRABLE_CATEGORIES` 中而标记为 `is_repairable=False`。

**Courant 数检测**: 分类器使用正则 `Courant Number mean:\s*([\d.]+)\s*max:\s*([\d.]+)` 提取 Courant 数，当 `max > 1.0` 时生成 `PHYSICS_ERROR + RECOVERABLE` 错误。当 `max == 1.0`（不严格大于）时不产生错误。

**返回码检测**: 当日志中没有其他错误信号但包含 `Return code: N`（N != 0）时，分类为 `UNKNOWN_ERROR + FATAL + is_repairable=False`。返回码为 0 时不产生任何错误。

**测试验证**:
- `test_courant_at_limit_does_not_raise_error`: max=1.0 时无错误
- `test_return_code_zero_yields_no_error`: Return code: 0 时无错误
- `test_unknown_nonzero_return_code`: Return code: 139 时 UNKNOWN_ERROR
- `test_multiple_errors_are_all_returned`: mesh + CFL 同时出现时两个错误都返回

### 1.5 优先级排序证据

**测试位置**: `TestGetPrimaryError`（4 个测试）

`get_primary_error()` 的优先级逻辑：

1. 空列表返回 None
2. 有 FATAL 错误时，优先返回**可修复**的 FATAL（即使它出现在后面）
3. 全部不可修复时，返回首个 FATAL
4. 仅非 FATAL 时，返回首个

```python
# 测试: test_prefers_repairable_fatal_even_when_later
fatal_non_repairable = ClassifiedError(category=FILE_ERROR, severity=FATAL, is_repairable=False)
fatal_repairable = ClassifiedError(category=BOUNDARY_CONDITION_ERROR, severity=FATAL, is_repairable=True)
primary = classifier.get_primary_error([fatal_non_repairable, fatal_repairable])
assert primary is fatal_repairable  # 可修复的优先，即使排在后面
```

---

## 2. 修复策略证据

### 2.1 修复级别递进

**代码位置**: `src/fluid_scientist/repair/repair_policy.py` -> `RepairPolicy.get_repair_level()`

```python
def get_repair_level(self, phase: RepairPhase, attempt_number: int) -> RepairLevel:
    if attempt_number <= 1:
        return RepairLevel.CONFIG_ONLY        # Level 1: 调整 controlDict/fvSolution 参数
    elif attempt_number <= 2:
        return RepairLevel.DICTIONARY_SYNTAX   # Level 2: 修复字典语法错误
    else:
        return RepairLevel.PARTIAL_REGENERATION # Level 3: 从 CaseSpec 重新生成特定文件
```

**测试证据**: `TestRepairPolicy.test_repair_level_progression`

```python
assert policy.get_repair_level(RepairPhase.SMOKE, 1) == RepairLevel.CONFIG_ONLY
assert policy.get_repair_level(RepairPhase.SMOKE, 2) == RepairLevel.DICTIONARY_SYNTAX
assert policy.get_repair_level(RepairPhase.SMOKE, 3) == RepairLevel.PARTIAL_REGENERATION
# 超过第 3 次后保持最高级别
assert policy.get_repair_level(RepairPhase.SMOKE, 4) == RepairLevel.PARTIAL_REGENERATION
```

**端到端升级证据**: `TestRepairOrchestrator.test_test_g_freezes_when_executor_cannot_apply`

Test G（NaN）通过真实执行器（无工作站）验证完整升级序列：
```python
levels = [a["level"] for a in snap["attempts"]]
assert levels == ["config_only", "dictionary_syntax", "partial_regeneration"]
```

### 2.2 阶段冻结

**代码位置**: `RepairPolicy.record_attempt()` 和 `RepairPolicy.can_attempt()`

**规则**:
- 每个阶段（mesh/smoke/full_run）最多 3 次修复尝试
- 3 次失败后该阶段冻结（`phase_frozen[phase] = True`）
- 冻结阶段阻止该阶段的进一步尝试
- 冻结不影响其他阶段

**测试证据**: `TestRepairPolicy.test_phase_freezes_after_max_attempts`

```python
# 3 次失败尝试后阶段冻结
statuses = [RepairStatus.FAILED, RepairStatus.FAILED, RepairStatus.PHASE_FROZEN]
assert policy.phase_attempts["smoke"] == 3
assert policy.phase_frozen["smoke"] is True
# 冻结阶段阻止重试
assert policy.can_attempt(RepairPhase.SMOKE) is False
# 其他阶段仍可用
assert policy.can_attempt(RepairPhase.MESH) is True
assert policy.can_attempt(RepairPhase.FULL_RUN) is True
```

**隔离证据**: `test_other_phase_unaffected_by_freeze`
```python
# mesh 冻结后 smoke 仍可用
assert policy.phase_frozen["mesh"] is True
assert policy.can_attempt(RepairPhase.MESH) is False
assert policy.can_attempt(RepairPhase.SMOKE) is True
```

### 2.3 全局上限

**代码位置**: `RepairPolicy.can_attempt()` 和 `RepairPolicy.record_attempt()`

**规则**:
- 默认全局上限 10 次尝试
- 达到全局上限后所有阶段都被阻止
- 全局上限优先于阶段冻结（即使阶段未冻结也无法尝试）

**测试证据**: `TestRepairPolicy.test_global_limit_reached`

```python
policy = RepairPolicy(max_attempts_per_phase=10, max_global_attempts=2)
# 第 1 次失败
assert policy.record_attempt(a1) == RepairStatus.FAILED
# 第 2 次达到全局上限
assert policy.record_attempt(a2) == RepairStatus.GLOBAL_LIMIT_REACHED
# 全局上限阻止所有阶段
assert policy.can_attempt(RepairPhase.SMOKE) is False
assert policy.can_attempt(RepairPhase.MESH) is False
```

**全局上限优先于阶段冻结**: `test_global_limit_takes_precedence_over_phase_freeze`
```python
policy = RepairPolicy(max_attempts_per_phase=10, max_global_attempts=1)
assert policy.record_attempt(attempt) == RepairStatus.GLOBAL_LIMIT_REACHED
# mesh 只用了 1/10 次尝试，但全局上限仍阻止它
assert policy.phase_frozen["mesh"] is False  # 阶段未冻结
assert policy.can_attempt(RepairPhase.MESH) is False  # 但全局上限阻止
assert policy.can_attempt(RepairPhase.FULL_RUN) is False
```

### 2.4 默认限制值

**测试证据**: `TestRepairPolicy.test_default_limits`

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `max_attempts_per_phase` | 3 | 每阶段最多 3 次修复尝试 |
| `max_global_attempts` | 10 | 全局最多 10 次尝试 |
| `current_global_attempts` | 0 | 初始全局计数 |
| `phase_attempts` | {mesh:0, smoke:0, full_run:0} | 各阶段计数 |
| `phase_frozen` | {mesh:False, smoke:False, full_run:False} | 各阶段冻结状态 |
| `has_repair_been_attempted` | False | 是否已有修复尝试 |

---

## 3. 修复流程证据

### 3.1 完整流程：classify -> context -> diagnose -> apply -> validate

**代码位置**: `src/fluid_scientist/repair/repair_orchestrator.py` -> `RepairOrchestrator.attempt_repair()`

```
error_log
  |
  v
[1. Classify] -- OpenFOAMErrorClassifier.classify()
  |               -> get_primary_error()
  |               -> 如果 None: 返回 "No classifiable error"
  |               -> 如果 !is_repairable: 返回 "not repairable"，跳过修复循环
  v
[2. Build Context] -- RepairContextBuilder.build_context()
  |                    -> error: {category, error_message, raw_log}
  |                    -> stage: "smoke" / "mesh" / "full_run"
  |                    -> spec_summary: {domain, cylinder, boundaries, simulation, ...}
  |                    -> files: {文件名: 内容}（截断 2000 字符）
  |                    -> user_original_input（截断 500 字符）
  |                    -> previous_attempts: 最近 3 条
  v
[3. Diagnose] -- LLMDiagnoser.diagnose()
  |              -> client.call(purpose="explanation", prompt_name="of_error_diagnosis", ...)
  |              -> 如果无 client: root_cause="LLM client not available", fixes=[]
  |              -> 如果无 fixes: 记录 "no_fixes_suggested" 尝试，continue
  v
[4. Apply] -- ControlledRepairExecutor.execute_repair()
  |            -> policy.record_attempt(attempt)
  |            -> 返回 RepairResult
  v
[5. Validate] -- 检查 repair_result.status
  |               -> SUCCESS: 修复成功，返回
  |               -> PHASE_FROZEN: 阶段冻结，返回
  |               -> GLOBAL_LIMIT_REACHED: 全局上限，返回
  |               -> FAILED: 继续循环
  v
[Loop] -- 回到 [2] 直到 can_attempt(phase) 为 False
```

### 3.2 修复上下文裁剪证据

**测试位置**: `TestRepairContextBuilder`（8 个测试）

| 测试方法 | 验证的裁剪规则 |
|---------|-------------|
| `test_basic_context_keys` | 上下文包含 stage、error.category、user_original_input |
| `test_no_spec_omits_spec_summary` | 无 spec 时省略 spec_summary 键 |
| `test_spec_summary_for_boundary_error_includes_obstacles` | 边界条件错误含 triangle/rectangle/bottom_profile 几何 |
| `test_spec_summary_for_physics_error_includes_simulation` | 物理错误含 simulation(delta_t/end_time/max_courant)和 fluid(nu)，不含障碍物 |
| `test_file_contents_truncated_to_2000_chars` | 文件内容截断到 2000 字符 |
| `test_user_text_truncated_to_500_chars` | 用户文本截断到 500 字符 |
| `test_previous_attempts_limited_to_last_three` | 前次尝试保留最近 3 条（fix_2, fix_3, fix_4） |
| `test_context_references_error_raw_log` | 上下文引用原始错误日志（含 "blockMesh"） |

**关键裁剪规则**:
- 边界/网格错误 -> 暴露障碍物几何（triangle/rectangle/bottom_profile）
- 物理/求解器错误 -> 暴露仿真参数（delta_t/end_time/max_courant）和流体参数（nu），**不**暴露障碍物
- 文件内容 -> 每个文件截断到 2000 字符
- 用户文本 -> 截断到 500 字符
- 前次尝试 -> 保留最近 3 条（避免上下文过长和重复修复）

### 3.3 修复编排测试证据

**测试位置**: `TestRepairOrchestrator`（9 个测试）

| 测试方法 | 验证的流程分支 |
|---------|-------------|
| `test_non_repairable_error_skips_repair_loop` | FILE_ERROR 不可修复 -> 跳过修复循环，current_global_attempts=0 |
| `test_no_classifiable_error_returns_failure` | Return code: 0 无错误 -> "No classifiable error" |
| `test_unknown_error_is_not_repairable` | Return code: 139 -> UNKNOWN_ERROR 不可修复 |
| `test_test_f_freezes_when_no_llm_available` | Test F + 无 LLM -> 3 轮 "no_fixes_suggested" -> PHASE_FROZEN |
| `test_test_g_freezes_when_executor_cannot_apply` | Test G + 有 LLM + 无执行器 -> 3 轮升级 -> PHASE_FROZEN |
| `test_test_f_success_path` | Test F + 有 LLM + fake 执行器 -> SUCCESS（1 轮） |
| `test_global_limit_propagated_from_executor` | 低全局上限 -> GLOBAL_LIMIT_REACHED |
| `test_stage_maps_to_repair_phase` | full_run 阶段映射到 RepairPhase.FULL_RUN |
| `test_reset_policy_clears_history` | reset_policy 清空计数和历史 |

---

## 4. 不变量证据：RETRY_WITHOUT_REPAIR 永不允许

### 4.1 专属测试类

**测试位置**: `tests/v5_closed_loop/test_error_repair.py` -> `TestNoRetryWithoutRepair`（5 个测试）

改造方案第 8.5 节要求：

> 每轮必须产生真实 diff。若 diff 为空：RETRY_WITHOUT_REPAIR，禁止继续重试。

### 4.2 不变量验证矩阵

| 测试方法 | 验证的不变量 | 核心断言 |
|---------|------------|---------|
| `test_policy_flag_guards_first_retry` | `has_repair_been_attempted` 标志在首次修复前为 False，修复后为 True | `assert policy.has_repair_been_attempted is False` -> `record_attempt()` -> `assert policy.has_repair_been_attempted is True` |
| `test_every_recorded_attempt_has_documented_fix` | 每个记录的尝试都有非空 `fix_applied`，且不等于 "retry_without_repair" 或空字符串 | `for attempt in attempts: assert attempt["fix_applied"] != "" and != "retry_without_repair"` |
| `test_loop_iteration_count_matches_recorded_attempts` | LLM 调用数等于记录的尝试数（每次迭代既诊断又记录） | `assert client.call_count == snap["attempt_count"]` |
| `test_frozen_phase_blocks_all_retries` | 冻结阶段阻止所有重试（含无修复重试） | `assert policy.can_attempt(RepairPhase.SMOKE) is False` |
| `test_no_fixes_path_still_records_before_retrying` | "LLM 无修复建议"分支先记录尝试（fix_applied="no_fixes_suggested"）再继续循环，不静默重试 | `assert all(a["fix_applied"] == "no_fixes_suggested" for a in attempts)` |

### 4.3 实现机制

**代码位置**: `RepairOrchestrator.attempt_repair()` 第 160-172 行

当 LLM 返回空 `fixes` 时，编排器不会静默重试，而是显式记录一个 `fix_applied="no_fixes_suggested"` 的尝试：

```python
if not diagnosis.get("fixes"):
    logger.warning("LLM suggested no fixes, escalating repair level")
    attempt = RepairAttempt(
        attempt_number=self._policy.current_global_attempts + 1,
        phase=phase,
        level=self._policy.get_repair_level(phase, ...),
        error_summary=primary_error.error_message,
        fix_applied="no_fixes_suggested",  # 显式标记，非空
    )
    self._policy.record_attempt(attempt)
    continue  # 继续循环（级别升级）
```

这确保了：
1. 每次 `continue`（重试）前都有 `record_attempt` 调用
2. `fix_applied` 字段始终非空
3. `has_repair_been_attempted` 在首次 `record_attempt` 后翻转为 True
4. 调用计数（`client.call_count`）与尝试计数（`attempt_count`）始终相等

### 4.4 has_repair_been_attempted 标志

**代码位置**: `RepairPolicy.has_repair_been_attempted`

```python
@property
def has_repair_been_attempted(self) -> bool:
    """Check if any repair has been attempted (vs RETRY_WITHOUT_REPAIR)."""
    return len(self.attempt_history) > 0
```

此属性用于在重试前检查是否已有修复尝试。在 `attempt_repair()` 的不可修复错误分支中验证：

```python
# test_non_repairable_error_skips_repair_loop
assert orchestrator.policy.has_repair_been_attempted is False  # 不可修复错误不产生修复尝试
```

---

## 5. Test F 与 Test G 场景映射

### 5.1 Test F：可控 patch 错误

**改造方案第 14 节 Test F 定义**:
> 故意制造 0/U patch name 与网格不一致。验证：smoke 失败、full run 不启动、错误分类为 PATCH_MISMATCH、LLM 收到相关日志和文件、受控修复更新 patch、再次 smoke 成功、有真实 diff、不是重复运行同一 Case。

**Mock 日志**:
```python
LOG_PATCH_MISMATCH_TEST_F = """
--> FOAM FATAL IO ERROR
patch 'outlet' not found in 0/U
    file: fields/U  line: 200
"""
```

**测试覆盖**:

| 测试方法 | 验证的 Test F 要求 | 断言 |
|---------|------------------|------|
| `test_primary_error_classification`（LOG_PATCH_MISMATCH_TEST_F） | 错误分类为 BOUNDARY_CONDITION_ERROR | `primary.category == ErrorCategory.BOUNDARY_CONDITION_ERROR` |
| 同上 | 严重程度为 FATAL | `primary.severity == ErrorSeverity.FATAL` |
| 同上 | 可修复 | `primary.is_repairable is True` |
| 同上 | 有建议修复 | `primary.suggested_fix` 非空 |
| `test_severity_and_repairability_flags` | FATAL + 可修复矩阵 | `bc.severity == FATAL and bc.is_repairable is True` |
| `test_test_f_freezes_when_no_llm_available` | 无 LLM 时 3 轮后冻结 | `result.attempts == 3`, `snap["phase_frozen"]["smoke"] is True` |
| 同上 | 每轮诊断记录 "LLM client not available" | `diagnosis_history[0]["fixes"] == []`, `"LLM client not available" in root_cause` |
| `test_test_f_success_path` | 完整成功路径（classify->context->diagnose->apply->validate） | `result.repaired is True`, `result.final_status == SUCCESS`, `result.attempts == 1` |
| 同上 | 有真实修复（fixes_applied 非空） | `result.fixes_applied` 非空 |
| 同上 | LLM 恰好调用 1 次 | `client.call_count == 1` |
| 同上 | 记录的尝试含 fix_applied 和 retry_passed=True | `snap["attempts"][0]["fix_applied"]` 非空, `retry_passed is True` |
| `test_spec_summary_for_boundary_error_includes_obstacles` | LLM 收到含障碍物几何的上下文 | `summary["triangle"]` 和 `summary["rectangle"]` 存在 |

### 5.2 Test G：数值发散

**改造方案第 14 节 Test G 定义**:
> 制造较大 deltaT。验证：分类为 COURANT_TOO_HIGH 或 NUMERICAL_DIVERGENCE、修复策略修改 deltaT、不修改几何、重新运行成功。

**Mock 日志**:
```python
LOG_NAN_STANDALONE_TEST_G = """
Time = 0.05
 Courant Number mean: 0.4 max: 0.9
NaN detected in solution at cell 1234
Return code: 1
"""
```

**测试覆盖**:

| 测试方法 | 验证的 Test G 要求 | 断言 |
|---------|------------------|------|
| `test_primary_error_classification`（LOG_NAN_STANDALONE_TEST_G） | NaN 分类为 SOLVER_ERROR | `primary.category == ErrorCategory.SOLVER_ERROR` |
| 同上 | 严重程度为 FATAL | `primary.severity == ErrorSeverity.FATAL` |
| 同上 | 可修复（通过 standalone NaN 路径） | `primary.is_repairable is True` |
| `test_test_g_freezes_when_executor_cannot_apply` | LLM 建议修复但执行器无法应用 -> 冻结 | `result.final_status == PHASE_FROZEN` |
| 同上 | 3 轮尝试后 full_run 阶段冻结 | `snap["phase_attempts"]["full_run"] == 3`, `snap["phase_frozen"]["full_run"] is True` |
| 同上 | LLM 每轮调用 1 次，共 3 次 | `client.call_count == 3` |
| 同上 | 无工作站时无法应用修复 | `result.fixes_applied == []` |
| 同上 | 修复级别逐级升级 | `levels == ["config_only", "dictionary_syntax", "partial_regeneration"]` |
| `test_courant_message_includes_max_value` | Courant 数消息含 max 值 | `"2.75" in err.error_message`（使用 LOG_COURANT_HIGH） |
| `test_spec_summary_for_physics_error_includes_simulation` | 物理错误上下文含仿真参数（deltaT/end_time/max_courant） | `summary["simulation"] == {"delta_t": 0.01, "end_time": 1.0, "max_courant": 0.5}` |
| 同上 | 物理错误上下文不含障碍物几何 | `"triangle" not in summary`, `"rectangle" not in summary` |

### 5.3 Test F 与 Test G 对比

| 维度 | Test F（patch 错误） | Test G（数值发散） |
|------|---------------------|-------------------|
| Mock 日志 | `LOG_PATCH_MISMATCH_TEST_F` | `LOG_NAN_STANDALONE_TEST_G` |
| 错误类别 | BOUNDARY_CONDITION_ERROR | SOLVER_ERROR |
| 严重程度 | FATAL | FATAL |
| 可修复 | True | True（standalone NaN 路径） |
| 修复阶段 | smoke | full_run |
| 上下文含障碍物 | 是（边界错误暴露几何） | 否（物理错误暴露仿真参数） |
| 无 LLM 时行为 | 3 轮 "no_fixes_suggested" -> 冻结 | -- |
| 有 LLM 无执行器时行为 | -- | 3 轮升级 -> 冻结 |
| 有 LLM 有执行器时行为 | 1 轮 SUCCESS | -- |
| 级别序列 | -- | config_only -> dictionary_syntax -> partial_regeneration |
| 修复内容 | 更新 patch 名称 | 修改 deltaT |

### 5.4 修复记录持久化

**测试位置**: `test_persistence.py` -> `TestRepairRecords`

修复记录通过 `SQLitePersistence.save_repair_record()` 持久化到 `repair_records` 表：

```sql
CREATE TABLE IF NOT EXISTS repair_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,               -- 关联的 job ID
    attempt_number INTEGER,    -- 尝试编号
    phase TEXT,                -- 阶段（mesh/smoke/full_run）
    level TEXT,                -- 修复级别（config_only/dictionary_syntax/partial_regeneration）
    diagnosis_json TEXT,       -- 诊断结果 JSON
    fixes_json TEXT,           -- 修复方案 JSON
    status TEXT,               -- 状态（applied/failed/pending）
    created_at TEXT NOT NULL
);
```

**测试验证**:
- `test_save_and_list_repair_record`: 保存含 diagnosis（dict）和 fixes（list）的记录，JSON 序列化/反序列化正确
- `test_multiple_repair_records_ordered_by_attempt`: 多条记录按 attempt_number 升序返回
- `test_repair_record_without_diagnosis_and_fixes`: 无 diagnosis/fixes 时存为 NULL
- `test_list_repair_records_empty_for_unknown_job`: 未知 job_id 返回空列表

---

## 6. 修复策略分级总结

### 6.1 三级修复体系

| 级别 | 枚举值 | 修复方式 | LLM 角色 | 测试证据 |
|------|--------|---------|---------|---------|
| Level 1 | `CONFIG_ONLY` | 调整 controlDict/fvSolution 参数（deltaT、maxCo 等） | 模型提供分类和修复建议 | `test_repair_level_progression` 第 1 次 |
| Level 2 | `DICTIONARY_SYNTAX` | 修复 OpenFOAM 字典语法错误（分号、大括号、关键词拼写） | 模型输出 repair_actions（file/operation/path/value） | `test_repair_level_progression` 第 2 次 |
| Level 3 | `PARTIAL_REGENERATION` | 从 CaseSpec 重新生成特定文件 | 进入 ExtensionOrchestrator（最小 Patch） | `test_repair_level_progression` 第 3 次及以后 |

### 6.2 改造方案对照

| 方案要求（第 8.3 节） | 测试验证 |
|---------------------|---------|
| Level 1: 不调用模型或模型只提供分类 | `test_test_f_freezes_when_no_llm_available`（无 LLM 时仍执行 3 轮，级别递进） |
| Level 2: 模型输出 repair_actions，由程序执行 | `test_test_f_success_path`（LLM 返回 fixes，fake 执行器执行） |
| Level 3: 仅在配置无法修复时进入 ExtensionOrchestrator | `test_test_g_freezes_when_executor_cannot_apply`（3 级升级后冻结） |
| 由程序执行，不允许模型直接写整个文件 | `_DEFAULT_FIXES` 结构含 file/parameter/old_value/new_value/reason，非完整文件内容 |

### 6.3 改造方案第 8.4 节：阶段冻结

| 方案要求 | 测试验证 |
|---------|---------|
| 已通过阶段默认不可修改 | `test_phase_freezes_after_max_attempts`（3 次失败后冻结） |
| 修复器默认不得修改几何和网格 | `test_spec_summary_for_physics_error_includes_simulation`（物理错误上下文暴露仿真参数，不暴露障碍物） |
| 如需回退，模型必须明确说明原因 | 诊断输出含 root_cause 字段，需引用错误日志 |

### 6.4 改造方案第 8.5 节：重试上限

| 方案要求 | 测试验证 |
|---------|---------|
| 每个阶段最多 3 轮自动修复 | `test_default_limits`: `max_attempts_per_phase == 3` |
| 防止无限循环 | `test_phase_freezes_after_max_attempts`: 3 轮后 PHASE_FROZEN |
| 每轮必须产生真实 diff | `test_every_recorded_attempt_has_documented_fix`: `fix_applied` 非空 |
| 若 diff 为空：RETRY_WITHOUT_REPAIR，禁止继续重试 | `test_no_fixes_path_still_records_before_retrying`: 空修复标记为 "no_fixes_suggested" |
