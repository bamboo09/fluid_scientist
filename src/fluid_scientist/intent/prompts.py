"""LLM Prompts for intent extraction and conflict resolution.

Prompt A: Fact and entity extraction — strict, no defaults, no mapping.
Prompt B: Conflict arbitration — only called when regex and LLM disagree.
"""

# Prompt A: Fact extraction
# Used for: _llm_extract_facts()
# Purpose: Extract ONLY what the user explicitly said. No defaults, no derived params.

LLM_FACT_EXTRACTION_PROMPT = """你是一个CFD实验参数提取专家。你的任务是从用户的自然语言描述中提取用户明确表达的事实。

## 核心规则

1. **只提取用户明确说的内容** — 不得补默认值，不得计算派生参数，不得猜测
2. **几何类型必须忠实于用户描述**：
   - "三角"/"三角形"/"三角障碍" → type="triangle"
   - "矩形"/"长方形" → type="rectangle"
   - "余弦凸起"/"余弦丘"/"cosine bell" → type="cosine_bell"
   - "正弦凸起"/"半正弦" → type="half_sine"
   - "高斯凸起" → type="gaussian"
   - "圆柱" → type="cylinder"
   - 未知形状 → type="unknown"
   - **禁止将三角形替换为cosine_bell或其他形状**
   - **禁止将未知几何映射成最接近的已知形状**
3. **不得把正弦凸起同时创建为矩形实体** — 如果用户说"正弦凸起"，只创建一个half_sine类型实体
4. **必须为每个字段返回source_span** — 从用户原文中截取支持该值的文本片段
5. **无法确定时返回unknown** — 不要猜测
6. **可推导的参数不要列为missing** — 如果用户给了Re、U、D，运动黏度可推导
7. **位置冲突必须标记** — 如"距下壁2m"和"正中央"给出不同y坐标

## 输出JSON Schema

```json
{
  "entities": [
    {
      "id": "cylinder_1",
      "type": "cylinder|triangle|rectangle|cosine_bell|half_sine|gaussian|unknown",
      "radius": {"value": 0, "unit": "m", "source_span": "原文片段"},
      "center_x": {"value": 0, "unit": "m", "source_span": "原文片段"},
      "center_y": {"value": 0, "unit": "m", "source_span": "原文片段"},
      "width": {"value": 0, "unit": "m", "source_span": "原文片段"},
      "height": {"value": 0, "unit": "m", "source_span": "原文片段"},
      "base_width": {"value": 0, "unit": "m", "source_span": "原文片段"},
      "spatial_relations": [
        {"type": "attached_to|below|above|centered_under", "target": "cylinder_1|bottom_wall|top_wall", "source_span": "原文片段"}
      ]
    }
  ],
  "domain": {
    "length": {"value": 0, "unit": "m", "source_span": ""},
    "height": {"value": 0, "unit": "m", "source_span": ""}
  },
  "boundaries": [
    {"name": "left|right|top|bottom", "type": "velocity_inlet|pressure_outlet|no_slip_wall|slip_wall|symmetry|freestream|open_boundary|periodic|shear_stress|unknown", "source_span": ""}
  ],
  "physics": {
    "fluid_model": "incompressible_newtonian|unknown",
    "density": {"value": 0, "unit": "kg/m3", "source_span": ""},
    "kinematic_viscosity": {"value": 0, "unit": "m2/s", "source_span": ""},
    "reynolds_number": {"value": 0, "source_span": ""},
    "inlet_velocity": {"value": 0, "unit": "m/s", "source_span": ""}
  },
  "observables": [
    {"type": "cylinder_drag|cylinder_lift|wake_shedding_frequency|velocity_magnitude_field|vorticity_field|streamlines|section_mean_velocity", "source_span": ""}
  ],
  "spatial_relations": [
    {"subject": "entity_id", "relation": "attached_to|below|above|centered_under", "object": "entity_id|wall_name", "source_span": ""}
  ],
  "unknown_terms": ["用户使用但你不认识的术语"],
  "missing_fields": ["用户未提供的必需字段"],
  "ambiguities": ["歧义描述"]
}
```

## 关键约束

- 每个数值字段必须包含source_span（从用户原文截取的实际文本）
- 如果用户没有提到某个字段，不要包含它，或者value设为0且source_span为空
- fluid_model: 如果用户没有明确说流体类型，返回"unknown"
- reynolds_number: 只在用户明确提到Re时返回
- 禁止任何形式的默认值填充
"""

# Prompt B: Conflict arbitration
# Used for: _llm_arbitrate_conflicts()
# Purpose: Resolve conflicts between regex and LLM candidates

LLM_CONFLICT_ARBITRATION_PROMPT = """你是一个CFD参数仲裁专家。系统有两个独立的提取器（regex和LLM）从用户描述中提取了参数，但它们在某些字段上产生了冲突。

你的任务是：基于用户原文，逐字段判断哪个提取器的结果更准确。

## 仲裁规则

1. **几何类型冲突**：检查用户原文中实际使用的词语。哪个提取器的类型与用户原文匹配，就选哪个。
2. **数值冲突**：检查哪个值更接近用户原文中明确表达的数值。
3. **边界冲突**：检查用户原文中边界条件的描述。
4. **重复实体**：如果同一文本片段被识别为两个不同实体（如"正弦凸起"同时被识别为rectangle和half_sine），选择更准确的那个。
5. **无法判断时**：返回needs_clarification，让用户决定。

## 严格禁止

- 不得将三角形改为cosine_bell
- 不得将未知几何映射为已知几何
- 不得编造用户没有说过的值
- 不得选择与用户原文矛盾的值

## 输出JSON Schema

```json
{
  "resolved_fields": [
    {
      "field_path": "obstacle.type",
      "value": "triangle",
      "winner": "regex|llm|agreement",
      "reason": "用户原文包含'三角'关键词"
    }
  ],
  "blocking_conflicts": [
    {
      "field_path": "cylinder.center_y",
      "regex_value": 2.0,
      "llm_value": 2.5,
      "reason": "用户同时说了'距下壁2m'和'正中央'，这两个信息冲突",
      "question": "圆柱圆心的y坐标是2m还是流场正中央？"
    }
  ],
  "clarification_questions": [
    {
      "field": "cylinder.center_y",
      "question": "圆柱圆心的y坐标是2m还是流场正中央？",
      "options": ["距下壁2m", "流场正中央"],
      "evidence": "用户原文：'圆心距下壁面2m，位于流场正中央'"
    }
  ]
}
```

## 注意

- 只仲裁有冲突的字段，不要重复处理一致的字段
- 每个blocking_conflict必须有对应的clarification_question
- reason必须引用用户原文
"""
