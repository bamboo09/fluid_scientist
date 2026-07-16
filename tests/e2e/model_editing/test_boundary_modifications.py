"""E2E tests: boundary condition modifications via the generic PatchEngine.

These tests prove that the generic PatchEngine handles boundary condition
modifications using index-based JSON Pointer paths (e.g.
``/boundaries/conditions/0/bc_type``) without any field-specific if/else logic.

Default spec boundary layout:
  - conditions[0]: inlet   (velocityInlet,  role=inlet)
  - conditions[1]: outlet  (pressureOutlet, role=outlet)
  - conditions[2]: cylinder (noSlipWall,    role=wall)

10 scenarios covered:
  1. Set inlet velocity
  2. Set pressure outlet
  3. Set slip wall
  4. Set no-slip wall
  5. Set cyclic
  6. Set symmetry
  7. Set convective outlet (inletOutlet)
  8. Set moving wall
  9. Add pressure gradient condition (append_unique)
 10. Rename patch keeping role
"""
from __future__ import annotations

from tests.e2e.model_editing.conftest import make_study_spec, make_patch
from fluid_scientist.spec_editing import PatchEngine, PatchOperation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec_with_outlet_bc(bc_type: str):
    """Build a spec where the outlet (conditions[1]) has *bc_type*."""
    base = make_study_spec()
    conditions = list(base.boundaries.conditions)
    conditions[1] = conditions[1].model_copy(update={"bc_type": bc_type})
    return base.model_copy(update={
        "boundaries": base.boundaries.model_copy(update={"conditions": conditions}),
    })


def _spec_with_wall_bc(bc_type: str):
    """Build a spec where the wall (conditions[2]) has *bc_type*."""
    base = make_study_spec()
    conditions = list(base.boundaries.conditions)
    conditions[2] = conditions[2].model_copy(update={"bc_type": bc_type})
    return base.model_copy(update={
        "boundaries": base.boundaries.model_copy(update={"conditions": conditions}),
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_set_inlet_velocity():
    """Replace /boundaries/conditions/0/parameters with {velocity: 0.2}."""
    spec = make_study_spec()
    original_bc_type = spec.boundaries.conditions[0].bc_type

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/0/parameters",
            value={"velocity": 0.2},
            source_quote="入口速度改为0.2",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    params = result.new_spec.boundaries.conditions[0].parameters
    assert params == {"velocity": 0.2}

    # Unrelated field unchanged.
    assert result.new_spec.boundaries.conditions[0].bc_type == original_bc_type


def test_set_pressure_outlet():
    """Replace /boundaries/conditions/1/bc_type to 'pressureOutlet'."""
    spec = _spec_with_outlet_bc("totalPressure")
    original_role = spec.boundaries.conditions[1].role

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/1/bc_type",
            value="pressureOutlet",
            source_quote="出口设为压力出口",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.boundaries.conditions[1].bc_type == "pressureOutlet"

    # Unrelated field unchanged.
    assert result.new_spec.boundaries.conditions[1].role == original_role


def test_set_slip_wall():
    """Replace /boundaries/conditions/2/bc_type to 'slipWall'."""
    spec = make_study_spec()
    original_patch_name = spec.boundaries.conditions[2].patch_name

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/2/bc_type",
            value="slipWall",
            source_quote="壁面设为滑移壁面",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.boundaries.conditions[2].bc_type == "slipWall"

    # Unrelated field unchanged.
    assert result.new_spec.boundaries.conditions[2].patch_name == original_patch_name


def test_set_no_slip_wall():
    """Replace /boundaries/conditions/2/bc_type to 'noSlipWall'."""
    spec = _spec_with_wall_bc("slipWall")
    original_patch_name = spec.boundaries.conditions[2].patch_name

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/2/bc_type",
            value="noSlipWall",
            source_quote="壁面设为无滑移壁面",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.boundaries.conditions[2].bc_type == "noSlipWall"

    # Unrelated field unchanged.
    assert result.new_spec.boundaries.conditions[2].patch_name == original_patch_name


def test_set_cyclic():
    """Replace /boundaries/conditions/2/role to 'cyclic' and bc_type to 'cyclic'."""
    spec = make_study_spec()
    original_patch_name = spec.boundaries.conditions[2].patch_name

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/2/role",
            value="cyclic",
            source_quote="设为周期边界",
            confidence=0.95,
        ),
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/2/bc_type",
            value="cyclic",
            source_quote="设为周期边界",
            confidence=0.95,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.boundaries.conditions[2].role == "cyclic"
    assert result.new_spec.boundaries.conditions[2].bc_type == "cyclic"

    # Unrelated field unchanged.
    assert result.new_spec.boundaries.conditions[2].patch_name == original_patch_name


def test_set_symmetry():
    """Replace /boundaries/conditions/2/role to 'symmetry' and bc_type to 'symmetryPlane'."""
    spec = make_study_spec()
    original_patch_name = spec.boundaries.conditions[2].patch_name

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/2/role",
            value="symmetry",
            source_quote="设为对称边界",
            confidence=0.95,
        ),
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/2/bc_type",
            value="symmetryPlane",
            source_quote="设为对称边界",
            confidence=0.95,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.boundaries.conditions[2].role == "symmetry"
    assert result.new_spec.boundaries.conditions[2].bc_type == "symmetryPlane"

    # Unrelated field unchanged.
    assert result.new_spec.boundaries.conditions[2].patch_name == original_patch_name


def test_set_convective_outlet():
    """Replace /boundaries/conditions/1/bc_type to 'inletOutlet' with parameters."""
    spec = make_study_spec()
    original_role = spec.boundaries.conditions[1].role

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/1/bc_type",
            value="inletOutlet",
            source_quote="出口设为对流出流",
            confidence=0.95,
        ),
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/1/parameters",
            value={"inletValue": 0, "value": "uniform"},
            source_quote="出口设为对流出流",
            confidence=0.95,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    cond = result.new_spec.boundaries.conditions[1]
    assert cond.bc_type == "inletOutlet"
    assert cond.parameters == {"inletValue": 0, "value": "uniform"}

    # Unrelated field unchanged.
    assert cond.role == original_role


def test_set_moving_wall():
    """Replace /boundaries/conditions/2/bc_type to 'movingWallVelocity'."""
    spec = make_study_spec()
    original_patch_name = spec.boundaries.conditions[2].patch_name

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/2/bc_type",
            value="movingWallVelocity",
            source_quote="壁面设为运动壁面",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.boundaries.conditions[2].bc_type == "movingWallVelocity"

    # Unrelated field unchanged.
    assert result.new_spec.boundaries.conditions[2].patch_name == original_patch_name


def test_add_pressure_gradient():
    """Append_unique to /boundaries/conditions/- with new pressure gradient condition."""
    spec = make_study_spec()
    original_count = len(spec.boundaries.conditions)
    original_inlet = spec.boundaries.conditions[0].bc_type

    new_condition = {
        "patch_name": "pressure_gradient",
        "role": "custom",
        "bc_type": "pressureGradientExplicitSource",
        "parameters": {"gradient": 0.1},
        "source_status": "user_explicit",
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/boundaries/conditions/-",
                value=new_condition,
                source_quote="添加压力梯度源项",
                confidence=0.9,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied: new condition appended.
    conditions = result.new_spec.boundaries.conditions
    assert len(conditions) == original_count + 1
    assert conditions[-1].patch_name == "pressure_gradient"
    assert conditions[-1].bc_type == "pressureGradientExplicitSource"

    # Unrelated field unchanged.
    assert conditions[0].bc_type == original_inlet


def test_rename_patch_keep_role():
    """Replace /boundaries/conditions/0/patch_name to 'inlet_left'; role still 'inlet'."""
    spec = make_study_spec()
    original_role = spec.boundaries.conditions[0].role
    original_bc_type = spec.boundaries.conditions[0].bc_type

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/boundaries/conditions/0/patch_name",
            value="inlet_left",
            source_quote="重命名入口patch为inlet_left",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    assert result.new_spec.boundaries.conditions[0].patch_name == "inlet_left"

    # Role unchanged.
    assert result.new_spec.boundaries.conditions[0].role == original_role
    # bc_type unchanged.
    assert result.new_spec.boundaries.conditions[0].bc_type == original_bc_type
