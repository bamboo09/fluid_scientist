"""Tests for the LLMClient wiring in the v5 code-extension endpoints.

These tests verify that the ``generate`` and ``review`` endpoints in the
v5 router actually invoke :class:`LLMClient` (instead of producing
placeholder strings) and that the spec gets updated with whatever the
LLM returned.  They also confirm that LLM failures are absorbed and do
not break the HTTP endpoint.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fluid_scientist.api import v5_router
from fluid_scientist.code_extension.spec import (
    CodeExtensionSpec,
    CodeExtensionWorkflow,
)
from fluid_scientist.llm import LLMClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client_for_router() -> TestClient:
    """Build a TestClient that exercises the v5 router in isolation."""
    app = FastAPI()
    app.include_router(v5_router.router)
    return TestClient(app)


def _make_spec_in_reviewed_state(
    extension_id: str = "ext-llm-001",
    session_id: str = "session-llm-001",
) -> CodeExtensionSpec:
    """Create a :class:`CodeExtensionSpec` in ``spec_reviewed`` status.

    This is the prerequisite state for the ``/generate`` endpoint.  The
    function uses the same workflow class that the router uses so the
    state machine stays consistent.
    """
    wf = CodeExtensionWorkflow()
    missing_capability = {
        "capability_id": "cap_metric_custom",
        "capability_type": "metric_operator",
        "requested_behavior": "Calculate custom Reynolds stress metric",
        "reason": "Metric not in registry",
        "required_inputs": ["velocity_field"],
        "expected_outputs": ["reynolds_stress"],
        "suggested_extension_type": "metric_operator",
    }
    spec = wf.create_spec(missing_capability, session_id=session_id)
    # Force the IDs to be deterministic for the test.
    spec = spec.model_copy(
        update={"extension_id": extension_id}
    )
    spec = wf.review_spec(spec, notes="ready for generation")
    assert spec.status == "spec_reviewed"
    return spec


def _make_spec_in_tested_state(
    extension_id: str = "ext-llm-002",
    session_id: str = "session-llm-002",
) -> CodeExtensionSpec:
    """Create a :class:`CodeExtensionSpec` in ``tested`` status.

    Required for the ``/review`` endpoint.  The function uses the same
    workflow class the router uses so state transitions remain valid.
    """
    wf = CodeExtensionWorkflow()
    spec = _make_spec_in_reviewed_state(extension_id=extension_id, session_id=session_id)
    spec = wf.submit_for_generation(spec)
    spec = wf.submit_code(spec, "def run(*args, **kwargs):\n    return 42\n")
    spec = wf.run_tests(spec)
    assert spec.status == "tested"
    return spec


@pytest.fixture
def isolated_state(monkeypatch: pytest.MonkeyPatch):
    """Snapshot the module-level state we mutate and restore it on teardown.

    Yields a namespace the tests can use to access the working copies of
    the router's LLM client.
    """
    # Use isolated copies of mutable module-level state.
    original_llm = v5_router._llm_client

    fresh_llm = LLMClient()
    monkeypatch.setattr(v5_router, "_llm_client", fresh_llm)
    v5_router._reset_repo_for_testing()

    try:
        yield {"llm": fresh_llm}
    finally:
        v5_router._reset_repo_for_testing()
        v5_router._llm_client = original_llm


# ---------------------------------------------------------------------------
# generate endpoint
# ---------------------------------------------------------------------------


class TestGenerateCodeExtensionLLM:
    """Verify that ``POST /code-extensions/{id}/generate`` calls the LLM."""

    def test_generate_calls_llm_with_code_generation_purpose(
        self, isolated_state
    ) -> None:
        spec = _make_spec_in_reviewed_state()
        v5_router._repo.save_extension(spec)
        llm = isolated_state["llm"]

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/generate"
        )

        assert response.status_code == 200
        records = llm.get_records(session_id=spec.session_id)
        assert len(records) == 1
        assert records[0].purpose == "code_generation"
        assert records[0].prompt_name == "code_extension_generate"
        assert "Extension type" in records[0].input_summary
        assert "Requirement" in records[0].input_summary

    def test_generate_uses_spec_session_id_when_request_has_none(
        self, isolated_state
    ) -> None:
        """When the request omits ``session_id``, the spec's session_id wins."""
        spec = _make_spec_in_reviewed_state(
            extension_id="ext-no-sess",
            session_id="sess-from-spec",
        )
        v5_router._repo.save_extension(spec)
        llm = isolated_state["llm"]

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/generate"
        )

        assert response.status_code == 200
        records = llm.get_records(session_id="sess-from-spec")
        assert len(records) == 1
        assert records[0].purpose == "code_generation"

    def test_generate_returns_spec_with_status_generated(
        self, isolated_state
    ) -> None:
        spec = _make_spec_in_reviewed_state()
        v5_router._repo.save_extension(spec)

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/generate"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "generated"
        assert body["generated_code"]  # must not be empty
        assert spec.extension_id == body["extension_id"]


# ---------------------------------------------------------------------------
# review endpoint
# ---------------------------------------------------------------------------


class TestReviewCodeExtensionLLM:
    """Verify that ``POST /code-extensions/{id}/review`` calls the LLM."""

    def test_review_calls_llm_with_code_review_purpose(
        self, isolated_state
    ) -> None:
        spec = _make_spec_in_tested_state()
        v5_router._repo.save_extension(spec)
        llm = isolated_state["llm"]

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/review",
            json={"approved": True, "review_notes": "looks good"},
        )

        assert response.status_code == 200
        records = llm.get_records(session_id=spec.session_id)
        assert len(records) == 1
        assert records[0].purpose == "code_review"
        assert records[0].prompt_name == "code_extension_review"
        assert spec.extension_id in records[0].input_summary

    def test_review_approval_keeps_status_approved(
        self, isolated_state
    ) -> None:
        spec = _make_spec_in_tested_state()
        v5_router._repo.save_extension(spec)

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/review",
            json={"approved": True, "review_notes": "ship it"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert "ship it" in body["review_notes"]

    def test_review_rejection_keeps_status_rejected(
        self, isolated_state
    ) -> None:
        spec = _make_spec_in_tested_state()
        v5_router._repo.save_extension(spec)

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/review",
            json={"approved": False, "review_notes": "broken"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert "broken" in body["review_notes"]


# ---------------------------------------------------------------------------
# LLM-failure resilience
# ---------------------------------------------------------------------------


class TestLLMFailureResilience:
    """The HTTP endpoints must keep working when the LLM is unavailable."""

    def test_generate_keeps_working_when_llm_raises(
        self, isolated_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = _make_spec_in_reviewed_state()
        v5_router._repo.save_extension(spec)

        # Force the LLM client to raise on every call.  The endpoint
        # must catch the exception and fall back to the placeholder
        # string so the workflow can still complete.
        class _BoomClient:
            def call(self, *args, **kwargs):  # noqa: D401 - test double
                raise RuntimeError("LLM provider unavailable")

            def get_records(self, session_id: str | None = None):  # noqa: D401
                return []

            def get_last_record(self, session_id: str | None = None):  # noqa: D401
                return None

        monkeypatch.setattr(v5_router, "_llm_client", _BoomClient())

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/generate"
        )

        # Endpoint must succeed despite the LLM blowing up.
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "generated"
        # Placeholder text must still be present so downstream stages
        # have something to work with.
        assert "Auto-generated code extension" in body["generated_code"]

    def test_review_keeps_working_when_llm_raises(
        self, isolated_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = _make_spec_in_tested_state()
        v5_router._repo.save_extension(spec)

        class _BoomClient:
            def call(self, *args, **kwargs):
                raise RuntimeError("LLM provider unavailable")

            def get_records(self, session_id: str | None = None):
                return []

            def get_last_record(self, session_id: str | None = None):
                return None

        monkeypatch.setattr(v5_router, "_llm_client", _BoomClient())

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/review",
            json={"approved": True, "review_notes": "human only"},
        )

        # Endpoint must succeed despite the LLM blowing up.
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert "human only" in body["review_notes"]


# ---------------------------------------------------------------------------
# Spec update with LLM-produced code
# ---------------------------------------------------------------------------


class TestSpecUpdatedWithLLMCode:
    """Verify that the spec is updated with LLM-produced content."""

    def test_spec_uses_llm_generated_code_when_provided(
        self, isolated_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = _make_spec_in_reviewed_state()
        v5_router._repo.save_extension(spec)

        # Build a fake LLM that pretends to produce real code.
        produced_code = "def run(*args, **kwargs):\n    return 'real-llm-code'\n"
        produced_notes = "Generated by the LLM under test"

        class _ProducingLLM(LLMClient):
            def call(  # type: ignore[override]
                self,
                purpose: str,
                prompt_name: str,
                system_prompt: str,
                user_message: str,
                output_schema: dict | None = None,
                session_id: str = "",
                input_refs: list[str] | None = None,
                prompt_version: str = "",
            ) -> tuple[dict, object]:
                # Record the call the same way the real client would.
                from fluid_scientist.draft_session.models import LLMCallRecord

                record = LLMCallRecord(
                    call_id="llm_test_call",
                    session_id=session_id,
                    purpose=purpose,  # type: ignore[arg-type]
                    provider="mock",
                    model_name="mock-v1",
                    prompt_name=prompt_name,
                    prompt_version="test-1",
                    input_refs=list(input_refs) if input_refs else [],
                    input_summary=user_message[:200],
                    output_schema=str(output_schema) if output_schema else "",
                    raw_output=str(
                        {"code": produced_code, "notes": produced_notes}
                    ),
                    parsed_output={
                        "code": produced_code,
                        "notes": produced_notes,
                    },
                    success=True,
                    fallback_used=True,
                    fallback_reason="test fake",
                    error=None,
                )
                self._records.append(record)
                return {"code": produced_code, "notes": produced_notes}, record

        fake_llm = _ProducingLLM()
        monkeypatch.setattr(v5_router, "_llm_client", fake_llm)

        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/generate"
        )

        assert response.status_code == 200
        body = response.json()
        # The spec's generated_code must reflect the LLM output, not the
        # default placeholder.
        assert body["generated_code"] == produced_code
        assert "Auto-generated code extension" not in body["generated_code"]
        # review_notes should also pick up the LLM-provided notes.
        assert body["review_notes"] == produced_notes

        # And the in-memory store must be updated with the same spec.
        stored = v5_router._repo.get_extension(spec.extension_id)
        assert stored.generated_code == produced_code
        assert stored.review_notes == produced_notes

    def test_spec_falls_back_to_placeholder_when_llm_returns_no_code(
        self, isolated_state
    ) -> None:
        """When the LLM returns an empty ``code`` field, the placeholder wins."""
        spec = _make_spec_in_reviewed_state()
        v5_router._repo.save_extension(spec)

        # The default LLMClient is the mock and returns a generic
        # fallback dict with no ``code``/``generated_code`` keys, so
        # the placeholder must be used.
        client = _build_client_for_router()
        response = client.post(
            f"/api/v5/code-extensions/{spec.extension_id}/generate"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["generated_code"]
        assert "Auto-generated code extension" in body["generated_code"]
