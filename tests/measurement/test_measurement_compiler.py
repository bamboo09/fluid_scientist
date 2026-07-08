"""Tests for the MeasurementPlan compiler (Commit 6).

Verifies that ``compile_measurement_plan`` correctly writes functionObjects
into OpenFOAM case file additions, validates that all MetricSpec required
data has corresponding functionObjects, and blocks compilation when core
metrics cannot obtain required data.
"""

from __future__ import annotations

import pytest

from fluid_scientist.measurement.compiler import (
    MeasurementCompilationResult,
    _render_function_object,
    compile_measurement_plan,
)
from fluid_scientist.measurement.models import (
    FieldOutputSpec,
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
    MetricBinding,
    SpatialSamplingSpec,
    SpatialSamplingType,
    TimeSamplingSpec,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_measurement_plan():
    """A valid measurement plan with pressure_drop metric."""
    return MeasurementPlan(
        required_fields=[
            FieldOutputSpec(field_name="U"),
            FieldOutputSpec(field_name="p"),
        ],
        function_objects=[
            FunctionObjectSpec(
                type=FunctionObjectType.SURFACE_FIELD_VALUE,
                name="pressure_inlet",
                field="p",
                operation="areaAverage",
                surface="inlet_section",
            ),
            FunctionObjectSpec(
                type=FunctionObjectType.SURFACE_FIELD_VALUE,
                name="pressure_outlet",
                field="p",
                operation="areaAverage",
                surface="outlet_section",
            ),
        ],
        spatial_sampling=[
            SpatialSamplingSpec(
                id="inlet_section", type=SpatialSamplingType.SURFACE
            ),
            SpatialSamplingSpec(
                id="outlet_section", type=SpatialSamplingType.SURFACE
            ),
        ],
        time_sampling=TimeSamplingSpec(
            start_time=10.0, end_time=100.0, interval=0.01
        ),
        metric_bindings=[
            MetricBinding(
                metric_id="pressure_drop",
                source="outlet_section",
                function_object="pressure_outlet",
            ),
        ],
    )


@pytest.fixture
def force_coeffs_plan():
    """A measurement plan with forceCoeffs referencing a patch."""
    return MeasurementPlan(
        required_fields=[FieldOutputSpec(field_name="U"), FieldOutputSpec(field_name="p")],
        function_objects=[
            FunctionObjectSpec(
                type=FunctionObjectType.FORCE_COEFFS,
                name="forceCoeffs_1",
                target_patch="cylinder",
            ),
        ],
        time_sampling=TimeSamplingSpec(start_time=10.0, end_time=100.0, interval=0.01),
        metric_bindings=[
            MetricBinding(
                metric_id="drag_coefficient",
                source="forceCoeffs_1",
                function_object="forceCoeffs_1",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 1. Successful compilation
# ---------------------------------------------------------------------------


class TestSuccessfulCompilation:
    def test_compile_success_when_all_metrics_have_bindings(
        self, simple_measurement_plan
    ):
        """compile_measurement_plan returns success=True when all metrics have
        bindings and patches exist."""
        result = compile_measurement_plan(
            simple_measurement_plan,
            solver_output_fields=["U", "p"],
            simulation_end_time=100.0,
            core_metric_ids=["pressure_drop"],
        )
        assert result.success is True
        assert len(result.issues) == 0 or all(
            i.severity == "warning" for i in result.issues
        )


# ---------------------------------------------------------------------------
# 2. Core metric with no binding — blocking
# ---------------------------------------------------------------------------


class TestCoreMetricBlocking:
    def test_compile_fails_when_core_metric_has_no_binding(
        self, simple_measurement_plan
    ):
        """compile_measurement_plan returns success=False when a core metric
        has no MetricBinding (blocking)."""
        result = compile_measurement_plan(
            simple_measurement_plan,
            solver_output_fields=["U", "p"],
            simulation_end_time=100.0,
            core_metric_ids=["pressure_drop", "drag_coefficient"],
        )
        # drag_coefficient has no binding -> blocking error
        assert result.success is False
        error_issues = [i for i in result.issues if i.severity == "error"]
        assert len(error_issues) >= 1
        assert any("drag_coefficient" in i.message for i in error_issues)


# ---------------------------------------------------------------------------
# 3. Non-existent functionObject reference
# ---------------------------------------------------------------------------


class TestNonExistentFunctionObject:
    def test_compile_fails_when_binding_references_nonexistent_fo(self):
        """compile_measurement_plan returns success=False when a MetricBinding
        references non-existent functionObject."""
        plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.SURFACE_FIELD_VALUE,
                    name="pressure_inlet",
                    field="p",
                    operation="areaAverage",
                    surface="inlet_section",
                ),
            ],
            time_sampling=TimeSamplingSpec(start_time=0.0, end_time=100.0),
            metric_bindings=[
                MetricBinding(
                    metric_id="pressure_drop",
                    source="outlet_section",
                    function_object="nonexistent_fo",
                ),
            ],
        )
        result = compile_measurement_plan(plan)
        assert result.success is False
        error_issues = [i for i in result.issues if i.severity == "error"]
        assert len(error_issues) >= 1
        assert any("nonexistent_fo" in i.message for i in error_issues)


# ---------------------------------------------------------------------------
# 4. controlDict additions with functionObjects
# ---------------------------------------------------------------------------


class TestControlDictAdditions:
    def test_compile_generates_control_dict_additions(self, simple_measurement_plan):
        """compile_measurement_plan generates controlDict additions with
        functionObjects."""
        result = compile_measurement_plan(
            simple_measurement_plan,
            solver_output_fields=["U", "p"],
            simulation_end_time=100.0,
        )
        assert result.success is True
        assert "functions" in result.control_dict_additions
        functions = result.control_dict_additions["functions"]
        assert "pressure_inlet" in functions
        assert "pressure_outlet" in functions
        assert functions["pressure_inlet"]["type"] == "surfaceFieldValue"


# ---------------------------------------------------------------------------
# 5. Patch validation
# ---------------------------------------------------------------------------


class TestPatchValidation:
    def test_compile_fails_when_patch_not_available(self, force_coeffs_plan):
        """compile_measurement_plan validates that patches referenced by
        functionObjects exist."""
        result = compile_measurement_plan(
            force_coeffs_plan,
            available_patches=["inlet", "outlet"],  # "cylinder" not present
            solver_output_fields=["U", "p"],
            simulation_end_time=100.0,
        )
        assert result.success is False
        error_issues = [i for i in result.issues if i.severity == "error"]
        assert any("cylinder" in i.message for i in error_issues)

    def test_compile_succeeds_when_patch_available(self, force_coeffs_plan):
        """Compilation succeeds when the referenced patch is available."""
        result = compile_measurement_plan(
            force_coeffs_plan,
            available_patches=["cylinder", "inlet", "outlet"],
            solver_output_fields=["U", "p"],
            simulation_end_time=100.0,
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# 6. Field not in solver_output_fields — warning
# ---------------------------------------------------------------------------


class TestFieldValidation:
    def test_compile_warns_when_field_not_in_solver_output(self):
        """compile_measurement_plan warns when field is not in
        solver_output_fields."""
        plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.SURFACE_FIELD_VALUE,
                    name="k_sampler",
                    field="k",
                    operation="areaAverage",
                    surface="section_1",
                ),
            ],
            spatial_sampling=[
                SpatialSamplingSpec(id="section_1", type=SpatialSamplingType.SURFACE),
            ],
            time_sampling=TimeSamplingSpec(start_time=0.0, end_time=100.0),
            metric_bindings=[],
        )
        result = compile_measurement_plan(
            plan,
            solver_output_fields=["U", "p"],  # "k" not present
            simulation_end_time=100.0,
        )
        assert result.success is True
        warning_issues = [i for i in result.issues if i.severity == "warning"]
        assert len(warning_issues) >= 1
        assert any("k" in i.message for i in warning_issues)


# ---------------------------------------------------------------------------
# 7. end_time exceeds simulation end_time — warning
# ---------------------------------------------------------------------------


class TestTimeSamplingValidation:
    def test_compile_warns_when_end_time_exceeds_simulation(self):
        """compile_measurement_plan warns when measurement end_time exceeds
        simulation end_time."""
        plan = MeasurementPlan(
            function_objects=[],
            time_sampling=TimeSamplingSpec(start_time=0.0, end_time=200.0),
            metric_bindings=[],
        )
        result = compile_measurement_plan(
            plan,
            simulation_end_time=100.0,
        )
        assert result.success is True
        warning_issues = [i for i in result.issues if i.severity == "warning"]
        assert len(warning_issues) >= 1
        assert any("exceeds" in i.message for i in warning_issues)


# ---------------------------------------------------------------------------
# 8. start_time >= end_time — error
# ---------------------------------------------------------------------------


class TestInvalidTimeRange:
    def test_compile_error_when_start_time_gte_end_time(self):
        """compile_measurement_plan returns error when start_time >= end_time."""
        plan = MeasurementPlan(
            function_objects=[],
            time_sampling=TimeSamplingSpec(start_time=100.0, end_time=100.0),
            metric_bindings=[],
        )
        result = compile_measurement_plan(plan)
        assert result.success is False
        error_issues = [i for i in result.issues if i.severity == "error"]
        assert len(error_issues) >= 1
        assert any("Invalid time range" in i.message for i in error_issues)


# ---------------------------------------------------------------------------
# 9. _render_function_object: forceCoeffs
# ---------------------------------------------------------------------------


class TestRenderForceCoeffs:
    def test_render_force_coeffs(self):
        """_render_function_object renders forceCoeffs correctly with patches,
        liftDir, dragDir."""
        fo = FunctionObjectSpec(
            type=FunctionObjectType.FORCE_COEFFS,
            name="forceCoeffs_1",
            target_patch="cylinder",
        )
        ts = TimeSamplingSpec(start_time=0.0, end_time=100.0)
        result = _render_function_object(fo, ts)
        assert result["type"] == "forceCoeffs"
        assert '"cylinder"' in result["patches"]
        assert result["liftDir"] == "(0 1 0)"
        assert result["dragDir"] == "(1 0 0)"
        assert result["rhoInf"] == 998.2
        assert result["magUInf"] == 1.0
        assert result["lRef"] == 1.0
        assert result["Aref"] == 1.0
        assert result["writeControl"] == "timeStep"


# ---------------------------------------------------------------------------
# 10. _render_function_object: surfaceFieldValue
# ---------------------------------------------------------------------------


class TestRenderSurfaceFieldValue:
    def test_render_surface_field_value(self):
        """_render_function_object renders surfaceFieldValue correctly with
        operation and field."""
        fo = FunctionObjectSpec(
            type=FunctionObjectType.SURFACE_FIELD_VALUE,
            name="pressure_inlet",
            field="p",
            operation="areaAverage",
            surface="inlet_section",
        )
        ts = TimeSamplingSpec(start_time=0.0, end_time=100.0)
        result = _render_function_object(fo, ts)
        assert result["type"] == "surfaceFieldValue"
        assert result["surface"] == "inlet_section"
        assert result["fields"] == ["p"]
        assert result["operation"] == "areaAverage"
        assert result["writeControl"] == "timeStep"


# ---------------------------------------------------------------------------
# 11. _render_function_object: probes
# ---------------------------------------------------------------------------


class TestRenderProbes:
    def test_render_probes(self):
        """_render_function_object renders probes correctly."""
        fo = FunctionObjectSpec(
            type=FunctionObjectType.PROBES,
            name="velocity_probes",
            field="U",
        )
        ts = TimeSamplingSpec(start_time=0.0, end_time=100.0)
        result = _render_function_object(fo, ts)
        assert result["type"] == "probes"
        assert result["fields"] == ["U"]
        assert result["probeLocations"] == []
        assert result["writeControl"] == "timeStep"


# ---------------------------------------------------------------------------
# 12. _render_function_object: residuals
# ---------------------------------------------------------------------------


class TestRenderResiduals:
    def test_render_residuals(self):
        """_render_function_object renders residuals correctly."""
        fo = FunctionObjectSpec(
            type=FunctionObjectType.RESIDUALS,
            name="residuals_1",
        )
        ts = TimeSamplingSpec(start_time=0.0, end_time=100.0)
        result = _render_function_object(fo, ts)
        assert result["type"] == "residuals"
        assert "U" in result["fields"]
        assert "p" in result["fields"]
        assert result["writeControl"] == "timeStep"


# ---------------------------------------------------------------------------
# 13. MeasurementCompilationResult fields
# ---------------------------------------------------------------------------


class TestMeasurementCompilationResultFields:
    def test_result_has_all_required_fields(self):
        """MeasurementCompilationResult has all required fields (success,
        control_dict_additions, issues, generated_function_objects)."""
        result = MeasurementCompilationResult(success=True)
        assert hasattr(result, "success")
        assert hasattr(result, "control_dict_additions")
        assert hasattr(result, "issues")
        assert hasattr(result, "generated_function_objects")
        assert hasattr(result, "sample_dict")
        assert hasattr(result, "surface_sampling_dict")
        assert result.success is True
        assert result.control_dict_additions == {}
        assert result.issues == []
        assert result.generated_function_objects == []


# ---------------------------------------------------------------------------
# 14. No core metrics -> success with only warnings
# ---------------------------------------------------------------------------


class TestNoCoreMetrics:
    def test_compile_succeeds_with_no_core_metrics(self, simple_measurement_plan):
        """When no core metrics are specified, compilation succeeds with only
        warnings."""
        result = compile_measurement_plan(
            simple_measurement_plan,
            solver_output_fields=["U", "p"],
            simulation_end_time=100.0,
            core_metric_ids=[],  # no core metrics
        )
        assert result.success is True
        # All issues should be warnings (or none at all)
        for issue in result.issues:
            assert issue.severity == "warning"


# ---------------------------------------------------------------------------
# 15. Multiple functionObjects rendered
# ---------------------------------------------------------------------------


class TestMultipleFunctionObjects:
    def test_multiple_function_objects_all_rendered(self, simple_measurement_plan):
        """Multiple functionObjects are all rendered into controlDict
        additions."""
        result = compile_measurement_plan(
            simple_measurement_plan,
            solver_output_fields=["U", "p"],
            simulation_end_time=100.0,
        )
        assert result.success is True
        functions = result.control_dict_additions["functions"]
        assert len(functions) == 2
        assert "pressure_inlet" in functions
        assert "pressure_outlet" in functions
        assert len(result.generated_function_objects) == 2
