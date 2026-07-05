"""API integration tests for candidate template validation and pilot run."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.candidate_templates.models import CandidateState
from fluid_scientist.ports import StoredCandidateTemplate
from tests.adapters.test_sql_repository import (
    seed_generated_draft,
)


def _make_app(tmp_path) -> tuple[TestClient, SQLWorkflowRepository]:
    repo = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(repository=repo, execution_targets=())
    return TestClient(app), repo


def _seed_full_candidate(repo: SQLWorkflowRepository) -> StoredCandidateTemplate:
    """Store a draft and create a DRAFT candidate from it."""
    draft = seed_generated_draft(repo)
    repo.store_generated_case_draft(draft)
    now = datetime.now(UTC).isoformat()
    template = StoredCandidateTemplate(
        candidate_id="cand-api-1",
        draft_id=draft.draft_id,
        project_id=draft.project_id,
        plan_id=draft.plan_id,
        plan_version=draft.plan_version,
        draft_version=draft.version,
        archive_sha256=draft.archive_sha256,
        state=CandidateState.DRAFT.value,
        rejection_reason=None,
        created_at=now,
        updated_at=now,
    )
    repo.save_candidate_template(template)
    return template


class TestCandidateApiLifecycle:
    def test_create_candidate(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        draft = seed_generated_draft(repo)
        repo.store_generated_case_draft(draft)

        response = client.post(
            "/api/projects/project-1/candidates",
            json={"draft_id": "draft-1"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["state"] == "draft"
        assert body["draft_id"] == "draft-1"
        assert body["project_id"] == "project-1"

    def test_create_candidate_nonexistent_draft(self, tmp_path) -> None:
        client, _ = _make_app(tmp_path)
        response = client.post(
            "/api/projects/project-1/candidates",
            json={"draft_id": "nonexistent"},
        )
        assert response.status_code == 404

    def test_create_candidate_wrong_project(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        draft = seed_generated_draft(repo)
        repo.store_generated_case_draft(draft)

        response = client.post(
            "/api/projects/wrong-project/candidates",
            json={"draft_id": "draft-1"},
        )
        assert response.status_code == 400

    def test_list_candidates(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        response = client.get("/api/projects/project-1/candidates")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["candidate_id"] == "cand-api-1"

    def test_list_candidates_filtered_by_state(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        response = client.get(
            "/api/projects/project-1/candidates?state=draft"
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

        response = client.get(
            "/api/projects/project-1/candidates?state=published"
        )
        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_get_candidate(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        response = client.get(
            "/api/projects/project-1/candidates/cand-api-1"
        )
        assert response.status_code == 200
        assert response.json()["candidate_id"] == "cand-api-1"

    def test_get_candidate_not_found(self, tmp_path) -> None:
        client, _ = _make_app(tmp_path)
        response = client.get(
            "/api/projects/project-1/candidates/nonexistent"
        )
        assert response.status_code == 404

    def test_validate_static_advances_to_static_validated(
        self, tmp_path
    ) -> None:
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        response = client.post(
            "/api/projects/project-1/candidates/cand-api-1/validate-static"
        )
        assert response.status_code == 200
        assert response.json()["state"] == "static_validated"

    def test_reject_candidate(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        response = client.post(
            "/api/projects/project-1/candidates/cand-api-1/reject",
            json={"reason": "Safety concerns in solver configuration"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "rejected"
        assert "Safety concerns" in body["rejection_reason"]

    def test_reject_published_not_allowed(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        # Manually advance to PUBLISHED via repository
        for state in [
            CandidateState.STATIC_VALIDATED,
            CandidateState.PILOT_PASSED,
            CandidateState.CANDIDATE_APPROVED,
            CandidateState.REGRESSION_PASSED,
            CandidateState.PUBLISHED,
        ]:
            repo.update_candidate_template_state(
                "cand-api-1",
                new_state=state.value,
                rejection_reason=None,
                updated_at=datetime.now(UTC).isoformat(),
            )

        response = client.post(
            "/api/projects/project-1/candidates/cand-api-1/reject",
            json={"reason": "Too late"},
        )
        assert response.status_code == 422

    def test_full_lifecycle_transitions(self, tmp_path) -> None:
        """Test the full DRAFT -> PUBLISHED lifecycle via API."""
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        # DRAFT -> STATIC_VALIDATED
        r = client.post(
            "/api/projects/project-1/candidates/cand-api-1/validate-static"
        )
        assert r.status_code == 200
        assert r.json()["state"] == "static_validated"

        # STATIC_VALIDATED -> PILOT_PASSED (skip actual pilot, use repo)
        repo.update_candidate_template_state(
            "cand-api-1",
            new_state=CandidateState.PILOT_PASSED.value,
            rejection_reason=None,
            updated_at=datetime.now(UTC).isoformat(),
        )

        # PILOT_PASSED -> CANDIDATE_APPROVED
        r = client.post(
            "/api/projects/project-1/candidates/cand-api-1/approve",
            json={},
        )
        assert r.status_code == 200
        assert r.json()["state"] == "candidate_approved"

        # CANDIDATE_APPROVED -> REGRESSION_PASSED (via repo)
        repo.update_candidate_template_state(
            "cand-api-1",
            new_state=CandidateState.REGRESSION_PASSED.value,
            rejection_reason=None,
            updated_at=datetime.now(UTC).isoformat(),
        )

        # REGRESSION_PASSED -> PUBLISHED
        r = client.post(
            "/api/projects/project-1/candidates/cand-api-1/publish"
        )
        assert r.status_code == 200
        assert r.json()["state"] == "published"

    def test_cannot_skip_states(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        # Try to publish directly from DRAFT
        response = client.post(
            "/api/projects/project-1/candidates/cand-api-1/publish"
        )
        assert response.status_code == 422

    def test_cannot_go_backwards(self, tmp_path) -> None:
        client, repo = _make_app(tmp_path)
        _seed_full_candidate(repo)

        # Advance to STATIC_VALIDATED
        repo.update_candidate_template_state(
            "cand-api-1",
            new_state=CandidateState.STATIC_VALIDATED.value,
            rejection_reason=None,
            updated_at=datetime.now(UTC).isoformat(),
        )

        # Try to validate-static again (STATIC_VALIDATED -> STATIC_VALIDATED)
        response = client.post(
            "/api/projects/project-1/candidates/cand-api-1/validate-static"
        )
        assert response.status_code == 422
