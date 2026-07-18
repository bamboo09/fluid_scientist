# Fluid Scientist 测试检查器纠偏、根因修复与能力扩展施工方案

> **执行对象：Trae**
>
> **依据：** `PRE_EXPERIMENT_CAPABILITY_BOUNDARY_REPORT.md`、`WORKSTATION_RECOVERY_QUEUE.md`、`pre-experiment-test-report.html`
>
> **核心结论：** 当前报告证明了一部分 `cylinder_flow_2d` 路径能够工作，但不能据此得出“仿真前智能体全链路已基本跑通”。报告与检查器存在统计矛盾、语义误判、假通过和范围降级；代码仍存在模型上下文未使用、默认 Skill 为空、正则补丁继续扩张、固定场景 API 充当系统本体等根因。

---

# 1. 先停止使用当前 89% 作为产品结论

当前测试汇总必须撤回并重新生成，原因如下。

## 1.1 分类表与合计不一致

逐行相加后，分类表并不是：

```text
184 total / 152 pass / 21 fail / 11 skip
```

当前表格中不同类别的计数存在重叠、漏计或错误聚合。报告生成器必须基于唯一测试 ID 聚合，禁止手工填写合计。

新增：

```text
scripts/rebuild_test_report.py
```

输入只能是结构化测试结果，不得读取 Markdown 表格反推数据。

每个测试结果必须包含：

```json
{
  "test_id": "...",
  "suite": "...",
  "category": "...",
  "status": "PASS|FAIL|EXPECTED_CLARIFICATION|EXPECTED_CAPABILITY_BLOCK|UNVERIFIED|SKIP",
  "is_unique_case": true,
  "evidence_path": "...",
  "checker_version": "..."
}
```

报告必须分别展示：

```text
行为场景数
属性生成样例数
变异点数
代码走查项数
UI 浏览器案例数
```

这些不能全部混成一个“总测试数”。

## 1.2 SKIP 不能提高通过率

后台阶、周期通道、自然对流、三维圆柱和运动壁面被标记为 `cylinder_flow_2d` 的预期边界，但产品目标不是只支持该 endpoint。

产品级测试中：

- 系统正确生成 typed capability block：`EXPECTED_CAPABILITY_BLOCK`；
- 系统错误进入 cylinder endpoint 后拒绝：`FAIL_PRODUCT_ARCHITECTURE`；
- 测试根本没执行：`SKIP`。

不得把“不支持”解释为“预期边界”后从失败中移除。

---

# 2. 检查器存在的关键误判

## 2.1 Unknown Capability 检查器过宽

报告把以下行为记为通过：

```text
superellipse → NEEDS_CLARIFICATION
combustion → NEEDS_CLARIFICATION
MRF → NEEDS_CLARIFICATION
```

但在 CaseIR 和方案设计章节中，又明确记录它们没有触发能力扩展。

正确通过条件必须同时满足：

```text
保留原始用户语义
输出 capability_key
输出 capability status
区分 CONFIG_EXTENSION / CODE_EXTENSION / UNSUPPORTED
不给已知模板
生成 capability requirement 或 extension proposal
```

仅返回 `NEEDS_CLARIFICATION` 不算 Unknown Capability 通过。

## 2.2 歧义检查器可能只检查“存在 clarification”

报告中二选一问题显示“澄清选项数 1”，而“正中央”显示 9，“自由出流”显示 8。这说明统计字段可能在数 clarification record、字段数或全局选项，而不是检查语义正确性。

每个歧义测试必须验证：

```text
问题文本是否正确
候选数量是否正确
每个候选是否有结构化 Patch
候选之间是否互斥
受影响路径是否正确
用户选择后是否应用正确 Patch
```

## 2.3 UI 测试混入 API 测试

当前 UI 报告中：

```text
澄清按钮 → API 验证
方案/编译切换 → API 验证
刷新持久化 → 50轮会话无丢失
```

这些不能证明真实浏览器 UI 工作。

必须拆成：

```text
API_CONTRACT
FRONTEND_COMPONENT
BROWSER_E2E
PERSISTENCE_E2E
```

浏览器测试至少使用 Playwright 验证：

- DOM 中旧值/新值；
-按钮点击产生正确请求；
-刷新后恢复；
-前后切换；
-模型失败提示；
-多个 tab；
-停机状态；
-Skill 证据来自后端。

## 2.4 故障测试不能由代码审查代替

模型超时、缺 reference、数据库失败、过期 Patch 等必须使用 fault injection 真实触发。代码走查只能标记：

```text
IMPLEMENTATION_REVIEWED_NOT_EXECUTED
```

不能标记 PASS。

## 2.5 静态校验器存在假通过

报告同时声称：

```text
跨文件一致性 100% 通过
```

又警告：

```text
blockMeshDict patch 名称与 0/ boundaryField 不匹配
```

这两者不能同时成立。

以下必须是硬失败：

```text
mesh patch set != boundaryField patch set
functionObject patch 不存在
patch role 与字段 BC 不一致
```

只允许显式白名单差异，例如 `defaultFaces`，且必须说明来源。

## 2.6 Property 测试 100% 没有可信度

7 个变异只杀死 2 个，说明测试断言没有覆盖真正关键行为。变异杀死率只有 29% 时，不得宣称 Property/Metamorphic 充分。

完成标准：

```text
核心主链 mutation kill rate >= 85%
Patch/Dependency/Capability/Compiler >= 90%
```

所有存活变异必须逐项增加断言。

---

# 3. 报告和恢复队列中的直接矛盾

## 3.1 deltaT 与步数矛盾

报告不同位置出现：

```text
deltaT = 0.005 s
endTime = 10 s
estimated steps = 20000
```

但：

```text
10 / 0.005 = 2000
```

另一个编译预览又写：

```text
deltaT = 0.0005
steps = 20000
```

这说明至少有一个环节使用了不同事实源：

```text
Dependency Engine
Canonical Spec
Compiler
Preview
Recovery Queue generator
```

必须建立硬断言：

```python
estimated_steps == ceil((end_time - start_time) / delta_t)
```

恢复队列必须从 compiled manifest 自动生成，不能手写共同参数。

## 3.2 编译场景数量矛盾

摘要写 6/12 通过，但详细表只明确列出 5 个通过场景，并另外在恢复队列加入 `2D cylinder observables`。

必须通过 `artifact_id` 聚合真实编译产物，不得手工拼场景列表。

## 3.3 恢复队列不是可执行队列

大量字段仍为：

```text
spec_*
job_*
sha256:*
```

因此不能称为“恢复后直接执行”。

每个队列项必须有：

```text
真实 spec_id
真实 spec_version
真实 artifact_id
完整 archive sha256
真实 manifest path
唯一 solver command
预期 function objects
静态校验结果 hash
```

工作站提交前不能生成 external job ID。若 `job_*` 只是 artifact ID，应改名避免混淆。

## 3.4 solver 命令不唯一

队列出现：

```text
icoFoam/foamRun transient
```

又写共同 solver：

```text
foamRun -solver incompressibleFluid
```

Foundation 13 队列中每个场景必须只有一个已经静态验证的执行命令。不得使用斜杠列多个候选。

## 3.5 通用 archive 文件结构可疑

恢复队列对 triangle、rectangle、cosine bump、twin cylinder 都展示相同的：

```text
constant/triSurface/cylinder.stl
```

这不能证明障碍物几何真实进入 archive。

必须为每个 artifact 输出：

```text
geometry entities
geometry files
geometry fingerprint
mesh recipe fingerprint
file list + hash
```

如果多个不同场景的几何文件、mesh recipe 和 patch set 相同，应判定 `TEMPLATE_SWALLOWING`。

---

# 4. 当前最严重的代码根因

## P0-1：模型上下文可能根本没有被使用

`build_context()` 返回值被丢弃不是普通代码质量问题，而是主链阻断问题。

在修复之前，下列结论都不能被信任：

```text
模型获得完整 spec
模型获得完整历史
Skill 指导了修改
多轮语义由模型完成
```

修复要求：

```text
context object 必须进入 Model Client request
保存 context hash
测试 prompt 中包含 spec_id/version
测试 prompt 中包含 confirmed facts/conflicts
测试删除 context 后行为 Eval 必须失败
```

## P0-2：默认 Skill 列表为空

`_DEFAULT_SKILLS = []` 说明核心阶段可能没有强制 Skill，只在显式传入或 UI 标签中显示。

修复要求：

```text
phase → mandatory skills
mandatory skill 缺失 → BLOCKED
SkillInvocationRecord → prompt hash
测试 Skill 内容变异后模型输出变化
```

不能仅检查 Skill 文件存在或 Router 返回名字。

## P0-3：正则补丁仍在代替模型

报告列出的三个“已修复 Bug”全部是增加正则：

```text
计算15秒
米每秒
保持Re
```

这正是之前明确禁止的逐表达打补丁模式。

正确分工：

```text
模型 + Skill：理解语义和输出结构化事实/Patch
确定性代码：数字解析、单位换算、Schema校验
```

正则可以用于：

- 安全的数字格式正规化；
-单位 tokenizer；
-对模型输出做确定性校验。

正则不能决定：

- 用户意图；
-修改哪个字段；
-形状语义；
-约束优先级；
-是否保持 Re。

新增反作弊测试：

```text
禁用 regex extractor 后，强模型链仍能正确输出 Patch
替换同义表达后无需修改代码
源代码新增中文业务关键词时 CI 失败
```

## P0-4：`/api/v5/cylinder-flow` 仍然是系统入口

HTML 报告显示测试 API 是：

```text
/api/v5/cylinder-flow
```

因此后台阶、周期通道、自然对流和 3D 被跳过并非正常产品边界，而是系统仍然以场景 endpoint 为主链。

不能继续新增：

```text
backward_step pipeline
periodic_channel pipeline
natural_convection pipeline
cavity pipeline
```

这会再次形成模板爆炸。

应实现通用入口：

```text
POST /api/research-sessions/{session_id}/turns
```

通用链路：

```text
SimulationStudySpec
→ CaseIR
→ CapabilityRequirementGraph
→ 可组合 compiler adapters
```

场景差异由可组合能力表达：

```text
geometry adapter
physics adapter
boundary adapter
mesh adapter
measurement adapter
solver adapter
```

而不是“一种研究一个完整 pipeline”。

---

# 5. 必须修复的功能

## P0：报告可信性和提交安全

1. 修复 `build_context()` 并加入真实 prompt 断言。
2. 建立 phase mandatory Skill，不允许默认空列表。
3. 修复 patch 名称不一致，静态验证改成 hard fail。
4. 统一 deltaT、estimated steps、preview、manifest 和 recovery queue。
5. 修复 deterministic gzip：固定 mtime、uid/gid、uname/gname、文件排序和权限。
6. 重建测试汇总器，按唯一 ID 聚合。
7. 重写 Unknown Capability、ambiguity、UI 和 fault checker。
8. 提高 mutation kill rate。
9. 恢复队列移除所有占位符。
10. 明确唯一 Foundation 13 solver command。

## P1：通用智能体主链

1. 通用 research session endpoint。
2. `Project → Study → Variant → SpecVersion → Run` 数据模型。
3. `CREATE_VARIANT` 与 `CREATE_NEW_STUDY`。
4. 相对 Patch 表达式：

```json
{
  "expression": {
    "operator": "multiply",
    "path": "/numerics/time/delta_t",
    "factor": 0.5
  }
}
```

5. 跨维度和目标的 Physics Constraint Engine。
6. Open Geometry AST 与 typed Unknown Capability。
7. 时变入口 BoundaryFunction AST 和 Foundation 13 compiler adapter。
8. Measurement compiler：probes、wallShearStress、yPlus。

## P2：能力扩展

以下不能做成完整独立 pipeline，应做成可组合 adapter：

- backward-step geometry/topology adapter；
- periodic channel boundary/forcing adapter；
- natural convection physics/thermal adapter；
- 3D extrusion/spanwise/cyclic adapter；
- moving wall boundary adapter；
- polygon geometry adapter；
- airfoil generator/import adapter；
- STL import validator；
- parametric geometry extension contract。

---

# 6. 检查器重构标准

## 6.1 每个测试验证首层和末层

例如“时间改15秒”必须分别断言：

```text
模型原始输出正确
Skill 真正注入
Patch path 正确
无关字段未变
Spec version 增加
Dependency 影响正确
controlDict endTime 正确
manifest 引用新 version
```

不能只验证最终 API 出现 15。

## 6.2 检测规则覆盖

### Model-driven requirement

```text
model_request_id 非空
actual_model 非 unknown
prompt 包含 Skill hash
prompt 包含 spec version
raw model output 包含目标 operation
最终 Patch 与 model output 可追溯
```

### Unknown capability

```text
capability_key 非空
original_semantics 保存
status typed
no known-template fallback
extension proposal 存在
```

### Static compilation

```text
all dictionaries parse
patch sets equal
required fields complete
function objects reference valid patches
all derived time values consistent
artifact digest 100% reproducible
```

### UI

```text
真实浏览器 DOM
真实点击
真实刷新
真实 network response
不是 API 替代
```

---

# 7. 新的优先执行顺序

## 阶段 A：先让测试可信

- 修复报告聚合；
-修复 semantic checkers；
-修复 UI/fault 分类；
-提高 mutation kill rate；
-重新生成 baseline。

## 阶段 B：修 P0 主链

- context；
-Skill；
-model provenance；
-patch set validator；
-time consistency；
-deterministic archive。

## 阶段 C：去正则补丁化

- 模型负责语义；
-确定性 parser 只做单位数字；
-同义表达和未见表达行为 Eval。

## 阶段 D：通用架构

- general session endpoint；
-project/study/variant；
-relative patch；
-open CaseIR；
-typed capability graph。

## 阶段 E：编译能力

- time-varying inlet；
-probes；
-wallShearStress；
-yPlus；
-polygon/STL/airfoil capability。

---

# 8. 完成标准

- [ ] 报告总数可由原始唯一测试 ID 重算；
- [ ] 所有统计无矛盾；
- [ ] Unknown Capability 不再把 clarification 当通过；
- [ ] UI 通过真实浏览器测试；
- [ ] Fault 通过真实注入；
- [ ] patch 名称不匹配为 hard fail；
- [ ] deltaT 与 steps 全链一致；
- [ ] digest 50/50 及更大样本 100% 重现；
- [ ] mutation kill rate 达标；
- [ ] context 实际进入模型请求；
- [ ] mandatory Skill 实际进入 prompt；
- [ ] actual model 全链可追溯；
- [ ] 语义不再靠新增中文业务正则；
- [ ] general research session 主入口建立；
- [ ] CREATE_VARIANT / CREATE_NEW_STUDY；
- [ ] relative Patch；
- [ ] typed Unknown Capability；
- [ ] time-varying inlet 静态编译；
- [ ] probes / wallShearStress / yPlus；
- [ ] recovery queue 无占位符；
- [ ] 每个 artifact 有真实 immutable ID 和完整 SHA。

---

# 9. 直接交给 Trae 的指令

```text
当前报告不能直接作为“89%通过、全链基本跑通”的依据。先暂停继续扩充场景，优先修复
测试检查器和报告可信性。

第一，重建报告聚合。所有统计必须从唯一test_id的结构化结果生成，行为场景、属性样本、
变异点、代码走查项和UI案例分开统计。禁止手工填184/152/21/11。SKIP不得算成功。

第二，修复检查器假通过：
- Unknown Capability不能只看NEEDS_CLARIFICATION，必须有capability_key、typed status、
  original semantics和extension proposal；
- 歧义必须检查候选语义和对应Patch；
- UI必须真实Playwright浏览器验证，API测试不能算UI；
- 故障必须fault injection，代码审查不能算PASS；
- patch名称不一致必须静态验证失败；
- mutation kill rate不足85%不得宣称测试充分。

第三，修复主链P0：
- build_context()返回值必须真实进入Model Client；
- phase必须有mandatory Skills，不能_DEFAULT_SKILLS=[]；
- actual model、Skill hash、reference hash、spec version进入InvocationRecord；
- 修复gzip完全确定性；
- 统一deltaT、estimated steps、preview、manifest和recovery queue；
- recovery queue删除spec_*、job_*、sha256:*等占位符；
- 每个场景只有一个Foundation 13 solver命令。

第四，停止增加业务语义正则。报告中“计算15秒、米每秒、保持Re”的修复仍是逐句补丁。
模型+Skill负责语义和Patch，确定性代码只负责数字、单位和Schema校验。添加CI规则，业务
代码新增中文关键词驱动逻辑时失败。

第五，不能把后台阶、周期通道、自然对流、3D和移动壁面继续标为cylinder_flow_2d的
正常边界，也不能为它们分别造完整pipeline。建立通用research session入口、开放CaseIR、
CapabilityRequirementGraph，以及geometry/physics/boundary/mesh/measurement/solver adapters。

第六，完成CREATE_VARIANT、CREATE_NEW_STUDY、相对Patch、2D/3D冲突、polygon、airfoil、
STL、superellipse capability、时变入口、probes、wallShearStress和yPlus。

修复后重新运行全部测试，输出新的可信报告。没有解决统计矛盾、检查器假通过、模型上下文、
Skill注入、patch名称、时间参数矛盾和digest问题，不得将任何artifact标记为
READY_FOR_WORKSTATION。
```
