# Workstation Scope Lock

## 基线
- 仓库: `d:\desktop\AI FOR SCIENCE` (仓库 A)
- 基线分支: `integration/trae-codex`
- 基线 SHA: `bb0303833c77cadd3f4837cef6b64155a7762a4f`
- 开发分支: `trae/workstation-auto-connect`
- Worktree: `D:\desktop\AI FOR SCIENCE\.worktrees\workstation-auto-connect`

## 允许修改的文件

### 新增文件
- `src/fluid_scientist/workstations/__init__.py`
- `src/fluid_scientist/workstations/models.py`
- `src/fluid_scientist/workstations/discovery.py`
- `src/fluid_scientist/workstations/connection.py`
- `src/fluid_scientist/workstations/probes.py`
- `src/fluid_scientist/workstations/profile_store.py`
- `src/fluid_scientist/workstations/ssh_runner.py`
- `src/fluid_scientist/workstations/cli.py`
- `src/fluid_scientist/api/workstation_router.py`
- `tests/workstations/__init__.py`
- `tests/workstations/test_discovery.py`
- `tests/workstations/test_connection.py`
- `tests/workstations/test_probes.py`
- `tests/workstations/test_profile_store.py`
- `tests/workstations/test_ssh_runner.py`
- `tests/workstations/test_api.py`
- `tests/workstations/test_cli.py`
- `docs/workstation_existing_architecture.md`
- `docs/workstation_scope_lock.md`

### 允许最小修改的文件
- `src/fluid_scientist/api/app.py` — 仅增加 `include_router(workstation_router)` 一行
- `apps/web/v5-app.js` — 仅增加工作站设置面板入口和交互逻辑
- `apps/web/styles.css` — 仅增加工作站面板样式

### 允许只读引用的文件（不修改）
- `src/fluid_scientist/execution/ssh.py` — 复用 ProcessRunner/ProcessResult 协议
- `src/fluid_scientist/execution_targets/workstation.py` — 复用 ExecutionTargetCapability
- `src/fluid_scientist/execution_targets/base.py` — 复用接口
- `src/fluid_scientist/settings.py` — 读取现有 WorkstationSettings
- `src/fluid_scientist/draft_session/v5_storage.py` — 复用 SQLite 持久化模式

## 禁止修改的路径
- `src/fluid_scientist/workflow_pipeline/`
- `src/fluid_scientist/capabilities/`
- `src/fluid_scientist/code_extension/`
- `src/fluid_scientist/case_generation/`
- `src/fluid_scientist/prompts/`
- `src/fluid_scientist/research/`
- `src/fluid_scientist/draft_session/`
- `src/fluid_scientist/metrics/`
- `src/fluid_scientist/analysis/`
- `src/fluid_scientist/execution/` (现有 SSH transport 不修改)
- `src/fluid_scientist/execution_targets/` (现有 target 不修改)
- `apps/web/index.html` (三栏布局不修改)
