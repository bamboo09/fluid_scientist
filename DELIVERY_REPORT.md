# Fluid Scientist 二维可配置圆柱绕流实验 - 最终交付报告

## 1. 最终架构和实际接入路径

### API 路由
```
POST /api/v5/cylinder-flow/route       — 场景识别
POST /api/v5/cylinder-flow/draft       — 创建草案 (6-Pass Pipeline)
POST /api/v5/cylinder-flow/confirm     — 确认草案
POST /api/v5/cylinder-flow/compile     — 编译 OpenFOAM Case
POST /api/v5/cylinder-flow/execute     — 工作站执行 + 后处理
GET  /api/v5/cylinder-flow/jobs/{id}/plots      — 列出图片
GET  /api/v5/cylinder-flow/jobs/{id}/plots/{name} — 下载图片
GET  /api/v5/cylinder-flow/jobs/{id}/results    — 查询结果
```

### 数据流
```
用户自然语言 → CylinderFlow2DSceneRouter.route()
  → CylinderFlow2DV1Pipeline.run() (6-Pass: 事实提取→歧义分析→归一化→派生→观测量→Critic)
  → CylinderFlow2DExperimentSpecV1 (ProvenanceField 包装)
  → 用户澄清 → Confirm 端点 (GeometryNormalizer→DerivedFieldResolver→...→ReadinessEvaluator)
  → SPEC_CONFIRMED
  → SpecAdapter.adapt() → ObstacleFlowExperimentSpecV1
  → ObstacleFlowCompiler.compile() → 15个OpenFOAM文件 (tar.gz)
  → WorkstationExecutor (SSH上传→blockMesh→snappyHexMesh→checkMesh→smoke test→full run)
  → Postprocessor (foamToVTK→VTK解析→matplotlib绘图)
  → 5张PNG图片 + forceCoeffs.dat
```

### 关键模块
| 模块 | 文件 | 职责 |
|------|------|------|
| Pipeline | `cylinder_flow_2d/pipeline.py` | 6-Pass LLM 分析管道 |
| Models | `cylinder_flow_2d/models.py` | Spec 语义模型 + ProvenanceField |
| Execution | `cylinder_flow_2d/execution.py` | 工作站执行 + VTK后处理 |
| Compiler | `obstacle_flow/compiler.py` | OpenFOAM Foundation 13 编译 |
| Mesh | `obstacle_flow/mesh.py` | blockMesh + snappyHexMesh |
| Router | `api/cylinder_flow_router.py` | REST API 端点 |

## 2. 修改的主要模块

### 本轮修复的文件

| 文件 | 修改内容 |
|------|---------|
| `obstacle_flow/mesh.py` | snappyHexMeshDict Foundation 13 兼容性修复: geometry使用`file`关键字, meshQualityControls使用`#includeEtc`, 添加`mergeTolerance`, `resolveFeatureAngle`, `insidePoint`替代`locationInMesh`, 移除`resolveFeatureSlice` |
| `obstacle_flow/compiler.py` | controlDict添加`libs ("libforces.so")`; forceCoeffs添加`dragDir/liftDir/pitchAxis/CofR/rho rhoInf`; physicalProperties添加`viscosityModel Newtonian`; fvSchemes添加`wallDist`; fvSolution添加`UFinal/kFinal/omegaFinal`; surfaces使用`cuttingPlane`+`planeType pointAndNormal`; probes/surfaces/residuals/vorticity添加`libs`声明 |
| `cylinder_flow_2d/execution.py` | sed替代awk修复SSH引号问题; `_fetch_field_data`改用foamToVTK替代sample; 新增`_parse_vtk_ascii`解析VTK ASCII文件; VTK文件查找支持Foundation 13命名格式 |
| `api/cylinder_flow_router.py` | 修复smoke_test/run状态字段映射; 新增`/plots`和`/plots/{name}`图片服务端点 |

## 3. 数据库和API变化

- 使用内存存储 `_spec_store` 和 `_execution_store` (生产环境需替换为数据库)
- 新增 4 个 API 端点 (compile, execute, plots, plots/{name})
- ExecuteResponse 修复字段: `smoke_test_status` 基于报告 `status` 字段, `run_status` 基于仿真 `status` 字段

## 4. 模型调用策略

6-Pass LLM Pipeline:
1. Pass 1: 事实提取
2. Pass 2: 歧义和冲突分析
3. Pass 3: 科学语义归一化
4. Pass 4: 确定性派生字段 (代码完成, 不依赖模型)
5. Pass 5: 观测量提取和推荐
6. Pass 6: 独立Critic检查

确定性代码归一化:
- 圆柱半径→直径→特征尺度 (FORMULA_DERIVED)
- 二维 front/back = empty (强制)
- 字段优先级: USER_CONFIRMED > USER_EXPLICIT > FORMULA_DERIVED > SYSTEM_DERIVED > MODEL_RECOMMENDED

## 5. 完整状态机

```
NEEDS_CLARIFICATION → AWAITING_CONFIRMATION → READY_TO_CONFIRM → SPEC_CONFIRMED
     ↓                                          ↓
  用户澄清                                   用户确认
```

执行状态链:
```
SPEC_CONFIRMED → GEOMETRY_VALIDATED → MESH_GENERATED → MESH_VALIDATED →
CASE_COMPILED → STATIC_VALIDATED → SERIAL_SMOKE_TEST_PASSED →
READY_TO_SUBMIT → SUBMITTED → RUNNING → COMPLETED →
POSTPROCESSING → PLOTS_GENERATED → RESULTS_READY
```

## 6. 工作站环境信息

| 项目 | 值 |
|------|-----|
| 工作站地址 | 10.129.177.241 |
| SSH用户 | ls |
| SSH密钥 | ~/.ssh/fluid_scientist_ed25519 |
| OpenFOAM版本 | Foundation 13 (/opt/openfoam13/) |
| CPU核心 | 64 |
| 内存 | 125 GB |
| 磁盘 | 766 GB |

## 7. 验证结果汇总

### 真实工作站验证 (非mock)

| 验证项 | 第一次运行 | 第二次运行 |
|--------|-----------|-----------|
| Job ID | job_1020349f7254 | job_5680ce3ddbb9 |
| 草案创建 | ✅ NEEDS_CLARIFICATION | ✅ NEEDS_CLARIFICATION |
| 草案确认 | ✅ SPEC_CONFIRMED | ✅ SPEC_CONFIRMED |
| 编译 | ✅ 15个文件 | ✅ 15个文件 |
| blockMesh | ✅ PASSED | ✅ PASSED |
| snappyHexMesh | ✅ cylinder patch (496 faces) | ✅ cylinder patch |
| checkMesh | ✅ PASSED | ✅ PASSED |
| 串行Smoke Test | ✅ PASSED | ✅ PASSED |
| 正式运行 | ✅ COMPLETED | ✅ COMPLETED |
| velocity_magnitude.png | ✅ 51KB | ✅ 51KB |
| ux.png | ✅ 57KB | ✅ 57KB |
| pressure.png | ✅ 42KB | ✅ 42KB |
| streamlines.png | ✅ 134KB | ✅ 134KB |
| cd_cl_time_series.png | ✅ 43KB | ✅ 43KB |
| 图片服务端点 | ✅ 可访问 | ✅ 可访问 |

### 图片内容验证

velocity_magnitude.png 经验证包含:
- 速度大小云图 (viridis colormap, 0-3.2 m/s)
- 圆柱几何 (黑色圆形, x≈5m, y≈2m)
- 坐标轴 (x [m], y [m])
- 色标 (|U| [m/s])
- 流场特征正确: 圆柱加速(黄色)、尾迹减速、涡街形成
- 元数据: Run ID, Spec版本, 仿真时间

## 8. 测试场景

### 场景A: 平底恒速入口圆柱绕流 ✅
输入: "二维圆柱绕流实验，流体为水，左侧以2 m/s恒速流入，右侧压力出口，顶部滑移，底部无滑息，圆柱半径0.1 m，圆心位于x=5 m, y=2 m处，观测圆柱下游截面x=10 m处平均速度"
- 连续两次成功
- 5张图片生成

### 场景B/C: 待测试
- 场景B (时变入口): 需要修改测试输入
- 场景C (周期压力驱动+凸起): 需要修改测试输入

## 9. 仍存在但不阻塞的限制

1. **前端集成未完成**: 当前API端点可用，但前端页面尚未连接到cylinder-flow API
2. **内存存储**: spec和execution使用内存存储，重启后丢失
3. **仿真时间**: 当前限制为0.5s以适应HTTP超时，生产环境应使用异步任务队列
4. **涡量云图**: vorticity场在部分运行中可能缺失(postProcess可能失败)，但有4张其他图片保证
5. **并行Smoke Test**: 当前仅验证串行，并行需decomposePar+mpirun
6. **场景B/C**: 尚未测试，需要适配时变入口和周期压力驱动的编译逻辑

## 10. Commit列表

本轮修改的文件 (未git commit，直接在运行目录修改):
- `src/fluid_scientist/obstacle_flow/compiler.py` — Foundation 13 编译修复
- `src/fluid_scientist/obstacle_flow/mesh.py` — snappyHexMeshDict 兼容性修复
- `src/fluid_scientist/cylinder_flow_2d/execution.py` — foamToVTK后处理 + VTK解析
- `src/fluid_scientist/api/cylinder_flow_router.py` — 状态修复 + 图片端点

## 11. 证据文件位置

| 证据 | 位置 |
|------|------|
| 测试脚本 | `c:\Users\baoxu\.trae-cn\work\6a54e60b61da2d346acf6b5d\e2e_test.py` |
| 生成图片 (Run 1) | `d:\desktop\AI FOR SCIENCE\results\job_1020349f7254\` |
| 生成图片 (Run 2) | `d:\desktop\AI FOR SCIENCE\results\job_5680ce3ddbb9\` |
| 工作站日志 | `ls@10.129.177.241:/home/ls/fluid_scientist/runs/job_*/log.*` |
| 后端运行 | `http://localhost:8000/api/v5/cylinder-flow/health` |
