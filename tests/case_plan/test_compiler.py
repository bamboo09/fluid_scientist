"""Tests for the :class:`NativeCaseCompiler`."""

from __future__ import annotations

import copy

import pytest

from fluid_scientist.case_plan.compiler import NativeCaseCompiler
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
            {"observable_id": "drag", "display_name": "Drag", "category": "force"},
            {"observable_id": "pressure", "display_name": "Pressure", "category": "pressure"},
        ],
    )
    defaults.update(overrides)
    return ExperimentDraft(**defaults)


def _make_case_plan(**draft_overrides) -> CasePlan:
    """Generate a compilable CasePlan from a confirmed draft."""
    draft = _make_confirmed_draft(**draft_overrides)
    return CasePlanGenerator().generate(draft)


# ---------------------------------------------------------------------------
# Compilation structure
# ---------------------------------------------------------------------------


class TestCompileStructure:
    def test_compile_returns_dict(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        assert isinstance(result, dict)

    def test_has_top_level_directories(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        assert "system" in result
        assert "constant" in result
        assert "0" in result

    def test_system_has_required_files(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        system = result["system"]
        assert "controlDict" in system
        assert "fvSchemes" in system
        assert "fvSolution" in system
        assert "blockMeshDict" in system

    def test_constant_has_required_files(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        constant = result["constant"]
        assert "transportProperties" in constant
        assert "turbulenceProperties" in constant

    def test_zero_has_velocity_and_pressure(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        zero = result["0"]
        assert "U" in zero
        assert "p" in zero


# ---------------------------------------------------------------------------
# controlDict
# ---------------------------------------------------------------------------


class TestControlDict:
    def test_has_solver(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        control_dict = result["system"]["controlDict"]
        assert control_dict["application"] == "simpleFoam"

    def test_has_end_time_and_delta_t(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        control_dict = result["system"]["controlDict"]
        assert control_dict["endTime"] == 1000
        assert control_dict["deltaT"] == 1.0

    def test_has_write_control(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        control_dict = result["system"]["controlDict"]
        assert control_dict["writeControl"] == "timeStep"
        assert control_dict["writeInterval"] == 100

    def test_has_function_objects(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        control_dict = result["system"]["controlDict"]
        assert "functions" in control_dict
        assert len(control_dict["functions"]) > 0

    def test_forces_function_object_present(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        functions = result["system"]["controlDict"]["functions"]
        fo_ids = list(functions.keys())
        assert any("forces" in fid for fid in fo_ids)

    def test_probes_function_object_present(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        functions = result["system"]["controlDict"]["functions"]
        fo_ids = list(functions.keys())
        assert any("probes" in fid for fid in fo_ids)

    def test_function_object_has_type(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        functions = result["system"]["controlDict"]["functions"]
        for fo_dict in functions.values():
            assert "type" in fo_dict
            assert "writeControl" in fo_dict
            assert "writeInterval" in fo_dict

    def test_function_object_has_patches_for_forces(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        functions = result["system"]["controlDict"]["functions"]
        for _fo_id, fo_dict in functions.items():
            if fo_dict["type"] == "forces":
                assert "patches" in fo_dict
                assert "cylinder" in fo_dict["patches"]


# ---------------------------------------------------------------------------
# blockMeshDict
# ---------------------------------------------------------------------------


class TestBlockMeshDict:
    def test_has_vertices(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        block_mesh = result["system"]["blockMeshDict"]
        assert "vertices" in block_mesh
        assert len(block_mesh["vertices"]) == 8

    def test_vertex_values_from_geometry(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        vertices = result["system"]["blockMeshDict"]["vertices"]
        # v1 should be at (length, 0, 0) = (3.0, 0, 0)
        assert vertices[1] == [3.0, 0.0, 0.0]
        # v2 should be at (length, height, 0) = (3.0, 1.0, 0)
        assert vertices[2] == [3.0, 1.0, 0.0]

    def test_has_blocks(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        blocks = result["system"]["blockMeshDict"]["blocks"]
        assert len(blocks) == 1
        assert blocks[0]["hex"] == [0, 1, 2, 3, 4, 5, 6, 7]

    def test_block_cells_from_mesh_plan(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        blocks = result["system"]["blockMeshDict"]["blocks"]
        assert blocks[0]["cells"] == [100, 50, 10]

    def test_boundary_has_patches(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        boundary = result["system"]["blockMeshDict"]["boundary"]
        assert "inlet" in boundary
        assert "outlet" in boundary
        assert "wall" in boundary

    def test_wall_patch_type(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        boundary = result["system"]["blockMeshDict"]["boundary"]
        assert boundary["wall"]["type"] == "wall"


# ---------------------------------------------------------------------------
# constant/transportProperties and turbulenceProperties
# ---------------------------------------------------------------------------


class TestConstantFiles:
    def test_transport_properties_has_nu(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        transport = result["constant"]["transportProperties"]
        assert transport["nu"] == 1e-5
        assert transport["rho"] == 1.0
        assert transport["transportModel"] == "Newtonian"

    def test_turbulence_properties_ras(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        turbulence = result["constant"]["turbulenceProperties"]
        assert turbulence["simulationType"] == "RAS"
        assert "RAS" in turbulence

    def test_turbulence_properties_laminar(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={
                "turbulent": False,
                "temporal": "steady",
                "nu": 1e-5,
                "rho": 1.0,
            },
        )
        plan = CasePlanGenerator().generate(draft)
        result = NativeCaseCompiler().compile(plan)
        turbulence = result["constant"]["turbulenceProperties"]
        assert turbulence["simulationType"] == "laminar"


# ---------------------------------------------------------------------------
# 0/U and 0/p
# ---------------------------------------------------------------------------


class TestBoundaryFields:
    def test_velocity_has_internal_field(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        u_field = result["0"]["U"]
        assert "internalField" in u_field
        assert "boundaryField" in u_field

    def test_pressure_has_internal_field(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        p_field = result["0"]["p"]
        assert "internalField" in p_field
        assert "boundaryField" in p_field

    def test_inlet_velocity_fixed_value(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        inlet_bc = result["0"]["U"]["boundaryField"]["inlet"]
        assert inlet_bc["type"] == "fixedValue"
        assert inlet_bc["value"]["uniform"] == [1.0, 0.0, 0.0]

    def test_outlet_velocity_zero_gradient(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        outlet_bc = result["0"]["U"]["boundaryField"]["outlet"]
        assert outlet_bc["type"] == "zeroGradient"

    def test_wall_velocity_no_slip(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        wall_bc = result["0"]["U"]["boundaryField"]["wall"]
        assert wall_bc["type"] == "noSlip"

    def test_outlet_pressure_fixed_value(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        outlet_bc = result["0"]["p"]["boundaryField"]["outlet"]
        assert outlet_bc["type"] == "fixedValue"
        assert outlet_bc["value"]["uniform"] == 0.0

    def test_inlet_pressure_zero_gradient(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        inlet_bc = result["0"]["p"]["boundaryField"]["inlet"]
        assert inlet_bc["type"] == "zeroGradient"


# ---------------------------------------------------------------------------
# fvSchemes and fvSolution
# ---------------------------------------------------------------------------


class TestFvFiles:
    def test_fv_schemes_has_sections(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        fv_schemes = result["system"]["fvSchemes"]
        assert "ddtSchemes" in fv_schemes
        assert "gradSchemes" in fv_schemes
        assert "divSchemes" in fv_schemes
        assert "laplacianSchemes" in fv_schemes

    def test_fv_schemes_steady(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        ddt = result["system"]["fvSchemes"]["ddtSchemes"]
        assert ddt["default"] == "steadyState"

    def test_fv_schemes_transient(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={"turbulent": True, "temporal": "transient", "nu": 1e-5},
        )
        plan = CasePlanGenerator().generate(draft)
        result = NativeCaseCompiler().compile(plan)
        ddt = result["system"]["fvSchemes"]["ddtSchemes"]
        assert ddt["default"] == "Euler"

    def test_fv_solution_has_solvers(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        fv_solution = result["system"]["fvSolution"]
        assert "solvers" in fv_solution
        assert "p" in fv_solution["solvers"]
        assert "U" in fv_solution["solvers"]

    def test_fv_solution_steady_has_simple(self) -> None:
        plan = _make_case_plan()
        result = NativeCaseCompiler().compile(plan)
        fv_solution = result["system"]["fvSolution"]
        assert "SIMPLE" in fv_solution

    def test_fv_solution_transient_has_pimple(self) -> None:
        draft = _make_confirmed_draft(
            physics_models={"turbulent": True, "temporal": "transient", "nu": 1e-5},
        )
        plan = CasePlanGenerator().generate(draft)
        result = NativeCaseCompiler().compile(plan)
        fv_solution = result["system"]["fvSolution"]
        assert "PIMPLE" in fv_solution


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_cannot_compile_raises(self) -> None:
        plan = _make_case_plan()
        plan.can_compile = False
        plan.blocking_reasons = ["missing capability"]
        with pytest.raises(ValueError, match="cannot be compiled"):
            NativeCaseCompiler().compile(plan)

    def test_cannot_compile_no_reasons(self) -> None:
        plan = _make_case_plan()
        plan.can_compile = False
        plan.blocking_reasons = []
        with pytest.raises(ValueError, match="cannot be compiled"):
            NativeCaseCompiler().compile(plan)

    def test_missing_delta_t_raises(self) -> None:
        plan = _make_case_plan()
        plan.numerics_plan.pop("deltaT")
        with pytest.raises(ValueError, match="deltaT"):
            NativeCaseCompiler().compile(plan)

    def test_missing_end_time_raises(self) -> None:
        plan = _make_case_plan()
        plan.numerics_plan.pop("endTime")
        with pytest.raises(ValueError, match="endTime"):
            NativeCaseCompiler().compile(plan)

    def test_missing_nu_raises(self) -> None:
        plan = _make_case_plan()
        plan.physical_model_plan.pop("nu")
        with pytest.raises(ValueError, match="nu"):
            NativeCaseCompiler().compile(plan)

    def test_missing_length_raises(self) -> None:
        plan = _make_case_plan()
        plan.geometry_plan.pop("length")
        with pytest.raises(ValueError, match="length"):
            NativeCaseCompiler().compile(plan)

    def test_missing_height_raises(self) -> None:
        plan = _make_case_plan()
        plan.geometry_plan.pop("height")
        with pytest.raises(ValueError, match="height"):
            NativeCaseCompiler().compile(plan)

    def test_kinematic_viscosity_alias_works(self) -> None:
        plan = _make_case_plan()
        plan.physical_model_plan.pop("nu")
        plan.physical_model_plan["kinematic_viscosity"] = 2e-5
        result = NativeCaseCompiler().compile(plan)
        assert result["constant"]["transportProperties"]["nu"] == 2e-5


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_generate_then_compile(self) -> None:
        draft = _make_confirmed_draft()
        plan = CasePlanGenerator().generate(draft)
        assert plan.can_compile is True
        result = NativeCaseCompiler().compile(plan)
        assert "system" in result
        assert "constant" in result
        assert "0" in result

    def test_compile_uses_values_from_plan(self) -> None:
        draft = _make_confirmed_draft(
            numerics={
                "endTime": 5000,
                "deltaT": 0.5,
                "writeControl": "timeStep",
                "writeInterval": 200,
            },
        )
        plan = CasePlanGenerator().generate(draft)
        result = NativeCaseCompiler().compile(plan)
        control_dict = result["system"]["controlDict"]
        assert control_dict["endTime"] == 5000
        assert control_dict["deltaT"] == 0.5
        assert control_dict["writeInterval"] == 200

    def test_compile_with_no_function_objects(self) -> None:
        draft = _make_confirmed_draft(requested_outputs=[])
        plan = CasePlanGenerator().generate(draft)
        result = NativeCaseCompiler().compile(plan)
        control_dict = result["system"]["controlDict"]
        # No functionObjects when no requested outputs.
        assert "functions" not in control_dict or (
            control_dict["functions"] == {}
        )

    def test_compile_does_not_mutate_plan(self) -> None:
        plan = _make_case_plan()
        plan_copy = copy.deepcopy(plan)
        NativeCaseCompiler().compile(plan)
        assert plan.numerics_plan == plan_copy.numerics_plan
        assert plan.geometry_plan == plan_copy.geometry_plan
        assert plan.boundary_condition_plan == plan_copy.boundary_condition_plan
