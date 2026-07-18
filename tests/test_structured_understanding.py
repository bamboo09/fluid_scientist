from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from fluid_scientist.llm.structured_understanding import (
    ModelNativeUnderstandingService,
    ModelUnderstandingError,
    StructuredUnderstanding,
    UnderstandingContext,
)


MESSAGE = "把仿真时间设为20s，保留已经确认的圆柱半径。"


def _context() -> UnderstandingContext:
    return UnderstandingContext(
        user_message=MESSAGE,
        current_spec={"spec_id": "study_1", "version": 3, "numerics": {"time": {"end_time": 10}}},
        conversation_history=[{"role": "user", "content": "研究二维圆柱绕流"}],
        confirmed_facts=[{"key": "geometry.cylinder.radius", "value": {"value": 0.1, "unit": "m"}, "confirmed": True}],
        unresolved_conflicts=[{"description": "上边界语义待确认"}],
        workflow_skills=[{"skill_id": "research_session", "content": "Preserve confirmed facts."}],
        professional_skills=[{"skill_id": "cfd_spec", "content": "Use SI units."}],
        references=[{"reference_id": "openfoam-v2312", "content": "OpenFOAM v2312 reference"}],
    )


def _model_output(context: UnderstandingContext) -> dict:
    return {
        "summary": "只修改结束时间并保留已确认事实",
        "facts": [
            {
                "fact_id": "f_end_time",
                "path": "/numerics/time/end_time",
                "value": 20.0,
                "unit": "s",
                "origin": "USER_EXPLICIT",
                "evidence": [{"quote": "仿真时间设为20s", "source": "current_message", "source_id": "turn_current"}],
                "confidence": 1.0,
            }
        ],
        "entities": [],
        "relations": [],
        "ambiguities": [],
        "conflicts": [],
        "capability_requirements": [],
        "evidence_quotes": [{"quote": "仿真时间设为20s", "source": "current_message", "source_id": "turn_current"}],
        "proposed_patch": {
            "patch_id": "patch_model_1",
            "session_id": "session_1",
            "base_spec_id": "study_1",
            "base_version": 3,
            "intent": "modify_existing_spec",
            "operations": [{
                "op": "replace",
                "path": "/numerics/time/end_time",
                "value": 20.0,
                "source_quote": "仿真时间设为20s",
                "confidence": 1.0,
            }],
            "untouched_guarantee": True,
            "assistant_message": "仿真结束时间将由10s改为20s。",
        },
    }


def test_model_native_service_returns_understanding_patch_and_source_chain() -> None:
    context = _context()
    captured: dict = {}

    def semantic_model(prompt: str, schema: dict) -> dict:
        captured["prompt"] = prompt
        captured["schema"] = schema
        return _model_output(context)

    understanding, validation = ModelNativeUnderstandingService(semantic_model).understand(context)

    assert isinstance(understanding, StructuredUnderstanding)
    assert understanding.proposed_patch.operations[0].path == "/numerics/time/end_time"
    assert validation.valid
    assert validation.field_source_chain == [{
        "path": "/numerics/time/end_time",
        "origin": "USER_EXPLICIT",
        "value": 20.0,
        "unit": "s",
        "evidence_quotes": ["仿真时间设为20s"],
        "derivation": None,
        "confidence": 1.0,
    }]
    for required_fragment in (
        "current_spec", "conversation_history", "confirmed_facts",
        "workflow_skills", "professional_skills", "references", "output_schema",
    ):
        assert required_fragment in captured["prompt"]
    assert captured["schema"]["title"] == "StructuredUnderstanding"


def test_llm_disabled_fails_instead_of_using_regex_or_template() -> None:
    with pytest.raises(ModelUnderstandingError, match="LLM_DISABLED"):
        ModelNativeUnderstandingService(None).understand(_context())


@pytest.mark.parametrize(
    ("removed_field", "expected_loss"),
    [
        ("current_spec", "cannot target base spec/version"),
        ("conversation_history", "cannot preserve conversational objective"),
        ("confirmed_facts", "cannot preserve confirmed radius"),
        ("workflow_skills", "cannot follow session workflow"),
        ("professional_skills", "cannot apply CFD semantics"),
        ("references", "cannot ground OpenFOAM capability"),
    ],
)
def test_context_removal_reduces_model_ability(removed_field: str, expected_loss: str) -> None:
    context = _context()
    ablated = context.model_copy(deep=True)
    setattr(ablated, removed_field, None if removed_field == "current_spec" else [])

    def context_sensitive_model(prompt: str, schema: dict) -> dict:
        del schema
        if f'"{removed_field}": []' in prompt or f'"{removed_field}": null' in prompt:
            raise RuntimeError(expected_loss)
        return _model_output(context)

    full, _ = ModelNativeUnderstandingService(context_sensitive_model).understand(context)
    assert full.proposed_patch.base_version == 3
    with pytest.raises(RuntimeError, match=expected_loss):
        ModelNativeUnderstandingService(context_sensitive_model).understand(ablated)


def test_evidence_validator_rejects_hallucinated_numeric_value() -> None:
    context = _context()
    output = copy.deepcopy(_model_output(context))
    output["facts"][0]["value"] = 200.0

    with pytest.raises(ModelUnderstandingError, match="explicit numeric value is absent"):
        ModelNativeUnderstandingService(lambda prompt, schema: output).understand(context)


def test_evidence_validator_rejects_patch_source_quote_not_in_context() -> None:
    context = _context()
    output = copy.deepcopy(_model_output(context))
    output["proposed_patch"]["operations"][0]["source_quote"] = "模型自行猜测"

    with pytest.raises(ModelUnderstandingError, match="source_quote is absent"):
        ModelNativeUnderstandingService(lambda prompt, schema: output).understand(context)


def test_model_editing_primary_path_records_full_prompt_trace(monkeypatch) -> None:
    from fluid_scientist.api import model_editing_router as router
    from fluid_scientist.draft_session.models import LLMCallRecord
    from fluid_scientist.llm.prompt_trace import get_prompt_trace_recorder

    session = router._session_manager.create_session("structured-test")
    context = _context()
    output = _model_output(context)
    output["proposed_patch"]["session_id"] = session.session_id

    class FakeClient:
        _provider = "fake-semantic-provider"
        _model_name = "fake-semantic-model"

        def call(self, **kwargs):
            assert kwargs["purpose"] == "structured_understanding"
            assert kwargs["output_schema"]["title"] == "StructuredUnderstanding"
            return output, LLMCallRecord(
                call_id="call_structured_1",
                session_id=session.session_id,
                purpose="structured_understanding",
                provider=self._provider,
                model_name=self._model_name,
                prompt_name="model_native_spec_understanding",
                prompt_version="structured-understanding-v1",
                input_summary="complete context",
                raw_output="{}",
                parsed_output=output,
                latency_ms=12,
                success=True,
                fallback_used=False,
            )

    recorder = get_prompt_trace_recorder()
    recorder.clear()
    model_context = SimpleNamespace(
        recent_conversation=context.conversation_history,
        confirmed_facts=context.confirmed_facts,
        unresolved_conflicts=context.unresolved_conflicts,
        system_role="preserve confirmed facts and emit patches",
        references=context.references,
    )
    monkeypatch.setattr(router, "_record_trace", lambda *args, **kwargs: "trace_model_1")

    patch, trace_id, errors = router._run_model_native_understanding(
        session_id=session.session_id,
        user_message=MESSAGE,
        current_spec=None,
        model_context=model_context,
        resolved_skills=["fluid.intent_to_spec"],
        llm_client=FakeClient(),
    )

    assert not errors
    assert patch is not None
    assert trace_id == "trace_model_1"
    trace = recorder.get_last_trace(session.session_id)
    assert trace is not None
    assert trace.result.parsed_successfully
    assert trace.context.conversation_history
    assert trace.context.confirmed_facts
    assert trace.context.skill_prompt_fragments
    assert trace.context.reference_documents
    assert trace.context.output_schema["title"] == "StructuredUnderstanding"
    assert trace.field_provenance[0]["path"] == "/numerics/time/end_time"
