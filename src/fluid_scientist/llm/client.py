"""Simple LLM client wrapper with call recording.

The :class:`LLMClient` is a thin abstraction over an LLM provider.  In
production it would call OpenAI / Anthropic / a local model; for now it
ships with a deterministic mock backend that returns structured
responses tailored to each ``purpose`` while recording every invocation
as an :class:`~fluid_scientist.draft_session.models.LLMCallRecord`.
"""

from __future__ import annotations

import uuid
from typing import Any

from fluid_scientist.draft_session.models import LLMCallRecord

# Purpose values accepted by LLMCallRecord.purpose (kept in sync with the model).
_ALLOWED_PURPOSES: frozenset[str] = frozenset({
    "input_routing",
    "study_decomposition",
    "physics_intent",
    "clarification_extract",
    "clarification_planning",
    "draft_generation",
    "draft_change_proposal",
    "unknown_parameter_mapping",
    "unknown_metric_mapping",
    "case_plan_generation",
    "missing_capability_analysis",
    "code_extension_spec",
    "code_generation",
    "code_review",
    "explanation",
})
_FALLBACK_PURPOSE = "explanation"


class LLMClient:
    """Simple LLM client wrapper with call recording.

    In production this would connect to OpenAI/Anthropic/etc.
    For now it provides a deterministic fallback that returns structured
    responses based on prompt type, while recording all calls via LLMCallRecord.
    """

    def __init__(self, provider: str = "mock", model_name: str = "mock-v1") -> None:
        self._records: list[LLMCallRecord] = []
        self._provider = provider
        self._model_name = model_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(
        self,
        purpose: str,
        prompt_name: str,
        system_prompt: str,
        user_message: str,
        output_schema: dict | None = None,
        session_id: str = "",
        input_refs: list[str] | None = None,
        prompt_version: str = "",
    ) -> tuple[dict, LLMCallRecord]:
        """Call LLM (or fallback) and return (parsed_output, record).

        Args:
            purpose: Why the LLM is being invoked (e.g. ``"study_decomposition"``).
            prompt_name: Logical name of the prompt template used.
            system_prompt: The system prompt content.
            user_message: The user-facing message content.
            output_schema: Optional JSON schema describing expected output.
            session_id: Draft session this call belongs to (for audit).
            input_refs: Optional list of referenced input artifact IDs.
            prompt_version: Version string for the prompt template.  When
                empty (default) the client records ``"mock-1"`` for mock
                runs and ``""`` for real-provider runs.

        Returns:
            A tuple of ``(parsed_output_dict, call_record)``.
        """
        call_id = f"llm_{uuid.uuid4().hex[:12]}"
        refs = list(input_refs) if input_refs else []

        # Decide whether to use the real provider or fall back to mock.
        use_mock = self._provider == "mock"
        fallback_reason: str | None = None
        raw_output: str | None = None
        parsed_output: dict[str, Any]
        success = True
        error: str | None = None

        if use_mock:
            fallback_reason = "LLM provider not configured; using deterministic mock"
            try:
                parsed_output = self._mock_response(
                    purpose, user_message, output_schema
                )
                raw_output = str(parsed_output)
            except Exception as exc:  # pragma: no cover - defensive
                success = False
                error = str(exc)
                parsed_output = {
                    "status": "error",
                    "message": f"mock response failed: {exc}",
                }
        else:  # pragma: no cover - future real-provider path
            # Placeholder for real provider integration.
            fallback_reason = "Real provider not yet implemented; falling back to mock"
            parsed_output = self._mock_response(
                purpose, user_message, output_schema
            )
            raw_output = str(parsed_output)

        # Coerce unknown purposes to a valid Literal value so the record
        # always validates; the original purpose is preserved in the
        # ``original_purpose`` field for debugging.
        purpose_known = purpose in _ALLOWED_PURPOSES
        record_purpose = purpose if purpose_known else _FALLBACK_PURPOSE
        record_original_purpose = None if purpose_known else purpose

        # Resolve prompt_version: explicit caller value wins, else mock
        # default ("mock-1"), else empty for real provider.
        resolved_prompt_version = prompt_version or ("mock-1" if use_mock else "")

        record = LLMCallRecord(
            call_id=call_id,
            session_id=session_id,
            purpose=record_purpose,  # type: ignore[arg-type]
            provider=self._provider,
            model_name=self._model_name,
            prompt_name=prompt_name,
            prompt_version=resolved_prompt_version,
            input_refs=refs,
            input_summary=user_message[:200],
            output_schema=str(output_schema) if output_schema else "",
            raw_output=raw_output,
            parsed_output=parsed_output,
            success=success,
            fallback_used=use_mock or bool(fallback_reason),
            fallback_reason=fallback_reason,
            original_purpose=record_original_purpose,
            error=error,
        )
        self._records.append(record)
        return parsed_output, record

    def get_records(self, session_id: str | None = None) -> list[LLMCallRecord]:
        """Return recorded calls, optionally filtered by *session_id*."""
        if session_id is None:
            return list(self._records)
        return [r for r in self._records if r.session_id == session_id]

    def get_last_record(self, session_id: str | None = None) -> LLMCallRecord | None:
        """Return the most recent recorded call, optionally filtered by session."""
        records = self.get_records(session_id)
        return records[-1] if records else None

    # ------------------------------------------------------------------
    # Mock response generators
    # ------------------------------------------------------------------

    def _mock_response(
        self,
        purpose: str,
        user_message: str,
        output_schema: dict | None,
    ) -> dict[str, Any]:
        """Return a deterministic structured response keyed by *purpose*."""
        if purpose == "study_decomposition":
            return {
                "status": "decomposed",
                "studies": [
                    {
                        "study_id": f"mock_study_{uuid.uuid4().hex[:6]}",
                        "title": user_message[:60] if user_message else "Mock study",
                        "study_type": "cfd_simulation",
                        "research_objective": user_message,
                        "physical_models": {
                            "dimension": "3d",
                            "temporal": "steady",
                            "turbulent": False,
                        },
                        "geometry": {"type": "unknown"},
                        "confidence": 0.5,
                        "fallback": True,
                    }
                ],
                "ambiguities": [],
                "fallback_used": True,
            }

        if purpose == "draft_generation":
            return {
                "status": "draft_generated",
                "draft": {
                    "title": f"Draft for: {user_message[:50]}",
                    "sections": [
                        {"name": "objective", "content": user_message},
                        {"name": "geometry", "content": "Auto-generated geometry section"},
                        {"name": "physics", "content": "Auto-generated physics section"},
                        {"name": "boundary_conditions", "content": "To be specified"},
                        {"name": "numerics", "content": "Default numerics"},
                    ],
                    "parameters": [],
                    "observables": [],
                },
                "fallback_used": True,
            }

        if purpose == "draft_change_proposal":
            return {
                "status": "proposal_generated",
                "proposal": {
                    "change_type": "edit",
                    "summary": f"Proposed change based on: {user_message[:80]}",
                    "patches": [],
                    "reasoning": "Mock proposal – LLM not configured",
                },
                "fallback_used": True,
            }

        if purpose == "code_extension_spec":
            return {
                "status": "spec_generated",
                "spec": {
                    "extension_type": "unknown",
                    "description": f"Code extension for: {user_message[:80]}",
                    "interface": {},
                    "tests": [],
                    "fallback": True,
                },
                "fallback_used": True,
            }

        return {
            "status": "fallback",
            "message": "LLM not configured",
            "fallback_used": True,
        }


__all__ = ["LLMClient"]
