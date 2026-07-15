# 意图候选提取与冲突仲裁设计文档

> **核心原则**: regex 和 LLM 产生独立候选，在候选阶段不直接互相覆盖。一个 `ConflictResolver` 逐字段基于证据、来源优先级和语义忠实性进行仲裁。每个字段值都携带 `source_span`，实现从用户原文到最终 Spec 的全链路可追踪。

---

## 目录

1. [双候选提取流程](#1-双候选提取流程)
2. [字段级仲裁规则](#2-字段级仲裁规则)
3. [重复实体检测](#3-重复实体检测)
4. [几何同义词表](#4-几何同义词表)
5. [SemanticFidelityGuard 四类检查](#5-semanticfidelityguard-四类检查)

---

## 1. 双候选提取流程

### 1.1 流程概览

```text
user_text（用户原始输入）
  ↓
  ├──→ regex_extract_candidates()     → regex_candidates: list[ExtractionCandidate]
  │                                     独立执行，不受 LLM 影响
  │
  └──→ llm_extract_candidates()       → llm_candidates: list[ExtractionCandidate]
                                        独立执行，不受 regex 影响
  ↓
resolve_candidates(regex_candidates, llm_candidates, user_text)
  ↓
IntentCandidateSet
  ├── regex_candidates    → 原始 regex 候选（保留）
  ├── llm_candidates      → 原始 LLM 候选（保留）
  ├── conflicts           → 检测到的冲突列表
  ├── unresolved          → 无法自动解决的字段
  └── resolved_fields     → 已解决字段（含来源追踪）
  ↓
Canonical Experiment Spec
```

**源文件**: `src/fluid_scientist/intent/conflict_resolver.py` → `ConflictResolver.resolve()`

### 1.2 候选模型

每个候选值都是一个 `ExtractionCandidate`，携带完整的来源信息:

```python
# 源文件: src/fluid_scientist/intent/__init__.py

@dataclass
class ExtractionCandidate:
    field_path: str          # 点分路径，如 "domain.length"、"obstacle.type"
    value: Any               # 提取的值
    source: CandidateSource  # 来源: REGEX | LLM | USER | FORMULA | DEFAULT
    source_span: str | None  # 用户原文中支持该值的文本片段
    confidence: float        # 0.0 到 1.0
    reasoning_summary: str | None  # LLM 候选的推理摘要
```

`CandidateSource` 枚举:

| 值 | 说明 |
|----|------|
| `REGEX` | 来自正则表达式提取器 |
| `LLM` | 来自大模型结构化解析 |
| `USER` | 用户手动确认或修改 |
| `FORMULA` | 物理公式推导（如 nu = U*D/Re） |
| `DEFAULT` | 系统默认值 |

### 1.3 Regex 候选提取器

**源文件**: `src/fluid_scientist/intent/conflict_resolver.py` → `RegexCandidateExtractor`

`RegexCandidateExtractor.extract(spec, user_text)` 从 pipeline 产出的 `CylinderFlow2DExperimentSpecV1` 中提取 regex 候选。提取覆盖以下字段:

| 字段路径 | 说明 | 置信度 |
|----------|------|--------|
| `domain.length` | 流场长度 | 0.95（用户提供）/ 0.7（默认） |
| `domain.height` | 流场高度 | 0.95 / 0.7 |
| `cylinder.radius` | 圆柱半径 | 0.95 / 0.7 |
| `cylinder.center_x` | 圆柱圆心 X | 0.9 / 0.6 |
| `cylinder.center_y` | 圆柱圆心 Y | 0.9 / 0.6 |
| `obstacle.type` | 障碍物类型（triangle/rectangle/bump） | 0.95 |
| `triangle.base_width` | 三角形底宽 | 0.95 / 0.7 |
| `triangle.height` | 三角形高度 | 0.95 / 0.7 |
| `rectangle.width` | 矩形宽度 | 0.95 / 0.7 |
| `rectangle.height` | 矩形高度 | 0.95 / 0.7 |
| `bump.height` | 凸起高度 | 0.9 |
| `bump.width` | 凸起宽度 | 0.9 |
| `boundary.left/right/top/bottom` | 边界条件类型 | 0.85-0.9 |
| `physics.inlet_velocity` | 入口速度 | 0.95 |
| `physics.reynolds_number` | 雷诺数 | 0.95 |
| `observable.*` | 观测量 | 0.85 |

每个候选的 `source_span` 通过 `_find_in_text(user_text, keywords)` 从用户原文中截取匹配关键词的上下文窗口。

### 1.4 LLM 候选提取器

**源文件**: `src/fluid_scientist/intent/conflict_resolver.py` → `LLMCandidateExtractor`

`LLMCandidateExtractor.extract(llm_parsed, user_text)` 从 LLM 的 JSON 响应中提取独立候选。LLM 的 JSON 响应结构:

```json
{
  "scene": {"dimension": "2D"},
  "geometry": {
    "domain": {"length": {"value": 10}, "height": {"value": 5}},
    "objects": [
      {"id": "cylinder_1", "type": "cylinder", "radius": {"value": 0.1}, ...},
      {"id": "obstacle_1", "type": "triangle", "width": {"value": 0.1}, ...}
    ]
  },
  "physics": {"inlet_velocity": {"value": 1.0}, "reynolds_number": {"value": 200}},
  "boundaries": [{"name": "left", "type": "velocity_inlet"}, ...],
  "requested_metrics": ["cylinder_drag", "cylinder_lift"]
}
```

LLM 候选提取器将 `geometry.objects` 中的对象类型映射到 `obstacle.type` 字段路径，使用 `_normalize_geometry_type()` 进行规范化。

### 1.5 禁止事项

- 禁止 regex 和 LLM 在候选阶段直接互相覆盖
- 禁止静默使用 regex fallback
- 禁止将未知几何映射到"最接近"的已有几何
- LLM Prompt A 明确规定: 不得补默认值、不得计算派生参数、不得将三角形映射为 cosine_bell、不得把正弦凸起同时创建为矩形实体

---

## 2. 字段级仲裁规则

### 2.1 仲裁策略枚举

**源文件**: `src/fluid_scientist/intent/__init__.py` → `ResolutionStrategy`

| 策略 | 值 | 说明 |
|------|----|------|
| `AGREEMENT` | `agreement` | regex 和 LLM 候选一致，直接接受 |
| `REGEX_ONLY` | `regex_only` | 仅 regex 有值，接受 regex |
| `LLM_ONLY` | `llm_only` | 仅 LLM 有值，经语义忠实校验后接受 |
| `REGEX_WINS` | `regex_wins` | 两者冲突，regex 胜出 |
| `LLM_WINS` | `llm_wins` | 两者冲突，LLM 胜出 |
| `MERGED` | `merged` | 值合并 |
| `NEEDS_CLARIFICATION` | `needs_clarification` | 无法自动解决，需用户澄清 |
| `DUPLICATE_REMOVED` | `duplicate_removed` | 重复实体已移除 |

### 2.2 冲突类型枚举

**源文件**: `src/fluid_scientist/intent/__init__.py` → `ConflictType`

| 类型 | 值 | 说明 |
|------|----|------|
| `SEMANTIC_TYPE_CONFLICT` | `semantic_type_conflict` | 几何类型语义冲突（如 regex=rectangle, LLM=half_sine） |
| `DUPLICATE_ENTITY` | `duplicate_entity` | 同一文本片段被识别为两个不同实体 |
| `VALUE_CONFLICT` | `value_conflict` | 数值冲突 |
| `SPATIAL_CONFLICT` | `spatial_conflict` | 空间位置冲突 |
| `BOUNDARY_CONFLICT` | `boundary_conflict` | 边界条件冲突 |
| `UNSUPPORTED_CAPABILITY` | `unsupported_capability` | 不支持的能力 |
| `MISSING_REQUIRED_FIELD` | `missing_required_field` | 缺少必需字段 |

### 2.3 六种仲裁情况

**源文件**: `src/fluid_scientist/intent/conflict_resolver.py` → `ConflictResolver._resolve_field()`

#### 情况 A: AGREEMENT（候选一致）

```text
regex = triangle
llm   = triangle
→ 直接接受，resolution = AGREEMENT
```

值相等判断使用 `_values_equal(a, b, path)`:
- `obstacle.type` 字段: 先通过 `_normalize_geometry_type()` 规范化再比较
- 数值字段: `abs(float(a) - float(b)) < 1e-6`
- 其他: `str(a) == str(b)`

#### 情况 B: REGEX_ONLY（regex 漏掉，LLM 有值）

```text
regex = None
llm   = triangle
source_span = "三角小障碍物"
→ 经语义忠实校验后接受，resolution = LLM_ONLY
```

对于 `obstacle.type` 字段，LLM_ONLY 接受前必须通过语义忠实校验:
- 使用 `_normalize_geometry_type(l_val)` 获取规范类型
- 使用 `GEOMETRY_SYNONYMS` 查找同义词列表
- 检查用户原文是否实际包含该几何关键词
- 如果 LLM 说 triangle 但用户原文没有三角相关词 → 生成 `SEMANTIC_TYPE_CONFLICT`（blocking），字段进入 `unresolved`

#### 情况 C: REGEX_WINS / LLM_WINS（regex 和 LLM 冲突）

```text
regex = rectangle
llm   = half_sine
→ 进入 ConflictResolver 逐字段仲裁
```

对于 `obstacle.type` 字段冲突:
1. 使用 `_normalize_geometry_type()` 规范化两边的值
2. 使用 `_text_matches_geometry(user_text, geom_type)` 检查哪个与用户原文匹配
3. 如果 regex 匹配而 LLM 不匹配 → `REGEX_WINS`
4. 如果 LLM 匹配而 regex 不匹配 → `LLM_WINS`
5. 如果都匹配或都不匹配 → `NEEDS_CLARIFICATION`（blocking）

对于 `physics.*`、`domain.*`、`cylinder.*` 等数值字段冲突:
- 比较置信度，高置信度胜出
- 相同置信度时 regex 胜出

对于 `boundary.*` 字段冲突:
- regex 胜出（结构化文本提取更可靠）

#### 情况 D: DUPLICATE_ENTITY（同一文本片段产生两个实体）

```text
正弦凸起，高5m、宽20m
→ regex: rectangle
→ LLM:   half_sine
→ conflict_type = DUPLICATE_ENTITY
→ resolution = keep_bottom_profile_remove_rectangle
```

详见 [第 3 节: 重复实体检测](#3-重复实体检测)。

#### 情况 E: NEEDS_CLARIFICATION（无法自动仲裁）

```text
→ 生成用户澄清问题，不得静默选择
→ conflict.severity = BLOCKING
→ field 进入 unresolved 列表
```

只有用户明确回答后才能解决。

#### 情况 F: REGEX_ONLY（仅 regex 有值）

```text
regex = 200
llm   = None
→ 接受 regex，resolution = REGEX_ONLY
```

### 2.4 已解决字段模型

**源文件**: `src/fluid_scientist/intent/__init__.py` → `ResolvedField`

```python
@dataclass
class ResolvedField:
    field_path: str           # 字段路径
    value: Any                # 最终值
    raw_value: str | None     # 支持该值的原始文本
    source_span: str | None   # 用户原文片段
    source: CandidateSource   # 最终来源
    regex_candidate: Any      # regex 说了什么（None 表示无候选）
    llm_candidate: Any        # LLM 说了什么（None 表示无候选）
    resolution: ResolutionStrategy  # 仲裁策略
    confidence: float         # 最终置信度
    confirmed: bool           # 用户是否已确认
```

### 2.5 字段来源可追踪性

所有关键字段的已解决结果必须保存以下追踪信息:

```json
{
  "value": "triangle",
  "raw_value": "三角小障碍物",
  "source_span": "下壁面贴附一个高0.05m、宽0.1m的三角小障碍物",
  "source": "user_explicit",
  "regex_candidate": "triangle",
  "llm_candidate": "triangle",
  "resolution": "agreement",
  "confidence": 1.0,
  "confirmed": true
}
```

至少覆盖: domain length/height、geometry type、geometry dimensions、geometry positions、spatial relationships、fluid、Re、inlet velocity、viscosity、boundaries、initial conditions、observables、research goals。

---

## 3. 重复实体检测

### 3.1 问题描述

用户描述"正弦凸起，高5m、宽20m"时，regex 可能同时识别出:
- `rectangle`（因为有"宽"和"高"的尺寸描述）
- `bottom_profile`（half_sine 类型，因为有"正弦凸起"关键词）

这导致同一句话被识别为两个实体，产生重复几何体。

### 3.2 检测逻辑

**源文件**: `src/fluid_scientist/intent/conflict_resolver.py` → `ConflictResolver._detect_duplicate_entities()`

```python
def _detect_duplicate_entities(self, regex_cands, llm_cands, user_text, result):
    # 检查用户原文是否包含 bump 关键词
    has_bump_keyword = any(
        kw in user_text for kw in ["正弦凸起", "余弦凸起", "半正弦", "sinusoidal", "cosine bell"]
    )

    # 检查是否存在 rectangle 候选
    has_rectangle_cand = any(
        str(c.value) == "rectangle"
        for c in regex_cands + llm_cands
        if c.field_path == "obstacle.type"
    )

    # 检查是否存在 bump 候选
    has_bump_cand = any(
        str(c.value) in ("cosine_bell", "half_sine", "gaussian")
        for c in regex_cands + llm_cands
        if c.field_path == "obstacle.type"
    )

    # 三个条件同时满足 → 生成 DUPLICATE_ENTITY 冲突
    if has_bump_keyword and has_rectangle_cand and has_bump_cand:
        conflict = CandidateConflict(
            field_path="obstacle.type",
            conflict_type=ConflictType.DUPLICATE_ENTITY,
            severity=ConflictSeverity.WARNING,
            resolution="keep_bottom_profile_remove_rectangle",
        )
        result.conflicts.append(conflict)
```

### 3.3 解决策略

当检测到重复实体时，仲裁结果固定为:

```text
resolution = "keep_bottom_profile_remove_rectangle"
```

即保留底部轮廓（half_sine/cosine_bell/gaussian），移除 rectangle 实体。这是因为:
- 用户描述的"正弦凸起"本质是底部轮廓的一种类型，不是独立的矩形实体
- 尺寸（高5m、宽20m）应归属于正弦凸起，而非第二个矩形实体

### 3.4 SemanticFidelityGuard 中的交叉验证

**源文件**: `src/fluid_scientist/intent/semantic_fidelity_guard.py` → `_check_geometry_fidelity()`

除了 ConflictResolver 中的检测，SemanticFidelityGuard 在 Spec 层面也进行检查:

```python
# 检查 half_sine 忠实性
sine_keywords = GEOMETRY_SYNONYMS["half_sine"]
has_sine_in_text = any(kw.lower() in text_lower for kw in sine_keywords)
if has_sine_in_text:
    # 如果 spec 同时启用了 rectangle 和 bottom_profile → blocking violation
    if spec.has_rectangle:
        result.add_violation(
            code="DUPLICATE_ENTITY",
            message="用户原文描述的是正弦凸起，但spec同时启用了rectangle和bottom_profile",
            severity="blocking",
            field_path="rectangle.enabled",
        )
```

同时检查 rectangle 和 bottom_profile 的重叠:

```python
# 源文件: semantic_fidelity_guard.py → _check_geometry_intersections()
if spec.has_rectangle and spec.has_bottom_profile:
    bp_type = spec.bottom_profile.profile_type.value
    if bp_type != "flat":
        result.add_violation(
            code="RECTANGLE_BUMP_OVERLAP",
            message="rect和bottom_profile同时启用，可能产生重复几何体",
            severity="blocking",
        )
```

---

## 4. 几何同义词表

### 4.1 GEOMETRY_SYNONYMS 映射

**源文件**: `src/fluid_scientist/intent/conflict_resolver.py`

```python
GEOMETRY_SYNONYMS: dict[str, list[str]] = {
    "triangle":    ["三角", "三角形", "三角障碍", "三角凸起", "三角小障碍物", "triangular", "triangle"],
    "rectangle":   ["矩形", "长方形", "rectangular", "rectangle"],
    "cosine_bell": ["余弦凸起", "余弦形凸起", "余弦丘", "余弦钟形", "cosine bell", "cosine_bell"],
    "half_sine":   ["正弦凸起", "半正弦凸起", "正弦形凸起", "sinusoidal bump", "half_sine", "sine bump"],
    "gaussian":    ["高斯凸起", "高斯丘", "gaussian bump", "gaussian"],
    "cylinder":    ["圆柱", "圆柱体", "cylinder", "circular cylinder"],
}
```

### 4.2 用途

| 用途 | 方法 | 说明 |
|------|------|------|
| 规范化几何类型 | `_normalize_geometry_type(value)` | 将用户文本或 LLM 输出的几何类型词映射到规范类型 |
| 候选值比较 | `_values_equal(a, b, "obstacle.type")` | 对 obstacle.type 字段，先规范化再比较 |
| 文本匹配 | `_text_matches_geometry(text, geom_type)` | 检查用户原文是否包含某几何类型的同义词 |
| 语义忠实校验 | `SemanticFidelityGuard._check_geometry_fidelity` | 验证 Spec 中的几何类型与用户原文一致 |
| source_span 提取 | `_find_in_text(user_text, keywords)` | 从用户原文中截取支持该值的文本片段 |

### 4.3 规范化逻辑

```python
def _normalize_geometry_type(value: Any) -> str | None:
    val_str = str(value).lower().strip()
    for canonical, synonyms in GEOMETRY_SYNONYMS.items():
        if val_str in [s.lower() for s in synonyms] or val_str == canonical:
            return canonical
    return val_str  # 未知类型原样返回
```

### 4.4 几何忠实规则

| 用户原文 | 正确 Spec 类型 | 禁止的映射 |
|----------|---------------|-----------|
| 三角/三角形/三角障碍 | `triangle` | 禁止映射为 `cosine_bell` |
| 矩形/长方形 | `rectangle` | 禁止映射为 `triangle` |
| 余弦凸起/余弦丘 | `cosine_bell`（底部轮廓） | 禁止映射为 `triangle` |
| 正弦凸起/半正弦 | `half_sine`（底部轮廓） | 禁止额外创建 `rectangle` |
| 高斯凸起 | `gaussian`（底部轮廓） | 禁止映射为近似类型 |
| 圆柱 | `cylinder` | — |
| 未知形状 | `unknown` | 禁止映射到默认类型，触发 clarification |

---

## 5. SemanticFidelityGuard 四类检查

**源文件**: `src/fluid_scientist/intent/semantic_fidelity_guard.py` → `SemanticFidelityGuard`

### 5.1 执行时机

SemanticFidelityGuard 在三个检查点分别执行:

1. **候选仲裁完成后**（pre-spec）— 确保仲裁结果忠实于用户意图
2. **Spec 落库前**（post-derivation）— 确保派生参数不违反语义
3. **编译前** — 确保最终 Spec 在编译前通过所有语义检查

### 5.2 检查结果模型

```python
class GuardResult:
    violations: list[GuardViolation]  # 违规列表
    warnings: list[GuardWarning]      # 警告列表

    @property
    def passed(self) -> bool:
        """True if no blocking violations."""
        return not any(v.severity == "blocking" for v in self.violations)
```

```python
@dataclass
class GuardViolation:
    code: str           # 违规代码
    message: str        # 违规描述
    severity: Literal["blocking", "warning"]
    field_path: str | None  # 相关字段路径
    evidence: str | None    # 证据（用户原文片段）
```

### 5.3 第一类: 几何忠实性检查

**方法**: `_check_geometry_fidelity(spec, user_text, result)`

| 检查项 | 违规代码 | 严重度 | 说明 |
|--------|----------|--------|------|
| 用户说三角但 Spec 用了 bottom_profile | `GEOMETRY_TYPE_MISMATCH` | blocking | 三角关键词存在但 `spec.has_triangle` 为 False，且 bottom_profile 类型不是 flat |
| 用户说余弦凸起但 Spec 用了 triangle | `GEOMETRY_TYPE_MISMATCH` | blocking | 余弦关键词存在但 Spec 使用了 triangle 而非 cosine_bell |
| 用户说正弦凸起但 Spec 未用 half_sine | `GEOMETRY_TYPE_WARNING` | warning | 正弦关键词存在但 bottom_profile 类型不是 half_sine |
| 正弦凸起同时启用了 rectangle | `DUPLICATE_ENTITY` | blocking | `has_sine_in_text` 且 `spec.has_rectangle` 为 True |
| 用户说矩形但 Spec 未启用 rectangle | `GEOMETRY_TYPE_WARNING` | warning | 矩形关键词存在但无 sine/cosine 关键词时才警告 |

### 5.4 第二类: 空间关系检查

**方法**: `_check_spatial_relations(spec, user_text, result)`

支持并验证的空间关系类型:

| 关系 | 用户原文示例 | Spec 约束 | 违规代码 |
|------|-------------|-----------|----------|
| `centered_under` | "位于圆柱正下方" | `obstacle.center_x == cylinder.center_x`（容差 0.01m） | `SPATIAL_RELATION_VIOLATION`（blocking） |
| `attached_to` | "贴附下壁面"/"贴壁" | 障碍物位于域底部 | `SPATIAL_WARNING`（矩形高度超过域高度 50%） |
| `centered_in_x` | "正中央"/"流场中央" | `cylinder.center_x == domain.length / 2`（5% 容差） | `POSITION_CONFLICT`（同时有"距下壁"约束时警告） |

**位置冲突检测**: 当用户同时指定"正中央"和"距下壁 Xm"时，这两个约束可能给出不同的 y 坐标。系统不静默解释，而是生成 `POSITION_CONFLICT` 警告，提示用户澄清。

### 5.5 第三类: 几何相交检查

**方法**: `_check_geometry_intersections(spec, result)`

| 检查项 | 违规代码 | 严重度 | 说明 |
|--------|----------|--------|------|
| 圆柱超出域长度 | `CYLINDER_OUT_OF_DOMAIN` | blocking | `cyl_cx - cyl_r < 0` 或 `cyl_cx + cyl_r > domain_len` |
| 圆柱与壁面相交 | `CYLINDER_INTERSECTS_WALL` | blocking | `cyl_cy - cyl_r < 0` 或 `cyl_cy + cyl_r > domain_h` |
| 三角形高度非正 | `INVALID_TRIANGLE_DIMENSION` | blocking | `tri_h <= 0` |
| 三角形宽度非正 | `INVALID_TRIANGLE_DIMENSION` | blocking | `tri_w <= 0` |
| 三角形过高 | `TRIANGLE_TOO_LARGE` | warning | `tri_h > domain_h * 0.5` |
| 矩形高度非正 | `INVALID_RECTANGLE_DIMENSION` | blocking | `rect_h <= 0` |
| 矩形宽度非正 | `INVALID_RECTANGLE_DIMENSION` | blocking | `rect_w <= 0` |
| 圆柱与三角形相交 | `CYLINDER_TRIANGLE_INTERSECTION` | blocking | 三角形在圆柱正下方且高度超过圆柱底部 |
| 圆柱与矩形相交 | `CYLINDER_RECTANGLE_INTERSECTION` | blocking | 矩形顶部超过圆柱底部且水平重叠 |
| 矩形与底部轮廓重叠 | `RECTANGLE_BUMP_OVERLAP` | blocking | `has_rectangle` 且 `has_bottom_profile` 且 profile_type != "flat" |

### 5.6 第四类: 边界语义检查

**方法**: `_check_boundary_semantics(spec, user_text, result)`

| 检查项 | 违规代码 | 严重度 | 说明 |
|--------|----------|--------|------|
| 入口外流模式下左边界非 velocity_inlet | `BOUNDARY_WARNING` | warning | 入口外流模式左边界应为 velocity_inlet |
| 入口外流模式下右边界非 pressure_outlet | `BOUNDARY_WARNING` | warning | 入口外流模式右边界应为 pressure_outlet |
| 周期边界不成对 | `PERIODIC_BOUNDARY_UNPAIRED` | blocking | 左为 periodic 但右不是，或反之 |
| 用户要求"自由出流"但上边界设为 no_slip_wall | `BOUNDARY_SEMANTIC_MISMATCH` | blocking | 自由出流应为 symmetry/slip/freestream/open_boundary |
| 2D 问题 front 边界非 empty | `BOUNDARY_2D_WARNING` | warning | 2D 问题 front/back 应为 empty |
| 2D 问题 back 边界非 empty | `BOUNDARY_2D_WARNING` | warning | 同上 |
| 用户要求"无滑移"但下边界类型不匹配 | `BOUNDARY_SEMANTIC_MISMATCH` | blocking | 无滑移应为 no_slip_wall |

### 5.7 失败返回码

当 SemanticFidelityGuard 检测到 blocking 违规时:

```text
SEMANTIC_GEOMETRY_INVALID
```

Spec 不得进入编译阶段。必须先修复所有 blocking 违规或由用户澄清。

---

## 附录: LLM Prompt 设计

### Prompt A: 事实和实体提取

**源文件**: `src/fluid_scientist/intent/prompts.py` → `LLM_FACT_EXTRACTION_PROMPT`

核心规则:
1. 只提取用户明确说的内容，不得补默认值
2. 几何类型必须忠实于用户描述
3. 不得把正弦凸起同时创建为矩形实体
4. 必须为每个字段返回 source_span
5. 无法确定时返回 unknown
6. 可推导的参数不要列为 missing
7. 位置冲突必须标记

输出 JSON Schema:
```json
{
  "entities": [...],
  "domain": {"length": {...}, "height": {...}},
  "boundaries": [...],
  "physics": {...},
  "observables": [...],
  "spatial_relations": [...],
  "unknown_terms": [...],
  "missing_fields": [...],
  "ambiguities": [...]
}
```

### Prompt B: 冲突仲裁

**源文件**: `src/fluid_scientist/intent/prompts.py` → `LLM_CONFLICT_ARBITRATION_PROMPT`

仅在 regex 和 LLM 冲突时调用，避免无意义增加成本。

输入: 用户原文、regex candidates、LLM candidates、当前 Schema、支持能力清单、几何和边界冲突规则

输出 JSON Schema:
```json
{
  "resolved_fields": [
    {"field_path": "obstacle.type", "value": "triangle", "winner": "regex|llm|agreement", "reason": "..."}
  ],
  "blocking_conflicts": [
    {"field_path": "...", "regex_value": ..., "llm_value": ..., "reason": "...", "question": "..."}
  ],
  "clarification_questions": [
    {"field": "...", "question": "...", "options": [...], "evidence": "..."}
  ]
}
```

严格禁止:
- 不得将三角形改为 cosine_bell
- 不得将未知几何映射为已知几何
- 不得编造用户没有说过的值
- 不得选择与用户原文矛盾的值
