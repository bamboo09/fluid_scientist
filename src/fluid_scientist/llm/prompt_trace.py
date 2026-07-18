"""Prompt Trace infrastructure for LLM auditability.

This module provides a unified PromptTrace record that captures the full
context entering an LLM call, enabling downstream auditing of:

- Which skills were selected and injected
- What spec version / conversation history was provided
- Which references were included
- The actual model request/response
- Field-level provenance from user quote to spec patch

This addresses V2 plan sections 3.5 (field-level source tracking) and
4.1/4.2 (skill invocation audit and prompt trace reviewability).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class PromptTraceContext(BaseModel):
    """The context that was provided to the LLM."""

    user_message: str = ""
    current_spec_snapshot: dict[str, Any] | None = None
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    confirmed_facts: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    skill_prompt_fragments: list[dict[str, str]] = Field(default_factory=list)
    reference_documents: list[dict[str, str]] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    system_prompt_hash: str = ""


class PromptTraceResult(BaseModel):
    """The result of the LLM call."""

    model_raw_response: str = ""
    model_structured_output: dict[str, Any] | None = None
    parsed_successfully: bool = False
    parse_error: str | None = None
    latency_ms: int = 0


class PromptTrace(BaseModel):
    """A complete prompt trace record for a single LLM invocation.

    This record is saved for every LLM call and can be reviewed to
    verify that the full context (spec, history, skills, references)
    was provided to the model, and to trace field-level provenance.
    """

    trace_id: str = Field(default_factory=lambda: f"pt_{uuid.uuid4().hex[:12]}")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Session and spec linkage
    session_id: str = ""
    spec_id: str = ""
    spec_version: int = 0

    # Model information
    actual_model: str = ""
    model_request_id: str = ""
    provider: str = ""
    purpose: str = ""

    # Skill information
    skill_ids: list[str] = Field(default_factory=list)
    skill_bundle_hash: str = ""
    reference_ids: list[str] = Field(default_factory=list)
    reference_hashes: dict[str, str] = Field(default_factory=dict)
    prompt_snapshot_id: str = ""

    # Context and result
    context: PromptTraceContext = Field(default_factory=PromptTraceContext)
    result: PromptTraceResult = Field(default_factory=PromptTraceResult)

    # Field-level provenance: maps each extracted field to its source quote
    field_provenance: list[dict[str, str]] = Field(default_factory=list)

    def compute_skill_bundle_hash(self) -> str:
        """Compute a deterministic hash of the skill bundle."""
        skill_str = json.dumps(self.skill_ids, sort_keys=True)
        return hashlib.sha256(skill_str.encode()).hexdigest()[:16]

    def to_audit_dict(self) -> dict[str, Any]:
        """Return a serializable audit dictionary."""
        return {
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "actual_model": self.actual_model,
            "model_request_id": self.model_request_id,
            "provider": self.provider,
            "purpose": self.purpose,
            "skill_ids": self.skill_ids,
            "skill_bundle_hash": self.skill_bundle_hash,
            "reference_ids": self.reference_ids,
            "reference_hashes": self.reference_hashes,
            "prompt_snapshot_id": self.prompt_snapshot_id,
            "context_summary": {
                "user_message_length": len(self.context.user_message),
                "has_spec_snapshot": self.context.current_spec_snapshot is not None,
                "conversation_turns": len(self.context.conversation_history),
                "confirmed_facts_count": len(self.context.confirmed_facts),
                "unresolved_conflicts_count": len(self.context.unresolved_conflicts),
                "skill_fragments_count": len(self.context.skill_prompt_fragments),
                "reference_count": len(self.context.reference_documents),
                "has_output_schema": self.context.output_schema is not None,
                "system_prompt_hash": self.context.system_prompt_hash,
            },
            "result_summary": {
                "parsed_successfully": self.result.parsed_successfully,
                "parse_error": self.result.parse_error,
                "latency_ms": self.result.latency_ms,
                "raw_response_length": len(self.result.model_raw_response),
            },
            "field_provenance_count": len(self.field_provenance),
        }


class PromptTraceRecorder:
    """Records and stores prompt traces for auditing.

    Traces are stored in memory and can be persisted to disk.
    """

    def __init__(self) -> None:
        self._traces: list[PromptTrace] = []
        self._traces_by_session: dict[str, list[PromptTrace]] = {}

    def record(self, trace: PromptTrace) -> None:
        """Record a prompt trace."""
        if trace.skill_ids and not trace.skill_bundle_hash:
            trace.skill_bundle_hash = trace.compute_skill_bundle_hash()
        self._traces.append(trace)
        if trace.session_id:
            self._traces_by_session.setdefault(trace.session_id, []).append(trace)

    def get_traces(self, session_id: str | None = None) -> list[PromptTrace]:
        """Get traces, optionally filtered by session."""
        if session_id:
            return list(self._traces_by_session.get(session_id, []))
        return list(self._traces)

    def get_last_trace(self, session_id: str | None = None) -> PromptTrace | None:
        """Get the most recent trace."""
        traces = self.get_traces(session_id)
        return traces[-1] if traces else None

    def to_audit_report(self, session_id: str | None = None) -> dict[str, Any]:
        """Generate an audit report from stored traces."""
        traces = self.get_traces(session_id)
        return {
            "total_traces": len(traces),
            "traces": [t.to_audit_dict() for t in traces],
            "unique_skills_used": list(set(
                sid for t in traces for sid in t.skill_ids
            )),
            "models_used": list(set(t.actual_model for t in traces if t.actual_model)),
            "purposes": list(set(t.purpose for t in traces if t.purpose)),
            "success_rate": (
                sum(1 for t in traces if t.result.parsed_successfully) / len(traces)
                if traces else 0.0
            ),
        }

    def clear(self) -> None:
        """Clear all traces."""
        self._traces.clear()
        self._traces_by_session.clear()


# Global recorder instance
_global_recorder: PromptTraceRecorder | None = None


def get_prompt_trace_recorder() -> PromptTraceRecorder:
    """Get the global prompt trace recorder."""
    global _global_recorder
    if _global_recorder is None:
        _global_recorder = PromptTraceRecorder()
    return _global_recorder
