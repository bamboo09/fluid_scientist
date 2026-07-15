# geometry.cylinder.normalizer

## 目的

归一化圆柱语义，保留用户输入。

## 调用时机

仅在输入满足本 Skill 的前置条件时调用。不要让模型直接绕过本 Skill
生成 OpenFOAM 字典或最终状态。

## 输入

- 语义化 Spec 或自然语言片段；
- 已确认字段的来源和状态；
- 必要时提供工作站命令执行器。

## 输出

统一返回 `SkillResult`：

```json
{
  "skill_id": "geometry.cylinder.normalizer",
  "status": "SUCCESS | PARTIAL | FAILED | ENVIRONMENT_BLOCKED",
  "data": {},
  "issues": [],
  "evidence": []
}
```

## 硬约束

- 用户确认值不能被模型推荐覆盖；
- 未知信息必须保留为 unresolved；
- 失败必须返回结构化 issue；
- 不得伪造工作站执行证据；
- 不得直接输出完整 OpenFOAM 字典；
- 与 Foundation 13 不兼容时必须失败。

## 入口

`fluid_skills.runtime.geometry:normalize_cylinder_geometry`

## 依赖

无

## 完成条件

- 输出通过 Schema；
- 所有阻塞问题均显式列出；
- 确定性规则具备回归测试；
- 真实环境 Skill 必须附带 evidence。
