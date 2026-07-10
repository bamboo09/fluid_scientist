"""Case plan package.

Generates a :class:`CasePlan` from a confirmed
:class:`~fluid_scientist.draft.models.ExperimentDraft` and compiles it
into an in-memory OpenFOAM case structure via the
:class:`NativeCaseCompiler`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fluid_scientist.case_plan.models import (
    CasePlan,
    FunctionObjectSpec,
    MeasurementPlanSpec,
    MissingCapability,
)

# Lazy imports to avoid circular dependency chains:
# CasePlanGenerator imports from study_decomposition.capability_checker,
# which imports from capabilities.models, which triggers code_extension
# loading, which imports back into case_plan.
if TYPE_CHECKING:
    from fluid_scientist.case_plan.compiler import NativeCaseCompiler
    from fluid_scientist.case_plan.generator import CasePlanGenerator


def __getattr__(name: str):
    if name == "CasePlanGenerator":
        from fluid_scientist.case_plan.generator import CasePlanGenerator as _CPG
        return _CPG
    if name == "NativeCaseCompiler":
        from fluid_scientist.case_plan.compiler import NativeCaseCompiler as _NCC
        return _NCC
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CasePlan",
    "CasePlanGenerator",
    "FunctionObjectSpec",
    "MeasurementPlanSpec",
    "MissingCapability",
    "NativeCaseCompiler",
]
