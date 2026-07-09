"""Tests for the :class:`CasePlanGenerator`."""

from __future__ import annotations

import pytest

from fluid_scientist.case_plan.generator import CasePlanGenerator
from fluid_scientist.case_plan.models import CasePlan
from fluid_scientist.draft.models import DraftStatus, ExperimentDraft

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_confirmed_draft(**overrides) -> ExperimentDraft:
    """Create a confirmed backward-facing-step draft for testing."""
    defaults: dict = dict(
        draft_id="draft_001",
        session_id="session_001",
        study_id="study_001",
        version=1,
        status=DraftStatus.CONFIRMED,
        objective="Study backward facing step flow",
        study_type="backward_facing_step",
        geometry={
            "type": "backward_facing_step",
            "length": 3.0,
            "height": 1.0,
            "step_height": 0.5,
            "width": 1.0,
        },
        physics_models={
            "turbulent": True,
            "temporal": "steady",
            "dimension": "3D",
            "nu": 1e-5,
            "rho": 1.0,
        },
        initial_conditions={
            "velocity": {"field": "velocity", "value": [0.0, 0.0, 0.0]},
            "pressure": {"field": "pressure", "value": 0.0},
        },
        boundary_conditions={
            "inlet": {"type": "inlet_velocity", "velocity": 1.0, "unit": "m/s"},
            "outlet": {"type": "outlet_pressure", "pressure": 0.0, "unit": "Pa"},
            "wall": {"type": "no_slip"},
        },
        mesh={
            "cells_x": 100,
            "cells_y": 50,
            "cells_z": 10,
        },
        numerics={
            "endTime": 1000,
            "deltaT": 1.0,
            "writeControl": "timeStep",
            "writeInterval": 100,
        },
        solver={},
        requested_outputs=[
            {
                "observable_id": "drag",
                "display_name": "Drag",
                "category": "force",
                "required_fields": ["Cd"],
            },
            {
                "observable_id": "pressure",
                "display_name": "Pressure",
                "category": "pressure",
                "required_fields": ["p"],
            },
        ],
    )
    defaults.update(overrides)
    return ExperimentDraft(**defaults)


# ---------------------------------------------------------------------------
# Basic generation
# ---------------------------------------------------------------------------


class TestCasePlanGeneration:
    def test_returns_case_plan(self) -> None:
        plan = CasePlanGenerator().generate(_make_confirmed_draft())
        assert isinstance(plan, CasePlan)

    def test_case_plan_id_is_uuid(self) -> None:
        plan = CasePlanGenerator().generate(_make_confirmed_draft())
        assert len(plan.case_plan_id) > 0
        plan2 = CasePlanGenerator().generate(_make_confirmed_draft())
        assert plan.case_plan_id != plan2.case_plan_id

    def test_draft_id_and_version_copied(self) -> None:
        draft = _make_confirmed_draft(version=3)
        plan = CasePlanGenerator().generate(draft)
        assert plan.draft_id == "draft_001"
        assert plan.draft_version == 3

    def test_case_type_from_geometry(self) -> None:
        plan = CasePlanGenerator().generate(_make_confirmed_draft())
        assert plan.case_type == "backward_facing_step"

    def test_case_type_cylinder(self) -> None:
        draft = _make_confirmed_draft(
            geometry={"type": "cylinder", "D": 0.1},
            study_type="cylinder_wake",
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.case_type == "cylinder_cross_flow"

    def test_case_type_fallback_to_study_type(self) -> None:
        draft = _make_confirmed_draft(
            geometry={"type": "custom_geometry"},
            study_type="custom_study",
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.case_type == "custom_geometry"

    def test_dimensions_from_physics(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={"turbulent": True, "temporal": "steady", "dimension": "2D"},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.dimensions == "2D"

    def test_dimensions_defaults_to_3d(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert plan.dimensions == "3D"

    def test_not_confirmed_raises(self) -> None:
        draft = _make_confirmed_draft(status=DraftStatus.DRAFT)
        with pytest.raises(ValueError, match="confirmed"):
            CasePlanGenerator().generate(draft)

    def test_ready_status_raises(self) -> None:
        draft = _make_confirmed_draft(status=DraftStatus.READY)
        with pytest.raises(ValueError, match="confirmed"):
            CasePlanGenerator().generate(draft)


# ---------------------------------------------------------------------------
# Solver auto-selection
# ---------------------------------------------------------------------------


class TestSolverAutoSelection:
    def test_turbulent_transient_pimpleFoam(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={"turbulent": True, "temporal": "transient"},
            solver={},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.solver == "pimpleFoam"

    def test_turbulent_steady_simpleFoam(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={"turbulent": True, "temporal": "steady"},
            solver={},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.solver == "simpleFoam"

    def test_laminar_transient_pisoFoam(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={"turbulent": False, "temporal": "transient"},
            solver={},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.solver == "pisoFoam"

    def test_laminar_steady_simpleFoam(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={"turbulent": False, "temporal": "steady"},
            solver={},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.solver == "simpleFoam"

    def test_buoyancy_buoyantPimpleFoam(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={
                "buoyancy": True,
                "turbulent": True,
                "temporal": "transient",
            },
            solver={},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.solver == "buoyantPimpleFoam"

    def test_default_pimpleFoam(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={},
            solver={},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.solver == "pimpleFoam"

    def test_explicit_solver_overrides_auto(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={"turbulent": True, "temporal": "transient"},
            solver={"name": "simpleFoam"},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.solver == "simpleFoam"

    def test_explicit_solver_via_solver_key(self) -> None:
        draft = _make_confirmed_draft(
            solver={"solver": "icoFoam"},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.solver == "icoFoam"


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------


class TestPlanGeneration:
    def test_geometry_plan_copied(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert plan.geometry_plan["type"] == "backward_facing_step"
        assert plan.geometry_plan["length"] == 3.0

    def test_mesh_plan_from_draft(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert plan.mesh_plan["cells_x"] == 100
        assert plan.mesh_plan["cells_y"] == 50

    def test_mesh_plan_defaults_3d(self) -> None:
        draft = _make_confirmed_draft(mesh={})
        plan = CasePlanGenerator().generate(draft)
        assert "cells_x" in plan.mesh_plan
        assert "cells_z" in plan.mesh_plan
        assert plan.mesh_plan["cells_z"] > 1

    def test_mesh_plan_defaults_2d(self) -> None:
        draft = _make_confirmed_draft(
            mesh={},
            physics_models={"turbulent": True, "temporal": "steady", "dimension": "2D"},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.mesh_plan["cells_z"] == 1

    def test_boundary_condition_plan_copied(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert "inlet" in plan.boundary_condition_plan
        assert "outlet" in plan.boundary_condition_plan
        assert "wall" in plan.boundary_condition_plan
        assert plan.boundary_condition_plan["inlet"]["velocity"] == 1.0

    def test_initial_condition_plan_copied(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert "velocity" in plan.initial_condition_plan
        assert "pressure" in plan.initial_condition_plan

    def test_physical_model_plan_copied(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert plan.physical_model_plan["turbulent"] is True
        assert plan.physical_model_plan["nu"] == 1e-5

    def test_numerics_plan_from_draft(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert plan.numerics_plan["endTime"] == 1000
        assert plan.numerics_plan["deltaT"] == 1.0
        assert plan.numerics_plan["steady"] is True

    def test_numerics_plan_defaults_steady(self) -> None:
        draft = _make_confirmed_draft(numerics={})
        plan = CasePlanGenerator().generate(draft)
        assert plan.numerics_plan["endTime"] == 1000
        assert plan.numerics_plan["deltaT"] == 1.0
        assert plan.numerics_plan["steady"] is True

    def test_numerics_plan_defaults_transient(self) -> None:
        draft = _make_confirmed_draft(
            numerics={},
            physics_models={"turbulent": True, "temporal": "transient"},
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.numerics_plan["deltaT"] == 0.01
        assert plan.numerics_plan["steady"] is False


# ---------------------------------------------------------------------------
# Measurement plan generation
# ---------------------------------------------------------------------------


class TestMeasurementPlan:
    def test_drag_maps_to_forces(self) -> None:
        draft = _make_confirmed_draft(
            requested_outputs=[
                {"observable_id": "drag", "category": "force"},
            ],
        )
        plan = CasePlanGenerator().generate(draft)
        assert len(plan.measurement_plan.function_objects) == 1
        fo = plan.measurement_plan.function_objects[0]
        assert fo.function_object_type == "forces"
        assert fo.patches == ["cylinder"]

    def test_cd_maps_to_forces(self) -> None:
        draft = _make_confirmed_draft(
            requested_outputs=[{"observable_id": "cd", "category": "force"}],
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.measurement_plan.function_objects[0].function_object_type == "forces"

    def test_lift_maps_to_forceCoeffs(self) -> None:
        draft = _make_confirmed_draft(
            requested_outputs=[{"observable_id": "lift", "category": "force"}],
        )
        plan = CasePlanGenerator().generate(draft)
        fo = plan.measurement_plan.function_objects[0]
        assert fo.function_object_type == "forceCoeffs"

    def test_pressure_maps_to_probes(self) -> None:
        draft = _make_confirmed_draft(
            requested_outputs=[{"observable_id": "pressure", "category": "pressure"}],
        )
        plan = CasePlanGenerator().generate(draft)
        fo = plan.measurement_plan.function_objects[0]
        assert fo.function_object_type == "probes"
        assert "p" in fo.fields

    def test_velocity_profile_maps_to_probes(self) -> None:
        draft = _make_confirmed_draft(
            requested_outputs=[
                {"observable_id": "velocity_profile", "category": "custom"},
            ],
        )
        plan = CasePlanGenerator().generate(draft)
        fo = plan.measurement_plan.function_objects[0]
        assert fo.function_object_type == "probes"
        assert "U" in fo.fields

    def test_strouhal_maps_to_probes(self) -> None:
        draft = _make_confirmed_draft(
            requested_outputs=[{"observable_id": "strouhal", "category": "spectral"}],
        )
        plan = CasePlanGenerator().generate(draft)
        fo = plan.measurement_plan.function_objects[0]
        assert fo.function_object_type == "probes"
        assert "U" in fo.fields

    def test_multiple_outputs(self) -> None:
        draft = _make_confirmed_draft(
            requested_outputs=[
                {"observable_id": "drag", "category": "force"},
                {"observable_id": "lift", "category": "force"},
                {"observable_id": "pressure", "category": "pressure"},
            ],
        )
        plan = CasePlanGenerator().generate(draft)
        assert len(plan.measurement_plan.function_objects) == 3

    def test_unknown_output_skipped(self) -> None:
        draft = _make_confirmed_draft(
            requested_outputs=[
                {"observable_id": "drag", "category": "force"},
                {"observable_id": "unknown_metric", "category": "custom"},
            ],
        )
        plan = CasePlanGenerator().generate(draft)
        assert len(plan.measurement_plan.function_objects) == 1

    def test_write_interval_from_numerics(self) -> None:
        draft = _make_confirmed_draft(
            numerics={
                "endTime": 1000,
                "deltaT": 1.0,
                "writeInterval": 50,
            },
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.measurement_plan.write_interval == 50


# ---------------------------------------------------------------------------
# Capability check
# ---------------------------------------------------------------------------


class TestCapabilityCheck:
    def test_native_case_can_compile(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert plan.missing_capabilities == []
        assert plan.can_compile is True
        assert plan.blocking_reasons == []

    def test_density_stratification_blocks(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={
                "turbulent": True,
                "temporal": "transient",
                "density_stratification": True,
                "nu": 1e-5,
            },
        )
        plan = CasePlanGenerator().generate(draft)
        assert len(plan.missing_capabilities) > 0
        assert plan.can_compile is False
        assert len(plan.blocking_reasons) > 0
        assert any(
            mc.severity == "blocking" for mc in plan.missing_capabilities
        )

    def test_thermal_blocks(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={
                "turbulent": True,
                "temporal": "steady",
                "thermal": True,
                "nu": 1e-5,
            },
        )
        plan = CasePlanGenerator().generate(draft)
        assert plan.can_compile is False
        assert len(plan.blocking_reasons) > 0

    def test_required_capabilities_populated(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert "solver:simpleFoam" in plan.required_capabilities
        assert "geometry_generator:backward_facing_step" in plan.required_capabilities
        assert "boundary_condition_writer:inlet_velocity" in plan.required_capabilities
        assert "function_object_writer:forces" in plan.required_capabilities

    def test_missing_capability_has_correct_fields(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={
                "turbulent": True,
                "temporal": "transient",
                "moving_body": True,
                "nu": 1e-5,
            },
        )
        plan = CasePlanGenerator().generate(draft)
        mc = plan.missing_capabilities[0]
        assert mc.capability_id == "dynamic_mesh_writer"
        assert mc.capability_type == "mesh_generator"
        assert mc.severity == "blocking"
        assert mc.reason != ""
