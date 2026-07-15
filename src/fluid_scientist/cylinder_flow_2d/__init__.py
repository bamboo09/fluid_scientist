"""CylinderFlow2D — 二维可配置圆柱绕流实验族.

This module implements the full pipeline for 2D configurable cylinder flow
experiments. It replaces the generic obstacle_flow module with a dedicated
pipeline that handles cylinder flow scenarios with proper multi-pass reasoning,
geometry normalization, boundary topology resolution, observable extraction,
and draft readiness evaluation.

Pipeline flow:
    Natural language input
    → Scene routing (CylinderFlow2DSceneRouter)
    → Multi-pass reasoning (CylinderFlow2DV1Pipeline)
        Pass 1: Fact extraction
        Pass 2: Ambiguity detection
        Pass 3: Scientific normalization
        Pass 4: Deterministic field derivation
        Pass 5: Observable extraction + recommendation
        Pass 5b: Analysis goal generation
        Pass 6: Critic review + auto-repair
    → Readiness evaluation
    → User confirmation
"""

from fluid_scientist.cylinder_flow_2d.models import (
    AnalysisGoalSpec,
    BottomProfileSpec,
    BoundaryConfig,
    BoundarySpec,
    BumpProfileType,
    CylinderFlow2DExperimentSpecV1,
    CylinderSpec,
    CylinderWallType,
    DecisionSummary,
    DomainSpec,
    DraftStatus,
    FieldSource,
    FieldStatus,
    FlowMode,
    FlowRegime,
    FluidSpec,
    ForcingSpec,
    InletProfileSpec,
    InitialConditionsSpec,
    ModelPolicy,
    ObservableSpec,
    ObservableType,
    PressureGradientUnit,
    ProvenanceField,
    SemanticBoundaryType,
    SimulationSpec,
    SpatialType,
    TemporalType,
    TimeMode,
)
from fluid_scientist.cylinder_flow_2d.geometry_normalizer import (
    CylinderFlow2DDerivedFieldResolver,
    CylinderFlow2DGeometryNormalizer,
)
from fluid_scientist.cylinder_flow_2d.boundary_topology import (
    CylinderFlow2DBoundaryCombinationValidator,
    CylinderFlow2DBoundaryTopologyResolver,
)
from fluid_scientist.cylinder_flow_2d.observable import (
    CylinderFlow2DObservableExtractor,
    CylinderFlow2DObservableRecommender,
    CylinderFlow2DObservableValidator,
)
from fluid_scientist.cylinder_flow_2d.analysis_goals import (
    CylinderFlow2DAnalysisGoalBuilder,
)
from fluid_scientist.cylinder_flow_2d.readiness import (
    CylinderFlow2DDraftReadinessEvaluator,
)
from fluid_scientist.cylinder_flow_2d.critic import (
    CriticResult,
    CylinderFlow2DCoverageChecker,
    CylinderFlow2DCritic,
)
from fluid_scientist.cylinder_flow_2d.pipeline import (
    CylinderFlow2DV1Pipeline,
    PipelineRunResult,
    PipelineStageResult,
)
from fluid_scientist.cylinder_flow_2d.router import (
    CylinderFlow2DSceneRouter,
    SceneRouteResult,
)
from fluid_scientist.cylinder_flow_2d.execution import (
    ExecutionOrchestrator,
    ExecutionResult,
    Postprocessor,
    SpecAdapter,
    WorkstationExecutor,
)

__all__ = [
    "AnalysisGoalSpec",
    "BottomProfileSpec",
    "BoundaryConfig",
    "BoundarySpec",
    "BumpProfileType",
    "CriticResult",
    "CylinderFlow2DAnalysisGoalBuilder",
    "CylinderFlow2DBoundaryCombinationValidator",
    "CylinderFlow2DBoundaryTopologyResolver",
    "CylinderFlow2DCoverageChecker",
    "CylinderFlow2DCritic",
    "CylinderFlow2DDerivedFieldResolver",
    "CylinderFlow2DDraftReadinessEvaluator",
    "CylinderFlow2DExperimentSpecV1",
    "CylinderFlow2DGeometryNormalizer",
    "CylinderFlow2DObservableExtractor",
    "CylinderFlow2DObservableRecommender",
    "CylinderFlow2DObservableValidator",
    "CylinderFlow2DSceneRouter",
    "CylinderFlow2DV1Pipeline",
    "ExecutionOrchestrator",
    "ExecutionResult",
    "Postprocessor",
    "SpecAdapter",
    "WorkstationExecutor",
    "CylinderSpec",
    "CylinderWallType",
    "DecisionSummary",
    "DomainSpec",
    "DraftStatus",
    "FieldSource",
    "FieldStatus",
    "FlowMode",
    "FlowRegime",
    "FluidSpec",
    "ForcingSpec",
    "InletProfileSpec",
    "InitialConditionsSpec",
    "ModelPolicy",
    "ObservableSpec",
    "ObservableType",
    "PipelineRunResult",
    "PipelineStageResult",
    "PressureGradientUnit",
    "ProvenanceField",
    "SceneRouteResult",
    "SemanticBoundaryType",
    "SimulationSpec",
    "SpatialType",
    "TemporalType",
    "TimeMode",
]
