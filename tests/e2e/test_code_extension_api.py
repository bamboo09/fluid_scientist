"""E2E tests for the CodeExtension user loop API endpoints.

Verifies CRUD, approval workflow, and state recovery for code extensions
exposed via:
  GET    /api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions
  POST   /api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions
  GET    /api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}
  POST   /api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/approve
  POST   /api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/reject
  POST   /api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/register
  GET    /api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/history
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
    ExperimentSpec,
    ExperimentStatus,
    ResearchSpec,
)
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
        "/api/projects", json={"question": "code extension api e2e test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_extension(
    extension_id: str = "",
    name: str = "Test Extension",
    status: str = "auto_tested",
    extension_type: str = "function_object",
    code: str = "def my_func():\n    return 42\n",
) -> dict:
    """Build a minimal extension dict for direct repository insertion."""
    return {
        "extension_id": extension_id or f"ext-{uuid4().hex[:12]}",
        "name": name,
        "description": "A test extension",
        "extension_type": extension_type,
        "code": code,
        "language": "python",
        "dependencies": [],
        "openfoam_files": [],
        "tests": [],
        "status": status,
        "version": "1.0.0",
        "author": "tester",
        "review_notes": "",
        "created_at": "",
        "updated_at": "",
    }


def _create_spec(
    repository,
    project_id: str,
    *,
    code_extensions: list[dict] | None = None,
    status: ExperimentStatus = ExperimentStatus.DRAFT,
) -> str:
    """Create an experiment spec directly in the repository.

    Returns the experiment_id.
    """
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        research=ResearchSpec(
            title="Code Extension Test",
            objective="Test the code extension API endpoints",
        ),
        status=status,
        code_extensions=code_extensions or [],
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


def _ext_url(project_id: str, experiment_id: str, extension_id: str = "") -> str:
    """Build the code-extensions URL."""
    base = (
        f"/api/projects/{project_id}/experiment-specs/{experiment_id}"
        f"/code-extensions"
    )
    if extension_id:
        return f"{base}/{extension_id}"
    return base


# ---------------------------------------------------------------------------
# Test 1: List code extensions (empty)
# ---------------------------------------------------------------------------


def test_list_code_extensions_empty(client, repository, project_id):
    """GET /code-extensions on a spec with no extensions returns empty list."""
    experiment_id = _create_spec(repository, project_id)

    response = client.get(_ext_url(project_id, experiment_id))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["experiment_id"] == experiment_id
    assert data["code_extensions"] == []


# ---------------------------------------------------------------------------
# Test 2: Create code extension
# ---------------------------------------------------------------------------


def test_create_code_extension(client, repository, project_id):
    """POST /code-extensions creates an extension with DRAFT status."""
    experiment_id = _create_spec(repository, project_id)

    response = client.post(
        _ext_url(project_id, experiment_id),
        json={
            "name": "My Function Object",
            "extension_type": "function_object",
            "source_code": "def compute():\n    return 3.14\n",
            "description": "A test function object",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["experiment_id"] == experiment_id
    ext = data["code_extension"]
    assert ext["name"] == "My Function Object"
    assert ext["status"] == "draft"
    assert ext["extension_type"] == "function_object"
    assert ext["extension_id"].startswith("ext-")

    # Verify it was persisted
    response = client.get(_ext_url(project_id, experiment_id))
    assert response.status_code == 200
    assert len(response.json()["code_extensions"]) == 1


# ---------------------------------------------------------------------------
# Test 3: Get code extension
# ---------------------------------------------------------------------------


def test_get_code_extension(client, repository, project_id):
    """GET /code-extensions/{extension_id} returns the extension details."""
    ext_id = "ext-test-001"
    experiment_id = _create_spec(
        repository,
        project_id,
        code_extensions=[
            _make_extension(extension_id=ext_id, name="Boundary BC"),
        ],
    )

    response = client.get(_ext_url(project_id, experiment_id, ext_id))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["experiment_id"] == experiment_id
    assert data["code_extension"]["extension_id"] == ext_id
    assert data["code_extension"]["name"] == "Boundary BC"


# ---------------------------------------------------------------------------
# Test 4: Approve code extension
# ---------------------------------------------------------------------------


def test_approve_code_extension(client, repository, project_id):
    """POST /approve transitions auto_tested -> approved."""
    ext_id = "ext-approve-001"
    experiment_id = _create_spec(
        repository,
        project_id,
        code_extensions=[
            _make_extension(extension_id=ext_id, status="auto_tested"),
        ],
    )

    response = client.post(
        _ext_url(project_id, experiment_id, ext_id) + "/approve",
        json={"reviewer": "alice", "notes": "Looks good"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    extensions = data["code_extension"]
    assert isinstance(extensions, list)
    approved = next(e for e in extensions if e["extension_id"] == ext_id)
    assert approved["status"] == "approved"
    assert "Approved by alice" in approved["review_notes"]


# ---------------------------------------------------------------------------
# Test 5: Reject code extension
# ---------------------------------------------------------------------------


def test_reject_code_extension(client, repository, project_id):
    """POST /reject transitions any status -> rejected."""
    ext_id = "ext-reject-001"
    experiment_id = _create_spec(
        repository,
        project_id,
        code_extensions=[
            _make_extension(extension_id=ext_id, status="auto_tested"),
        ],
    )

    response = client.post(
        _ext_url(project_id, experiment_id, ext_id) + "/reject",
        json={"reason": "Code has security issues"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    extensions = data["code_extension"]
    rejected = next(e for e in extensions if e["extension_id"] == ext_id)
    assert rejected["status"] == "rejected"
    assert "Rejected: Code has security issues" in rejected["review_notes"]


# ---------------------------------------------------------------------------
# Test 6: Register code extension
# ---------------------------------------------------------------------------


def test_register_code_extension(client, repository, project_id):
    """POST /register transitions approved -> registered."""
    ext_id = "ext-register-001"
    experiment_id = _create_spec(
        repository,
        project_id,
        code_extensions=[
            _make_extension(extension_id=ext_id, status="approved"),
        ],
    )

    response = client.post(
        _ext_url(project_id, experiment_id, ext_id) + "/register",
    )
    assert response.status_code == 200, response.text
    data = response.json()
    extensions = data["code_extension"]
    registered = next(e for e in extensions if e["extension_id"] == ext_id)
    assert registered["status"] == "registered"


# ---------------------------------------------------------------------------
# Test 7: State recovery — all approved transitions spec to confirmed
# ---------------------------------------------------------------------------


def test_state_recovery_all_approved(client, repository, project_id):
    """Approving all auto_tested extensions in an AWAITING_CODE_APPROVAL
    spec transitions the spec back to confirmed."""
    ext_id_1 = "ext-recovery-001"
    ext_id_2 = "ext-recovery-002"
    experiment_id = _create_spec(
        repository,
        project_id,
        status=ExperimentStatus.AWAITING_CODE_APPROVAL,
        code_extensions=[
            _make_extension(extension_id=ext_id_1, status="auto_tested"),
            _make_extension(extension_id=ext_id_2, status="auto_tested"),
        ],
    )

    # Approve first extension — spec should still be awaiting_code_approval
    response = client.post(
        _ext_url(project_id, experiment_id, ext_id_1) + "/approve",
        json={"reviewer": "bob", "notes": "First approved"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["spec_status"] == "awaiting_code_approval"

    # Approve second extension — spec should transition to confirmed
    response = client.post(
        _ext_url(project_id, experiment_id, ext_id_2) + "/approve",
        json={"reviewer": "bob", "notes": "Second approved"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["spec_status"] == "confirmed"


# ---------------------------------------------------------------------------
# Test 8: Approve wrong status fails
# ---------------------------------------------------------------------------


def test_approve_wrong_status_fails(client, repository, project_id):
    """POST /approve on a DRAFT extension returns 400."""
    ext_id = "ext-wrong-001"
    experiment_id = _create_spec(
        repository,
        project_id,
        code_extensions=[
            _make_extension(extension_id=ext_id, status="draft"),
        ],
    )

    response = client.post(
        _ext_url(project_id, experiment_id, ext_id) + "/approve",
        json={"reviewer": "alice", "notes": "try to approve"},
    )
    assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# Test 9: Get non-existent extension returns 404
# ---------------------------------------------------------------------------


def test_get_nonexistent_extension(client, repository, project_id):
    """GET /code-extensions/{extension_id} for a missing extension returns 404."""
    experiment_id = _create_spec(repository, project_id)

    response = client.get(
        _ext_url(project_id, experiment_id, "ext-does-not-exist")
    )
    assert response.status_code == 404, response.text
