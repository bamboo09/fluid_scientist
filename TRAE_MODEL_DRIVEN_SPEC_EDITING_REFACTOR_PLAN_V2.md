# Fluid Scientist 模型驱动根因重构施工方案

> **执行者：Trae**
>
> **仓库：** `bamboo09/fluid_scientist`  
> **目标运行环境：** 当前用户正在使用的聊天式版本 + OpenFOAM Foundation 13 工作站  
> **本文件优先级：** 高于此前所有“为单个参数打补丁”“为单个形状增加枚举”“继续扩充固定模板”的方案  
> **核心目标：** 让大模型成为“当前仿真方案的通用语义编辑器和科研规划器”，而不是关键词分类器；让任意参数、任意场景、多轮修改统一进入同一条可验证链路。

---

# 0. 开始前先读：本次到底要解决什么

用户当前遇到的直接问题是：

- 用户说“仿真时间设为 15 秒”，系统识别不了；
- 用户继续修改其他参数，右侧方案不更新或丢失之前的信息；
- 每暴露一个新参数，开发者就添加一段关键词、正则、枚举或专用接口；
- 三角形可能被错误识别成 `cosine_bell`；
- 换一个研究场景，旧补丁和固定模板几乎全部失效；
- 模型看起来被调用了，但并没有真正负责理解、修改和审查方案；
- 模型输出失败时，系统可能仍然用规则、默认模板或 fake 数据伪装成功；
- 模型编码能力不足，却被要求直接生成可靠 OpenFOAM 文件，导致质量不可控。

这些问题不能通过继续添加如下代码解决：

```python
if "仿真时间" in user_text:
    plan.end_time = extract_number(user_text)

if "三角形" in user_text:
    shape = "triangle"

if "矩形" in user_text:
    shape = "rectangle"
```

本次必须从根源改成：

```text
任意用户消息
→ 读取同一会话的完整当前方案
→ 强模型理解“新增/修改/删除/确认/否定/澄清”
→ 输出通用 SimulationSpecPatch
→ 通用 Patch Engine 做路径、类型、单位和约束校验
→ Dependency Engine 分析连锁影响并重算派生量
→ 生成清晰 diff
→ 用户确认高风险修改
→ 更新同一份方案的新版本
→ 生成开放 CaseIR
→ 确定性编译 OpenFOAM 13 case
→ 工作站执行、后处理、验证和报告
```

这意味着：

- “仿真时间设为 15 秒”
- “时间步缩小一半”
- “把空气改成水”
- “三角形改成矩形”
- “圆柱向上移动 0.2 米”
- “添加出口前 1 米处的速度探针”
- “上边界改成滑移”
- “只统计最后 5 秒”
- “增加涡量动画”
- “删除压力云图”
- “把 RANS 改成 LES”
- “新增一个未见过的多边形障碍”

全部使用同一套语义编辑、Patch、依赖分析和验证机制。不得为每一项再写专用业务补丁。

---

# 1. 绝对禁止事项

以下行为一律视为本次任务失败。

## 1.1 禁止继续打字段补丁

核心业务代码中禁止新增：

```python
if "仿真时间" in ...
if "结束时间" in ...
if "三角形" in ...
if "矩形" in ...
if "改成水" in ...
```

同义词、自然语言理解必须由模型和专业 Skill 完成。程序只处理结构化结果。

允许这些词只出现在：

- 测试样例；
- Prompt 示例；
- Skill 参考文档；
- Eval 数据。

## 1.2 禁止未知值回退成最近模板

禁止：

```python
shape = known_shapes.get(model_shape, "cosine_bell")
scenario = known_scenarios.get(intent, "cylinder_flow")
solver = solver_map.get(problem, "simpleFoam")
```

无法表达时必须返回：

```text
UNKNOWN_CAPABILITY
```

并保留用户原始语义。

## 1.3 禁止静默 fallback

真实模式中以下错误不能继续生成“成功方案”：

- API 超时；
- 模型返回非 JSON；
- Structured Output 校验失败；
- parser 失败；
- schema 不支持字段；
- patch 无法应用；
- 模型 ID 不存在；
- provider 私自换成小模型；
- Skill 加载失败。

必须明确返回失败状态，不得自动切到：

- fake model；
- regex；
-固定模板；
-旧 plan；
-上一次成功结果；
-手写默认推荐。

## 1.4 禁止模型自由执行

模型不得直接：

- 执行 Shell；
-拼接 SSH；
-访问密钥；
-修改工作站系统文件；
-直接覆盖 OpenFOAM case；
-自由生成并执行 Python；
-声称“已经跑通”但无 run ID 和 artifact。

## 1.5 禁止随意破坏当前版本

禁止：

```bash
git reset --hard
git clean -fd
git clean -fdx
git restore .
git checkout -- .
git push --force
rm -rf src
rm -rf apps
rm -rf tests
```

禁止用历史分支整个目录覆盖当前聊天式版本。

## 1.6 禁止只做界面假效果

右侧参数变化必须来自后端 canonical spec 新版本，不能只改前端 local state。

## 1.7 禁止只通过单元测试宣称完成

必须有：

- 真实模型请求证据；
-真实方案 Patch；
-真实编译制品；
-真实 OpenFOAM Foundation 13 工作站 run；
-真实图片、动画、指标和报告。

---

# 2. 第一阶段：准确锁定当前实际运行版本

不要假设 `main`、某个名字带 `chat` 的分支或最近修改时间就是当前运行版本。

## 2.1 收集 Git 基线

在仓库根目录执行：

```bash
pwd
git rev-parse --show-toplevel
git status --short --branch
git rev-parse HEAD
git remote -v
git worktree list --porcelain
git branch -vv
git log --graph --decorate --oneline --all -n 180
```

保存原始输出。

## 2.2 找出真实运行进程

执行：

```bash
ps -ef | grep -E "uvicorn|gunicorn|vite|npm|pnpm|node|fluid" | grep -v grep
```

对每个候选 PID：

```bash
readlink -f /proc/<PID>/cwd
tr '\0' ' ' < /proc/<PID>/cmdline
readlink -f /proc/<PID>/exe
```

如果在 Windows/WSL，则同时检查：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "uvicorn|vite|node|fluid" } |
  Select-Object ProcessId, ExecutablePath, CommandLine
```

## 2.3 找出页面和 API 来源

必须记录：

- 浏览器当前页面 URL；
- 前端启动命令；
- 前端 cwd；
- 前端构建 commit；
- 后端启动命令；
- 后端 cwd；
- 后端 commit；
- API base URL；
- 当前数据库；
- 当前工作站配置来源；
- 当前环境变量文件名称，但不得记录密钥值。

## 2.4 建立基线文档

创建：

```text
docs/audits/TRAE_RUNNING_BASELINE.md
```

必须写明：

```yaml
repository_root:
frontend:
  cwd:
  branch:
  commit:
  start_command:
  url:
backend:
  cwd:
  branch:
  commit:
  start_command:
  api_base:
database:
worker:
  version:
  target:
canonical_running_commit:
reason:
```

## 2.5 创建保护点

在确认的真实运行 commit 上：

```bash
git tag backup/pre-model-driven-spec-editing-$(date +%Y%m%d-%H%M%S)
git push origin --tags
```

如果当前目录有未提交修改：

1. 不得丢弃；
2. 先列出 `git diff --stat`；
3. 识别是否属于正在运行版本；
4. 保存 patch：

```bash
git diff > docs/audits/pre_refactor_worktree.patch
git diff --cached > docs/audits/pre_refactor_index.patch
```

5. 再决定最小化提交或在同一 worktree 保留。

## 2.6 创建开发分支

从真实运行 commit 创建：

```bash
git switch -c trae/v5-model-driven-spec-editing
```

后续不得从其他分支整体 merge。只允许对经过逐文件审查的独立提交使用 cherry-pick。

---

# 3. 第二阶段：先建立失败基线，不要马上修

在改代码之前，必须让当前问题可重复、可观察。

创建：

```text
tests/e2e/model_editing/
├── test_set_end_time_15s.py
├── test_two_consecutive_edits.py
├── test_triangle_not_cosine_bell.py
├── test_material_change_recomputes_dependencies.py
├── test_add_probe_reaches_measurement_plan.py
├── test_unknown_geometry_does_not_use_template.py
└── test_no_silent_fallback.py
```

## 3.1 失败案例 A：仿真时间

已有方案：

```json
{
  "numerics": {
    "time": {
      "start_time": {"value": 0, "unit": "s"},
      "end_time": {"value": 10, "unit": "s"},
      "delta_t": {"value": 0.01, "unit": "s"}
    }
  }
}
```

用户输入：

```text
仿真时间设为15秒
```

当前失败必须记录：

- 原始请求；
-发送给模型的 prompt；
-模型原始输出；
-parser 输出；
-最终方案；
-右侧 UI；
-数据库记录。

## 3.2 失败案例 B：连续修改

连续消息：

1. `仿真时间设为15秒`
2. `时间步改为0.005秒`
3. `再增加一个出口前1米的速度探针`

检查是否：

- 每次新建了 session；
-前一轮修改丢失；
-只更新聊天文本；
-右侧方案没更新；
-探针没进入编译计划。

## 3.3 失败案例 C：几何误识别

用户描述三角形，检查所有阶段：

```text
model raw output
→ normalized intent
→ geometry model
→ CaseIR
→ compiler input
→ generated mesh files
```

找到是哪一层变成 `cosine_bell`，不能只修最终结果。

## 3.4 失败案例 D：模型失败伪装成功

人为使模型返回无效 JSON，断言系统不得生成方案。

## 3.5 失败基线报告

创建：

```text
docs/audits/MODEL_EDITING_FAILURE_BASELINE.md
```

每个问题写：

```text
复现步骤
预期
实际
错误发生层
原始证据路径
当前错误代码
```

---

# 4. 第三阶段：诊断“模型弱”还是“链路让模型变弱”

不能只观察 UI 回答。需要把整个模型调用管线拆开。

## 4.1 新增诊断脚本

创建：

```text
scripts/diagnose_model_pipeline.py
```

参数：

```bash
python scripts/diagnose_model_pipeline.py \
  --provider current \
  --model current \
  --output artifacts/model_diagnostics/<timestamp>
```

## 4.2 必须记录的信息

脱敏后保存：

```json
{
  "provider": "...",
  "configured_model": "...",
  "actual_model_from_response": "...",
  "endpoint_type": "responses|chat_completions|compatible",
  "reasoning_effort": "...",
  "temperature": "...",
  "max_output_tokens": 0,
  "structured_output_enabled": true,
  "tool_calling_enabled": true,
  "system_prompt_sha256": "...",
  "conversation_turn_count": 0,
  "current_spec_included": true,
  "skill_ids": [],
  "request_id": "...",
  "latency_ms": 0,
  "input_tokens": 0,
  "output_tokens": 0,
  "retry_count": 0,
  "fallback_used": false
}
```

不得保存 API key。

## 4.3 模型诊断测试集

至少执行 20 项，不是 10 项。

### 创建类

1. 创建圆柱绕流；
2. 创建圆柱 + 三角障碍；
3. 创建长方形通道 + 正弦凸起；
4. 创建 3D 倾斜圆柱；
5. 创建未知 polygon。

### 修改类

6. `仿真时间设为15秒`
7. `从5秒开始，再计算15秒`
8. `时间步减半`
9. `每0.1秒写出一次`
10. `只分析最后5秒`
11. `三角形改为矩形`
12. `圆柱上移0.2米`
13. `空气改成水`
14. `上边界改为slip`
15. `入口速度增加20%`
16. `添加出口前1米的速度探针`
17. `删除压力云图`
18. `增加截面平均速度`
19. `取消LES，使用层流`
20. `把刚才的修改撤销`

### 冲突和歧义类

21. 当前 `start_time=5s` 时说“仿真时间15秒”；
22. 同时给出 `Re=200`、固定 U、固定 D 和不一致 ν；
23. “圆柱位于正中央”与“距下壁2m”在高5m域中的关系；
24. 周期边界但几何两侧不一致；
25. 未知边界术语。

## 4.4 逐层评分

每个案例必须分别评分：

```text
A. 模型原始语义是否正确
B. 模型是否得到完整上下文
C. Structured Output 是否完整
D. parser 是否保留所有字段
E. schema 是否能表达
F. patch 是否能应用
G. 依赖是否正确更新
H. UI 是否显示新版本
I. compiler 是否采用新值
```

## 4.5 诊断结论必须明确

`summary.md` 不能只写“模型能力一般”。

必须写成：

```text
模型本身失败：x/25
上下文缺失导致失败：x/25
Schema 不可表达：x/25
Parser 丢失：x/25
Patch/写回失败：x/25
旧模板覆盖：x/25
UI 状态不同步：x/25
```

只有原始模型在获得完整上下文和正确 schema 后仍大面积失败，才能判定模型不适合作为主模型。

---

# 5. 第四阶段：建立模型准入和显式失败

## 5.1 定义模型角色

创建：

```text
src/fluid_scientist/model_runtime/
├── models.py
├── registry.py
├── client.py
├── tracing.py
├── capability_eval.py
├── structured_output.py
└── errors.py
```

角色：

```python
PRIMARY_REASONER = "primary_reasoner"
CRITIC = "critic"
FAST_ASSISTANT = "fast_assistant"
CODE_EXTENSION = "code_extension"
```

## 5.2 主模型必须负责

- 初次需求理解；
-当前方案修改；
-歧义判断；
-开放 CaseIR；
-能力缺口分析；
-物理审查；
-结果机理解释。

快速模型不得承担这些任务。

## 5.3 模型配置必须可见

管理/调试页面显示：

```text
role
provider
configured model
actual returned model
structured output support
reasoning mode
last health check
capability eval version
pass/fail
```

## 5.4 主模型准入阈值

在固定 eval 中至少满足：

```text
结构化输出可解析率 ≥ 98%
单字段最小修改正确率 ≥ 95%
连续 8 轮状态保持率 ≥ 90%
几何类型和关系正确率 ≥ 95%
单位正确率 ≥ 98%
冲突/歧义召回率 ≥ 90%
未知能力召回率 ≥ 95%
模板误用率 ≤ 2%
虚构执行成功率 = 0
```

未达到阈值不得注册为 `primary_reasoner`。

## 5.5 显式错误模型

定义：

```python
class ModelInvocationError:
    code: Literal[
        "MODEL_UNAVAILABLE",
        "MODEL_TIMEOUT",
        "MODEL_OUTPUT_INVALID",
        "MODEL_SCHEMA_MISMATCH",
        "MODEL_CAPABILITY_INSUFFICIENT",
        "SKILL_LOAD_FAILED"
    ]
    provider: str
    configured_model: str
    actual_model: str | None
    request_id: str | None
    retryable: bool
    fallback_used: bool = False
```

真实模式必须保证：

```python
assert fallback_used is False
```

---

# 6. 第五阶段：建立唯一 Canonical SimulationStudySpec

当前系统如果同时存在旧 plan、新 spec、UI draft、compiler plan，必须停止双向来回转换。

## 6.1 唯一事实源

新增或统一为：

```python
SimulationStudySpec
```

建议路径：

```text
src/fluid_scientist/study_spec/
├── models.py
├── quantities.py
├── geometry.py
├── boundaries.py
├── numerics.py
├── observations.py
├── provenance.py
├── versioning.py
├── schema_export.py
└── migration.py
```

## 6.2 顶层结构

```python
class SimulationStudySpec(BaseModel):
    schema_version: str
    spec_id: str
    session_id: str
    version: int
    parent_version: int | None

    study: StudyDefinition
    physics: PhysicsDefinition
    geometry: GeometryDefinition
    boundaries: BoundaryDefinition
    initial_conditions: list[InitialCondition]
    numerics: NumericsDefinition
    mesh: MeshDefinition
    observations: ObservationDefinition
    execution: ExecutionDefinition
    validation: ValidationDefinition
    extensions: dict[str, Any]
    provenance: SpecProvenance
```

## 6.3 时间字段必须明确

```python
class TimeControl(BaseModel):
    mode: Literal["steady", "transient"]
    start_time: Quantity | None
    end_time: Quantity | None
    duration: Quantity | None
    delta_t: Quantity | None
    adaptive: bool
    max_courant: float | None
    max_delta_t: Quantity | None
    write_control: Literal[
        "timeStep",
        "runTime",
        "adjustableRunTime",
        "clockTime",
        "cpuTime"
    ] | None
    write_interval: Quantity | int | None
    purge_write: int | None
    statistics_windows: list[TimeWindow]
```

规则：

- `end_time` 和 `duration` 可以同时存在，但必须满足一致性；
- `duration = end_time - start_time`；
- 用户只说“仿真时间”且存在歧义时必须澄清；
- 统计窗口必须落在模拟时间范围内；
- 修改 `end_time` 时不能偷偷改变 `delta_t`；
- 修改 `delta_t` 后必须重新检查 Courant 数和采样频率。

## 6.4 每个重要值保存来源

```python
class SourcedValue(BaseModel):
    value: Any
    unit: str | None
    status: Literal[
        "user_explicit",
        "user_confirmed",
        "model_recommended",
        "derived",
        "default_pending",
        "unknown"
    ]
    source_turn_ids: list[str]
    confidence: float | None
    derivation_id: str | None
    last_modified_by_patch: str | None
```

## 6.5 不允许高风险默认值冒充已确认值

例如：

```text
end_time
material
geometry kind
main boundary role
solver physics
```

缺失时状态必须是 `unknown` 或 `default_pending`，不能直接用默认值并显示为已确认。

## 6.6 旧模型迁移

可以实现只读迁移：

```text
legacy plan → SimulationStudySpec
```

禁止主链继续：

```text
SimulationStudySpec → legacy plan → compiler
```

编译器必须直接消费新 spec 或由新 spec 生成 CaseIR。

---

# 7. 第六阶段：实现通用 SimulationSpecPatch

这是本次最关键的代码。

## 7.1 模块路径

创建：

```text
src/fluid_scientist/spec_editing/
├── __init__.py
├── models.py
├── path_registry.py
├── patch_validator.py
├── patch_executor.py
├── quantity_resolver.py
├── relation_resolver.py
├── dependency_engine.py
├── impact_analyzer.py
├── diff_builder.py
├── undo.py
├── provenance.py
└── errors.py
```

## 7.2 Patch 数据结构

```python
class PatchOperation(BaseModel):
    op: Literal[
        "add",
        "replace",
        "remove",
        "merge",
        "append_unique",
        "move",
        "copy",
        "test",
        "set_relation",
        "unset_relation",
        "declare_unknown_capability"
    ]
    path: str
    value: Any | None = None
    from_path: str | None = None
    entity_id: str | None = None
    relation: dict[str, Any] | None = None
    source_quote: str
    confidence: float
    rationale: str | None = None

class ClarificationRequest(BaseModel):
    clarification_id: str
    question: str
    alternatives: list[ClarificationAlternative]
    affected_paths: list[str]
    blocking: bool

class SimulationSpecPatch(BaseModel):
    patch_id: str
    session_id: str
    base_spec_id: str
    base_version: int
    intent: Literal[
        "create_spec",
        "modify_existing_spec",
        "confirm_pending_patch",
        "reject_pending_patch",
        "undo_last_patch",
        "request_explanation"
    ]
    operations: list[PatchOperation]
    clarifications: list[ClarificationRequest]
    impact_requests: list[str]
    untouched_guarantee: bool
    assistant_message: str
```

## 7.3 Path Registry 必须由 Schema 自动生成

禁止手写每一个字段映射。

`PathRegistry` 从 Pydantic model / JSON Schema 自动生成：

```text
/numerics/time/end_time
/numerics/time/delta_t
/geometry/entities/{entity_id}/primitive/type
/observations/probes/-
```

同时保存：

```python
class PathMetadata:
    json_pointer: str
    value_schema: dict
    required: bool
    mutable: bool
    risk_level: Literal["low", "medium", "high", "critical"]
    unit_dimension: str | None
    dependency_tags: set[str]
```

## 7.4 Patch 执行步骤

严格按顺序：

```text
1. 校验 base_version
2. 校验 JSON Pointer
3. 校验字段是否允许修改
4. 校验操作类型
5. 解析单位
6. 类型校验
7. dry-run 到副本
8. 结构约束校验
9. 物理约束校验
10. 运行 Dependency Engine
11. 生成 Impact Report
12. 生成 SpecDiff
13. 判断是否需要用户确认
14. 原子保存新版本
15. 保存 PatchRecord 和 provenance
```

任一步失败不得部分写入。

## 7.5 Patch 示例：结束时间

用户：

```text
仿真时间设为15秒
```

当前：

```text
start_time=0s
end_time=10s
```

模型应输出：

```json
{
  "intent": "modify_existing_spec",
  "operations": [
    {
      "op": "replace",
      "path": "/numerics/time/end_time",
      "value": {
        "value": 15,
        "unit": "s",
        "status": "user_explicit"
      },
      "source_quote": "仿真时间设为15秒",
      "confidence": 0.99
    }
  ],
  "clarifications": [],
  "impact_requests": [
    "recheck_statistics_windows",
    "recompute_expected_output_times"
  ],
  "untouched_guarantee": true,
  "assistant_message": "已准备将仿真结束时间由10秒改为15秒。"
}
```

## 7.6 Patch 示例：语义歧义

当前：

```text
start_time=5s
end_time=10s
```

用户：

```text
仿真时间设为15秒
```

不得猜。输出：

```json
{
  "operations": [],
  "clarifications": [
    {
      "clarification_id": "time_semantics_001",
      "question": "你希望结束时间改为15秒，还是从5秒开始继续计算15秒并在20秒结束？",
      "alternatives": [
        {
          "label": "结束时间为15秒",
          "operations": [
            {
              "op": "replace",
              "path": "/numerics/time/end_time",
              "value": {"value": 15, "unit": "s"}
            }
          ]
        },
        {
          "label": "持续计算15秒",
          "operations": [
            {
              "op": "replace",
              "path": "/numerics/time/duration",
              "value": {"value": 15, "unit": "s"}
            },
            {
              "op": "replace",
              "path": "/numerics/time/end_time",
              "value": {"value": 20, "unit": "s"}
            }
          ]
        }
      ],
      "affected_paths": [
        "/numerics/time/end_time",
        "/numerics/time/duration"
      ],
      "blocking": true
    }
  ]
}
```

## 7.7 Patch 示例：相对修改

用户：

```text
时间步改成原来的一半
```

模型可以输出表达式：

```json
{
  "op": "replace",
  "path": "/numerics/time/delta_t",
  "value": {
    "expression": {
      "operator": "multiply",
      "path": "/numerics/time/delta_t",
      "factor": 0.5
    },
    "unit": "s"
  }
}
```

表达式由 `quantity_resolver.py` 计算，不能让模型自己心算后丢失来源。

## 7.8 Patch 示例：三角形改矩形

只改目标实体：

```json
{
  "operations": [
    {
      "op": "replace",
      "path": "/geometry/entities/wall_obstacle/primitive/type",
      "value": "rectangle",
      "source_quote": "三角形改成矩形",
      "confidence": 0.99
    },
    {
      "op": "merge",
      "path": "/geometry/entities/wall_obstacle/primitive/parameters",
      "value": {
        "width": {"value": 0.1, "unit": "m"},
        "height": {"value": 0.05, "unit": "m"}
      },
      "source_quote": "三角形改成矩形",
      "confidence": 0.95
    }
  ],
  "untouched_guarantee": true
}
```

材料、圆柱、入口、Re、观测指标不得改变。

## 7.9 未知能力

```json
{
  "op": "declare_unknown_capability",
  "path": "/geometry/entities/-",
  "value": {
    "capability_key": "geometry.superellipse",
    "original_semantics": "...",
    "requested_parameters": {...}
  },
  "source_quote": "...",
  "confidence": 0.98
}
```

---

# 8. 第七阶段：Dependency Engine

用户修改一个参数后，系统必须知道哪些内容需要重算、失效或重新确认。

## 8.1 模块

```text
src/fluid_scientist/dependencies/
├── graph.py
├── rules.py
├── derived_values.py
├── invalidation.py
└── report.py
```

## 8.2 依赖关系示例

```text
U, D, nu → Re
rho, nu → mu
start_time, end_time → duration
end_time, write_interval → expected output count
delta_t, U, mesh size → Courant estimate
material → rho/nu/mu → Re → force normalization
geometry → mesh → patches → boundary field mapping
objective Cd/Cl → forceCoeffs function object
objective point velocity → probes function object
objective section mean → surfaceFieldValue
last 5 s statistics → fieldAverage start time
```

## 8.3 修改材料

空气改成水时不得只改字符串。

必须生成影响：

```text
材料名称改变
→ 需要确认温度或物性来源
→ rho/nu/mu 变更
→ 如果 U 和 D 固定，Re 改变
→ 如果 Re 必须保持200，需要重算 U 或 nu 并澄清
→ 力系数参考值检查
→ 之前结果不可直接比较
→ case 需要重新编译和运行
```

## 8.4 Invalidation 状态

每个下游产物：

```python
VALID
NEEDS_RECOMPUTE
NEEDS_RECOMPILE
NEEDS_RERUN
NEEDS_REVIEW
BLOCKED
```

例如只修改报告标题，不需要 rerun；修改 end_time 需要 recompile/rerun；添加新后处理图在字段已保存充分时可能只需 postprocess。

---

# 9. 第八阶段：多轮 Session State

## 9.1 数据结构

```python
class ResearchSessionState(BaseModel):
    session_id: str
    project_id: str
    active_spec_id: str
    active_spec_version: int
    turns: list[ConversationTurn]
    compact_summary: str
    confirmed_facts: list[FactRecord]
    unresolved_conflicts: list[ConflictRecord]
    pending_patch: SimulationSpecPatch | None
    patch_history: list[PatchRecord]
    model_trace_ids: list[str]
    current_phase: Literal[
        "UNDERSTANDING",
        "CLARIFYING",
        "DRAFT_READY",
        "PLAN_CONFIRMED",
        "COMPILED",
        "RUN_CONFIRMED",
        "RUNNING",
        "RESULTS_READY",
        "REVIEWED"
    ]
```

## 9.2 默认意图规则

除非用户明确说：

- “新建另一个实验”
- “另外创建一个方案”
- “复制为新方案”

否则短消息默认是：

```text
modify_existing_spec
```

## 9.3 每轮必须给模型的上下文

按以下固定顺序构建：

```text
1. 系统角色和禁止事项
2. 当前工作流阶段
3. 当前 OpenFOAM 环境和能力
4. 当前启用的专业 Skills
5. SimulationSpecPatch JSON Schema
6. 当前完整 SimulationStudySpec
7. confirmed facts
8. unresolved conflicts
9. 更早会话摘要
10. 最近原始对话
11. 用户本轮原文
```

不允许只传本轮消息。

## 9.4 上下文压缩

压缩不能丢：

- 数值；
-单位；
-几何关系；
-用户确认状态；
-冲突；
-最近 Patch；
-研究目标。

摘要生成后必须与 canonical spec 对照，canonical spec 才是事实源。

---

# 10. 第九阶段：重新设计模型 Prompt

不要再使用“从以下模板选择一个”的主提示词。

## 10.1 Spec Editor System Prompt

运行时系统提示必须至少包含：

```text
你是 CFD 仿真方案的结构化编辑器，不是模板分类器。

你会收到：
- 当前完整 SimulationStudySpec；
- 当前方案版本；
- 当前会话事实和冲突；
- 本轮用户消息；
- 可用能力；
- SimulationSpecPatch schema；
- CFD/OpenFOAM 专业 Skill。

你的任务：
1. 判断用户是在创建、修改、删除、确认、拒绝、撤销还是询问。
2. 对修改只输出最小必要 Patch。
3. 保留用户没有修改的所有字段。
4. 精确引用用户原文作为 source_quote。
5. 处理单位、相对量和几何关系。
6. 有歧义时输出 clarification，不得猜测。
7. 当前能力无法表达时 declare_unknown_capability。
8. 不把未知形状映射为已有形状。
9. 不输出 Shell、OpenFOAM 文件或执行成功声明。
10. 输出必须符合给定 JSON Schema。
```

## 10.2 Critic Prompt

Critic 检查：

- 是否遗漏修改；
-是否改变了无关字段；
-是否错误猜测；
-是否用模板替代未知语义；
-单位；
-物理依赖；
-风险等级；
-是否需要澄清。

Critic 输出：

```json
{
  "accepted": true,
  "violations": [],
  "required_corrections": []
}
```

## 10.3 两次调用策略

核心修改：

```text
primary_reasoner → candidate patch
critic → review
```

如果 critic 拒绝，只允许有限重试，不得无限循环。

---

# 11. 第十阶段：开放 CaseIR，不再以场景模板作为知识本体

已有模板可以保留为 compiler adapter，但不能决定系统能理解什么。

## 11.1 CaseIR 模块

```text
src/fluid_scientist/case_ir/
├── models.py
├── geometry_ast.py
├── mesh_ir.py
├── boundary_ir.py
├── physics_ir.py
├── numerics_ir.py
├── measurement_ir.py
├── capability_requirements.py
└── validators.py
```

## 11.2 几何 AST

```python
class GeometryEntity(BaseModel):
    entity_id: str
    representation: Literal[
        "primitive",
        "csg",
        "parametric",
        "polygon",
        "imported",
        "unknown"
    ]
    primitive: PrimitiveGeometry | None
    csg: CSGGeometry | None
    parametric: ParametricGeometry | None
    polygon: PolygonGeometry | None
    imported: ImportedGeometry | None
    original_user_semantics: str
```

Primitive 可以扩展，但未知值不得强制枚举成已知。

## 11.3 几何关系

```python
class GeometryRelation(BaseModel):
    relation_id: str
    type: Literal[
        "attached_to",
        "aligned_below",
        "aligned_above",
        "centered_in",
        "distance_to",
        "tangent_to",
        "inside",
        "outside",
        "intersects",
        "custom"
    ]
    subject_id: str
    object_id: str
    parameters: dict
```

“位于圆柱正下方”应保存为关系，再由坐标求解器计算。

## 11.4 能力需求图

```python
class CapabilityRequirement(BaseModel):
    key: str
    status: Literal[
        "AVAILABLE_VERIFIED",
        "AVAILABLE_UNVERIFIED",
        "CONFIG_EXTENSION_REQUIRED",
        "CODE_EXTENSION_REQUIRED",
        "ENVIRONMENT_BLOCKED",
        "UNSUPPORTED"
    ]
    reason: str
    evidence_ids: list[str]
```

---

# 12. 第十一阶段：模型编码能力不足时的正确边界

不能靠让弱模型直接写更多 OpenFOAM 代码解决。

## 12.1 正常场景

模型输出：

- `SimulationSpecPatch`
- `CaseIR`
- `GeometryRecipe`
- `MeshRecipe`
- `BoundaryPlan`
- `NumericsPlan`
- `MeasurementPlan`
- `PostProcessPlan`

确定性程序生成：

- `controlDict`
- `fvSchemes`
- `fvSolution`
- field files；
- geometry/mesh files；
- function objects；
- worker manifest。

## 12.2 编译器必须 schema-driven

建议：

```text
src/fluid_scientist/openfoam_compiler/
├── compiler.py
├── foundation13/
│   ├── control_dict.py
│   ├── fields.py
│   ├── boundary_conditions.py
│   ├── numerics.py
│   ├── turbulence.py
│   ├── function_objects.py
│   └── mesh.py
├── adapters/
└── validators/
```

## 12.3 未知能力扩展

模型只输出：

```python
CapabilityExtensionProposal
```

由开发流程实现和验证。运行时模型不得直接修改 `src/`。

---

# 13. 第十二阶段：把时间修改真正编译进 OpenFOAM

“方案里显示15秒”不代表完成。

## 13.1 映射

对 Foundation 13：

```text
SimulationStudySpec.numerics.time.start_time
→ controlDict startFrom/startTime

end_time
→ controlDict endTime

delta_t
→ controlDict deltaT

adaptive
→ adjustTimeStep

max_courant
→ maxCo

max_delta_t
→ maxDeltaT

write_control
→ writeControl

write_interval
→ writeInterval
```

具体字段必须按工作站 OpenFOAM 13 验证，不得照搬其他发行版。

## 13.2 编译验收

修改 10 s → 15 s 后断言：

- canonical spec version 增加；
-compiled manifest 引用新 version；
-`controlDict` `endTime` 为 15；
-archive SHA 改变；
-用户确认 digest 改变；
-旧 archive 不得提交；
-run record 指向新 artifact。

---

# 14. 第十三阶段：MeasurementPlan 真正进入 case

## 14.1 目标到功能对象

至少支持：

| 用户目标 | 编译结果 |
|---|---|
| Cd、Cl | forces/forceCoeffs |
| 涡脱落频率、St | forceCoeffs 时间序列 + FFT 计划 |
| 点速度 | probes |
| 截面平均速度 | surfaceFieldValue |
| 时间平均场 | fieldAverage |
| 涡量 | vorticity function object 或后处理 |
| 壁面剪切 | wallShearStress |
| y+ | yPlus |
| 指定线/面采样 | sampledSets/sampledSurfaces |
| 动画 | PostProcessPlan + ParaView recipe |

## 14.2 添加探针的真实链路

```text
用户消息
→ Patch add /observations/probes/-
→ dependency: MeasurementPlan invalid
→ MeasurementCompiler
→ controlDict/functions 或 include
→ compile preview
→ run
→ postProcessing/probes
→ ResultIngestor
→ UI time series
```

任何一段缺失都不能宣称支持探针。

---

# 15. 第十四阶段：前端交互必须明确

不重做当前聊天式 UI，只在当前版本增量修改。

## 15.1 每轮模型处理后显示

聊天区域：

```text
我理解你的修改为：
- 仿真结束时间：10 s → 15 s

影响：
- 需要重新编译和运行
- 输出时刻数量会增加
- 当前统计窗口仍为8–10 s，建议确认是否调整

未修改：
- 时间步0.01 s
- 写出间隔0.1 s
```

右侧方案立即显示 pending diff，但在高风险修改确认前标记：

```text
待确认
```

## 15.2 右侧必须显示值来源

例如：

```text
结束时间 15 s
来源：用户第7轮明确指定
状态：待确认/已确认
```

## 15.3 澄清交互

当 `start_time=5s`：

```text
你希望：
[结束时间为15秒]
[再运行15秒，结束于20秒]
```

选择后应用对应 Patch，不需要用户重新输入整句话。

## 15.4 同一方案

用户连续修改不得跳回创建页，不得生成新的无关实验。

## 15.5 结果持久化

固定：

```text
方案 | 编译 | 运行 | 场图 | 时间序列 | 频谱 | 验证 | 分析 | 审计
```

“查看方案”和“返回结果”双向可用。

---

# 16. 第十五阶段：API 行为要写清

以下是建议契约。若当前路由名称不同，可保持兼容 adapter，但语义必须一致。

## 16.1 修改当前方案

```http
POST /api/research-sessions/{session_id}/turns
```

请求：

```json
{
  "message": "仿真时间设为15秒",
  "client_active_spec_version": 4
}
```

响应一：可直接生成 diff：

```json
{
  "status": "PATCH_PENDING_CONFIRMATION",
  "turn_id": "...",
  "base_spec_version": 4,
  "candidate_spec_version": 5,
  "patch": {...},
  "diff": {...},
  "impact": {...},
  "model_trace_id": "...",
  "skill_trace_ids": [...]
}
```

响应二：需要澄清：

```json
{
  "status": "CLARIFICATION_REQUIRED",
  "clarifications": [...]
}
```

响应三：模型失败：

```json
{
  "status": "MODEL_FAILED",
  "fallback_used": false,
  "error": {...}
}
```

## 16.2 确认 Patch

```http
POST /api/research-sessions/{session_id}/patches/{patch_id}/confirm
```

必须校验 base version，防止并发覆盖。

## 16.3 拒绝 Patch

```http
POST /api/research-sessions/{session_id}/patches/{patch_id}/reject
```

## 16.4 撤销

```http
POST /api/research-sessions/{session_id}/undo
```

生成逆 Patch 和新版本，不直接删除历史。

---

# 17. 第十六阶段：运行和证据模型

## 17.1 RunRecord

```python
class RunRecord(BaseModel):
    run_id: str
    session_id: str
    spec_id: str
    spec_version: int
    case_ir_id: str
    compiled_artifact_id: str
    archive_sha256: str
    target_id: str
    external_job_id: str | None
    status: Literal[
        "CREATED",
        "AWAITING_RUN_CONFIRMATION",
        "SUBMITTED",
        "RUNNING",
        "COLLECTING",
        "COMPLETED_PROCESS",
        "FAILED",
        "CANCELLED"
    ]
```

没有 `external_job_id` 时不得显示 `SUBMITTED`。

## 17.2 结果可信性状态

独立保存：

```text
PROCESS_COMPLETED
NUMERICALLY_ACCEPTABLE
PHYSICALLY_CREDIBLE
RESEARCH_READY
```

不能只用 `SUCCESS`。

---

# 18. 第十七阶段：测试矩阵

至少 60 个端到端语义修改案例，且核心代码不得为这些案例逐项加 if/else。

## 18.1 时间

1. 仿真时间设为15秒；
2. 结束时间为20秒；
3. 从5秒开始再运行15秒；
4. 时间步0.005秒；
5. 时间步减半；
6. 自适应时间步；
7. maxCo=0.5；
8. 每0.1秒写出；
9. 每20步写出；
10. 只统计最后5秒；
11. 删除旧统计窗口；
12. 恢复上一次时间设置。

## 18.2 几何

13. 三角障碍；
14. 三角改矩形；
15. 矩形改正弦凸起；
16. 圆柱上移；
17. 圆柱横向移动；
18. 增加第二个圆柱；
19. 删除障碍；
20. 改障碍宽度；
21. 改障碍高度；
22. 新 polygon；
23. superellipse；
24. 导入 STL；
25. 相对关系“正下方”；
26. “流场中央”与明确坐标冲突。

## 18.3 材料和物理

27. 空气改水；
28. 修改密度；
29. 修改运动黏度；
30. 固定Re并修改U；
31. 固定U/D/Re推导ν；
32. 温度相关物性；
33. 2D改3D；
34. 层流改RANS；
35. RANS改LES；
36. 稳态改瞬态。

## 18.4 边界

37. 入口速度；
38. 压力出口；
39. slip；
40. no-slip；
41. cyclic；
42. symmetry；
43. convective outlet；
44. moving wall；
45. 压力梯度；
46. 改 patch 名但保持角色。

## 18.5 观测

47. 添加Cd；
48. 添加Cl；
49. 添加St；
50. 添加FFT；
51. 添加点探针；
52. 移动点探针；
53. 删除点探针；
54. 添加截面平均；
55. 添加壁面剪切；
56. 添加y+；
57. 添加涡量动画；
58. 删除压力图；
59. 时间平均场；
60. 只输出特定时间段。

## 18.6 多轮累积测试

同一 session 顺序执行：

1. 创建圆柱+三角障碍；
2. 仿真时间15秒；
3. 时间步0.005秒；
4. 三角改矩形；
5. 空气改水；
6. 增加出口前探针；
7. 删除压力云图；
8. 只分析最后5秒；
9. 确认方案；
10. 编译。

最终 spec 必须同时保留全部有效修改。

---

# 19. 第十八阶段：防模板通吃自动检查

创建：

```text
src/fluid_scientist/validation/artifact_diversity.py
```

比较三角、矩形、正弦凸起：

```text
CaseIR fingerprint
geometry fingerprint
mesh recipe
patch list
compiled file hash
archive hash
```

要求：

- 三种几何 fingerprint 不同；
-对应几何文件或 mesh 参数不同；
-材料和入口在未修改时一致；
-未知 polygon 不得与任一模板 hash 高度相同；
-用户目标改变时 function objects 必须变化。

---

# 20. 第十九阶段：真实 OpenFOAM 13 验收

至少真实运行：

## 20.1 案例 A：圆柱 + 三角障碍

- 2D；
-域长10m、宽5m；
-圆柱半径0.1m；
-圆心距下壁2m；
-来流1m/s；
-Re=200；
-三角障碍高0.05m、宽0.1m；
-左速度入口；
-右压力出口；
-上自由出流；
-下无滑移；
-观测涡街、Cd、Cl、频率、St。

随后同 session：

```text
仿真时间设为15秒
```

确认 `controlDict endTime=15`。

## 20.2 案例 B：改成矩形

只修改几何，重新编译和运行，证明不是同一模板。

## 20.3 最终 artifact

```text
artifacts/e2e/<run_id>/
├── conversation.json
├── model_traces/
├── skill_traces.json
├── spec_versions/
├── patches/
├── case_ir.json
├── compiled_manifest.json
├── archive.sha256
├── worker_doctor.json
├── checkMesh.log
├── solver.log
├── postProcessing/
├── figures/
├── animations/
├── metrics.json
├── validation.json
├── analysis.json
└── report.md
```

---

# 21. 提交顺序

不要一次提交全部。

建议：

```text
1. chore(audit): capture running baseline and failure evidence
2. feat(model-runtime): add model tracing and capability diagnostics
3. feat(study-spec): establish canonical versioned simulation spec
4. feat(spec-editing): add schema-driven patch engine
5. feat(dependencies): add derived-value and invalidation graph
6. feat(orchestration): make model edit current session spec
7. feat(case-ir): add open geometry and capability requirements
8. feat(openfoam): compile time and measurement plans for foundation13
9. feat(ui): show patch diff clarification and model trace
10. test(e2e): cover multi-turn edits and geometry diversity
11. docs(audit): publish real workstation evidence
```

每个提交必须：

- 范围单一；
-有测试；
-不删除无关文件；
-不覆盖当前 UI；
-`git diff --stat` 可理解。

---

# 22. 每阶段完成后必须汇报的格式

Trae 每个阶段报告：

```text
阶段：
基线 commit：
当前分支：
修改文件：
新增测试：
测试命令：
测试结果：
真实证据路径：
未解决问题：
下一步：
```

不能只说“已完成优化”。

---

# 23. 最终完成清单

只有全部满足才能结束：

- [ ] 已锁定真实运行版本；
- [ ] 已创建保护 tag；
- [ ] 在正确新分支开发；
- [ ] 未随意删除文件；
- [ ] 已保存当前失败基线；
- [ ] 已证明模型问题与链路问题各占多少；
- [ ] 主模型通过准入；
- [ ] 无静默 fallback；
- [ ] 唯一 canonical spec；
- [ ] 同一 session 版本链；
- [ ] 通用 Patch Engine；
- [ ] Schema 自动路径；
- [ ] 单位和相对表达；
- [ ] Dependency Engine；
- [ ] Clarification；
- [ ] Undo；
- [ ] 三角不再变 cosine_bell；
- [ ] 未知场景进入能力缺口；
- [ ] 60 个修改测试；
- [ ] 连续8轮修改不丢状态；
- [ ] 时间真实进入 controlDict；
- [ ] MeasurementPlan 真实进入 function objects；
- [ ] 不同几何 archive 不同；
- [ ] 真实 OpenFOAM 13 run；
- [ ] 图片、动画、指标、验证和报告；
- [ ] 页面刷新后结果仍在；
- [ ] 最终审计列出 commit、run ID 和限制。

---

# 24. 可直接执行的总指令

```text
你负责对 bamboo09/fluid_scientist 做根因级重构。

当前最重要的问题不是缺少“仿真时间”或“某种形状”的专用识别，而是模型没有成为
当前仿真方案的通用语义编辑器。禁止继续为单个参数添加关键词、正则、枚举和
if/else，禁止把未知语义映射为最近模板。

第一步必须锁定用户当前实际运行版本。检查 Git、worktree、运行进程、前后端 cwd、
启动命令、URL 和 commit，写入 docs/audits/TRAE_RUNNING_BASELINE.md。在实际
运行 commit 创建保护 tag，再创建 trae/v5-model-driven-spec-editing。禁止
reset --hard、git clean、强推、批量删除目录和历史分支整体覆盖。

第二步先建立失败基线，真实复现：
- “仿真时间设为15秒”无法修改；
- 连续修改丢状态；
- 三角形变成 cosine_bell；
- 材料修改后依赖不更新；
- 探针不进入 MeasurementPlan；
- 模型失败后仍伪装成功。

第三步实现模型调用诊断。保存脱敏 request、response、actual model、context、
structured output、parser、schema、patch 和 UI 写回证据。明确区分模型本身、
上下文、schema、parser、旧模板、fallback 和 UI 各自造成的失败。

第四步建立：
1. 主模型准入和显式失败；
2. 唯一、版本化的 SimulationStudySpec；
3. 同一 ResearchSessionState；
4. 通用 SimulationSpecPatch；
5. 由 Schema 自动生成的 JSON Pointer PathRegistry；
6. 类型、单位、相对表达、dry-run、diff、impact、undo 和 provenance；
7. Dependency Engine；
8. primary reasoner + critic；
9. 开放 CaseIR、几何 AST 和 CapabilityRequirementGraph；
10. 确定性 OpenFOAM Foundation 13 compiler。

模型每轮必须收到当前完整 spec、最近对话、历史摘要、confirmed facts、conflicts、
能力清单、OpenFOAM 版本、专业 Skills 和 Patch schema。模型只输出最小 Patch，
不得重建完整方案、改变无关字段、输出 Shell 或声明执行成功。

“仿真时间设为15秒”必须走通用 Patch：
/numerics/time/end_time = 15 s。
如果 start_time=5s，则必须澄清“结束于15s”还是“继续15s结束于20s”。

随后证明同一个 Patch Engine 可以处理至少60种时间、几何、材料、边界、数值和
观测修改，不新增字段专用 if/else。三角、矩形、正弦凸起必须生成不同 CaseIR、
mesh recipe 和 archive；未知 polygon 必须进入 Unknown Capability。

最后在 OpenFOAM Foundation 13 工作站真实跑通圆柱+三角障碍，在同一 session
把时间改为15秒，确认 controlDict、archive、run record、场图、动画、Cd、Cl、
St、探针、验证和报告全部对应新 spec version。没有全量测试、真实 run 和完整
artifact 审计，不得结束。
```
