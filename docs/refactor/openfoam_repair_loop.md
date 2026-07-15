# OpenFOAM 受控修复闭环设计文档

> **核心原则**: LLM 负责诊断和提出修改建议，程序负责执行和验证，OpenFOAM 负责判定正确性。模型不直接写文件，程序不盲目重试。每轮修复必须产生真实 diff，diff 为空则禁止重试。

---

## 目录

1. [错误分类系统](#1-错误分类系统)
2. [3级修复策略](#2-3级修复策略)
3. [修复策略限制](#3-修复策略限制)
4. [LLM诊断流程](#4-llm诊断流程)
5. [不变量: RETRY_WITHOUT_REPAIR 永不被允许](#5-不变量-retry_without_repair-永不被允许)

---

## 1. 错误分类系统

### 1.1 概述

OpenFOAM 仿真失败时，错误日志可能包含大量信息。`OpenFOAMErrorClassifier` 基于正则模式匹配日志，将错误归入 9 个类别之一，为后续修复策略选择提供依据。

**源文件**: `src/fluid_scientist/repair/error_classifier.py` → `OpenFOAMErrorClassifier`

### 1.2 错误类别枚举

**源文件**: `src/fluid_scientist/repair/error_classifier.py` → `ErrorCategory`

| 类别 | 枚举值 | 严重度 | 说明 |
|------|--------|--------|------|
| MESH_ERROR | `mesh_error` | high | 网格生成失败（blockMesh/snappyHexMesh/checkMesh 错误） |
| BOUNDARY_CONDITION_ERROR | `boundary_condition_error` | high | 边界条件配置错误（patch 不匹配、类型不一致） |
| SOLVER_ERROR | `solver_error` | high | 求解器配置错误（线性求解器发散、矩阵奇异） |
| PHYSICS_ERROR | `physics_error` | medium | 物理参数错误（CFL 过大、负体积、NaN） |
| FILE_ERROR | `file_error` | medium | 文件缺失或路径错误 |
| SYNTAX_ERROR | `syntax_error` | medium | 字典语法错误（分号缺失、括号不匹配） |
| MEMORY_ERROR | `memory_error` | high | 内存不足 |
| TIMEOUT_ERROR | `timeout_error` | medium | 执行超时 |
| UNKNOWN_ERROR | `unknown_error` | low | 无法分类的错误 |

### 1.3 分类模式匹配

**源文件**: `src/fluid_scientist/repair/error_classifier.py` → `OpenFOAMErrorClassifier._PATTERNS`

每个类别对应一组正则表达式模式，按优先级顺序匹配。匹配到第一个模式即返回对应类别。

#### MESH_ERROR 模式

```python
[
    r"blockMesh.*[Ee]rror",
    r"snappyHexMesh.*[Ee]rror",
    r"checkMesh.*[Ff]ail",
    r"mesh.*not.*valid",
    r"cell.*[0-9]+.*owner.*neighbour",
    r"faceZone.*not.*found",
    r"boundary.*patch.*not.*found",
    r"number of cells.*zero",
]
```

典型匹配日志:
```
--> FOAM FATAL ERROR: number of cells zero
--> FOAM Warning : cell 1234 has owner neighbour 5678
```

#### BOUNDARY_CONDITION_ERROR 模式

```python
[
    r"patch.*not.*found",
    r"boundary.*field.*not.*found",
    r"physicalType.*mismatch",
    r"inletOutlet.*not.*defined",
    r"patchField.*type.*not.*found",
]
```

典型匹配日志:
```
--> FOAM FATAL ERROR: patch 'inlet' not found in field U
```

#### SOLVER_ERROR 模式

```python
[
    r"[Ss]olver.*[Ff]ail",
    r"[Mm]atrix.*[Ss]ingular",
    r"linear.*solver.*diverge",
    r"PCG.*not.*converge",
    r"GAMG.*not.*converge",
    r"Solution.*not.*converge",
    r"residual.*not.*decrease",
]
```

#### PHYSICS_ERROR 模式

```python
[
    r"CFL.*exceed",
    r"Courant.*number.*exceed",
    r"[Nn]a[Nn].*detect",
    r"negative.*volume",
    r"divergence.*detect",
    r"unbounded.*result",
]
```

典型匹配日志:
```
Courant Number mean: 5.234 max: 15.67  (超过 1.0)
NaN detected in field U at cell 1234
```

#### FILE_ERROR 模式

```python
[
    r"[Ff]ile.*not.*found",
    r"cannot.*open.*file",
    r"[Pp]ath.*not.*exist",
    r"No.*such.*file",
]
```

#### SYNTAX_ERROR 模式

```python
[
    r"unexpected.*token",
    r"missing.*semicolon",
    r"unmatched.*bracket",
    r"parse.*error",
    r"ill.*formed.*entry",
    r"keyword.*not.*found.*in.*dictionary",
]
```

#### MEMORY_ERROR 模式

```python
[
    r"[Oo]ut.*of.*memory",
    r"[Mm]emory.*exhaust",
    r"bad.*alloc",
    r"[Ss]egmentation.*fault",
]
```

#### TIMEOUT_ERROR 模式

```python
[
    r"[Tt]imeout",
    r"[Ee]xceed.*time.*limit",
    r"[Ee]xceed.*wall.*time",
]
```

### 1.4 分类结果模型

```python
# 源文件: src/fluid_scientist/repair/error_classifier.py

@dataclass
class ClassifiedError:
    category: ErrorCategory        # 错误类别
    sub_type: str | None           # 子类型（如 mesh_error 下的 "patch_not_found"）
    severity: ErrorSeverity        # 严重度: high | medium | low
    error_message: str             # 完整错误消息
    error_location: str | None     # 错误位置（文件名:行号）
    relevant_files: list[str]      # 相关文件列表
    raw_log: str                   # 原始日志片段
    confidence: float              # 分类置信度 0.0-1.0
```

### 1.5 多错误处理

当日志中同时出现多种错误时:
1. 按 `MESH_ERROR > BOUNDARY_CONDITION_ERROR > SOLVER_ERROR > PHYSICS_ERROR > MEMORY_ERROR > FILE_ERROR > SYNTAX_ERROR > TIMEOUT_ERROR > UNKNOWN_ERROR` 优先级排序
2. 取最高优先级错误作为主错误
3. 其余错误记录在 `secondary_errors` 列表中

---

## 2. 3级修复策略

### 2.1 修复级别枚举

**源文件**: `src/fluid_scientist/repair/repair_policy.py` → `RepairLevel`

| 级别 | 枚举值 | 说明 | 操作范围 |
|------|--------|------|----------|
| CONFIG_ONLY | `config_only` | 仅修改 OpenFOAM 字典配置参数 | 修改 `controlDict`、`fvSchemes`、`fvSolution` 等字典中的数值参数 |
| DICTIONARY_SYNTAX | `dictionary_syntax` | 修改字典语法和结构 | 修正分号、括号、字段名、patch 名 |
| PARTIAL_REGENERATION | `partial_regeneration` | 部分重新生成 Case 文件 | 重新生成 blockMeshDict、snappyHexMeshDict 或 boundary 文件 |

### 2.2 级别选择规则

**源文件**: `src/fluid_scientist/repair/repair_policy.py` → `RepairPolicy.select_repair_level()`

修复级别的选择基于错误类别:

```python
LEVEL_FOR_CATEGORY = {
    ErrorCategory.MESH_ERROR:             RepairLevel.PARTIAL_REGENERATION,
    ErrorCategory.BOUNDARY_CONDITION_ERROR: RepairLevel.DICTIONARY_SYNTAX,
    ErrorCategory.SOLVER_ERROR:            RepairLevel.CONFIG_ONLY,
    ErrorCategory.PHYSICS_ERROR:           RepairLevel.CONFIG_ONLY,
    ErrorCategory.FILE_ERROR:              RepairLevel.DICTIONARY_SYNTAX,
    ErrorCategory.SYNTAX_ERROR:            RepairLevel.DICTIONARY_SYNTAX,
    ErrorCategory.MEMORY_ERROR:            RepairLevel.CONFIG_ONLY,
    ErrorCategory.TIMEOUT_ERROR:           RepairLevel.CONFIG_ONLY,
    ErrorCategory.UNKNOWN_ERROR:           RepairLevel.CONFIG_ONLY,
}
```

### 2.3 升级机制

**源文件**: `src/fluid_scientist/repair/repair_policy.py` → `RepairPolicy.should_escalate()`

当当前级别的修复尝试耗尽后，自动升级到下一级别:

```text
CONFIG_ONLY（最多 3 轮）
    ↓ 升级
DICTIONARY_SYNTAX（最多 3 轮）
    ↓ 升级
PARTIAL_REGENERATION（最多 3 轮）
    ↓ 耗尽
REPAIR_EXHAUSTED → 返回失败
```

升级条件:
1. 当前级别已用尽 `max_attempts_per_phase`（默认 3）轮尝试
2. 或者连续 2 轮修复产生的 diff 完全相同（陷入循环）

### 2.4 各级别允许的操作

#### CONFIG_ONLY 允许的操作

| 操作 | 目标文件 | 说明 |
|------|----------|------|
| `adjust_timestep` | `controlDict` | 减小 deltaT |
| `adjust_endtime` | `controlDict` | 减小 endTime |
| `adjust_write_interval` | `controlDict` | 调整 writeInterval |
| `adjust_solver_tolerance` | `fvSolution` | 调整线性求解器容差 |
| `adjust_relaxation` | `fvSolution` | 调整欠松弛因子 |
| `adjust_schemes` | `fvSchemes` | 降低离散格式阶数（如 linearUpwind → upwind） |
| `adjust_cfl` | `controlDict` | 调整最大 CFL 数 |

#### DICTIONARY_SYNTAX 允许的操作

| 操作 | 目标文件 | 说明 |
|------|----------|------|
| `fix_missing_semicolon` | 任意字典 | 补充分号 |
| `fix_bracket_mismatch` | 任意字典 | 修正括号 |
| `fix_patch_name` | `boundary`、`U`、`p` 等 | 修正 patch 名称 |
| `fix_field_type` | 边界场文件 | 修正边界场类型（如 fixedValue → inletValue） |
| `add_missing_entry` | 任意字典 | 补充缺失的字典条目 |

#### PARTIAL_REGENERATION 允许的操作

| 操作 | 目标文件 | 说明 |
|------|----------|------|
| `regenerate_blockmesh` | `blockMeshDict` | 重新生成 blockMeshDict |
| `regenerate_snappy` | `snappyHexMeshDict` | 重新生成 snappyHexMeshDict |
| `regenerate_boundary` | `polyMesh/boundary` | 重新生成 boundary 文件 |
| `simplify_mesh` | `blockMeshDict` | 简化网格（减少分级、增大 cellSize） |

### 2.5 禁止操作

所有级别均禁止:
- 禁止修改用户指定的物理参数（Re、入口速度、几何尺寸）
- 禁止删除用户要求的观测量
- 禁止改变流动类型（层流↔湍流）
- 禁止修改 `constant/transportProperties` 中的运动黏度（用户明确指定时）
- LLM 不得直接写文件，只能输出修复建议

---

## 3. 修复策略限制

### 3.1 全局限制参数

**源文件**: `src/fluid_scientist/repair/repair_policy.py` → `RepairPolicy`

```python
class RepairPolicy:
    max_attempts_per_phase: int = 3   # 每个修复级别最多尝试 3 轮
    max_global_attempts: int = 10     # 全局最多尝试 10 轮
    max_phase_escalations: int = 2    # 最多升级 2 次（CONFIG_ONLY → DICTIONARY_SYNTAX → PARTIAL_REGENERATION）
    enable_phase_freezing: bool = True  # 启用阶段冻结
    enable_loop_detection: bool = True  # 启用循环检测
    loop_detection_threshold: int = 2  # 连续 2 轮相同 diff 触发循环检测
```

### 3.2 阶段冻结（Phase Freezing）

**源文件**: `src/fluid_scientist/repair/repair_policy.py` → `RepairPolicy.freeze_phase()`

当某个修复级别连续失败达到 `max_attempts_per_phase` 后，该级别被冻结:

```python
frozen_phases: set[RepairLevel]  # 已冻结的修复级别集合

def freeze_phase(self, level: RepairLevel):
    """冻结指定修复级别，禁止后续再使用该级别。"""
    self.frozen_phases.add(level)

def is_phase_frozen(self, level: RepairLevel) -> bool:
    """检查指定修复级别是否已冻结。"""
    return level in self.frozen_phases

def get_available_levels(self) -> list[RepairLevel]:
    """获取当前可用的修复级别（排除已冻结的）。"""
    all_levels = [RepairLevel.CONFIG_ONLY, RepairLevel.DICTIONARY_SYNTAX, RepairLevel.PARTIAL_REGENERATION]
    return [lv for lv in all_levels if lv not in self.frozen_phases]
```

冻结后:
- 该级别不再被选择
- 升级到下一可用级别
- 如果所有级别都被冻结 → 返回 `REPAIR_EXHAUSTED`

### 3.3 循环检测

**源文件**: `src/fluid_scientist/repair/repair_policy.py` → `RepairPolicy.detect_loop()`

```python
def detect_loop(self, attempt_history: list[RepairAttempt]) -> bool:
    """检测是否陷入修复循环（连续 N 轮产生相同 diff）。"""
    if len(attempt_history) < self.loop_detection_threshold:
        return False
    recent_diffs = [a.diff_hash for a in attempt_history[-self.loop_detection_threshold:]]
    return len(set(recent_diffs)) == 1  # 所有 diff hash 相同
```

当检测到循环时:
- 立即升级到下一修复级别
- 如果已在最高级别 → 返回 `REPAIR_EXHAUSTED`

### 3.4 全局尝试计数

```python
def can_retry(self) -> bool:
    """检查是否还能继续修复。"""
    return self.global_attempt_count < self.max_global_attempts

def record_attempt(self, attempt: RepairAttempt):
    """记录一次修复尝试。"""
    self.global_attempt_count += 1
    self.phase_attempt_count[attempt.level] += 1
    if self.phase_attempt_count[attempt.level] >= self.max_attempts_per_phase:
        self.freeze_phase(attempt.level)
```

### 3.5 修复尝试记录模型

```python
# 源文件: src/fluid_scientist/repair/repair_policy.py

@dataclass
class RepairAttempt:
    attempt_id: str               # 尝试 ID
    level: RepairLevel            # 修复级别
    error_category: ErrorCategory # 错误类别
    llm_diagnosis: str | None     # LLM 诊断结果
    fix_actions: list[FixAction]  # 执行的修复操作列表
    diff: str                     # 产生的 diff
    diff_hash: str                # diff 的 SHA256 哈希
    fix_applied: bool             # 修复是否成功应用
    validation_result: str | None # 验证结果
    error_log: str | None         # 错误日志
    timestamp: str                # 时间戳
```

### 3.6 限制总结

| 限制项 | 值 | 说明 |
|--------|----|------|
| 每级别最大尝试轮数 | 3 | 超过后冻结该级别 |
| 全局最大尝试轮数 | 10 | 超过后返回 `REPAIR_EXHAUSTED` |
| 最大升级次数 | 2 | CONFIG_ONLY → DICTIONARY_SYNTAX → PARTIAL_REGENERATION |
| 循环检测阈值 | 2 | 连续 2 轮相同 diff 触发升级 |
| 阶段冻结 | 启用 | 冻结后该级别不再可用 |
| 循环检测 | 启用 | 防止无限相同修复 |

---

## 4. LLM诊断流程

### 4.1 流程概览

```text
OpenFOAM 执行失败
  ↓
ErrorClassifier.classify(log)                    ← src/fluid_scientist/repair/error_classifier.py
  ↓
ClassifiedError（错误类别 + 相关文件 + 日志）
  ↓
RepairPolicy.select_repair_level(category)       ← src/fluid_scientist/repair/repair_policy.py
  ↓
RepairLevel（CONFIG_ONLY / DICTIONARY_SYNTAX / PARTIAL_REGENERATION）
  ↓
RepairContextBuilder.build(error, level)         ← src/fluid_scientist/repair/repair_context_builder.py
  ↓
RepairContext（最小相关文件集 + 日志摘要 + 当前配置）
  ↓
LLMDiagnoser.diagnose(context)                   ← src/fluid_scientist/repair/llm_diagnoser.py
  ↓
LLMDiagnosis（root_cause + fix_actions）
  ↓
ControlledRepairExecutor.execute(diagnosis)      ← src/fluid_scientist/repair/controlled_repair_executor.py
  ↓
RepairAttempt（diff + 验证结果）
  ↓
验证通过？
  ├─ 是 → 修复成功，恢复流程
  └─ 否 → RepairPolicy.can_retry()？
       ├─ 是 → 升级或重试
       └─ 否 → REPAIR_EXHAUSTED
```

**编排器**: `src/fluid_scientist/repair/repair_orchestrator.py` → `RepairOrchestrator.repair()`

### 4.2 RepairContextBuilder

**源文件**: `src/fluid_scientist/repair/repair_context_builder.py` → `RepairContextBuilder`

按错误类型收集最小相关文件和日志，避免向 LLM 发送整个 Case 目录。

```python
class RepairContextBuilder:
    def build(self, error: ClassifiedError, level: RepairLevel) -> RepairContext:
        """构建修复上下文。"""
        relevant_files = self._collect_relevant_files(error, level)
        log_summary = self._summarize_log(error.raw_log)
        current_config = self._extract_current_config(relevant_files)
        return RepairContext(
            error=error,
            repair_level=level,
            relevant_files=relevant_files,
            log_summary=log_summary,
            current_config=current_config,
        )
```

#### 各错误类别收集的文件

| 错误类别 | 收集的文件 |
|----------|-----------|
| MESH_ERROR | `blockMeshDict`、`snappyHexMeshDict`、`checkMesh.log`、`polyMesh/boundary` |
| BOUNDARY_CONDITION_ERROR | `0/U`、`0/p`、`polyMesh/boundary`、相关边界场文件 |
| SOLVER_ERROR | `system/fvSolution`、`system/controlDict`、求解器日志 |
| PHYSICS_ERROR | `system/controlDict`、`system/fvSchemes`、`constant/transportProperties`、残差日志 |
| FILE_ERROR | 缺失文件路径、相关目录列表 |
| SYNTAX_ERROR | 报错文件、同目录字典文件 |
| MEMORY_ERROR | `system/controlDict`、`system/decomposeParDict` |
| TIMEOUT_ERROR | `system/controlDict`、执行日志 |
| UNKNOWN_ERROR | `system/controlDict`、完整日志摘要 |

#### 日志摘要规则

```python
def _summarize_log(self, raw_log: str) -> str:
    """提取日志关键信息，限制在 2000 字符以内。"""
    lines = raw_log.split('\n')
    # 1. 提取 FOAM FATAL ERROR / FOAM Warning 行
    # 2. 提取错误前后 5 行上下文
    # 3. 提取 Courant Number、residual 等关键数值
    # 4. 限制总长度
    return summary
```

### 4.3 LLMDiagnoser

**源文件**: `src/fluid_scientist/repair/llm_diagnoser.py` → `LLMDiagnoser`

#### 诊断 Prompt

**源文件**: `src/fluid_scientist/repair/llm_diagnoser.py` → `_DIAGNOSIS_SYSTEM_PROMPT`

系统 Prompt 核心规则:
1. 分析错误根因，输出 `root_cause`
2. 提出 `fix_actions`，每个 action 包含: 文件路径、操作类型、具体修改
3. 修改必须在当前 `repair_level` 允许的范围内
4. 禁止修改用户指定的物理参数
5. 禁止输出完整文件内容，只输出 diff 或键值对修改
6. 必须解释每个修改的原因

#### 诊断输入

```python
# 源文件: src/fluid_scientist/repair/llm_diagnoser.py

def diagnose(self, context: RepairContext) -> LLMDiagnosis:
    user_prompt = self._build_user_prompt(context)
    response = self._call_llm(
        system_prompt=self._DIAGNOSIS_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0,
        response_format="json",
    )
    return self._parse_diagnosis(response)
```

User Prompt 结构:
```text
## 错误类别
{error.category} ({error.sub_type})

## 错误消息
{error.error_message}

## 错误位置
{error.error_location}

## 日志摘要
{context.log_summary}

## 当前修复级别
{context.repair_level}（允许的操作: {allowed_actions}）

## 相关文件内容
### {file_path}
{file_content}
...

## 禁止修改的参数
- Reynolds number
- inlet_velocity
- cylinder radius
- obstacle dimensions
- flow type (laminar/turbulent)

## 请输出
root_cause: 根因分析
fix_actions: 修复操作列表
```

#### 诊断输出模型

```python
# 源文件: src/fluid_scientist/repair/llm_diagnoser.py

@dataclass
class FixAction:
    file_path: str              # 目标文件
    action_type: str            # 操作类型: set_value / fix_syntax / regenerate / add_entry
    key_path: str | None        # 键路径（如 "application" 或 "dt"）
    old_value: str | None       # 旧值
    new_value: str              # 新值
    reason: str                 # 修改原因

@dataclass
class LLMDiagnosis:
    root_cause: str             # 根因分析
    fix_actions: list[FixAction]# 修复操作列表
    confidence: float           # 置信度
    raw_response: str           # 原始 LLM 响应
```

#### LLM 不可用时的行为

```python
def diagnose(self, context: RepairContext) -> LLMDiagnosis:
    if self._llm_client is None:
        return LLMDiagnosis(
            root_cause="LLM client not available",
            fix_actions=[],
            confidence=0.0,
            raw_response="",
        )
    try:
        ...
    except Exception as e:
        return LLMDiagnosis(
            root_cause=f"LLM diagnosis failed: {e}",
            fix_actions=[],
            confidence=0.0,
            raw_response="",
        )
```

当 LLM 不可用时，`fix_actions` 为空，`ControlledRepairExecutor` 将无法执行修复，流程进入 `REPAIR_EXHAUSTED`。

### 4.4 ControlledRepairExecutor

**源文件**: `src/fluid_scientist/repair/controlled_repair_executor.py` → `ControlledRepairExecutor`

#### 执行流程

```python
class ControlledRepairExecutor:
    def execute(self, diagnosis: LLMDiagnosis, case_path: str) -> RepairAttempt:
        """执行 LLM 诊断建议的修复。"""
        if not diagnosis.fix_actions:
            return RepairAttempt(
                fix_applied=False,
                diff="",
                diff_hash="empty",
                error_log="No fix actions from LLM diagnosis",
            )

        diffs = []
        for action in diagnosis.fix_actions:
            diff = self._apply_single_fix(action, case_path)
            if diff:
                diffs.append(diff)

        combined_diff = "\n".join(diffs)
        diff_hash = hashlib.sha256(combined_diff.encode()).hexdigest()

        return RepairAttempt(
            fix_applied=len(diffs) > 0,
            diff=combined_diff,
            diff_hash=diff_hash,
        )
```

#### 单个修复执行

```python
def _apply_single_fix(self, action: FixAction, case_path: str) -> str | None:
    """执行单个修复操作，返回 diff 字符串。"""
    file_path = os.path.join(case_path, action.file_path)

    # 读取原文件
    original = self._read_remote_file(file_path)

    # 根据操作类型执行修改
    if action.action_type == "set_value":
        modified = self._set_dict_value(original, action.key_path, action.new_value)
    elif action.action_type == "fix_syntax":
        modified = self._fix_syntax(original, action.key_path, action.new_value)
    elif action.action_type == "add_entry":
        modified = self._add_dict_entry(original, action.key_path, action.new_value)
    elif action.action_type == "regenerate":
        modified = self._regenerate_file(action.file_path, action.new_value)
    else:
        return None

    if modified == original:
        return None  # 无变化

    # 通过 SSH 写回远程文件
    self._write_remote_file(file_path, modified)

    # 生成 unified diff
    return difflib.unified_diff(
        original.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        fromfile=f"a/{action.file_path}",
        tofile=f"b/{action.file_path}",
    )
```

#### 修复验证

```python
def _validate_repair(self, case_path: str, level: RepairLevel) -> str | None:
    """验证修复后的 Case 是否有效。"""
    # CONFIG_ONLY: 静态验证字典语法
    if level == RepairLevel.CONFIG_ONLY:
        return self._validate_dicts(case_path)

    # DICTIONARY_SYNTAX: 验证字典 + blockMesh -help
    if level == RepairLevel.DICTIONARY_SYNTAX:
        dict_check = self._validate_dicts(case_path)
        if dict_check:
            return dict_check
        return self._validate_blockmesh(case_path)

    # PARTIAL_REGENERATION: blockMesh + checkMesh
    if level == RepairLevel.PARTIAL_REGENERATION:
        mesh_check = self._validate_blockmesh(case_path)
        if mesh_check:
            return mesh_check
        return self._validate_checkmesh(case_path)

    return None  # 验证通过
```

验证依赖远程工作站 SSH 连接和 OpenFOAM 环境。无连接时返回 `"validation_skipped: no workstation connection"`。

### 4.5 RepairOrchestrator

**源文件**: `src/fluid_scientist/repair/repair_orchestrator.py` → `RepairOrchestrator`

#### 编排主循环

```python
class RepairOrchestrator:
    def repair(self, error_log: str, case_path: str) -> RepairResult:
        """编排完整的修复流程。"""
        # 1. 错误分类
        classified = self._classifier.classify(error_log)

        # 2. 初始化修复策略
        policy = RepairPolicy()
        attempts: list[RepairAttempt] = []

        while policy.can_retry():
            # 3. 选择修复级别
            level = policy.select_repair_level(classified.category)
            if level is None:
                break  # 所有级别已冻结

            # 4. 构建上下文
            context = self._context_builder.build(classified, level)

            # 5. LLM 诊断
            diagnosis = self._diagnoser.diagnose(context)

            # 6. 执行修复
            attempt = self._executor.execute(diagnosis, case_path)
            attempts.append(attempt)

            # 7. 不变量检查: RETRY_WITHOUT_REPAIR
            if not attempt.fix_applied:
                # 无 diff → 禁止重试
                return RepairResult(
                    success=False,
                    reason="RETRY_WITHOUT_REPAIR_DETECTED",
                    attempts=attempts,
                )

            # 8. 验证修复
            validation = self._executor._validate_repair(case_path, level)
            attempt.validation_result = validation

            if validation is None:
                # 验证通过
                return RepairResult(success=True, attempts=attempts)

            # 9. 记录尝试
            policy.record_attempt(attempt)

            # 10. 循环检测
            if policy.detect_loop(attempts):
                policy.freeze_phase(level)
                continue

            # 11. 阶段升级
            if policy.is_phase_frozen(level):
                continue  # 下一轮自动选择更高级别

        return RepairResult(
            success=False,
            reason="REPAIR_EXHAUSTED",
            attempts=attempts,
        )
```

#### 修复结果模型

```python
@dataclass
class RepairResult:
    success: bool                   # 修复是否成功
    reason: str                     # 失败原因（成功时为空）
    attempts: list[RepairAttempt]   # 所有修复尝试记录
    total_diff: str                 # 累计 diff
    repaired_files: list[str]       # 被修改的文件列表
```

---

## 5. 不变量: RETRY_WITHOUT_REPAIR 永不被允许

### 5.1 不变量定义

**核心规则**: 每轮修复必须产生真实的文件修改（diff）。如果一轮修复的 diff 为空，则禁止重试，整个修复流程立即终止。

```text
IF attempt.fix_applied == False OR attempt.diff_hash == "empty":
    → 立即返回 RepairResult(success=False, reason="RETRY_WITHOUT_REPAIR_DETECTED")
    → 禁止继续重试
```

### 5.2 为什么需要这个不变量

| 场景 | 没有不变量的后果 | 有不变量的保护 |
|------|-----------------|---------------|
| LLM 诊断返回空 fix_actions | 程序盲目重试，无限循环 | 立即终止，返回失败 |
| LLM 建议的修改与当前值相同 | diff 为空，但程序认为"已修复" | 立即终止，暴露问题 |
| LLM 不可用 | 每轮都空转 | 立即终止，提示 LLM 不可用 |
| 修复操作执行失败 | 程序继续重试相同操作 | 立即终止，记录失败原因 |

### 5.3 实现细节

**源文件**: `src/fluid_scientist/repair/repair_orchestrator.py` → `RepairOrchestrator.repair()`

```python
# 在每轮修复执行后立即检查
attempt = self._executor.execute(diagnosis, case_path)
attempts.append(attempt)

# 不变量检查
if not attempt.fix_applied:
    return RepairResult(
        success=False,
        reason="RETRY_WITHOUT_REPAIR_DETECTED",
        attempts=attempts,
    )
```

**源文件**: `src/fluid_scientist/repair/controlled_repair_executor.py` → `ControlledRepairExecutor.execute()`

```python
# 当没有 fix_actions 时，返回 fix_applied=False
if not diagnosis.fix_actions:
    return RepairAttempt(
        fix_applied=False,  # 触发不变量
        diff="",
        diff_hash="empty",
        error_log="No fix actions from LLM diagnosis",
    )

# 当所有 fix_action 执行后无变化时
combined_diff = "\n".join(diffs)
if not combined_diff:
    return RepairAttempt(
        fix_applied=False,  # 触发不变量
        diff="",
        diff_hash="empty",
        error_log="All fix actions produced no changes",
    )
```

### 5.4 不变量违反时的处理

当 `RETRY_WITHOUT_REPAIR` 被检测到时:

1. **立即终止修复流程** — 不进入下一轮
2. **返回失败结果** — `RepairResult(success=False, reason="RETRY_WITHOUT_REPAIR_DETECTED")`
3. **保存所有尝试记录** — `attempts` 列表保留完整审计轨迹
4. **通知用户** — 前端显示"自动修复无法继续，请检查 LLM 服务或手动修改 Case"
5. **允许用户手动介入** — 用户可以手动修改 Case 后重试仿真

### 5.5 其他不变量

| 不变量 | 说明 | 源文件 |
|--------|------|--------|
| `RETRY_WITHOUT_REPAIR` | diff 为空则禁止重试 | `repair_orchestrator.py` |
| `MAX_GLOBAL_ATTEMPTS` | 全局最多 10 轮 | `repair_policy.py` → `max_global_attempts` |
| `MAX_PHASE_ATTEMPTS` | 每级别最多 3 轮 | `repair_policy.py` → `max_attempts_per_phase` |
| `NO_PHYSICS_OVERRIDE` | 禁止修改用户物理参数 | `llm_diagnoser.py` → Prompt 约束 + `controlled_repair_executor.py` → 操作白名单 |
| `NO_LLM_FILE_WRITE` | LLM 不直接写文件 | `llm_diagnoser.py` 只输出建议，`controlled_repair_executor.py` 执行修改 |
| `NO_SILENT_FALLBACK` | LLM 失败不静默降级 | `llm_diagnoser.py` 返回空 fix_actions → 触发 `RETRY_WITHOUT_REPAIR` |

### 5.6 修复流程状态机

```text
                    ┌──────────────────┐
                    │  REPAIR_PENDING  │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ CLASSIFYING_ERROR│
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  SELECTING_LEVEL │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  BUILDING_CONTEXT │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  LLM_DIAGNOSING  │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  EXECUTING_REPAIR│
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ diff 为空?       │──是──→ REPAIR_FAILED (RETRY_WITHOUT_REPAIR)
                    └────────┬─────────┘
                             │ 否
                    ┌────────▼─────────┐
                    │ VALIDATING_REPAIR│
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
     │ 验证通过      │ │ 循环检测 │ │ 可以重试?   │
     └────────┬──────┘ └────┬─────┘ └──────┬──────┘
              │              │              │
     ┌────────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
     │ REPAIR_SUCCESS│ │ 冻结级别 │ │ REPAIR_     │
     └───────────────┘ │ 升级     │ │ EXHAUSTED   │
                       └────┬─────┘ └─────────────┘
                            │
                    回到 SELECTING_LEVEL
```

---

## 附录: 修复记录持久化

所有修复尝试记录保存到 `repair_records` 中，确保审计可追溯:

```json
{
  "repair_session_id": "repair-2026-07-15-001",
  "case_path": "/home/user/case_cylinder_Re200",
  "error_category": "PHYSICS_ERROR",
  "error_message": "Courant Number mean: 5.234 max: 15.67",
  "total_attempts": 3,
  "final_status": "REPAIR_SUCCESS",
  "attempts": [
    {
      "attempt_id": "attempt-1",
      "level": "CONFIG_ONLY",
      "llm_diagnosis": "CFL number too high, reduce timestep",
      "fix_actions": [
        {"file_path": "system/controlDict", "action_type": "set_value", "key_path": "deltaT", "old_value": "0.01", "new_value": "0.005", "reason": "Reduce timestep to lower CFL"}
      ],
      "diff_hash": "sha256:abc123...",
      "fix_applied": true,
      "validation_result": "Courant Number mean: 2.567 max: 7.89 (still > 1.0)"
    },
    {
      "attempt_id": "attempt-2",
      "level": "CONFIG_ONLY",
      "llm_diagnosis": "CFL still too high, further reduce timestep",
      "fix_actions": [
        {"file_path": "system/controlDict", "action_type": "set_value", "key_path": "deltaT", "old_value": "0.005", "new_value": "0.002", "reason": "Further reduce timestep"}
      ],
      "diff_hash": "sha256:def456...",
      "fix_applied": true,
      "validation_result": null
    }
  ],
  "total_diff": "--- a/system/controlDict\n+++ b/system/controlDict\n@@ -1,7 +1,7 @@\n-deltaT 0.01;\n+deltaT 0.002;\n",
  "repaired_files": ["system/controlDict"]
}
```
