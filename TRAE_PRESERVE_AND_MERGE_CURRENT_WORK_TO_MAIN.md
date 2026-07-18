# Trae 任务：完整保留当前成果、安全合并主分支并交接给 Codex

> 仓库：`bamboo09/fluid_scientist`
>
> **Trae 本轮只做一件事：**
>
> 把当前实际运行版本中的所有成果完整保存并安全合并到 `main`，然后向 Codex 交付一个唯一、准确、可复现的主分支基线。
>
> 本轮不再继续大规模架构重构。模型原生理解、开放几何、模型参与编译和真实工作站完整 E2E 由 Codex 在合并后的主分支上完成。

---

# 1. 不可违反的原则

1. 当前前端、后端、Skill、模型调用、Spec、Patch、CaseIR、Compiler、工作站、测试、文档全部保留。
2. 不得回退旧 UI、旧 API、旧 Compiler 或旧工作流。
3. 不得根据分支名猜当前版本，必须从实际运行进程确认。
4. 不得挑选性 cherry-pick 当前成果；使用完整 merge 保留历史。
5. 不得用整目录 `ours/theirs` 覆盖业务代码。
6. 所有当前已知问题必须如实交接，不能为了“合并成功”而隐藏。
7. 合并后 Trae 停止修改业务主链，Codex 成为唯一业务代码写入者。

---

# 2. 锁定当前实际运行版本

执行：

```bash
pwd
git rev-parse --show-toplevel
git status --short --branch
git rev-parse HEAD
git remote -v
git branch -vv
git worktree list --porcelain
git log --graph --decorate --oneline --all -n 250
```

检查运行进程。

Linux/WSL：

```bash
ps -ef | grep -E "uvicorn|gunicorn|vite|npm|pnpm|node|fluid" | grep -v grep
readlink -f /proc/<PID>/cwd
tr '\0' ' ' < /proc/<PID>/cmdline
```

Windows：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "uvicorn|vite|node|fluid" } |
  Select-Object ProcessId, ExecutablePath, CommandLine
```

创建：

```text
docs/audits/TRAE_FINAL_RUNNING_BASELINE.md
```

必须记录：

```yaml
repository_root:
trae_source_branch:
trae_source_commit:
frontend:
  cwd:
  commit:
  command:
  url:
backend:
  cwd:
  commit:
  command:
  api_base:
database:
worker:
  workstation_profile:
  openfoam_version:
model:
skills:
```

当前实际运行 commit 才是 Trae 成果源。

---

# 3. 收拢所有未提交成果

执行：

```bash
git status --short
git diff --stat
git diff
git ls-files --others --exclude-standard
```

对未提交内容分类：

```text
runtime/model/skill
spec/patch/dependency
caseir/compiler
workstation
frontend
tests
docs/audits
```

分逻辑提交保存，例如：

```text
feat(runtime): preserve current model and skill integration
feat(spec): preserve current study spec and patch workflow
feat(openfoam): preserve compiler and workstation workflow
feat(ui): preserve current chat and research-plan experience
test(e2e): preserve current regression and capability tests
docs(audit): record current behavior and known issues
```

不得留下未提交业务文件后切换分支。

---

# 4. 生成当前实现清单

创建：

```text
docs/audits/TRAE_CURRENT_IMPLEMENTATION_MANIFEST.md
```

格式：

| 模块 | 当前入口 | 关键文件 | 已实现能力 | 已知问题 | 测试 |
|---|---|---|---|---|---|

覆盖：

```text
session
intent
LLM
skills
facts
ambiguities
conflicts
spec
patch
dependency
caseir
capability
compiler
workstation
postprocess
frontend
tests
```

必须记录目前已知问题：

```text
未指定材料却出现水属性
长方形计算域被误生成为矩形障碍
regex和LLM冲突候选同时进入Spec
时间参数修改后右侧未更新
READY_TO_CONFIRM与NEEDS_CLARIFICATION冲突
阻塞问题不可解释
字段provenance错误
模型可能替用户回答澄清
```

---

# 5. 创建保护标签

在 Trae 当前完整成果 commit 上：

```bash
git tag backup/trae-complete-before-main-merge-<timestamp>
```

在 main 合并前 commit 上：

```bash
git switch main
git pull --ff-only origin main
git tag backup/main-before-trae-merge-<timestamp>
git push origin --tags
```

两个 tag 和 SHA 必须写入交接文档。

---

# 6. 创建安全集成分支

从最新 main：

```bash
git switch -c integration/trae-current-to-main
```

完整合并 Trae 当前成果分支：

```bash
git merge --no-ff <TRAE_SOURCE_BRANCH>
```

用户要求完整保留，因此不得用挑选性 cherry-pick 代替完整合并。

---

# 7. 冲突解决规则

业务实现优先级：

```text
当前实际运行的Trae实现
>
main中的旧实现
```

但以下必须人工融合：

```text
依赖配置
环境变量示例
数据库迁移
路由注册
测试配置
文档索引
```

创建：

```text
docs/audits/TRAE_TO_MAIN_MERGE_DECISIONS.md
```

格式：

| 文件 | main含义 | Trae含义 | 最终选择 | 原因 | 验证 |
|---|---|---|---|---|---|

禁止：

```bash
git checkout --ours src
git checkout --theirs apps
git restore --source=<old-branch> -- src
```

---

# 8. 合并后的完整性验证

检查 diff：

```bash
git diff backup/main-before-trae-merge-<timestamp>...HEAD --name-status
git diff backup/main-before-trae-merge-<timestamp>...HEAD --stat
```

验证：

```text
前端启动
后端启动
数据库迁移
创建会话
当前聊天UI
Skill Registry
模型调用
方案生成
方案修改
静态编译
工作站Doctor
```

同时用用户当前“矩形域 + 梯形障碍”案例保留已知问题基线。目的不是让缺陷通过，而是证明合并没有换回旧版本。

保存：

```text
artifacts/handoff/known_issue_trapezoid_case/
```

---

# 9. 合并到 main

集成分支验证通过后：

```bash
git switch main
git merge --no-ff integration/trae-current-to-main
git push origin main
```

记录：

```text
MAIN_AFTER_TRAE_MERGE_SHA
```

---

# 10. 从 main 重新启动实际服务

停止旧前后端，从合并后的 main worktree 重启。

新增或验证：

```text
GET /api/system/build-info
```

返回：

```json
{
  "branch": "main",
  "commit": "...",
  "frontend_commit": "...",
  "backend_commit": "...",
  "build_time": "..."
}
```

必须证明页面和 API 都运行：

```text
MAIN_AFTER_TRAE_MERGE_SHA
```

不得继续运行旧 worktree。

---

# 11. 向 Codex 交接

创建仓库根目录文件：

```text
CODEX_HANDOFF_FROM_TRAE.md
```

必须包含：

```yaml
repository:
main_commit:
main_tag:
trae_backup_tag:
main_before_merge_tag:
frontend:
backend:
database:
workstation:
  profile:
  openfoam_version:
model:
  provider:
  configured_model:
skills:
  root:
  bundle_hash:
current_api_entrypoints:
current_canonical_spec:
current_compiler:
current_tests:
known_issues:
forbidden_old_branches:
```

并附：

```text
当前真实调用链
前后端运行命令
关键目录
数据库迁移状态
测试命令
工作站Doctor命令
用户当前截图问题的复现步骤
```

明确写：

> Codex 必须从 `MAIN_AFTER_TRAE_MERGE_SHA` 创建新分支。任何旧分支、旧 worktree 和旧提交都不得作为业务代码来源。

---

# 12. 协作冻结规则

交接完成后：

```text
Trae停止修改业务主链
Codex成为唯一业务代码写入者
```

Trae 后续只负责：

```text
复现
运行测试
收集日志
代码审查
```

不得与 Codex 同时修改相同业务文件。

---

# 13. 完成标准

- [ ] 当前实际运行 commit 已确认；
- [ ] 所有未提交成果已保存；
- [ ] 当前实现 manifest 已生成；
- [ ] 已知问题完整记录；
- [ ] Trae 成果和 main 均已打保护 tag；
- [ ] 使用完整 `merge --no-ff`；
- [ ] 未使用旧分支覆盖当前实现；
- [ ] 冲突逐文件记录；
- [ ] 合并后前后端、数据库、模型、Skill、Compiler、Doctor可用；
- [ ] main 已推送；
- [ ] 实际服务来自合并后的 main；
- [ ] build-info 返回精确 SHA；
- [ ] Codex 交接文件完整；
- [ ] Trae 业务写入已冻结。

---

# 14. 直接执行指令

```text
你本轮不要继续做大规模架构改造。你的职责是完整保留目前所有成果，安全合并到主分支，
并给Codex建立唯一、准确的后续基线。

先从实际前后端运行进程确认当前worktree、branch和commit，不得根据分支名称猜测。
把所有未提交代码、测试、UI和文档按逻辑提交。

生成TRAE_CURRENT_IMPLEMENTATION_MANIFEST.md，如实记录已实现功能和当前问题，包括材料
默认污染、矩形域误生矩形障碍、regex/LLM候选合并、时间参数修改未显示、状态不一致、
阻塞不可解释和provenance错误。

在Trae完整成果和main合并前版本分别打保护tag。从最新main创建
integration/trae-current-to-main，使用git merge --no-ff完整合并Trae分支。
禁止挑选性cherry-pick，禁止旧main或其他旧分支覆盖业务目录，禁止整目录ours/theirs。

逐文件解决冲突并写TRAE_TO_MAIN_MERGE_DECISIONS.md。完成前端、后端、数据库、UI、
Skill、模型、Spec、Compiler和Workstation Doctor验证后，将集成分支合并到main并推送。

停止旧服务，从合并后的main重新启动，并通过/api/system/build-info证明实际运行版本。

最后生成CODEX_HANDOFF_FROM_TRAE.md，写明精确main SHA、tags、运行命令、数据库、
模型、Skill、工作站、关键目录、调用链、测试命令、已知问题和复现步骤。

交接完成后冻结Trae对业务主链的写入，Codex成为后续唯一业务代码写入者。
```
