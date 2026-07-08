"""E2E tests for dynamic metric planning (Commit 3).

Verifies the enhanced metric planning system:
  1. MetricPlanner generates core metrics from Chinese research objectives
  2. Unknown metrics are captured and flow through MissingCapability
  3. Metric definitions include required_data and quality_checks
  4. MetricPlan drives MeasurementPlan (correct functionObjects)
  5. API endpoint returns metric plan
  6. Additional metrics (pressure_rms, velocity_rms) are available
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.capabilities.models import (
    CapabilityType,
    CodeExtensionSpec,
    MissingCapability,
)
from fluid_scientist.capabilities.resolver import (
    CapabilityResolver,
    detect_missing_capabilities_from_metrics,
)
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ExperimentStatus,
    ResearchSpec,
)
from fluid_scientist.measurement.models import (
    FunctionObjectType,
    MeasurementPlan,
)
from fluid_scientist.measurement.planner import (
    MetricPlanner,
)
from fluid_scientist.ports import StoredExperimentSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def planner() -> MetricPlanner:
    return MetricPlanner()


@pytest.fixture
def repository():
    """Create an in-memory repository."""
    return SQLWorkflowRepository("sqlite:///:memory:")


@pytest.fixture
def client(repository):
    """Create a test client backed by *repository*."""
    app = create_app(repository=repository, execution_targets=[])
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def project_id(client):
    """Create a test project and return its id."""
    response = client.post(
        "/api/projects", json={"question": "dynamic metric planning e2e test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_spec_with_metrics(
    repository,
    project_id: str,
    *,
    metrics: list[dict] | None = None,
) -> str:
    """Create an experiment spec directly in the repository with metrics.

    Returns the experiment_id.
    """
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        research=ResearchSpec(
            title="Dynamic Metric Planning Test",
            objective="Test dynamic metric planning",
        ),
        status=ExperimentStatus.DRAFT,
        metrics=metrics or [],
    )

    stored = StoredExperimentSpec(
        experiment_id=experiment_id,
        project_id=project_id,
        schema_version=spec.schema_version,
        experiment_version=spec.experiment_version,
        status=spec.status.value,
        task_type=spec.task_type.value,
        interaction_mode=spec.interaction_mode.value,
        spec_json=spec.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    repository.save_experiment_spec(stored)
    return experiment_id


# ---------------------------------------------------------------------------
# Test 1: Cylinder flow objective generates core metrics
# ---------------------------------------------------------------------------


class TestCylinderFlowMetricInference:
    """MetricPlanner generates core metrics for cylinder flow objective."""

    def test_vortex_shedding_objective_infers_strouhal(self, planner: MetricPlanner):
        """研究圆柱涡脱落 should infer strouhal_number as a core metric."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱涡脱落",
            experiment_type="cylinder_flow",
        )

        # strouhal_number should be inferred from "涡脱落" keyword
        assert "strouhal_number" in plan.core_metrics
        # drag_coefficient is critical in cylinder registry -> core
        assert "drag_coefficient" in plan.core_metrics

    def test_vortex_shedding_metric_has_definition(self, planner: MetricPlanner):
        """strouhal_number should have a metric definition with required_data."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱涡脱落",
            experiment_type="cylinder_flow",
        )

        assert "strouhal_number" in plan.metric_definitions
        defs = plan.metric_definitions["strouhal_number"]
        assert "formula" in defs
        assert defs["unit"] == "dimensionless"


# ---------------------------------------------------------------------------
# Test 2: Pipe flow objective generates core metrics
# ---------------------------------------------------------------------------


class TestPipeFlowMetricInference:
    """MetricPlanner generates core metrics for pipe flow objective."""

    def test_pressure_drop_objective_infers_pressure_drop(
        self, planner: MetricPlanner
    ):
        """研究管内压降 should infer pressure_drop as a core metric."""
        plan = planner.propose_metrics(
            research_objective="研究管内压降",
            experiment_type="laminar_pipe",
        )

        # pressure_drop should be inferred from "压降" keyword
        assert "pressure_drop" in plan.core_metrics

    def test_pressure_drop_has_definition(self, planner: MetricPlanner):
        """pressure_drop should have a metric definition."""
        plan = planner.propose_metrics(
            research_objective="研究管内压降",
            experiment_type="laminar_pipe",
        )

        assert "pressure_drop" in plan.metric_definitions
        defs = plan.metric_definitions["pressure_drop"]
        assert defs["unit"] == "Pa"


# ---------------------------------------------------------------------------
# Test 3: Cavity flow objective generates core metrics
# ---------------------------------------------------------------------------


class TestCavityFlowMetricInference:
    """MetricPlanner generates core metrics for cavity flow objective."""

    def test_velocity_field_objective_infers_velocity_profile(
        self, planner: MetricPlanner
    ):
        """研究方腔流速度场 should infer velocity_profile as a core metric."""
        plan = planner.propose_metrics(
            research_objective="研究方腔流速度场",
            experiment_type="lid_driven_cavity",
        )

        # velocity_profile should be inferred from "速度场" keyword
        assert "velocity_profile" in plan.core_metrics

    def test_velocity_profile_has_definition(self, planner: MetricPlanner):
        """velocity_profile should have a metric definition."""
        plan = planner.propose_metrics(
            research_objective="研究方腔流速度场",
            experiment_type="lid_driven_cavity",
        )

        assert "velocity_profile" in plan.metric_definitions


# ---------------------------------------------------------------------------
# Test 4: Unknown metric goes to unknown_metrics
# ---------------------------------------------------------------------------


class TestUnknownMetricHandling:
    """Unknown metrics like 旋涡破碎指数 go to unknown_metrics."""

    def test_unknown_metric_goes_to_unknown(self, planner: MetricPlanner):
        """旋涡破碎指数 (vortex_breakdown_index) should go to unknown_metrics."""
        plan = planner.propose_metrics(
            research_objective="研究旋涡破碎指数",
            user_metrics=["vortex_breakdown_index"],
            experiment_type="cylinder_flow",
        )

        assert "vortex_breakdown_index" in plan.unknown_metrics
        assert len(plan.unknown_metric_details) == 1
        detail = plan.unknown_metric_details[0]
        assert detail.metric_name == "vortex_breakdown_index"
        assert detail.status == "unknown"

    def test_unknown_metric_not_in_core(self, planner: MetricPlanner):
        """Unknown metric should not appear in core_metrics."""
        plan = planner.propose_metrics(
            research_objective="研究旋涡破碎指数",
            user_metrics=["vortex_breakdown_index"],
            experiment_type="cylinder_flow",
        )

        assert "vortex_breakdown_index" not in plan.core_metrics
        assert "vortex_breakdown_index" not in plan.extension_metrics


# ---------------------------------------------------------------------------
# Test 5: MissingCapability is created for unknown metrics
# ---------------------------------------------------------------------------


class TestMissingCapabilityForUnknownMetrics:
    """Unknown metrics flow through MissingCapability detection."""

    def test_detect_missing_capabilities_creates_blocking_capability(
        self, planner: MetricPlanner
    ):
        """detect_missing_capabilities_from_metrics creates blocking
        MissingCapability for unknown metrics."""
        plan = planner.propose_metrics(
            research_objective="研究旋涡破碎指数",
            user_metrics=["vortex_breakdown_index"],
            experiment_type="cylinder_flow",
        )

        caps = detect_missing_capabilities_from_metrics(plan)
        assert len(caps) == 1
        cap = caps[0]
        assert isinstance(cap, MissingCapability)
        assert cap.capability_type == CapabilityType.METRIC_OPERATOR
        assert cap.severity == "blocking"
        assert cap.is_blocking() is True
        assert "vortex_breakdown_index" in cap.capability_id

    def test_capability_resolver_creates_code_extension(
        self, planner: MetricPlanner
    ):
        """CapabilityResolver creates CodeExtensionSpec for unknown metrics."""
        plan = planner.propose_metrics(
            research_objective="研究旋涡破碎指数",
            user_metrics=["vortex_breakdown_index"],
            experiment_type="cylinder_flow",
        )

        resolver = CapabilityResolver()
        caps = resolver.resolve(metric_plan=plan)
        assert len(caps) == 1
        assert caps[0].is_blocking()

        extensions = resolver.create_extensions(caps)
        assert len(extensions) == 1
        ext = extensions[0]
        assert isinstance(ext, CodeExtensionSpec)
        assert ext.state == "draft"
        assert ext.extension_type == "metric_operator"

    def test_full_flow_unknown_to_extension(self, planner: MetricPlanner):
        """Full flow: unknown metric -> MissingCapability -> CodeExtensionSpec
        with AWAITING_CODE_APPROVAL status."""
        plan = planner.propose_metrics(
            research_objective="研究旋涡破碎指数",
            user_metrics=["vortex_breakdown_index"],
            experiment_type="cylinder_flow",
        )

        resolver = CapabilityResolver()
        caps = resolver.resolve(metric_plan=plan)
        assert len(caps) == 1

        # Should raise when blocking
        with pytest.raises(Exception) as exc_info:
            resolver.resolve_or_raise(metric_plan=plan)
        assert len(exc_info.value.capabilities) == 1

        # Create extensions
        extensions = resolver.create_extensions(caps)
        assert len(extensions) == 1
        assert extensions[0].state == "draft"


# ---------------------------------------------------------------------------
# Test 6: Metric definitions include required_data
# ---------------------------------------------------------------------------


class TestMetricDefinitionsRequiredData:
    """Metric definitions include required_data field."""

    def test_registry_metric_has_required_data(self, planner: MetricPlanner):
        """Metrics from registry include required_data in their definitions."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱涡脱落",
            experiment_type="cylinder_flow",
        )

        # drag_coefficient from registry should have required_data
        assert "drag_coefficient" in plan.metric_definitions
        drag_def = plan.metric_definitions["drag_coefficient"]
        assert "required_data" in drag_def
        assert isinstance(drag_def["required_data"], list)
        assert len(drag_def["required_data"]) > 0
        # Should mention forceCoeffs time series
        assert any("forceCoeffs" in rd for rd in drag_def["required_data"])

    def test_strouhal_has_required_data(self, planner: MetricPlanner):
        """strouhal_number definition includes required_data."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱涡脱落",
            experiment_type="cylinder_flow",
        )

        assert "strouhal_number" in plan.metric_definitions
        st_def = plan.metric_definitions["strouhal_number"]
        assert "required_data" in st_def
        assert len(st_def["required_data"]) > 0

    def test_pressure_drop_has_required_data(self, planner: MetricPlanner):
        """pressure_drop definition includes required_data."""
        plan = planner.propose_metrics(
            research_objective="研究管内压降",
            experiment_type="laminar_pipe",
        )

        assert "pressure_drop" in plan.metric_definitions
        pd_def = plan.metric_definitions["pressure_drop"]
        assert "required_data" in pd_def
        assert len(pd_def["required_data"]) > 0
        # Should mention surfaceFieldValue
        assert any("surfaceFieldValue" in rd for rd in pd_def["required_data"])


# ---------------------------------------------------------------------------
# Test 7: Metric definitions include quality_checks
# ---------------------------------------------------------------------------


class TestMetricDefinitionsQualityChecks:
    """Metric definitions include quality_checks field."""

    def test_registry_metric_has_quality_checks(self, planner: MetricPlanner):
        """Metrics from registry include quality_checks in their definitions."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱涡脱落",
            experiment_type="cylinder_flow",
        )

        drag_def = plan.metric_definitions["drag_coefficient"]
        assert "quality_checks" in drag_def
        assert isinstance(drag_def["quality_checks"], list)
        assert len(drag_def["quality_checks"]) > 0

    def test_strouhal_has_quality_checks(self, planner: MetricPlanner):
        """strouhal_number definition includes quality_checks with
        sampling_frequency and peak_prominence."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱涡脱落",
            experiment_type="cylinder_flow",
        )

        st_def = plan.metric_definitions["strouhal_number"]
        assert "quality_checks" in st_def
        assert len(st_def["quality_checks"]) > 0
        # Strouhal should have sampling_frequency and peak_prominence
        qc_set = set(st_def["quality_checks"])
        assert "sampling_frequency" in qc_set
        assert "peak_prominence" in qc_set

    def test_pressure_drop_has_quality_checks(self, planner: MetricPlanner):
        """pressure_drop definition includes quality_checks."""
        plan = planner.propose_metrics(
            research_objective="研究管内压降",
            experiment_type="laminar_pipe",
        )

        pd_def = plan.metric_definitions["pressure_drop"]
        assert "quality_checks" in pd_def
        assert len(pd_def["quality_checks"]) > 0


# ---------------------------------------------------------------------------
# Test 8: MetricPlan drives MeasurementPlan
# ---------------------------------------------------------------------------


class TestMetricPlanDrivesMeasurementPlan:
    """MetricPlan generates correct MeasurementPlan with functionObjects."""

    def test_drag_coefficient_generates_force_coeffs(self, planner: MetricPlanner):
        """drag_coefficient in plan -> forceCoeffs functionObject in
        MeasurementPlan."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱绕流阻力",
            user_metrics=["drag_coefficient"],
            experiment_type="cylinder_flow",
        )

        mp = plan.measurement_plan
        assert isinstance(mp, MeasurementPlan)
        # Should have a FORCE_COEFFS function object
        fo_types = [fo.type for fo in mp.function_objects]
        assert FunctionObjectType.FORCE_COEFFS in fo_types

        # Should have a metric binding for drag_coefficient
        binding_metric_ids = [b.metric_id for b in mp.metric_bindings]
        assert "drag_coefficient" in binding_metric_ids

    def test_pressure_drop_generates_surface_field_value(
        self, planner: MetricPlanner
    ):
        """pressure_drop in plan -> surfaceFieldValue functionObjects in
        MeasurementPlan."""
        plan = planner.propose_metrics(
            research_objective="研究管内压降",
            experiment_type="laminar_pipe",
        )

        mp = plan.measurement_plan
        assert isinstance(mp, MeasurementPlan)
        # Should have SURFACE_FIELD_VALUE function objects
        fo_types = [fo.type for fo in mp.function_objects]
        assert FunctionObjectType.SURFACE_FIELD_VALUE in fo_types

        # Should have a metric binding for pressure_drop
        binding_metric_ids = [b.metric_id for b in mp.metric_bindings]
        assert "pressure_drop" in binding_metric_ids

    def test_measurement_plan_has_required_fields(self, planner: MetricPlanner):
        """MeasurementPlan has required field outputs (U, p)."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱绕流",
            experiment_type="cylinder_flow",
        )

        mp = plan.measurement_plan
        field_names = [f.field_name for f in mp.required_fields]
        assert "U" in field_names
        assert "p" in field_names


# ---------------------------------------------------------------------------
# Test 9: API endpoint returns metric plan
# ---------------------------------------------------------------------------


class TestMetricPlanAPIEndpoint:
    """GET /api/projects/{project_id}/experiment-specs/{experiment_id}/metric-plan
    returns the metric plan."""

    def test_metric_plan_endpoint_returns_metrics(
        self, client, repository, project_id
    ):
        """API endpoint returns metrics from the experiment spec."""
        test_metrics = [
            {
                "kind": "measurement_plan",
                "core_metrics": ["pressure_drop", "drag_coefficient"],
                "credibility_metrics": ["residual_tolerance"],
                "unknown_metrics": [],
                "metric_definitions": {
                    "pressure_drop": {
                        "formula": "p_inlet - p_outlet",
                        "unit": "Pa",
                        "required_data": ["inlet/outlet surfaceFieldValue"],
                        "quality_checks": ["mass_balance"],
                    }
                },
            }
        ]
        experiment_id = _create_spec_with_metrics(
            repository, project_id, metrics=test_metrics
        )

        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/metric-plan"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["experiment_id"] == experiment_id
        assert data["metric_count"] == 1
        assert len(data["metrics"]) == 1
        assert data["metrics"][0]["kind"] == "measurement_plan"
        assert "pressure_drop" in data["metrics"][0]["core_metrics"]

    def test_metric_plan_endpoint_404_for_missing_spec(
        self, client, project_id
    ):
        """API endpoint returns 404 for non-existent experiment spec."""
        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/nonexistent/metric-plan"
        )
        assert response.status_code == 404

    def test_metric_plan_endpoint_empty_metrics(
        self, client, repository, project_id
    ):
        """API endpoint returns empty metrics for spec with no metrics."""
        experiment_id = _create_spec_with_metrics(
            repository, project_id, metrics=[]
        )

        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/metric-plan"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["metric_count"] == 0
        assert data["metrics"] == []


# ---------------------------------------------------------------------------
# Test 10: Additional metrics are available
# ---------------------------------------------------------------------------


class TestAdditionalMetricsAvailable:
    """Additional metrics (pressure_rms, velocity_rms, etc.) are available
    in the MetricPlanner."""

    def test_pressure_rms_available(self, planner: MetricPlanner):
        """pressure_rms is a known metric in _METRIC_DEFINITIONS."""
        assert "pressure_rms" in MetricPlanner._METRIC_DEFINITIONS

        plan = planner.propose_metrics(
            research_objective="研究压力脉动",
            user_metrics=["pressure_rms"],
            experiment_type="cylinder_flow",
        )

        assert "pressure_rms" in plan.core_metrics
        assert "pressure_rms" not in plan.unknown_metrics
        assert "pressure_rms" in plan.metric_definitions
        defs = plan.metric_definitions["pressure_rms"]
        assert "required_data" in defs
        assert "quality_checks" in defs
        assert defs["unit"] == "Pa"

    def test_velocity_rms_available(self, planner: MetricPlanner):
        """velocity_rms is a known metric in _METRIC_DEFINITIONS."""
        assert "velocity_rms" in MetricPlanner._METRIC_DEFINITIONS

        plan = planner.propose_metrics(
            research_objective="研究速度脉动",
            user_metrics=["velocity_rms"],
            experiment_type="cylinder_flow",
        )

        assert "velocity_rms" in plan.core_metrics
        assert "velocity_rms" not in plan.unknown_metrics
        assert "velocity_rms" in plan.metric_definitions
        defs = plan.metric_definitions["velocity_rms"]
        assert "required_data" in defs
        assert "quality_checks" in defs
        assert defs["unit"] == "m/s"

    def test_all_additional_metrics_available(self, planner: MetricPlanner):
        """All additional metrics are available in _METRIC_DEFINITIONS."""
        additional = [
            "pressure_rms",
            "velocity_rms",
            "outlet_velocity_distortion",
            "secondary_flow_intensity",
            "swirl_number",
            "frequency_spectrum_peak",
            "statistical_stability",
        ]
        for metric_id in additional:
            assert metric_id in MetricPlanner._METRIC_DEFINITIONS, (
                f"{metric_id} not in _METRIC_DEFINITIONS"
            )
            defs = MetricPlanner._METRIC_DEFINITIONS[metric_id]
            assert "required_data" in defs
            assert "quality_checks" in defs
            assert len(defs["required_data"]) > 0
            assert len(defs["quality_checks"]) > 0

    def test_additional_metrics_recognized_as_standard(self):
        """Additional metrics are recognized by _is_standard_metric."""
        additional = [
            "pressure_rms",
            "velocity_rms",
            "outlet_velocity_distortion",
            "secondary_flow_intensity",
            "swirl_number",
            "frequency_spectrum_peak",
            "statistical_stability",
        ]
        for metric_id in additional:
            assert MetricPlanner._is_standard_metric(metric_id), (
                f"{metric_id} should be recognized as standard"
            )

    def test_keyword_inference_for_additional_metrics(
        self, planner: MetricPlanner
    ):
        """Chinese keywords for additional metrics are correctly inferred."""
        # 压力脉动 -> pressure_rms
        plan = planner.propose_metrics(
            research_objective="研究压力脉动特性",
            experiment_type="cylinder_flow",
        )
        assert "pressure_rms" in plan.core_metrics

        # 速度脉动 -> velocity_rms
        plan2 = planner.propose_metrics(
            research_objective="研究速度脉动特性",
            experiment_type="cylinder_flow",
        )
        assert "velocity_rms" in plan2.core_metrics
