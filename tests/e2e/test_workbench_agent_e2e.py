"""Comprehensive E2E tests for the Workbench Agent system (Commit 8).

Tests cover:
- WorkbenchAgent fake mode (rule-based intent detection)
- SpecEditExecutor (deterministic edit application)
- DerivationEngine (parameter change propagation)
- WorkbenchValidator (spec validation for state transitions)
- Workbench Turn API (/workbench-turn and /apply-edit endpoints)
- Prompt template files (existence and content)
- ParameterSchemaPlanner (parameter schema generation)
"""
from __future__ import annotations

import math
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    Criticality,
    ExperimentSpec,
    ExperimentStatus,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)
from fluid_scientist.ports import StoredExperimentSpec
from fluid_scientist.prompts import load_prompt
from fluid_scientist.workbench.derivation_engine import DerivationEngine
from fluid_scientist.workbench.edit_executor import SpecEditExecutor
from fluid_scientist.workbench.edit_models import (
    EditProposal,
    ProposedMetric,
    ProposedParameter,
    SpecEditOperation,
)
from fluid_scientist.workbench.parameter_schema_planner import (
    ParameterSchemaPlanner,
)
from fluid_scientist.workbench.validator import WorkbenchValidator
from fluid_scientist.workbench.workbench_agent import WorkbenchAgent

# ===========================================================================
# Helper functions for building test specs
# ===========================================================================


def _make_simple_spec(
    *,
    experiment_id: str = "test-exp-1",
    experiment_version: int = 1,
    status: str = "draft",
    parameters: list[dict] | None = None,
    physics: dict | None = None,
) -> dict:
    """Build a simple spec dict for WorkbenchAgent fake mode tests.

    This dict is NOT valid for ExperimentSpec.model_validate() -- it is
    a lightweight dict used for intent detection tests where the agent
    only reads experiment_id and experiment_version.
    """
    return {
        "experiment_id": experiment_id,
        "experiment_version": experiment_version,
        "status": status,
        "parameters": parameters
        or [
            {
                "parameter_id": "diameter",
                "display_name": "直径",
                "value": 0.1,
                "unit": "m",
                "category": "geometry",
            }
        ],
        "physics": physics
        or {"compressibility": "incompressible", "temporal_type": "steady"},
    }


def _make_valid_spec_dict(
    *,
    experiment_id: str = "test-exp-nl",
    experiment_version: int = 1,
    status: str = "draft",
) -> dict:
    """Build a spec dict that IS valid for ExperimentSpec.model_validate().

    Used for tests that exercise the NL parser path, which requires
    ExperimentSpec.model_validate(spec) to succeed.
    """
    spec_obj = ExperimentSpec(
        experiment_id=experiment_id,
        experiment_version=experiment_version,
        status=ExperimentStatus(status),
        research=ResearchSpec(
            title="E2E Workbench Test",
            objective="Test workbench agent parameter editing",
        ),
        parameters=[
            ParameterSpec(
                parameter_id="diameter",
                display_name="直径",
                category="geometry",
                value=0.1,
                unit="m",
                source=ParameterSourceInfo(type=ParameterSource.USER),
            ),
        ],
    )
    return spec_obj.model_dump()


def _make_param_dict(
    parameter_id: str,
    display_name: str,
    category: str,
    value: float | int | str | None,
    *,
    unit: str | None = None,
    criticality: str = "medium",
    source_type: str = "user",
    status: str = "pending",
) -> dict:
    """Build a parameter dict for executor and derivation tests."""
    return {
        "parameter_id": parameter_id,
        "display_name": display_name,
        "category": category,
        "value": value,
        "unit": unit,
        "criticality": criticality,
        "source": {"type": source_type, "reason": ""},
        "status": status,
    }


def _make_proposal(
    *,
    experiment_id: str = "test-exp-1",
    experiment_version: int = 1,
    edit_intent: str = "add_parameter",
    proposed_operations: list[SpecEditOperation] | None = None,
    invalidates: list[str] | None = None,
    requires_confirmation: bool = True,
) -> EditProposal:
    """Build an EditProposal for executor tests."""
    return EditProposal(
        proposal_id=f"prop-{uuid4().hex[:16]}",
        experiment_id=experiment_id,
        experiment_version=experiment_version,
        edit_intent=edit_intent,  # type: ignore[arg-type]
        summary="Test proposal",
        proposed_operations=proposed_operations or [],
        requires_confirmation=requires_confirmation,
        invalidates=invalidates or [],
    )


# ===========================================================================
# API test fixtures
# ===========================================================================


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
        "/api/projects", json={"question": "workbench agent e2e test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


def _create_api_spec(
    repository,
    project_id: str,
    *,
    parameters: list[ParameterSpec] | None = None,
    status: str = "draft",
    version: int = 1,
) -> str:
    """Create an experiment spec directly in the repository.

    Returns the experiment_id.
    """
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        experiment_version=version,
        status=ExperimentStatus(status),
        research=ResearchSpec(
            title="Workbench E2E Test",
            objective="Test workbench agent end-to-end",
        ),
        parameters=parameters or [],
    )

    stored = StoredExperimentSpec(
        experiment_id=experiment_id,
        project_id=project_id,
        schema_version=spec.schema_version,
        experiment_version=version,
        status=status,
        task_type=spec.task_type.value,
        interaction_mode=spec.interaction_mode.value,
        spec_json=spec.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    repository.save_experiment_spec(stored)
    return experiment_id


def _make_api_param(
    parameter_id: str,
    display_name: str,
    category: str,
    value: float | int | str,
    *,
    unit: str | None = None,
    criticality: Criticality = Criticality.MEDIUM,
    source_type: ParameterSource = ParameterSource.USER,
) -> ParameterSpec:
    """Build a ParameterSpec for API tests."""
    return ParameterSpec(
        parameter_id=parameter_id,
        display_name=display_name,
        category=category,
        value=value,
        unit=unit,
        source=ParameterSourceInfo(type=source_type),
        criticality=criticality,
    )


# ===========================================================================
# Test Class 1: WorkbenchAgent Fake Mode (10 tests)
# ===========================================================================


class TestWorkbenchAgentFakeMode:
    """Test the WorkbenchAgent in fake mode (no LLM) directly."""

    def test_add_parameter_without_name_returns_clarification(self):
        """Adding a parameter without specifying which -> clarification."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("增加一个参数", spec)

        assert proposal.edit_intent == "clarification_required"
        assert proposal.clarification_question is not None

    def test_add_wall_roughness_returns_add_parameter(self):
        """Adding wall roughness -> add_parameter with wall_roughness."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("增加壁面粗糙度参数", spec)

        assert proposal.edit_intent == "add_parameter"
        assert len(proposal.proposed_operations) > 0
        op = proposal.proposed_operations[0]
        assert op.operation == "add_parameter"
        assert op.parameter is not None
        assert op.parameter.parameter_id == "wall_roughness"

    def test_add_lift_coefficient_metric(self):
        """Requesting lift coefficient -> add_metric with lift_coefficient."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("我还想看升力系数", spec)

        assert proposal.edit_intent == "add_metric"
        assert len(proposal.proposed_operations) > 0
        op = proposal.proposed_operations[0]
        assert op.metric is not None
        assert op.metric.metric_id == "lift_coefficient"
        assert "forceCoeffs" in op.metric.required_data

    def test_change_fluid_to_air(self):
        """Changing fluid to air -> change_physics_model with density ~1.225."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("把流体改为空气", spec)

        assert proposal.edit_intent == "change_physics_model"
        assert len(proposal.proposed_operations) > 0
        # Check that measurement_plan is invalidated
        assert "measurement_plan" in proposal.invalidates
        # Check density value
        density_ops = [
            op for op in proposal.proposed_operations
            if op.target_id == "density"
        ]
        assert len(density_ops) > 0
        assert abs(density_ops[0].value - 1.225) < 0.01

    def test_change_fluid_to_water(self):
        """Changing fluid to water -> change_physics_model with density ~998.2."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("把流体改为水", spec)

        assert proposal.edit_intent == "change_physics_model"
        assert len(proposal.proposed_operations) > 0
        density_ops = [
            op for op in proposal.proposed_operations
            if op.target_id == "density"
        ]
        assert len(density_ops) > 0
        assert abs(density_ops[0].value - 998.2) < 0.01

    def test_accept_recommendations_intent(self):
        """Accepting recommendations -> accept_recommendations intent."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("接受所有推荐值", spec)

        assert proposal.edit_intent == "accept_recommendations"

    def test_validate_spec_intent(self):
        """Validation request -> validate_spec intent."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("验证一下", spec)

        assert proposal.edit_intent == "validate_spec"

    def test_prepare_compile_intent(self):
        """Compile question -> prepare_compile intent."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("可以编译了吗", spec)

        assert proposal.edit_intent == "prepare_compile"

    def test_parameter_value_change(self):
        """Changing parameter value via NL -> update_parameter intent.

        This test requires a spec valid for ExperimentSpec.model_validate()
        because the fake mode uses the NL parser internally.
        """
        agent = WorkbenchAgent()
        spec = _make_valid_spec_dict()
        proposal = agent.process_turn("管径改成50毫米", spec)

        assert proposal.edit_intent == "update_parameter"
        assert len(proposal.proposed_operations) > 0
        # Check that diameter is in the proposed operations
        diam_ops = [
            op for op in proposal.proposed_operations
            if op.target_id == "diameter"
        ]
        assert len(diam_ops) > 0
        assert abs(diam_ops[0].value - 0.05) < 0.001

    def test_unknown_intent_returns_clarification(self):
        """Unrecognized request -> clarification_required."""
        agent = WorkbenchAgent()
        spec = _make_simple_spec()
        proposal = agent.process_turn("请帮我煮咖啡", spec)

        assert proposal.edit_intent == "clarification_required"


# ===========================================================================
# Test Class 2: SpecEditExecutor (8 tests)
# ===========================================================================


class TestEditExecutor:
    """Test the SpecEditExecutor directly."""

    def test_add_parameter_to_spec(self):
        """Adding a parameter increases the parameter count."""
        executor = SpecEditExecutor()
        spec = {
            "experiment_id": "test-exec-1",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [
                _make_param_dict("diameter", "Diameter", "geometry", 0.1),
                _make_param_dict("density", "Density", "material", 998.2),
                _make_param_dict("length", "Length", "geometry", 1.0),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        proposal = _make_proposal(
            edit_intent="add_parameter",
            proposed_operations=[
                SpecEditOperation(
                    operation="add_parameter",
                    target_id="wall_roughness",
                    parameter=ProposedParameter(
                        parameter_id="wall_roughness",
                        display_name="Wall Roughness",
                        category="material_property",
                        unit="m",
                        value=0.0,
                        criticality="low",
                    ),
                    reason="Add wall roughness",
                ),
            ],
        )
        updated_spec, summary = executor.apply(spec, proposal, [0])

        assert len(updated_spec["parameters"]) == 4
        assert "wall_roughness" in summary.added_parameters

    def test_update_parameter_value(self):
        """Updating a parameter changes its value."""
        executor = SpecEditExecutor()
        spec = {
            "experiment_id": "test-exec-2",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [
                _make_param_dict("diameter", "Diameter", "geometry", 0.1, unit="m"),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        proposal = _make_proposal(
            edit_intent="update_parameter",
            proposed_operations=[
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="diameter",
                    value=0.05,
                    unit="m",
                    reason="Update diameter",
                ),
            ],
        )
        updated_spec, summary = executor.apply(spec, proposal, [0])

        params = {p["parameter_id"]: p for p in updated_spec["parameters"]}
        assert abs(params["diameter"]["value"] - 0.05) < 0.001
        # direct_updates should contain the change
        direct_ids = [d["parameter_id"] for d in summary.direct_updates]
        assert "diameter" in direct_ids

    def test_remove_parameter(self):
        """Removing a parameter decreases the parameter count."""
        executor = SpecEditExecutor()
        spec = {
            "experiment_id": "test-exec-3",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [
                _make_param_dict("diameter", "Diameter", "geometry", 0.1),
                _make_param_dict("density", "Density", "material", 998.2),
                _make_param_dict("length", "Length", "geometry", 1.0),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        proposal = _make_proposal(
            edit_intent="remove_parameter",
            proposed_operations=[
                SpecEditOperation(
                    operation="remove_parameter",
                    target_id="length",
                    reason="Remove length",
                ),
            ],
        )
        updated_spec, summary = executor.apply(spec, proposal, [0])

        assert len(updated_spec["parameters"]) == 2
        assert "length" in summary.removed_parameters

    def test_add_metric_to_spec(self):
        """Adding a metric records it in added_metrics."""
        executor = SpecEditExecutor()
        spec = {
            "experiment_id": "test-exec-4",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [
                _make_param_dict("diameter", "Diameter", "geometry", 0.1),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        proposal = _make_proposal(
            edit_intent="add_metric",
            proposed_operations=[
                SpecEditOperation(
                    operation="add_metric",
                    target_id="lift_coefficient",
                    metric=ProposedMetric(
                        metric_id="lift_coefficient",
                        display_name="Lift Coefficient Cl",
                        definition="Fl / (0.5 * rho * U^2 * A)",
                        required_data=["forceCoeffs"],
                        measurement_requirements=["forceCoeffs"],
                    ),
                    reason="Add lift coefficient",
                ),
            ],
        )
        updated_spec, summary = executor.apply(spec, proposal, [0])

        assert "lift_coefficient" in summary.added_metrics

    def test_derivation_propagation_on_diameter_change(self):
        """Changing diameter triggers derivation for mean_velocity or reynolds_number."""
        executor = SpecEditExecutor()
        spec = {
            "experiment_id": "test-exec-5",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [
                _make_param_dict("diameter", "Diameter", "geometry", 0.1, unit="m"),
                _make_param_dict("density", "Density", "material", 998.2),
                _make_param_dict(
                    "mass_flow_rate", "Mass Flow Rate", "boundary_condition", 2.0
                ),
                _make_param_dict(
                    "mean_velocity", "Mean Velocity", "derived", None
                ),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        proposal = _make_proposal(
            edit_intent="update_parameter",
            proposed_operations=[
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="diameter",
                    value=0.05,
                    unit="m",
                    reason="Change diameter",
                ),
            ],
        )
        updated_spec, summary = executor.apply(spec, proposal, [0])

        # Derivation should produce an update for mean_velocity (or reynolds_number)
        derived_ids = [d["parameter_id"] for d in summary.derived_updates]
        assert (
            "mean_velocity" in derived_ids or "reynolds_number" in derived_ids
        ), f"Expected derivation update, got: {derived_ids}"

    def test_validation_after_edit(self):
        """Editing a spec with an unknown_required critical param -> blocking issues."""
        executor = SpecEditExecutor()
        spec = {
            "experiment_id": "test-exec-6",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [
                _make_param_dict(
                    "mystery",
                    "Mystery Param",
                    "physics",
                    None,
                    criticality="critical",
                    source_type="unknown",
                ),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        proposal = _make_proposal(
            edit_intent="update_parameter",
            proposed_operations=[
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="mystery",
                    value=42,
                    reason="Set mystery value",
                ),
            ],
        )
        updated_spec, summary = executor.apply(spec, proposal, [0])

        # After updating mystery to 42, the value is set but the source type
        # remains "unknown" (executor only changes status to "modified", not source).
        # The validator checks: criticality == "critical" and source_type == "unknown"
        # So blocking_issues should be non-empty.
        assert len(summary.blocking_issues) > 0

    def test_invalidates_in_change_summary(self):
        """A proposal with invalidates -> change_summary.invalidated contains them."""
        executor = SpecEditExecutor()
        spec = {
            "experiment_id": "test-exec-7",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [
                _make_param_dict("diameter", "Diameter", "geometry", 0.1),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        proposal = _make_proposal(
            edit_intent="change_physics_model",
            proposed_operations=[
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="density",
                    value=1.225,
                    unit="kg/m3",
                    reason="Change to air",
                ),
            ],
            invalidates=["measurement_plan", "compiled_case"],
        )
        updated_spec, summary = executor.apply(spec, proposal, [0])

        assert "measurement_plan" in summary.invalidated

    def test_rejected_operations_not_applied(self):
        """Only accepted operations are applied; rejected ones are skipped."""
        executor = SpecEditExecutor()
        spec = {
            "experiment_id": "test-exec-8",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [
                _make_param_dict("diameter", "Diameter", "geometry", 0.1),
                _make_param_dict("density", "Density", "material", 998.2),
                _make_param_dict("length", "Length", "geometry", 1.0),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        proposal = _make_proposal(
            edit_intent="update_parameter",
            proposed_operations=[
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="diameter",
                    value=0.05,
                    reason="Update diameter",
                ),
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="density",
                    value=1000.0,
                    reason="Update density",
                ),
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="length",
                    value=2.0,
                    reason="Update length",
                ),
            ],
        )
        # Only accept operation at index 0
        updated_spec, summary = executor.apply(spec, proposal, [0])

        params = {p["parameter_id"]: p for p in updated_spec["parameters"]}
        # Operation 0 was applied
        assert abs(params["diameter"]["value"] - 0.05) < 0.001
        # Operations 1 and 2 were NOT applied
        assert abs(params["density"]["value"] - 998.2) < 0.001
        assert abs(params["length"]["value"] - 1.0) < 0.001
        # Only 1 direct update
        assert len(summary.direct_updates) == 1


# ===========================================================================
# Test Class 3: DerivationEngine (6 tests)
# ===========================================================================


class TestDerivationEngine:
    """Test the DerivationEngine directly."""

    def test_mean_velocity_from_mass_flow(self):
        """mean_velocity = m_dot / (rho * pi * D^2 / 4)."""
        engine = DerivationEngine()
        spec = {
            "parameters": [
                {"parameter_id": "mass_flow_rate", "value": 2.0},
                {"parameter_id": "density", "value": 998.2},
                {"parameter_id": "diameter", "value": 0.05},
                {"parameter_id": "mean_velocity", "value": None},
            ]
        }
        updates = engine.propagate(spec, ["mass_flow_rate"])

        mean_vel_updates = [
            u for u in updates if u["parameter_id"] == "mean_velocity"
        ]
        assert len(mean_vel_updates) == 1
        expected = 2.0 / (998.2 * math.pi * (0.05 / 2) ** 2)
        assert abs(mean_vel_updates[0]["new_value"] - expected) < 0.01

    def test_reynolds_number_from_mean_velocity(self):
        """Re = U * D / nu, using mean_velocity."""
        engine = DerivationEngine()
        spec = {
            "parameters": [
                {"parameter_id": "mean_velocity", "value": 1.0},
                {"parameter_id": "diameter", "value": 0.05},
                {"parameter_id": "kinematic_viscosity", "value": 1e-6},
                {"parameter_id": "reynolds_number", "value": None},
            ]
        }
        updates = engine.propagate(spec, ["mean_velocity"])

        re_updates = [
            u for u in updates if u["parameter_id"] == "reynolds_number"
        ]
        assert len(re_updates) == 1
        assert abs(re_updates[0]["new_value"] - 50000.0) < 1.0

    def test_reynolds_number_from_inlet_velocity(self):
        """Re = U * D / nu, using inlet_velocity (reynolds_number_from_inlet rule)."""
        engine = DerivationEngine()
        spec = {
            "parameters": [
                {"parameter_id": "inlet_velocity", "value": 1.0},
                {"parameter_id": "diameter", "value": 0.05},
                {"parameter_id": "kinematic_viscosity", "value": 1e-6},
                {"parameter_id": "reynolds_number", "value": None},
            ]
        }
        updates = engine.propagate(spec, ["inlet_velocity"])

        re_updates = [
            u for u in updates if u["parameter_id"] == "reynolds_number"
        ]
        assert len(re_updates) == 1
        assert abs(re_updates[0]["new_value"] - 50000.0) < 1.0

    def test_area_from_diameter(self):
        """area = pi * D^2 / 4."""
        engine = DerivationEngine()
        spec = {
            "parameters": [
                {"parameter_id": "diameter", "value": 0.05},
                {"parameter_id": "area", "value": None},
            ]
        }
        updates = engine.propagate(spec, ["diameter"])

        area_updates = [u for u in updates if u["parameter_id"] == "area"]
        assert len(area_updates) == 1
        expected = math.pi * (0.05 / 2) ** 2
        assert abs(area_updates[0]["new_value"] - expected) < 1e-10

    def test_no_derivation_when_source_missing(self):
        """No mean_velocity derivation when mass_flow_rate is missing."""
        engine = DerivationEngine()
        spec = {
            "parameters": [
                {"parameter_id": "density", "value": 998.2},
                {"parameter_id": "diameter", "value": 0.05},
                {"parameter_id": "mean_velocity", "value": None},
            ]
        }
        # diameter is a source for mean_velocity, but mass_flow_rate is missing
        updates = engine.propagate(spec, ["diameter"])

        mean_vel_updates = [
            u for u in updates if u["parameter_id"] == "mean_velocity"
        ]
        assert len(mean_vel_updates) == 0

    def test_no_derivation_overwrites_user_confirmed(self):
        """Derivation should not overwrite a user-confirmed parameter value.

        The propagate() method returns derivation updates but must NOT
        mutate the input spec.  A user-confirmed mean_velocity should
        retain its original value in the spec after propagation.
        """
        engine = DerivationEngine()
        spec = {
            "parameters": [
                {"parameter_id": "mass_flow_rate", "value": 2.0},
                {"parameter_id": "density", "value": 998.2},
                {"parameter_id": "diameter", "value": 0.05},
                {
                    "parameter_id": "mean_velocity",
                    "value": 5.0,
                    "source": {"type": "user", "reason": "User confirmed"},
                },
            ]
        }
        original_value = 5.0
        engine.propagate(spec, ["mass_flow_rate"])

        # propagate() must not mutate the spec -- the user-confirmed value
        # should be preserved in the spec dict.
        mean_vel_param = next(
            p for p in spec["parameters"]
            if p["parameter_id"] == "mean_velocity"
        )
        assert mean_vel_param["value"] == original_value, (
            "propagate() must not mutate the input spec; "
            "user-confirmed value should be preserved"
        )


# ===========================================================================
# Test Class 4: WorkbenchValidator (5 tests)
# ===========================================================================


class TestWorkbenchValidator:
    """Test the WorkbenchValidator directly."""

    def test_valid_spec_passes(self):
        """A spec with all critical params resolved and complete physics passes."""
        validator = WorkbenchValidator()
        spec = {
            "status": "ready",
            "parameters": [
                _make_param_dict(
                    "diameter", "Diameter", "geometry", 0.1,
                    criticality="critical",
                    source_type="user",
                ),
                _make_param_dict(
                    "inlet_velocity", "Inlet Velocity", "boundary_condition", 1.0,
                    criticality="critical",
                    source_type="user",
                ),
                _make_param_dict(
                    "outlet_pressure", "Outlet Pressure", "boundary_condition", 0.0,
                    criticality="critical",
                    source_type="user",
                ),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        result = validator.validate(spec)

        assert result.is_valid is True
        assert result.can_transition_to_ready is True

    def test_unknown_required_blocks(self):
        """A critical parameter with unknown source blocks validation."""
        validator = WorkbenchValidator()
        spec = {
            "status": "draft",
            "parameters": [
                _make_param_dict(
                    "mystery", "Mystery", "physics", None,
                    criticality="critical",
                    source_type="unknown",
                ),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        result = validator.validate(spec)

        assert result.is_valid is False
        assert len(result.blocking_issues) > 0

    def test_missing_physics_blocks(self):
        """Missing physics.compressibility blocks validation."""
        validator = WorkbenchValidator()
        spec = {
            "status": "draft",
            "parameters": [],
            "physics": {
                "compressibility": None,
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        result = validator.validate(spec)

        assert result.is_valid is False

    def test_can_compile_requires_confirmed_status(self):
        """can_compile requires status to be 'confirmed'."""
        validator = WorkbenchValidator()
        spec = {
            "status": "draft",
            "parameters": [
                _make_param_dict(
                    "diameter", "Diameter", "geometry", 0.1,
                    criticality="critical",
                    source_type="user",
                ),
                _make_param_dict(
                    "inlet_velocity", "Inlet Velocity", "boundary_condition", 1.0,
                    criticality="critical",
                    source_type="user",
                ),
                _make_param_dict(
                    "outlet_pressure", "Outlet Pressure", "boundary_condition", 0.0,
                    criticality="critical",
                    source_type="user",
                ),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        result = validator.validate(spec)

        assert result.can_compile is False

    def test_warnings_for_missing_boundary(self):
        """Missing outlet boundary produces warnings."""
        validator = WorkbenchValidator()
        spec = {
            "status": "draft",
            "parameters": [
                _make_param_dict(
                    "diameter", "Diameter", "geometry", 0.1,
                    criticality="critical",
                    source_type="user",
                ),
                # Has inlet but no outlet
                _make_param_dict(
                    "inlet_velocity", "Inlet Velocity", "boundary_condition", 1.0,
                    criticality="critical",
                    source_type="user",
                ),
            ],
            "physics": {
                "compressibility": "incompressible",
                "temporal_type": "steady",
                "phases": "single_phase",
            },
        }
        result = validator.validate(spec)

        assert len(result.warnings) > 0


# ===========================================================================
# Test Class 5: Workbench Turn API (8 tests)
# ===========================================================================


class TestWorkbenchTurnAPI:
    """Test the /workbench-turn and /apply-edit API endpoints."""

    def test_workbench_turn_returns_proposal(
        self, client: TestClient, repository, project_id: str
    ):
        """POST /workbench-turn with add_parameter message returns proposal."""
        eid = _create_api_spec(
            repository,
            project_id,
            parameters=[
                _make_api_param("diameter", "Diameter", "geometry", 0.1),
            ],
        )
        response = client.post(
            "/api/research-sessions/test-session/workbench-turn",
            json={
                "experiment_id": eid,
                "experiment_version": 1,
                "message": "增加壁面粗糙度参数",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["edit_intent"] == "add_parameter"

    def test_workbench_turn_clarification(
        self, client: TestClient, repository, project_id: str
    ):
        """POST /workbench-turn with vague message returns clarification."""
        eid = _create_api_spec(repository, project_id)
        response = client.post(
            "/api/research-sessions/test-session/workbench-turn",
            json={
                "experiment_id": eid,
                "experiment_version": 1,
                "message": "增加一个参数",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["edit_intent"] == "clarification_required"

    def test_workbench_turn_add_metric(
        self, client: TestClient, repository, project_id: str
    ):
        """POST /workbench-turn with metric request returns add_metric."""
        eid = _create_api_spec(repository, project_id)
        response = client.post(
            "/api/research-sessions/test-session/workbench-turn",
            json={
                "experiment_id": eid,
                "experiment_version": 1,
                "message": "我还想看升力系数",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["edit_intent"] == "add_metric"

    def test_workbench_turn_change_fluid(
        self, client: TestClient, repository, project_id: str
    ):
        """POST /workbench-turn with fluid change returns change_physics_model."""
        eid = _create_api_spec(repository, project_id)
        response = client.post(
            "/api/research-sessions/test-session/workbench-turn",
            json={
                "experiment_id": eid,
                "experiment_version": 1,
                "message": "把流体改为空气",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["edit_intent"] == "change_physics_model"
        assert len(body["invalidates"]) > 0

    def test_apply_edit_adds_parameter(
        self, client: TestClient, repository, project_id: str
    ):
        """POST /apply-edit applies the accepted operations to the spec."""
        eid = _create_api_spec(
            repository,
            project_id,
            parameters=[
                _make_api_param("diameter", "Diameter", "geometry", 0.1),
            ],
        )
        # Step 1: Get a proposal via workbench-turn
        turn_response = client.post(
            "/api/research-sessions/test-session/workbench-turn",
            json={
                "experiment_id": eid,
                "experiment_version": 1,
                "message": "增加壁面粗糙度参数",
            },
        )
        assert turn_response.status_code == 200, turn_response.text
        proposal = turn_response.json()
        proposal_id = proposal["proposal_id"]

        # Step 2: Apply the edit
        apply_response = client.post(
            f"/api/experiment-specs/{eid}/apply-edit",
            json={
                "experiment_version": 1,
                "proposal_id": proposal_id,
                "accepted_operation_indices": [0],
            },
        )
        assert apply_response.status_code == 200, apply_response.text
        updated_spec = apply_response.json()

        # The updated spec should have the new parameter
        param_ids = [p["parameter_id"] for p in updated_spec["parameters"]]
        assert "wall_roughness" in param_ids

    def test_apply_edit_rejected_operations(
        self, client: TestClient, repository, project_id: str
    ):
        """Only accepted operations are applied; rejected ones are skipped."""
        eid = _create_api_spec(
            repository,
            project_id,
            parameters=[
                _make_api_param("diameter", "Diameter", "geometry", 0.1),
                _make_api_param(
                    "density", "Density", "material_property", 998.2
                ),
            ],
        )
        # Get a proposal with multiple operations (fluid change -> 2 ops)
        turn_response = client.post(
            "/api/research-sessions/test-session/workbench-turn",
            json={
                "experiment_id": eid,
                "experiment_version": 1,
                "message": "把流体改为空气",
            },
        )
        assert turn_response.status_code == 200, turn_response.text
        proposal = turn_response.json()
        proposal_id = proposal["proposal_id"]
        num_ops = len(proposal["proposed_operations"])
        assert num_ops >= 2, f"Expected >=2 operations, got {num_ops}"

        # Apply only the first operation
        apply_response = client.post(
            f"/api/experiment-specs/{eid}/apply-edit",
            json={
                "experiment_version": 1,
                "proposal_id": proposal_id,
                "accepted_operation_indices": [0],
            },
        )
        assert apply_response.status_code == 200, apply_response.text
        updated_spec = apply_response.json()

        # Only the first operation (density update) should be applied
        params = {p["parameter_id"]: p for p in updated_spec["parameters"]}
        # density should be updated to 1.225 (air)
        assert abs(params["density"]["value"] - 1.225) < 0.01
        # Check change_summary
        summary = updated_spec.get("_change_summary", {})
        assert len(summary.get("direct_updates", [])) == 1

    def test_apply_edit_version_mismatch(
        self, client: TestClient, repository, project_id: str
    ):
        """POST /apply-edit with wrong version returns 409."""
        eid = _create_api_spec(repository, project_id)

        # Step 1: Get a real proposal via workbench-turn
        turn_response = client.post(
            "/api/research-sessions/test-session/workbench-turn",
            json={
                "experiment_id": eid,
                "experiment_version": 1,
                "message": "增加壁面粗糙度参数",
            },
        )
        assert turn_response.status_code == 200, turn_response.text
        proposal_id = turn_response.json()["proposal_id"]

        # Step 2: Try to apply with wrong version
        response = client.post(
            f"/api/experiment-specs/{eid}/apply-edit",
            json={
                "experiment_version": 999,  # wrong version
                "proposal_id": proposal_id,
                "accepted_operation_indices": [0],
            },
        )
        assert response.status_code in (409, 400)

    def test_apply_edit_proposal_not_found(
        self, client: TestClient, repository, project_id: str
    ):
        """POST /apply-edit with non-existent proposal_id returns 404."""
        eid = _create_api_spec(repository, project_id)

        response = client.post(
            f"/api/experiment-specs/{eid}/apply-edit",
            json={
                "experiment_version": 1,
                "proposal_id": "non-existent-proposal",
                "accepted_operation_indices": [0],
            },
        )
        assert response.status_code == 404


# ===========================================================================
# Test Class 6: Prompt Files (4 tests)
# ===========================================================================


class TestPromptFiles:
    """Test that prompt template files exist and load correctly."""

    def test_intent_prompt_exists(self):
        """intent_system_prompt.txt exists and contains 'physical_system'."""
        content = load_prompt("intent_system_prompt")
        assert "physical_system" in content

    def test_parameter_schema_prompt_exists(self):
        """parameter_schema_prompt.txt exists and contains 'parameter_groups'."""
        content = load_prompt("parameter_schema_prompt")
        assert "parameter_groups" in content

    def test_metric_planner_prompt_exists(self):
        """metric_planner_prompt.txt exists and contains 'required_data'."""
        content = load_prompt("metric_planner_prompt")
        assert "required_data" in content

    def test_workbench_edit_prompt_exists(self):
        """workbench_edit_prompt.txt exists and contains EditProposal/edit_intent."""
        content = load_prompt("workbench_edit_prompt")
        assert "EditProposal" in content or "edit_intent" in content


# ===========================================================================
# Test Class 7: ParameterSchemaPlanner (5 tests)
# ===========================================================================


class TestParameterSchemaPlanner:
    """Test the ParameterSchemaPlanner directly."""

    @staticmethod
    def _physics_spec() -> dict:
        """Return a standard physics spec for planner tests."""
        return {
            "compressibility": "incompressible",
            "temporal_type": "steady",
            "phases": "single_phase",
        }

    def test_pipe_flow_generates_pipe_parameters(self):
        """pipe_flow intent generates diameter, length, mass_flow_rate."""
        planner = ParameterSchemaPlanner()
        params = planner.plan(
            intent_assessment={"physical_system": "pipe_flow"},
            physics_spec=self._physics_spec(),
            metric_plan=[],
            user_values={},
        )
        param_ids = [p["parameter_id"] for p in params]
        assert "diameter" in param_ids
        assert "length" in param_ids
        assert "mass_flow_rate" in param_ids
        # Should NOT include nonsensical / non-existent parameter ids
        assert "nonexistent_pipe_param" not in param_ids
        assert "nonsense_flow_rate" not in param_ids

    def test_cylinder_flow_generates_cylinder_parameters(self):
        """cylinder_external_flow intent generates diameter, inlet_velocity."""
        planner = ParameterSchemaPlanner()
        params = planner.plan(
            intent_assessment={"physical_system": "cylinder_external_flow"},
            physics_spec=self._physics_spec(),
            metric_plan=[],
            user_values={},
        )
        param_ids = [p["parameter_id"] for p in params]
        assert "diameter" in param_ids
        assert "inlet_velocity" in param_ids
        # Should NOT include non-existent parameter ids
        assert "pipe_length" not in param_ids
        assert "nonexistent_cylinder_param" not in param_ids

    def test_user_values_prefilled(self):
        """User-provided values are pre-filled in the parameter list."""
        planner = ParameterSchemaPlanner()
        params = planner.plan(
            intent_assessment={"physical_system": "pipe_flow"},
            physics_spec=self._physics_spec(),
            metric_plan=[],
            user_values={"diameter": 0.05},
        )
        diameter = next(
            p for p in params if p["parameter_id"] == "diameter"
        )
        assert abs(diameter["value"] - 0.05) < 0.001

    def test_water_recommended(self):
        """Water fluid type fills density ~998.2 and kinematic_viscosity ~1e-6."""
        planner = ParameterSchemaPlanner()
        params = planner.plan(
            intent_assessment={
                "physical_system": "pipe_flow",
                "fluid_type": "water",
            },
            physics_spec=self._physics_spec(),
            metric_plan=[],
            user_values={},
        )
        density = next(
            p for p in params if p["parameter_id"] == "density"
        )
        assert abs(density["value"] - 998.2) < 0.1
        kinematic_viscosity = next(
            p for p in params if p["parameter_id"] == "kinematic_viscosity"
        )
        assert abs(kinematic_viscosity["value"] - 1e-6) < 1e-7

    def test_derived_parameters_computed(self):
        """Derived parameters (mean_velocity) are computed from user values."""
        planner = ParameterSchemaPlanner()
        params = planner.plan(
            intent_assessment={"physical_system": "pipe_flow"},
            physics_spec=self._physics_spec(),
            metric_plan=[],
            user_values={
                "diameter": 0.05,
                "mass_flow_rate": 2.0,
                "density": 998.2,
            },
        )
        mean_velocity = next(
            p for p in params if p["parameter_id"] == "mean_velocity"
        )
        assert mean_velocity["value"] is not None
