# cylinder_flow_2d.e2e_loop

## 目标

从用户自然语言开始，持续执行到真实 OpenFOAM Foundation 13 仿真完成、
Python 生成真实流场图并回传原对话。完整流程未通过前不得标记完成。

## 状态节点

```text
DRAFT
→ NEEDS_CLARIFICATION / AWAITING_CONFIRMATION
→ SPEC_CONFIRMED
→ GEOMETRY_VALIDATED
→ MESH_GENERATED
→ MESH_VALIDATED
→ CASE_COMPILED
→ STATIC_VALIDATED
→ DICTIONARY_VALIDATED
→ SERIAL_SMOKE_TEST_PASSED
→ PARALLEL_SMOKE_TEST_PASSED
→ READY_TO_SUBMIT
→ SUBMITTED
→ RUNNING
→ COMPLETED
→ POSTPROCESSING
→ RESULTS_READY
```

## 循环策略

```text
找到最早失败节点
→ 分类错误
→ 确定性修复一次
→ 重跑该节点及下游
→ LLM仅生成结构化Spec Patch一次
→ 重跑
→ 仍失败则VALIDATION_FAILED或ENVIRONMENT_BLOCKED
```

不允许无限修改最终 OpenFOAM 文件。

## 每轮必须记录

- 当前节点；
- 输入 Spec 版本；
- 工作站 Case 路径；
- 命令、退出码和日志摘要；
- 修改前后 Diff；
- 重启的进程和 build hash；
- 生成的 Artifact；
- 下一失败节点。

## 完成判据

必须同时满足：

1. 专用 pipeline 生效；
2. 用户事实没有遗漏；
3. Spec 已确认；
4. `checkMesh` 通过；
5. 串行 Smoke Test 通过；
6. 正式计算真实完成；
7. Python 图像非空、可打开且不是占位图；
8. Artifact 绑定正确 run_id/spec_version；
9. 原对话框可展示图片；
10. 关键场景连续成功两次。

## 禁止提前结束

以下不算完成：

- 代码写完；
- 单元测试通过；
- API 200；
- 生成若干字典；
- mock 工作站成功；
- mock 图片；
- 需要用户自己手动跑最后一步。
