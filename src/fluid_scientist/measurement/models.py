"""MeasurementPlan models — metric-driven in-simulation sampling configuration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from fluid_scientist.compat import StrEnum


class FunctionObjectType(StrEnum):
    """OpenFOAM functionObject types supported for metric extraction."""

    FORCE_COEFFS = "forceCoeffs"
    SURFACE_FIELD_VALUE = "surfaceFieldValue"
    FIELD_VALUE = "fieldValue"
    PROBES = "probes"
    FIELD_AVERAGE = "fieldAverage"


class SpatialSamplingType(StrEnum):
    """Geometry of a spatial sampling location."""

    PLANE = "plane"
    LINE = "line"
    POINT = "point"
    SURFACE = "surface"
    VOLUME = "volume"


class FieldOutputSpec(BaseModel):
    """场变量输出规格。"""

    field_name: str  # U, p, k, omega, etc.
    write_interval: int = 100


class FunctionObjectSpec(BaseModel):
    """OpenFOAM functionObject 规格。"""

    type: FunctionObjectType
    name: str = ""
    target_patch: str | None = None  # e.g., "cylinder", "inlet"
    field: str | None = None  # e.g., "U", "p"
    operation: str | None = None  # areaAverage, areaIntegrate, etc.
    surface: str | None = None  # 引用 spatial_sampling 的 id
    write_interval: int = 100
    additional_config: dict[str, Any] = Field(default_factory=dict)


class SpatialSamplingSpec(BaseModel):
    """空间采样位置规格。"""

    id: str
    type: SpatialSamplingType
    location: dict[str, float] = Field(default_factory=dict)  # e.g., {"x": 5.0}
    description: str = ""


class TimeSamplingSpec(BaseModel):
    """时间采样规格。"""

    start_time: float = 0.0
    end_time: float = 100.0
    interval: float = 0.01
    write_control: str = "timeStep"  # timeStep, runTime, etc.
    # Physical context for why these values were chosen
    characteristic_length: float | None = None
    characteristic_velocity: float | None = None
    convection_time: float | None = None
    estimated_frequency: float | None = None
    nyquist_frequency: float | None = None
    samples_per_cycle: int | None = None
    minimum_cycles: int | None = None
    derivation_reason: str = ""


class MetricBinding(BaseModel):
    """指标与数据源的绑定。"""

    metric_id: str
    source: str  # 引用 spatial_sampling.id 或 function_object.name
    function_object: str | None = None  # 引用 FunctionObjectSpec.name


class ProbeSpec(BaseModel):
    """探针采样规格。"""

    id: str
    field: str  # U, p, etc.
    positions: list[dict[str, float]] = Field(default_factory=list)
    write_interval: int = 1


class LineSamplingSpec(BaseModel):
    """线采样规格。"""

    id: str
    field: str
    start: dict[str, float] = Field(default_factory=dict)
    end: dict[str, float] = Field(default_factory=dict)
    num_points: int = 50


class VolumeSamplingSpec(BaseModel):
    """体积采样规格。"""

    id: str
    field: str
    bounds: dict[str, list[float]] = Field(default_factory=dict)
    resolution: list[int] = Field(default_factory=list)
    write_interval: int = 100


class StorageEstimate(BaseModel):
    """预计存储量估算。"""

    estimated_bytes: int = 0
    breakdown: dict[str, int] = Field(default_factory=dict)  # category -> bytes
    exceeds_budget: bool = False
    budget_bytes: int | None = None


class MeasurementPlan(BaseModel):
    """MeasurementPlan — 指标驱动的模拟内部采样配置。

    与 DOEPlan（实验设计矩阵）不同，MeasurementPlan 定义的是单次模拟内部
    需要采样哪些场变量、在哪些位置、以什么频率采样，以便后续提取研究指标。
    """

    required_fields: list[FieldOutputSpec] = Field(default_factory=list)
    function_objects: list[FunctionObjectSpec] = Field(default_factory=list)
    spatial_sampling: list[SpatialSamplingSpec] = Field(default_factory=list)
    probes: list[ProbeSpec] = Field(default_factory=list)
    lines: list[LineSamplingSpec] = Field(default_factory=list)
    time_sampling: TimeSamplingSpec = Field(default_factory=TimeSamplingSpec)
    metric_bindings: list[MetricBinding] = Field(default_factory=list)
    storage_estimate: StorageEstimate | None = None


__all__ = [
    "FieldOutputSpec",
    "FunctionObjectSpec",
    "FunctionObjectType",
    "LineSamplingSpec",
    "MeasurementPlan",
    "MetricBinding",
    "ProbeSpec",
    "SpatialSamplingSpec",
    "SpatialSamplingType",
    "StorageEstimate",
    "TimeSamplingSpec",
    "VolumeSamplingSpec",
]
