# 给 Trae 的 Skill Pack 接入任务

将本目录的 `fluid_skills` 接入当前 `fluid_scientist` 仓库。

## 强制要求

1. 先审计现有路由、Draft API、确认 API、Compiler、ValidationRunner、
   WorkstationRunner、Artifact Store 和前端状态来源。
2. 不得只复制文件；必须把每个 Skill 接入真实主链路。
3. 专用圆柱流程命中后，不得再次调用旧通用 Draft Generator。
4. 模型输出先进入语义 Spec，再运行确定性 Skills。
5. Compiler 只能消费 `SPEC_CONFIRMED` 的语义 Spec。
6. OpenFOAM 只允许 Foundation 13 Profile。
7. 真实工作站验证缺失时返回 `ENVIRONMENT_BLOCKED`，不得伪成功。
8. 每次改动后重启后端、前端和 Worker，确认 PID/build hash 变化。
9. 必须跑单元测试、API 集成测试、真实页面测试和真实工作站 E2E。
10. 自然语言到可视化图片回传未完整通过前不得结束。

## 推荐接入顺序

```text
router
→ geometry normalizer
→ derived fields
→ flow topology
→ 2D boundary topology
→ observable extraction/recommendation
→ analysis goals
→ readiness
→ OpenFOAM compiler
→ static validator
→ smoke test
→ formal run
→ postprocess
→ artifact return
```

## 完成证据

必须输出：

- 实际 API 路由；
- 当前 pipeline_id；
- Spec 版本；
- 自动化测试结果；
- checkMesh 日志；
- Smoke Test 日志；
- 正式运行日志；
- 生成图片；
- Artifact 回传结果；
- 浏览器页面截图；
- Commit 列表。
