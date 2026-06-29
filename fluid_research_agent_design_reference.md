# 流体力学科研智能体完整设计方案

> 文档用途：作为流体力学科研智能体项目的统一设计参考，供大模型、开发人员、科研人员和后续评测流程共同使用。  
> 建议保存位置：`docs/fluid_research_agent_design.md`  
> 当前建议版本：V1.0  
> 第一阶段目标：实现“自然语言输入 → 自动设计实验 → 调用 OpenFOAM 仿真 → 自动验证 → 统计分析 → 输出科研结论”的可信闭环。

---

## 1. 项目目标

构建一个面向流体力学研究的科研智能体。用户输入一段自然语言研究需求后，系统能够：

1. 理解研究问题；
2. 提取研究对象、变量、范围、目标和约束；
3. 检索相关论文、教材、标准、软件手册和历史算例；
4. 形成物理假设和数值模型方案；
5. 自主设计实验矩阵；
6. 调用 OpenFOAM、OLGA 或其他仿真软件；
7. 自动监控网格、收敛、守恒和运行状态；
8. 对失败算例进行分类和受控修复；
9. 完成网格无关性、时间步无关性、敏感性和不确定性分析；
10. 输出有证据链、适用范围和可信度说明的实验结论。

系统的最终目标不是生成“看起来像科研报告”的文本，而是形成一个：

> 能规划、能执行、能验证、能追溯、能复现的流体力学科研闭环。

---

## 2. 第一阶段研究边界

第一版不得直接定义为“通用流体力学科学家”。

### 2.1 第一阶段支持范围

优先支持：

- 单相流；
- 不可压缩流动；
- 圆管流动；
- 90° 弯管流动；
- 稳态层流；
- 稳态 RANS 湍流；
- 压降分析；
- 阻力系数分析；
- 速度剖面分析；
- 二次流分析；
- 网格无关性分析；
- 参数敏感性分析。

### 2.2 第一阶段暂不支持

暂不自动处理：

- 任意复杂 CAD 几何；
- 任意多相流模型；
- 燃烧；
- 空化；
- 非牛顿复杂流变；
- 高速可压缩流；
- 大涡模拟；
- 直接数值模拟；
- 完全无人审批；
- 直接生成并提交论文；
- 自动修改未知仿真软件配置；
- 无边界的多 Agent 自由协商。

### 2.3 推荐扩展顺序

```text
单相圆管
→ 单相弯管
→ 瞬态管流
→ 气液两相管流
→ 段塞流和流型分析
→ OLGA 系统级管流
→ 多保真代理模型
→ 流体世界模型
```

---

## 3. 核心设计原则

### 3.1 大模型不负责数值真值

大模型主要负责：

- 自然语言理解；
- 研究问题规划；
- 文献检索规划；
- 假设生成；
- 候选方案解释；
- 工具选择；
- 异常原因高层分析；
- 结论措辞；
- 科研审查。

确定性程序负责：

- 单位转换；
- Reynolds 数计算；
- Dean 数计算；
- Courant 数计算；
- GCI 计算；
- 守恒误差计算；
- Sobol 指数；
- 统计分析；
- 网格质量检查；
- 收敛判定；
- 参数范围检查。

### 3.2 仿真结束不等于科研可信

必须区分：

```text
程序运行完成
≠
数值收敛
≠
物理可信
≠
结论可推广
```

### 3.3 工作流必须可恢复

流体仿真可能运行数小时或数天，系统必须支持：

- 中断恢复；
- 状态持久化；
- HPC 排队；
- 异步任务；
- 超时；
- 重试；
- 人工审批；
- 结果追踪。

### 3.4 所有关键决策必须有证据

每个模型选择、边界条件、参数范围和结论都应绑定：

- 规则编号；
- 文献编号；
- 原始文献位置；
- 软件手册位置；
- 专家审批记录；
- 仿真结果编号。

### 3.5 先模板化，再开放化

第一阶段采用经过验证的几何和仿真模板。

禁止大模型一开始自由编写：

- 完整 OpenFOAM case；
- 任意 Shell 命令；
- 任意网格拓扑；
- 任意求解器配置；
- 任意 HPC 脚本。

---

## 4. 总体系统架构

```text
┌──────────────────────────────────────────────┐
│ 用户交互层                                   │
│ 研究问题、参数确认、审批、进度、报告         │
└──────────────────────┬───────────────────────┘
                       ↓
┌──────────────────────────────────────────────┐
│ 科研智能体编排层                             │
│ LangGraph / 状态机 / Agent 节点 / 人工中断   │
└──────────────┬────────────┬─────────────┬────┘
               ↓            ↓             ↓
┌──────────────────┐ ┌───────────────┐ ┌──────────────┐
│ 文献知识库       │ │ 物理知识与规则 │ │ 工具适配层   │
│ RAG / Paper Card │ │ 规则执行器      │ │ 仿真和分析   │
└──────────────────┘ └───────────────┘ └──────┬───────┘
                                              ↓
┌──────────────────────────────────────────────┐
│ 任务执行层                                   │
│ Celery / Temporal / Slurm / 容器 / Worker    │
└──────────────────────┬───────────────────────┘
                       ↓
┌──────────────────────────────────────────────┐
│ 数据、审计与复现层                           │
│ PostgreSQL / Qdrant / MinIO / Git / 日志     │
└──────────────────────────────────────────────┘
```

---

## 5. 推荐技术栈

| 模块 | 推荐方案 |
|---|---|
| 主推理模型 | GPT-5.5 |
| 轻量抽取模型 | GPT-5.4 mini |
| 本地大模型备选 | Qwen3.6 |
| API 服务 | FastAPI |
| 数据校验 | Pydantic |
| Agent 编排 | LangGraph |
| MVP 长任务 | Celery + Redis 或 RabbitMQ |
| 正式长任务 | Temporal |
| HPC 调度 | Slurm / slurmrestd |
| 本地容器 | Docker |
| HPC 容器 | Apptainer |
| 业务数据库 | PostgreSQL |
| 灵活字段 | PostgreSQL JSONB |
| 向量数据库 | Qdrant |
| 对象存储 | MinIO 或 S3 |
| 规则版本 | Git |
| 文献发现 | OpenAlex |
| DOI 校正 | Crossref |
| 开放全文 | Unpaywall |
| PDF 结构化 | GROBID |
| 本地向量模型 | BGE-M3 |
| 实验设计 | SALib、SciPy、BoTorch、Ax |
| 数据分析 | NumPy、pandas、SciPy、scikit-learn |
| 流场处理 | PyVista、VTK |
| 第一仿真软件 | OpenFOAM |
| 后续仿真软件 | OLGA、SU2 |

---

## 6. 模型选型与职责

## 6.1 主规划模型

推荐使用 GPT-5.5。

负责：

- 复杂研究问题理解；
- 研究假设设计；
- 数值模型候选生成；
- 多阶段实验规划；
- 文献冲突分析；
- 仿真失败的高层诊断；
- 最终科学审查。

推荐策略：

```yaml
planner:
  model: gpt-5.5
  reasoning_effort: medium
```

以下情况使用高推理：

- 初始完整研究计划；
- 多物理模型选择；
- 文献结论冲突；
- 仿真结果互相矛盾；
- 最终 Reviewer；
- 自适应实验设计。

## 6.2 轻量子任务模型

推荐使用 GPT-5.4 mini。

负责：

- 文献分类；
- Paper Card 初步抽取；
- 关键词扩展；
- 日志分类；
- 单位字段归一化；
- 格式整理；
- 简单查询重写；
- 标签生成。

## 6.3 本地模型

如存在数据保密、离线和成本限制，可部署 Qwen3.6。

推荐用法：

- 轻量分类；
- 内部文献抽取；
- 规则查询；
- 简单工具调用；
- 日志分类；
- 低风险报告整理。

不建议第一阶段用本地小模型承担复杂主规划。

## 6.4 向量模型

外部 API：

```text
text-embedding-3-large
```

本地部署：

```text
BGE-M3
```

## 6.5 模型统一接口

所有模型通过统一 Provider 调用，禁止业务代码直接绑定模型名称。

```python
class LLMProvider:
    def generate_structured(
        self,
        task_type: str,
        messages: list,
        output_schema: dict,
        reasoning_level: str
    ) -> dict:
        ...
```

示例配置：

```yaml
models:
  planner:
    provider: openai
    model: gpt-5.5
    reasoning: medium

  reviewer:
    provider: openai
    model: gpt-5.5
    reasoning: high

  extractor:
    provider: openai
    model: gpt-5.4-mini

  local_fallback:
    provider: vllm
    model: qwen3.6
```

---

## 7. 文献知识库构建

文献不应一篇一篇手动上传。

完整处理链：

```text
文献发现
→ 元数据抓取
→ DOI 去重
→ 合法全文获取
→ PDF 解析
→ 结构化切分
→ Paper Card 抽取
→ 向量化
→ 质量检查
→ 人工审核
→ 正式发布
```

---

## 8. 文献来源

## 8.1 OpenAlex

用途：

- 按关键词发现论文；
- 按年份筛选；
- 按作者、机构、期刊筛选；
- 获取摘要；
- 获取引用网络；
- 获取相似论文；
- 获取开放获取状态。

建议检索主题：

```text
internal pipe flow pressure drop
curved pipe secondary flow
Dean vortex CFD
multiphase pipe slug flow
OpenFOAM pipe flow validation
mesh independence CFD
turbulence model curved pipe
```

## 8.2 Crossref

用途：

- DOI 校正；
- 出版信息校正；
- 期刊、卷期和作者补全；
- 检查勘误和撤稿；
- 许可元数据补充。

## 8.3 Unpaywall

用途：

- 根据 DOI 查询合法开放全文；
- 获取开放 PDF 地址；
- 区分出版社版本和作者存档版本。

## 8.4 本地文献目录

建议目录：

```text
knowledge_sources/
├── textbooks/
├── papers/
├── standards/
├── openfoam_manuals/
├── olga_manuals/
├── advisor_papers/
├── internal_reports/
└── benchmark_cases/
```

系统定期扫描目录，自动发现新文件。

## 8.5 Zotero 接入

可选方式：

- 读取 Zotero 导出的 DOI；
- 按 Collection 批量导入；
- 读取合法本地附件；
- 保留 Zotero Item ID；
- 用 Zotero Collection 作为知识库标签。

---

## 9. 文献采集范围

第一阶段不建议收集整个流体力学领域。

推荐初始规模：

| 类型 | 建议数量 |
|---|---:|
| 流体力学教材 | 5～15 本 |
| CFD 教材 | 5～10 本 |
| OpenFOAM 相关文档 | 全部相关章节 |
| 目标领域综述 | 30～50 篇 |
| 管流和弯管论文 | 200～500 篇 |
| 基准实验论文 | 30～80 篇 |
| 导师课题组论文 | 全部 |
| 已验证算例 | 10～30 个 |

优先建设一个深而可信的子领域知识库，而不是大而杂的论文集合。

---

## 10. PDF 自动解析

推荐使用 GROBID 将 PDF 转换为 TEI XML。

需要识别：

- 标题；
- 作者；
- 摘要；
- 章节；
- 段落；
- 公式附近文本；
- 表格标题；
- 图注；
- 参考文献；
- 文内引用。

处理流程：

```text
PDF
→ 可读性检查
→ GROBID
→ TEI XML
→ 清洗页眉页脚
→ 章节和段落结构
→ 公式、图表和引用绑定
→ 质量评分
```

扫描型 PDF：

```text
扫描 PDF
→ OCR
→ GROBID
→ 低置信页视觉复核
```

### 10.1 文献解析质量分数

```json
{
  "text_coverage": 0.97,
  "section_detection": 0.91,
  "reference_matching": 0.95,
  "formula_quality": 0.62,
  "table_quality": 0.48,
  "requires_visual_pass": true
}
```

### 10.2 低置信内容处理

以下内容必须谨慎：

- 经验关联式；
- 复杂公式；
- 上下标；
- 表格数字；
- 图中数据；
- 单位；
- 符号定义。

处理原则：

1. 正文主要由 GROBID 解析；
2. 关键公式和表格保留页面坐标；
3. 低置信度页面使用视觉模型复核；
4. 原图和抽取结果必须绑定；
5. 未审核公式不得进入正式物理规则库。

---

## 11. 文献切分策略

禁止只按固定 token 切分。

推荐结构：

```text
Document
├── Section
│   ├── Subsection
│   │   ├── Paragraph Group
│   │   ├── Equation Block
│   │   ├── Table Block
│   │   └── Figure Caption
```

切块类型：

- `background`
- `equation`
- `physical_mechanism`
- `experimental_setup`
- `numerical_setup`
- `validation`
- `result`
- `limitation`
- `conclusion`

切块结构：

```json
{
  "chunk_id": "paper_013_sec_3_2_chunk_4",
  "paper_id": "doi:10.xxxx/xxxx",
  "section": "3.2 Numerical setup",
  "chunk_type": "numerical_setup",
  "text": "...",
  "page_start": 6,
  "page_end": 7,
  "parent_section_id": "paper_013_sec_3_2",
  "citation_context": ["ref_12", "ref_18"],
  "entities": [
    "k-omega SST",
    "Reynolds number",
    "curved pipe"
  ]
}
```

---

## 12. Paper Card

每篇论文生成结构化文献卡片。

```json
{
  "paper_id": "doi:...",
  "problem": "90 degree curved pipe turbulent flow",
  "geometry": {
    "type": "90_degree_bend",
    "diameter_m": 0.2,
    "curvature_ratio": [1.0, 5.0]
  },
  "fluid": {
    "name": "water",
    "temperature_K": 293.15
  },
  "regime": {
    "reynolds_number": [10000, 100000],
    "phase": "single_phase",
    "compressibility": "incompressible"
  },
  "numerics": {
    "software": "OpenFOAM",
    "solver": "simpleFoam",
    "turbulence_models": ["kOmegaSST"],
    "mesh_count": 1300000
  },
  "boundary_conditions": [],
  "validation_data": [],
  "metrics": [
    "pressure_drop",
    "secondary_flow_intensity"
  ],
  "main_findings": [],
  "limitations": [],
  "evidence_spans": []
}
```

每个抽取字段必须绑定：

- 文献 ID；
- 页码；
- 章节；
- 原文片段；
- 模型版本；
- 抽取时间；
- 置信度。

没有来源定位的信息不能作为高可信决策依据。

---

## 13. 文献数据存储

## 13.1 PostgreSQL

存储：

- 论文元数据；
- DOI；
- 作者；
- 期刊；
- 年份；
- Paper Card；
- 处理状态；
- 引用关系；
- 文献质量等级；
- 审核状态；
- 用户权限。

## 13.2 Qdrant

存储：

- chunk 向量；
- chunk 元数据；
- 稀疏向量；
- 稠密向量；
- 文献标签；
- 过滤字段。

推荐检索：

```text
Dense Retrieval
+
Sparse Retrieval
+
Metadata Filter
+
Reranker
```

元数据过滤字段：

- 几何类型；
- 流体类型；
- 相态；
- Reynolds 数范围；
- 软件；
- 求解器；
- 湍流模型；
- 年份；
- 期刊；
- 是否实验验证；
- 文献质量等级。

## 13.3 MinIO 或 S3

存储：

- 原始 PDF；
- TEI XML；
- OCR 文件；
- 页面截图；
- 图表；
- 公式图片；
- OpenFOAM case；
- 网格；
- 仿真结果；
- 日志；
- 报告。

## 13.4 Git

存储：

- 物理规则；
- OpenFOAM 模板；
- Prompt；
- Schema；
- 后处理代码；
- 数据库迁移；
- 验证规则。

---

## 14. 文献生命周期

文献状态：

```text
RAW
→ PARSED
→ EXTRACTED
→ REVIEW_PENDING
→ REVIEWED
→ PUBLISHED
```

只有 `REVIEWED` 或 `PUBLISHED` 内容可以用于高风险自动决策。

增量流程：

```text
定时查询新论文
→ DOI 去重
→ 元数据校正
→ 全文查询
→ 下载
→ 解析
→ Paper Card
→ 向量化
→ 质量检查
→ 待审核
```

---

## 15. 文献检索过程

检索不能只执行一次。

推荐过程：

```text
用户问题
→ 识别物理主题
→ 检索物理机制
→ 检索数值模型
→ 检索边界条件
→ 检索验证数据
→ 检索参数范围
→ 检索冲突文献
→ 形成 Evidence Package
```

Evidence Package 示例：

```json
{
  "query": "k omega SST curved pipe secondary flow",
  "evidence": [],
  "conflicts": [],
  "coverage": {
    "physical_mechanism": true,
    "solver_selection": true,
    "validation_data": false
  },
  "next_queries": [
    "curved pipe experimental velocity profile validation"
  ]
}
```

如果验证数据缺失，系统必须继续定向检索。

---

## 16. 文献冲突处理

当论文结论冲突时，不允许模型仅按引用量选一个结论。

比较维度：

- 几何相似度；
- 流体类型；
- Reynolds 数范围；
- 粗糙度；
- 入口发展条件；
- 湍流模型；
- 网格规模；
- 是否有实验验证；
- 时间尺度；
- 论文年代；
- 适用范围。

冲突输出：

```json
{
  "claim": "kOmegaSST gives better bend-flow prediction",
  "supporting_evidence": [],
  "opposing_evidence": [],
  "current_case_similarity": 0.78,
  "decision": "compare_two_models_in_pilot"
}
```

科研上更合理的处理通常是设计模型敏感性实验。

---

## 17. 物理知识库

文献知识库回答：

> 哪篇论文在什么条件下做过什么？

物理知识库回答：

> 当前问题必须满足哪些物理和数值约束？

---

## 18. 物理知识类型

### 18.1 无量纲数

包括：

- Reynolds 数；
- Dean 数；
- Mach 数；
- Courant 数；
- Weber 数；
- Froude 数；
- Strouhal 数；
- Euler 数。

示例：

```json
{
  "name": "Reynolds number",
  "symbol": "Re",
  "definition": "rho*U*D/mu",
  "variables": ["rho", "U", "D", "mu"],
  "unit": "dimensionless",
  "applicability": ["internal_flow"],
  "sources": []
}
```

### 18.2 物理模型适用性

例如：

- 不可压缩模型适用范围；
- 层流与湍流判断；
- 湍流模型适用条件；
- 近壁处理；
- 多相模型适用流型；
- 稳态与瞬态模型选择。

### 18.3 边界条件规则

例如：

- 入口不能过约束；
- 出口压力设置；
- 周期边界条件；
- 对称边界合法性；
- 入口发展段要求；
- 入口湍流强度设置。

### 18.4 数值稳定性规则

例如：

- Courant 数过高；
- 松弛因子过大；
- 网格非正交过高；
- skewness 超标；
- 时间步不合理；
- 离散格式过于激进；
- 残差下降但监测量未稳定。

### 18.5 验证规则

包括：

- 质量守恒；
- 动量守恒；
- 能量守恒；
- 网格无关性；
- 时间步无关性；
- Richardson 外推；
- GCI；
- 实验数据验证；
- 解析解验证；
- 不确定性量化。

### 18.6 软件能力映射

例如：

```text
不可压缩稳态 RANS
→ simpleFoam

不可压缩瞬态流动
→ pimpleFoam

自由液面两相流
→ interFoam

系统级瞬态多相管流
→ OLGA
```

### 18.7 经验关联式

每个经验关联式保存：

- 公式；
- 参数定义；
- 单位；
- 适用流体；
- 适用几何；
- Reynolds 数范围；
- 来源；
- 不确定度；
- 是否允许外推；
- 审核状态。

---

## 19. 物理知识存储

第一阶段推荐：

```text
PostgreSQL
+
JSONB
+
关系表
+
YAML 规则库
```

表结构建议：

```text
physics_entities
physics_relations
physics_rules
correlations
solver_capabilities
boundary_condition_rules
verification_rules
unit_definitions
rule_sources
rule_versions
```

关系示例：

```text
source_entity | relation          | target_entity
------------------------------------------------
kOmegaSST     | applicable_to     | adverse_pressure_gradient
wallFunction  | requires_check    | y_plus
interFoam     | supports          | free_surface_two_phase
steady_solver | incompatible_with | transient_slugging
```

第一阶段不急于引入 Neo4j。只有在图查询需求明确后再扩展。

---

## 20. 物理规则格式

示例：

```yaml
id: RULE-TURB-001
name: wall_treatment_consistency
category: turbulence
severity: hard

when:
  turbulence_model: kOmegaSST
  wall_treatment: wall_function

check:
  metric: y_plus
  operator: within_model_target

then:
  pass: continue
  fail: redesign_boundary_layer_mesh

sources:
  - source_id: MANUAL-OPENFOAM-001

version: 1.2.0
```

瞬态规则：

```yaml
id: RULE-TRANSIENT-004
name: transient_flow_requires_time_step_check
severity: hard

when:
  simulation_type: transient

require:
  - courant_number_check
  - time_step_independence
```

规则等级：

| 等级 | 行为 |
|---|---|
| HARD | 不满足则禁止继续 |
| SOFT | 可继续，但必须说明理由 |
| WARNING | 写入报告 |
| INFO | 作为建议 |

LLM 只能建议新增规则，不能直接修改正式规则库。

---

## 21. 规则执行流程

```text
ResearchSpec
→ 单位归一化
→ 计算派生物理量
→ 初步判断流态
→ 匹配规则
→ 执行 HARD 规则
→ 执行 SOFT 规则
→ 生成 ViolationReport
```

示例：

用户输入：

```text
管径 200 mm，水，流速 2 m/s
```

程序计算：

- 管径转为 0.2 m；
- 截面积；
- 体积流量；
- Reynolds 数；
- 初步流态；
- 是否需要湍流模型；
- 建议入口发展段；
- 可能的数值模型。

所有数值由程序计算，不由模型心算。

---

## 22. Agent 设计

系统不是多个 Agent 自由聊天，而是：

> 一个确定性的状态机，调用多个角色化模型节点。

推荐角色：

| Agent | 职责 |
|---|---|
| Research Interpreter | 将自然语言转换为 ResearchSpec |
| Retrieval Planner | 规划文献检索 |
| Fluid Scientist | 提出物理机制和研究假设 |
| Numerical Expert | 选择求解器、网格和边界条件 |
| Experiment Designer | 设计实验矩阵和追加实验 |
| Simulation Supervisor | 分析仿真执行状态 |
| Scientific Reviewer | 审查证据、适用范围和结论 |

这些角色可以使用同一个底层模型，但 Prompt、可读字段和可写字段不同。

---

## 23. 共享状态 Blackboard

示例：

```python
class ResearchState:
    project_id: str
    workflow_version: int

    user_request: str
    research_spec: dict

    unresolved_questions: list
    assumptions: list

    retrieval_queries: list
    evidence_packages: list

    hypotheses: list
    candidate_models: list
    selected_model: dict

    experiment_plan: dict
    simulation_cases: list

    validation_results: dict
    analysis_results: dict
    conclusions: list

    warnings: list
    approvals: list
    audit_events: list
```

字段权限：

| Agent | 可读取 | 可写入 |
|---|---|---|
| Interpreter | 用户问题 | ResearchSpec |
| Retriever | ResearchSpec | Evidence Package |
| Fluid Scientist | ResearchSpec、证据 | Hypotheses |
| Numerical Expert | 假设、证据 | Candidate Models |
| Experiment Designer | 模型、预算 | Experiment Plan |
| Simulation Supervisor | Case、日志 | Failure Diagnosis |
| Reviewer | 全部 | Review Result |

---

## 24. Agent 通信协议

禁止自由文本作为唯一通信方式。

统一 Action：

```json
{
  "agent": "NumericalExpert",
  "action_type": "SELECT_SOLVER",
  "status": "PROPOSED",
  "reason_codes": [
    "INCOMPRESSIBLE_FLOW",
    "STEADY_RANS"
  ],
  "evidence_ids": [
    "rule:SOLVER-002",
    "paper:doi_xxx:chunk_41"
  ],
  "payload": {
    "solver": "simpleFoam",
    "turbulence_model": "kOmegaSST"
  },
  "confidence": 0.86,
  "requires_approval": true
}
```

所有输出必须通过 JSON Schema 校验。

---

## 25. Agent 工作流

```text
START
→ interpret_question
→ validate_research_spec
  ├─ 缺关键参数 → human_interrupt
  └─ 完整
      → retrieve_evidence
      → generate_hypotheses
      → select_physical_model
      → rule_validation
        ├─ HARD violation → revise_plan
        └─ pass
            → design_pilot_experiment
            → human_approval
            → submit_pilot
            → wait_simulation
            → verify_results
              ├─ infra_failure → retry
              ├─ mesh_failure → regenerate_mesh
              ├─ numerical_failure → controlled_repair
              ├─ physics_failure → revise_model
              └─ pass
                  → design_full_experiment
                  → run_full_experiment
                  → analyze
                  → scientific_review
                  → REPORT
```

---

## 26. 循环限制

必须防止 Agent 无限讨论。

```yaml
limits:
  max_plan_revisions: 3
  max_solver_revisions: 2
  max_retrieval_rounds: 3
  max_numerical_repairs_per_case: 2
  max_total_llm_calls_per_project: 50
```

超过上限自动转人工审核。

---

## 27. ResearchSpec

自然语言必须先转换为结构化研究规格。

示例：

```json
{
  "research_question": "弯管曲率和雷诺数如何影响压降及二次流",
  "system": {
    "geometry_type": "90_degree_bend",
    "diameter_m": 0.2,
    "upstream_length_D": 20,
    "downstream_length_D": 30
  },
  "fluid": {
    "type": "single_phase",
    "name": "water",
    "temperature_K": 293.15
  },
  "independent_variables": [
    {
      "name": "reynolds_number",
      "range": [10000, 100000],
      "scale": "log"
    },
    {
      "name": "curvature_ratio",
      "range": [1.0, 5.0]
    }
  ],
  "responses": [
    "pressure_drop",
    "loss_coefficient",
    "secondary_flow_intensity"
  ],
  "constraints": [
    "steady_state",
    "incompressible",
    "mass_imbalance_below_0.1_percent"
  ],
  "simulation_budget": {
    "max_cases": 60,
    "max_parallel": 8
  }
}
```

缺失信息分为：

1. 可根据文献或默认规范推断；
2. 会显著影响结论，必须确认。

所有默认值必须显示来源和影响。

---

## 28. 实验设计

实验设计由标准算法完成，不由 LLM 随意列点。

### 28.1 基准算例

正式实验前必须选择：

- 解析解；
- 高质量实验数据；
- 公认基准；
- 已验证文献算例。

### 28.2 初始采样

根据维度选择：

| 场景 | 方法 |
|---|---|
| 变量很少 | 全因子 |
| 中等维度 | 拉丁超立方 |
| 全局敏感性 | Sobol |
| 高维初筛 | Morris |
| 昂贵仿真 | 贝叶斯优化 |

### 28.3 自适应实验

首批结果后，优先追加：

- 高梯度区域；
- 不确定性高的区域；
- 状态转变区域；
- 当前最优附近；
- 文献冲突区域；
- 异常区域。

### 28.4 实验停止条件

```json
{
  "design_type": "sobol_then_adaptive",
  "initial_cases": 24,
  "adaptive_cases": 16,
  "mesh_levels": ["coarse", "medium", "fine"],
  "stopping_rules": [
    "surrogate_error_below_3_percent",
    "parameter_importance_stable",
    "budget_exhausted"
  ]
}
```

---

## 29. Pilot 机制

禁止一开始批量提交全部算例。

推荐 Pilot：

```text
1 个中心工况
×
3 套网格
×
2 个湍流模型
```

Pilot 验证：

- 几何是否正确；
- 网格是否可生成；
- 边界条件是否合理；
- 求解是否稳定；
- 后处理是否正确；
- 压降是否合理；
- 守恒是否满足；
- 与基准结果是否一致。

Pilot 不通过，禁止正式批量运行。

---

## 30. 仿真工具调用

模型不能直接访问 Shell。

白名单工具：

```text
validate_case
render_case
generate_geometry
generate_mesh
check_mesh
submit_solver
get_job_status
read_solver_log
read_residuals
cancel_job
extract_metrics
extract_profiles
generate_visualization
archive_case
```

执行前检查：

```text
JSON Schema
→ 权限
→ 参数范围
→ 路径白名单
→ 软件许可
→ 计算预算
→ 资源限制
→ 执行
```

禁止：

- 任意 Shell；
- 任意文件删除；
- `sudo`；
- 任意网络请求；
- 任意路径读写；
- 修改软件许可证；
- 修改系统配置。

---

## 31. 仿真适配器

统一接口：

```python
class SimulatorAdapter:
    def validate_spec(self, research_spec): ...
    def select_template(self, research_spec): ...
    def render_case(self, case_manifest): ...
    def generate_mesh(self, case_id): ...
    def check_mesh(self, case_id): ...
    def submit(self, case_id): ...
    def get_status(self, job_id): ...
    def postprocess(self, case_id): ...
    def collect_results(self, case_id): ...
```

### 31.1 OpenFOAM Adapter

```text
OpenFOAMAdapter
├── validate_research_spec
├── select_template
├── render_case_files
├── generate_geometry
├── generate_mesh
├── run_checkMesh
├── run_solver
├── monitor_log
├── run_function_objects
└── collect_artifacts
```

### 31.2 OLGA Adapter

```text
OLGAAdapter
├── check_license
├── load_base_model
├── modify_geometry
├── set_fluid_properties
├── set_boundary_schedule
├── configure_output_channels
├── start_simulation
├── monitor_variables
└── export_results
```

第一版应先完成 OpenFOAM。OLGA 等获得合法授权后再接入。

---

## 32. Case Manifest

每个算例必须生成不可变配置。

```json
{
  "case_id": "BEND_RE50000_CR2_001",
  "template_id": "openfoam_bend_v1.4",
  "template_git_commit": "abc123",
  "solver": "simpleFoam",
  "software_version": "OpenFOAM-x",
  "container_digest": "sha256:...",
  "geometry": {},
  "mesh": {},
  "physics": {},
  "boundary_conditions": {},
  "numerics": {},
  "resources": {
    "cpu": 16,
    "memory_gb": 32,
    "walltime_min": 120
  },
  "expected_outputs": [
    "pressure_drop",
    "mass_balance",
    "velocity_profile"
  ]
}
```

任何修改都生成新版本，不能覆盖旧版本。

---

## 33. 长任务架构

### 33.1 MVP

```text
LangGraph
+
PostgreSQL Checkpoint
+
Celery
+
Redis/RabbitMQ
+
本地 OpenFOAM Worker
```

### 33.2 正式版

```text
LangGraph
+
Temporal
+
Slurm
+
Apptainer
```

职责区分：

| 模块 | 职责 |
|---|---|
| LangGraph | 科研逻辑和 Agent 决策 |
| Temporal/Celery | 长任务、重试、超时和恢复 |
| Slurm | HPC 资源调度 |
| OpenFOAM Worker | 实际运行仿真 |
| PostgreSQL | 保存状态 |
| MinIO | 保存文件 |

---

## 34. 任务状态机

```text
CREATED
→ VALIDATING
→ READY
→ QUEUED
→ RUNNING
→ POSTPROCESSING
→ VERIFYING
  ├─ PASSED
  ├─ FAILED_INFRA
  ├─ FAILED_MESH
  ├─ FAILED_NUMERICAL
  ├─ FAILED_PHYSICS
  └─ NEEDS_REVIEW
```

### 34.1 可自动重试

- 节点暂时故障；
- 网络短时中断；
- 文件系统短时异常；
- Worker 掉线；
- 临时许可证占满；
- 调度器瞬时异常。

### 34.2 不可原样重试

- 网格负体积；
- 参数非法；
- 边界过约束；
- 模型不适用；
- 连续发散；
- 软件根本没有许可证；
- 模板不支持当前几何。

---

## 35. 自动故障分类

### 35.1 基础设施错误

- 软件不存在；
- 环境变量错误；
- MPI 错误；
- 容器错误；
- 许可证错误；
- 文件路径错误；
- 磁盘空间不足。

### 35.2 网格错误

- 负体积；
- 非正交性过高；
- skewness 过高；
- 几何不闭合；
- 网格数量异常；
- 边界命名错误。

### 35.3 数值错误

- 残差爆炸；
- 压力或速度非物理；
- Courant 数过高；
- continuity error 增大；
- 浮点异常；
- 解振荡不稳定。

### 35.4 物理错误

- 模型不适用；
- 稳态假设不成立；
- 边界条件不合理；
- 输出违反基本物理规律；
- 参数超出模型适用范围。

---

## 36. 受控自动修复

允许的修复：

- 降低时间步；
- 调整松弛因子；
- 切换更稳健离散格式；
- 使用低阶格式初始化；
- 增加入口发展段；
- 修改局部网格；
- 降低网格增长率；
- 重新初始化流场。

限制：

- 每个算例最多自动修复 2 次；
- 修复必须记录；
- 修复后生成新配置版本；
- 最终报告必须列出修复动作；
- 不能静默改变物理模型。

---

## 37. 收敛和可信性验证

### 37.1 迭代收敛

检查：

- 残差；
- continuity error；
- 监测量稳定；
- 压降稳定；
- 流量稳定；
- 是否存在周期振荡。

### 37.2 质量守恒

检查：

```text
入口质量流量
出口质量流量
相对不平衡误差
```

### 37.3 网格无关性

粗、中、细三套网格。

比较：

- 压降；
- 阻力系数；
- 最大速度；
- 二次流强度；
- 壁面剪切应力。

进一步计算：

- Richardson 外推；
- GCI。

### 37.4 时间步无关性

瞬态问题比较：

- 均值；
- 峰值；
- RMS；
- 主频；
- 相位；
- 段塞频率；
- 界面传播速度。

### 37.5 模型敏感性

比较：

- 湍流模型；
- 壁面处理；
- 多相模型；
- 相间作用模型；
- 入口扰动；
- 离散格式。

### 37.6 基准验证优先级

```text
解析解
>
高质量实验数据
>
公认基准算例
>
高质量数值论文
>
工程经验
```

### 37.7 可信性结果

```json
{
  "iterative_convergence": 0.95,
  "mass_conservation": 0.99,
  "mesh_independence": 0.88,
  "time_step_independence": null,
  "benchmark_agreement": 0.91,
  "model_sensitivity": 0.73,
  "overall_level": "moderate_to_high"
}
```

禁止只输出一个不可解释的总分。

---

## 38. 数据分析

### 38.1 基础统计

- 均值；
- 方差；
- 置信区间；
- 异常点；
- 相关性；
- 主效应；
- 交互效应；
- 重复性；
- 数据分布。

### 38.2 流体专用指标

根据任务选择：

- 压降；
- 阻力系数；
- 速度剖面；
- 湍流强度；
- 涡量；
- Q-criterion；
- 二次流强度；
- 壁面剪切应力；
- 相含率；
- 段塞频率；
- 压力频谱；
- POD；
- DMD；
- 能谱；
- 流型转变区域。

### 38.3 敏感性和代理模型

可采用：

- Sobol；
- Morris；
- FAST；
- 高斯过程；
- 随机森林；
- 神经网络；
- 贝叶斯优化。

LLM 只解释计算结果，不替代统计计算。

---

## 39. 输出报告结构

### 39.1 研究问题

正式化的研究问题。

### 39.2 假设与范围

说明：

- 物理假设；
- 数值假设；
- 几何范围；
- 参数范围；
- 不考虑的因素。

### 39.3 文献证据

列出：

- 关键文献；
- 支持结论；
- 冲突文献；
- 适用性；
- 当前算例相似度。

### 39.4 实验设计

列出：

- 自变量；
- 因变量；
- 控制变量；
- 采样方法；
- 仿真数量；
- 网格；
- 求解器；
- 收敛标准；
- 资源预算。

### 39.5 可信性分析

列出：

- 守恒误差；
- 网格误差；
- 时间步误差；
- 基准误差；
- 模型敏感性；
- 失败算例。

### 39.6 主要结果

结果必须包含：

- 趋势；
- 效应大小；
- 置信区间；
- 异常点；
- 适用范围。

### 39.7 证据等级

结论分为：

- 仿真直接观察；
- 统计推断；
- 文献支持；
- 模型外推；
- 尚未验证假设。

### 39.8 最终结论

必须使用限定表达：

> 在当前几何、流体模型、数值模型和参数范围内……

不得将局部仿真结果描述为普适规律。

---

## 40. 人工审批节点

第一版保留三个 Gate。

### Gate 1：研究问题确认

确认：

- 自变量；
- 因变量；
- 参数范围；
- 物理假设；
- 默认值。

### Gate 2：批量运行前

确认：

- 求解器；
- 湍流模型；
- 网格策略；
- 资源预算；
- 仿真数量。

### Gate 3：最终结论前

确认：

- 可信性；
- 失败算例；
- 适用范围；
- 结论措辞；
- 是否存在过度外推。

---

## 41. 权限与安全

角色：

| 角色 | 权限 |
|---|---|
| User | 提交问题、查看项目 |
| Researcher | 修改方案、批准计算 |
| Expert Reviewer | 审批模型和结论 |
| Administrator | 管理软件、资源和权限 |

大模型不得持有：

- Slurm 管理员凭据；
- OLGA 许可证文件；
- 数据库管理员密码；
- 任意删除权限；
- 任意网络权限；
- 系统管理权限。

---

## 42. 数据生命周期

### 42.1 长期保存

- ResearchSpec；
- Case Manifest；
- 软件版本；
- 容器版本；
- 模板版本；
- 网格摘要；
- 求解配置；
- 关键日志；
- 关键指标；
- 结果图；
- 可信性结果；
- 审批记录；
- 最终报告。

### 42.2 可归档或删除

- 每个时间步的完整场；
- 中间重启文件；
- 临时网格；
- 重复可视化文件；
- 无价值失败中间文件。

建议策略：

```text
失败算例原始文件保留 30 天
普通通过算例完整场保留 90 天
关键基准算例永久保留
其他算例保留指标和压缩归档
```

---

## 43. 项目目录

```text
fluid-research-agent/
├── apps/
│   ├── api/
│   └── web/
├── orchestration/
│   ├── graph.py
│   ├── states.py
│   ├── transitions.py
│   └── interrupts.py
├── agents/
│   ├── interpreter.py
│   ├── retrieval_planner.py
│   ├── fluid_scientist.py
│   ├── numerical_expert.py
│   ├── experiment_designer.py
│   ├── simulation_supervisor.py
│   └── reviewer.py
├── schemas/
│   ├── research_spec.py
│   ├── experiment_plan.py
│   ├── case_manifest.py
│   ├── evidence.py
│   └── report.py
├── knowledge/
│   ├── ingestion/
│   │   ├── openalex.py
│   │   ├── crossref.py
│   │   ├── unpaywall.py
│   │   ├── grobid.py
│   │   └── chunking.py
│   ├── retrieval/
│   ├── paper_cards/
│   ├── rules/
│   └── ontologies/
├── simulators/
│   ├── base.py
│   ├── openfoam/
│   │   ├── adapter.py
│   │   ├── templates/
│   │   └── parsers/
│   └── olga/
│       ├── adapter.py
│       └── templates/
├── execution/
│   ├── celery_tasks.py
│   ├── temporal_workflows.py
│   ├── slurm_client.py
│   └── workers/
├── validation/
│   ├── units.py
│   ├── conservation.py
│   ├── convergence.py
│   ├── mesh_independence.py
│   ├── timestep_independence.py
│   └── benchmark.py
├── analysis/
│   ├── doe.py
│   ├── sensitivity.py
│   ├── surrogate.py
│   └── visualization.py
├── evals/
│   ├── retrieval/
│   ├── planning/
│   ├── simulation/
│   └── reporting/
└── infra/
    ├── docker/
    ├── apptainer/
    ├── postgres/
    ├── qdrant/
    └── minio/
```

---

## 44. 主要卡点与解决方案

## 44.1 文献很多但检索差

原因：

- chunk 不合理；
- 只做向量检索；
- 元数据缺失；
- 低质量论文过多；
- 术语和符号不统一。

解决：

```text
章节级切分
+
Paper Card
+
混合检索
+
元数据过滤
+
Reranker
+
检索评测集
```

评测指标：

- Recall@5；
- Recall@20；
- MRR；
- 证据支持率；
- 错误引用率；
- 无答案识别率。

## 44.2 公式和表格解析错误

解决：

- 保留原始页面坐标；
- 低置信公式走视觉复核；
- 原图和抽取结果绑定；
- 经验公式人工审核；
- 不允许模型自动补全乱码并直接运行。

## 44.3 Agent 参数漂移

解决：

- ResearchSpec 版本化；
- 每次修改生成 diff；
- 已批准字段锁定；
- 修改关键字段重新审批；
- 运行中的 Case Manifest 不可变。

## 44.4 仿真收敛但物理错误

解决：

```text
残差
+
监测量
+
守恒
+
网格独立性
+
时间步独立性
+
基准
+
模型敏感性
+
物理范围
```

## 44.5 参数化网格失败

解决：

1. 参数范围预检查；
2. 模板适用范围限制；
3. `checkMesh` 硬门槛；
4. 单元数上限；
5. 自动网格质量报告；
6. 超范围转人工；
7. 不让模型自由生成网格拓扑。

## 44.6 HPC 排队导致工作流卡住

解决：

```text
提交任务
→ 保存 job_id
→ 工作流暂停
→ 异步查询
→ 完成事件
→ 恢复工作流
```

## 44.7 模型调用延迟和成本高

路由：

```text
规则程序能做
→ 不调用模型

轻量抽取
→ GPT-5.4 mini

普通规划
→ GPT-5.5 medium

冲突和审核
→ GPT-5.5 high
```

还应使用：

- 查询缓存；
- Prompt 缓存；
- 文献抽取缓存；
- 批量 embedding；
- Evidence 数量限制；
- 不把全文全部放入上下文。

## 44.8 OLGA 许可证不足

第一版：

```text
OpenFOAM 完整闭环
→ 定义 OLGA Adapter
→ 有合法许可证后部署 OLGA Worker
```

许可证不可用时返回：

```text
LICENSE_UNAVAILABLE
```

禁止绕过许可证。

---

## 45. 评测体系

### 45.1 需求理解

- 变量识别准确率；
- 单位识别准确率；
- 参数范围识别；
- 缺失条件发现率；
- ResearchSpec 合法率。

### 45.2 文献检索

- Recall@5；
- Recall@20；
- MRR；
- 引用正确率；
- 证据覆盖度；
- 无答案识别率。

### 45.3 实验设计

- 专家接受率；
- 无效实验比例；
- 参数覆盖度；
- 相同预算的信息增益；
- 仿真节省率。

### 45.4 仿真执行

- 首次运行成功率；
- 自动修复成功率；
- 故障分类准确率；
- 非法操作拦截率；
- 平均计算成本。

### 45.5 可信性

- 守恒通过率；
- 网格无关性通过率；
- 时间步无关性通过率；
- 基准误差；
- 非物理解识别率。

### 45.6 报告质量

- 数值引用正确率；
- 文献引用正确率；
- 结论与证据一致率；
- 过度外推率；
- 专家修改幅度；
- 可复现率。

---

## 46. 开发路线

## 阶段 0：确定边界

完成：

- 支持问题清单；
- 不支持问题清单；
- ResearchSpec Schema；
- 10 个标准问题；
- 第一版验收标准。

## 阶段 1：文献知识库

完成：

- OpenAlex；
- Crossref；
- Unpaywall；
- 本地 PDF 批量导入；
- GROBID；
- Paper Card；
- Qdrant；
- 检索 API。

目标：

- 300 篇论文自动处理；
- DOI 去重；
- 正文解析率达到可接受水平；
- 原文可回溯。

## 阶段 2：问题结构化

完成：

```text
自然语言
→ ResearchSpec
→ 单位归一化
→ 缺失条件
→ 派生物理量
```

建立 100～200 条人工标注问题。

## 阶段 3：物理规则和模板

完成模板：

```text
laminar_pipe
turbulent_pipe
90_degree_bend
```

完成 30～50 条高价值规则。

## 阶段 4：OpenFOAM 执行闭环

完成：

```text
Case Manifest
→ 模板渲染
→ 网格
→ checkMesh
→ 求解
→ 日志
→ 后处理
→ 验证
```

## 阶段 5：实验设计

加入：

- 全因子；
- 拉丁超立方；
- Sobol；
- 参数约束；
- 批量任务；
- 预算控制。

## 阶段 6：可信性分析

加入：

- 网格无关性；
- 守恒；
- 基准；
- 模型敏感性；
- GCI；
- 可信等级。

完成这一阶段后，系统才可以称为“科研智能体”。

## 阶段 7：自适应实验

加入：

```text
初始样本
→ 代理模型
→ 不确定性
→ 追加工况
→ 停止准则
```

## 阶段 8：两相流和 OLGA

加入：

- 瞬态；
- 多相；
- 段塞；
- OLGA Adapter；
- 许可证管理；
- 跨软件验证。

## 阶段 9：代理模型和世界模型

训练：

```text
几何
+
边界条件
+
流体性质
+
时间
→
压力场
+
速度场
+
相分布
```

用途：

- 快速初筛；
- 贝叶斯优化；
- 高价值工况选择；
- 仿真失败预测；
- 多保真优化。

---

## 47. 是否需要微调大模型

第一阶段不建议微调。

优先顺序：

| 方法 | 当前优先级 |
|---|---:|
| Structured Prompt | 高 |
| RAG | 高 |
| 物理规则 | 高 |
| 仿真模板 | 高 |
| 状态机 | 高 |
| 确定性校验 | 高 |
| SFT/LoRA | 低 |
| 领域继续预训练 | 很低 |
| 仿真代理模型 | 中后期高 |

论文原文更适合进入 RAG，而不是直接做普通监督微调。

---

## 48. 未来微调数据

真正有价值的数据：

```text
自然语言问题
→ 专家确认后的 ResearchSpec

ResearchSpec + Evidence
→ 专家确认后的实验方案

错误日志
→ 专家确认后的诊断和修复

统计结果
→ 专家修订后的科学结论
```

建议数据量：

| 类型 | 建议数量 |
|---|---:|
| ResearchSpec | 500～1000 |
| 实验方案 | 300～500 |
| 故障诊断 | 500～1000 |
| 结论修订 | 300～500 |
| 安全和拒绝样本 | 200 以上 |

微调目标：

- 稳定 JSON；
- 正确工具选择；
- 团队实验规范；
- 故障处理习惯；
- 报告风格；
- 何时拒绝执行。

微调不能替代：

- 仿真；
- 守恒；
- 网格验证；
- 物理方程；
- 文献更新；
- 数值求解。

---

## 49. 推荐第一版系统

```text
GPT-5.5 主规划
+
GPT-5.4 mini 文献抽取
+
OpenAlex / Crossref / Unpaywall
+
GROBID
+
PostgreSQL
+
Qdrant
+
YAML 物理规则
+
LangGraph
+
Celery
+
OpenFOAM
+
Python 确定性验证
+
人工审批
```

第一版闭环：

```text
输入弯管流动问题
→ ResearchSpec
→ 文献检索
→ 物理假设
→ 数值模型
→ Pilot
→ 用户审批
→ OpenFOAM 批量仿真
→ 守恒和网格验证
→ 敏感性分析
→ 科研 Reviewer
→ 带证据链报告
```

---

## 50. 供模型执行时的行为约束

模型在使用本文档时应遵循：

1. 不把仿真成功等同于结论可信；
2. 不直接生成并执行任意 Shell；
3. 不擅自修改已批准参数；
4. 不在缺少关键条件时静默猜测；
5. 不引用没有原文位置的文献结论；
6. 不从乱码公式中自行补全关联式；
7. 不把统计相关性写成物理因果；
8. 不忽视失败工况；
9. 不超出参数范围外推；
10. 不绕过软件许可证；
11. 不让 Agent 无限讨论；
12. 不让 LLM 替代确定性数值程序；
13. 关键模型选择必须经过规则检查；
14. 批量运行前必须先完成 Pilot；
15. 最终结论必须说明适用范围和不确定性。

---

## 51. 最终定位

本项目最合适的科研定位不是：

> 大模型调用 OpenFOAM。

而是：

> 基于领域知识检索、物理规则约束、可信数值验证和自适应实验设计的流体力学科研智能体。

核心创新应放在：

- 研究问题结构化；
- 文献证据链；
- 物理规则和模型协同；
- 数值仿真可信性；
- 长任务可恢复执行；
- 自适应实验设计；
- 跨软件统一接口；
- 人机协同审批；
- 结果可追溯与可复现；
- 仿真代理模型与高保真软件协同。

