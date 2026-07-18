# Codex 任务：从 Trae 合并后的唯一主分支开始，完成模型原生 Fluid Scientist 重构

> **启动前提**
>
> Trae 已将当前全部成果完整合并到 `main`，并生成：
>
> ```text
> CODEX_HANDOFF_FROM_TRAE.md
> ```
>
> Codex 必须从其中记录的：
>
> ```text
> MAIN_AFTER_TRAE_MERGE_SHA
> ```
>
> 创建新分支。
>
> **禁止使用任何旧 Codex 分支、旧 Trae worktree、历史 UI、历史 Compiler 或旧业务提交作为代码来源。**
>
> 当前 main 上的 Trae 成果必须完整保留，Codex 只能在其上增量修复和重构。

---

# 1. 分支和版本纪律

验证：

```bash
git switch main
git pull --ff-only origin main
git rev-parse HEAD
```

必须等于：

```text
MAIN_AFTER_TRAE_MERGE_SHA
```

不一致立即停止。

创建唯一开发分支：

```bash
git switch -c codex/v6-model-native-fluid-scientist
```

创建保护标签：

```bash
git tag backup/codex-start-from-trae-main-<timestamp>
git push origin --tags
```

禁止：

```text
从旧Codex分支恢复业务文件
从旧Trae worktree复制src/apps
恢复旧聊天UI
回到旧API
用旧Compiler覆盖当前Compiler
删除当前成果后重新做
```

---

# 2. 建立 Codex 起始基线

读取：

```text
CODEX_HANDOFF_FROM_TRAE.md
TRAE_CURRENT_IMPLEMENTATION_MANIFEST.md
TRAE_FINAL_RUNNING_BASELINE.md
TRAE_TO_MAIN_MERGE_DECISIONS.md
```

运行当前全部测试并创建：

```text
docs/audits/CODEX_STARTING_BASELINE.md
```

包含：

```text
main SHA
Codex分支SHA
当前前后端命令
当前通过和失败测试
当前浏览器截图
当前API响应
当前Workstation Doctor
当前已知P0
```

当前实现即使有缺陷，也必须成为迁移基线，不能无意消失。

---

# 3. 总体重构目标

将当前系统从：

```text
regex / keywords
→ cylinder-specific pipeline
→ LLM补充
→ template compiler
```

升级为：

```text
用户消息
+ 完整会话
+ 当前Canonical Spec
+ Skills / References
→ LLM Structured Understanding
→ Evidence / Unit / Physics Validators
→ Intent / Facts / Ambiguities / Conflicts / Capability
→ 通用SimulationSpecPatch
→ 用户确认
→ Open CaseIR
→ Model-assisted Case Planning
→ Structured CompilePlan
→ Deterministic Compiler
→ Static Validation
→ Workstation E2E
→ Model-assisted Diagnostics / Postprocess / Validation / Report
```

---

# 4. 模型需要参与的阶段

## 4.1 意图和需求理解

模型负责：

```text
新建/修改/删除/撤销/询问/确认/创建Variant
显式事实
实体和关系
研究目标
歧义
冲突
未知能力
```

输出：

```python
class SemanticUnderstandingResult(BaseModel):
    intent: IntentType
    facts: list[StructuredFact]
    entities: list[EntityCandidate]
    relations: list[RelationCandidate]
    ambiguities: list[ClarificationRequest]
    conflicts: list[SemanticConflict]
    proposed_patch: SimulationSpecPatch | None
    capability_requirements: list[CapabilityRequirement]
    evidence_quotes: list[str]
```

## 4.2 研究方案设计

模型结合 CFD Skills 生成：

```text
物理假设
模型类型
边界语义
初始条件
数值策略
网格意图
观测指标
验证方案
资源和风险
```

## 4.3 多轮修改

模型输出最小 `SimulationSpecPatch`，不能重新生成整份模板。

## 4.4 CaseIR

模型把自然语言语义映射到开放结构：

```text
domain
geometry entities
relations
physics
boundaries
numerics
measurements
capability requirements
```

## 4.5 编译规划

模型必须参与编译，但不能直接输出未经验证的生产文件。

模型输出：

```python
class CompilePlan(BaseModel):
    target_distribution: str
    target_version: str
    solver_family: str
    geometry_adapter: str
    mesh_strategy: str
    boundary_plan: list
    field_plan: list
    numerics_plan: dict
    function_object_plan: list
    validation_checks: list
    unresolved_capabilities: list
```

## 4.6 编译诊断

编译或静态校验失败后，模型结合 Skill 和错误证据生成：

```text
DiagnosticProposal
RepairPatch
Risk
RequiredConfirmation
```

## 4.7 未知能力代码扩展

对于新的几何、边界或测量能力：

```text
模型分析缺口
→ 生成结构化扩展设计
→ 在隔离分支/沙箱生成受限代码补丁
→ 自动生成测试
→ 类型检查
→ 静态Case编译
→ 代码审查
→ 注册Capability
```

不得让模型直接在生产目录写任意代码或执行任意 shell。

---

# 5. 模型不得直接控制的内容

禁止模型直接：

```text
执行任意shell
读取密钥
修改数据库状态
生成run ID
跳过用户确认
绕过静态验证
把推荐值记成用户确认
输出自由文本字典后直接运行
```

正确边界：

```text
模型负责理解、规划、候选、代码扩展建议和诊断
确定性代码负责Schema、单位、状态、AST渲染、安全和执行
```

---

# 6. P0：先修复当前真实页面问题

真实工作站运行前必须完成。

## 6.1 材料污染

用户未指定材料时：

```text
material.name = null
不得出现water
不得出现998 kg/m³
不得出现water数据库黏度
```

如果只提供 `U、D、Re`，只能推导等效运动黏度，不能推导材料身份。

## 6.2 域和障碍角色

```text
长方形计算域
≠
矩形障碍物
```

实体必须带：

```text
DOMAIN
SOLID_OBSTACLE
BOUNDARY_FEATURE
```

## 6.3 Regex/LLM候选互斥

当：

```text
regex=rectangle
llm=trapezoid
```

不能同时写入两个实体。

一个语义槽只能选中一个候选。

## 6.4 修改必须真正持久化

成功条件：

```text
Patch生成
→ 校验
→ 原子应用
→ 持久化
→ read-back
→ version+1
→ UI刷新
```

任何一步失败都不能显示“方案已修改”。

## 6.5 单一状态源

`READY_TO_CONFIRM` 不能与 blockers 同时存在。

## 6.6 可解释阻塞

`SEMANTIC_COVERAGE_GAP` 必须转换成：

```text
问题是什么
涉及哪些字段
为什么阻塞
用户如何解决
可点击选项
```

## 6.7 Provenance

区分：

```text
USER_EXPLICIT
USER_CONFIRMED
FORMULA_DERIVED
MODEL_RECOMMENDED
DEFAULT_PENDING
```

## 6.8 澄清必须由用户确认

模型推荐不能自动记成用户答案。

---

# 7. 移除正则的语义决策权

## 7.1 正则保留范围

只保留：

```text
数字
单位
坐标
科学计数法
格式
安全
OpenFOAM词法
```

## 7.2 正则改为影子校验

以下不得再直接写 Spec：

```text
_extract_reynolds
_extract_end_time
_extract_inlet_velocity
geometry keyword detector
scenario keyword router
CYLINDER_KEYWORDS
```

输出类型只能是：

```text
EvidenceToken
ValidationWarning
```

不能输出：

```text
GeometryEntity
SimulationSpecPatch
SimulationStudySpec
```

## 7.3 反事实验证

关闭 LLM 且禁止 fallback。

以下请求必须失败或提示模型不可用：

```text
算久一点，跑到15秒
别动步长，把终止时刻往后延5秒
把贴壁的小尖块换成上窄下宽的小台
刚才那个点向出口方向移动两个直径
```

如果仍成功，说明规则仍是主引擎。

---

# 8. Skills 必须真实进入模型运行时

对 Codex 创建的 Skill 做完整审计。

每次调用保存：

```text
skill IDs
skill hashes
reference IDs
reference hashes
prompt snapshot
actual model
request ID
spec ID/version
```

Prompt Trace 必须能证明：

```text
workflow Skill
主专业 Skill
选中 references
当前完整 Spec
最近历史
confirmed facts
conflicts
output schema
```

运行同一批至少30条：

```text
Skills ON
Skills OFF
Wrong Skill
Skill missing
```

比较：

```text
意图正确率
事实准确率
最小Patch
歧义召回
冲突召回
未知能力召回
无关字段变更率
物理审查
CompilePlan完整性
```

没有量化增益，不能宣称 Skill 已用上。

---

# 9. 候选层和 Canonical Spec 分离

必须有三个独立层：

```text
Interpretation Candidates
Pending Clarifications
Canonical Confirmed Spec
```

未经选择的候选不能进入 Spec。

典型语义槽：

```text
bottom_obstacle_1.geometry_type
material.kind
top_boundary.semantic_role
```

同一槽只能有一个选中值。

---

# 10. 通用研究层级

实现：

```text
Project
→ Study
→ Variant
→ SpecVersion
→ CompileArtifact
→ Run
```

支持：

```text
CREATE_NEW_STUDY
CREATE_VARIANT
UNDO
BRANCH_FROM_VERSION
COMPARE_VARIANTS
```

短修改必须修改当前 active Spec，不能创建新 workflow。

---

# 11. Open CaseIR

替代固定 scenario 作为系统本体：

```python
class CaseIR(BaseModel):
    dimensionality: str
    domain: DomainIR
    entities: list[GeometryIR]
    physics: PhysicsIR
    boundaries: list[BoundaryIR]
    initial_conditions: list
    numerics: NumericsIR
    mesh: MeshIR
    measurements: MeasurementIR
    capabilities: list[CapabilityRequirement]
```

几何表达：

```text
primitive
polygon
parametric
csg
imported
unknown
```

---

# 12. 模型参与编译的实现方式

新增或整理：

```text
model_compile_planner.py
compile_plan_models.py
dictionary_ast.py
adapter_registry.py
compile_critic.py
repair_orchestrator.py
```

流程：

```text
CaseIR
→ Model Compile Planner + Skills
→ CompilePlan
→ CompilePlan Validator
→ Adapter Registry
→ Dictionary AST
→ Deterministic Renderer
→ Static Validator
→ Compile Critic
```

模型负责：

```text
solver family
adapter选择
边界语义映射
数值策略
field plan
function object plan
诊断与修复建议
```

确定性系统负责：

```text
Foundation 13 Schema
AST
文件渲染
路径
安全
跨文件一致性
digest
```

---

# 13. 模型角色分层

当前 Flash 模型不应承担所有核心任务。

实现角色：

```text
primary_reasoner
physics_critic
compile_planner
diagnostic_reasoner
fast_ui_model
```

每个角色记录真实 provider/model，并通过能力 Eval Gate。

弱模型可做摘要，核心推理不能静默降级到弱模型或 regex。

---

# 14. 语义和开放能力测试

## 14.1 用户当前梯形案例

真实浏览器逐值验证：

```text
材料未指定
没有water/998/1e-6命名材料值
domain=6×4
只有一个trapezoid
没有rectangle obstacle
deltaT修改后显示0.005s
endTime修改后显示20s
blocker可理解并可操作
状态一致
刷新持久化
```

## 14.2 100条未见表达

测试时不得把测试句加入关键词或正则。

## 14.3 开放几何

至少：

```text
ellipse
capsule
triangle
rectangle
trapezoid
wedge
polygon
annulus
sine bump
cosine bell
piecewise ramp
two unequal cylinders
NACA0012
STL
superellipse
CSG union
CSG subtraction
```

每类至少5种不同表达。

## 14.4 Context Removal

分别移除：

```text
current Spec
conversation history
Skills
confirmed facts
```

对应能力应下降，否则说明上下文没有真正使用。

---

# 15. 通用研究主链

不得继续只依赖：

```text
/api/v5/cylinder-flow
```

建立或使用：

```text
/api/research-sessions
```

同一主链至少处理：

```text
cylinder wake
backward-facing step
lid-driven cavity
periodic channel
natural convection
3D periodic cylinder
twin cylinder
moving wall
time-varying inlet
polygon obstacle
```

不得为每个案例建立整条独立 pipeline。

使用组合 adapter：

```text
geometry
physics
boundary
mesh
measurement
solver
postprocess
```

---

# 16. 真实工作站完整 E2E

每个 run 必须经过：

```text
Doctor
Upload
Extract
Mesh
checkMesh
Smoke
Full Solver
Postprocess
Collect
Numerical Validation
Physical Validation
UI Persistence
```

第一批：

```text
RUN-001 2D cylinder
RUN-002 cylinder + triangle
RUN-003 triangle → rectangle Variant
RUN-004 sine vs cosine
RUN-005 twin cylinder
RUN-006 time-varying inlet
RUN-007 polygon
RUN-008 probes + surface average + wall shear
```

第二批同一通用主链：

```text
RUN-009 backward step
RUN-010 periodic channel
RUN-011 lid-driven cavity
RUN-012 natural convection
RUN-013 3D periodic cylinder
```

---

# 17. 每个 Run 的强制证据

```text
conversation
prompt trace
skill invocations
model raw/structured output
spec versions
patches
dependency review
physics review
CaseIR
CompilePlan
compiled manifest
archive SHA
Workstation Doctor
upload evidence
blockMesh/snappy/checkMesh logs
smoke logs
solver log
postProcessing
figures
animations
metrics
numerical validation
physical validation
UI assertions
report
```

---

# 18. 工作站故障恢复测试

至少覆盖：

```text
SSH中断
上传中断
SHA不一致
磁盘不足
OpenFOAM环境未加载
checkMesh失败
smoke发散
full run中断
后处理失败
collect失败
后端重启
取消任务
```

不得重复提交或伪造完成状态。

---

# 19. 物理可信性

基准圆柱至少执行：

```text
粗/中/细网格
deltaT=0.01/0.005/0.0025
```

比较：

```text
Cd
Cl
St
质量守恒
CFL
网格质量
计算成本
```

分别判定：

```text
PROCESS_COMPLETED
NUMERICALLY_ACCEPTABLE
PHYSICALLY_CREDIBLE
RESEARCH_READY
```

不能用“有漂亮图片”代替可信性。

---

# 20. Git 提交策略

建议：

```text
1. fix(p0): restore canonical spec and state consistency
2. refactor(semantics): make LLM structured understanding primary
3. refactor(regex): restrict regex to evidence and validation
4. feat(skills): enforce runtime prompt integration and ablation
5. feat(study): add study variant and spec version hierarchy
6. feat(caseir): introduce open geometry and capability model
7. feat(compile): add model-assisted structured compile planning
8. feat(capability): add sandboxed model-generated extensions
9. feat(workstation): complete real execution evidence chain
10. test(e2e): add browser semantic and real workstation tests
11. docs(audit): publish model skill compiler and runtime reports
```

每个提交独立测试和可回滚，禁止一次性大爆改。

---

# 21. 防止再次回到旧版本

每次开始工作前验证：

```bash
git merge-base HEAD main
git log -1
```

CI 强制检查：

```text
当前分支必须包含 MAIN_AFTER_TRAE_MERGE_SHA
```

新增启动检查：

```text
/api/system/build-info
```

开发报告必须写当前运行 commit。

发现当前分支不包含 Trae 合并 commit，立即停止。

禁止：

```text
rebase到旧main
restore旧UI
checkout旧src
从旧worktree复制业务代码
```

---

# 22. 最终报告

分别生成：

```text
LLM_PARTICIPATION_AUDIT.md
SKILL_RUNTIME_EFFECTIVENESS_REPORT.md
OPEN_GEOMETRY_CAPABILITY_REPORT.md
GENERAL_RESEARCH_PIPELINE_REPORT.md
MODEL_ASSISTED_COMPILER_REPORT.md
REAL_WORKSTATION_E2E_REPORT.md
WORKSTATION_FAILURE_RECOVERY_REPORT.md
PHYSICAL_VALIDATION_REPORT.md
UI_PERSISTENCE_REPORT.md
```

必须分别统计：

```text
LLM_SEMANTIC_E2E
SKILL_ABLATION
API_CONTRACT
BROWSER_E2E
PERSISTENCE_E2E
REAL_WORKSTATION_E2E
MESH_VALIDATION
SOLVER_VALIDATION
POSTPROCESS_VALIDATION
PHYSICAL_VALIDATION
FAULT_INJECTION
MUTATION
```

不能再合并成一个“智能体100%”。

---

# 23. 完成标准

- [ ] 从精确 Trae 合并 main SHA 开始；
- [ ] 未使用旧业务分支；
- [ ] Trae 当前成果全部保留；
- [ ] 当前P0全部修复；
- [ ] LLM成为语义主引擎；
- [ ] Regex没有Spec写权限；
- [ ] Skills有Prompt和消融证据；
- [ ] Candidate与Canonical Spec分离；
- [ ] Study/Variant/Version层级；
- [ ] Open CaseIR；
- [ ] 模型参与结构化编译规划；
- [ ] 编译仍确定、安全、可验证；
- [ ] 未知能力可受控扩展；
- [ ] 100条未见表达；
- [ ] 开放几何；
- [ ] 非圆柱通用主链；
- [ ] 真实工作站完整E2E；
- [ ] 图、动画、指标和报告；
- [ ] 数值和物理验证；
- [ ] UI持久化；
- [ ] 无版本回退。

---

# 24. 直接执行指令

```text
你必须从CODEX_HANDOFF_FROM_TRAE.md记录的MAIN_AFTER_TRAE_MERGE_SHA开始。
先验证本地main等于该SHA，再创建codex/v6-model-native-fluid-scientist。
禁止使用任何旧Codex分支、旧Trae worktree或历史业务实现作为代码来源。

Trae当前所有成果必须保留。你的工作是在该版本上增量重构，不是回到旧版重做。

先修复当前P0：未指定材料不得出现water属性；长方形domain不得生成rectangle obstacle；
regex/LLM冲突候选不得同时进入Spec；Patch成功必须持久化、read-back并显示diff；
状态只能来自一个后端权威对象；blocker必须可解释；provenance必须正确；模型不能替用户确认。

随后把LLM变成语义主引擎。用户消息、完整Spec、历史、confirmed facts、Skills和
references进入模型，模型输出Structured Understanding和SimulationSpecPatch。
Regex只允许解析数字、单位、坐标和做证据校验，禁止决定场景、形状或写Spec。

验证Codex创建的Skills真实使用：保存Prompt Trace、hash、references和InvocationRecord，
执行Skills ON/OFF、Wrong Skill和Skill missing消融。

实现Project→Study→Variant→SpecVersion→Artifact→Run层级和Open CaseIR。
几何支持primitive、polygon、parametric、CSG、imported和unknown，不再以固定scenario
作为系统本体。

增加模型参与编译：模型输出结构化CompilePlan、adapter选择、边界计划、数值计划和
function objects；确定性AST和renderer生成Foundation 13文件。未知能力可以由模型
提出并在沙箱生成受限代码扩展，但必须经过测试、静态编译、审查和能力注册，不能直接
写生产case或执行任意shell。

执行100条未见表达、开放几何、LLM关闭、Context Removal和Skill消融。
然后完成真实工作站Doctor、上传、网格、checkMesh、smoke、full solver、postprocess、
collect、数值/物理验证、图、动画、报告和UI持久化。

第一批运行圆柱、三角、矩形Variant、正弦/余弦、双圆柱、时变入口、polygon和完整
MeasurementPlan；随后同一通用主链运行后台阶、周期通道、顶盖方腔、自然对流和三维
周期圆柱。

每次提交前检查当前分支包含MAIN_AFTER_TRAE_MERGE_SHA。发现回到旧版本立即停止。
没有P0修复、模型参与证据、Skill增益、模型辅助编译、开放CaseIR和真实工作站E2E，
不得结束。
```
