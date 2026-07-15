# V5 智能闭环端到端测试报告

## 1. 测试概览

本报告记录 V5 智能闭环改造方案（第 14 节 Test A-I 强制端到端测试集）的完整测试执行结果。

| 指标 | 数值 |
|------|------|
| 测试文件总数 | 6 |
| 测试用例总数 | 247 |
| 通过数 | 247 |
| 失败数 | 0 |
| 通过率 | 100% |
| 覆盖优先级 | P1 - P8 |

### 测试文件与优先级映射

| 测试文件 | 优先级 | 测试用例数 | 核心验证内容 |
|----------|--------|-----------|-------------|
| `test_intent_conflict.py` | P1, P2 | 51 | 意图候选提取、冲突仲裁、语义忠实校验 |
| `test_error_repair.py` | P3, P4 | 54 | OpenFOAM 错误分类、受控修复闭环 |
| `test_skills.py` | P5 | 39 | Skill Manifest 加载、选择、Prompt 注入、编译器钩子 |
| `test_capabilities.py` | P6 | 38 | 能力检查、未知能力识别、扩展触发 |
| `test_persistence.py` | P7 | 36 | SQLite 持久化、Spec/Job/LLM/修复记录、重启恢复 |
| `test_llm_report.py` | P8 | 28 | LLM 科学报告、物理验证、经验公式对比 |
| **合计** | | **247** | |

所有测试位于 `tests/v5_closed_loop/` 目录下，无需运行中的服务器或真实 OpenFOAM 安装即可执行，通过 mock LLM client 和 mock 执行器验证业务逻辑的正确性。

---

## 2. 测试矩阵：Test A-I 场景覆盖

下表展示改造方案第 14 节定义的 Test A-I 场景与测试文件的对应关系：

| 场景 | 场景名称 | 覆盖测试文件 | 覆盖测试类 / 关键测试 | 验证要点 |
|------|---------|-------------|---------------------|---------|
| **Test A** | 标准圆柱绕流 | `test_intent_conflict.py` | `TestRegexCandidateExtractor.test_extract_cylinder_candidates` | 圆柱 spec 构建正确、半径/域/入口速度候选提取、source_span 可追踪 |
| **Test B** | 圆柱 + 矩形障碍 | `test_intent_conflict.py` | `TestLLMCandidateExtractor.test_extract_cylinder_dimensions`、`TestConflictResolver.test_agreement_when_both_match` | 矩形候选提取、regex/LLM 候选一致性仲裁、字段来源可追踪 |
| **Test C** | 圆柱 + 三角障碍 | `test_intent_conflict.py` | `TestCandidateSetIntegration.test_test_c_triangle_not_cosine_bell`、`TestGeometryFidelity.test_triangle_stays_triangle`、`TestSpatialRelations.test_centered_under_violation` | triangle 保持为 triangle（不变成 cosine_bell）、空间关系 `centered_under` 校验、obstacle.center_x 与 cylinder 一致 |
| **Test D** | 正弦凸起 | `test_intent_conflict.py` | `TestConflictResolver.test_duplicate_entity_detection_sine_bump`、`TestCandidateSetIntegration.test_test_d_sine_bump_no_rectangle`、`TestGeometryFidelity.test_sine_bump_not_rectangle` | half_sine profile 启用、rectangle 被标记为 DUPLICATE_ENTITY 移除、周期边界成对 |
| **Test E** | 多轮修改 | `test_persistence.py` | `TestSpecRoundtrip.test_save_spec_overwrites_on_same_id`、`TestSpecRoundtrip.test_save_spec_preserves_draft_status` | 同一 spec_id 覆盖更新、spec_version 版本递增、draft_status 持久化 |
| **Test F** | 可控 patch 错误 | `test_error_repair.py` | `TestErrorClassification.test_primary_error_classification`（LOG_PATCH_MISMATCH_TEST_F）、`TestRepairOrchestrator.test_test_f_freezes_when_no_llm_available`、`TestRepairOrchestrator.test_test_f_success_path` | patch 不匹配分类为 BOUNDARY_CONDITION_ERROR、FATAL + 可修复、修复闭环 classify-diagnose-apply-validate |
| **Test G** | 数值发散 | `test_error_repair.py` | `TestErrorClassification.test_primary_error_classification`（LOG_NAN_STANDALONE_TEST_G）、`TestRepairOrchestrator.test_test_g_freezes_when_executor_cannot_apply` | NaN 分类为 SOLVER_ERROR、Courant 数检测为 PHYSICS_ERROR、修复级别逐级升级 |
| **Test H** | 未知能力 | `test_capabilities.py` | `TestUnsupportedCapabilities.test_unsupported_bottom_profile_type`、`TestExtend.test_extend_creates_checkpoint_for_unsupported`、`TestExtend.test_extend_with_mock_orchestrator` | 未知能力进入 unsupported 列表、创建扩展检查点、触发 ExtensionOrchestrator |
| **Test I** | 重启恢复 | `test_persistence.py` | `TestRecovery.test_recover_all_specs_after_reopen`、`TestRecovery.test_recover_jobs_for_spec_after_reopen`、`TestRecovery.test_recover_preserves_spec_data_integrity` | 模拟重启后 spec/job 全量恢复、数据完整性保持 |

---

## 3. 各测试文件详情

### 3.1 test_intent_conflict.py（P1/P2 — 51 个测试）

**文件路径**: `tests/v5_closed_loop/test_intent_conflict.py`

覆盖 P1（意图候选与冲突仲裁）和 P2（语义忠实校验）两大优先级。

| 测试类 | 测试数 | 验证内容 |
|--------|-------|---------|
| `TestGeometrySynonyms` | 14 | 几何同义词归一化：三角->triangle、正弦凸起->half_sine、余弦丘->cosine_bell 等；未知类型透传；None 处理；同义词表完整性（覆盖 triangle/rectangle/cosine_bell/half_sine/gaussian/cylinder） |
| `TestRegexCandidateExtractor` | 5 | 正则提取器从 spec 中提取圆柱、三角、正弦凸起候选；所有候选 source=REGEX；包含 source_span |
| `TestLLMCandidateExtractor` | 5 | LLM 提取器从结构化 JSON 解析三角、正弦凸起、圆柱尺寸、边界类型；所有候选 source=LLM |
| `TestConflictResolver` | 6 | regex/LLM 一致时 AGREEMENT；仅 regex 时 REGEX_ONLY；仅 LLM 时语义校验通过后 LLM_ONLY、不通过时 BLOCKING；值冲突记录；Test D 重复实体检测（rectangle + half_sine -> DUPLICATE_ENTITY） |
| `TestCandidateSetIntegration` | 3 | Test C 三角不被替换为 cosine_bell；Test D 正弦凸起不产生 rectangle；IntentCandidateSet.to_dict() 序列化 |
| `TestGeometryFidelity` | 4 | 三角保持三角（不触发 GEOMETRY_TYPE_MISMATCH）；三角变成 cosine_bell 时 BLOCKING；正弦凸起不产生 rectangle（DUPLICATE_ENTITY）；余弦凸起忠实性 |
| `TestSpatialRelations` | 4 | `centered_under` 空间关系违反时 SPATIAL_RELATION_VIOLATION；一致时通过；"正中央"域中心校验；偏离中心时 POSITION_CONFLICT 警告 |
| `TestGeometryIntersections` | 2 | 圆柱在域内通过；圆柱出域时产生违规/警告 |
| `TestBoundarySemantics` | 4 | 入口/出口配对通过；自由出流 vs no_slip 检测；周期边界成对；2D front/back=empty |
| `TestGuardResult` | 4 | 空结果通过；blocking 违规导致失败；warning 不导致失败；to_dict() 结构正确 |

**关键验证点**:
- Regex 和 LLM 候选独立提取，互不覆盖（`CandidateSource.REGEX` vs `CandidateSource.LLM`）
- 三角障碍不会被替换为 cosine_bell（Test C 核心不变量）
- 正弦凸起不会重复创建 rectangle 实体（Test D 核心不变量，`ConflictType.DUPLICATE_ENTITY`）
- source_span 可追踪（每个候选携带用户原文片段）
- 空间关系 `centered_under` 的数值校验（`obstacle.center_x == cylinder.center_x`）

### 3.2 test_error_repair.py（P3/P4 — 54 个测试）

**文件路径**: `tests/v5_closed_loop/test_error_repair.py`

覆盖 P3（smoke 失败阻断与错误分类）和 P4（受控错误修复闭环）。

| 测试类 | 测试数 | 验证内容 |
|--------|-------|---------|
| `TestErrorClassification` | 18 | 9 类 mock 日志参数化分类（mesh/BC/solver/physics/syntax/file/memory/timeout/unknown）；严重程度与可修复性矩阵；REPAIRABLE_CATEGORIES 集合完整性；文件/行号提取；Courant 数消息含 max 值；返回码 0 无错误；多错误同时返回 |
| `TestGetPrimaryError` | 4 | 空列表返回 None；优先可修复 FATAL；仅不可修复时返回 FATAL；仅非 FATAL 时返回首个 |
| `TestRepairPolicy` | 10 | 默认限制（per-phase=3, global=10）；级别递进 CONFIG_ONLY -> DICTIONARY_SYNTAX -> PARTIAL_REGENERATION；成功/失败/冻结状态；阶段冻结隔离其他阶段；全局上限优先于阶段冻结；尝试历史与 to_dict()；has_repair_been_attempted 标志 |
| `TestRepairContextBuilder` | 8 | 基本上下文键；无 spec 时省略 spec_summary；边界错误含障碍物几何；物理错误含仿真参数；文件内容截断 2000 字符；用户文本截断 500 字符；前次尝试保留最近 3 条；引用 raw_log |
| `TestRepairOrchestrator` | 9 | 不可修复错误跳过修复循环；无可分类错误返回失败；未知错误不可修复；Test F 无 LLM 时 3 轮后冻结；Test G 有 LLM 但无法执行时级别升级并冻结；Test F 成功路径（classify->context->diagnose->apply->validate）；全局上限传播；阶段映射；reset_policy 清空历史 |
| `TestNoRetryWithoutRepair` | 5 | has_repair_been_attempted 守卫首次重试；每个记录的尝试都有 fix_applied；循环迭代数等于记录尝试数（LLM 调用数=尝试数）；冻结阶段阻止所有重试；无修复建议路径仍先记录再重试（fix_applied="no_fixes_suggested"） |

**关键验证点**:
- **RETRY_WITHOUT_REPAIR 不变量**: 专属测试类 `TestNoRetryWithoutRepair` 确保"每次重试必须先有记录的修复尝试"
- **修复级别递进**: 第 1 次 CONFIG_ONLY -> 第 2 次 DICTIONARY_SYNTAX -> 第 3 次 PARTIAL_REGENERATION
- **阶段冻结隔离**: smoke 冻结不影响 mesh 和 full_run 阶段
- **全局上限优先**: 达到全局上限后所有阶段都被阻止
- **修复上下文裁剪**: 文件内容截断 2000 字符、用户文本截断 500 字符、前次尝试保留最近 3 条

### 3.3 test_skills.py（P5 — 39 个测试）

**文件路径**: `tests/v5_closed_loop/test_skills.py`

覆盖 P5（Skill 真实生效）。

| 测试类 | 测试数 | 验证内容 |
|--------|-------|---------|
| `TestManifestLoading` | 6 | 从 data/skills/ 加载 >= 10 个 YAML manifest；geometry_reasoning 字段完整（stage=geometry, priority=90, enabled=true）；所有 manifest 有非空 prompt_fragment；to_dict() 含 key 字段；未知 ID 返回 None；reload() 重新加载 |
| `TestSkillSelectionByKeyword` | 6 | "三角"选中 geometry_reasoning；"圆柱"选中 geometry_reasoning；"网格"选中 mesh_strategy；大小写不敏感；无关文本不选中；多关键词选中多个 skill |
| `TestSkillSelectionByGeometry` | 5 | triangle 选中 geometry_reasoning；cylinder 选中 mesh_strategy；cosine_bell 选中 geometry_reasoning；多几何类型选中多个 skill；未知几何不触发 |
| `TestBuildPromptInjection` | 6 | 三角输入返回非空注入；含 "## Skill 提供的领域知识" 头部；含 "几何推理规则" 片段内容；含 priority= 标注；无关输入返回空字符串；多 skill 合并注入 |
| `TestGetCompilerHooks` | 7 | 返回 dict 类型；cylinder 返回 mesh_refinement_cylinder=20_cells_per_diameter；triangle 返回 enforce_semantic_type=true；默认 compile stage 返回空；未知几何返回空；多几何合并钩子；重复钩子最高优先级胜出 |
| `TestRelevantSelection` | 5 | 三角障碍完整管线选择+注入；选中 skill 按优先级降序排序；stage 过滤限制匹配阶段；stage 过滤排除其他阶段；无 stage 包含所有阶段 |
| `TestDisabledSkill` | 4 | enabled=false 不被选中；disabled skill 不出现在 prompt 注入；disabled skill 不贡献编译器钩子；高优先级 disabled 仍被排除 |

**关键验证点**:
- Skill 真实进入 Prompt（`build_prompt_injection` 返回含 "几何推理规则" 和 "网格策略规则" 的非空字符串）
- Skill 影响编译器（`get_compiler_hooks` 返回 `mesh_refinement_cylinder`、`enforce_semantic_type` 等真实钩子）
- 禁用 Skill 后结果不同（`enabled=false` 的 skill 不会被选中、不会注入 prompt、不贡献钩子）
- 选中 skill 按优先级降序排序

### 3.4 test_capabilities.py（P6 — 38 个测试）

**文件路径**: `tests/v5_closed_loop/test_capabilities.py`

覆盖 P6（ExtensionOrchestrator 接入）。

| 测试类 | 测试数 | 验证内容 |
|--------|-------|---------|
| `TestCapabilityCheck` | 9 | 全支持 spec 返回 all_supported=True；cylinder/triangle/rectangle 几何检测；physics/observable/boundary 检测；bottom_profile 检测；to_dict() 含全部字段 |
| `TestSupportedGeometrySet` | 9 | SUPPORTED_GEOMETRY 含 cylinder/rectangle/triangle/cosine_bell/half_sine；SUPPORTED_PHYSICS 含 incompressible_newtonian/laminar/turbulent_k_omega_sst；SUPPORTED_BOUNDARIES 含常见边界；SUPPORTED_OBSERVABLES 含常见观测量 |
| `TestUnsupportedCapabilities` | 7 | 不支持的边界类型触发 unsupported；blocking_issues(UNSUPPORTED_CAPABILITY) 加入 unsupported；不支持的 bottom_profile 类型；extendable physics model（不使 all_supported=False）；extendable observable；多个 unsupported 项；非 UNSUPPORTED_CAPABILITY 的 blocking_issue 被忽略 |
| `TestExtend` | 7 | 全支持 spec 不创建 checkpoint；unsupported 创建 checkpoint；无 orchestrator 时 extension_triggered=False；有 mock orchestrator 时 extension_triggered=True；非 orchestrator 对象报告类型无效；extend 结果含 unsupported 和 extendable 列表；不修改 supported 列表 |
| `TestVerifiedExtensions` | 7 | 初始为空；未知能力 is_verified=False；手动标记后 True；失败扩展后保持空；模拟成功后填充；仅 extendable（非 unsupported）获验证跟踪；is_verified 与 supported 区分 |

**关键验证点**:
- SUPPORTED_GEOMETRY 包含全部 5 种几何类型
- 未知能力不映射到近似类型，而是进入 unsupported 或 extendable 列表
- 扩展流程：check -> checkpoint -> extend -> orchestrator -> verified
- extendable 能力不阻止 all_supported=True（仅 unsupported 阻止）

### 3.5 test_persistence.py（P7 — 36 个测试）

**文件路径**: `tests/v5_closed_loop/test_persistence.py`

覆盖 P7（持久化）。

| 测试类 | 测试数 | 验证内容 |
|--------|-------|---------|
| `TestSpecRoundtrip` | 5 | save/load 保留数据；不存在返回 None；同 ID 覆盖；draft_status 持久化；user_input 持久化 |
| `TestListSpecs` | 4 | 返回全部 spec；按 session 过滤；空 DB 返回空列表；返回元数据字段（spec_id/session_id/draft_status/created_at/updated_at） |
| `TestDeleteSpec` | 4 | 删除后 load 返回 None；删除后不在列表中；删除不存在的不报错；删除全部后列表为空 |
| `TestJobRoundtrip` | 6 | save/load 保留数据；不存在返回 None；同 ID 覆盖；无 result 保存；按 spec 列出 job；remote_case_path 持久化 |
| `TestLLMRecords` | 5 | save/list 保留数据；无 session 过滤返回全部；按 session 过滤；错误记录保留 error 文本；同 call_id 覆盖 |
| `TestRepairRecords` | 4 | save/list 保留数据（含 diagnosis/fixes JSON）；按 attempt_number 升序；无 diagnosis/fixes 时 NULL；未知 job 返回空列表 |
| `TestRecovery` | 5 | 重启后 recover_all_specs 恢复全部；新 DB 返回空；重启后 recover_jobs_for_spec 恢复 job；多 session 恢复；数据完整性保持 |
| `TestTempDbEdgeCases` | 3 | 初始化创建 DB 文件；spec/job/llm/repair 共存无干扰；删除 spec 不级联删除 job |

**关键验证点**:
- SQLite 持久化替代内存字典，服务重启后状态可恢复
- 四张核心表：specs、jobs、llm_records、repair_records
- recover_all_specs 模拟重启后全量恢复
- 删除 spec 不级联删除 job（无 FK 强制约束）

### 3.6 test_llm_report.py（P8 — 28 个测试）

**文件路径**: `tests/v5_closed_loop/test_llm_report.py`

覆盖 P8（LLM 科学报告和物理验证）。

| 测试类 | 测试数 | 验证内容 |
|--------|-------|---------|
| `TestResultSummaryBuilder` | 8 | 空输入 has_results=False；mesh_report 填充网格信息；成功仿真 has_results=True；失败仿真 has_results=False；Cd/Cl 日志解析；forceCoeffs 指标提取；Strouhal 估计（数据不足返回 None）；振荡 Cl 产生非零 Strouhal |
| `TestPhysicsValidator` | 12 | Stokes 区 Cd=24/Re；过渡区 Cd=10/sqrt(Re)+3；亚临界区 Cd=1.2；超临界区返回 None（阻力危机）；低 Re Strouhal=None；Roshko 公式 St=0.198(1-19.7/Re)；高 Re St=0.2；Cd 接近经验值时通过；NaN 时失败；网格<1000 失败；Cd 误差>30% 失败 |
| `TestLLMReportGenerator` | 7 | 无 LLM 时规则报告；失败仿真报告；含物理验证；含结果摘要；有 LLM client 时调用 LLM（report_source="llm"）；LLM 失败时降级规则报告；plot_paths 包含在结果摘要 |
| `TestReportWithSpec` | 1 | 报告含 spec 信息（Reynolds 数、入口速度） |

**关键验证点**:
- Cd/Cl 从 forceCoeffs 日志解析（正则匹配 `Cd = 1.234` 格式）
- Strouhal 从 Cl 时间序列零交叉频率估计
- 物理验证对比经验公式（Stokes/过渡区/亚临界区/Roshko）
- LLM 失败时降级为规则报告（report_source 从 "llm" 变为 "rule_based"）
- 报告只基于真实数据，不编造不存在的场

---

## 4. 反偷懒测试覆盖（第 15 节）

改造方案第 15 节定义了五类反偷懒测试，以下为各测试文件的覆盖情况：

### 4.1 输入与产物差异（15.1）

> 对 A、B、C、D 保存并比较：canonical spec hash、compiler input hash、case archive hash、blockMeshDict diff、snappyHexMeshDict diff、STL hash、boundary 文件、远程目录、结果文件 hash。

| 覆盖项 | 测试文件 | 测试类 / 方法 | 验证方式 |
|--------|---------|-------------|---------|
| Spec 数据完整性 | `test_persistence.py` | `TestRecovery.test_recover_preserves_spec_data_integrity` | 保存嵌套 dict（含 list/dict），重启后恢复，逐字段比对（spec_id/geometry/reynolds/nested.key/nested.list） |
| Spec 版本覆盖 | `test_persistence.py` | `TestSpecRoundtrip.test_save_spec_overwrites_on_same_id` | 同 spec_id 保存 cylinder -> triangle，load 后确认为 triangle（旧数据完全消失） |
| 场景间 Spec 差异 | `test_intent_conflict.py` | `TestCandidateSetIntegration.test_test_c_triangle_not_cosine_bell` vs `test_test_d_sine_bump_no_rectangle` | Test C（圆柱+三角）和 Test D（正弦凸起）产生完全不同的候选集和冲突结果 |
| 候选来源可追踪 | `test_intent_conflict.py` | `TestRegexCandidateExtractor.test_all_candidates_have_source_regex`、`TestLLMCandidateExtractor.test_all_candidates_have_source_llm` | 每个候选携带 `CandidateSource.REGEX` 或 `CandidateSource.LLM` 标记，不混用 |

### 4.2 模型真实性（15.2）

> 每次模型调用必须有：trace_id、stage、model、provider、latency、prompt_hash、raw_output_hash、parsed_output_hash、success、fallback。

| 覆盖项 | 测试文件 | 测试类 / 方法 | 验证方式 |
|--------|---------|-------------|---------|
| LLM 调用计数追踪 | `test_error_repair.py` | `TestNoRetryWithoutRepair.test_loop_iteration_count_matches_recorded_attempts` | `client.call_count == snap["attempt_count"]`，每次 LLM 调用对应一次记录的修复尝试 |
| LLM 调用成功/失败状态 | `test_llm_report.py` | `TestLLMReportGenerator.test_generate_report_with_llm_client_calls_llm`、`test_generate_report_llm_failure_falls_back` | 成功时 `report_source="llm"`；失败时降级 `report_source="rule_based"` |
| LLM 记录持久化 | `test_persistence.py` | `TestLLMRecords.test_save_and_list_llm_record`、`test_save_llm_record_with_error` | llm_records 表保存 call_id/session_id/purpose/model/prompt_name/latency_ms/success/fallback_used/error |
| LLM 诊断上下文 | `test_error_repair.py` | `TestRepairContextBuilder` | 每次诊断包含 stage、error.category、error.raw_log、user_original_input、spec_summary、files、previous_attempts |
| FakeLLMClient 记录 | `test_error_repair.py` | `_FakeLLMClient`、`_FakeLLMRecord` | mock client 返回 `(parsed_dict, record)` 二元组，record 携带 `success` 和 `error` 属性，模拟真实 LLM client 契约 |

### 4.3 Skill 真实性（15.3）

> Skill 必须有业务影响证据：selected、prompt injected、validator executed、compiler hook used、output affected。

| 覆盖项 | 测试文件 | 测试类 / 方法 | 验证方式 |
|--------|---------|-------------|---------|
| Skill 被选中 | `test_skills.py` | `TestSkillSelectionByKeyword.test_triangle_keyword_selects_geometry_reasoning` | "三角"输入选中 `fluid.geometry_reasoning` |
| Prompt 被注入 | `test_skills.py` | `TestBuildPromptInjection.test_injection_contains_prompt_fragment_content` | 注入文本包含 "几何推理规则" 实际片段内容 |
| 编译器钩子被使用 | `test_skills.py` | `TestGetCompilerHooks.test_cylinder_geometry_returns_mesh_hooks` | cylinder 几何返回 `mesh_refinement_cylinder=20_cells_per_diameter` |
| 禁用后结果不同 | `test_skills.py` | `TestDisabledSkill.test_disabled_skill_not_selected`、`test_disabled_skill_not_in_prompt_injection`、`test_disabled_skill_not_in_compiler_hooks` | enabled=false 的 skill 不被选中、不注入 prompt、不贡献钩子（与 enabled=true 行为不同） |
| 多 Skill 合并 | `test_skills.py` | `TestBuildPromptInjection.test_injection_combines_multiple_skills` | "圆柱绕流网格生成" 同时注入 "几何推理规则" 和 "网格策略规则" |

### 4.4 Repair 真实性（15.4）

> 每次修复必须存在：错误日志、分类、诊断、repair plan、文件 diff、重试命令、新结果。无 diff 不得计为修复。

| 覆盖项 | 测试文件 | 测试类 / 方法 | 验证方式 |
|--------|---------|-------------|---------|
| 错误日志 -> 分类 | `test_error_repair.py` | `TestErrorClassification.test_primary_error_classification` | 9 类 mock 日志逐一分类，断言 category/severity/is_repairable |
| 分类 -> 诊断 | `test_error_repair.py` | `TestRepairOrchestrator.test_test_f_freezes_when_no_llm_available` | diagnosis_history 保存每轮诊断结果，含 root_cause 和 fixes |
| 诊断 -> 修复计划 | `test_error_repair.py` | `TestRepairOrchestrator.test_test_f_success_path` | fixes_applied 列表非空，含 file/parameter/old_value/new_value/reason |
| 修复 -> 文件 diff | `test_error_repair.py` | `TestNoRetryWithoutRepair.test_every_recorded_attempt_has_documented_fix` | 每个 attempt 的 `fix_applied` 非空且不等于 "retry_without_repair" 或空字符串 |
| 修复记录持久化 | `test_persistence.py` | `TestRepairRecords.test_save_and_list_repair_record` | repair_records 表保存 job_id/attempt_number/phase/level/diagnosis_json/fixes_json/status |
| 无 diff 不计为修复 | `test_error_repair.py` | `TestNoRetryWithoutRepair.test_no_fixes_path_still_records_before_retrying` | LLM 返回空 fixes 时，fix_applied 标记为 "no_fixes_suggested"（显式记录，非静默重试） |

### 4.5 OpenFOAM 真实性（15.5）

> 必须保存：SSH 命令、OpenFOAM 环境、blockMesh 日志、checkMesh 日志、foamRun 时间步、当前 job 结果、文件时间戳。

| 覆盖项 | 测试文件 | 测试类 / 方法 | 验证方式 |
|--------|---------|-------------|---------|
| Mock 日志仿真真实 OpenFOAM 输出 | `test_error_repair.py` | `LOG_BLOCKMESH_FAIL`、`LOG_PATCH_MISMATCH_TEST_F`、`LOG_NAN_STANDALONE_TEST_G` 等 | mock 日志基于真实 OpenFOAM stderr 格式（`FOAM FATAL ERROR`、`FOAM FATAL IO ERROR`、`Courant Number mean: ... max: ...`、`Return code: N`） |
| 文件/行号提取 | `test_error_repair.py` | `TestErrorClassification.test_extracts_file_and_line_number` | 从 `file: mesh/blockMesh/blockMesh.C line: 124` 提取 file_path 和 line_number |
| Job 结果持久化 | `test_persistence.py` | `TestJobRoundtrip.test_save_and_load_job` | job 保存 status/result/remote_case_path，重启后可恢复 |
| 修复级别逐级升级 | `test_error_repair.py` | `TestRepairOrchestrator.test_test_g_freezes_when_executor_cannot_apply` | 级别序列 `["config_only", "dictionary_syntax", "partial_regeneration"]` 可见于 policy snapshot |
| remote_case_path 持久化 | `test_persistence.py` | `TestJobRoundtrip.test_save_job_with_remote_case_path` | 远程 Case 路径 `/tmp/cases/case_6` 保存并可加载 |

---

## 5. 测试执行方式

```bash
# 运行全部 V5 闭环测试
pytest tests/v5_closed_loop/ -v

# 运行特定优先级的测试
pytest tests/v5_closed_loop/test_intent_conflict.py -v    # P1/P2
pytest tests/v5_closed_loop/test_error_repair.py -v       # P3/P4
pytest tests/v5_closed_loop/test_skills.py -v             # P5
pytest tests/v5_closed_loop/test_capabilities.py -v       # P6
pytest tests/v5_closed_loop/test_persistence.py -v        # P7
pytest tests/v5_closed_loop/test_llm_report.py -v         # P8
```

测试不依赖运行中的服务器、真实 GLM API 或真实 OpenFOAM 安装。所有外部依赖通过以下方式隔离：

- **LLM**: 使用 `_FakeLLMClient`（test_error_repair.py）和 `FakeLLMClient`（test_llm_report.py）模拟 LLM client 契约
- **OpenFOAM**: 使用 mock 日志字符串（如 `LOG_PATCH_MISMATCH_TEST_F`、`LOG_NAN_STANDALONE_TEST_G`）模拟真实 OpenFOAM stderr 输出
- **Spec**: 使用 `SimpleNamespace` 构建的 duck-typed mock spec，兼容真实 Pydantic 模型的接口
- **数据库**: 使用 pytest `tmp_path` fixture 创建临时 SQLite 文件，测试间无干扰

---

## 6. 已知限制与后续工作

| 项目 | 当前状态 | 后续工作 |
|------|---------|---------|
| 真实 GLM API 测试 | 测试使用 mock LLM client | 生产环境需配置真实 GLM API key，执行手动集成测试并记录 model/latency/raw_response/parsed_result/token_cost/accuracy |
| 真实 OpenFOAM 测试 | 测试使用 mock 日志字符串 | 需在工作站 OpenFOAM Foundation v13 上执行真实端到端测试，每次生成独立目录 |
| 编译产物 hash 对比 | 测试验证 spec 数据完整性 | 需在真实编译后比较 blockMeshDict diff、STL hash、boundary 文件 hash |
| SSH 命令记录 | 测试验证 remote_case_path 持久化 | 需在真实执行中保存 SSH 命令、OpenFOAM 环境变量、blockMesh/checkMesh 日志 |
