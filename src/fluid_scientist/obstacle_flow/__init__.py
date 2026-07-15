"""ConfigurableObstacleFlow2D — 二维可配置障碍物流动实验族.

This module implements the full pipeline for 2D configurable obstacle flow
experiments using OpenFOAM Foundation 13.

Pipeline::

    Natural language
      -> Scenario identification
      -> Parameter and boundary extraction
      -> Flow topology detection
      -> Clarification
      -> User confirms experiment spec
      -> Geometry and mesh generation
      -> OpenFOAM 13 deterministic compilation
      -> Workstation Smoke Test
      -> Formal computation
      -> Workstation Python post-processing
      -> Flow field plots and metrics
      -> Images and results returned to chat
"""

from fluid_scientist.obstacle_flow.models import (
    BodyForceSpec,
    BoundaryConfig,
    BoundarySpec,
    BoundaryType,
    BumpProfileType,
    BumpSpec,
    CylinderBoundaryType,
    CylinderSpec,
    DomainSpec,
    FieldProvenance,
    FlowDefinitionSpec,
    FlowMode,
    FlowRegime,
    FluidSpec,
    ForcingSpec,
    GeometryFeasibilityError,
    GeometryFeasibilityValidator,
    InitialVelocitySpec,
    InletProfileSpec,
    ObservableSpec,
    ObservableType,
    ObstacleFlowExperimentSpecV1,
    PlotRequest,
    PressureGradientSpec,
    PressureGradientUnit,
    SimulationSpec,
    SpatialType,
    SpecSource,
    SpecStatus,
    TemporalType,
    TimeMode,
    TurbulenceModel,
)
from fluid_scientist.obstacle_flow.boundary_validator import (
    BoundaryCombinationValidator,
    BoundaryTopologyError,
)
from fluid_scientist.obstacle_flow.compiler import (
    ObstacleFlowCompiler,
    ObstacleFlowCompilerRegistry,
)
from fluid_scientist.obstacle_flow.geometry import (
    BumpProfileGenerator,
    CylinderGeometryBuilder,
)
from fluid_scientist.obstacle_flow.mesh import ObstacleFlowMeshBackend
from fluid_scientist.obstacle_flow.static_validator import (
    ObstacleFlowStaticValidator,
)
from fluid_scientist.obstacle_flow.postprocessing import (
    PlotSpec,
    ResultManifest,
    WorkstationObstacleFlowPostprocessor,
)

__all__ = [
    "BodyForceSpec",
    "BoundaryCombinationValidator",
    "BoundaryConfig",
    "BoundarySpec",
    "BoundaryTopologyError",
    "BoundaryType",
    "BumpProfileGenerator",
    "BumpProfileType",
    "BumpSpec",
    "CylinderBoundaryType",
    "CylinderGeometryBuilder",
    "CylinderSpec",
    "DomainSpec",
    "FieldProvenance",
    "FlowDefinitionSpec",
    "FlowMode",
    "FlowRegime",
    "FluidSpec",
    "ForcingSpec",
    "GeometryFeasibilityError",
    "GeometryFeasibilityValidator",
    "InitialVelocitySpec",
    "InletProfileSpec",
    "ObservableSpec",
    "ObservableType",
    "ObstacleFlowCompiler",
    "ObstacleFlowCompilerRegistry",
    "ObstacleFlowExperimentSpecV1",
    "ObstacleFlowMeshBackend",
    "ObstacleFlowStaticValidator",
    "PlotRequest",
    "PlotSpec",
    "PressureGradientSpec",
    "PressureGradientUnit",
    "ResultManifest",
    "SimulationSpec",
    "SpatialType",
    "SpecSource",
    "SpecStatus",
    "TemporalType",
    "TimeMode",
    "TurbulenceModel",
    "WorkstationObstacleFlowPostprocessor",
]
