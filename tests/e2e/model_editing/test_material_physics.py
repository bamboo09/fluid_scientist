"""E2E tests: material and physics modifications via the generic PatchEngine.

These tests prove that the generic PatchEngine handles material, physics,
geometry, and numerics modifications without any field-specific if/else logic.
All operations go through the same validate -> apply -> diff pipeline.

10 scenarios covered:
  1. Air -> water material change (with dependency graph + Re recompute)
  2. Change density
  3. Change kinematic viscosity
  4. Fix Re by changing velocity
  5. Derive nu from U, D, Re
  6. Declare unknown capability (temperature-dependent viscosity)
  7. 2D -> 3D
  8. Laminar -> RANS
  9. RANS -> LES
 10. Steady -> transient
"""
from __future__ import annotations

import pytest

from tests.e2e.model_editing.conftest import make_study_spec, make_patch, _sourced
from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.dependencies import DependencyGraph, DerivedValueComputer
from fluid_scientist.study_spec import (
    NumericsDefinition,
    PhysicsDefinition,
    Quantity,
    TimeControl,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_air_spec():
    """Build a spec with air as the fluid material.

    Air at room temperature: nu = 1.5e-5 m^2/s.
    Re = U * D / nu = 1.0 * 0.1 / 1.5e-5 ~ 6667.
    """
    spec = make_study_spec()
    return spec.model_copy(update={
        "physics": PhysicsDefinition(
            material=_sourced("air", status="user_confirmed"),
            density=_sourced(1.225, unit="kg/m^3", status="user_confirmed"),
            kinematic_viscosity=_sourced(1.5e-5, unit="m^2/s", status="derived"),
            reynolds_number=_sourced(6667.0, status="derived"),
            velocity=_sourced(1.0, unit="m/s", status="user_explicit"),
            characteristic_length=_sourced(0.1, unit="m", status="user_explicit"),
        ),
    })


def _water_sourced_dict():
    """Return a SourcedValue dict for the 'water' material."""
    return _sourced("water", status="user_confirmed").model_dump()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_air_to_water():
    """Replace material from air to water; verify dependency graph and Re."""
    spec = _make_air_spec()
    assert spec.physics.material.value == "air"

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/physics/material",
            value=_water_sourced_dict(),
            source_quote="把材料改为水",
            confidence=0.95,
        ),
        PatchOperation(
            op="replace",
            path="/physics/kinematic_viscosity",
            value=1.0e-6,
            source_quote="水的运动粘度为1.0e-6 m^2/s",
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.physics.material.value == "water"
    assert result.new_spec.physics.kinematic_viscosity.value == 1.0e-6

    # Unrelated field unchanged.
    assert result.new_spec.physics.velocity.value == 1.0

    # Dependency graph: Re depends on kinematic_viscosity.
    graph = DependencyGraph()
    dependents = graph.get_dependents("/physics/kinematic_viscosity")
    assert "/physics/reynolds_number" in dependents, (
        f"Re should depend on kinematic_viscosity; got: {dependents}"
    )

    # DerivedValueComputer: compute new Re with water's nu.
    computer = DerivedValueComputer()
    re_value, re_formula = computer.compute(
        "/physics/reynolds_number", result.new_spec.model_dump()
    )
    assert re_value is not None, "Re should be computable"
    assert re_value == pytest.approx(100000.0, rel=0.01), (
        f"Re for water should be ~100000, got {re_value}"
    )
    assert re_formula is not None


def test_change_density():
    """Replace /physics/density to 1050.0."""
    spec = make_study_spec()
    original_velocity = spec.physics.velocity.value

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/physics/density",
            value=1050.0,
            source_quote="密度改为1050",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.physics.density.value == 1050.0

    # Unrelated field unchanged.
    assert result.new_spec.physics.velocity.value == original_velocity


def test_change_kinematic_viscosity():
    """Replace /physics/kinematic_viscosity to 2.0e-6."""
    spec = make_study_spec()
    original_density = spec.physics.density.value

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/physics/kinematic_viscosity",
            value=2.0e-6,
            source_quote="运动粘度改为2.0e-6",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.physics.kinematic_viscosity.value == 2.0e-6

    # Unrelated field unchanged.
    assert result.new_spec.physics.density.value == original_density


def test_fix_Re_change_U():
    """Replace /physics/velocity to 0.2; verify Re should be recomputed."""
    spec = make_study_spec()
    assert spec.physics.velocity.value == 0.1
    original_nu = spec.physics.kinematic_viscosity.value

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/physics/velocity",
            value=0.2,
            source_quote="速度改为0.2 m/s",
            confidence=0.95,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.physics.velocity.value == 0.2

    # Unrelated field unchanged.
    assert result.new_spec.physics.kinematic_viscosity.value == original_nu

    # Re should be recomputed: Re = U*D/nu = 0.2*0.001/1.0e-6 = 200.
    computer = DerivedValueComputer()
    re_value, _ = computer.compute(
        "/physics/reynolds_number", result.new_spec.model_dump()
    )
    assert re_value is not None
    assert re_value == pytest.approx(200.0, rel=0.01)

    # Impact analyzer should flag Re for recomputation.
    assert result.impact is not None
    assert "/physics/reynolds_number" in result.impact.derived_recompute_needed


def test_derive_nu_from_UDRe():
    """Set Re=200, U=0.1, D=0.001; compute nu=U*D/Re=5e-7."""
    spec = make_study_spec().model_copy(update={
        "physics": PhysicsDefinition(
            material=_sourced("water", status="user_confirmed"),
            density=_sourced(998.2, unit="kg/m^3", status="derived"),
            kinematic_viscosity=_sourced(5.0e-7, unit="m^2/s", status="derived"),
            reynolds_number=_sourced(200.0, status="derived"),
            velocity=_sourced(0.1, unit="m/s", status="user_explicit"),
            characteristic_length=_sourced(0.001, unit="m", status="user_explicit"),
        ),
    })

    # Compute nu from Re, U, D using DerivedValueComputer.
    computer = DerivedValueComputer()
    nu = computer.compute_viscosity_from_re(200.0, 0.1, 0.001)
    assert nu == pytest.approx(5.0e-7, rel=0.01), (
        f"nu should be 5e-7, got {nu}"
    )

    # Verify the formula: nu = U * D / Re.
    expected_nu = 0.1 * 0.001 / 200.0
    assert nu == pytest.approx(expected_nu)

    # Also verify via the spec: Re = U*D/nu should give 200.
    re_value, _ = computer.compute("/physics/reynolds_number", spec.model_dump())
    assert re_value is not None
    assert re_value == pytest.approx(200.0, rel=0.01)


def test_temperature_dependent_property():
    """Declare unknown capability for temperature-dependent viscosity."""
    spec = make_study_spec()
    original_material = spec.physics.material.value
    original_nu = spec.physics.kinematic_viscosity.value

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="declare_unknown_capability",
            path="/physics/kinematic_viscosity",
            value={
                "capability": "temperature_dependent_viscosity",
                "description": "Viscosity varies with temperature",
            },
            source_quote="粘度随温度变化",
            confidence=0.8,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Unrelated fields unchanged.
    assert result.new_spec.physics.material.value == original_material
    assert result.new_spec.physics.kinematic_viscosity.value == original_nu


def test_2d_to_3d():
    """Replace /geometry/domain/dimensions to '3d'."""
    spec = make_study_spec()
    assert spec.geometry.domain.dimensions == "2d"
    original_length = spec.geometry.domain.length.value

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/geometry/domain/dimensions",
            value="3d",
            source_quote="改为3D模拟",
            confidence=0.95,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.geometry.domain.dimensions == "3d"

    # Unrelated field unchanged.
    assert result.new_spec.geometry.domain.length.value == original_length


def test_laminar_to_rans():
    """Replace /numerics/turbulence_model to 'RANS_kEpsilon'."""
    spec = make_study_spec()
    assert spec.numerics.turbulence_model == "laminar"
    original_solver = spec.numerics.solver

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/numerics/turbulence_model",
            value="RANS_kEpsilon",
            source_quote="使用RANS k-epsilon湍流模型",
            confidence=0.95,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.numerics.turbulence_model == "RANS_kEpsilon"

    # Unrelated field unchanged.
    assert result.new_spec.numerics.solver == original_solver


def test_rans_to_les():
    """Replace /numerics/turbulence_model to 'LES' (spec starts RANS)."""
    base = make_study_spec()
    spec = base.model_copy(update={
        "numerics": base.numerics.model_copy(update={"turbulence_model": "RANS_kEpsilon"}),
    })
    assert spec.numerics.turbulence_model == "RANS_kEpsilon"
    original_solver = spec.numerics.solver

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/numerics/turbulence_model",
            value="LES",
            source_quote="切换到LES大涡模拟",
            confidence=0.95,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.numerics.turbulence_model == "LES"

    # Unrelated field unchanged.
    assert result.new_spec.numerics.solver == original_solver


def test_steady_to_transient():
    """Replace /numerics/time/mode to 'transient' (spec starts steady)."""
    base = make_study_spec()
    spec = base.model_copy(update={
        "numerics": base.numerics.model_copy(update={
            "time": base.numerics.time.model_copy(update={"mode": "steady"}),
        }),
    })
    assert spec.numerics.time.mode == "steady"
    original_delta_t = spec.numerics.time.delta_t.value

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/numerics/time/mode",
            value="transient",
            source_quote="改为瞬态计算",
            confidence=0.95,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.numerics.time.mode == "transient"

    # Unrelated field unchanged.
    assert result.new_spec.numerics.time.delta_t.value == original_delta_t
