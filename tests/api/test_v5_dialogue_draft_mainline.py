from __future__ import annotations

import shutil
import tempfile
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fluid_scientist.api import v5_router
from fluid_scientist.draft.models import DraftStatus, ExperimentDraft
from fluid_scientist.draft_session.models import DraftSessionStatus
from fluid_scientist.draft_session.persistence import JsonSessionPersistence
from fluid_scientist.draft_session.session_store import DraftSessionStore
from fluid_scientist.llm import LLMClient


class FakeLLM:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def call(self, **kwargs: Any) -> tuple[dict[str, Any], Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("model down")
        purpose = kwargs["purpose"]
        message = kwargs.get("user_message", "")
        if purpose == "input_routing":
            if "new research" in message.lower():
                return {"intent": "NEW_RESEARCH", "confidence": 0.96, "reason": "explicit new"}, None
            if "新建" in message:
                return {"intent": "NEW_RESEARCH", "confidence": 0.96, "reason": "explicit new"}, None
            return {"intent": "SUPPLEMENT_DRAFT", "confidence": 0.9, "reason": "active draft"}, None
        if purpose == "study_decomposition":
            return {
                "study": {
                    "study_type": "pipe",
                    "geometry": {"type": "pipe"},
                    "boundary_conditions": [{"type": "free_slip", "location": "top"}],
                    "missing_information": [],
                },
                "studies": [],
            }, None
        return {}, None


@pytest.fixture()
def client() -> TestClient:
    tmp_dir = tempfile.mkdtemp(prefix="v5_mainline_")
    persistence = JsonSessionPersistence(storage_dir=tmp_dir)
    store = DraftSessionStore(persistence=persistence)
    original = {
        "persistence": v5_router._session_persistence,
        "store": v5_router._session_store,
        "drafts": dict(v5_router._draft_store),
        "batches": dict(v5_router._batch_store),
        "proposals": dict(v5_router._proposal_store),
        "llm": v5_router._llm_client,
    }
    v5_router._session_persistence = persistence
    v5_router._session_store = store
    v5_router._draft_store.clear()
    v5_router._batch_store.clear()
    v5_router._proposal_store.clear()
    v5_router._llm_client = FakeLLM()
    app = FastAPI()
    app.include_router(v5_router.router)
    try:
        yield TestClient(app)
    finally:
        v5_router._session_persistence = original["persistence"]
        v5_router._session_store = original["store"]
        v5_router._draft_store.clear()
        v5_router._draft_store.update(original["drafts"])
        v5_router._batch_store.clear()
        v5_router._batch_store.update(original["batches"])
        v5_router._proposal_store.clear()
        v5_router._proposal_store.update(original["proposals"])
        v5_router._llm_client = original["llm"]
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _session(client: TestClient) -> str:
    response = client.post("/api/v5/sessions", json={"user_id": "u"})
    assert response.status_code == 201, response.text
    return response.json()["session"]["session_id"]


def _draft(session_id: str) -> ExperimentDraft:
    draft = ExperimentDraft(
        draft_id="draft_main",
        session_id=session_id,
        version=1,
        status=DraftStatus.DRAFT,
        objective="pipe flow",
        study_type="pipe",
        boundary_conditions={"wall": {"type": "no_slip"}},
        capability_preview={
            "fields": {
                "solver": {
                    "value_status": "MISSING_REQUIRED",
                    "capability_status": "SUPPORTED_NATIVE",
                    "display_value": "待选择",
                },
                "mesh": {
                    "value_status": "MISSING_REQUIRED",
                    "capability_status": "SUPPORTED_NATIVE",
                    "display_value": "待设计",
                },
                "requested_outputs": {
                    "value_status": "MISSING_REQUIRED",
                    "capability_status": "SUPPORTED_NATIVE",
                    "display_value": "待补充",
                },
            }
        },
    )
    v5_router._draft_store[draft.draft_id] = draft
    session = v5_router._session_store.get_session(session_id)
    assert session is not None
    session.current_draft_id = draft.draft_id
    session.current_draft_version = draft.version
    session.status = DraftSessionStatus.DRAFT_READY
    v5_router._session_store.update_session(session)
    return draft


def test_ambiguous_free_slip_supplement_asks_boundary_without_new_study(client: TestClient) -> None:
    session_id = _session(client)
    draft = _draft(session_id)
    response = client.post(
        f"/api/v5/sessions/{session_id}/messages",
        json={"session_id": session_id, "message": "边界条件是自由滑移"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["route"]["input_type"] == "draft_change_request"
    assert body["route"]["intent"] == "SUPPLEMENT_DRAFT"
    assert body["actions"][0]["action"] == "clarification_required"
    assert "哪个边界" in body["actions"][0]["message"]
    session = v5_router._session_store.get_session(session_id)
    assert session is not None
    assert session.current_draft_id == draft.draft_id
    assert session.selected_study_id is None
    assert v5_router._draft_store[draft.draft_id].version == 1


def test_real_chinese_free_slip_page_flow_asks_boundary_without_new_study(client: TestClient) -> None:
    session_id = _session(client)
    draft = _draft(session_id)
    response = client.post(
        f"/api/v5/sessions/{session_id}/messages",
        json={"session_id": session_id, "message": "边界条件是自由滑移"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["route"]["input_type"] == "draft_change_request"
    assert body["route"]["intent"] == "SUPPLEMENT_DRAFT"
    assert body["actions"][0]["action"] == "clarification_required"
    assert body["actions"][0]["message"] == "哪个边界需要设为自由滑移？"
    session = v5_router._session_store.get_session(session_id)
    assert session is not None
    assert session.current_draft_id == draft.draft_id
    assert session.selected_study_id is None


def test_specific_top_boundary_generates_proposal_then_confirm_applies(client: TestClient) -> None:
    session_id = _session(client)
    _draft(session_id)
    response = client.post(
        f"/api/v5/sessions/{session_id}/messages",
        json={"session_id": session_id, "message": "把上边界设为自由滑移"},
    )
    assert response.status_code == 200, response.text
    proposal = response.json()["actions"][0]["proposal"]
    assert proposal["status"] == "pending"
    session = v5_router._session_store.get_session(session_id)
    assert session is not None
    assert session.pending_proposal_id == proposal["proposal_id"]

    response = client.post(
        f"/api/v5/sessions/{session_id}/messages",
        json={"session_id": session_id, "message": "确认"},
    )
    assert response.status_code == 200, response.text
    updated = response.json()["actions"][0]["draft"]
    assert updated["draft_id"] != "draft_main"
    assert updated["boundary_conditions"]["top"]["type"] == "free_slip"
    assert updated["capability_preview"]["fields"]["boundary_conditions.top"]["capability_status"] == "SUPPORTED_NATIVE"


def test_free_slip_question_only_answers(client: TestClient) -> None:
    session_id = _session(client)
    _draft(session_id)
    response = client.post(
        f"/api/v5/sessions/{session_id}/messages",
        json={"session_id": session_id, "message": "为什么这里使用自由滑移边界"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["actions"][0]["action"] == "answer"
    assert not v5_router._proposal_store


def test_explicit_new_free_slip_pipe_creates_new_study(client: TestClient) -> None:
    session_id = _session(client)
    _draft(session_id)
    response = client.post(
        f"/api/v5/sessions/{session_id}/messages",
        json={"session_id": session_id, "message": "新建一个上边界为自由滑移的管流研究"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["route"]["intent"] == "NEW_RESEARCH"
    assert body["actions"][0]["action"] == "study_decomposed"


def test_configured_glm_records_real_model() -> None:
    fake_client = object()
    llm = v5_router.configure_llm_client(
        provider="glm",
        model="glm-4-flash",
        api_key="k",
        client=fake_client,
    )
    assert llm._provider == "glm"
    assert llm._model_name == "glm-4-flash"
    assert llm._provider != "mock"


def test_model_failure_leaves_session_state_unchanged(client: TestClient) -> None:
    session_id = _session(client)
    _draft(session_id)
    v5_router._llm_client = FakeLLM(fail=True)
    before = v5_router._session_store.get_session(session_id)
    assert before is not None
    response = client.post(
        f"/api/v5/sessions/{session_id}/messages",
        json={"session_id": session_id, "message": "边界条件是自由滑移"},
    )
    assert response.status_code == 502
    after = v5_router._session_store.get_session(session_id)
    assert after is not None
    assert after.status == before.status
    assert after.current_draft_id == before.current_draft_id
    assert after.pending_proposal_id == before.pending_proposal_id


def test_empty_fields_are_pending_not_missing_capabilities(client: TestClient) -> None:
    session_id = _session(client)
    draft = _draft(session_id)
    fields = draft.capability_preview["fields"]
    assert fields["solver"]["display_value"] == "待选择"
    assert fields["solver"]["capability_status"] == "SUPPORTED_NATIVE"
    assert fields["mesh"]["display_value"] == "待设计"
    assert fields["requested_outputs"]["display_value"] == "待补充"


def test_api_new_research_runs_complete_design_before_capability_check(client: TestClient) -> None:
    session_id = _session(client)
    response = client.post(
        f"/api/v5/sessions/{session_id}/messages",
        json={
            "session_id": session_id,
            "message": "new research pipe flow Re=3900 with top free slip",
        },
    )
    assert response.status_code == 200, response.text
    action = response.json()["actions"][0]
    assert action["action"] == "study_decomposed"
    study = action["study"]
    assert study["experiment_design"]["solver"]["name"] == "pimpleFoam"
    assert study["scientific_metrics"]
    assert study["boundary_verification_metrics"]
    assert action["capability_check"]["readiness_level"] in {
        "draftable",
        "needs_clarification",
        "not_compilable_yet",
    }


def test_generate_draft_api_materializes_complete_design(client: TestClient) -> None:
    session_id = _session(client)
    study, _check = v5_router._decompose_single_study(
        "new research pipe flow Re=3900 with top free slip",
        session_id=session_id,
    )
    response = client.post(
        "/api/v5/drafts/generate",
        json={"session_id": session_id, "study": study.model_dump(mode="json")},
    )

    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["solver"]["name"] == "pimpleFoam"
    assert draft["mesh"]["strategy"]
    assert draft["measurement_plan"]["boundary_verification_metrics"]
    assert draft["capability_preview"]["fields"]["solver"]["capability_status"] == "SUPPORTED_NATIVE"


def test_restart_restores_complete_draft_pointer(client: TestClient) -> None:
    session_id = _session(client)
    study, _check = v5_router._decompose_single_study(
        "new research pipe flow Re=3900 with top free slip",
        session_id=session_id,
    )
    response = client.post(
        "/api/v5/drafts/generate",
        json={"session_id": session_id, "study": study.model_dump(mode="json")},
    )
    assert response.status_code == 200, response.text
    draft = response.json()

    restored_store = DraftSessionStore(persistence=v5_router._session_persistence)
    restored = restored_store.get_session(session_id)
    assert restored is not None
    assert restored.current_draft_id == draft["draft_id"]
    assert restored.current_draft_version == 1


def test_llm_client_failure_records_error_without_mock() -> None:
    class BrokenCompletions:
        def create(self, **_: Any) -> Any:
            raise RuntimeError("boom")

    class BrokenClient:
        chat = type("Chat", (), {"completions": BrokenCompletions()})()

    llm = LLMClient(provider="glm", model_name="glm-4-flash", api_key="k", client=BrokenClient())
    with pytest.raises(RuntimeError):
        llm.call(
            purpose="input_routing",
            prompt_name="p",
            system_prompt="s",
            user_message="u",
            session_id="s1",
        )
    record = llm.get_last_record("s1")
    assert record is not None
    assert record.provider == "glm"
    assert record.model_name == "glm-4-flash"
    assert record.success is False
    assert record.fallback_used is False
    assert record.error
    assert record.latency_ms is not None
