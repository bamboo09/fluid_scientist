"""Tracing infrastructure for model runtime invocations.

Every model call produces a :class:`ModelTrace` capturing full
provenance: which role/provider/model was targeted, what was actually
returned, prompt/effort metadata, token usage, latency and retry
information.  Traces are stored in-memory by :class:`TraceRecorder` and
can be exported to JSON for offline analysis.

A hard rule of this module: **API keys are never stored in traces.**
The :class:`ModelTrace` schema intentionally has no key/secret field,
and :meth:`TraceRecorder.export_json` scrubs any key-like field as a
defence in depth.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import UTC

__all__ = ["ModelTrace", "TraceRecorder"]

# Field names that would indicate a leaked secret if they ever appeared in a
# trace payload.  Used by :meth:`TraceRecorder.export_json` as defence in
# depth; the :class:`ModelTrace` schema itself never defines them.
_SECRET_KEYS: frozenset[str] = frozenset({
    "api_key",
    "api_key_env",
    "apikey",
    "authorization",
    "key",
    "secret",
    "token",
})


def _sha256(text: str) -> str:
    """Return the hex SHA-256 digest of *text* (UTF-8 encoded)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ModelTrace(BaseModel):
    """Provenance record for a single model invocation.

    The trace captures *what was attempted* and *what happened* without
    ever retaining secrets or full prompt bodies (only the SHA-256 of the
    system prompt is kept, so prompt identity can be correlated without
    leaking prompt content).
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(
        default_factory=lambda: f"trace_{uuid.uuid4().hex[:16]}"
    )
    role: str
    provider: str
    configured_model: str
    actual_model_from_response: str | None = None
    endpoint_type: str = "chat.completions"
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    structured_output_enabled: bool = False
    tool_calling_enabled: bool = False
    system_prompt_sha256: str | None = None
    conversation_turn_count: int = 0
    current_spec_included: bool = False
    skill_ids: list[str] = Field(default_factory=list)
    request_id: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    retry_count: int = 0
    fallback_used: bool = False
    timestamp: str = Field(
        default_factory=lambda: _now_iso()
    )

    @staticmethod
    def hash_system_prompt(system_prompt: str) -> str:
        """Return the SHA-256 digest of a system prompt."""
        return _sha256(system_prompt)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Uses :data:`fluid_scientist.compat.UTC` so the module stays
    Python-3.10 compatible.
    """
    from datetime import datetime

    return datetime.now(UTC).isoformat()


class TraceRecorder:
    """In-memory store of :class:`ModelTrace` records with JSON export.

    The recorder is deliberately simple: traces are appended in order and
    can be retrieved wholesale, filtered by role or request id, and
    serialized to a JSON document.  It performs no I/O of its own and
    holds no references to API keys.
    """

    def __init__(self) -> None:
        self._traces: list[ModelTrace] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def record(self, trace: ModelTrace) -> ModelTrace:
        """Append *trace* to the in-memory store and return it."""
        if not isinstance(trace, ModelTrace):
            raise TypeError(
                f"TraceRecorder.record expects ModelTrace, got {type(trace).__name__}"
            )
        self._traces.append(trace)
        return trace

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def all(self) -> list[ModelTrace]:
        """Return a shallow copy of every recorded trace."""
        return list(self._traces)

    def for_role(self, role: str) -> list[ModelTrace]:
        """Return traces matching *role*."""
        return [t for t in self._traces if t.role == role]

    def for_request(self, request_id: str) -> list[ModelTrace]:
        """Return traces matching *request_id*."""
        return [t for t in self._traces if t.request_id == request_id]

    def latest(self) -> ModelTrace | None:
        """Return the most recently recorded trace, or ``None``."""
        return self._traces[-1] if self._traces else None

    def clear(self) -> None:
        """Drop all recorded traces."""
        self._traces.clear()

    def __len__(self) -> int:
        return len(self._traces)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._traces)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_json(self, *, indent: int = 2) -> str:
        """Serialize all traces to a JSON string.

        As defence in depth, any field whose name looks like a secret is
        stripped from the payload.  The :class:`ModelTrace` schema never
        defines such fields, so in practice nothing is removed, but this
        guarantees a leaked key can never leave the process via a trace
        export.
        """
        payload: list[dict[str, Any]] = []
        for trace in self._traces:
            data: dict[str, Any] = trace.model_dump(mode="json")
            for secret in _SECRET_KEYS:
                data.pop(secret, None)
            payload.append(data)
        return json.dumps(payload, indent=indent, ensure_ascii=False, default=str)
