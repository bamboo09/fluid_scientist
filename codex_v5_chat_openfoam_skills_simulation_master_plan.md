# Fluid Scientist V5 对话式工作台与 OpenFOAM 全闭环重构任务书（Codex 主导，修订版）

> **本文件替代上一版总任务书。**  
> 本轮不允许 Codex 自己猜“当前版本”或在任意 V5 页面上继续开发。必须先找到并锁定与参考截图一致的**对话式科研工作台版本**，再在该版本上完成开放世界理解、外部 OpenFOAM Skill 接入、工作站仿真、工作站后处理、结果分析、静态图与动图输出的全部闭环。

![目标对话式工作台参考图](./chat_workbench_reference.png)

---

# 1. 本轮三个不可偏离的目标

## 1.1 必须以参考图中的 Chat 工作台作为 UI 和交互基线

目标不是旧式表单页，也不是重新设计一个新页面，而是保留并完善参考图中的布局：

- 左侧：研究任务 / session 列表、新建任务、工作站状态；
- 中间：用户与 Fluid Scientist 的多轮对话；
- 中间系统消息：系统理解结果、派生参数、默认假设、审计分类、模型调用与 Skill 运行证据；
- 底部：自然语言输入框，支持继续补充、修改、澄清；
- 右侧：与当前会话实时同步的“研究方案”工作台；
- 右侧分区：研究目标、计算域、几何实体、流体属性、流动拓扑、边界条件、仿真参数、观测量、分析目标；
- 顶部：当前模型、执行平台、workflow 版本和 commit；
- 状态：`NEEDS_CLARIFICATION`、待确认、编译中、Smoke、运行中、后处理、分析完成等；
- 用户通过对话修改方案，而不是在表单里直接随意改最终 Schema；
- 方案、编译、运行日志、结果、报告能够在右侧区域切换，关闭弹窗后不丢失。

Codex 必须在这个 Chat 版本上改造，禁止退回旧表单、旧弹窗结果页或另起一套不相关 UI。

## 1.2 必须系统性接入可复用的 OpenFOAM Skills / Agent 能力

不能继续把 Skill 做成“函数调用包装器 + PASSED 日志”。

本轮必须：

1. 调研并审计已有开源 OpenFOAM Agent、Skill、MCP、RAG、执行、纠错和后处理项目；
2. 对许可证、OpenFOAM 版本、维护状态、安全性、依赖和代码质量做准入审核；
3. 可直接复用的，使用依赖、插件、MCP sidecar 或适配器接入；
4. 不适合直接复制的，提取其架构、Prompt、测试集、知识库构建和工具设计；
5. 闭源或商业能力只能通过合法授权的 API、SDK、MCP、插件或合同接入，禁止复制闭源代码；
6. 所有外部能力必须统一进入项目自己的 Skill Registry、版本治理、审计和回归测试；
7. 不得因外部项目目标 OpenFOAM v10、v12 或 v2406，就直接用于当前 Foundation v13。

## 1.3 必须真正跑完仿真、工作站后处理、分析、图片和动图

成功标准不是：

- 生成了 Case；
- `checkMesh` 通过；
- 页面出现一张旧图；
- 返回几个规则计算值。

成功标准必须是：

```text
用户需求
→ 方案确认
→ Case 生成
→ 工作站网格与验证
→ Smoke
→ 正式仿真
→ 工作站后处理
→ 数值指标提取
→ 物理可信性验证
→ 静态图
→ 动图
→ LLM 基于真实数据分析
→ 报告与全部产物持久化
→ 页面可重复打开
```

---

# 2. 第一阶段：先找到参考图对应的真实版本

Codex 当前不知道项目做到哪个版本，因此**不得直接从当前 HEAD 开始修改**。

## 2.1 保存参考图

把本文件旁边的：

```text
chat_workbench_reference.png
```

复制到仓库：

```text
docs/reference/chat_workbench_reference.png
```

该图片是本轮 UI 验收基线。

## 2.2 全仓库和全分支搜索 UI 指纹

参考图中可用于定位版本的文字包括但不限于：

```text
Fluid Scientist
V5 对话式科研工作台
研究任务
研究方案
系统理解结果
推导参数
默认假设
审计分类
大模型调用
Skill执行
工作站配置
execution target is unavailable
NEEDS_CLARIFICATION
```

必须在当前工作区和所有相关分支中搜索：

```bash
git grep -n "V5 对话式科研工作台"
git grep -n "系统理解结果"
git grep -n "大模型调用"
git grep -n "Skill执行"
git grep -n "execution target is unavailable"
```

对其他分支：

```bash
git grep -n "系统理解结果" <branch-name>
```

重点调查：

```text
feature/v5-study-decomposer-draft-workflow
codex/v5-dialogue-draft-mainline
integration/trae-codex
codex/v5-integrated-unknown-capability
trae/*
codex/*
所有包含 v5-app.js、cylinder-flow.js、chat、draft-viewer、workbench 的分支
```

## 2.3 对候选版本逐个启动和截图

不能只看文件名判断。

对每个候选 commit 建独立 worktree，分别启动在不同端口：

```text
candidate A → 8101
candidate B → 8102
candidate C → 8103
```

使用 Playwright 或现有浏览器 E2E：

1. 打开页面；
2. 新建研究任务；
3. 输入一个圆柱绕流问题；
4. 截取全屏；
5. 保存 DOM 结构、网络请求和 runtime-info。

输出：

```text
docs/audits/chat_ui_candidate_comparison.md
docs/audits/screenshots/<branch>_<commit>.png
```

比较：

- 三栏布局；
- 中间是否真正是多轮 Chat；
- 右侧是否是研究方案；
- 状态和派生信息位置；
- 顶部模型 / 平台 / commit；
- 工作站状态；
- API 调用链。

## 2.4 选择 UI Baseline

只允许选择与参考图最接近、且核心交互可运行的 commit。

若 UI 和后端最佳实现在不同分支：

- 以 Chat UI 版本为界面基线；
- 通过小粒度 cherry-pick 或手工移植接入更好的后端能力；
- 禁止为了保留后端而退回旧 UI。

输出：

```text
docs/audits/chat_ui_baseline_decision.md
```

必须记录：

```text
branch
commit
worktree
前端入口
后端入口
页面实际 API
与参考图差异
需要迁移的其他提交
```

## 2.5 建立唯一新分支

建议：

```bash
git switch <chat-ui-baseline>
git switch -c codex/v5-chat-openfoam-closed-loop
git worktree add ../fluid_scientist_codex_chat codex/v5-chat-openfoam-closed-loop
```

之后所有工作只能在该 worktree 进行。

---

# 3. 分支、worktree 和运行版本治理

## 3.1 保存所有脏工作区

不得丢弃 Trae 或 Codex 现有改动。

对每个 dirty worktree：

```bash
git diff > docs/audits/snapshots/<name>_unstaged.diff
git diff --staged > docs/audits/snapshots/<name>_staged.diff
git ls-files --others --exclude-standard > docs/audits/snapshots/<name>_untracked.txt
```

必要时建立：

```text
archive/trae-dirty-snapshot-<date>
archive/codex-dirty-snapshot-<date>
```

## 3.2 运行时指纹

新增并在页面顶部显示：

```json
{
  "repo_root": "...",
  "worktree": "...",
  "branch": "...",
  "commit": "...",
  "dirty": false,
  "source_hash": "...",
  "prompt_bundle_hash": "...",
  "skill_bundle_hash": "...",
  "compiler_version": "...",
  "openfoam_distribution": "foundation",
  "openfoam_version": "13"
}
```

端点：

```text
GET /api/v5/runtime-info
```

每个 session、spec、case、job、postprocess 和 report 都保存该指纹。

## 3.3 禁止混乱运行

最终验收时：

- 只有一个后端进程；
- 只有一个前端目标；
- 页面 API 指向当前 worktree；
- Git 必须 clean；
- 页面顶部 commit 与 `git rev-parse HEAD` 一致；
- 禁止旧端口和旧进程继续提供页面。

---

# 4. 现有完成度重新审计

Codex 必须在 Chat UI baseline 上重新验证，不得沿用过去报告。

分类：

```text
VERIFIED_PRODUCTION
IMPLEMENTED_NOT_CONNECTED
PARTIAL
TEST_ONLY
MOCK_ONLY
DEAD_CODE
BROKEN
NOT_IMPLEMENTED
```

至少审计：

- Chat session 和 message；
- 多轮修改；
- 右侧 Dynamic Schema；
- 原始输入覆盖率；
- OpenWorld Research IR；
- Regex / LLM 候选；
- Conflict Resolver；
- Semantic Critic；
- Source Coverage Guard；
- Prompt Registry；
- Skill Registry；
- 外部 Skill 接入；
- Capability Planner；
- ExtensionOrchestrator；
- Case Compiler；
- 工作站；
- Smoke / Full Run；
- Repair Loop；
- Postprocess；
- ParaView；
- 图片；
- 动图；
- 数值分析；
- 物理验证；
- 报告；
- 持久化；
- 重启恢复。

输出：

```text
docs/audits/codex_chat_v5_current_state.md
```

---

# 5. Chat 工作台交互必须保持和完善

## 5.1 单一会话主线

每个研究任务对应：

```text
session
messages
current_research_ir
spec_versions
clarifications
confirmations
case_plan
job
postprocess_run
analysis_run
report
artifacts
```

用户继续输入：

```text
把矩形改成梯形
流体改为水
上边界改为切向应力
再分析壁面剪切
```

必须在同一 session 更新，不创建无关联流程。

## 5.2 中间 Chat 内容

系统回复必须按阶段输出，不应把所有日志粗暴堆在一张卡片中：

```text
1. 我理解到的研究问题
2. 显式输入
3. 推导参数
4. 检测到的冲突
5. 需要用户确认的问题
6. 使用的模型和 Skills
7. 方案修改摘要
8. 编译和运行进度
9. 错误诊断与修复
10. 结果摘要和分析
```

技术日志可折叠，默认不要淹没用户。

## 5.3 右侧方案是唯一正式方案来源

右侧必须直接渲染 Canonical Research IR。

禁止：

```text
中间摘要包含梯形
右侧正式方案没有梯形
```

任何显式需求未进入右侧方案时：

```text
SourceCoverageGuard = FAILED
```

确认按钮不可用。

## 5.4 右侧状态切换

至少支持：

```text
研究方案
变更记录
编译计划
运行进度
运行日志
结果
图像与动图
科学分析
最终报告
```

可来回切换，刷新和重启后可恢复。

---

# 6. 开放世界 Research IR

保留现有已验证 Schema 作为 Lowering 目标，但在上游加入统一开放语义层：

```text
OpenWorldResearchIR
```

核心列表：

```text
domain
geometry_entities[]
materials[]
boundaries[]
initial_conditions[]
physics_models[]
numerics
observables[]
spatial_relations[]
ambiguities[]
unresolved_mentions[]
capability_requirements[]
measurement_plan
```

每个字段包含：

```text
value
unit
raw_value
source_span
source_message_id
origin
confidence
confirmed
formula
dependencies
```

---

# 7. Mention Inventory 和 100% 覆盖率

用户输入在映射前先生成 Mention Inventory。

每个显式信息只能处于：

```text
MAPPED
DERIVED
AMBIGUOUS
UNSUPPORTED
NEEDS_CLARIFICATION
```

禁止：

```text
IGNORED
DROPPED
```

编译前：

```python
if unaccounted_mentions:
    block_confirmation_and_compile()
```

页面显示未处理内容。

---

# 8. Regex、LLM 与 Critic

保留 Regex，但不再让 Regex 直接占领最终 Spec。

```text
用户文本
├─ Regex Explicit Candidate
├─ LLM Open-World Candidate
└─ Mention Inventory
      ↓
Candidate Merger
      ↓
Semantic Critic
      ↓
Canonical Research IR
```

冲突时：

- 自动可解：给出证据和 resolution；
- 不可解：在 Chat 中提问；
- 不能固定 Regex 永远胜出；
- 不能固定 LLM 永远胜出。

---

# 9. 通用几何，而不是无限模板

统一表示：

```text
circle
ellipse
parametric_polygon
explicit_polygon
profile_function
csg
imported_mesh
implicit_surface
unknown
```

通用 Compiler：

```text
CircleGeometryCompiler
EllipseGeometryCompiler
PolygonGeometryCompiler
ProfileFunctionCompiler
ImportedMeshCompiler
CSGGeometryCompiler
```

以下共用 Polygon：

```text
triangle
rectangle
trapezoid
parallelogram
regular polygon
user provided vertices
```

梯形必须走：

```text
parametric_polygon → vertices → PolygonGeometryCompiler
```

禁止专用完整梯形 Case 模板。

---

# 10. 外部 OpenFOAM Skills / Agent 能力接入计划

## 10.1 不允许盲目复制

外部项目必须先建立：

```text
ExternalCapabilityIntake
```

包含：

```text
name
repository_or_vendor
license
version/tag/commit
maintenance_status
openfoam_target
language
dependencies
security_risks
data_privacy
candidate_components
integration_mode
acceptance_tests
decision
```

## 10.2 三种合法接入模式

### A. 依赖 / 代码复用

适合许可证兼容、版本可控、模块边界清晰的库。

要求：

- 锁定 commit/tag；
- 保存 LICENSE 和 NOTICE；
- SBOM；
- 依赖漏洞扫描；
- v13 回归测试；
- 明确修改记录。

### B. MCP / Sidecar / CLI Adapter

适合完整 Agent 项目或依赖较重的系统。

本项目调用其工具，但：

- 本项目的 Canonical Research IR 仍是事实源；
- 外部系统不能绕过用户确认；
- 外部输出必须经过本项目 Validator；
- 不能直接写生产 Case 目录；
- 在隔离 sandbox 生成候选产物；
- 通过 v13 验证后才能进入主流程。

### C. 设计与知识迁移

如果许可证、版本或架构不适合直接接入：

- 学习其 agent decomposition；
- 学习 Prompt；
- 学习 RAG 索引结构；
- 学习纠错循环；
- 学习测试集；
- 在本项目重新实现接口；
- 不复制不兼容或无授权代码。

## 10.3 闭源能力

闭源或商业 Skill 只能：

- 用户提供合法账户/许可；
- 通过公开 API、SDK、MCP 或插件；
- 明确数据传输范围；
- 不上传 API Key 到仓库；
- 不把用户算例、几何和结果发送到第三方，除非用户同意；
- 不逆向或复制闭源实现；
- 提供可关闭的 feature flag；
- 必须有本地替代路径或明确的 vendor lock-in 风险。

## 10.4 首批重点调研对象

### Foam-Agent

优先评估：

- MCP 工具链；
- `plan → input_writer → run → review → apply_fixes → visualization`；
- 分层 RAG；
- dependency-aware 文件生成；
- 自动纠错；
- ParaView / PyVista 可视化；
- `.claude/skills` 中的工作流 Skill；
- HPC 提交与结果处理。

注意：

- 其主要验证环境是 Foundation OpenFOAM v10；
- 当前工作站是 Foundation v13；
- 不可直接信任配置文件；
- 必须增加 v10→v13 Compatibility Adapter 和真实 v13 测试。

建议集成方式：

```text
先作为隔离 MCP/sidecar 评估
→ 选择性移植 RAG、review/fix、visualization 设计
→ 不让其替代本项目 UI 和 Canonical IR
```

### ChatCFD

重点评估：

- OpenFOAM tutorial 结构化知识库；
- solver / turbulence / boundary dependency tables；
- PyFoam 文件解析；
- `file_corrector`；
- 文献输入解析；
- 多轮 Chat 交互设计；
- 基础算例测试数据集。

注意：

- 主要目标是 OpenFOAM v2406，而非 Foundation v13；
- 知识库和字典命名可能不兼容；
- 先完成许可证审查；
- 适合知识和测试迁移，不应整套覆盖当前项目。

### openfoam-mcp-server

重点评估：

- MCP 工具定义；
- 网格质量与 STL 分析；
- error resolution；
- 参数提取；
- 教学式澄清策略。

注意：

- 项目自述 OpenFOAM 集成为 partial；
- 目标版本曾为 OpenFOAM 12；
- 只能按工具逐项验证。

### sim-cli

重点评估其通用运行时思想：

```text
check
connect
inspect
exec bounded step
verify
checkpoint
artifact
disconnect
```

可以借鉴：

- Skill 同步；
- solver plugin；
- 远程 runtime；
- checkpoint；
- bounded action；
- artifact manifest。

不能假设其已有可直接使用的 OpenFOAM v13 插件；Codex 必须实际确认。

### fluidfoam / PyFoam

用于：

- 读取 OpenFOAM 字段和后处理数据；
- 解析字典；
- 生成曲线；
- 自动化运行。

必须评估：

- Foundation v13 兼容性；
- GPL 等许可证对分发方式的影响；
- 作为可选依赖、sidecar 或内部部署的边界。

### OpenFOAM Foundation v13 官方教程和文档

这是最终源事实：

```text
solver
boundary condition
functionObjects
dictionary syntax
postprocess
foamPostProcess
ParaView
```

任何外部 Skill 与官方 v13 冲突时，以真实 v13 执行和官方资料为准。

## 10.5 外部 Skill 落地目录

```text
skills/
  registry.yaml
  external/
    <skill_id>/
      manifest.yaml
      LICENSE
      NOTICE
      adapter.py
      compatibility.md
      prompts/
      validators/
      tests/
```

## 10.6 Skill 必须真实产生业务影响

状态：

```text
DISCOVERED
LICENSE_APPROVED
SECURITY_APPROVED
VERSION_COMPATIBLE
REGISTERED
SELECTED
PROMPT_INJECTED
TOOL_CALLED
VALIDATOR_EXECUTED
OUTPUT_ACCEPTED
OPENFOAM_V13_VERIFIED
```

只有 `OPENFOAM_V13_VERIFIED` 才能进入默认生产路径。

---

# 11. RAG 和知识库

建立 v13 专属、多索引知识库：

```text
solver_index
boundary_index
turbulence_index
material_index
function_object_index
mesh_index
error_index
tutorial_case_index
postprocess_index
```

来源优先级：

1. Foundation v13 官方教程与源码；
2. 项目自身真实成功 Case；
3. 经过 v13 验证的外部 Skill / Case；
4. 学术论文和社区案例，标注版本与可信度。

每个文档必须保存：

```text
source
license
openfoam_distribution
openfoam_version
case_hash
validation_status
commands
expected_artifacts
```

RAG 检索结果必须进入模型 trace。

---

# 12. 能力规划和未知能力扩展

优先级：

```text
1. 已有通用能力
2. 配置组合
3. 经验证的外部 Skill
4. 通用组件扩展
5. 用户提供外部几何/网格
6. 受控代码扩展
```

未知能力不能消失。

接入：

```text
CapabilityPlanner
→ MissingCapability
→ checkpoint
→ ExtensionOrchestrator
→ 最小 patch
→ tests
→ OpenFOAM v13
→ VERIFIED
→ resume original task
```

---

# 13. 工作站真实仿真链

## 13.1 环境检查

在每次运行前执行并保存：

```bash
source /opt/openfoam13/etc/bashrc
foamVersion
which blockMesh
which snappyHexMesh
which checkMesh
which foamRun
which foamPostProcess
which pvbatch || true
which pvpython || true
which ffmpeg || true
```

生成：

```text
WorkstationCapabilityReport
```

## 13.2 Case 工作目录

每次 job 使用独立目录：

```text
runs/<session_id>/<spec_version>/<job_id>/
```

禁止复用旧目录。

目录含：

```text
input/
case/
logs/
postProcessing/
analysis/
visualization/
artifacts/
manifest.json
```

## 13.3 执行阶段

```text
PREPARE
STATIC_VALIDATE
BLOCKMESH
SNAPPY
CHECKMESH
SMOKE
WAITING_FULL_RUN_CONFIRMATION
FULL_RUN
POSTPROCESS
VISUALIZATION
ANALYSIS
REPORT
COMPLETED
```

每阶段写 event 和日志。

## 13.4 Smoke

Smoke 失败：

```text
禁止 Full Run
→ Error Classifier
→ LLM Diagnosis
→ Controlled Repair
→ 重跑受影响阶段
```

---

# 14. 仿真前就生成 Measurement Plan

用户提出的目标必须在运行前转成数据采集计划。

例如：

```text
Cd / Cl
→ forceCoeffs

St
→ Cl 时间序列 + 合理采样频率

涡街
→ U、p、vorticity 写出
→ 适当 writeInterval

壁面剪切
→ wallShearStress

截面平均速度
→ sampled surface + surfaceFieldValue

点压力
→ probes

平均场
→ fieldAverage
```

若 Measurement Plan 无法生成：

```text
阻断 Full Run
```

不能仿真结束后才发现没有数据。

---

# 15. 工作站后处理

后处理必须优先在工作站完成，避免下载完整大场数据。

## 15.1 PostprocessPlan

```python
class PostprocessPlan:
    numeric_extractors
    field_calculations
    sampling_tasks
    static_visualizations
    animation_tasks
    analysis_tasks
    artifact_policy
```

## 15.2 OpenFOAM 原生后处理

使用 Foundation v13 支持的：

```text
foamPostProcess
functionObjects
fieldAverage
vorticity
wallShearStress
forces
forceCoeffs
probes
surfaceFieldValue
sampling surfaces
streamlines
CourantNo
```

必须从真实 v13 能力列表检查，不凭记忆生成。

## 15.3 数值数据产物

至少输出：

```text
metrics.json
metrics.csv
time_series.csv
residuals.csv
force_coefficients.csv
sampling_results.csv
mesh_quality.json
solver_summary.json
```

大数据可使用 Parquet，但前端摘要仍用 JSON。

---

# 16. 工作站静态图输出

优先使用 headless ParaView：

```text
pvbatch
pvpython
offscreen rendering
```

每个可视化由版本化脚本生成，禁止人工临时点 GUI 才能得到。

圆柱绕流基线至少输出：

```text
velocity_magnitude.png
pressure.png
vorticity.png
streamlines.png
velocity_vectors.png
mesh_overview.png
near_body_mesh.png
cd_cl_history.png
cl_psd.png
residual_history.png
courant_history.png
```

依据用户目标动态选择，不要求所有场景固定同一套图。

每张图必须有：

```text
artifact_id
source_job_id
source_time_or_window
field
range
camera
script_hash
created_at
```

---

# 17. 工作站动图输出

## 17.1 动画任务

非定常流动至少支持：

```text
velocity animation
pressure animation
vorticity animation
streamline/pathline animation（适用时）
```

## 17.2 生成方式

推荐：

```text
ParaView Python script
→ PNG frames
→ ffmpeg
→ MP4 / WebM
→ 可选 GIF 预览
```

保存：

```text
frames/
animation.mp4
animation.webm
preview.gif
animation_manifest.json
paraview_state.pvsm
render_script.py
render.log
```

## 17.3 动画参数

由 `AnimationPlan` 控制：

```text
field
time_start
time_end
frame_stride
fps
camera
color_range_mode
fixed_or_dynamic_range
resolution
annotations
```

为科学对比，默认应使用固定色标，不应每帧自动缩放导致误导。

## 17.4 无 ParaView 时

若工作站没有 `pvbatch`：

1. WorkstationCapabilityReport 显示缺失；
2. 尝试经批准的安装或使用项目容器；
3. 或 `foamToVTK` 后由隔离可视化 sidecar 处理；
4. 仍不可用则明确标记 `VISUALIZATION_BLOCKED`；
5. 禁止返回旧动画或伪造视频。

---

# 18. 数值分析和物理可信性

## 18.1 基础分析

至少实现：

- 网格质量；
- residual；
- Courant；
- 质量守恒（适用时）；
- Cd/Cl 的均值、RMS、峰值；
- 去除初始过渡段；
- Welch PSD / FFT；
- 主频和 Strouhal；
- 信号采样长度与频率分辨率；
- 周期稳定性；
- 网格和时间步警告。

## 18.2 圆柱尾迹分析

适用时分析：

```text
vortex shedding frequency
Strouhal number
mean drag
lift RMS
wake symmetry
recirculation region
vorticity extrema
obstacle influence
```

不得仅依靠 LLM 看图猜测。

应先通过数值算法和场数据生成 `AnalysisEvidence`。

## 18.3 LLM 科学报告

LLM 只能读取：

```text
已确认 Research IR
真实 metrics
真实 analysis evidence
图片/动图 manifest
运行日志摘要
物理验证结果
warnings
missing data
```

禁止让模型编造未计算的结论。

报告必须区分：

```text
Observed
Computed
Derived
Inferred
Not Verified
```

---

# 19. 结果 UI

右侧“结果”区域至少包含：

## 19.1 摘要

- 运行状态；
- OpenFOAM 版本；
- 网格数量和质量；
- 模拟时间范围；
- Cd、Cl、St；
- 可信性状态；
- 警告。

## 19.2 曲线

- Cd / Cl；
- PSD；
- residual；
- Courant；
- 用户指定采样量。

## 19.3 流场图

缩略图和全屏查看。

## 19.4 动图

支持 MP4/WebM 播放，不要求用户下载后才看。

## 19.5 科学分析

按用户研究目标组织，而不是固定模板段落。

## 19.6 产物

可下载：

```text
case archive
logs
CSV
JSON
images
videos
ParaView state
render script
analysis report
```

---

# 20. 错误诊断和修复

错误分类至少包括：

```text
DICTIONARY_SYNTAX
MISSING_FILE
PATCH_MISMATCH
DIMENSION_MISMATCH
VERSION_MISMATCH
BLOCKMESH_FAILURE
SNAPPY_FAILURE
CHECKMESH_FAILURE
SOLVER_STARTUP_FAILURE
NUMERICAL_DIVERGENCE
COURANT_TOO_HIGH
POSTPROCESS_FAILURE
PARAVIEW_FAILURE
FFMPEG_FAILURE
MISSING_MEASUREMENT_DATA
```

修复分级：

```text
1. 确定性配置修复
2. LLM 结构化 RepairAction
3. 外部 Skill 工具
4. ExtensionOrchestrator 最小代码 patch
5. 用户干预
```

每轮修复必须有真实 diff。

---

# 21. 持久化

必须保存：

```text
sessions
messages
research_ir_versions
mention_inventory
candidate_sets
conflicts
clarifications
confirmations
skill_traces
external_skill_versions
capability_plans
case_plans
compiled_cases
jobs
job_events
repair_attempts
postprocess_runs
visualization_runs
analysis_runs
artifacts
reports
runtime_fingerprints
```

服务重启后可恢复。

---

# 22. 强制 E2E 测试

## A. Chat UI 基线

- 页面与参考图同一交互范式；
- 三栏布局；
- 对话修改；
- 右侧同步；
- commit 显示正确。

## B. 新几何

- rectangle；
- triangle；
- trapezoid；
- 任意五边形；
- 正弦壁面；
- unknown shape。

梯形和五边形共用 Polygon。

## C. 新流体和材料

- air；
- water；
- 用户给定 ρ/ν；
- 非牛顿；
- 缺属性。

## D. 新边界

- velocity inlet；
- pressure outlet；
- periodic；
- moving wall；
- shear stress；
- convective outlet；
- unknown custom。

## E. 指标

- Cd、Cl、St；
- pressure drop；
- wall shear；
- section average velocity；
- point pressure。

## F. 多轮修改

同 session：

```text
圆柱
→ 增加矩形
→ 改成梯形
→ 流体改水
→ 上边界切向应力
→ 增加壁面剪切
```

## G. 完整真实仿真

圆柱 + 壁面小障碍：

- 工作站；
- v13；
- mesh；
- smoke；
- full run；
- 数值指标；
- 至少 6 张静态图；
- 至少 1 个 MP4/WebM 动画；
- 科学报告。

## H. Repair

- patch mismatch；
- Courant 过高；
- ParaView 脚本错误。

必须真实修复。

## I. 外部 Skill

至少选两个外部能力完成从 intake 到 v13 verified：

1. 一个 Agent/RAG/error 工具；
2. 一个 postprocess/visualization 工具。

## J. 重启恢复

草案、运行中、后处理后分别重启。

---

# 23. 分阶段提交建议

```text
chore(repo): locate and freeze chat workbench baseline
chore(runtime): add runtime and UI fingerprint
docs(audit): record current chat v5 implementation
feat(ir): add open world research ir and mention coverage
feat(intent): separate regex and llm candidates
feat(intent): add semantic critic and conflict resolver
feat(prompts): load versioned production prompts
feat(ui): bind chat workbench to canonical research ir
feat(geometry): add generic polygon lowering
feat(material): add open material model
feat(boundary): add semantic boundary lowering
feat(metrics): add measurement plan
feat(skills): add external capability intake and adapters
feat(rag): add foundation v13 multi-index knowledge base
feat(capabilities): connect capability planner
feat(extensions): connect orchestrator to production
fix(execution): enforce smoke gate
feat(repair): add simulation and postprocess repair loop
feat(postprocess): execute measurement and analysis on workstation
feat(viz): add headless paraview image rendering
feat(viz): add animation and ffmpeg pipeline
feat(analysis): add physical validation and grounded report
feat(persistence): persist chat workflow and artifacts
test(e2e): add real glm openfoam postprocess visualization tests
docs(evidence): publish branch skill and simulation evidence
```

---

# 24. 最终交付物

```text
docs/audits/chat_ui_baseline_decision.md
docs/audits/codex_chat_v5_current_state.md
docs/architecture/open_world_research_ir.md
docs/architecture/external_openfoam_skill_intake.md
docs/architecture/openfoam_v13_rag.md
docs/architecture/workstation_simulation_pipeline.md
docs/architecture/workstation_postprocess_visualization.md
docs/architecture/openfoam_repair_loop.md
docs/tests/chat_ui_e2e.md
docs/tests/real_openfoam_v13_e2e.md
docs/tests/postprocess_and_animation_evidence.md
docs/tests/external_skill_compatibility_matrix.md
docs/integration/branch_worktree_commit_report.md
```

证据必须包含：

- 参考图对应 baseline；
- 最终页面截图；
- 全部 commit；
- 外部 Skill 来源、license、commit 和 v13 测试；
- Case 文件；
- OpenFOAM 日志；
- 后处理日志；
- PNG；
- MP4/WebM；
- metrics；
- AnalysisEvidence；
- 报告；
- 重启恢复证据。

---

# 25. 完成定义

只有同时满足以下条件才可结束：

1. Codex 找到了参考图对应的 Chat UI 版本并以其为 baseline；
2. 当前页面仍是参考图的对话式科研工作台，不是旧表单；
3. 用户的每个显式需求覆盖率为 100%；
4. 新几何、材料、边界和指标不会静默消失；
5. 外部 OpenFOAM Skill 有合法准入、版本锁定和 v13 验证；
6. Skill 真实影响 Prompt、工具、Validator、Compiler、Repair 或 Postprocess；
7. 工作站真实完成 Foundation v13 仿真；
8. Smoke 失败绝不继续 Full Run；
9. 工作站真实完成数值后处理；
10. 工作站或受控 sidecar 真实生成静态图和动图；
11. 页面可以查看曲线、流场图、动画和科学分析；
12. LLM 报告只基于真实数据和 Evidence；
13. 服务重启后流程和结果仍在；
14. 最终分支 clean、提交清晰、可审查和可合并；
15. 没有旧结果、mock、硬编码输入或模板堆叠冒充完成。

最终目标：

> 在参考图的 Chat 工作台中，用户可以持续用自然语言设计和修改 CFD 研究；系统能够调用经过治理的 OpenFOAM Skills，忠实生成并验证算例，在工作站完成仿真、后处理、图片和动图，再以真实数据为依据完成科学分析和报告。
