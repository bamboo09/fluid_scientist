"""Result ingestion and metric execution."""

from fluid_scientist.results.analysis import (
    AnalysisLayer,
    ScientificAnalysis,
    ScientificAnalyzer,
)
from fluid_scientist.results.ingestor import OpenFOAMResultIngestor
from fluid_scientist.results.log_parser import OpenFOAMLogParser
from fluid_scientist.results.metric_executor import (
    MetricExecutor,
    QualityCheckResult,
)
from fluid_scientist.results.metric_pipeline import execute_metric_pipeline
from fluid_scientist.results.models import MetricResult, ResultManifest, SimulationData
from fluid_scientist.results.postprocessing_parser import PostProcessingParser
from fluid_scientist.results.simulation_data import (
    ForceCoefficientsData,
    ResidualData,
    SurfaceFieldValueData,
)

__all__ = [
    "AnalysisLayer",
    "ForceCoefficientsData",
    "MetricExecutor",
    "MetricResult",
    "OpenFOAMLogParser",
    "OpenFOAMResultIngestor",
    "PostProcessingParser",
    "QualityCheckResult",
    "ResidualData",
    "ResultManifest",
    "ScientificAnalysis",
    "ScientificAnalyzer",
    "SimulationData",
    "SurfaceFieldValueData",
    "execute_metric_pipeline",
]
