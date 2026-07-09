"""Case plan data models.

Defines the Pydantic v2 data structures used by the case plan workflow.
A :class:`CasePlan` is an intermediate representation between a confirmed
:class:`~fluid_scientist.draft.models.ExperimentDraft` and the OpenFOAM case
files produced by the :class:`~fluid_scientist.case_plan.compiler.NativeCaseCompiler`.

The case plan captures everything needed to generate a complete OpenFOAM
case: geometry, mesh, boundary/initial conditions, physical models,
numerics, and measurement (functionObject) specifications, along with a
capability check result indicating whether the case can be compiled
natively or requires extensions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from fluid_scientist.compat import UTC

# ---------------------------------------------------------------------------
# 1. MissingCapability
# ---------------------------------------------------------------------------


class MissingCapability(BaseModel):
    """A capability required by the case plan that is not yet available.

    ``severity`` controls whether the missing capability blocks compilation
    (``"blocking"``) or merely produces a warning (``"warning"``).
    ``extension_spec_id`` links to a :class:`CodeExtensionSpec` that, once
    approved and registered, would satisfy this capability.
    """

    capability_id: str
    capability_type: str  # geometry_generator, boundary_condition_writer, etc.
    reason: str
    severity: Literal["blocking", "warning"] = "blocking"
    extension_spec_id: str | None = None
    required_by: list[str] = Field(default_factory=list)
    suggested_resolution: Literal[
        "ask_user_to_simplify",
        "use_supported_alternative",
        "create_code_extension",
    ] = "create_code_extension"
    alternative_options: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. FunctionObjectSpec
# ---------------------------------------------------------------------------


class FunctionObjectSpec(BaseModel):
    """Specification for a single OpenFOAM functionObject.

    ``function_object_id`` must be unique within a
    :class:`MeasurementPlanSpec`.  ``function_object_type`` corresponds to
    the OpenFOAM functionObject type (forces, forceCoeffs, probes,
    fieldAverage, etc.).  ``configuration`` holds type-specific parameters
    (liftDir, dragDir, probeLocations, ...).
    """

    function_object_id: str
    function_object_type: str
    fields: list[str] = Field(default_factory=list)
    patches: list[str] = Field(default_factory=list)
    output_directory: str = ""
    configuration: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 3. MeasurementPlanSpec
# ---------------------------------------------------------------------------


class MeasurementPlanSpec(BaseModel):
    """Measurement plan aggregating functionObjects and sampling points.

    Produced by the :class:`CasePlanGenerator` from the draft's
    ``requested_outputs`` and consumed by the
    :class:`NativeCaseCompiler` to populate the ``functions`` block of
    ``controlDict``.
    """

    function_objects: list[FunctionObjectSpec] = Field(default_factory=list)
    sample_points: list[dict] = Field(default_factory=list)
    write_interval: int = 100
    output_directory: str = "postProcessing"


# ---------------------------------------------------------------------------
# 4. CasePlan
# ---------------------------------------------------------------------------


class CasePlan(BaseModel):
    """A compile-ready plan for a single OpenFOAM case.

    A :class:`CasePlan` is generated deterministically from a confirmed
    :class:`~fluid_scientist.draft.models.ExperimentDraft` by the
    :class:`~fluid_scientist.case_plan.generator.CasePlanGenerator`.  It
    captures every aspect of the case — geometry, mesh, boundary/initial
    conditions, physical models, numerics, and measurement — as structured
    dicts that the :class:`NativeCaseCompiler` turns into an in-memory
    OpenFOAM case structure.

    ``can_compile`` reflects the capability check result: when ``False``,
    ``blocking_reasons`` explains why and ``missing_capabilities`` lists
    the specific gaps.
    """

    case_plan_id: str
    draft_id: str
    draft_version: int
    case_type: str
    solver: str
    dimensions: Literal["2D", "3D"] = "3D"
    geometry_plan: dict = Field(default_factory=dict)
    mesh_plan: dict = Field(default_factory=dict)
    boundary_condition_plan: dict = Field(default_factory=dict)
    initial_condition_plan: dict = Field(default_factory=dict)
    physical_model_plan: dict = Field(default_factory=dict)
    numerics_plan: dict = Field(default_factory=dict)
    measurement_plan: MeasurementPlanSpec = Field(default_factory=MeasurementPlanSpec)
    postprocess_plan: dict = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[MissingCapability] = Field(default_factory=list)
    can_compile: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "CasePlan",
    "FunctionObjectSpec",
    "MeasurementPlanSpec",
    "MissingCapability",
]
