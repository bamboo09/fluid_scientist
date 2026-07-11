"""Simple LLM client wrapper with call recording.

The :class:`LLMClient` is a thin abstraction over an LLM provider.  It
supports real OpenAI-compatible providers (OpenAI, GLM/智谱, DeepSeek)
via the ``openai`` SDK, and falls back to a deterministic mock backend
when no provider is configured.

Every invocation is recorded as an :class:`~fluid_scientist.draft_session.models.LLMCallRecord`.
"""

from __future__ import annotations

import json
import re
import time
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

# Provider base URLs for OpenAI-compatible APIs.
_PROVIDER_BASE_URLS: dict[str, str] = {
    "glm": "https://open.bigmodel.cn/api/paas/v4/",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}

# Suggested model IDs for each provider.
_PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4"],
    "glm": ["glm-4-plus", "glm-4", "glm-4-flash", "glm-4-long"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
}


class LLMClient:
    """LLM client wrapper with call recording.

    When ``provider`` is ``"mock"`` (default), uses a deterministic fallback.
    When a real provider and API key are configured, calls the provider's
    chat.completions endpoint via the ``openai`` SDK.

    An optional pre-built ``client`` may be injected for testing or advanced
    configuration; when omitted, an :class:`openai.OpenAI` client is created
    eagerly via :meth:`_init_real_client` (or lazily on the first real call).
    """

    def __init__(
        self,
        provider: str = "mock",
        model_name: str = "mock-v1",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 120.0,
        client: Any | None = None,
    ) -> None:
        self._records: list[LLMCallRecord] = []
        self._provider = provider
        self._model_name = model_name
        self._api_key = api_key
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._client = client

        # Eagerly initialize the real client when a provider and key are
        # available and no pre-built client was injected.  This mirrors the
        # validated Trae behaviour while preserving the Codex ability to
        # accept an injected client.
        if provider != "mock" and client is None and api_key:
            self._init_real_client()

    # ------------------------------------------------------------------
    # Configuration / introspection
    # ------------------------------------------------------------------

    def _init_real_client(self) -> None:
        """Initialize the OpenAI SDK client for the configured provider.

        Uses :data:`_PROVIDER_BASE_URLS` to resolve a sensible default
        ``base_url`` when none was supplied explicitly.  Raises
        ``ImportError`` if the ``openai`` package is not installed — this
        is intentional so that misconfigured real providers fail loudly
        instead of silently degrading to the mock backend.
        """
        from openai import OpenAI

        base_url = self._base_url or _PROVIDER_BASE_URLS.get(self._provider)
        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": self._timeout_seconds,
            "max_retries": 0,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def reconfigure(
        self,
        provider: str,
        model_name: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        """Reconfigure the client to use a real provider at runtime.

        Discards any previously injected or constructed client and builds
        a fresh one via :meth:`_init_real_client`.
        """
        self._provider = provider
        self._model_name = model_name
        self._api_key = api_key
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._client = None
        self._init_real_client()

    @property
    def is_mock(self) -> bool:
        """``True`` when no real provider/client is available."""
        return self._provider == "mock" or self._client is None

    @property
    def provider(self) -> str:
        """The currently configured provider identifier."""
        return self._provider

    @property
    def model_name(self) -> str:
        """The currently configured model name."""
        return self._model_name

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

        Raises:
            RuntimeError: When a real (non-mock) provider is configured
                but the call fails.  The error is raised *after* the
                failure is recorded so callers still have an audit trail.
        """
        call_id = f"llm_{uuid.uuid4().hex[:12]}"
        refs = list(input_refs) if input_refs else []
        started = time.perf_counter()

        # Decide whether to use the real provider or fall back to mock.
        # NB: we intentionally check ``self._provider`` (not
        # ``self.is_mock``) so that a misconfigured real provider — one
        # whose ``_client`` is ``None`` due to a missing api_key — fails
        # loudly inside ``_real_response`` rather than silently using the
        # mock backend.
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
        else:
            try:
                parsed_output, raw_output = self._real_response(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    output_schema=output_schema,
                )
            except Exception as exc:
                success = False
                error = str(exc)
                parsed_output = {
                    "status": "error",
                    "message": str(exc),
                }

        # Coerce unknown purposes to a valid Literal value so the record
        # always validates; the original purpose is preserved in the
        # ``original_purpose`` field for debugging.
        purpose_known = purpose in _ALLOWED_PURPOSES
        record_purpose = purpose if purpose_known else _FALLBACK_PURPOSE
        record_original_purpose = None if purpose_known else purpose

        # Resolve prompt_version: explicit caller value wins, else mock
        # default ("mock-1"), else empty for real provider.
        resolved_prompt_version = prompt_version or ("mock-1" if use_mock else "")

        latency_ms = round((time.perf_counter() - started) * 1000, 3)
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
            latency_ms=latency_ms,
        )
        self._records.append(record)
        if not success and not use_mock:
            raise RuntimeError(
                f"LLM call failed: purpose={purpose}, provider={self._provider}, "
                f"model={self._model_name}, error={error}"
            )
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
    # Real provider call
    # ------------------------------------------------------------------

    def _real_response(
        self,
        *,
        system_prompt: str,
        user_message: str,
        output_schema: dict | None,
    ) -> tuple[dict[str, Any], str]:
        """Call the configured OpenAI-compatible provider and parse JSON.

        If the provider returns content wrapped in markdown code blocks
        (```` ```json ```` / ```` ``` ````) or embedded in prose, the
        :meth:`_extract_json` helper is used to recover the JSON object
        before falling back to a hard ``RuntimeError``.

        Raises:
            RuntimeError: If no client/api_key is available, the provider
                returns an empty response, or the content cannot be
                parsed as JSON.
        """
        if self._client is None:
            # Lazily initialize when the constructor could not (e.g. the
            # caller set ``provider != "mock"`` but supplied no key at
            # construction time and later called ``reconfigure`` without
            # an api_key, or the api_key was set via attribute mutation).
            if not self._api_key:
                raise RuntimeError("LLM provider is configured without an api_key")
            self._init_real_client()
        client = self._client
        if client is None:  # pragma: no cover - defensive
            raise RuntimeError("LLM provider client could not be initialized")

        schema_note = ""
        if output_schema:
            schema_note = (
                "\nReturn only JSON matching this schema: "
                + json.dumps(output_schema, ensure_ascii=False)
            )
        response = client.chat.completions.create(
            model=self._model_name,
            messages=[
                {"role": "system", "content": system_prompt + schema_note},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("provider returned empty response")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # The provider may have wrapped the JSON in markdown fences or
            # surrounding prose; attempt extraction before failing.
            parsed = self._extract_json(content)
            if parsed is None:
                raise RuntimeError("provider returned non-JSON response")
        if not isinstance(parsed, dict):
            raise RuntimeError("provider JSON root must be an object")
        return parsed, content

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Try to extract a JSON object from a text response.

        Handles three common provider output patterns:

        1. ```` ```json ... ``` ```` fenced blocks
        2. ```` ``` ... ``` ```` generic fenced blocks
        3. Bare ``{ ... }`` objects embedded in prose

        Returns the parsed ``dict`` on success, or ``None`` if no valid
        JSON object could be recovered.
        """
        # 1. ```json ... ``` block
        match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(1))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # 2. ``` ... ``` block
        match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(1))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # 3. Bare { ... } object
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        return None

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
