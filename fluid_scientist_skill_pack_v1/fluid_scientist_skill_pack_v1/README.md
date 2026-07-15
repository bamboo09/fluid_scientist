# Fluid Scientist Skill Pack v1

这是一套面向 `fluid_scientist` 的项目内 Skill Pack，目标平台为：

- OpenFOAM Foundation 13
- `foamRun`
- `incompressibleFluid`
- 二维可配置圆柱绕流
- 工作站真实验证
- Python 后处理与图片回传

它不是让模型直接生成 OpenFOAM 字典的 Prompt 集，而是由四部分组成：

1. `SKILL.md`：模型调用说明和约束；
2. `skill.json`：机器可读元数据、依赖、输入输出和验证等级；
3. `runtime/`：可确定性执行的规则、编排和验证代码；
4. `tests/`：最低回归测试。

## 首批 Skills

1. `cylinder_flow_2d_router`
2. `geometry_cylinder_normalizer`
3. `geometry_derived_dimensions`
4. `fluid_flow_topology_classifier`
5. `boundary_topology_2d`
6. `observable_extractor`
7. `observable_recommender`
8. `analysis_goal_builder`
9. `openfoam13_platform_discovery`
10. `openfoam13_case_static_validator`
11. `validation_smoke_test`
12. `postprocess_flow_visualization`
13. `cylinder_flow_2d_e2e_loop`

## 接入原则

```text
自然语言
→ router
→ facts / semantic spec
→ deterministic normalization
→ topology
→ observables
→ goals
→ readiness
→ compiler
→ static validation
→ workstation smoke test
→ formal run
→ visualization
→ artifact return
```

模型只负责理解、候选解释和结构化建议。以下内容由确定性代码负责：

- 圆柱半径、直径和特征尺度派生；
- 二维 `front/back = empty`；
- 用户字段优先级；
- 非法边界组合；
- Readiness 判定；
- OpenFOAM 文件静态检查；
- Smoke Test 成功条件；
- PlotSpec 白名单。

## 快速测试

```bash
python -m pytest -q
```

## 接入现有仓库

建议复制到：

```text
backend/
  fluid_skills/
tests/
  skills/
```

然后由现有 `UnknownCapabilityOrchestrator` 或新的
`CylinderFlow2DOrchestrator` 调用 `fluid_skills.runtime.orchestrator`。

请先阅读 `TRAE_INTEGRATION_PROMPT.md`。
