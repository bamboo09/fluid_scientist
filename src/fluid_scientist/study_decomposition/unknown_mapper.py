"""Unknown parameter and metric mapping for the v5 workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UnknownParameterMapping(BaseModel):
    """Structured mapping for an unknown parameter discovered in user input."""
    canonical_id: str
    display_name: str
    category: Literal[
        "geometry", "material_property", "initial_condition",
        "boundary_condition", "physics_model", "mesh",
        "numerics", "metric", "measurement", "postprocess",
        "compute", "custom",
    ]
    unit: str | None = None
    expected_type: str = "float"
    description: str = ""
    affects: list[str] = Field(default_factory=list)
    requires_user_value: bool = True
    can_recommend_default: bool = False
    capability_check_required: bool = False
    confidence: float = 0.0
    reason: str = ""


class MetricDefinition(BaseModel):
    """Structured definition for a metric requested by the user."""
    metric_id: str
    display_name: str
    category: Literal[
        "force", "pressure", "velocity", "thermal",
        "vorticity", "uniformity", "frequency",
        "reattachment", "mixing", "custom",
    ]
    formula: str | None = None
    required_fields: list[str] = Field(default_factory=list)
    required_sampling: list[dict] = Field(default_factory=list)
    postprocess_method: Literal[
        "openfoam_function_object", "python_postprocess",
        "field_export", "custom_extension",
    ] = "python_postprocess"
    unit: str | None = None
    capability_check_required: bool = True


__all__ = [
    "UnknownParameterMapping",
    "MetricDefinition",
]
