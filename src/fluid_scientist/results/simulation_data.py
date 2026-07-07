"""仿真数据模型 — 从 OpenFOAM 输出解析得到。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResidualData(BaseModel):
    """残差数据。"""

    time: list[float] = Field(default_factory=list)
    ux: list[float] = Field(default_factory=list)  # Ux residual
    uy: list[float] = Field(default_factory=list)
    uz: list[float] = Field(default_factory=list)
    p: list[float] = Field(default_factory=list)  # pressure residual


class ForceCoefficientsData(BaseModel):
    """力系数数据。"""

    time: list[float] = Field(default_factory=list)
    cd: list[float] = Field(default_factory=list)  # drag coefficient
    cl: list[float] = Field(default_factory=list)  # lift coefficient
    cm: list[float] = Field(default_factory=list)  # moment coefficient


class SurfaceFieldValueData(BaseModel):
    """面场值数据。"""

    name: str
    time: list[float] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)


class SimulationData(BaseModel):
    """完整仿真数据。"""

    residuals: ResidualData = Field(default_factory=ResidualData)
    forces: ForceCoefficientsData | None = None
    surface_values: list[SurfaceFieldValueData] = Field(default_factory=list)
    max_courant: list[float] = Field(default_factory=list)
    continuity_errors: list[float] = Field(default_factory=list)
    time_steps: list[float] = Field(default_factory=list)
    custom: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ForceCoefficientsData",
    "ResidualData",
    "SimulationData",
    "SurfaceFieldValueData",
]
