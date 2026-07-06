# Fluid Scientist Docker 部署指南

## 1. 前提条件

| 项目 | 要求 |
|------|------|
| 操作系统 | Linux / macOS / Windows（含 Docker Desktop） |
| Docker | 28.0+ |
| Docker Compose | v2.20+ |
| 内存 | ≥ 4GB（基础服务） / ≥ 8GB（含应用） |
| 磁盘 | ≥ 10GB 可用空间 |
| 网络 | 可访问 Docker Hub 或配置了镜像加速器 |

## 2. 文件结构

```
fluid_scientist/
├── Dockerfile              # 多阶段构建镜像
├── .dockerignore           # 构建上下文排除规则
├── .env.docker.example     # Docker 部署环境变量模板
├── infra/
│   └── compose.yaml        # Docker Compose 编排文件
└── ssh-keys/               # SSH 密钥目录（需手动创建）
    ├── id_ed25519          # OpenFOAM 工作站私钥
    └── known_hosts         # 已验证的主机公钥
```

## 3. 快速开始（Fake 模式）

Fake 模式不需要 OpenAI API Key、SSH 工作站或 OpenFOAM，适合本地演示和功能验证。

```bash
# 1. 进入项目目录
cd fluid_scientist

# 2. 启动仅应用容器（使用 SQLite，无需外部服务）
docker compose -f infra/compose.yaml --profile app up --build

# 3. 访问 http://localhost:8000
```

## 4. 完整部署（Real 模式）

Real 模式连接真实的 OpenFOAM 工作站和 LLM API。

### 4.1 准备 SSH 密钥

```bash
# 创建 SSH 密钥目录
mkdir -p ssh-keys

# 复制工作站私钥（必须是 ed25519 格式）
cp ~/.ssh/id_ed25519 ssh-keys/

# 生成 known_hosts（在工作站本机执行）
ssh-keyscan -H <workstation-ip> >> ssh-keys/known_hosts

# 设置权限
chmod 700 ssh-keys
chmod 600 ssh-keys/id_ed25519
chmod 644 ssh-keys/known_hosts
```

### 4.2 配置环境变量

```bash
# 复制环境变量模板
cp .env.docker.example .env

# 编辑 .env 文件
vi .env
```

关键配置项：

```env
# 切换为真实模式
FLUID_APP_MODE=real

# 填入 OpenAI API Key
FLUID_OPENAI__API_KEY=sk-xxxxx

# 配置工作站
FLUID_WORKSTATION__HOSTS=["192.168.1.100"]
FLUID_WORKSTATION__USERNAME=ls
FLUID_WORKSTATION__IDENTITY_FILE=/app/ssh-keys/id_ed25519
FLUID_WORKSTATION__KNOWN_HOSTS_FILE=/app/ssh-keys/known_hosts
```

### 4.3 启动全部服务

```bash
# 启动应用 + 基础设施（PostgreSQL、Redis、Qdrant、MinIO）
docker compose -f infra/compose.yaml --profile full up --build -d

# 查看日志
docker compose -f infra/compose.yaml logs -f api

# 检查健康状态
curl http://localhost:8000/health
```

### 4.4 使用 PostgreSQL（可选）

默认使用容器内 SQLite。如需切换到 PostgreSQL：

```env
# 在 .env 中取消注释
FLUID_DATABASE__URL=postgresql://fluid_scientist:change-me-in-production@postgres:5432/fluid_scientist
```

## 5. 开发模式

开发模式挂载本地源码，支持热重载：

```bash
# 启动开发容器（仅应用，无外部服务）
docker compose -f infra/compose.yaml --profile dev up --build

# 修改代码后自动重载
```

## 6. 服务端口

| 服务 | 端口 | 用途 |
|------|------|------|
| API Server | 8000 | FastAPI 应用 + Web 工作台 |
| PostgreSQL | 5432 | 业务数据库 |
| Redis | 6379 | 缓存与任务队列 |
| Qdrant | 6333 / 6334 | 向量数据库（HTTP / gRPC） |
| MinIO | 9000 / 9001 | 对象存储（API / 控制台） |

## 7. 数据持久化

Docker Compose 使用命名卷持久化数据：

| 卷名 | 挂载点 | 用途 |
|------|--------|------|
| postgres-data | /var/lib/postgresql/data | PostgreSQL 数据 |
| redis-data | /data | Redis 持久化 |
| qdrant-data | /qdrant/storage | 向量索引 |
| minio-data | /data | 对象存储文件 |
| app-data | /app/data | SQLite 数据库与临时文件 |

备份数据：

```bash
# 备份 PostgreSQL
docker compose -f infra/compose.yaml exec postgres pg_dump -U fluid_scientist fluid_scientist > backup.sql

# 备份应用数据
docker run --rm -v fluid-scientist_app-data:/data -v $(pwd):/backup alpine tar czf /backup/app-data.tar.gz /data
```

## 8. 镜像构建优化

Dockerfile 采用多阶段构建：

- **Builder 阶段**：安装编译工具链，编译 C 扩展，安装 Python 依赖到虚拟环境
- **Runtime 阶段**：仅复制虚拟环境和应用代码，以非 root 用户运行

层缓存策略：`pyproject.toml` 和 `README.md` 单独一层，源码变更时不会重新安装依赖。

## 9. 安全注意事项

1. **SSH 密钥**：私钥通过只读 volume 挂载，不进入镜像层
2. **API Key**：通过环境变量注入，不写入 Dockerfile 或 .env.docker.example
3. **非 Root 运行**：容器以 `fluid` 用户（UID 1000）运行
4. **主机密钥校验**：禁止自动接受未知 SSH 主机密钥
5. **网络隔离**：生产环境建议不暴露 PostgreSQL/Redis 端口，仅通过 Docker 内部网络通信

## 10. 故障排查

### 容器无法启动

```bash
# 查看容器日志
docker compose -f infra/compose.yaml logs api

# 检查健康状态
docker compose -f infra/compose.yaml ps
```

### SSH 连接失败

```bash
# 进入容器测试 SSH
docker compose -f infra/compose.yaml exec api ssh -i /app/ssh-keys/id_ed25519 <user>@<host> echo ok

# 检查 known_hosts
docker compose -f infra/compose.yaml exec api cat /app/ssh-keys/known_hosts
```

### 数据库迁移

```bash
# 进入容器执行迁移
docker compose -f infra/compose.yaml exec api python -c "from fluid_scientist.adapters.sql_repository import Base; from sqlalchemy import create_engine; Base.metadata.create_all(create_engine('sqlite:///app/data/fluid_scientist.db'))"
```

## 11. 清理

```bash
# 停止全部服务
docker compose -f infra/compose.yaml down

# 停止并删除数据卷（谨慎操作！）
docker compose -f infra/compose.yaml down -v

# 删除镜像
docker rmi fluid-scientist:latest
```