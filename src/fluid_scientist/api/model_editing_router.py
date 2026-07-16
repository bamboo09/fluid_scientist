"""API router for model-driven spec editing.

Provides endpoints for the new model-driven spec editing system that uses
SimulationSpecPatch instead of keyword/regex-based field extraction.

Endpoints:
  POST   /api/v5/model-editing/sessions                       — Create session
  GET    /api/v5/model-editing/sessions/{session_id}           — Get session state
  POST   /api/v5/model-editing/sessions/{session_id}/turns     — Process user message
  GET    /api/v5/model-editing/sessions/{session_id}/spec      — Get current spec
  PATCH  /api/v5/model-editing/sessions/{session_id}/spec      — Apply patch directly
  POST   /api/v5/model-editing/sessions/{session_id}/confirm   — Confirm pending patch
  POST   /api/v5/model-editing/sessions/{session_id}/reject    — Reject pending patch
  POST   /api/v5/model-editing/sessions/{session_id}/undo      — Undo last patch
  GET    /api/v5/model-editing/sessions/{session_id}/history   — Get patch history
  GET    /api/v5/model-editing/sessions/{session_id}/trace     — Get model traces
  GET    /api/v5/model-editing/schema                          — Get patch JSON schema
  POST   /api/v5/model-editing/migrate                         — Migrate legacy spec
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from fluid_scientist.compat import UTC
from fluid_scientist.model_runtime.tracing import ModelTrace, TraceRecorder
from fluid_scientist.prompts.critic import build_critic_prompt
from fluid_scientist.prompts.spec_editor import build_spec_editor_prompt
from fluid_scientist.prompts.two_call_strategy import TwoCallStrategy
from fluid_scientist.session_state.context_builder import ContextBuilder
from fluid_scientist.session_state.intent_detector import IntentDetector, UserIntent
from fluid_scientist.session_state.models import ConversationTurn
from fluid_scientist.session_state.session_manager import SessionManager
from fluid_scientist.spec_editing.models import SimulationSpecPatch
from fluid_scientist.spec_editing.patch_engine import PatchEngine
from fluid_scientist.study_spec.migration import LegacyMigrator
from fluid_scientist.study_spec.models import SimulationStudySpec
from fluid_scientist.study_spec.schema_export import SchemaExporter

router = APIRouter(prefix="/api/v5/model-editing", tags=["model-editing"])

# ---------------------------------------------------------------------------
# Global in-memory instances (same pattern as cylinder_flow_router._spec_store)
# ---------------------------------------------------------------------------

_session_manager: SessionManager = SessionManager()
_patch_engine: PatchEngine = PatchEngine()
_schema_exporter: SchemaExporter = SchemaExporter()
_context_builder: ContextBuilder = ContextBuilder()
_intent_detector: IntentDetector = IntentDetector()
_trace_recorder: TraceRecorder = TraceRecorder()
_two_call_strategy: TwoCallStrategy = TwoCallStrategy(
    system_prompt_builder=build_spec_editor_prompt,
    critic_prompt_builder=build_critic_prompt,
)

_OPENFOAM_ENV: dict[str, Any] = {
    "version": "v2312",
    "solvers": ["icoFoam", "pimpleFoam", "simpleFoam"],
    "function_objects": ["forceCoeffs", "probes", "fieldAverage"],
    "mesh_tools": ["blockMesh", "snappyHexMesh"],
}
_DEFAULT_SKILLS: list[str] = []


# ---------------------------------------------------------------------------
# LLM client access — bridges to the global LLM configured via v5_router
# ---------------------------------------------------------------------------

def _get_llm_client():
    """Return the globally configured LLMClient, or None if not configured.

    A *mock* provider or a client without an API key is treated as
    unavailable — this prevents silent fallback to deterministic mock
    responses, which is explicitly prohibited by the model-editing API
    contract.
    """
    try:
        from fluid_scientist.api.v5_router import _llm_client
        if _llm_client is None:
            return None
        provider = getattr(_llm_client, "_provider", None)
        api_key = getattr(_llm_client, "_api_key", None)
        if provider == "mock" or not api_key:
            return None
        return _llm_client
    except (ImportError, AttributeError):
        return None


def _make_model_client_callable(
    llm_client: Any,
    session_id: str = "",
) -> Callable[[str], dict]:
    """Create a ``Callable[[str], dict]`` adapter around *llm_client*.

    The :class:`TwoCallStrategy` expects a simple callable that takes a
    prompt string and returns a parsed dict.  This adapter bridges the
    full :class:`LLMClient.call`` API to that contract.

    No silent fallback: if the underlying client reports a fallback or
    returns a non-dict, the adapter raises so that
    :class:`TwoCallStrategy` surfaces a ``MODEL_FAILED`` error.
    """

    def _callable(prompt: str) -> dict:
        parsed, record = llm_client.call(
            purpose="spec_editing",
            prompt_name="model_editing",
            system_prompt="",
            user_message=prompt,
            output_schema=None,
            session_id=session_id,
        )
        if getattr(record, "fallback_used", False):
            raise RuntimeError(
                "underlying LLM client used a fallback; "
                "silent fallback is prohibited"
            )
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str) and parsed.strip():
            try:
                result = json.loads(parsed)
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, TypeError):
                pass
        raise RuntimeError(
            f"model returned non-dict output: {type(parsed).__name__}"
        )

    return _callable


# ---------------------------------------------------------------------------
# Request / Response Pydantic models
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    """Request body for POST /sessions."""

    project_id: str | None = None
    legacy_spec: dict[str, Any] | None = None


class CreateSessionResponse(BaseModel):
    """Response for POST /sessions."""

    session_id: str
    spec: dict[str, Any] | None = None
    phase: str


class TurnRequest(BaseModel):
    """Request body for POST /sessions/{session_id}/turns."""

    user_message: str


class TurnResponse(BaseModel):
    """Response for POST /sessions/{session_id}/turns."""

    session_id: str
    intent: str
    phase: str
    assistant_message: str = ""
    pending_patch: dict[str, Any] | None = None
    clarifications: list[dict[str, Any]] = Field(default_factory=list)
    diff: dict[str, Any] | None = None
    impact: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)
    spec_version: int = 0


class PatchRequest(BaseModel):
    """Request body for PATCH /sessions/{session_id}/spec."""

    patch: dict[str, Any]


class PatchResponse(BaseModel):
    """Response for PATCH /sessions/{session_id}/spec."""

    new_spec: dict[str, Any] | None = None
    diff: dict[str, Any] | None = None
    impact: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)


class ConfirmResponse(BaseModel):
    """Response for POST /sessions/{session_id}/confirm."""

    confirmed: bool
    patch_id: str | None = None
    spec_version: int = 0
    errors: list[str] = Field(default_factory=list)


class RejectResponse(BaseModel):
    """Response for POST /sessions/{session_id}/reject."""

    rejected: bool
    errors: list[str] = Field(default_factory=list)


class UndoResponse(BaseModel):
    """Response for POST /sessions/{session_id}/undo."""

    undone: bool
    spec_version: int = 0
    errors: list[str] = Field(default_factory=list)


class MigrateRequest(BaseModel):
    """Request body for POST /migrate."""

    legacy_spec: dict[str, Any]


class MigrateResponse(BaseModel):
    """Response for POST /migrate."""

    spec: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _require_session(session_id: str):
    """Return the session or raise 404."""
    session = _session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )
    return session


def _record_turn(
    session_id: str,
    user_message: str,
    intent: str,
    assistant_message: str,
    *,
    patch_id: str | None = None,
    trace_ids: list[str] | None = None,
) -> None:
    """Append a ConversationTurn to the session."""
    turn = ConversationTurn(
        turn_id=f"turn_{uuid.uuid4().hex[:16]}",
        timestamp=_now_iso(),
        user_message=user_message,
        assistant_message=assistant_message,
        patch_id=patch_id,
        model_trace_ids=trace_ids or [],
        intent=intent,
    )
    _session_manager.add_turn(session_id, turn)


def _record_trace(
    session_id: str,
    *,
    role: str = "spec_editor",
    provider: str = "unknown",
    configured_model: str = "unknown",
) -> str:
    """Record a ModelTrace and associate it with the session."""
    trace = ModelTrace(
        role=role,
        provider=provider,
        configured_model=configured_model,
        request_id=f"req_{uuid.uuid4().hex[:16]}",
    )
    _trace_recorder.record(trace)
    _session_manager.add_model_trace(session_id, trace.trace_id)
    return trace.trace_id


def _serialize_patch_result(result) -> tuple[dict | None, dict | None, list[str]]:
    """Serialize a PatchResult into (diff_dict, impact_dict, errors)."""
    diff = result.diff.model_dump() if result.diff else None
    impact = result.impact.model_dump() if result.impact else None
    return diff, impact, list(result.errors)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/sessions", response_model=CreateSessionResponse)
def create_session(request: CreateSessionRequest) -> CreateSessionResponse:
    """Create a new research session.

    If *legacy_spec* is provided, it is migrated to a
    :class:`SimulationStudySpec` and set as the session's active spec.
    """
    project_id = request.project_id or "default"
    session = _session_manager.create_session(project_id)

    spec_dict: dict[str, Any] | None = None
    if request.legacy_spec is not None:
        migrator = LegacyMigrator()
        new_spec = migrator.migrate_from_cylinder_flow_spec(request.legacy_spec)
        _session_manager.set_active_spec(session.session_id, new_spec)
        spec_dict = new_spec.model_dump(mode="json")

    return CreateSessionResponse(
        session_id=session.session_id,
        spec=spec_dict,
        phase=str(session.current_phase),
    )


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    """Return the full session state."""
    session = _require_session(session_id)
    return session.model_dump(mode="json")


@router.post("/sessions/{session_id}/turns", response_model=TurnResponse)
def process_turn(session_id: str, request: TurnRequest) -> TurnResponse:
    """Process a user message within a session.

    The endpoint detects the user's high-level intent and routes to the
    appropriate handler:

    * **CONFIRM_PENDING_PATCH** — confirm and apply the pending patch.
    * **REJECT_PENDING_PATCH** — reject the pending patch.
    * **UNDO_LAST_PATCH** — undo the last applied patch.
    * **REQUEST_EXPLANATION** — echo back (placeholder).
    * **CREATE_SPEC / MODIFY_EXISTING_SPEC** — build context, call the
      model via the two-call strategy, and process the resulting patch.
    """
    session = _require_session(session_id)
    user_message = request.user_message
    intent = _intent_detector.detect_intent(user_message, session)
    intent_str = str(intent)

    # --- Control intents (work without an LLM) ---

    if intent == UserIntent.CONFIRM_PENDING_PATCH:
        return _handle_confirm_turn(session_id, user_message, intent_str)

    if intent == UserIntent.REJECT_PENDING_PATCH:
        return _handle_reject_turn(session_id, user_message, intent_str)

    if intent == UserIntent.UNDO_LAST_PATCH:
        return _handle_undo_turn(session_id, user_message, intent_str)

    if intent == UserIntent.REQUEST_EXPLANATION:
        return _handle_explanation_turn(session_id, user_message, intent_str)

    # --- CREATE_SPEC / MODIFY_EXISTING_SPEC (require an LLM) ---

    return _handle_spec_modification(session_id, user_message, intent_str)


@router.get("/sessions/{session_id}/spec")
def get_spec(session_id: str) -> dict[str, Any]:
    """Return the session's active spec."""
    session = _require_session(session_id)
    spec = _session_manager.get_active_spec(session_id)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active spec for this session.",
        )
    return spec.model_dump(mode="json")


@router.patch("/sessions/{session_id}/spec", response_model=PatchResponse)
def apply_patch_directly(session_id: str, request: PatchRequest) -> PatchResponse:
    """Apply a SimulationSpecPatch directly to the active spec.

    The patch is validated and processed through the full
    :class:`PatchEngine` pipeline (validate -> impact -> apply -> diff ->
    record).  If the patch has blocking clarifications, they are returned
    as errors since there is no confirmation flow for direct patches.
    """
    session = _require_session(session_id)
    current_spec = _session_manager.get_active_spec(session_id)
    if current_spec is None:
        return PatchResponse(
            errors=["NO_SPEC: No active spec to apply the patch to."],
        )

    # Validate the patch dict into a SimulationSpecPatch.
    try:
        patch = SimulationSpecPatch.model_validate(request.patch)
    except Exception as exc:
        return PatchResponse(
            errors=[f"PATCH_VALIDATION_ERROR: {exc}"],
        )

    result = _patch_engine.process_patch(patch, current_spec)

    if result.errors:
        return PatchResponse(errors=list(result.errors))

    if result.clarifications:
        return PatchResponse(
            errors=[
                f"CLARIFICATION_NEEDED: {c.question}"
                for c in result.clarifications
            ],
        )

    if result.new_spec is not None:
        _session_manager.set_active_spec(session_id, result.new_spec)

    return PatchResponse(
        new_spec=result.new_spec.model_dump(mode="json") if result.new_spec else None,
        diff=result.diff.model_dump(mode="json") if result.diff else None,
        impact=result.impact.model_dump(mode="json") if result.impact else None,
    )


@router.post("/sessions/{session_id}/confirm", response_model=ConfirmResponse)
def confirm_pending_patch(session_id: str) -> ConfirmResponse:
    """Confirm the pending patch.

    If a pending patch exists, it is confirmed (added to the session's
    patch history), applied through the :class:`PatchEngine`, and the
    pending field is cleared.
    """
    session = _require_session(session_id)

    if session.pending_patch is None:
        return ConfirmResponse(
            confirmed=False,
            patch_id=None,
            spec_version=session.active_spec_version,
            errors=["NO_PENDING_PATCH: There is no pending patch to confirm."],
        )

    patch = session.pending_patch
    patch_id = _session_manager.confirm_pending_patch(session_id)

    errors: list[str] = []
    spec_version = session.active_spec_version

    current_spec = _session_manager.get_active_spec(session_id)
    if current_spec is not None:
        result = _patch_engine.process_patch(patch, current_spec)
        if result.errors:
            errors.extend(result.errors)
        elif result.new_spec is not None:
            _session_manager.set_active_spec(session_id, result.new_spec)
            spec_version = result.new_spec.version
    else:
        errors.append("NO_SPEC: No active spec to apply the patch to.")

    _session_manager.clear_pending_patch(session_id)

    return ConfirmResponse(
        confirmed=True,
        patch_id=patch_id,
        spec_version=spec_version,
        errors=errors,
    )


@router.post("/sessions/{session_id}/reject", response_model=RejectResponse)
def reject_pending_patch(session_id: str) -> RejectResponse:
    """Reject the pending patch.

    The pending patch is cleared without being applied.
    """
    session = _require_session(session_id)

    if session.pending_patch is None:
        return RejectResponse(
            rejected=False,
            errors=["NO_PENDING_PATCH: There is no pending patch to reject."],
        )

    _session_manager.clear_pending_patch(session_id)

    return RejectResponse(rejected=True)


@router.post("/sessions/{session_id}/undo", response_model=UndoResponse)
def undo_last_patch(session_id: str) -> UndoResponse:
    """Undo the last applied patch.

    Retrieves the latest :class:`PatchRecord` from the
    :class:`PatchHistory`, generates a reverse patch via the
    :class:`UndoEngine`, and applies it through the :class:`PatchEngine`.
    """
    session = _require_session(session_id)

    current_spec = _session_manager.get_active_spec(session_id)
    if current_spec is None:
        return UndoResponse(
            undone=False,
            spec_version=0,
            errors=["NO_SPEC: No active spec to undo."],
        )

    spec_id = session.active_spec_id
    if not spec_id:
        return UndoResponse(
            undone=False,
            spec_version=session.active_spec_version,
            errors=["NO_SPEC: No active spec to undo."],
        )

    latest_record = _patch_engine.history.get_latest(spec_id)
    if latest_record is None:
        return UndoResponse(
            undone=False,
            spec_version=session.active_spec_version,
            errors=["NO_PATCH_TO_UNDO: No patches have been applied to this spec."],
        )

    # Retrieve the pre-patch spec so the UndoEngine can read original values.
    pre_patch_spec = _session_manager._spec_store.get_version(
        spec_id, latest_record.base_version,
    )
    if pre_patch_spec is None:
        return UndoResponse(
            undone=False,
            spec_version=session.active_spec_version,
            errors=["UNDO_FAILED: Could not retrieve the pre-patch spec version."],
        )

    # Generate the reverse patch.
    reverse_patch = _patch_engine.undo_engine.create_reverse_patch(
        latest_record.patch,
        pre_patch_spec.model_dump(),
    )

    # Apply the reverse patch.
    result = _patch_engine.process_patch(reverse_patch, current_spec)
    if result.errors:
        return UndoResponse(
            undone=False,
            spec_version=session.active_spec_version,
            errors=result.errors,
        )

    if result.new_spec is not None:
        _session_manager.set_active_spec(session_id, result.new_spec)

    return UndoResponse(
        undone=True,
        spec_version=result.new_spec.version if result.new_spec else session.active_spec_version,
    )


@router.get("/sessions/{session_id}/history")
def get_patch_history(session_id: str) -> list[dict[str, Any]]:
    """Return the patch history for the session's active spec."""
    session = _require_session(session_id)
    spec_id = session.active_spec_id
    if not spec_id:
        return []
    records = _patch_engine.history.list_for_spec(spec_id)
    return [r.model_dump(mode="json") for r in records]


@router.get("/sessions/{session_id}/trace")
def get_model_traces(session_id: str) -> list[dict[str, Any]]:
    """Return the model traces recorded for this session."""
    session = _require_session(session_id)
    trace_ids = set(session.model_trace_ids)
    traces = [t for t in _trace_recorder.all() if t.trace_id in trace_ids]
    return [t.model_dump(mode="json") for t in traces]


@router.get("/schema")
def get_patch_schema() -> dict[str, Any]:
    """Return the JSON Schema for :class:`SimulationSpecPatch`."""
    return SimulationSpecPatch.model_json_schema()


@router.post("/migrate", response_model=MigrateResponse)
def migrate_legacy_spec(request: MigrateRequest) -> MigrateResponse:
    """Migrate a legacy CylinderFlow2DExperimentSpecV1 dict to a
    :class:`SimulationStudySpec`.
    """
    try:
        migrator = LegacyMigrator()
        new_spec = migrator.migrate_from_cylinder_flow_spec(request.legacy_spec)
        return MigrateResponse(
            spec=new_spec.model_dump(mode="json"),
            errors=[],
        )
    except Exception as exc:
        return MigrateResponse(
            spec={},
            errors=[f"MIGRATION_FAILED: {exc}"],
        )


# ---------------------------------------------------------------------------
# Turn handlers (internal)
# ---------------------------------------------------------------------------

def _handle_confirm_turn(
    session_id: str,
    user_message: str,
    intent_str: str,
) -> TurnResponse:
    """Handle CONFIRM_PENDING_PATCH intent within the /turns endpoint."""
    session = _session_manager.get_session(session_id)
    assert session is not None

    if session.pending_patch is None:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="There is no pending patch to confirm.",
            errors=["NO_PENDING_PATCH: There is no pending patch to confirm."],
            spec_version=session.active_spec_version,
        )
        _record_turn(session_id, user_message, intent_str, resp.assistant_message)
        return resp

    patch = session.pending_patch
    patch_id = _session_manager.confirm_pending_patch(session_id)

    errors: list[str] = []
    diff_dict: dict[str, Any] | None = None
    impact_dict: dict[str, Any] | None = None
    spec_version = session.active_spec_version

    current_spec = _session_manager.get_active_spec(session_id)
    if current_spec is not None:
        result = _patch_engine.process_patch(patch, current_spec)
        if result.errors:
            errors.extend(result.errors)
        elif result.new_spec is not None:
            _session_manager.set_active_spec(session_id, result.new_spec)
            spec_version = result.new_spec.version
            diff_dict, impact_dict, _ = _serialize_patch_result(result)
    else:
        errors.append("NO_SPEC: No active spec to apply the patch to.")

    _session_manager.clear_pending_patch(session_id)

    assistant_msg = (
        f"Patch {patch_id} confirmed and applied."
        if not errors
        else f"Patch {patch_id} confirmed but encountered errors."
    )
    resp = TurnResponse(
        session_id=session_id,
        intent=intent_str,
        phase=str(session.current_phase),
        assistant_message=assistant_msg,
        diff=diff_dict,
        impact=impact_dict,
        errors=errors,
        spec_version=spec_version,
    )
    _record_turn(
        session_id, user_message, intent_str, resp.assistant_message,
        patch_id=patch_id,
    )
    return resp


def _handle_reject_turn(
    session_id: str,
    user_message: str,
    intent_str: str,
) -> TurnResponse:
    """Handle REJECT_PENDING_PATCH intent within the /turns endpoint."""
    session = _session_manager.get_session(session_id)
    assert session is not None

    if session.pending_patch is None:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="There is no pending patch to reject.",
            errors=["NO_PENDING_PATCH: There is no pending patch to reject."],
            spec_version=session.active_spec_version,
        )
        _record_turn(session_id, user_message, intent_str, resp.assistant_message)
        return resp

    _session_manager.clear_pending_patch(session_id)

    resp = TurnResponse(
        session_id=session_id,
        intent=intent_str,
        phase=str(session.current_phase),
        assistant_message="Pending patch rejected.",
        spec_version=session.active_spec_version,
    )
    _record_turn(session_id, user_message, intent_str, resp.assistant_message)
    return resp


def _handle_undo_turn(
    session_id: str,
    user_message: str,
    intent_str: str,
) -> TurnResponse:
    """Handle UNDO_LAST_PATCH intent within the /turns endpoint."""
    session = _session_manager.get_session(session_id)
    assert session is not None

    current_spec = _session_manager.get_active_spec(session_id)
    if current_spec is None:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="No active spec to undo.",
            errors=["NO_SPEC: No active spec to undo."],
            spec_version=0,
        )
        _record_turn(session_id, user_message, intent_str, resp.assistant_message)
        return resp

    spec_id = session.active_spec_id
    latest_record = _patch_engine.history.get_latest(spec_id) if spec_id else None
    if latest_record is None:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="No patches have been applied to undo.",
            errors=["NO_PATCH_TO_UNDO: No patches have been applied to this spec."],
            spec_version=session.active_spec_version,
        )
        _record_turn(session_id, user_message, intent_str, resp.assistant_message)
        return resp

    pre_patch_spec = _session_manager._spec_store.get_version(
        spec_id, latest_record.base_version,
    )
    if pre_patch_spec is None:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="Could not retrieve the pre-patch spec for undo.",
            errors=["UNDO_FAILED: Could not retrieve the pre-patch spec version."],
            spec_version=session.active_spec_version,
        )
        _record_turn(session_id, user_message, intent_str, resp.assistant_message)
        return resp

    reverse_patch = _patch_engine.undo_engine.create_reverse_patch(
        latest_record.patch,
        pre_patch_spec.model_dump(),
    )
    result = _patch_engine.process_patch(reverse_patch, current_spec)

    if result.errors:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="Undo failed.",
            errors=result.errors,
            spec_version=session.active_spec_version,
        )
        _record_turn(session_id, user_message, intent_str, resp.assistant_message)
        return resp

    if result.new_spec is not None:
        _session_manager.set_active_spec(session_id, result.new_spec)

    resp = TurnResponse(
        session_id=session_id,
        intent=intent_str,
        phase=str(session.current_phase),
        assistant_message=f"Patch {latest_record.patch_id} undone.",
        diff=result.diff.model_dump(mode="json") if result.diff else None,
        impact=result.impact.model_dump(mode="json") if result.impact else None,
        spec_version=result.new_spec.version if result.new_spec else session.active_spec_version,
    )
    _record_turn(session_id, user_message, intent_str, resp.assistant_message)
    return resp


def _handle_explanation_turn(
    session_id: str,
    user_message: str,
    intent_str: str,
) -> TurnResponse:
    """Handle REQUEST_EXPLANATION intent (echo back for now)."""
    session = _session_manager.get_session(session_id)
    assert session is not None

    assistant_msg = f"Your question: {user_message}"
    resp = TurnResponse(
        session_id=session_id,
        intent=intent_str,
        phase=str(session.current_phase),
        assistant_message=assistant_msg,
        spec_version=session.active_spec_version,
    )
    _record_turn(session_id, user_message, intent_str, resp.assistant_message)
    return resp


def _handle_spec_modification(
    session_id: str,
    user_message: str,
    intent_str: str,
) -> TurnResponse:
    """Handle CREATE_SPEC / MODIFY_EXISTING_SPEC intent.

    This is the main model-driven path:
    1. Build context using :class:`ContextBuilder`.
    2. Get the patch schema from :class:`SchemaExporter`.
    3. Check for an available LLM client.
    4. If no LLM is available, return a ``MODEL_UNAVAILABLE`` error.
    5. Call the model via :class:`TwoCallStrategy`.
    6. Process the returned patch through :class:`PatchEngine`.
    7. If blocking clarifications exist, set as pending and return them.
    8. If the patch is clean, apply it and return diff + impact.
    """
    session = _session_manager.get_session(session_id)
    assert session is not None

    current_spec = _session_manager.get_active_spec(session_id)
    spec_dict = current_spec.model_dump(mode="json") if current_spec else None

    # 1. Build context.
    _context_builder.build_context(
        session=session,
        spec=spec_dict,
        user_message=user_message,
        skills=_DEFAULT_SKILLS,
        openfoam_env=_OPENFOAM_ENV,
    )

    # 2. Get patch schema.
    patch_schema = SimulationSpecPatch.model_json_schema()

    # 3. Check for LLM client.
    llm_client = _get_llm_client()
    if llm_client is None:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="Model unavailable.",
            errors=[
                "MODEL_UNAVAILABLE: No LLM client configured. "
                "Set OPENAI_API_KEY or configure model runtime."
            ],
            spec_version=session.active_spec_version,
        )
        _record_turn(session_id, user_message, intent_str, resp.assistant_message)
        return resp

    # 4. Create the model client callable adapter.
    model_client = _make_model_client_callable(llm_client, session_id)

    # 5. Build the context dict for TwoCallStrategy.
    context_dict: dict[str, Any] = {
        "workflow_phase": str(session.current_phase),
        "confirmed_facts": [f.model_dump() for f in session.confirmed_facts],
        "unresolved_conflicts": [
            c.model_dump() for c in session.unresolved_conflicts
        ],
        "skills": _DEFAULT_SKILLS,
        "openfoam_env": _OPENFOAM_ENV,
    }

    # 6. Execute the two-call strategy.
    candidate_patch, _critic_result, errors = _two_call_strategy.execute(
        model_client=model_client,
        context=context_dict,
        user_message=user_message,
        current_spec=spec_dict or {},
        patch_schema=patch_schema,
    )

    # 7. Record a trace for the model call.
    trace_id = _record_trace(session_id)

    if errors:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="Model call failed.",
            errors=errors,
            spec_version=session.active_spec_version,
        )
        _record_turn(
            session_id, user_message, intent_str, resp.assistant_message,
            trace_ids=[trace_id],
        )
        return resp

    if candidate_patch is None:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="The model did not produce a valid patch after retries.",
            errors=[
                "MODEL_NO_PATCH: The model did not produce a valid "
                "patch after retries."
            ],
            spec_version=session.active_spec_version,
        )
        _record_turn(
            session_id, user_message, intent_str, resp.assistant_message,
            trace_ids=[trace_id],
        )
        return resp

    # 8. Validate the candidate patch into a SimulationSpecPatch.
    try:
        patch = SimulationSpecPatch.model_validate(candidate_patch)
    except Exception as exc:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="Patch validation failed.",
            errors=[f"PATCH_VALIDATION_ERROR: {exc}"],
            spec_version=session.active_spec_version,
        )
        _record_turn(
            session_id, user_message, intent_str, resp.assistant_message,
            trace_ids=[trace_id],
        )
        return resp

    # 9. If there is no current spec, we cannot apply the patch yet —
    #    set it as pending for the user to review after providing a base.
    if current_spec is None:
        _session_manager.set_pending_patch(session_id, patch)
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message=(
                "No active spec exists. The patch has been set as "
                "pending — create or migrate a spec first, then confirm."
            ),
            pending_patch=patch.model_dump(mode="json"),
            spec_version=session.active_spec_version,
        )
        _record_turn(
            session_id, user_message, intent_str, resp.assistant_message,
            patch_id=patch.patch_id, trace_ids=[trace_id],
        )
        return resp

    # 10. Process the patch through the PatchEngine.
    result = _patch_engine.process_patch(patch, current_spec)

    if result.errors:
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message="Patch validation failed.",
            errors=result.errors,
            spec_version=session.active_spec_version,
        )
        _record_turn(
            session_id, user_message, intent_str, resp.assistant_message,
            patch_id=patch.patch_id, trace_ids=[trace_id],
        )
        return resp

    # 11. Blocking clarifications — set as pending.
    if result.clarifications:
        _session_manager.set_pending_patch(session_id, patch)
        resp = TurnResponse(
            session_id=session_id,
            intent=intent_str,
            phase=str(session.current_phase),
            assistant_message=(
                patch.assistant_message
                or "Clarification needed before applying the patch."
            ),
            pending_patch=patch.model_dump(mode="json"),
            clarifications=[c.model_dump(mode="json") for c in result.clarifications],
            spec_version=session.active_spec_version,
        )
        _record_turn(
            session_id, user_message, intent_str, resp.assistant_message,
            patch_id=patch.patch_id, trace_ids=[trace_id],
        )
        return resp

    # 12. Patch is clean — apply and return diff + impact.
    if result.new_spec is not None:
        _session_manager.set_active_spec(session_id, result.new_spec)

    diff_dict, impact_dict, _ = _serialize_patch_result(result)
    resp = TurnResponse(
        session_id=session_id,
        intent=intent_str,
        phase=str(session.current_phase),
        assistant_message=(
            patch.assistant_message or "Patch applied successfully."
        ),
        diff=diff_dict,
        impact=impact_dict,
        spec_version=result.new_spec.version if result.new_spec else session.active_spec_version,
    )
    _record_turn(
        session_id, user_message, intent_str, resp.assistant_message,
        patch_id=patch.patch_id, trace_ids=[trace_id],
    )
    return resp
