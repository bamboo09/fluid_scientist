# Fluid Scientist 工作站停机期间：仿真前智能体全链路能力探索与代码验证方案

> **执行对象：Trae**
>
> **当前约束：** OpenFOAM 工作站停机检修，暂不进行真实网格生成、求解和后处理。
>
> **本阶段唯一目标：** 把用户提出研究需求到“生成一份经过用户确认、可静态审查、可等待工作站恢复后提交的仿真方案与编译制品”之间的全部链路彻底跑通、测试、走查和修复。
>
> **重点不是实验执行，而是：**
>
> - 用户意图是否理解正确；
> - 模型是否真正参与；
> - 研究方案是否合理；
> - 多轮修改是否可靠；
> - 歧义和冲突是否正确处理；
> - Skill 是否真实生效；
> - 参数依赖是否同步更新；
> - 未知场景是否正确暴露能力边界；
> - CaseIR 和 OpenFOAM 方案是否结构化、一致、可静态验证；
> - UI 是否清晰、可确认、可撤销、可持久化；
> - 当前代码是否存在双主链、模板通吃、fake fallback、死代码和局部补丁。
>
> **本阶段禁止伪造任何仿真结果。**

---

# 1. 本阶段应该完成到哪里

完整目标链路：

```text
用户输入研究需求
→ 识别是新建、修改、删除、确认、撤销、询问还是澄清
→ 加载正确 CFD/OpenFOAM Skill
→ 模型读取完整会话和当前方案
→ 提取显式事实
→ 识别歧义、冲突、缺失信息和未知能力
→ 构建研究目标、物理模型、几何、边界、数值、指标和验证需求
→ 生成 SimulationStudySpec 草案
→ 用户多轮修改
→ 模型输出最小 SimulationSpecPatch
→ Patch 校验、单位换算和依赖更新
→ 显示修改前后差异及影响
→ 用户确认方案
→ 生成 CaseIR
→ Capability Resolution
→ OpenFOAM Foundation 13 Case Blueprint
→ 确定性静态编译
→ Dictionary、跨文件和语义一致性检查
→ 编译预览、manifest、archive、digest
→ 用户确认可执行制品
→ 标记 READY_FOR_WORKSTATION / ENVIRONMENT_BLOCKED
```

工作站恢复前，不要求：

```text
checkMesh
solver execution
真实场数据
Cd/Cl/St 数值
图片和动画
物理可信性结论
```

但必须准备好这些内容所需要的：

```text
MeasurementPlan
function objects
PostProcessPlan
ValidationPlan
expected artifacts
```

---

# 2. 严禁事项

## 2.1 不得伪造运行

禁止：

- fake external job ID；
-假 solver log；
-随机 Cd、Cl、St；
-静态假云图；
-mock worker 返回 COMPLETED；
-把 compiler 成功显示为仿真成功。

正确状态：

```text
COMPILED_STATICALLY_VALID
READY_FOR_WORKSTATION
ENVIRONMENT_BLOCKED
```

## 2.2 不得继续做单参数补丁

禁止在主链新增：

```python
if "仿真时间" in text:
if "三角形" in text:
if "入口速度" in text:
if "压力出口" in text:
```

这些只能存在于测试样例或 Skill 文档中。

## 2.3 不得让未知场景降级成模板

禁止：

```text
triangle → cosine_bell
polygon → rectangle
unknown boundary → zeroGradient
unknown study → cylinder_flow
```

未知能力必须保留原始语义并输出：

```text
CONFIG_EXTENSION_REQUIRED
CODE_EXTENSION_REQUIRED
UNSUPPORTED
```

## 2.4 不得静默 fallback

以下问题必须明确失败：

- 模型超时；
-Structured Output 错误；
-Skill 缺失；
-Schema 无法表达；
-Patch 路径非法；
-依赖冲突；
-编译器能力不足。

不得切换 fake、regex 或旧模板继续。

---

# 3. 本阶段状态设计

建议统一使用：

```python
class PreExperimentStatus(str, Enum):
    UNDERSTANDING = "UNDERSTANDING"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    DRAFT_READY = "DRAFT_READY"
    PATCH_PENDING_CONFIRMATION = "PATCH_PENDING_CONFIRMATION"
    SPEC_CONFIRMED = "SPEC_CONFIRMED"
    CAPABILITY_REVIEW_REQUIRED = "CAPABILITY_REVIEW_REQUIRED"
    CAPABILITY_BLOCKED = "CAPABILITY_BLOCKED"
    COMPILE_FAILED = "COMPILE_FAILED"
    COMPILED_STATICALLY_VALID = "COMPILED_STATICALLY_VALID"
    ARTIFACT_CONFIRMATION_REQUIRED = "ARTIFACT_CONFIRMATION_REQUIRED"
    READY_FOR_WORKSTATION = "READY_FOR_WORKSTATION"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
```

不得使用：

```text
RUNNING
COMPLETED
RESULTS_READY
RESEARCH_READY
```

---

# 4. 测试层级

```text
T0  当前运行版本和调用链审计
T1  Skill 路由与模型行为
T2  意图、事实、歧义、冲突
T3  研究方案设计
T4  多轮修改与 SpecPatch
T5  Dependency 和物理审查
T6  CaseIR 和 Capability
T7  OpenFOAM 静态编译
T8  UI、持久化和交互
T9  代码走查、属性测试、变异测试
```

本阶段必须完成 T0–T9。

---

# 5. 每个场景保存的证据

```text
artifacts/pre_experiment_tests/<scenario_id>/<timestamp>/
├── scenario.yaml
├── conversation.json
├── skill_selection.json
├── selected_references.json
├── model_request_trace.json
├── model_raw_response.json
├── structured_output.json
├── facts.json
├── ambiguities.json
├── conflicts.json
├── current_spec_before.json
├── patch.json
├── patch_validation.json
├── dependency_impact.json
├── current_spec_after.json
├── spec_diff.json
├── physics_review.json
├── case_ir.json
├── capability_resolution.json
├── case_blueprint.json
├── compiled_manifest.json
├── static_validation.json
├── ui_assertions.json
└── bug_report.md
```

---

# 6. 当前代码全链路走查

## 6.1 绘制真实调用链

创建：

```text
docs/audits/PRE_EXPERIMENT_CURRENT_CALL_CHAIN.md
```

必须从前端开始追踪到 compiler：

| 阶段 | 文件 | 类/函数 | 输入 | 输出 | 数据存储 | 错误处理 |
|---|---|---|---|---|---|---|

必须覆盖：

```text
Frontend message submit
API route
Session loader
Skill Router
Prompt Builder
Model Client
Structured Output parser
Fact merge
Ambiguity/conflict engine
Patch Engine
Dependency Engine
Spec repository
Confirmation API
CaseIR builder
Capability resolver
Case planner
OpenFOAM compiler
Static validators
Artifact repository
```

## 6.2 检查双主链

搜索：

```bash
rg -n "ExperimentPlan|ExperimentSpec|SimulationStudySpec|legacy|old_plan" src apps
rg -n "plan_to_spec|spec_to_plan|compile_.*plan" src
```

要求明确：

- 哪个是唯一 canonical spec；
-哪些只用于迁移；
-哪些仍在活跃主链；
-是否发生新 Spec → 旧 Plan → Compiler。

活跃主链不得回退旧计划。

## 6.3 检查 fake 和 fallback

```bash
rg -n "fake|fallback|default_plan|mock|regex|template" src apps
rg -n "except Exception" src/fluid_scientist
```

每项分类：

```text
TEST_ONLY
SAFE_RECOVERY
DANGEROUS_FALLBACK
DEAD_CODE
```

`DANGEROUS_FALLBACK` 必须移除。

## 6.4 检查参数专用补丁

```bash
rg -n '仿真时间|三角形|矩形|改成水|入口速度|压力出口' src apps
```

业务代码中出现时必须解释。如果是自然语言关键词驱动核心逻辑，应重构。

---

# 7. 意图识别能力测试

系统必须区分以下意图，而不是每次都创建新方案。

## INTENT-001：新建研究

```text
研究二维圆柱绕流，Re=200。
```

预期：

```text
CREATE_STUDY
```

## INTENT-002：修改当前方案

```text
仿真时间改成15秒。
```

预期：

```text
MODIFY_EXISTING_SPEC
```

## INTENT-003：追加信息

```text
流体使用20摄氏度的水。
```

预期：

```text
ADD_FACTS_TO_EXISTING_SPEC
```

## INTENT-004：删除要求

```text
压力云图不需要了。
```

预期：

```text
REMOVE_REQUIREMENT
```

## INTENT-005：撤销

```text
撤销刚才修改材料的操作。
```

预期：

```text
UNDO_SPECIFIC_CHANGE
```

## INTENT-006：否定模型理解

```text
不是结束于15秒，是再运行15秒。
```

预期：

```text
CORRECT_PENDING_INTERPRETATION
```

## INTENT-007：只询问，不修改

```text
当前雷诺数是怎么计算的？
```

预期：

```text
ASK_EXPLANATION
```

不得生成 Patch。

## INTENT-008：确认

```text
这个方案没问题，继续。
```

预期：

```text
CONFIRM_CURRENT_STAGE
```

## INTENT-009：复制对照方案

```text
保留当前方案，复制一个矩形障碍的对照组。
```

预期：

```text
CREATE_VARIANT
```

## INTENT-010：新建另一个研究

```text
当前方案保存，再新建一个自然对流方腔。
```

预期：

```text
CREATE_NEW_STUDY
```

---

# 8. 显式事实提取测试

模型必须区分：

```text
USER_EXPLICIT
USER_CONFIRMED
MODEL_RECOMMENDED
DERIVED
DEFAULT_PENDING
UNKNOWN
```

## FACT-001：完整圆柱场景

提取：

- domain；
-cylinder；
-U；
-Re；
-boundaries；
-time；
-observations。

## FACT-002：混合单位

```text
圆柱直径200毫米，障碍高5厘米，域长10米。
```

预期统一 SI，但保留原单位来源。

## FACT-003：中文数字

```text
计算十五秒，时间步五毫秒。
```

## FACT-004：科学计数法

```text
运动黏度1e-5平方米每秒。
```

## FACT-005：相对位置

```text
障碍位于圆柱正下方并贴附下壁。
```

应生成关系，不只是坐标字符串。

## FACT-006：条件约束

```text
入口速度保持1米每秒，同时Re保持200。
```

应保存两个约束，并检查是否与物性冲突。

---

# 9. 歧义处理测试

## AMB-001：仿真时间

当前 startTime=5s：

```text
仿真时间设为15秒。
```

必须询问：

- 结束于15秒；
-再运行15秒，结束于20秒。

## AMB-002：正中央

域高5m，圆心距下壁2m：

```text
圆柱位于流场正中央。
```

必须判断“中央”是 x 中央还是 x/y 都中央。

## AMB-003：高度

上下文有 domain 高度、障碍高度、圆柱高度：

```text
高度改成0.08。
```

必须询问对象。

## AMB-004：把它下移

上下文有圆柱、障碍、探针：

```text
把它向下移动0.1米。
```

必须询问实体。

## AMB-005：自由出流

```text
上边界采用自由出流。
```

必须解释不同字段的边界计划，不能只映射一个 OpenFOAM 类型。

## AMB-006：稍微加密

```text
网格稍微加密一点。
```

必须提供量化选项或询问目标，不得固定使用某倍率。

## AMB-007：用湍流模型

```text
改成湍流。
```

必须询问/推荐 RANS 或 LES，并结合 Re、维度和目标。

## AMB-008：平均速度

```text
输出平均速度。
```

必须区分：

- 时间平均；
-截面平均；
-体积平均；
-入口平均。

---

# 10. 冲突检测测试

## CONFLICT-001：Re 数值矛盾

```text
D=0.2m，U=1m/s，ν=1e-5m²/s，Re=200。
```

必须发现计算结果不是200。

## CONFLICT-002：边界角色矛盾

```text
左边既是速度入口又是周期边界。
```

必须阻断。

## CONFLICT-003：稳态和频率

```text
使用稳态求解，同时计算涡脱落频率。
```

必须识别目标冲突。

## CONFLICT-004：2D 和 Q 等值面

```text
做二维仿真并输出Q准则三维等值面。
```

必须解释不一致。

## CONFLICT-005：材料改变但物性锁定

```text
把空气改成水，但密度、黏度和Re都保持原值。
```

必须询问哪些是实际约束。

## CONFLICT-006：统计窗口超范围

```text
仿真到10秒，但统计10到15秒。
```

必须阻断或建议延长仿真时间。

---

# 11. 研究方案设计测试

方案不能只是参数表，必须包含：

```text
研究问题
物理假设
几何
材料
边界
初始条件
模型
数值设置
网格策略
观测指标
验证方案
风险
资源预估
待确认项
能力缺口
```

## PLAN-001：二维圆柱绕流

检查是否：

- 判断瞬态层流；
-包含 Cd/Cl/St；
-建议合理时间窗口；
-包含阻塞比和域长度审查；
-没有多余湍流字段。

## PLAN-002：后台阶

检查：

- 再附长度；
-充分发展入口；
-局部网格加密；
-回流区识别。

## PLAN-003：周期泊肃叶

检查：

- cyclic；
-pressure gradient；
-解析解基准；
-没有入口出口。

## PLAN-004：自然对流方腔

检查：

- gravity；
-p_rgh；
-热边界；
-Nu；
-Rayleigh/Prandtl；
-能力支持状态。

## PLAN-005：三维圆柱 LES

检查：

- spanwise 周期；
-LES；
-网格/时间步；
-计算资源；
-不能照搬二维方案。

## PLAN-006：未见超椭圆

检查：

- 能正确理解研究问题；
-不伪造支持；
-输出能力扩展需求；
-保留用户语义和参数。

---

# 12. 多轮修改与 SpecPatch 测试

## EDIT-001：时间修改

```text
仿真时间设为15秒。
```

预期：

```json
{
  "op": "replace",
  "path": "/numerics/time/end_time",
  "value": {"value": 15, "unit": "s"}
}
```

## EDIT-002：相对修改

```text
时间步改成原来的一半。
```

必须读取当前值，不能使用固定默认。

## EDIT-003：三角改矩形

只改变目标 geometry entity，其他不变。

## EDIT-004：空气改水

不能只改字符串，必须触发依赖和物理审查。

## EDIT-005：添加探针

新增 observations/probes，不修改 geometry。

## EDIT-006：删除压力图

只删除 figure request，不删除 p 字段或 force requirements。

## EDIT-007：批量修改

```text
入口速度改成2米每秒，结束时间改20秒，再增加一个压力探针。
```

一个原子 Patch，多 operation。

## EDIT-008：定向撤销

```text
撤销材料修改，保留时间和几何修改。
```

必须生成 inverse patch。

## EDIT-009：重复请求

重复“时间改15秒”。

第二次应 no-op，不新增无意义版本。

## EDIT-010：版本冲突

两个客户端基于同一 spec version 修改。

必须乐观锁或 rebase，不得静默覆盖。

---

# 13. 十轮和五十轮会话

## SESSION-10

1. 创建圆柱 + triangle；
2. 时间15秒；
3. deltaT=0.005；
4. triangle→rectangle；
5. air→water，Re保持；
6. U保持1；
7. 通过ν满足Re；
8. 添加探针；
9. 删除压力图；
10. 最后3秒统计。

检查最终 spec 和每轮 Patch。

## SESSION-50

至少包含：

- 15次参数修改；
-5次几何修改；
-5次观测修改；
-5次澄清；
-5次撤销；
-5次询问；
-5次确认；
-5次无效或冲突输入。

检查：

- canonical spec 正确；
-摘要没有覆盖事实；
-实体 ID 不丢；
-第50轮修改正确；
-token 和 Skill 加载受控。

---

# 14. Dependency Engine 验证

## 14.1 时间

```text
start/end → duration
end/writeInterval → output count
end/statistics duration → statistics window
deltaT/U/mesh → CFL estimate
```

## 14.2 物性

```text
rho + nu → mu
U + D + nu → Re
material + temperature → properties
```

## 14.3 几何

```text
geometry → mesh → patches → BC → measurements
```

## 14.4 观测

```text
Cd/Cl → forceCoeffs
probe → probes
section mean → surfaceFieldValue
average/RMS → fieldAverage
frequency → time series + FFT
```

每个依赖修改后检查：

```text
VALID
NEEDS_RECOMPUTE
NEEDS_RECOMPILE
NEEDS_REVIEW
BLOCKED
```

---

# 15. CaseIR 测试

必须区分：

```text
cylinder
triangle
rectangle
sine_bump
cosine_bell
trapezoid
polygon
airfoil
imported STL
unknown parametric geometry
```

## 15.1 几何身份

不同几何：

- kind 不同；
-fingerprint 不同；
-参数不同；
-不能共享同一个模板输出。

## 15.2 关系

支持：

```text
attached_to
aligned_below
aligned_above
upstream_of
downstream_of
centered_in
distance_to
```

## 15.3 Unknown Capability

测试：

- superellipse；
-FSI；
-combustion；
-multiphase；
-dynamic mesh；
-MRF。

必须输出能力状态，不得进入 compiler。

---

# 16. OpenFOAM 方案和静态编译测试

虽然工作站停机，但必须生成可审查制品。

## 16.1 编译场景

至少静态编译：

1. 2D cylinder；
2. cylinder + triangle；
3. cylinder + rectangle；
4. cylinder + sine bump；
5. cavity；
6. backward step；
7. periodic channel；
8. natural convection；
9. 3D periodic cylinder；
10. twin cylinder；
11. moving wall；
12. time-varying inlet。

## 16.2 时间参数映射

检查：

```text
startTime
endTime
deltaT
adjustTimeStep
maxCo
maxDeltaT
writeControl
writeInterval
```

## 16.3 Boundary 跨字段一致性

检查每个 patch 在：

```text
U
p / p_rgh
T
turbulence fields
```

中的边界是否一致。

## 16.4 MeasurementPlan

检查生成：

```text
forces/forceCoeffs
probes
surfaceFieldValue
fieldAverage
vorticity
wallShearStress
yPlus
sampling
```

## 16.5 Dictionary 静态验证

必须检查：

- 括号；
-分号；
-dictionary 结构；
-重复 key；
-非法字段；
-dimensions；
-patch 引用；
-include；
-required files。

## 16.6 跨文件一致性

```text
mesh patches ↔ field boundaries
solver ↔ required fields
buoyancy ↔ p_rgh/g
turbulence ↔ wall fields
function objects ↔ patch names
probe coordinates ↔ domain
statistics windows ↔ endTime
```

---

# 17. 编译器属性测试

## 17.1 Property-based

随机生成合法：

- 时间；
-单位；
-domain；
-entity；
-probe；
-boundary name；
-observation。

验证：

- 不抛未处理异常；
-文件可解析；
-patch 全覆盖；
-function object 引用有效；
-digest 可重现。

## 17.2 Metamorphic

### 只改 endTime

只应影响：

```text
time control
manifest
digest
```

不应影响 geometry/material。

### triangle→rectangle

只应影响 geometry/mesh 相关内容。

### 添加 probe

只应影响 measurement/function objects/postprocess。

## 17.3 Mutation

故意注入：

- endTime 写错；
-triangle 变 cosine_bell；
-忽略 material dependency；
-forceCoeffs 被删除；
-unknown 标为 available；
-Patch 覆盖整个 spec；
-fake fallback 生效。

测试必须能抓住这些变异。

---

# 18. UI 和交互测试

## UI-001：理解结果

显示：

- 模型理解到的事实；
-缺失项；
-歧义；
-冲突；
-模型建议；
-Skill。

## UI-002：方案展示

显示：

- 几何；
-边界；
-材料；
-数值；
-指标；
-验证；
-能力缺口。

## UI-003：修改差异

显示：

```text
旧值
新值
修改来源
受影响内容
是否需重新确认
```

## UI-004：澄清按钮

选项必须对应结构化 Patch。

## UI-005：确认 Gate

用户未确认前：

- 不生成最终 compiled artifact；
-不进入下一阶段。

## UI-006：方案和编译预览双向切换

用户可返回修改，不丢状态。

## UI-007：刷新持久化

刷新后保留：

- 输入；
-会话；
-spec；
-Patch；
-confirmation；
-compiled preview；
-Skill trace。

## UI-008：停机状态

最终显示：

```text
方案和编译制品已准备完成
工作站正在检修
恢复后可继续验证和运行
```

不得显示运行中或完成。

---

# 19. 代码走查清单

## 19.1 Model

- 是否实际调用强模型；
-是否记录 actual model；
-是否传完整 spec；
-是否传历史；
-是否加载 Skill；
-是否 structured output；
-是否静默 fallback。

## 19.2 Skill

- 是否阶段路由；
-是否每轮全量加载；
-reference 是否有限；
-hash 是否记录；
-script 是否禁用；
-path traversal。

## 19.3 Session

- active spec；
-version；
-pending patch；
-idempotency；
-concurrency；
-summary；
跨 session 污染。

## 19.4 Patch

- path；
-type；
-unit；
-relative expression；
-atomicity；
-no-op；
-undo；
-confirmation。

## 19.5 Dependency

- stale derived value；
-cycle；
-invalidation；
-material/Re；
-time/window；
-geometry/mesh；
-measurement。

## 19.6 CaseIR

- open world；
-original semantics；
-relations；
-unknown；
-capability evidence。

## 19.7 Compiler

- deterministic；
-no model-generated arbitrary text execution；
-version aware；
-cross-file consistency；
-reproducible archive。

## 19.8 Frontend

- 是否只改 local state；
-是否以后端为事实源；
-确认 race；
-error；
-refresh；
-current active spec。

---

# 20. 停机阶段故障测试

## FAIL-001：模型超时

不得生成方案版本。

## FAIL-002：模型输出无效 JSON

有限重试，无部分写入。

## FAIL-003：Skill 缺失

核心阶段阻断。

## FAIL-004：reference 缺失

Invocation 失败，不能忽略。

## FAIL-005：过期 Patch

版本冲突。

## FAIL-006：Schema 不支持字段

输出 capability/schema extension，不丢字段。

## FAIL-007：Compiler 不支持 geometry

阻断在 capability/compiler，不模板降级。

## FAIL-008：Dictionary 静态错误

编译失败，不生成 READY 制品。

## FAIL-009：数据库短暂失败

操作幂等，恢复后不重复版本。

## FAIL-010：工作站停机

正确保存：

```text
ENVIRONMENT_BLOCKED
```

无 external job ID。

---

# 21. 本阶段测试数量要求

至少完成：

- 80 个仿真前场景；
- 10类意图；
- 8类歧义；
- 6类冲突；
- 10类方案设计；
- 20类参数修改；
- 10轮和50轮会话；
- 10类 CaseIR 几何；
- 6类 Unknown Capability；
- 12类静态编译；
- 10个故障；
- 8个 UI 测试；
- 属性、变形和变异测试；
- 完整代码走查。

---

# 22. 第一轮优先执行顺序

## 批次 1：意图、模型和 Skill

```text
INTENT-001 到 INTENT-010
FACT-001 到 FACT-006
AMB-001 到 AMB-008
CONFLICT-001 到 CONFLICT-006
```

## 批次 2：方案与编辑

```text
PLAN-001 到 PLAN-006
EDIT-001 到 EDIT-010
SESSION-10
```

## 批次 3：Dependency、CaseIR、Capability

```text
所有 Dependency 测试
所有 Geometry 区分
所有 Unknown Capability
```

## 批次 4：静态编译和 UI

```text
12个编译场景
Dictionary/跨文件校验
UI-001 到 UI-008
FAIL-001 到 FAIL-010
```

## 批次 5：代码质量

```text
property-based
metamorphic
mutation
full code review
50轮会话
```

---

# 23. 每个 Bug 的修复流程

```text
保存失败证据
→ 标记首个错误层
→ 写最小复现
→ 判断同类问题
→ 通用修复
→ 同类回归
→ 核心回归
→ 更新能力边界
```

Bug 文档：

```markdown
# BUG-XXXX

## 场景
## 预期
## 实际
## 首个错误层
## 根因
## 通用修复
## 受益场景
## 新增测试
## 回归结果
## Commit
```

---

# 24. 最终报告

生成：

```text
PRE_EXPERIMENT_CAPABILITY_BOUNDARY_REPORT.md
```

必须包含：

- 已支持意图；
-已支持修改；
-需澄清表达；
-冲突处理；
-方案设计能力；
-CaseIR 覆盖；
-Unknown Capability；
-静态编译覆盖；
-Skill 使用情况；
-模型失败情况；
-代码走查发现；
-已修复 Bug；
-未解决边界；
-工作站恢复后待执行队列。

另生成：

```text
WORKSTATION_RECOVERY_QUEUE.md
```

记录：

```text
scenario
spec version
artifact ID
archive digest
required OpenFOAM version
expected checkMesh/solver/postprocess
```

---

# 25. 直接交给 Trae 的执行指令

```text
当前 OpenFOAM 工作站停机检修。本阶段不要等待工作站，也不要伪造实验、运行日志、
结果、云图、Cd、Cl 或 run ID。

你要重点完成“实验执行之前”的全部智能体链路：

用户意图 → Skill 路由 → 真实模型理解 → 显式事实 → 歧义和冲突 →
研究方案设计 → 多轮修改 → SimulationSpecPatch → Dependency/Physics Review →
用户确认 → Canonical SimulationStudySpec → CaseIR → Capability Resolution →
OpenFOAM Foundation 13 Case Blueprint → 确定性静态编译 →
Dictionary和跨文件一致性验证 → 编译预览 → archive/manifest/digest →
READY_FOR_WORKSTATION / ENVIRONMENT_BLOCKED。

重点测试和修复：
1. 新建、修改、删除、撤销、确认、询问、复制方案和新研究的意图区分；
2. 时间、材料、几何、边界、网格、数值和观测参数的通用修改；
3. endTime/duration、正中央、自由出流、平均速度等歧义；
4. Re矛盾、边界冲突、稳态与频率等冲突；
5. 圆柱、三角、矩形、正弦、余弦、梯形、polygon、airfoil和STL；
6. superellipse、FSI、燃烧、多相、动态网格等Unknown Capability；
7. 十轮累积编辑和50轮长会话；
8. Dependency Engine；
9. MeasurementPlan和function objects；
10. 12类OpenFOAM case的静态编译；
11. Property-based、metamorphic和mutation tests；
12. UI理解、澄清、diff、确认、持久化和停机状态；
13. 当前代码的双主链、legacy、fake、fallback、模板通吃和死代码走查。

禁止为测试句子添加关键词、正则、case ID 或专用 if/else。每个失败必须定位首个
错误层，并做通用修复和同类回归。

工作站停机时最终状态必须是 READY_FOR_WORKSTATION 或 ENVIRONMENT_BLOCKED，
不得显示 RUNNING、COMPLETED、RESULTS_READY 或 RESEARCH_READY。

最终生成：
PRE_EXPERIMENT_CAPABILITY_BOUNDARY_REPORT.md
WORKSTATION_RECOVERY_QUEUE.md
完整 Bug 清单、修复 commit 和静态验证 artifact。
```
