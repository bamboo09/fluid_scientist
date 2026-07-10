"""Tests for llm.client.LLMClient."""

from __future__ import annotations

from fluid_scientist.llm import LLMClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call(
    client: LLMClient,
    purpose: str = "study_decomposition",
    session_id: str = "sess-1",
    user_message: str = "研究圆柱绕流",
) -> tuple[dict, object]:
    return client.call(
        purpose=purpose,
        prompt_name=f"{purpose}_prompt",
        system_prompt=f"System prompt for {purpose}",
        user_message=user_message,
        session_id=session_id,
        input_refs=["ref-1"],
    )


# ---------------------------------------------------------------------------
# Call recording
# ---------------------------------------------------------------------------


class TestCallRecording:
    def test_call_returns_dict_and_record(self) -> None:
        client = LLMClient()
        result, record = _call(client)
        assert isinstance(result, dict)
        assert record.purpose == "study_decomposition"
        assert record.session_id == "sess-1"
        assert record.provider == "mock"
        assert record.model_name == "mock-v1"
        assert record.fallback_used is True
        assert record.success is True
        assert record.call_id.startswith("llm_")

    def test_every_call_is_recorded(self) -> None:
        client = LLMClient()
        _call(client, purpose="study_decomposition")
        _call(client, purpose="draft_generation")
        _call(client, purpose="explanation")
        records = client.get_records()
        assert len(records) == 3

    def test_get_records_filters_by_session(self) -> None:
        client = LLMClient()
        _call(client, purpose="study_decomposition", session_id="sess-a")
        _call(client, purpose="draft_generation", session_id="sess-b")
        _call(client, purpose="explanation", session_id="sess-a")

        a_records = client.get_records("sess-a")
        b_records = client.get_records("sess-b")
        assert len(a_records) == 2
        assert len(b_records) == 1

    def test_get_last_record(self) -> None:
        client = LLMClient()
        assert client.get_last_record() is None
        _call(client, purpose="study_decomposition", session_id="sess-1")
        _call(client, purpose="draft_generation", session_id="sess-1")
        last = client.get_last_record()
        assert last is not None
        assert last.purpose == "draft_generation"

    def test_get_last_record_filtered_by_session(self) -> None:
        client = LLMClient()
        _call(client, purpose="study_decomposition", session_id="sess-a")
        _call(client, purpose="draft_generation", session_id="sess-b")
        last_a = client.get_last_record("sess-a")
        last_b = client.get_last_record("sess-b")
        assert last_a is not None
        assert last_b is not None
        assert last_a.purpose == "study_decomposition"
        assert last_b.purpose == "draft_generation"

    def test_input_refs_are_stored(self) -> None:
        client = LLMClient()
        _, record = client.call(
            purpose="study_decomposition",
            prompt_name="test",
            system_prompt="sys",
            user_message="msg",
            input_refs=["ref-1", "ref-2"],
            session_id="sess-1",
        )
        assert record.input_refs == ["ref-1", "ref-2"]

    def test_input_summary_truncated(self) -> None:
        client = LLMClient()
        long_msg = "x" * 500
        _, record = _call(client, user_message=long_msg)
        assert len(record.input_summary) <= 200

    def test_unknown_purpose_is_coerced(self) -> None:
        """Unknown purpose strings should not crash; they get coerced to a valid Literal."""
        client = LLMClient()
        result, record = client.call(
            purpose="completely_unknown_purpose",
            prompt_name="test",
            system_prompt="sys",
            user_message="hello",
            session_id="sess-1",
        )
        # The record should validate successfully
        assert record.purpose == "explanation"
        assert record.fallback_used is True
        # The original purpose must be preserved in original_purpose (not in fallback_reason)
        assert record.original_purpose == "completely_unknown_purpose"
        # fallback_reason should be clean (no original_purpose hack)
        assert record.fallback_reason is not None
        assert "original_purpose" not in record.fallback_reason

    def test_known_purpose_has_no_original_purpose(self) -> None:
        """Known purposes should leave original_purpose as None."""
        client = LLMClient()
        _result, record = client.call(
            purpose="study_decomposition",
            prompt_name="test",
            system_prompt="sys",
            user_message="hello",
            session_id="sess-1",
        )
        assert record.purpose == "study_decomposition"
        assert record.original_purpose is None


# ---------------------------------------------------------------------------
# Mock / fallback behaviour
# ---------------------------------------------------------------------------


class TestMockResponses:
    def test_study_decomposition_returns_studies(self) -> None:
        client = LLMClient()
        result, record = _call(client, purpose="study_decomposition")
        assert result["status"] == "decomposed"
        assert "studies" in result
        assert len(result["studies"]) >= 1
        assert result["fallback_used"] is True
        assert record.fallback_used is True
        assert record.fallback_reason is not None

    def test_draft_generation_returns_draft(self) -> None:
        client = LLMClient()
        result, _ = _call(client, purpose="draft_generation")
        assert result["status"] == "draft_generated"
        assert "draft" in result
        assert "sections" in result["draft"]
        assert result["fallback_used"] is True

    def test_draft_change_proposal_returns_proposal(self) -> None:
        client = LLMClient()
        result, _ = _call(client, purpose="draft_change_proposal")
        assert result["status"] == "proposal_generated"
        assert "proposal" in result
        assert result["fallback_used"] is True

    def test_code_extension_spec_returns_spec(self) -> None:
        client = LLMClient()
        result, _ = _call(client, purpose="code_extension_spec")
        assert result["status"] == "spec_generated"
        assert "spec" in result
        assert result["fallback_used"] is True

    def test_unknown_purpose_returns_fallback(self) -> None:
        client = LLMClient()
        result, record = _call(client, purpose="completely_new_purpose")
        assert result["status"] == "fallback"
        assert "LLM not configured" in result["message"]
        assert result["fallback_used"] is True
        assert record.fallback_used is True

    def test_output_schema_recorded(self) -> None:
        client = LLMClient()
        schema = {"type": "object", "properties": {"foo": {"type": "string"}}}
        _, record = client.call(
            purpose="study_decomposition",
            prompt_name="test",
            system_prompt="sys",
            user_message="msg",
            output_schema=schema,
            session_id="sess-1",
        )
        assert "type" in record.output_schema
        assert "foo" in record.output_schema
