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


class MetricBinding(BaseModel):
    """指标与数据源的绑定。"""

    metric_id: str
    source: str  # 引用 spatial_sampling.id 或 function_object.name
    function_object: str | None = None  # 引用 FunctionObjectSpec.name


class MeasurementPlan(BaseModel):
    """MeasurementPlan — 指标驱动的模拟内部采样配置。

    与 DOEPlan（实验设计矩阵）不同，MeasurementPlan 定义的是单次模拟内部
    需要采样哪些场变量、在哪些位置、以什么频率采样，以便后续提取研究指标。
    """

    required_fields: list[FieldOutputSpec] = Field(default_factory=list)
    function_objects: list[FunctionObjectSpec] = Field(default_factory=list)
    spatial_sampling: list[SpatialSamplingSpec] = Field(default_factory=list)
    time_sampling: TimeSamplingSpec = Field(default_factory=TimeSamplingSpec)
    metric_bindings: list[MetricBinding] = Field(default_factory=list)


__all__ = [
    "FieldOutputSpec",
    "FunctionObjectSpec",
    "FunctionObjectType",
    "MeasurementPlan",
    "MetricBinding",
    "SpatialSamplingSpec",
    "SpatialSamplingType",
    "TimeSamplingSpec",
]
