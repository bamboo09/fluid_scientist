# Fluid Scientist

## 多模型实验计划与确定性编译

工作台第一批支持 OpenAI、GLM 和 DeepSeek。API Key 只保存在当前服务进程内存，不进入浏览器存储、数据库、日志、Skill 或 Git；服务重启后需要重新配置。模型 ID 可编辑，不把某个版本写死为系统能力。

模型只生成严格的 provider-neutral `ExperimentPlan`，可选择 `laminar_pipe`、`cylinder_flow`、`lid_driven_cavity` 或 `custom_openfoam`。内置类型由受控编译器生成 OpenFOAM Foundation 13 算例；自定义类型进入 tar.gz 双重安全校验流程。模型不能生成或执行 Shell、远程路径和 OpenFOAM 命令。

计划编译后会显示求解器、预处理链、输出指标和 `archive_sha256`。Gate 2 同时绑定计划 ID、计划版本和归档摘要；提交时读取并重新计算已存档字节的摘要，不会在审批后重新编译。

面向 OpenFOAM 与 HPC 的可信流体力学科研智能体。系统把自然语言研究问题转为严格的 ResearchSpec，通过文献证据、物理规则、Pilot、Slurm/OpenFOAM、确定性验证、Results Analyst 和 Scientific Reviewer 形成可追溯闭环。

当前版本包含两条路径：单相、不可压缩、稳态 90° 弯管的 Fake 科研闭环，以及可在 OpenFOAM Foundation 13 工作站上执行的真实层流圆管基准。默认使用 Fake 模式，不需要 OpenAI Key、HPC 地址或 OpenFOAM，适合本地演示和 CI。Fake 数值仅用于验证软件流程，不能作为科研结果。

## 快速开始

要求 Python 3.10 或更高版本。

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m uvicorn fluid_scientist.api.app:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`，提交默认弯管问题。系统会运行三网格 Fake Pilot，并展示质量守恒、GCI、确定性统计、证据化结论和审计数量。

## 已实现

- 严格 Pydantic ResearchSpec、Evidence、Case Manifest、Validation、Analysis 和 Report 契约。
- Reynolds/Dean 数、守恒、残差、监测量和三网格 GCI 的确定性计算。
- 三个人工 Gate、显式状态转换、外部 job ID 幂等绑定和 JSON 快照恢复。
- Fake Evidence、Results Analyst、Scientific Reviewer、Slurm 和 OpenFOAM 适配器。
- 防命令注入的 Slurm 值对象、远程相对路径和固定 OpenFOAM 命令枚举。
- 科研工作台，以及“实验结果分析与报告”视图；Skill 沉淀不出现在控制台。
- `fluid-research-workflow` 基础 Skill 和 RED/GREEN/人工审批的候选 Skill 生命周期。

## 执行平台

实验设计和 Gate 2 审批支持明确选择执行平台：

- `workstation_openfoam`：通过严格 host-key 校验的 SSH 调用固定 `fluid-worker` 协议，长任务在工作站脱离 SSH 会话继续运行。
- `hpc_slurm`：数据节点准备不可变制品，Login 节点提交和查询 Slurm，计算节点运行 OpenFOAM。

系统不会在平台不可用时静默切换到另一平台。真实主机、用户名、私钥路径和 `known_hosts` 路径只通过本地环境变量注入，不能提交到 Git。

## 部署工作站 Worker

工作站需要 Python 3.10+ 和 OpenFOAM Foundation 13。建议为 worker 使用独立虚拟环境，并创建一个负责加载 OpenFOAM 环境的固定包装器：

```bash
python3 -m venv ~/.local/share/fluid-scientist/venv
~/.local/share/fluid-scientist/venv/bin/python -m pip install fluid_scientist-0.1.0-py3-none-any.whl

mkdir -p ~/.local/bin
cat > ~/.local/bin/fluid-worker <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source /path/to/openfoam13/etc/bashrc
exec "$HOME/.local/share/fluid-scientist/venv/bin/fluid-worker" "$@"
EOF
chmod 700 ~/.local/bin/fluid-worker
```

部署后先在工作站本地执行 `fluid-worker doctor --json`。控制平面只会远程调用以下固定命令：

```text
fluid-worker doctor --json
fluid-worker submit --job-id <id> --diameter <m> --length <m> --velocity <m/s> --nu <m2/s> --density <kg/m3> --json
fluid-worker status <id> --json
fluid-worker cancel <id> --json
fluid-worker collect <id> --json
```

首次连接前，必须让研究人员在工作站本机读取 SSH host-key 指纹并在控制平面侧独立核对；禁止自动接受未知主机密钥。

## 真实层流圆管验收

真实基准使用 `foamRun -solver incompressibleFluid`。系统把 OpenFOAM 的运动压力和体积流量按算例密度转换为 Pa 与 kg/s，然后与 Hagen–Poiseuille 解析压降比较。只有同时满足以下条件才可标记为通过：

- `checkMesh` 通过且网格质量可接受；
- 最终残差不高于配置阈值；
- 入口/出口质量不平衡不高于 0.1%；
- 数值压降相对解析解误差不高于 5%。

求解器正常退出只代表作业完成，不代表基准可信；最终状态必须以 `collect` 结果和确定性验证为准。

## HPC 三节点契约

- **数据节点**：传输大文件、下载源码和依赖、编译 OpenFOAM/工具、生成校验和、发布不可变制品。
- **Login 节点**：仅执行类型化的 `sbatch`、`squeue`、`sacct` 和 `scancel`；不编译、不求解、不承载长期服务。
- **计算节点**：读取已批准制品，运行固定 OpenFOAM 命令并写回结果；不临时下载或编译。

三者通过配置的共享存储或受控同步交换 Case Manifest、作业脚本、制品和结果。真实 SSH/Slurm 接入需要平台主机、账号、共享根目录、分区、OpenFOAM 模块名和密钥策略；这些值不得提交到 Git。

## 本地基础设施

复制 `.env.example` 为 `.env`，只在本地修改密码，然后启动：

```powershell
docker compose --env-file .env -f infra/compose.yaml up -d
```

Compose 声明 PostgreSQL、Redis、Qdrant 和 MinIO。当前 Fake 纵向切片使用内存存储，因此即使不启动这些服务也能运行。

## 真实集成入口

业务层通过 `LLMProvider`、`EvidenceRetriever`、`SimulatorAdapter`、`JobScheduler`、`ArtifactStore` 和 `WorkflowRepository` Protocol 解耦。接入真实服务时实现这些接口，不修改科研核心。

OpenAI 模型 ID 仅为配置默认值。正式部署前需用最新官方文档核验模型和 Responses API 参数。真实 HPC/OpenFOAM 集成必须先通过层流圆管基准和 90° 弯管 Pilot，且不能绕过三个审批 Gate。

## 安全边界

- 不执行模型生成的任意 Shell。
- 不把密钥、主机名、用户名、内部绝对路径写入仓库或 Skill。
- Case Manifest 批准后不可变，修复生成新版本。
- 求解完成不等于可信；报告必须绑定仿真制品、确定性分析或可定位文献证据。
- 候选 Skill 必须先有失败基线、通过验证、脱敏和人工批准，才能发布。

详细设计见 `docs/superpowers/specs/2026-06-29-fluid-research-agent-design.md`，实施记录见 `docs/superpowers/plans/2026-06-29-fake-vertical-slice.md`。
