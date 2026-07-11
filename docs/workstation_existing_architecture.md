# Workstation Existing Architecture Audit

## 1. 现有工作站模型

### WorkstationSettings (`settings.py`)
- `hosts`: tuple[str, ...] — 工作站主机列表
- `username`: str — SSH 用户名
- `identity_file`: str | None — 私钥路径
- `known_hosts_file`: str | None — known_hosts 文件路径
- 通过 `.env` 文件配置: `FLUID_WORKSTATION__HOSTS`, `FLUID_WORKSTATION__USERNAME` 等

### NodeSettings (`settings.py`)
- `host`: str — 主机名
- `username`: str — 用户名
- `port`: int — SSH 端口
- `identity_file`: str | None — 私钥路径
- `known_hosts_file`: str | None — known_hosts 路径

### ExecutionTargetCapability (`execution_targets/base.py`)
- `target_id`, `kind`, `available`, `selected_candidate`
- `foam_version`, `cpu_count`, `memory_gb`, `disk_free_gb`
- `commands`, `worker_protocol`, `reason`

## 2. 现有 SSH 执行器

### SSHTransport (`execution/ssh.py`)
- 使用 `subprocess.run` (无 shell=True) — 安全
- `BatchMode=yes` + `StrictHostKeyChecking=yes` — 安全
- 要求 `known_hosts_file` 必须存在
- 支持私钥路径 (`identity_file`)
- 限制远程命令为 `RemoteProgram` 枚举
- 限制远程参数为 `RemoteArg` (正则验证)

### ProcessRunner Protocol
- `run(argv: tuple[str, ...], *, timeout: float) -> ProcessResult`
- `SubprocessRunner` 是默认实现
- 可替换为 Mock Runner 用于测试

## 3. 现有工作站 API

### `/api/workstation/status` (GET)
- 返回连接状态
### `/api/workstation/reconnect` (POST)
- 强制重新调用 doctor()
### `/api/workstation/test-ssh` (POST)
- 快速 SSH 连通性测试
### `/api/workstation/detect` (GET)
- 扫描本机 SSH 密钥和 known_hosts 文件路径
### `/api/workstation/configure` (POST)
- 写入 `.env` 文件并重新加载

## 4. 现有 Profile Store
- 无独立 WorkstationProfile 持久化
- 配置存储在 `.env` 文件中
- 服务重启后从 `.env` 重新加载

## 5. 现有前端入口
- `app.py` 中的 `/api/workstation/*` 端点
- 前端 `v5-app.js` 中有工作站状态迷你面板 `#workstation-panel-mini`
- `index.html` 中有工作站设置对话框

## 6. 现有 HPC Adapter
- `execution/hpc.py`: Slurm 支持
- `SafeSlurmValue`, `RemoteRelativePath`, `SlurmResources`
- `render_sbatch()` 生成 sbatch 脚本
- `adapters/slurm.py`: Slurm adapter

## 7. 现有任务提交模块
- `WorkstationOpenFOAMTarget`: 通过 fluid-worker 协议提交
- `submit()`, `submit_custom()`, `status()`, `cancel()`, `collect()`
- 依赖 `SSHTransport` 和 `RemoteProgram.FLUID_WORKER`

## 8. 本轮可复用代码
- `ProcessRunner` / `SubprocessRunner` / `ProcessResult` — subprocess 安全执行
- `RemoteArg` — 远程参数验证
- `ExecutionTargetCapability` — 能力描述模型
- SQLite 持久化模式 (来自 `v5_storage.py`)

## 9. 本轮不能修改的边界
- 不修改 `SSHTransport` 类
- 不修改 `WorkstationOpenFOAMTarget` 类
- 不修改 `WorkstationSettings` 模型
- 不修改 `/api/workstation/*` 现有端点
- 不修改 `.env` 配置机制

## 10. 预计新增文件
- `src/fluid_scientist/workstations/` — 新模块
- `src/fluid_scientist/api/workstation_router.py` — 新 API Router
- `tests/workstations/` — 新测试目录
- 工作站面板 UI 代码（在现有 v5-app.js 中扩展）
