"""Shared fixtures and helpers for the model-editing e2e tests.

Every test in this package builds its own :class:`SimulationStudySpec` from
scratch to remain self-contained.  The helpers below reduce boilerplate
while keeping each test readable.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project src/ is on sys.path (same pattern as tests/e2e/conftest.py).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from typing import Any

import pytest

from fluid_scientist.study_spec import (
    BoundaryCondition,
    BoundaryDefinition,
    DomainSpec,
    ExecutionDefinition,
    GeometryDefinition,
    GeometryEntity,
    MeshDefinition,
    NumericsDefinition,
    ObservationDefinition,
    ObservationTarget,
    PhysicsDefinition,
    PlacementSpec,
    ProbeSpec,
    Quantity,
    SimulationStudySpec,
    SpecProvenance,
    SourcedValue,
    StudyDefinition,
    TimeControl,
    ValidationDefinition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sourced(
    value: Any,
    unit: str | None = None,
    status: str = "user_explicit",
    confidence: float = 0.9,
) -> SourcedValue:
    """Build a SourcedValue with sensible test defaults."""
    return SourcedValue(
        value=value,
        unit=unit,
        status=status,  # type: ignore[arg-type]
        source_turn_ids=["turn_0"],
        confidence=confidence,
    )


def make_study_spec(**overrides: Any) -> SimulationStudySpec:
    """Build a fully-populated SimulationStudySpec for testing.

    The default spec represents a 2-D cylinder-flow study with:
    * material = water, Re = 100, velocity = 0.1 m/s, D = 0.001 m
    * end_time = 10 s, delta_t = 0.01 s
    * one cylinder entity and one drag-coefficient observation target

    Any sub-block can be overridden via *overrides*.
    """
    study = StudyDefinition(
        title="Cylinder Flow Re=100",
        objective="Investigate vortex shedding behind a cylinder",
        research_questions=["What is the Strouhal number at Re=100?"],
    )
    physics = PhysicsDefinition(
        material=_sourced("water", status="user_confirmed"),
        density=_sourced(998.2, unit="kg/m^3", status="user_confirmed"),
        kinematic_viscosity=_sourced(1.0e-6, unit="m^2/s", status="derived"),
        reynolds_number=_sourced(100.0, status="derived"),
        velocity=_sourced(0.1, unit="m/s", status="derived"),
        characteristic_length=_sourced(0.001, unit="m", status="derived"),
    )
    geometry = GeometryDefinition(
        domain=DomainSpec(
            length=_sourced(12.0, unit="m"),
            width=_sourced(8.0, unit="m"),
            dimensions="2d",
        ),
        entities={
            "cylinder": GeometryEntity(
                entity_id="cylinder",
                semantic_type="cylinder_2d",
                primitive={"type": "circle", "radius": 0.2, "diameter": 0.4},
                original_user_semantics="cylinder",
                placement=PlacementSpec(
                    x=_sourced(4.0, unit="m"),
                    y=_sourced(4.0, unit="m"),
                ),
            ),
        },
        relations=[],
    )
    boundaries = BoundaryDefinition(
        conditions=[
            BoundaryCondition(
                patch_name="inlet",
                role="inlet",
                bc_type="velocityInlet",
                parameters={"velocity": 0.1},
                source_status="user_explicit",
            ),
            BoundaryCondition(
                patch_name="outlet",
                role="outlet",
                bc_type="pressureOutlet",
                parameters={"pressure": 0.0},
                source_status="derived",
            ),
            BoundaryCondition(
                patch_name="cylinder",
                role="wall",
                bc_type="noSlipWall",
                parameters={},
                source_status="derived",
            ),
        ],
    )
    numerics = NumericsDefinition(
        time=TimeControl(
            mode="transient",
            start_time=Quantity(value=0.0, unit="s"),
            end_time=Quantity(value=10.0, unit="s"),
            delta_t=Quantity(value=0.01, unit="s"),
            adaptive=False,
            max_courant=0.5,
            write_control="runTime",
            write_interval=Quantity(value=0.1, unit="s"),
        ),
        solver="icoFoam",
        discretization={
            "ddtSchemes": {"ddtScheme": "backward"},
        },
        turbulence_model="laminar",
    )
    mesh = MeshDefinition(
        resolution=_sourced(1200, unit="cells", status="derived"),
        mesh_type="blockMesh",
        refinement_regions=[],
    )
    observations = ObservationDefinition(
        targets=[
            ObservationTarget(
                target_id="drag",
                metric="cd",
                parameters={"patches": ["cylinder"]},
                function_object_type="forceCoeffs",
            ),
        ],
        probes=[
            ProbeSpec(
                probe_id="wake_probe_1",
                location={"x": 5.0, "y": 4.0, "z": 0.0},
                field="U",
            ),
        ],
        postprocessing=["streamlines"],
    )
    execution = ExecutionDefinition(
        target_id="workstation",
        parallel=False,
        cores=None,
    )
    validation = ValidationDefinition(checks=["courant_number", "mass_balance"])
    provenance = SpecProvenance(
        created_at="2026-01-01T00:00:00+00:00",
        created_by="test_user",
        parent_version=None,
        creation_turn_id="turn_0",
    )

    defaults = dict(
        spec_id="test_spec_001",
        session_id="session_001",
        version=1,
        parent_version=None,
        study=study,
        physics=physics,
        geometry=geometry,
        boundaries=boundaries,
        initial_conditions=[],
        numerics=numerics,
        mesh=mesh,
        observations=observations,
        execution=execution,
        validation=validation,
        extensions={},
        provenance=provenance,
    )
    defaults.update(overrides)
    return SimulationStudySpec(**defaults)


def make_patch(
    spec: SimulationStudySpec,
    operations: list[Any],
    **kwargs: Any,
) -> Any:
    """Build a SimulationSpecPatch targeting *spec*."""
    from fluid_scientist.spec_editing import SimulationSpecPatch

    defaults: dict[str, Any] = dict(
        patch_id="patch_001",
        session_id=spec.session_id,
        base_spec_id=spec.spec_id,
        base_version=spec.version,
        intent="modify_existing_spec",
        operations=operations,
        clarifications=[],
        impact_requests=[],
        untouched_guarantee=True,
        assistant_message="Applying user requested changes",
    )
    defaults.update(kwargs)
    return SimulationSpecPatch(**defaults)


# Expose helpers at module level for import in test files.
__all__ = [
    "_sourced",
    "make_study_spec",
    "make_patch",
]
