"""Tests for the case plan data models."""

from __future__ import annotations

from fluid_scientist.case_plan.models import (
    CasePlan,
    FunctionObjectSpec,
    MeasurementPlanSpec,
    MissingCapability,
)

# ---------------------------------------------------------------------------
# MissingCapability
# ---------------------------------------------------------------------------


class TestMissingCapability:
    def test_create_minimal(self) -> None:
        mc = MissingCapability(
            capability_id="buoyancy_model_writer",
            capability_type="physical_model_writer",
            reason="buoyancy model not available",
        )
        assert mc.capability_id == "buoyancy_model_writer"
        assert mc.capability_type == "physical_model_writer"
        assert mc.reason == "buoyancy model not available"
        assert mc.severity == "blocking"
        assert mc.extension_spec_id is None

    def test_create_warning_severity(self) -> None:
        mc = MissingCapability(
            capability_id="inclined_geo",
            capability_type="geometry_generator",
            reason="inclined geometry needs special generator",
            severity="warning",
        )
        assert mc.severity == "warning"

    def test_create_with_extension_spec(self) -> None:
        mc = MissingCapability(
            capability_id="custom_bc",
            capability_type="boundary_condition_writer",
            reason="custom BC needed",
            extension_spec_id="ext_001",
        )
        assert mc.extension_spec_id == "ext_001"


# ---------------------------------------------------------------------------
# FunctionObjectSpec
# ---------------------------------------------------------------------------


class TestFunctionObjectSpec:
    def test_create_minimal(self) -> None:
        fo = FunctionObjectSpec(
            function_object_id="forces_drag",
            function_object_type="forces",
        )
        assert fo.function_object_id == "forces_drag"
        assert fo.function_object_type == "forces"
        assert fo.fields == []
        assert fo.patches == []
        assert fo.output_directory == ""
        assert fo.configuration == {}

    def test_create_full(self) -> None:
        fo = FunctionObjectSpec(
            function_object_id="forceCoeffs_lift",
            function_object_type="forceCoeffs",
            fields=["U", "p"],
            patches=["cylinder"],
            output_directory="postProcessing",
            configuration={"rho": "rhoInf", "rhoInf": 1.0},
        )
        assert fo.fields == ["U", "p"]
        assert fo.patches == ["cylinder"]
        assert fo.output_directory == "postProcessing"
        assert fo.configuration["rho"] == "rhoInf"


# ---------------------------------------------------------------------------
# MeasurementPlanSpec
# ---------------------------------------------------------------------------


class TestMeasurementPlanSpec:
    def test_defaults(self) -> None:
        mp = MeasurementPlanSpec()
        assert mp.function_objects == []
        assert mp.sample_points == []
        assert mp.write_interval == 100
        assert mp.output_directory == "postProcessing"

    def test_with_function_objects(self) -> None:
        fo = FunctionObjectSpec(
            function_object_id="probes_p",
            function_object_type="probes",
            fields=["p"],
        )
        mp = MeasurementPlanSpec(
            function_objects=[fo],
            write_interval=50,
            output_directory="postProc",
        )
        assert len(mp.function_objects) == 1
        assert mp.function_objects[0].function_object_type == "probes"
        assert mp.write_interval == 50
        assert mp.output_directory == "postProc"


# ---------------------------------------------------------------------------
# CasePlan
# ---------------------------------------------------------------------------


class TestCasePlan:
    def _make_plan(self, **overrides) -> CasePlan:
        defaults = dict(
            case_plan_id="plan_001",
            draft_id="draft_001",
            draft_version=1,
            case_type="backward_facing_step",
            solver="simpleFoam",
        )
        defaults.update(overrides)
        return CasePlan(**defaults)

    def test_create_with_defaults(self) -> None:
        plan = self._make_plan()
        assert plan.case_plan_id == "plan_001"
        assert plan.draft_id == "draft_001"
        assert plan.draft_version == 1
        assert plan.case_type == "backward_facing_step"
        assert plan.solver == "simpleFoam"
        assert plan.dimensions == "3D"
        assert plan.geometry_plan == {}
        assert plan.mesh_plan == {}
        assert plan.boundary_condition_plan == {}
        assert plan.initial_condition_plan == {}
        assert plan.physical_model_plan == {}
        assert plan.numerics_plan == {}
        assert plan.measurement_plan.function_objects == []
        assert plan.postprocess_plan == {}
        assert plan.required_capabilities == []
        assert plan.missing_capabilities == []
        assert plan.can_compile is False
        assert plan.blocking_reasons == []
        assert plan.created_at is not None

    def test_dimensions_2d(self) -> None:
        plan = self._make_plan(dimensions="2D")
        assert plan.dimensions == "2D"

    def test_with_measurement_plan(self) -> None:
        fo = FunctionObjectSpec(
            function_object_id="forces_drag",
            function_object_type="forces",
            patches=["cylinder"],
        )
        mp = MeasurementPlanSpec(function_objects=[fo])
        plan = self._make_plan(measurement_plan=mp)
        assert len(plan.measurement_plan.function_objects) == 1

    def test_with_missing_capabilities(self) -> None:
        mc = MissingCapability(
            capability_id="custom_bc",
            capability_type="boundary_condition_writer",
            reason="missing BC",
        )
        plan = self._make_plan(
            missing_capabilities=[mc],
            can_compile=False,
            blocking_reasons=["missing BC"],
        )
        assert len(plan.missing_capabilities) == 1
        assert plan.can_compile is False
        assert plan.blocking_reasons == ["missing BC"]

    def test_with_plans(self) -> None:
        plan = self._make_plan(
            geometry_plan={"type": "backward_facing_step", "length": 3.0},
            mesh_plan={"cells_x": 100, "cells_y": 50},
            physical_model_plan={"turbulent": True, "nu": 1e-5},
            numerics_plan={"endTime": 1000, "deltaT": 1.0},
            can_compile=True,
        )
        assert plan.geometry_plan["length"] == 3.0
        assert plan.mesh_plan["cells_x"] == 100
        assert plan.physical_model_plan["nu"] == 1e-5
        assert plan.numerics_plan["endTime"] == 1000
        assert plan.can_compile is True
