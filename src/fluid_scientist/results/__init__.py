"""Result Ingestor 模块 — 解析 OpenFOAM 输出结果并接入 Metric Engine。

提供从 OpenFOAM 求解器日志与 functionObject 后处理输出中提取仿真数据
的能力，并将数据转换为 Metric Engine 所需格式以生成指标报告。
"""

from fluid_scientist.results.ingestor import OpenFOAMResultIngestor
from fluid_scientist.results.log_parser import OpenFOAMLogParser
from fluid_scientist.results.metric_pipeline import execute_metric_pipeline
from fluid_scientist.results.postprocessing_parser import PostProcessingParser
from fluid_scientist.results.simulation_data import (
    ForceCoefficientsData,
    ResidualData,
    SimulationData,
    SurfaceFieldValueData,
)

__all__ = [
    "ForceCoefficientsData",
    "OpenFOAMLogParser",
    "OpenFOAMResultIngestor",
    "PostProcessingParser",
    "ResidualData",
    "SimulationData",
    "SurfaceFieldValueData",
    "execute_metric_pipeline",
]
