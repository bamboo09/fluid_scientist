# Trae 下一步施工指令：仅引入 Skill 文件，自主实现全部运行时能力

> **执行对象：Trae**  
> **目标仓库：** `bamboo09/fluid_scientist`  
> **唯一允许引入的外部成果：** 提交 `df93802` 中的 Skill 相关文件  
> **核心限制：** 不使用、不合并、不 cherry-pick 任何其他分支中的业务实现。所有后端、前端、模型运行时、SpecPatch、CaseIR、编译器、执行器和测试功能，都必须在当前实际运行版本上自行实现。

---

# 0. 本次任务的唯一正确理解

你不能从其他开发分支获取现成业务代码。

你只能使用 `df93802` 中的以下知识资产：

```text
skills/**
docs/skills/**
tests/skill_evals/**
third_party/skill_sources/**
```

这些内容包括：

- CFD/OpenFOAM Skill 文档；
- references；
- examples；
- 200 条单轮 Eval；
- 20 条多轮 Eval；
- 来源和许可证审计；
- OpenFOAM Foundation 13 兼容性说明；
- Runtime Integration Contract。

除上述四类目录外，其他分支、其他 worktree、其他提交中的业务代码一律禁止使用。

你必须在**用户当前正在运行的版本**上，自主实现：

```text
Runtime Skill Loader
Skill Registry
Skill Router
Reference Loader
Prompt Builder
模型调用和追踪
Canonical SimulationStudySpec
SimulationSpecPatch
Dependency Engine
CaseIR
Capability Requirement Graph
OpenFOAM Foundation 13 编译
MeasurementPlan
工作站执行接入
结果后处理
可信性验证
前端交互
行为 Eval Runner
E2E 测试
```

不能说“其他分支已经实现过，所以直接合并”。

不能说“历史分支里有类似代码，所以复制过来”。

即使其他分支有实现，也必须忽略，按照本文件和 Skill 契约在当前运行版本重新实现。

---

# 1. 绝对禁止事项

## 1.1 禁止使用其他分支的业务代码

禁止：

```bash
git merge <任何其他功能分支>
git cherry-pick <任何业务提交>
git checkout <其他分支> -- src
git restore --source=<其他业务提交> -- src
git restore --source=<其他业务提交> -- apps
```

禁止复制其他 worktree 中的：

```text
src/**
apps/**
tests/业务测试
数据库迁移
API
compiler
worker
UI
```

## 1.2 唯一允许的外部复制

只允许从 `df93802` 复制：

```text
skills/**
docs/skills/**
tests/skill_evals/**
third_party/skill_sources/**
```

不得复制该提交以外的任何文件。

## 1.3 禁止整提交 cherry-pick 后不审查

即使 `df93802` 被描述为纯文档提交，也不要直接依赖“它应该是干净的”。

必须先检查文件列表。

推荐使用路径级导入，而不是整提交 cherry-pick。

## 1.4 禁止继续按参数打补丁

不得新增：

```python
if "仿真时间" in user_text:
if "三角形" in user_text:
if "改成水" in user_text:
```

Skill 负责指导模型理解自然语言。

业务代码只处理结构化 `SimulationSpecPatch`。

## 1.5 禁止模板通吃

不得：

```text
unknown shape → cosine_bell
unknown scenario → cylinder_flow
unknown boundary → zeroGradient
```

必须进入 Unknown Capability。

## 1.6 禁止静默 fallback

核心模型或 Skill 失败时，不得切换：

- Fake；
-Regex；
-固定模板；
-旧计划；
-缓存结果；
-默认圆柱算例。

## 1.7 禁止随意删除当前文件

禁止：

```bash
git reset --hard
git clean -fd
git clean -fdx
git restore .
git checkout -- .
rm -rf src
rm -rf apps
rm -rf tests
git push --force
```

确需删除单个文件时，必须先证明无引用、有测试、有迁移说明，并单独提交。

---

# 2. 第一步：确认当前实际运行版本

不能根据分支名猜测。

## 2.1 检查 Git

在用户当前项目目录执行：

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

## 2.2 检查运行进程

Linux/WSL：

```bash
ps -ef | grep -E "uvicorn|gunicorn|vite|npm|pnpm|node|fluid" | grep -v grep
```

对 PID：

```bash
readlink -f /proc/<PID>/cwd
tr '\0' ' ' < /proc/<PID>/cmdline
```

Windows：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "uvicorn|vite|node|fluid" } |
  Select-Object ProcessId, ExecutablePath, CommandLine
```

## 2.3 输出基线文档

更新：

```text
docs/audits/TRAE_RUNNING_BASELINE.md
```

必须包含：

```yaml
repository_root:
frontend:
  cwd:
  branch:
  commit:
  command:
  url:
backend:
  cwd:
  branch:
  commit:
  command:
  api_base:
worker:
  target:
  version:
canonical_running_commit:
```

## 2.4 创建保护标签

```bash
git tag backup/pre-skill-runtime-self-implementation-$(date +%Y%m%d-%H%M%S)
git push origin --tags
```

## 2.5 创建新分支

只从当前实际运行 commit 创建：

```bash
git switch -c trae/v5-skill-runtime-self-implementation
```

---

# 3. 第二步：只导入 `df93802` 的 Skill 文件

## 3.1 检查提交

```bash
git show --stat --summary df93802
git diff-tree --no-commit-id --name-only -r df93802
```

断言所有路径都属于：

```text
skills/**
docs/skills/**
tests/skill_evals/**
third_party/skill_sources/**
```

发现任何其他路径，立即停止。

## 3.2 使用路径级导入

推荐：

```bash
git restore --source=df93802 -- \
  skills \
  docs/skills \
  tests/skill_evals \
  third_party/skill_sources
```

然后：

```bash
git status --short
git diff --stat
git diff --name-only
```

只允许出现上述四个目录。

提交：

```bash
git add skills docs/skills tests/skill_evals third_party/skill_sources
git commit -m "feat(skills): import audited CFD and OpenFOAM skill assets"
```

## 3.3 禁止执行来源目录中的代码

`third_party/skill_sources` 只用于：

- 来源；
-commit；
-license；
-审计；
-兼容性信息。

不得运行其中任何第三方脚本。

## 3.4 导入审计

创建：

```text
docs/audits/SKILL_ASSET_IMPORT_AUDIT.md
```

记录：

```text
source commit: df93802
imported directories
file count
content hashes
validation commands
no business code imported: true
```

---

# 4. 第三步：阅读 Skill，再制定代码映射

必须阅读：

```text
docs/skills/RUNTIME_INTEGRATION_CONTRACT.md
docs/skills/SKILL_CATALOG.md
docs/skills/SOURCE_AUDIT.md
docs/skills/OPENFOAM_FOUNDATION_13_COMPATIBILITY.md
docs/skills/EVAL_REPORT.md
skills/*/SKILL.md
skills/*/references/index.md
```

然后创建：

```text
docs/audits/SKILL_RUNTIME_IMPLEMENTATION_MAP.md
```

格式：

| Skill | 运行阶段 | 当前代码入口 | 需要自主实现的模块 | 输入 | 输出 | 阻断条件 | 验收 |
|---|---|---|---|---|---|---|---|

不允许写“参考其他分支实现”。

每一项都必须明确为：

```text
在当前运行版本中新增/修改哪些文件
```

---

# 5. 第四步：自主实现 Runtime Skill 系统

如果当前版本没有相关代码，就从零实现。

如果当前版本已有部分代码，只能在当前分支审查和改造，不能从其他分支补齐。

建议目录：

```text
src/fluid_scientist/skill_runtime/
├── __init__.py
├── models.py
├── frontmatter.py
├── loader.py
├── registry.py
├── router.py
├── reference_loader.py
├── prompt_builder.py
├── provenance.py
├── integrity.py
├── policy.py
├── service.py
└── errors.py
```

---

# 6. Runtime Skill 数据结构

## 6.1 SkillManifest

```python
class SkillManifest(BaseModel):
    skill_id: str
    name: str
    description: str
    version: str
    root_path: str
    content_sha256: str

    phases: set[str]
    mandatory_phases: set[str]
    supported_intents: set[str]

    input_contract: dict[str, Any] | None
    output_contract: dict[str, Any] | None

    reference_index: list["SkillReference"]

    enabled: bool = True
    scripts_enabled: bool = False
```

## 6.2 SkillReference

```python
class SkillReference(BaseModel):
    reference_id: str
    relative_path: str
    title: str
    topics: set[str]
    phases: set[str]
    content_sha256: str
    foundation13_status: Literal[
        "VERIFIED",
        "PARTIALLY_VERIFIED",
        "UNVERIFIED",
        "NOT_VERSION_SPECIFIC"
    ]
```

## 6.3 SkillSelection

```python
class SkillSelection(BaseModel):
    selection_id: str
    phase: str
    intent: str
    skill_id: str
    reason: str
    mandatory: bool
    selected_reference_ids: list[str]
    router_version: str
```

## 6.4 SkillInvocationRecord

```python
class SkillInvocationRecord(BaseModel):
    invocation_id: str
    session_id: str
    turn_id: str | None
    spec_id: str | None
    spec_version: int | None
    run_id: str | None

    phase: str
    intent: str

    skill_id: str
    skill_content_sha256: str
    reference_ids: list[str]
    reference_hashes: dict[str, str]

    model_role: str
    provider: str
    configured_model: str
    actual_model: str | None
    model_request_id: str | None

    outcome: Literal[
        "USED",
        "BLOCKED",
        "FAILED",
        "REJECTED_BY_CRITIC"
    ]

    failure_code: str | None
    failure_message: str | None
```

---

# 7. Loader 必须做什么

启动时：

```text
1. 扫描仓库根目录 skills/
2. 读取一级目录中的 SKILL.md
3. 解析 YAML front matter
4. 校验 name 和 description
5. 读取 references/index.md
6. 建立 Reference 元数据
7. 计算 Skill 目录 hash
8. 检查 sources.lock.yaml
9. 拒绝路径逃逸和危险 symlink
10. 默认禁用 scripts
11. 注册到 Registry
12. 输出结构化日志
```

核心 Skill 缺失时返回：

```text
SKILL_REGISTRY_INVALID
```

不得忽略。

---

# 8. Registry 与 Router 必须自行实现

## 8.1 核心 Skill

```text
fluid-research-workflow
cfd-spec-understanding-and-editing
cfd-physics-review
openfoam-case-planning
openfoam-geometry-meshing
openfoam-boundaries-numerics
openfoam-diagnostics
openfoam-postprocessing
cfd-validation-reporting
```

## 8.2 阶段路由

| 阶段 | 强制 Skill |
|---|---|
| 创建、修改、澄清、撤销 | cfd-spec-understanding-and-editing |
| Patch 后物理审查 | cfd-physics-review |
| 方案确认与 Case Blueprint | openfoam-case-planning |
| 几何和网格 | openfoam-geometry-meshing |
| 边界和数值 | openfoam-boundaries-numerics |
| 编译、网格、求解失败 | openfoam-diagnostics |
| 指标、图片、动画 | openfoam-postprocessing |
| 可信性和报告 | cfd-validation-reporting |

`fluid-research-workflow` 作为所有核心阶段的全局规则。

## 8.3 不能每轮加载全部 Skill

流程：

```text
phase
→ mandatory skill
→ 根据当前 spec path 选择辅助 Skill
→ 生成 reference 候选
→ 模型只能从候选 reference 中选择
```

模型不得提供任意文件路径。

---

# 9. Prompt Builder 必须自主实现

固定顺序：

```text
1. 产品安全规则
2. 当前工作流阶段
3. fluid-research-workflow
4. 主专业 Skill 完整 SKILL.md
5. 选中的 references
6. 当前模型角色
7. OpenFOAM Foundation 13 环境
8. 当前完整 SimulationStudySpec
9. confirmed facts
10. unresolved conflicts
11. 会话摘要
12. 最近原始对话
13. 输出 JSON Schema
14. 用户本轮消息
```

不得只给模型本轮短消息。

保存：

```text
skill IDs
skill hashes
reference IDs
reference hashes
spec ID/version
conversation turn IDs
output schema version
model request ID
```

---

# 10. 必须自主实现 Canonical SimulationStudySpec

不能从其他分支复制。

建议目录：

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

唯一事实源：

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

不能让 Skill 继续读取旧固定模板 plan 作为主事实源。

---

# 11. 必须自主实现 SimulationSpecPatch

建议目录：

```text
src/fluid_scientist/spec_editing/
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

## 11.1 Patch 操作

```text
add
replace
remove
merge
append_unique
move
copy
test
set_relation
unset_relation
declare_unknown_capability
```

## 11.2 Patch 路径必须由 Schema 自动生成

禁止为每个字段写映射。

`PathRegistry` 从 Pydantic/JSON Schema 自动产生：

```text
/numerics/time/end_time
/numerics/time/delta_t
/geometry/entities/{entity_id}/primitive/type
/observations/probes/-
```

## 11.3 原子执行

```text
校验 base version
→ 路径
→ 类型
→ 单位
→ dry-run
→ 结构约束
→ 物理约束
→ 依赖分析
→ diff
→ 确认
→ 保存新版本
```

失败时不得部分写入。

---

# 12. “仿真时间15秒”必须这样走

当前：

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

用户：

```text
仿真时间设为15秒
```

Router：

```text
cfd-spec-understanding-and-editing
```

References：

```text
patch-operations
units-and-relative-expressions
dependency-impact
```

模型输出：

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
  "untouched_guarantee": true
}
```

Critic 必须检查没有改：

- deltaT；
-geometry；
-material；
-boundary；
-observations。

Patch 应用：

```text
version N → N+1
end_time 10s → 15s
```

随后 `openfoam-case-planning` 指导 Case Blueprint。

确定性 compiler 必须写：

```text
endTime 15;
```

真实 run 必须指向新 spec version 和新 archive SHA。

---

# 13. start_time=5s 时必须澄清

当前：

```text
start_time=5s
end_time=10s
```

用户：

```text
仿真时间设为15秒
```

必须输出澄清：

```text
A. 结束时间改为15秒
B. 从5秒开始继续计算15秒，结束于20秒
```

不得使用关键词直接猜。

---

# 14. 必须自主实现 Dependency Engine

不能复制其他分支。

依赖示例：

```text
U, D, nu → Re
rho, nu → mu
start_time, end_time → duration
end_time, write_interval → output count
delta_t, U, mesh size → Courant
material → rho/nu/mu → Re → force normalization
geometry → mesh → patch mapping
Cd/Cl → forceCoeffs
probe → probes
section mean → surfaceFieldValue
last N seconds → statistics window
```

修改后产物状态：

```text
VALID
NEEDS_RECOMPUTE
NEEDS_RECOMPILE
NEEDS_RERUN
NEEDS_REVIEW
BLOCKED
```

---

# 15. 必须自主实现 CaseIR

建议目录：

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

不得使用固定 scenario enum 作为系统本体。

几何支持：

```text
primitive
csg
parametric
polygon
imported
unknown
```

未知几何必须保留原始语义，进入：

```text
CapabilityRequirementGraph
```

---

# 16. OpenFOAM 编译能力必须在当前分支自行实现

不得从其他分支复制 compiler。

建议：

```text
src/fluid_scientist/openfoam_compiler/
├── compiler.py
├── foundation13/
│   ├── control_dict.py
│   ├── fields.py
│   ├── boundaries.py
│   ├── numerics.py
│   ├── turbulence.py
│   ├── function_objects.py
│   └── mesh.py
├── adapters/
└── validators/
```

模型和 Skill 只生成结构化：

```text
CaseBlueprint
GeometryRecipe
MeshRecipe
BoundaryPlan
NumericsPlan
MeasurementPlan
PostProcessPlan
```

确定性代码生成 OpenFOAM 文件。

---

# 17. MeasurementPlan 必须真实编译

| 用户目标 | OpenFOAM/后处理 |
|---|---|
| Cd/Cl | forces/forceCoeffs |
| 频率/St | forceCoeffs + FFT |
| 点速度 | probes |
| 截面平均 | surfaceFieldValue |
| 时间平均 | fieldAverage |
| 涡量 | vorticity |
| 壁面剪切 | wallShearStress |
| y+ | yPlus |
| 动画 | PostProcessPlan + ParaView recipe |

不能只在报告文字里说“将观测”。

---

# 18. 模型调用服务必须统一

自主实现：

```python
class SkilledModelService:
    async def invoke(
        self,
        *,
        role: ModelRole,
        phase: WorkflowPhase,
        intent: str,
        session_state: ResearchSessionState,
        current_spec: SimulationStudySpec | None,
        payload: dict[str, Any],
        output_model: type[BaseModel],
    ) -> SkilledModelResult:
        ...
```

内部：

```text
Registry
→ Router
→ Reference Loader
→ Prompt Builder
→ Model Client
→ Structured Output
→ Critic
→ Provenance
```

禁止各业务模块自行拼 Skill 文本。

---

# 19. 行为 Eval Runner 必须自主实现

已有 Eval 是数据，不是运行器。

新增：

```text
scripts/run_skill_behavior_evals.py
src/fluid_scientist/skill_evals/
├── loader.py
├── runner.py
├── graders.py
├── reports.py
└── models.py
```

## 19.1 Smoke Eval

先执行至少 20 条：

- 时间15秒；
-start=5歧义；
-时间步减半；
-三角改矩形；
-三角不等于 cosine_bell；
-空气改水；
-探针；
-删除图；
-最后5秒；
-unknown polygon；
-多轮累积。

## 19.2 Full Eval

执行全部：

```text
200 条单轮
20 条多轮
```

## 19.3 评分

必须检查：

```text
Schema
required operations
forbidden changes
minimal patch
preserve untouched
clarification
unknown capability
unit
path
source quote
no template fallback
```

不能只做字符串包含判断。

---

# 20. 前端也必须在当前版本自主修改

不要从其他分支复制 UI。

在当前聊天式 UI 增量增加：

- 当前方案版本；
-pending diff；
-修改影响；
-澄清选项；
-字段来源；
-专业 Skill；
-调用状态；
-模型错误；
-结果和方案双向切换；
-持久化图片和动画。

前端 Skill 标签必须来自后端 `SkillInvocationRecord`，不能写死。

---

# 21. 工作站和执行能力也不得从其他分支复制

只使用当前运行版本已有的工作站能力作为基线。

缺少的功能在当前分支自行实现：

- doctor；
-submit；
-status；
-collect；
-postprocess；
-artifact；
-run record。

不得从其他分支复制 worker、SSH 或提交器。

继续遵守：

- 不在 UI 收集私钥；
-不开放任意 Shell；
-只执行受控类型化命令；
-没有 external job ID 不显示 submitted。

---

# 22. 必须完成的测试

```text
tests/skill_runtime/
tests/model_runtime/
tests/study_spec/
tests/spec_editing/
tests/dependencies/
tests/case_ir/
tests/openfoam_compiler/
tests/skill_evals/
tests/e2e/skill_integration/
```

关键 E2E：

```text
test_end_time_edit_uses_skill
test_multiturn_edit_uses_skill
test_triangle_not_cosine_bell
test_unknown_geometry_does_not_use_template
test_material_change_recomputes
test_probe_reaches_function_objects
test_skill_failure_does_not_fallback
test_foundation13_control_dict
test_ui_trace_is_backend_grounded
```

---

# 23. 真实 E2E

案例：

- 2D；
-域长10m、宽5m；
-圆柱半径0.1m；
-圆心距下壁2m；
-来流1m/s；
-Re=200；
-三角障碍；
-左速度入口；
-右压力出口；
-上自由出流；
-下无滑移；
-Cd、Cl、频率、St。

同一 session 修改：

```text
仿真时间设为15秒
```

必须保存：

```text
conversation
skill selection
references
prompt trace
model response
critic response
Patch
spec before/after
CaseIR
controlDict
archive SHA
run record
worker logs
figures
animations
metrics
validation
report
```

---

# 24. 多轮 E2E

同一 session：

1. 创建三角障碍；
2. 时间15秒；
3. deltaT=0.005秒；
4. 三角改矩形；
5. 空气改水；
6. 添加探针；
7. 删除压力图；
8. 只分析最后5秒；
9. 撤销；
10. 设置最后3秒。

要求：

- 不丢状态；
-不新建 session；
-无关字段不变；
-每轮有 Skill 证据；
-最终 compiled artifact 与 spec 一致；
-没有单参数 if/else；
-没有使用其他分支代码。

---

# 25. Git 审计必须证明“未使用其他分支业务代码”

最终创建：

```text
docs/audits/NO_EXTERNAL_BRANCH_BUSINESS_CODE_AUDIT.md
```

必须包含：

```text
基线 commit
开发分支
导入的唯一来源 commit df93802
导入的唯一目录
所有新增业务文件均在当前分支创建
未 merge 的分支列表
未 cherry-pick 的业务提交
git log
git diff
```

可以使用：

```bash
git log --first-parent
git diff <baseline>...HEAD --name-status
git show --stat <each-commit>
```

审计必须明确写：

```text
No business implementation was imported from any other branch.
```

---

# 26. 提交顺序

```text
1. chore(audit): capture running baseline and protect current version
2. feat(skills): import audited skill assets from df93802
3. feat(skill-runtime): implement loader registry router and integrity
4. feat(model-runtime): implement skilled structured model invocation
5. feat(study-spec): implement canonical simulation study spec
6. feat(spec-editing): implement schema-driven patch engine
7. feat(dependencies): implement dependency and invalidation graph
8. feat(case-ir): implement open case representation
9. feat(openfoam): implement foundation13 deterministic compiler
10. feat(measurement): compile observations into function objects
11. feat(ui): expose diff clarification and grounded skill trace
12. test(skills): implement behavior eval runner
13. test(e2e): verify real model and OpenFOAM workflow
14. docs(audit): prove no external branch business code was used
```

---

# 27. 每阶段报告

```text
阶段：
当前分支：
基线 commit：
本阶段 commit：
本阶段自行实现的模块：
从 df93802 使用的 Skill 文件：
是否使用其他分支业务代码：否
测试命令：
测试结果：
行为 Eval：
真实证据：
未解决：
下一步：
```

---

# 28. 最终完成清单

- [ ] 从当前实际运行版本创建分支；
- [ ] 创建保护标签；
- [ ] 只导入 `df93802` 的四类 Skill 目录；
- [ ] 没有 merge 其他功能分支；
- [ ] 没有 cherry-pick 业务提交；
- [ ] Runtime Skill 系统自行实现；
- [ ] Canonical Spec 自行实现；
- [ ] SpecPatch 自行实现；
- [ ] Dependency Engine 自行实现；
- [ ] CaseIR 自行实现；
- [ ] Foundation 13 compiler 自行实现；
- [ ] MeasurementPlan 自行实现；
- [ ] 前端交互自行实现；
- [ ] Eval Runner 自行实现；
- [ ] Skill 真正注入模型；
- [ ] 每次调用有证据；
- [ ] 时间15秒进入 controlDict；
- [ ] 三角不变 cosine_bell；
- [ ] unknown 不走模板；
- [ ] 200+20 行为 Eval；
- [ ] 真实 OpenFOAM 13 E2E；
- [ ] 最终无其他分支业务代码审计。

---

# 29. 直接执行总指令

```text
你负责在 bamboo09/fluid_scientist 当前实际运行版本上，自主实现完整的
Runtime Skill、模型语义编辑、SimulationStudySpec、SimulationSpecPatch、
Dependency Engine、CaseIR、OpenFOAM Foundation 13 编译、MeasurementPlan、
工作站闭环、前端交互、行为 Eval 和 E2E。

禁止使用任何其他分支的业务实现。禁止 merge 功能分支，禁止 cherry-pick 业务提交，
禁止从其他 worktree 复制 src、apps、API、compiler、worker、UI 和测试代码。

唯一允许引入的外部成果是提交 df93802 中的：
skills/**
docs/skills/**
tests/skill_evals/**
third_party/skill_sources/**

先确认当前实际运行的前后端 commit 和 worktree，创建保护 tag，再从该 commit 创建
trae/v5-skill-runtime-self-implementation。

使用路径级 git restore 只导入 df93802 的四个 Skill 目录，并审计文件范围。
这些 Skill 是知识、契约和 Eval 数据，不是运行时代码。所有运行时功能必须在当前
分支自行编写。

实现 SkillLoader、Registry、Router、ReferenceLoader、PromptBuilder、
SkillInvocationRecord 和 SkilledModelService。采用阶段路由和渐进式加载。

实现唯一版本化 SimulationStudySpec 和通用 SimulationSpecPatch。Patch 路径必须
从 Schema 自动生成，不能为“仿真时间、三角形、材料”等增加专用 if/else。

用户说“仿真时间设为15秒”时，必须使用 cfd-spec-understanding-and-editing，
读取当前完整 spec 和会话，输出最小 Patch：
replace /numerics/time/end_time = 15 s。
Critic 检查无关字段不变，Patch Engine 创建新版本，openfoam-case-planning 指导
Case Blueprint，确定性 compiler 写出 controlDict endTime 15，真实工作站运行新
archive。

如果 start_time=5s，必须澄清“结束于15s”还是“继续15s结束于20s”。

自主实现已有200条单轮和20条多轮 Eval 的 Runner 和 grader，执行真实模型行为评测。
quick_validate 不能代替行为 Eval。

最终完成真实圆柱+三角障碍 OpenFOAM Foundation 13 E2E 和十轮多轮修改，保存完整
Skill、模型、Patch、Spec、CaseIR、编译、运行、场图、动画、指标、验证和报告证据。

最终必须提交 NO_EXTERNAL_BRANCH_BUSINESS_CODE_AUDIT.md，证明除 df93802 的 Skill
文件外，没有从任何其他分支、提交或 worktree 引入业务代码。

没有完整行为 Eval、真实 E2E 和无外部分支业务代码审计，不得结束。
```
