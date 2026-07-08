"""Tests for natural language batch parameter modification.

Commit 5: Natural language edit API that parses instructions like
"把管径改成50毫米，长度改成5米" and returns proposed changes for
user confirmation.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    ConfirmationPolicy,
    Criticality,
    ExperimentSpec,
    ParameterDependency,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)
from fluid_scientist.experiment_spec.nl_parser import parse_nl_instruction
from fluid_scientist.ports import StoredExperimentSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        "/api/projects", json={"question": "natural language edit test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


def _make_param(
    parameter_id: str,
    display_name: str,
    category: str,
    value: float | int | str,
    *,
    unit: str | None = None,
    criticality: Criticality = Criticality.MEDIUM,
    source_type: ParameterSource = ParameterSource.USER,
    impact_scope: list[str] | None = None,
    dependencies: ParameterDependency | None = None,
    confirmation_policy: ConfirmationPolicy = ConfirmationPolicy.RECOMMEND_AND_NOTIFY,
) -> ParameterSpec:
    """Build a minimal ParameterSpec with proper enum values."""
    return ParameterSpec(
        parameter_id=parameter_id,
        display_name=display_name,
        category=category,
        value=value,
        unit=unit,
        source=ParameterSourceInfo(type=source_type),
        criticality=criticality,
        impact_scope=impact_scope or [],
        dependencies=dependencies or ParameterDependency(),
        confirmation_policy=confirmation_policy,
    )


def _build_spec(parameters: list[ParameterSpec] | None = None) -> ExperimentSpec:
    """Build an ExperimentSpec directly (for parser unit tests)."""
    return ExperimentSpec(
        experiment_id=f"exp-{uuid4().hex[:16]}",
        research=ResearchSpec(
            title="NL Edit Test",
            objective="Test natural language parameter parsing",
        ),
        parameters=parameters or [],
    )


def _create_spec(
    repository,
    project_id: str,
    *,
    parameters: list[ParameterSpec] | None = None,
) -> str:
    """Create an experiment spec directly in the repository.

    Returns the experiment_id.
    """
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        research=ResearchSpec(
            title="NL Edit API Test",
            objective="Test natural language edit API endpoint",
        ),
        parameters=parameters or [],
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


def _nl_parameters() -> list[ParameterSpec]:
    """Return a standard set of parameters for NL parsing tests."""
    return [
        _make_param(
            "diameter",
            "Cylinder Diameter",
            "geometry",
            0.1,
            unit="m",
            criticality=Criticality.CRITICAL,
        ),
        _make_param(
            "length",
            "Pipe Length",
            "geometry",
            1.0,
            unit="m",
        ),
        _make_param(
            "density",
            "Density",
            "fluid_property",
            1.0,
            unit="kg/m3",
        ),
        _make_param(
            "inlet_velocity",
            "Inlet Velocity",
            "boundary_condition",
            0.01,
            unit="m/s",
        ),
        _make_param(
            "reynolds_number",
            "Reynolds Number",
            "physics",
            1000.0,
            source_type=ParameterSource.DERIVED,
            dependencies=ParameterDependency(depends_on=["inlet_velocity"]),
        ),
    ]


@pytest.fixture
def spec_with_nl_parameters(repository, project_id):
    """Create an experiment spec with parameters for NL edit API testing."""
    experiment_id = _create_spec(
        repository, project_id, parameters=_nl_parameters()
    )
    return {
        "project_id": project_id,
        "experiment_id": experiment_id,
    }


# ---------------------------------------------------------------------------
# Parser unit tests (tests 1-7, 10)
# ---------------------------------------------------------------------------


class TestNLParser:
    """Verify the rule-based NL parser directly."""

    def test_parse_diameter_with_mm_unit(self):
        """Test 1: '把管径改成50毫米' returns proposed change for diameter = 0.05."""
        spec = _build_spec(_nl_parameters())
        result = parse_nl_instruction("把管径改成50毫米", spec)

        assert len(result.proposed_changes) == 1
        change = result.proposed_changes[0]
        assert change.parameter_id == "diameter"
        assert change.new_value == pytest.approx(0.05)
        assert change.unit == "m"

    def test_parse_length_with_chinese_meter(self):
        """Test 2: '长度改成5米' returns proposed change for length = 5.0."""
        spec = _build_spec(_nl_parameters())
        result = parse_nl_instruction("长度改成5米", spec)

        assert len(result.proposed_changes) == 1
        change = result.proposed_changes[0]
        assert change.parameter_id == "length"
        assert change.new_value == pytest.approx(5.0)
        assert change.unit == "m"

    def test_parse_density_without_unit(self):
        """Test 3: '密度设为1000' returns proposed change for density."""
        spec = _build_spec(_nl_parameters())
        result = parse_nl_instruction("密度设为1000", spec)

        assert len(result.proposed_changes) == 1
        change = result.proposed_changes[0]
        assert change.parameter_id == "density"
        assert change.new_value == pytest.approx(1000.0)

    def test_parse_multiple_parameters(self):
        """Test 4: Multiple parameters in one instruction are all parsed."""
        spec = _build_spec(_nl_parameters())
        instruction = "把管径改成50毫米，长度改成5米，密度设为1000"
        result = parse_nl_instruction(instruction, spec)

        assert len(result.proposed_changes) == 3
        ids = {c.parameter_id for c in result.proposed_changes}
        assert ids == {"diameter", "length", "density"}

    def test_unmatched_segments_returned(self):
        """Test 5: Unmatched segments are returned in unmatched_segments."""
        spec = _build_spec(_nl_parameters())
        instruction = "把管径改成50毫米，随便说点什么"
        result = parse_nl_instruction(instruction, spec)

        assert len(result.proposed_changes) == 1
        assert result.proposed_changes[0].parameter_id == "diameter"
        assert len(result.unmatched_segments) == 1
        assert "随便说点什么" in result.unmatched_segments[0]

    def test_requires_confirmation_true_when_changes_found(self):
        """Test 6: requires_confirmation is True when changes are found."""
        spec = _build_spec(_nl_parameters())
        result = parse_nl_instruction("把管径改成50毫米", spec)

        assert len(result.proposed_changes) > 0
        assert result.requires_confirmation is True

    def test_requires_confirmation_false_when_no_changes(self):
        """Test 7: requires_confirmation is False when no changes found."""
        spec = _build_spec(_nl_parameters())
        result = parse_nl_instruction("随便说点什么没有参数", spec)

        assert len(result.proposed_changes) == 0
        assert result.requires_confirmation is False

    def test_unit_conversion_mm_to_m(self):
        """Test 10: Unit conversion from mm to m works correctly."""
        spec = _build_spec(_nl_parameters())
        result = parse_nl_instruction("管径改成200毫米", spec)

        assert len(result.proposed_changes) == 1
        change = result.proposed_changes[0]
        assert change.parameter_id == "diameter"
        assert change.new_value == pytest.approx(0.2)
        assert change.unit == "m"


# ---------------------------------------------------------------------------
# API endpoint tests (tests 8, 9, 11)
# ---------------------------------------------------------------------------


class TestNaturalLanguageEditAPI:
    """Verify the natural-language-edit API endpoint."""

    def test_version_conflict_returns_409(
        self, client: TestClient, spec_with_nl_parameters: dict
    ):
        """Test 8: Version conflict returns 409."""
        project_id = spec_with_nl_parameters["project_id"]
        experiment_id = spec_with_nl_parameters["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 99,
                "instruction": "把管径改成50毫米",
            },
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error"] == "version_conflict"
        assert detail["current_version"] == 1
        assert detail["client_version"] == 99

    def test_empty_instruction_returns_422(
        self, client: TestClient, spec_with_nl_parameters: dict
    ):
        """Test 9: Empty instruction returns 422."""
        project_id = spec_with_nl_parameters["project_id"]
        experiment_id = spec_with_nl_parameters["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "",
            },
        )

        assert response.status_code == 422

    def test_whitespace_instruction_returns_422(
        self, client: TestClient, spec_with_nl_parameters: dict
    ):
        """Whitespace-only instruction returns 422."""
        project_id = spec_with_nl_parameters["project_id"]
        experiment_id = spec_with_nl_parameters["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "   ",
            },
        )

        assert response.status_code == 422

    def test_derived_updates_preview_populated(
        self, client: TestClient, spec_with_nl_parameters: dict
    ):
        """Test 11: derived_updates_preview is populated for dependent parameters."""
        project_id = spec_with_nl_parameters["project_id"]
        experiment_id = spec_with_nl_parameters["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "入口速度改成0.05",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()

        # inlet_velocity was modified
        proposed_ids = [c["parameter_id"] for c in body["proposed_changes"]]
        assert "inlet_velocity" in proposed_ids

        # reynolds_number depends on inlet_velocity → should be in derived preview
        derived_ids = [
            d["parameter_id"] for d in body["derived_updates_preview"]
        ]
        assert "reynolds_number" in derived_ids

        # Each derived preview entry should have display_name and reason
        for d in body["derived_updates_preview"]:
            assert "display_name" in d
            assert "reason" in d

    def test_api_returns_proposed_changes(
        self, client: TestClient, spec_with_nl_parameters: dict
    ):
        """API endpoint returns proposed_changes with old/new values."""
        project_id = spec_with_nl_parameters["project_id"]
        experiment_id = spec_with_nl_parameters["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "把管径改成50毫米",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()

        assert body["experiment_version"] == 1
        assert len(body["proposed_changes"]) == 1
        change = body["proposed_changes"][0]
        assert change["parameter_id"] == "diameter"
        assert change["new_value"] == pytest.approx(0.05)
        assert change["matched_term"] == "管径"

    def test_api_returns_unmatched_segments(
        self, client: TestClient, spec_with_nl_parameters: dict
    ):
        """API endpoint returns unmatched_segments for unrecognized text."""
        project_id = spec_with_nl_parameters["project_id"]
        experiment_id = spec_with_nl_parameters["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "把管径改成50毫米，胡言乱语",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()

        assert len(body["proposed_changes"]) == 1
        assert len(body["unmatched_segments"]) == 1
        assert "胡言乱语" in body["unmatched_segments"][0]

    def test_api_does_not_modify_spec(
        self, client: TestClient, spec_with_nl_parameters: dict
    ):
        """The natural-language-edit endpoint must NOT apply changes."""
        project_id = spec_with_nl_parameters["project_id"]
        experiment_id = spec_with_nl_parameters["experiment_id"]

        # Call NL edit
        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "把管径改成50毫米",
            },
        )
        assert response.status_code == 200

        # Verify the spec was NOT modified by fetching it
        get_response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}"
        )
        assert get_response.status_code == 200
        spec = get_response.json()
        params = {p["parameter_id"]: p for p in spec["parameters"]}
        assert params["diameter"]["value"] == 0.1  # original value unchanged


# ---------------------------------------------------------------------------
# Frontend tests (test 12)
# ---------------------------------------------------------------------------


class TestFrontendNLUI:
    """Verify the web assets include NL edit UI."""

    def test_app_js_includes_nl_input(self, client: TestClient):
        """app.js must include spec-nl-input element."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "spec-nl-input" in js

    def test_app_js_includes_parse_natural_language_edit(self, client: TestClient):
        """app.js must include parseNaturalLanguageEdit function."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "parseNaturalLanguageEdit" in js
        assert "async function parseNaturalLanguageEdit" in js

    def test_app_js_includes_render_nl_preview(self, client: TestClient):
        """app.js must include renderNLPreview function."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "renderNLPreview" in js
        assert "function renderNLPreview" in js

    def test_app_js_includes_apply_nl_changes(self, client: TestClient):
        """app.js must include applyNLChanges function."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "applyNLChanges" in js

    def test_app_js_nl_edit_calls_correct_endpoint(self, client: TestClient):
        """parseNaturalLanguageEdit must call the natural-language-edit endpoint."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "natural-language-edit" in js

    def test_styles_include_nl_edit_css(self, client: TestClient):
        """CSS must include NL edit styles."""
        response = client.get("/assets/styles.css")
        assert response.status_code == 200
        css = response.text
        assert "spec-nl-edit" in css
        assert "spec-nl-input" in css
        assert "spec-nl-preview" in css
