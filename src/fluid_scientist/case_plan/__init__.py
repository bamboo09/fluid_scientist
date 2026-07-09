"""Case plan package.

Generates a :class:`CasePlan` from a confirmed
:class:`~fluid_scientist.draft.models.ExperimentDraft` and compiles it
into an in-memory OpenFOAM case structure via the
:class:`NativeCaseCompiler`.
"""

from fluid_scientist.case_plan.compiler import NativeCaseCompiler
from fluid_scientist.case_plan.generator import CasePlanGenerator
from fluid_scientist.case_plan.models import (
    CasePlan,
    FunctionObjectSpec,
    MeasurementPlanSpec,
    MissingCapability,
)

__all__ = [
    "CasePlan",
    "CasePlanGenerator",
    "FunctionObjectSpec",
    "MeasurementPlanSpec",
    "MissingCapability",
    "NativeCaseCompiler",
]
