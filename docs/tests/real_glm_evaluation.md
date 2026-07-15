# GLM 模型调用评估文档

## 1. 模型调用点矩阵

V5 智能闭环改造在主链中引入了多个 LLM 调用点，覆盖意图理解、冲突仲裁、Skill 注入、错误诊断和报告生成五大环节。以下为所有调用点的完整矩阵：

| 调用点 | 所处阶段 | 目的 | prompt_name | system_prompt 来源 | output_schema | 代码位置 |
|--------|---------|------|------------|-------------------|--------------|---------|
| 意图事实提取 | 意图理解（P1） | 从用户自然语言中提取明确表达的事实和实体，不补默认值 | `intent_v2` | `LLM_FACT_EXTRACTION_PROMPT` | entities/boundaries/physics/observables/spatial_relations/unknown_terms/missing_fields/ambiguities | `src/fluid_scientist/intent/prompts.py` |
| 冲突仲裁 | 意图理解（P1） | 当 regex 和 LLM 候选在字段上产生冲突时，基于用户原文逐字段判断 | `conflict_arbitration` | `LLM_CONFLICT_ARBITRATION_PROMPT` | resolved_fields/blocking_conflicts/clarification_questions | `src/fluid_scientist/intent/prompts.py` |
| Skill 注入 | 编译前（P5） | 根据 spec 选中 Skill，将 prompt_fragment 注入 LLM 上下文 | N/A（确定性规则） | Skill YAML manifest 中的 `prompt_fragment` 字段 | N/A（注入文本，非 LLM 调用） | `src/fluid_scientist/skills/skill_resolver.py` |
| 错误诊断 | 修复闭环（P4） | 分析 OpenFOAM 错误日志，找出根因并提出具体修复方案 | `of_error_diagnosis` | `_DIAGNOSIS_SYSTEM_PROMPT` | root_cause/error_category/fix_strategy/fixes/regenerate_files/confidence/warnings | `src/fluid_scientist/repair/llm_diagnoser.py` |
| 科学报告生成 | 结果分析（P8） | 基于仿真结果摘要和物理验证，生成结构化科学报告 | `scientific_report` | `_REPORT_SYSTEM_PROMPT` | summary/results/conclusions/confidence | `src/fluid_scientist/analysis/llm_report.py` |

### 1.1 意图事实提取（Prompt A）

**调用位置**: `LLMCandidateExtractor.extract()` -> `client.call()`

**system_prompt 来源**: `src/fluid_scientist/intent/prompts.py` 中的 `LLM_FACT_EXTRACTION_PROMPT`

**核心规则**:
- 只提取用户明确说的内容，不得补默认值，不得计算派生参数
- 几何类型必须忠实于用户描述（三角->triangle、正弦凸起->half_sine）
- 禁止将三角形替换为 cosine_bell 或其他形状
- 禁止将未知几何映射成最接近的已知形状
- 不得把正弦凸起同时创建为矩形实体
- 必须为每个字段返回 source_span
- 无法确定时返回 unknown

**output_schema**:
```json
{
  "entities": [{"id": "", "type": "", "radius": {"value": 0, "source_span": ""}, ...}],
  "domain": {"length": {"value": 0, "source_span": ""}, "height": {"value": 0, "source_span": ""}},
  "boundaries": [{"name": "", "type": "", "source_span": ""}],
  "physics": {"fluid_model": "", "reynolds_number": {"value": 0, "source_span": ""}, ...},
  "observables": [{"type": "", "source_span": ""}],
  "spatial_relations": [{"subject": "", "relation": "", "object": "", "source_span": ""}],
  "unknown_terms": [],
  "missing_fields": [],
  "ambiguities": []
}
```

**调用参数**:
```
temperature = 0
response_format = JSON
model name = GLM-4-Flash（生产环境）
provider = GLM（生产环境）
```

### 1.2 冲突仲裁（Prompt B）

**调用位置**: `ConflictResolver.resolve()` -> `client.call()`（仅在存在冲突时调用）

**system_prompt 来源**: `src/fluid_scientist/intent/prompts.py` 中的 `LLM_CONFLICT_ARBITRATION_PROMPT`

**输入**: 用户原文、regex candidates、LLM candidates、当前 Schema、支持能力清单、几何和边界冲突规则

**output_schema**:
```json
{
  "resolved_fields": [{"field_path": "", "value": "", "winner": "regex|llm|agreement", "reason": ""}],
  "blocking_conflicts": [{"field_path": "", "regex_value": 0, "llm_value": 0, "reason": "", "question": ""}],
  "clarification_questions": [{"field": "", "question": "", "options": [], "evidence": ""}]
}
```

**调用条件**: 仅在 regex 和 LLM 候选在某个字段上产生冲突时调用，避免无意义增加成本。

### 1.3 Skill 注入

**调用位置**: `SkillResolver.build_prompt_injection()`

**性质**: 这不是一次 LLM 调用，而是确定性规则匹配。SkillResolver 从 `data/skills/*.yaml` 加载 SkillManifest，根据用户文本关键词和几何类型选择 skill，将选中 skill 的 `prompt_fragment` 拼接为注入文本。

**注入文本格式**:
```text
## Skill 提供的领域知识

### [skill_name] (priority=90)
[prompt_fragment 内容]

### [skill_name] (priority=70)
[prompt_fragment 内容]
```

**已加载的 Skill YAML 文件**（`data/skills/` 目录，>= 10 个）:

| Skill ID | stage | priority | 关键词 | 钩子 |
|----------|-------|----------|--------|------|
| `fluid.geometry_reasoning` | geometry | 90 | 三角、圆柱、余弦丘 | enforce_semantic_type=true, prevent_geometry_substitution |
| `fluid.mesh_strategy` | mesh | 70 | 网格 | mesh_refinement_cylinder=20_cells_per_diameter |
| `fluid.boundary_mapping` | - | - | - | - |
| `fluid.error_diagnosis` | - | - | - | - |
| `fluid.intent_to_spec` | - | - | - | - |
| `fluid.metric_spec_builder` | - | - | - | - |
| `fluid.physics_derivation` | - | - | - | - |
| `fluid.postprocess_config` | - | - | - | - |
| `fluid.report_generation` | - | - | - | - |
| `fluid.solver_selection` | - | - | - | - |
| `fluid.spatial_reasoning` | - | - | - | - |

### 1.4 错误诊断

**调用位置**: `LLMDiagnoser.diagnose()` -> `client.call()`

**system_prompt 来源**: `src/fluid_scientist/repair/llm_diagnoser.py` 中的 `_DIAGNOSIS_SYSTEM_PROMPT`

**核心规则**:
- 基于错误日志诊断，只根据实际错误信息提出修复，不得猜测
- 具体到文件和行，修复方案必须指明修改哪个文件的哪个参数
- 不改变用户意图（几何类型、边界条件语义等）
- 最小化修改，只修改导致错误的最小必要内容
- 禁止建议将 triangle 改为 cosine_bell 或其他几何类型
- 禁止建议改变用户明确指定的边界条件类型
- 不得返回空修复方案

**output_schema**:
```json
{
  "root_cause": "根本原因描述",
  "error_category": "mesh_error|boundary_condition_error|solver_error|physics_error|syntax_error|file_error",
  "fix_strategy": "config_only|dictionary_syntax|partial_regeneration",
  "fixes": [
    {
      "file": "system/controlDict",
      "parameter": "deltaT",
      "old_value": "0.01",
      "new_value": "0.001",
      "reason": "CFL number too high, reduce time step"
    }
  ],
  "regenerate_files": [],
  "confidence": 0.8,
  "warnings": []
}
```

**调用参数**:
```
purpose = "explanation"
prompt_name = "of_error_diagnosis"
prompt_version = "repair-diag-v1"
```

**诊断消息构建** (`_build_diagnosis_message`):
1. 错误信息（error_message + category + stage）
2. 错误日志（最后 500 字符）
3. 当前 Spec 摘要（JSON）
4. 相关文件内容（按错误类型裁剪）
5. 之前的修复尝试（最近 3 条，避免重复）
6. 用户原始输入

### 1.5 科学报告生成

**调用位置**: `LLMReportGenerator._call_llm_for_report()` -> `client.call()`

**system_prompt 来源**: `src/fluid_scientist/analysis/llm_report.py` 中的 `_REPORT_SYSTEM_PROMPT`

**核心规则**:
- 所有数值必须来自实际仿真结果，不得编造
- 如果数据缺失，明确标注"数据不可用"
- 物理验证必须对比经验值并计算误差百分比
- 置信度基于网格质量、收敛性、数据完整性综合评估

**output_schema**:
```json
{
  "summary": "一句话概述仿真结果",
  "experiment_overview": {},
  "mesh_info": {},
  "numerical_method": {},
  "results": {
    "Cd": {"mean": 0, "amplitude": 0, "empirical": 0, "error_percent": 0},
    "Cl": {"mean": 0, "amplitude": 0, "frequency": 0},
    "Strouhal": {"value": 0, "empirical": 0, "error_percent": 0}
  },
  "physics_validation": {"passed": true, "checks": []},
  "conclusions": [],
  "confidence": 0.0
}
```

**调用参数**:
```
purpose = "explanation"
prompt_name = "scientific_report"
prompt_version = "report-v1"
```

**降级机制**: 当 LLM client 为 None 或调用失败时，自动降级为 `_rule_based_report()`，`report_source` 标记为 `"rule_based"` 而非 `"llm"`。

---

## 2. 模型调用记录（llm_records 表）

所有 LLM 调用通过 `SQLitePersistence.save_llm_record()` 持久化到 SQLite 数据库的 `llm_records` 表中。

### 2.1 表结构

**代码位置**: `src/fluid_scientist/persistence/store.py`

```sql
CREATE TABLE IF NOT EXISTS llm_records (
    call_id TEXT PRIMARY KEY,          -- 调用唯一标识
    session_id TEXT,                    -- 会话 ID
    purpose TEXT,                       -- 调用目的（intent_parsing/explanation）
    model TEXT,                         -- 模型名称（如 GLM-4-Flash）
    prompt_name TEXT,                   -- Prompt 名称（intent_v2/of_error_diagnosis/scientific_report）
    prompt_version TEXT,                -- Prompt 版本（repair-diag-v1/report-v1）
    input_summary TEXT,                 -- 输入摘要（截断 500 字符）
    output_summary TEXT,                -- 输出摘要（截断 500 字符）
    latency_ms REAL,                    -- 延迟（毫秒）
    success INTEGER DEFAULT 0,          -- 是否成功（0/1）
    fallback_used INTEGER DEFAULT 0,    -- 是否使用了降级（0/1）
    error TEXT,                         -- 错误信息
    created_at TEXT NOT NULL            -- 创建时间
);

CREATE INDEX IF NOT EXISTS idx_llm_session ON llm_records(session_id);
```

### 2.2 记录字段与改造方案第 15.2 节的对应

改造方案第 15.2 节要求每次模型调用必须记录以下字段：

| 方案要求字段 | llm_records 表字段 | 说明 |
|-------------|-------------------|------|
| trace_id | call_id | 调用唯一标识 |
| stage | purpose | 调用所处阶段/目的 |
| model | model | 模型名称 |
| provider | （需通过 model 或 session 上下文推断） | 当前表未单独存储 provider 字段 |
| latency | latency_ms | 延迟毫秒数 |
| prompt_hash | prompt_name + prompt_version | Prompt 名称和版本组合标识 |
| raw_output_hash | output_summary | 输出摘要（截断 500 字符） |
| parsed_output_hash | output_summary | 与 raw_output 共用摘要字段 |
| success | success | 是否成功 |
| fallback | fallback_used | 是否使用降级 |

### 2.3 测试覆盖

`test_persistence.py` 中的 `TestLLMRecords` 类验证了 llm_records 表的完整 CRUD 操作：

| 测试方法 | 验证内容 |
|---------|---------|
| `test_save_and_list_llm_record` | 保存含全部字段的记录，列表查询后逐字段断言（call_id/session_id/purpose/model/prompt_name/latency_ms/success） |
| `test_list_llm_records_all_sessions` | 不传 session_id 时返回全部记录 |
| `test_list_llm_records_filtered_by_session` | 按 session_id 过滤 |
| `test_save_llm_record_with_error` | success=0、fallback_used=1、error="Rate limit exceeded" 的错误记录 |
| `test_llm_record_overwrites_on_same_call_id` | 同 call_id 覆盖（model 从 model_a 变为 model_b，success 从 0 变为 1） |

---

## 3. 测试中使用的 Mock LLM Client

### 3.1 test_error_repair.py 中的 _FakeLLMClient

**代码位置**: `tests/v5_closed_loop/test_error_repair.py`

```python
class _FakeLLMClient:
    """模拟 LLMDiagnoser 使用的 client.call() 契约。

    返回可配置的 diagnosis dict，其 fixes 列表可自定义，
    使修复循环可以同时测试"有修复建议"和"无修复建议"两条分支。
    """
    def __init__(self, fixes=None, success=True, root_cause="diagnosed root cause"):
        self._fixes = _DEFAULT_FIXES if fixes is None else fixes
        self._success = success
        self._root_cause = root_cause
        self.call_count = 0

    def call(self, **kwargs):
        self.call_count += 1
        parsed = {
            "root_cause": self._root_cause,
            "error_category": "physics_error",
            "fix_strategy": "config_only",
            "fixes": self._fixes,
            "confidence": 0.8,
            "warnings": [],
        }
        return parsed, _FakeLLMRecord(success=self._success)
```

**_FakeLLMRecord** 模拟真实 LLM client 返回的 record 对象：
```python
class _FakeLLMRecord:
    def __init__(self, success=True, error=None):
        self.success = success
        self.error = error
```

**使用场景**:
- `test_test_g_freezes_when_executor_cannot_apply`: 有 LLM 但无工作站执行器 -> 3 轮后冻结
- `test_test_f_success_path`: 有 LLM 且有 fake 执行器 -> 成功路径
- `test_global_limit_propagated_from_executor`: 低全局上限 -> GLOBAL_LIMIT_REACHED
- `test_loop_iteration_count_matches_recorded_attempts`: 验证 call_count == attempt_count
- `test_no_fixes_path_still_records_before_retrying`: fixes=[] -> "no_fixes_suggested" 路径

### 3.2 test_llm_report.py 中的 FakeLLMClient

**代码位置**: `tests/v5_closed_loop/test_llm_report.py`

```python
# 成功路径
class FakeLLMClient:
    def call(self, **kwargs):
        return {"summary": "LLM generated"}, type("Record", (), {"success": True})()

# 失败路径
class FailingLLMClient:
    def call(self, **kwargs):
        raise RuntimeError("LLM unavailable")
```

**使用场景**:
- `test_generate_report_with_llm_client_calls_llm`: 成功时 report_source="llm"
- `test_generate_report_llm_failure_falls_back`: 异常时降级 report_source="rule_based"

### 3.3 Mock Client 契约

两个测试文件中的 mock client 都遵循相同的 `client.call(**kwargs)` 契约，返回 `(parsed_dict, record)` 二元组：

| 参数 | 类型 | 说明 |
|------|------|------|
| `purpose` | str | 调用目的（"explanation" / "intent_parsing"） |
| `prompt_name` | str | Prompt 名称 |
| `system_prompt` | str | System prompt 文本 |
| `user_message` | str | 用户消息 |
| `output_schema` | dict | 期望输出的 JSON Schema |
| `session_id` | str | 会话 ID |
| `prompt_version` | str | Prompt 版本 |

**返回值**:
- `parsed`: 解析后的 dict（符合 output_schema）
- `record`: 记录对象，含 `.success`（bool）和 `.error`（str | None）属性

---

## 4. 生产环境真实 GLM 配置

### 4.1 当前状态

改造方案第 4.5 节要求：

> 意图抽取使用真实 GLM，显式配置 temperature=0, response_format=JSON, timeout, retry, model name, provider。不得使用默认随机参数。

改造方案第 12.3 节要求：

> 生产环境默认值不能是 mock。所有生产创建必须显式传入 provider。生产环境发现 provider=mock 时直接启动失败（PRODUCTION_LLM_PROVIDER_INVALID）。

### 4.2 生产配置要求

| 配置项 | 要求值 | 说明 |
|--------|-------|------|
| model | GLM-4-Flash | 智谱 GLM-4-Flash 模型 |
| provider | GLM（非 mock） | 生产环境禁止使用 mock provider |
| temperature | 0 | 确定性输出，避免随机性 |
| response_format | JSON | 结构化输出 |
| timeout | 显式指定 | 避免无限等待 |
| retry | 显式指定 | 自动重试机制 |
| API key | 环境变量 | 禁止明文存储在 data/llm_config.json |

### 4.3 API Key 安全要求

改造方案第 12.1 节要求：

- 移除 `data/llm_config.json` 中的明文 Key
- 改为环境变量或本地安全配置
- 任何日志不得输出 Key

### 4.4 LLM 失败行为

改造方案第 4.6 节要求：

当前静默 fallback 必须删除。LLM 失败后：

1. 保存错误
2. 标记 `llm_status=FAILED`
3. 前端显示"模型理解服务暂时失败"
4. 允许用户选择"仅使用规则草案继续"
5. 不得用户无感知地自动降级

只有用户明确选择规则模式，才继续。

### 4.5 真实模型测试要求

改造方案第 13.2 节要求测试分两类：

| 测试类型 | 环境 | 要求 |
|---------|------|------|
| CI 默认 | 固定录制响应 | 验证稳定逻辑（当前 247 个测试覆盖此类型） |
| 手动集成测试 | 真实 GLM API | 必须记录 model/latency/raw_response/parsed_result/token_cost/accuracy |

> 不得声称 mock 测试证明真实 LLM 可用。

### 4.6 从测试到生产的迁移清单

| 步骤 | 当前测试状态 | 生产迁移工作 |
|------|------------|-------------|
| 1. 意图事实提取 | mock LLM JSON 输出（`_make_llm_parsed_triangle`） | 配置真实 GLM API key，使用 `LLM_FACT_EXTRACTION_PROMPT` 调用 GLM-4-Flash |
| 2. 冲突仲裁 | `ConflictResolver` 使用规则仲裁（无 LLM 调用测试） | 在 regex/LLM 冲突时调用 `LLM_CONFLICT_ARBITRATION_PROMPT` |
| 3. Skill 注入 | 确定性规则匹配（无需 LLM） | 直接使用，无需迁移 |
| 4. 错误诊断 | `_FakeLLMClient` 返回固定 diagnosis | 替换为真实 GLM client，使用 `_DIAGNOSIS_SYSTEM_PROMPT` |
| 5. 科学报告 | `FakeLLMClient` 返回固定 report | 替换为真实 GLM client，使用 `_REPORT_SYSTEM_PROMPT` |
| 6. 调用记录 | `save_llm_record` 测试验证表结构 | 生产环境每次 LLM 调用后写入 llm_records 表 |
| 7. 降级机制 | 测试验证 LLM 失败时降级为 rule_based | 生产环境实现显式降级（非静默），用户需明确选择规则模式 |
