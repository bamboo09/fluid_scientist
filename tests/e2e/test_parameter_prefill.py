"""Tests for parameter pre-fill with recommended values and source metadata.

Commit 3: Experiment drafts come pre-filled with system-recommended values
and proper source metadata (reason, confidence, applicability, risk_level).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.experiment_planning.models import (
    CavityExperimentPlan,
    ConvergenceTargets,
    CylinderExperimentPlan,
    CylinderFlowCase,
    LaminarPipeCase,
    LidDrivenCavityCase,
    PipeExperimentPlan,
)
from fluid_scientist.experiment_spec.migration import apply_recommendations
from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
    ResearchSpec,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _convergence_targets() -> ConvergenceTargets:
    return ConvergenceTargets(residual_tolerance=1e-6, mass_imbalance_percent=0.1)


def _make_cylinder_plan() -> CylinderExperimentPlan:
    case = CylinderFlowCase(
        diameter_m=0.1,
        reynolds_number=100.0,
        end_time_s=10.0,
        density_kg_m3=998.2,
        kinematic_viscosity_m2_s=1.0e-6,
        mean_velocity_m_s=0.001,
        max_courant=0.5,
    )
    return CylinderExperimentPlan(
        experiment_name="Cylinder Flow Re=100",
        objective="Study vortex shedding behind a cylinder",
        rationale="Benchmark validation for laminar flow regime",
        assumptions=("2D flow", "incompressible"),
        limitations=("laminar only",),
        requested_outputs=("drag_coefficient", "strouhal_number"),
        convergence_targets=_convergence_targets(),
        case=case,
        experiment_type="cylinder_flow",
    )


def _make_pipe_plan() -> PipeExperimentPlan:
    case = LaminarPipeCase(
        diameter_m=0.01,
        length_m=1.0,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1.0e-6,
        density_kg_m3=998.2,
    )
    return PipeExperimentPlan(
        experiment_name="Laminar Pipe Re=1000",
        objective="Verify pressure drop in laminar pipe flow",
        rationale="Classic benchmark for laminar pressure-loss validation",
        assumptions=("steady flow", "fully developed"),
        limitations=("laminar only",),
        requested_outputs=("pressure_drop",),
        convergence_targets=_convergence_targets(),
        case=case,
        experiment_type="laminar_pipe",
    )


def _make_cavity_plan() -> CavityExperimentPlan:
    case = LidDrivenCavityCase(
        side_length_m=0.1,
        lid_velocity_m_s=1.0,
        kinematic_viscosity_m2_s=0.01,
        density_kg_m3=1.0,
        end_time_s=10.0,
    )
    return CavityExperimentPlan(
        experiment_name="Lid-Driven Cavity",
        objective="Benchmark reproduction of lid-driven cavity flow",
        rationale="Standard CFD validation case for viscous flow",
        assumptions=("2D flow", "incompressible"),
        limitations=("laminar only",),
        requested_outputs=("velocity_probes",),
        convergence_targets=_convergence_targets(),
        case=case,
        experiment_type="lid_driven_cavity",
    )


def _function_body(js: str, signature: str) -> str:
    """Extract the body of a function from JS source by its signature."""
    start = js.find(signature)
    assert start != -1, f"function not found: {signature}"
    search_from = start + len(signature)
    end = len(js)
    for marker in ("\nfunction ", "\nasync function "):
        pos = js.find(marker, search_from)
        if pos != -1 and pos < end:
            end = pos
    return js[start:end]


# ---------------------------------------------------------------------------
# Test 1: Cylinder plan migration produces parameters with reasons
# ---------------------------------------------------------------------------


class TestCylinderPlanMigrationReasons:
    """Verify cylinder plan migration attaches reasons to parameters."""

    def test_reynolds_number_has_user_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "exp-cyl-001")
        re = spec.get_parameter("reynolds_number")
        assert re is not None
        assert re.source.type == ParameterSource.USER
        assert re.source.reason is not None
        assert len(re.source.reason) > 0
        assert re.source.confidence == "high"
        assert re.status == ParameterStatus.PENDING

    def test_diameter_has_user_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "exp-cyl-002")
        d = spec.get_parameter("diameter")
        assert d is not None
        assert d.source.type == ParameterSource.USER
        assert d.source.reason is not None
        assert len(d.source.reason) > 0
        assert d.source.confidence == "high"
        assert d.status == ParameterStatus.PENDING

    def test_density_has_template_default_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "exp-cyl-003")
        rho = spec.get_parameter("density")
        assert rho is not None
        assert rho.source.type == ParameterSource.TEMPLATE_DEFAULT
        assert rho.source.reason is not None
        assert len(rho.source.reason) > 0
        assert rho.source.confidence == "medium"

    def test_kinematic_viscosity_has_template_default_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "exp-cyl-004")
        nu = spec.get_parameter("kinematic_viscosity")
        assert nu is not None
        assert nu.source.type == ParameterSource.TEMPLATE_DEFAULT
        assert nu.source.reason is not None
        assert len(nu.source.reason) > 0
        assert nu.source.confidence == "medium"


# ---------------------------------------------------------------------------
# Test 2: Pipe plan migration produces parameters with reasons
# ---------------------------------------------------------------------------


class TestPipePlanMigrationReasons:
    """Verify pipe plan migration attaches reasons to parameters."""

    def test_diameter_has_user_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_pipe_plan()
        spec = migrate_plan(plan, "exp-pipe-001")
        d = spec.get_parameter("diameter")
        assert d is not None
        assert d.source.type == ParameterSource.USER
        assert d.source.reason is not None
        assert len(d.source.reason) > 0
        assert d.source.confidence == "high"
        assert d.status == ParameterStatus.PENDING

    def test_length_has_user_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_pipe_plan()
        spec = migrate_plan(plan, "exp-pipe-002")
        length_param = spec.get_parameter("length")
        assert length_param is not None
        assert length_param.source.type == ParameterSource.USER
        assert length_param.source.reason is not None
        assert len(length_param.source.reason) > 0
        assert length_param.source.confidence == "high"
        assert length_param.status == ParameterStatus.PENDING

    def test_reynolds_number_has_user_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_pipe_plan()
        spec = migrate_plan(plan, "exp-pipe-003")
        re = spec.get_parameter("reynolds_number")
        assert re is not None
        assert re.source.type == ParameterSource.USER
        assert re.source.reason is not None
        assert len(re.source.reason) > 0
        assert re.source.confidence == "high"
        assert re.status == ParameterStatus.PENDING


# ---------------------------------------------------------------------------
# Test 3: Cavity plan migration produces parameters with reasons
# ---------------------------------------------------------------------------


class TestCavityPlanMigrationReasons:
    """Verify cavity plan migration attaches reasons to parameters."""

    def test_side_length_has_user_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cavity_plan()
        spec = migrate_plan(plan, "exp-cav-001")
        sl = spec.get_parameter("side_length")
        assert sl is not None
        assert sl.source.type == ParameterSource.USER
        assert sl.source.reason is not None
        assert len(sl.source.reason) > 0
        assert sl.source.confidence == "high"
        assert sl.status == ParameterStatus.PENDING

    def test_lid_velocity_has_user_source_and_reason(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cavity_plan()
        spec = migrate_plan(plan, "exp-cav-002")
        lv = spec.get_parameter("lid_velocity")
        assert lv is not None
        assert lv.source.type == ParameterSource.USER
        assert lv.source.reason is not None
        assert len(lv.source.reason) > 0
        assert lv.source.confidence == "high"
        assert lv.status == ParameterStatus.PENDING


# ---------------------------------------------------------------------------
# Test 4: apply_recommendations fills in missing water properties
# ---------------------------------------------------------------------------


class TestApplyRecommendationsWaterProperties:
    """Verify apply_recommendations fills in missing water properties."""

    def test_density_filled_when_none(self):
        p = ParameterSpec(
            parameter_id="density",
            display_name="Density",
            category="material",
            value=None,
            source=ParameterSourceInfo(type=ParameterSource.UNKNOWN),
        )
        spec = ExperimentSpec(
            experiment_id="test-fill-1",
            research=ResearchSpec(title="t", objective="test objective"),
            parameters=[p],
        )
        result = apply_recommendations(spec)
        dp = result.get_parameter("density")
        assert dp is not None
        assert dp.value == 998.2
        assert dp.source.type == ParameterSource.SYSTEM_RECOMMENDED
        assert dp.source.reason is not None

    def test_kinematic_viscosity_filled_when_none(self):
        p = ParameterSpec(
            parameter_id="kinematic_viscosity",
            display_name="Kinematic Viscosity",
            category="material",
            value=None,
            source=ParameterSourceInfo(type=ParameterSource.UNKNOWN),
        )
        spec = ExperimentSpec(
            experiment_id="test-fill-2",
            research=ResearchSpec(title="t", objective="test objective"),
            parameters=[p],
        )
        result = apply_recommendations(spec)
        nu = result.get_parameter("kinematic_viscosity")
        assert nu is not None
        assert nu.value == 1.0e-6
        assert nu.source.type == ParameterSource.SYSTEM_RECOMMENDED
        assert nu.source.reason is not None

    def test_existing_values_not_overridden(self):
        """apply_recommendations must NOT override user-provided values."""
        p = ParameterSpec(
            parameter_id="density",
            display_name="Density",
            category="material",
            value=1200.0,
            source=ParameterSourceInfo(type=ParameterSource.USER),
        )
        spec = ExperimentSpec(
            experiment_id="test-fill-3",
            research=ResearchSpec(title="t", objective="test objective"),
            parameters=[p],
        )
        result = apply_recommendations(spec)
        dp = result.get_parameter("density")
        assert dp is not None
        assert dp.value == 1200.0
        assert dp.source.type == ParameterSource.USER


# ---------------------------------------------------------------------------
# Test 5: system_recommended parameters have confidence and reason
# ---------------------------------------------------------------------------


class TestSystemRecommendedParameters:
    """Verify system_recommended parameters have proper metadata."""

    def test_domain_width_is_system_recommended_with_high_confidence(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "exp-rec-001")
        dw = spec.get_parameter("domain_width")
        assert dw is not None
        assert dw.source.type == ParameterSource.SYSTEM_RECOMMENDED
        assert dw.source.reason is not None
        assert len(dw.source.reason) > 0
        assert dw.source.confidence == "high"
        assert dw.status == ParameterStatus.PENDING

    def test_domain_height_is_system_recommended_with_high_confidence(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "exp-rec-002")
        dh = spec.get_parameter("domain_height")
        assert dh is not None
        assert dh.source.type == ParameterSource.SYSTEM_RECOMMENDED
        assert dh.source.reason is not None
        assert len(dh.source.reason) > 0
        assert dh.source.confidence == "high"
        assert dh.status == ParameterStatus.PENDING

    def test_system_recommended_has_applicability(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "exp-rec-003")
        dw = spec.get_parameter("domain_width")
        assert dw is not None
        assert dw.source.applicability is not None
        assert len(dw.source.applicability) > 0


# ---------------------------------------------------------------------------
# Test 6: user-specified parameters have source_type "user" and status "pending"
# ---------------------------------------------------------------------------


class TestUserSpecifiedParameters:
    """Verify user-specified parameters have correct source type and status."""

    def test_cylinder_user_params_have_user_source_and_pending_status(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "exp-user-001")
        for pid in ("reynolds_number", "diameter"):
            p = spec.get_parameter(pid)
            assert p is not None, f"parameter {pid} not found"
            assert p.source.type == ParameterSource.USER
            assert p.status == ParameterStatus.PENDING

    def test_pipe_user_params_have_user_source_and_pending_status(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_pipe_plan()
        spec = migrate_plan(plan, "exp-user-002")
        for pid in ("diameter", "length", "reynolds_number"):
            p = spec.get_parameter(pid)
            assert p is not None, f"parameter {pid} not found"
            assert p.source.type == ParameterSource.USER
            assert p.status == ParameterStatus.PENDING

    def test_cavity_user_params_have_user_source_and_pending_status(self):
        from fluid_scientist.experiment_spec.migration import migrate_plan

        plan = _make_cavity_plan()
        spec = migrate_plan(plan, "exp-user-003")
        for pid in ("side_length", "lid_velocity"):
            p = spec.get_parameter(pid)
            assert p is not None, f"parameter {pid} not found"
            assert p.source.type == ParameterSource.USER
            assert p.status == ParameterStatus.PENDING


# ---------------------------------------------------------------------------
# Test 7: Frontend displays reason in parameter rows
# ---------------------------------------------------------------------------


@pytest.fixture
def repository():
    return SQLWorkflowRepository("sqlite:///:memory:")


@pytest.fixture
def client(repository):
    app = create_app(repository=repository, execution_targets=[])
    return TestClient(app, raise_server_exceptions=False)


class TestFrontendReasonDisplay:
    """Verify the frontend includes reason display infrastructure."""

    def test_app_js_includes_spec_param_reason_class(self, client: TestClient):
        """app.js must create spec-param-reason elements."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "spec-param-reason" in js

    def test_app_js_render_parameter_row_displays_reason(self, client: TestClient):
        """renderParameterRow must display param.source.reason when available."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "function renderParameterRow(")
        assert "spec-param-reason" in body
        assert "param.source" in body
        assert "reason" in body
        assert "confidence" in body

    def test_styles_css_includes_reason_styles(self, client: TestClient):
        """CSS must include spec-param-reason styles."""
        response = client.get("/assets/styles.css")
        assert response.status_code == 200
        css = response.text
        assert ".spec-param-reason" in css
        assert 'data-confidence="high"' in css
        assert 'data-confidence="medium"' in css
        assert 'data-confidence="low"' in css
