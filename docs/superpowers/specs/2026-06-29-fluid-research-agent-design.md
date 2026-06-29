# 流体力学科研智能体设计规格

日期：2026-06-29  
状态：待用户书面复核  
项目代号：`fluid-research-agent`

## 1. 目标与首发边界

建设一个单用户首发、可扩展为团队平台的流体力学科研智能体。系统从自然语言研究问题出发，完成研究规格结构化、文献证据检索、物理规则校验、实验设计、HPC/OpenFOAM 执行、可信性验证、实验结果分析与报告，并把可复用经验沉淀为经过测试和人工批准的 Skill。

首发采用垂直闭环路线：完整搭建 Web、API、编排、知识、任务、数据和 HPC 适配骨架，但只承诺真正跑通单相、不可压缩、稳态的 90° 弯管研究闭环。层流圆管作为解析/数值基准。暂不支持任意 CAD、多相流、燃烧、空化、LES/DNS、完全无人审批和自动论文投稿。

## 2. 用户与验收场景

首发是单用户科研工作台，不实现登录和完整 RBAC；数据库实体保留 `owner_id`、审批角色和审计字段，为后续多用户迁移留出边界。

主验收场景：用户提出“弯管曲率和 Reynolds 数如何影响压降及二次流”，系统形成 ResearchSpec，检索证据并设计 Pilot，经用户批准后，在 HPC 上通过 Slurm 运行 OpenFOAM，验证守恒、网格无关性和基准一致性，完成批量实验、结果分析与带证据链报告，最后在后台产生一个等待测试和人工批准的候选 Skill。

## 3. 总体架构

控制平面首发部署在独立工作站或服务器，并保持部署位置配置化；未来可迁移到平台专用服务节点。控制平面包含 Web、FastAPI、LangGraph、Celery、Redis、PostgreSQL、Qdrant 和 MinIO。

HPC 分为三个职责节点：

1. 数据节点负责大文件传输、下载源码和依赖、编译 OpenFOAM/工具、制品缓存、校验和归档。
2. Login 节点只负责上传小型清单、`sbatch` 提交、`squeue`/`sacct` 查询和 `scancel` 取消，不编译、不运行求解器、不承载长期服务。
3. Slurm 计算节点读取已批准的不可变制品，运行网格、`checkMesh`、求解和后处理，不临时下载或编译。

三类节点通过平台共享项目存储或显式受控同步交接 Case Manifest、模板快照、软件制品、作业目录和结果。存储路径与同步方式由环境配置决定，不能散落在业务代码中。

## 4. 仓库与组件边界

采用 Python monorepo，前端单独构建：

- `apps/web`：项目总览、研究规格、文献证据、实验设计、HPC 作业、实验结果分析与报告、设置。
- `apps/api`：FastAPI 路由、依赖注入、可替换的身份上下文接口、SSE/WebSocket 进度接口；首发身份上下文固定为本地单用户。
- `orchestration`：LangGraph 状态、节点、转换、人工中断、检查点和循环上限。
- `agents`：Interpreter、Retrieval Planner、Fluid Scientist、Numerical Expert、Experiment Designer、Simulation Supervisor、Results Analyst、Scientific Reviewer。
- `domain`：ResearchSpec、ExperimentPlan、CaseManifest、EvidencePackage、ValidationResult、AnalysisResult、Report 和审计事件。
- `knowledge`：OpenAlex、Crossref、Unpaywall、本地 PDF、GROBID、切分、Paper Card、混合检索和重排。
- `physics`：单位、无量纲数、YAML 规则、求解器能力、边界条件和经验关联式。
- `simulators/openfoam`：模板、渲染器、作业脚本、日志解析、指标提取和适配器契约。
- `execution`：Celery 任务、数据节点网关、Login/Slurm 网关、制品存储和恢复逻辑。
- `validation`：收敛、质量守恒、网格无关性、Richardson 外推、GCI 和基准验证。
- `analysis`：描述统计、置信区间、DOE、敏感性、异常点和可视化。
- `skill_pipeline`：基础 Skill、候选提炼、脱敏、测试、审批、版本化发布和回滚。
- `infra`：Docker Compose、数据库迁移、服务健康检查和部署示例。
- `tests`：单元、契约、状态机、集成、安全、端到端和 Skill 场景测试。

每个组件通过 Pydantic 模型或 Protocol 接口通信。Agent 不读取无关状态，也不能写入未授权字段。

## 5. 端到端工作流

主流程为：

```text
研究问题
→ ResearchSpec 与缺失项确认（Gate 1）
→ 文献证据包、物理假设与规则校验
→ Pilot 实验计划（Gate 2）
→ 数据节点发布不可变制品
→ Login 节点提交 Slurm
→ 计算节点运行 OpenFOAM
→ 结果回收与可信性验证
→ 批量实验和统计分析
→ Results Analyst 总结
→ Scientific Reviewer 审查（Gate 3）
→ 实验结果分析与报告
→ 后台候选 Skill 提炼、测试和人工发布
```

LangGraph 只表达科研决策和人工中断；Celery 管理耗时 I/O、重试和轮询；Slurm 管理 HPC 资源。任何服务重启后，系统从 PostgreSQL 检查点和外部 job ID 恢复，而不是重复提交作业。

## 6. OpenAI 模型与确定性计算边界

业务层通过统一 `LLMProvider` 使用 OpenAI Responses API 和结构化输出，不直接依赖模型名称。当前回退参考建议规划和审核默认使用 `gpt-5.5`，轻量抽取使用 `gpt-5.4-mini`；正式实现前必须再次以 OpenAI 官方文档核验，最终模型 ID 通过环境变量配置。

大模型负责问题理解、假设、检索规划、候选方案解释、故障高层分类、结果解释和科研审查。确定性程序负责单位换算、Reynolds/Dean/Courant 数、规则执行、DOE、统计量、守恒、GCI 和数值阈值。模型不能心算替代这些程序。

Results Analyst 是独立角色，位于验证之后、Scientific Reviewer 之前。它读取 AnalysisResult，而不是原始散乱文件；总结趋势、效应大小、置信区间、交互作用、异常和适用范围。每条陈述绑定算例、图表、统计结果或文献证据 ID，并标记为直接观察、统计推断、文献支持、模型外推或未验证假设。证据不足时输出补充实验建议。

## 7. 知识与证据

文献流程覆盖 OpenAlex 发现、Crossref 校正、Unpaywall 合法全文、本地 PDF、GROBID 解析、结构切分、Paper Card、Qdrant 混合检索和 Evidence Package。原始 PDF/TEI/截图进入 MinIO，元数据和审核状态进入 PostgreSQL，向量进入 Qdrant。

只有 `REVIEWED` 或 `PUBLISHED` 内容能参与高风险自动决策。每个证据字段保留文献 ID、页码、章节、原文位置、抽取模型、时间和置信度。低置信公式、表格和图中数字转人工复核。

物理规则采用 Git 版本化 YAML，包含规则 ID、严重级别、适用条件、确定性检查、失败动作、来源和版本。HARD 失败阻止继续；模型只能提出候选规则，不能直接修改正式规则库。

## 8. OpenFOAM、Slurm 与制品

OpenFOAMAdapter 实现统一 SimulatorAdapter 契约。首发提供层流圆管和 90° 弯管模板，禁止模型自由生成完整 case 或任意 Shell。Case Manifest 包含模板 Git 提交、OpenFOAM 版本、容器/模块或二进制摘要、几何、网格、物理、边界条件、数值配置、资源和预期输出。

数据节点的下载和编译任务记录来源 URL、版本、许可证提示、构建命令、环境、日志和 SHA-256。计算任务只引用已发布制品。SSH 工具只允许配置中的主机、用户、根路径和操作；Slurm 命令由参数化构造器生成，不接受自由文本命令。

首发 Pilot 为中心工况乘三套网格乘两个湍流模型。Pilot 未通过，不能提交批量实验。

## 9. 错误处理与恢复

错误分为基础设施、网格、数值和物理四类：

- 临时网络、Worker、文件系统和调度器错误使用带抖动的指数退避并保持幂等。
- 网格负体积、边界名错误等进入重新网格分支。
- 数值发散可执行至多两次受控修复，每次生成新配置版本和审计事件。
- 模型不适用、边界不合理或 HARD 规则失败转修改方案或人工审核，禁止原样重跑。

作业提交使用幂等键，外部 job ID 与内部 case version 一一绑定。轮询中断只恢复查询，不重新提交。超过计划修订、检索轮次、模型修订、单算例修复或总模型调用上限时转人工。

## 10. 安全与审计

OpenAI Key、SSH 私钥和数据库密码只从环境变量或 Secret Provider 读取，不写入 Git、数据库正文和普通日志。日志统一脱敏。所有模型 Action 通过 JSON Schema、权限、状态、参数范围、路径白名单和预算检查。

Case Manifest 批准后不可变；变更产生新版本和 diff。系统记录用户审批、模型版本、Prompt 版本、规则版本、模板提交、制品摘要、Slurm job ID、修复动作和结论证据。首发不提供删除 HPC 任意路径、`sudo`、许可证修改或任意网络请求能力。

## 11. Skill 沉淀

仓库内置 `fluid-research-workflow` Skill，指导其他 Codex 实例按本项目的科研边界、规则、HPC 工具和验证流程工作。Skill 不出现在科研控制台。

运行经验只在后台生成候选 Skill：从成功任务、失败诊断、受控修复和审批记录中提取可复用模式，去除密钥、用户名、主机名、绝对路径和敏感研究数据，补充适用范围、来源和反例。候选必须遵循 RED/GREEN/REFACTOR：先记录无该 Skill 时的失败场景，再验证加载 Skill 后通过，并检查新漏洞。只有人工批准后才生成 Git 版本；不能自动覆盖正式 Skill。每次发布可回滚且保留测试证据。

## 12. Web 信息架构

控制台导航只显示项目总览、研究规格、文献证据、实验设计、HPC 作业、实验结果分析与报告、设置。Skill 沉淀是后台治理能力，不出现在控制台。

项目总览以研究阶段为主线，优先显示当前 Gate、阻塞原因、资源预算、数据节点制品状态、Slurm 状态、可信性快照和证据覆盖。实验结果分析与报告页面同时展示确定性统计图表、Results Analyst 总结、证据映射、Reviewer 意见和最终导出。

## 13. 测试策略

- 单元测试覆盖单位、无量纲数、规则、DOE、守恒、GCI、日志解析和分析。
- Schema 与属性测试覆盖所有 Agent Action、ResearchSpec、Case Manifest 和状态不变量。
- 状态机测试覆盖主路径、三个 Gate、重试、恢复、循环上限和重复事件。
- 适配器契约测试使用 Fake OpenAI、SSH、Slurm、对象存储和 OpenFOAM。
- HPC 集成先运行小型层流圆管基准，再运行 90° 弯管 Pilot。
- Web 端到端测试覆盖创建研究、审批、观察进度、查看证据和导出报告。
- 安全测试覆盖命令注入、路径越界、密钥泄漏、非法状态跳转和未审批提交。
- Skill 测试保存无 Skill 的失败基线、加载 Skill 的通过结果和回归用例。

测试默认不依赖真实 OpenAI 或 HPC。带显式标记的集成测试才读取外部凭据和平台配置。

## 14. 首发验收标准

1. 完整跑通一个 90° 弯管研究闭环。
2. 服务重启、SSH 短暂中断和 Slurm 长时间排队后可恢复且不重复提交。
3. 非法物理参数、路径越界、非白名单命令和未审批提交均被阻止。
4. 报告中的每条主要结论可追溯至数据、规则或文献。
5. Results Analyst 不产生脱离 AnalysisResult 的数值陈述。
6. 后台可生成一个候选 Skill，但只有测试通过和人工批准后才能发布。
7. 无 HPC/OpenFOAM 时，Fake 模式可在 CI 中运行完整演示。

## 15. 交付与 GitHub

仓库命名为 `fluid-research-agent`，默认创建为私有仓库以保护研究配置和后续平台信息。提交中不包含真实密钥、主机名或内部路径。GitHub Actions 运行静态检查、单元测试、契约测试和 Fake 端到端测试；真实 HPC 集成由手动触发且在平台侧执行。

首发实现仍按纵向切片推进：骨架与 Fake 闭环、确定性科研内核、HPC 三节点适配、OpenFOAM 基准与弯管 Pilot、知识库、分析与报告、后台 Skill 沉淀、整体加固。每个阶段都保持可运行和可验证。
