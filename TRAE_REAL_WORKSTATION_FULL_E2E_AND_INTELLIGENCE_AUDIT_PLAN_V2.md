# Trae 下一阶段任务 V2：智能性审计 + 真实工作站完整端到端验证

> **执行对象：Trae**
>
> **当前条件：**
>
> - OpenFOAM 工作站已经恢复连接；
> - 工作站运行 OpenFOAM Foundation 13；
> - 当前系统已经完成部分仿真前链路和静态编译；
> - 当前报告虽然显示 93/93 通过，但仍不能证明 LLM、Skill 和开放场景能力真正生效。
>
> **本轮目标：**
>
> 1. 证明智能体不是关键词、正则和固定场景模板驱动；
> 2. 证明 LLM 是事实理解、修改和能力判断的主引擎；
> 3. 证明 Codex 生成的 CFD/OpenFOAM Skills 被真实加载、注入和使用；
> 4. 使用真实工作站完成从用户输入到 OpenFOAM 结果、后处理、验证和 UI 展示的完整端到端；
> 5. 发现问题后修复通用机制，不得为单个句子或单个案例增加专用补丁。
>
> **本轮不得只完成一批已有 archive 的运行。**
>
> 必须同时完成：
>
> ```text
> 智能性证据
> + Skill有效性证据
> + 开放几何/场景证据
> + 真实工作站运行证据
> + 结果可信性证据
> + UI持久化证据
> ```

---

# 1. 当前报告存在的根本风险

当前报告中的修复仍包含：

```text
_extract_reynolds()
_extract_end_time()
计算域正则
CYLINDER_KEYWORDS
STL/多边形中文关键词
```

这说明系统仍可能是：

```text
关键词/正则识别
→ 圆柱类场景路由
→ 已知模板
→ LLM做部分补充
```

而不是：

```text
LLM读取完整上下文与Skill
→ 结构化理解
→ 通用SpecPatch
→ Open CaseIR
→ 可组合能力编译
```

此外，当前报告主要覆盖：

```text
圆柱
三角形
矩形
余弦丘
```

不能证明系统能够处理开放场景。

因此本轮第一阶段必须先证明智能性，再开始批量真实运行。

---

# 2. 本轮唯一完成定义

只有以下完整主链真实成立，才允许结束：

```text
用户自然语言
→ 通用 Research Session
→ Skill Router
→ Skill / Reference 注入
→ LLM Structured Understanding
→ Intent / Facts / Ambiguities / Conflicts / Capability
→ Canonical SimulationStudySpec
→ SimulationSpecPatch
→ Dependency / Physics Review
→ 用户确认
→ CaseIR
→ Capability Resolution
→ Foundation 13 Case Blueprint
→ 确定性编译
→ Static Validation
→ Artifact / Manifest / Digest
→ Workstation Doctor
→ Upload
→ Remote Extract
→ blockMesh
→ snappyHexMesh 或适配网格流程
→ checkMesh
→ Smoke Test
→ Full Solver
→ Runtime Monitoring
→ Postprocess
→ Collect
→ Metrics
→ Numerical Validation
→ Physical Validation
→ Figures / Animation
→ Report
→ UI Persistence
```

---

# 3. P0：证明 LLM 真正参与

## 3.1 主事实提取必须由 LLM 完成

主链必须改为：

```text
user message
+ full current spec
+ recent conversation
+ confirmed facts
+ unresolved conflicts
+ selected skills/references
→ LLMUnderstandingResult
→ deterministic validator
```

模型输出：

```python
class LLMUnderstandingResult(BaseModel):
    intent: IntentType
    explicit_facts: list[StructuredFact]
    proposed_patch: SimulationSpecPatch | None
    ambiguities: list[ClarificationRequest]
    conflicts: list[Conflict]
    capability_requirements: list[CapabilityRequirement]
    evidence_quotes: list[str]
    untouched_paths: list[str]
```

## 3.2 正则只能做校验

允许：

- 数字解析；
-单位转换；
-科学计数法；
-验证模型引用的数字是否存在；
-防止幻觉。

禁止：

- 关键词决定场景；
-关键词决定几何；
-关键词决定整条 pipeline；
-正则先构建主 Spec。

## 3.3 LLM Disabled Test

关闭 LLM 且禁止 regex fallback。

以下表达必须失败或明确提示模型不可用：

```text
算久一点，跑到15秒
别动步长，把终止时刻往后延5秒
把贴壁的小尖块换成上窄下宽的小台
刚才那个点向出口方向挪两个直径
```

如果仍然全部成功，说明系统仍由规则驱动。

## 3.4 Context Removal Test

分别移除：

```text
current spec
conversation history
skills
confirmed facts
```

预期：

| 移除内容 | 应受影响 |
|---|---|
| current spec | 相对修改失败 |
| history | 指代和连续修改失败 |
| skills | 物理审查和专业方案质量下降 |
| confirmed facts | 保留字段和冲突处理下降 |

没有变化说明上下文没有真正使用。

## 3.5 字段级来源追踪

每个字段必须能追踪：

```text
user quote
→ LLM field
→ validator decision
→ patch operation
→ spec version
→ CaseIR
→ compiler output
```

---

# 4. P0：证明 Skills 真实使用

## 4.1 每次调用必须保存

```json
{
  "skill_ids": [],
  "skill_bundle_hash": "",
  "reference_ids": [],
  "reference_hashes": {},
  "prompt_snapshot_id": "",
  "actual_model": "",
  "model_request_id": "",
  "spec_id": "",
  "spec_version": 0
}
```

## 4.2 Prompt Trace 必须可审查

必须能看到脱敏后的：

```text
workflow skill
main professional skill
selected references
current spec
recent history
confirmed facts
conflicts
output schema
```

## 4.3 Skill ON/OFF 消融

同一批至少 30 条请求运行：

```text
A. Skills ON
B. Skills OFF
C. Wrong Skill
```

比较：

```text
structured output success
minimal patch accuracy
ambiguity recall
conflict recall
unknown capability recall
unrelated field mutation
physics review quality
OpenFOAM plan completeness
```

## 4.4 Wrong Skill Negative Control

例如几何修改时只注入 postprocessing Skill。

应出现：

- Router 拒绝；
-或者模型质量显著下降。

若仍100%通过，说明 Skill 没被使用或测试标准无效。

---

# 5. 开放几何与场景能力

## 5.1 几何测试集

必须覆盖：

```text
cylinder
ellipse
capsule
triangle
rectangle
trapezoid
wedge
arbitrary polygon
annulus
sine bump
cosine bell
piecewise ramp
two unequal cylinders
three mixed obstacles
NACA0012
imported STL
superellipse
CSG union
CSG subtraction
```

每类至少 5 种自然语言表达。

## 5.2 不能依赖形状关键词

例如：

```text
一个四条边且四个角均为直角的障碍
一个上边短、下边长的四边形凸起
两端半圆、中间直线段连接的障碍
```

模型必须理解或合理澄清。

## 5.3 Geometry 输出要求

```python
class GeometryEntity(BaseModel):
    entity_id: str
    representation: Literal[
        "primitive",
        "polygon",
        "parametric",
        "csg",
        "imported",
        "unknown"
    ]
    original_user_semantics: str
    parameters: dict
    relations: list[GeometryRelation]
    capability_status: str
    fingerprint: str
```

---

# 6. 通用研究主链

不得继续只使用：

```text
/api/v5/cylinder-flow
```

必须建立或使用：

```text
/api/research-sessions
```

使用同一主链处理：

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

不得为每种场景创建完整独立 pipeline。

应使用：

```text
geometry adapter
physics adapter
boundary adapter
mesh adapter
measurement adapter
solver adapter
postprocess adapter
```

---

# 7. 工作站 Doctor

真实运行前先执行 Doctor。

保存：

```json
{
  "host_profile": "",
  "connected": true,
  "openfoam_distribution": "Foundation",
  "openfoam_version": "13",
  "commands": {
    "foamRun": "",
    "blockMesh": "",
    "snappyHexMesh": "",
    "checkMesh": "",
    "postProcess": "",
    "decomposePar": "",
    "mpirun": ""
  },
  "paraview_available": false,
  "pvpython_available": false,
  "ffmpeg_available": false,
  "disk_free_bytes": 0,
  "cpu_count": 0,
  "memory_bytes": 0,
  "run_root": ""
}
```

禁止记录：

- 私钥；
-密码；
-token；
-敏感路径内容。

Doctor 不通过不得提交。

---

# 8. 真实工作站 E2E 第一批场景

## RUN-001：二维圆柱基准

输入：

```text
二维域10m×5m，圆柱半径0.1m，圆心(5m,2m)，U=1m/s，Re=200。
左速度入口，右压力出口，上自由出流，下无滑移。
计算15s，deltaT=0.005s。
输出速度、压力、涡量、Cd、Cl、频率和St。
```

必须验证：

- LLM事实；
-Skill；
-CaseIR；
-controlDict；
-mesh；
-checkMesh；
-Cd/Cl；
-频谱；
-St；
-结果持久化。

---

## RUN-002：圆柱 + 三角贴壁障碍

目的：

- triangle 不变成 cosine_bell；
-障碍真实进入几何和网格；
-结果与 RUN-001 独立。

检查：

```text
geometry fingerprint
STL/geometry file
mesh patches
archive SHA
```

---

## RUN-003：三角改矩形 Variant

用户在同一 study：

```text
保留原方案，创建对照组，把三角障碍改成同宽同高的矩形。
```

验证：

- CREATE_VARIANT；
-parent spec；
-只改 geometry；
-两个 run 独立；
-结果可对比。

---

## RUN-004：正弦凸起 vs 余弦钟形

创建两个 variant：

```text
A：正弦凸起
B：余弦钟形，两端斜率为零
```

验证：

- 参数化公式不同；
-网格不同；
-制品 digest 不同；
-结果不串联。

---

## RUN-005：双圆柱

输入：

```text
在原圆柱下游2D增加同尺寸圆柱。
分别输出两个圆柱Cd/Cl及总体阻力。
```

验证：

- 两个 entity；
-分别 patch；
-三个 force measurement；
-后处理完整。

---

## RUN-006：时变入口

输入：

```text
入口速度从0开始，2秒内线性增加到1m/s，之后保持。
```

验证：

- 时变 BC 编译；
-Foundation 13 兼容；
-真实运行；
-入口随时间变化证据。

---

## RUN-007：任意 polygon

输入：

```text
下壁增加一个五边形障碍，顶点为
(4.9,0)、(5.1,0)、(5.15,0.04)、(5,0.08)、(4.85,0.04)。
```

验证：

- polygon；
-顶点顺序；
-网格；
-真实运行。

---

## RUN-008：完整 MeasurementPlan

输入：

```text
增加圆柱下游5D速度探针；
计算出口前1m截面平均速度；
输出下壁面剪切；
输出Cd/Cl、涡量、最后5秒平均场和RMS。
```

验证：

```text
probes
surfaceFieldValue
wallShearStress
forceCoeffs
vorticity
fieldAverage
```

都生成真实输出。

---

# 9. 第二批通用场景 E2E

第一批通过后，使用同一通用主链运行：

## RUN-009：后台阶

验证：

- step geometry；
-充分发展入口；
-再附长度；
-壁面剪切。

## RUN-010：周期通道

验证：

- cyclic pair；
-pressure gradient；
-无入口出口；
-解析速度剖面比较。

## RUN-011：顶盖驱动方腔

验证：

- moving wall；
-中心线速度；
-主涡；
-稳态验证。

## RUN-012：自然对流方腔

验证：

- energy；
-gravity；
-p_rgh；
-Nu；
-温度和速度场。

## RUN-013：三维周期圆柱

验证：

- 3D；
-spanwise cyclic；
-LES/RANS 决策；
-并行；
-三维结构和资源。

---

# 10. 每个真实 Run 的十个 Gate

## Gate 1：理解

必须有：

```text
LLM trace
Skill trace
facts
ambiguities
conflicts
capabilities
```

## Gate 2：Spec

必须有：

```text
confirmed spec
patch history
provenance
no unresolved critical fields
```

## Gate 3：CaseIR

检查：

```text
geometry
physics
boundaries
mesh intent
measurements
capabilities
```

## Gate 4：Compiler

检查：

```text
Foundation 13
required files
patch names
cross-file consistency
digest
no placeholders
```

## Gate 5：Mesh

执行：

```text
blockMesh
snappyHexMesh
checkMesh
```

必须记录：

```text
cells
maxNonOrtho
maxSkewness
negative volume
illegal faces
patch list
```

硬失败条件：

```text
negative volume > 0
missing patch
fatal mesh error
```

## Gate 6：Smoke

执行有限步 smoke。

检查：

```text
fields readable
no immediate FPE
no NaN
no missing BC
no dictionary error
```

## Gate 7：Full Solver

必须：

- 真实 external job ID；
-真实日志；
-可恢复状态；
-进度；
-取消；
-不重复提交。

## Gate 8：Postprocess

必须收集：

```text
raw functionObject outputs
field files
figures
animations
metrics
```

## Gate 9：Validation

分别判定：

```text
PROCESS_COMPLETED
NUMERICALLY_ACCEPTABLE
PHYSICALLY_CREDIBLE
RESEARCH_READY
```

不能合并成一个成功状态。

## Gate 10：UI

检查：

- 结果持久化；
-方案和结果双向切换；
-刷新；
-run history；
-variant 对比；
-证据链接；
-失败状态。

---

# 11. 远程执行安全设计

## 11.1 只允许类型化命令

允许：

```text
doctor
upload
extract
blockMesh
snappyHexMesh
checkMesh
smoke
solve
postProcess
collect
cancel
```

不得执行任意用户 shell。

## 11.2 每个任务独立目录

```text
<run_root>/<session_id>/<run_id>/
```

不得覆盖其他 run。

## 11.3 上传前验证

检查：

```text
archive digest
manifest
path traversal
symlink
sensitive files
file size
```

## 11.4 远程制品证据

保存：

```text
remote directory
uploaded SHA
extracted file list
command list
exit codes
timestamps
```

---

# 12. 工作站故障注入

必须真实或受控模拟：

## WS-FAIL-001：SSH 短暂断开

验证：

- 不重复提交；
-job ID 保留；
-恢复查询。

## WS-FAIL-002：上传中断

验证：

- partial archive 不执行；
-SHA 校验；
-可重传。

## WS-FAIL-003：磁盘不足

验证：

- preflight 阻断；
-不删除用户文件。

## WS-FAIL-004：OpenFOAM 环境未加载

验证：

- doctor 失败；
-不提交。

## WS-FAIL-005：checkMesh 失败

验证：

- solver 不启动；
-诊断 Skill；
-artifact 保留。

## WS-FAIL-006：smoke 发散

验证：

- full solver 不启动；
-诊断；
-高风险修复需确认。

## WS-FAIL-007：full run 中断

验证：

- 状态明确；
-可 restart；
-不显示 completed。

## WS-FAIL-008：后处理失败

验证：

- solver completed；
-postprocess failed；
-可单独重试。

## WS-FAIL-009：collect 中断

验证：

-远程结果保留；
-可重收集；
-不重跑 solver。

## WS-FAIL-010：后端重启

验证：

- run 状态恢复；
-job ID；
-结果关联。

---

# 13. 物理可信性验证

## 13.1 圆柱 Re=200

检查：

```text
Cd mean
Cl oscillation
dominant frequency
St
mass conservation
transient stationarity
```

不要把报告中的预期范围写成硬通过标准。

应使用：

```text
合理范围
趋势
与文献/基准条件一致性
误差解释
```

## 13.2 时间长度

如果有效涡脱落周期过少：

```text
frequency confidence = LOW
```

不能输出高精度 St。

## 13.3 网格与时间步

至少对基准圆柱做：

```text
coarse / medium / fine
deltaT 0.01 / 0.005 / 0.0025
```

比较：

```text
Cd
St
mesh quality
cost
```

## 13.4 Silent Failure

检查：

- 场是否近乎静止；
-入口/出口方向；
-质量流量；
-边界是否生效；
-force 是否非物理常数；
-结果是否来自当前 run。

---

# 14. 重新检查当前高风险修复

## 14.1 `_extract_*`

生产模式中不得绕过 LLM。

## 14.2 `CYLINDER_KEYWORDS`

不得决定通用研究路由。

## 14.3 水黏度范围

不得通过放宽水物性范围解决约束冲突。

正确处理：

```text
真实20℃水
U=1m/s
D=0.2m
Re=200
```

应要求用户选择：

- 保持真实水物性；
-保持 U；
-保持 Re；
-定义自定义高黏流体。

## 14.4 LLM 幻觉防护

必须使用：

```text
source quote
entity ID
schema
current spec
capability registry
```

不能只做关键词交叉验证。

---

# 15. 结果与报告产物

每个 run：

```text
artifacts/real_e2e/<run_id>/
├── conversation.json
├── prompt_trace.json
├── skill_invocations.json
├── model_raw_response.json
├── model_structured_output.json
├── spec_versions/
├── patches/
├── dependency_review.json
├── physics_review.json
├── case_ir.json
├── capability_resolution.json
├── compiled_manifest.json
├── archive.sha256
├── workstation_doctor.json
├── remote_upload.json
├── mesh/
│   ├── blockMesh.log
│   ├── snappyHexMesh.log
│   └── checkMesh.log
├── smoke/
├── solver/
│   └── solver.log
├── postProcessing/
├── figures/
├── animations/
├── metrics.json
├── numerical_validation.json
├── physical_validation.json
├── ui_assertions.json
└── report.md
```

---

# 16. 最终报告必须分开统计

生成：

```text
LLM_PARTICIPATION_AUDIT.md
SKILL_RUNTIME_EFFECTIVENESS_REPORT.md
OPEN_GEOMETRY_CAPABILITY_REPORT.md
GENERAL_RESEARCH_PIPELINE_REPORT.md
REAL_WORKSTATION_E2E_REPORT.md
WORKSTATION_FAILURE_RECOVERY_REPORT.md
PHYSICAL_VALIDATION_REPORT.md
UI_PERSISTENCE_REPORT.md
```

统计必须分开：

```text
LLM_SEMANTIC_E2E
SKILL_ABLATION
API_CONTRACT
PERSISTENCE_E2E
REAL_WORKSTATION_E2E
MESH_VALIDATION
SOLVER_VALIDATION
POSTPROCESS_VALIDATION
PHYSICAL_VALIDATION
UNIT
FAULT_INJECTION
MUTATION
```

不得再合并为单一“100%智能体通过率”。

---

# 17. 第一轮执行顺序

## 阶段 A：智能性证据

1. Prompt Trace；
2. LLM Disabled；
3. Context Removal；
4. Skill ON/OFF；
5. Wrong Skill；
6. 100条 paraphrase；
7. 15类开放几何。

## 阶段 B：工作站准备

1. Doctor；
2. archive/manifest 校验；
3. 远程目录和命令 allowlist；
4. 上传/恢复/取消测试。

## 阶段 C：第一批真实运行

```text
RUN-001
RUN-002
RUN-003
RUN-004
RUN-005
RUN-006
RUN-007
RUN-008
```

## 阶段 D：第二批通用场景

```text
RUN-009
RUN-010
RUN-011
RUN-012
RUN-013
```

## 阶段 E：可信性

```text
mesh sensitivity
time-step sensitivity
failure injection
result validation
UI persistence
```

---

# 18. 最终完成标准

- [ ] LLM Disabled 测试证明无模型不能完成复杂理解；
- [ ] Prompt Trace 证明完整上下文进入模型；
- [ ] Skill ON/OFF 有可量化增益；
- [ ] Wrong Skill 对照有效；
- [ ] 100条未见表达；
- [ ] 15类开放几何；
- [ ] 通用 Research Session 主链；
- [ ] Workstation Doctor；
- [ ] 8个第一批真实 E2E；
- [ ] 5个非圆柱通用场景；
- [ ] 每个 run 十个 Gate；
- [ ] 至少10个工作站故障恢复测试；
- [ ] probes/surfaceFieldValue/wallShearStress 真实输出；
- [ ] 网格敏感性；
- [ ] 时间步敏感性；
- [ ] 结果图和动画；
- [ ] 数值和物理验证；
- [ ] UI刷新和结果持久化；
- [ ] 无 fake、无静默 fallback；
- [ ] 不再靠新增关键词支持新场景；
- [ ] 最终报告分开统计各类证据。

---

# 19. 直接交给 Trae 的总指令

```text
工作站已经恢复。现在需要完成真实工作站完整端到端，但不能只运行当前已有的几个
圆柱类archive就宣布成功。

第一步先证明智能体真正智能：
- 保存真实Prompt Trace；
-证明完整Spec、历史、Skill和references进入模型；
-运行LLM Disabled、Context Removal、Skill ON/OFF、Wrong Skill；
-运行100条未见表达和15类开放几何；
-禁止regex fallback和CYLINDER_KEYWORDS决定场景。

事实提取必须由LLM输出结构化FactSet，regex只做数值、单位和证据校验。
Codex Skills必须有Registry、Router、Prompt、Reference、Hash、InvocationRecord和
消融增益证据。

完成智能性审计后，在真实OpenFOAM Foundation 13工作站执行完整E2E：
RUN-001 圆柱基准
RUN-002 圆柱+三角
RUN-003 三角→矩形Variant
RUN-004 正弦vs余弦
RUN-005 双圆柱
RUN-006 时变入口
RUN-007 polygon
RUN-008 probes+截面平均+壁面剪切

每个run必须通过：
理解 → Spec → CaseIR → Compiler → Mesh → Smoke → Full Solver →
Postprocess → Validation → UI 十个Gate。

随后使用同一个通用Research Session主链运行：
后台阶、周期通道、顶盖方腔、自然对流、三维周期圆柱。
禁止为每个场景创建独立完整pipeline。

执行工作站Doctor和至少10个故障恢复测试，包括SSH中断、上传失败、磁盘不足、
环境未加载、checkMesh失败、smoke发散、full run中断、后处理失败、收集失败和
后端重启。

特别复查：
- 水物性不能通过放宽范围解决冲突；
- triangle不能映射cosine_bell；
-不同几何必须有不同CaseIR、mesh recipe和archive SHA；
-MeasurementPlan必须产生真实probes、surfaceFieldValue、wallShearStress、
forceCoeffs、fieldAverage和vorticity输出；
-结果必须做数值和物理可信性验证；
-图片、动画、日志、指标和报告必须持久化到UI。

最终报告必须分别统计LLM语义、Skill消融、API、真实工作站、网格、求解、
后处理、物理验证、故障恢复和UI，不能再合并成“智能体100%”。

没有智能性证据、Skill增益、真实工作站run、非圆柱通用场景、物理验证和UI持久化，
不得结束。
```
