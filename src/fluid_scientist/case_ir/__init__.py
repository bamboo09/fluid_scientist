"""Case IR (Intermediate Representation) module.

This package contains the data models for the Case IR layer introduced
in Phase 2 of the Fluid Scientist refactor.

The central artefacts are:

- :class:`~fluid_scientist.case_ir.models.RequestedCaseIR` -- captures
  the full scientific intent of a simulation case as understood from the
  user's natural-language description.
- :class:`~fluid_scientist.case_ir.models.ResolvedCaseIR` -- produced
  after capability resolution; serves as the sole input to the
  deterministic OpenFOAM 13 compiler.

Typical flow::

    from fluid_scientist.case_ir import RequestedCaseIR, ParameterValue

    ir = RequestedCaseIR(
        study_id="study_001",
        case_id="case_001",
        physics=PhysicsIntent(turbulence="LES", turbulence_model="LESWALE"),
        entities=[
            Entity(
                id="cylinder_1",
                kind="cylinder",
                parameters={
                    "diameter": ParameterValue(
                        value=0.01, unit="m", source="USER_EXPLICIT"
                    )
                },
            )
        ],
    )
"""

from fluid_scientist.case_ir.models import (
    Ambiguity,
    Assumption,
    BoundaryIntent,
    CompositionPlan,
    DerivedConstraint,
    Entity,
    ExtensionSpecRef,
    FieldSpec,
    InitialConditionIntent,
    Interface,
    Material,
    MeshIntent,
    NumericalIntent,
    Observable,
    OperatingStage,
    ParameterValue,
    PhysicsIntent,
    Region,
    Relation,
    RequestedCaseIR,
    ResolvedCaseIR,
    ResolvedCapability,
    UnresolvedRequirement,
)

__all__ = [
    "Ambiguity",
    "Assumption",
    "BoundaryIntent",
    "CompositionPlan",
    "DerivedConstraint",
    "Entity",
    "ExtensionSpecRef",
    "FieldSpec",
    "InitialConditionIntent",
    "Interface",
    "Material",
    "MeshIntent",
    "NumericalIntent",
    "Observable",
    "OperatingStage",
    "ParameterValue",
    "PhysicsIntent",
    "Region",
    "Relation",
    "RequestedCaseIR",
    "ResolvedCaseIR",
    "ResolvedCapability",
    "UnresolvedRequirement",
]
