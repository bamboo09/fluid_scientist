"""API router for the v5 study-decomposer draft workflow.

This module exposes REST endpoints that wire together the new v5 components:
study decomposition, draft session management, experiment draft generation,
change proposals, case plan generation, and code extension workflow.

The router is designed to be included by the main FastAPI application.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from fluid_scientist.capabilities.models import CapabilityRegistry
from fluid_scientist.case_plan.compiler import NativeCaseCompiler
from fluid_scientist.case_plan.generator import CasePlanGenerator
from fluid_scientist.case_plan.models import CasePlan
from fluid_scientist.code_extension.spec import (
    CodeExtensionSpec,
    CodeExtensionWorkflow,
)
from fluid_scientist.draft.apply_executor import (
    ApplyProposalExecutor,
    ProposalNotPendingError,
    ProposalVersionMismatchError,
)
from fluid_scientist.draft.change_agent import DraftChangeAgent
from fluid_scientist.draft.draft_generator import DraftGenerator
from fluid_scientist.draft.models import (
    ChangeProposal,
    DraftStatus,
    ExperimentDraft,
    ValidationResult,
)
from fluid_scientist.draft.validator import DraftValidator
from fluid_scientist.draft_session.clarification import ClarificationPlanner
from fluid_scientist.draft_session.input_router import InputRouter
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
    SessionMessage,
)
from fluid_scientist.draft_session.persistence import JsonSessionPersistence
from fluid_scientist.draft_session.session_store import DraftSessionStore
from fluid_scientist.draft_session.state_machine import (
    DraftSessionStateMachine,
    TransitionError,
)
from fluid_scientist.draft_session.v5_storage import V5Repository
from fluid_scientist.llm import LLMClient
from fluid_scientist.study_decomposition.ambiguity_detector import AmbiguityDetector
from fluid_scientist.study_decomposition.capability_checker import (
    CapabilityPreChecker,
    PriorityRanker,
)
from fluid_scientist.study_decomposition.models import (
    BatchStudyPlan,
    StudyIntent,
)
from fluid_scientist.study_decomposition.physics_extractor import PhysicsFrameExtractor
from fluid_scientist.study_decomposition.splitter import StudySplitter

router = APIRouter(prefix="/api/v5", tags=["v5-workflow"])

# Shared JSON-file-backed session persistence (so sessions survive restarts)
_session_persistence = JsonSessionPersistence()
_session_store = DraftSessionStore(persistence=_session_persistence)

# SQLite-backed repository for all V5 workflow entities (drafts, proposals,
# case plans, batches, compiled cases, code extensions, audit events).
# Replaces the former in-memory dictionaries so data survives restarts.
_repo = V5Repository()

# Shared LLM client (defaults to mock backend)
_llm_client = LLMClient()

# Shared service instances
_splitter = StudySplitter()
_extractor = PhysicsFrameExtractor()
_detector = AmbiguityDetector()
_checker = CapabilityPreChecker()
_ranker = PriorityRanker()
_draft_generator = DraftGenerator(llm_client=_llm_client)
_validator = DraftValidator()
_change_agent = DraftChangeAgent()
_apply_executor = ApplyProposalExecutor()
_case_plan_generator = CasePlanGenerator()
_state_machine = DraftSessionStateMachine()
_input_router = InputRouter()
_extension_workflow = CodeExtensionWorkflow()
_case_compiler = NativeCaseCompiler()
_capability_registry = CapabilityRegistry()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


def get_session_persistence() -> JsonSessionPersistence:
    """Return the module-level session persistence instance (for testing)."""
    return _session_persistence


class CreateSessionRequest(BaseModel):
    user_id: str | None = None


class UserMessageRequest(BaseModel):
    session_id: str
    message: str


class StudySelectionRequest(BaseModel):
    session_id: str
    study_id: str


class GenerateDraftRequest(BaseModel):
    session_id: str
    study: StudyIntent


class ConfirmDraftRequest(BaseModel):
    session_id: str
    draft_id: str


class DraftChangeRequest(BaseModel):
    session_id: str
    draft_id: str
    user_message: str


class ApplyProposalRequest(BaseModel):
    session_id: str
    proposal_id: str


class GenerateCasePlanRequest(BaseModel):
    session_id: str
    draft_id: str


class SessionResponse(BaseModel):
    session: DraftSession
    messages: list[SessionMessage] = Field(default_factory=list)


class DecomposeRequest(BaseModel):
    message: str


class SelectStudyRequest(BaseModel):
    session_id: str
    study_id: str


class CloneDraftRequest(BaseModel):
    session_id: str | None = None


class CancelProposalRequest(BaseModel):
    session_id: str | None = None


class CodeExtensionGenerateRequest(BaseModel):
    session_id: str | None = None


class CodeExtensionTestRequest(BaseModel):
    session_id: str | None = None


class CodeExtensionReviewRequest(BaseModel):
    approved: bool = True
    review_notes: str = ""


class CodeExtensionRegisterRequest(BaseModel):
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------


def _decompose_message(message: str) -> BatchStudyPlan:
    """Decompose a user message into a BatchStudyPlan.

    This is the shared decomposition logic used by both the
    ``/sessions/{id}/messages`` endpoint (when a research request is
    detected) and the standalone ``/studies/decompose`` endpoint.
    """
    studies = _splitter.split(message)
    study_intents = []
    for study_text in studies:
        frame = _extractor.extract(study_text)
        params = _extractor.extract_parameters(study_text)
        observables = _extractor.extract_observables(study_text)
        ics, bcs = _extractor.extract_conditions(study_text)
        goals = _extractor.extract_analysis_goals(study_text)

        study = StudyIntent(
            study_id=f"study_{uuid.uuid4().hex[:8]}",
            batch_id=None,
            title=study_text[:60],
            raw_text=study_text,
            study_type=frame.geometry_type or "unknown",
            research_objective=study_text,
            geometry={
                "type": frame.geometry_type or "unknown",
                "inclined": frame.is_inclined,
                "near_wall": frame.near_wall,
            },
            physical_models={
                "dimension": frame.dimension,
                "temporal": frame.temporal_type,
                "turbulent": frame.flow_regime == "turbulent",
                "inclined": frame.is_inclined,
                "moving_body": frame.is_moving_body,
                "thermal": frame.has_thermal,
                "buoyancy": frame.has_buoyancy,
                "density_stratification": frame.has_density_stratification,
            },
            initial_conditions=ics,
            boundary_conditions=bcs,
            known_parameters=params,
            observables=observables,
            analysis_goals=goals,
        )
        ambiguities = _detector.detect(study)
        study.ambiguity_report = ambiguities
        study_intents.append(study)

    # Check capabilities and rank
    check_results = {
        s.study_id: _checker.check(s) for s in study_intents
    }
    # Propagate readiness_level from capability check to each study
    for s in study_intents:
        cr = check_results.get(s.study_id)
        if cr is not None:
            s.readiness_level = cr.readiness_level
            s.capability_requirements = cr.supported_capabilities
            s.likely_missing_capabilities = cr.missing_capabilities
    ranked = _ranker.rank(study_intents, check_results)

    batch = BatchStudyPlan(
        batch_id=f"batch_{uuid.uuid4().hex[:8]}",
        input_type="batch_study" if len(studies) > 1 else "single_study",
        studies=ranked,
        batch_summary=f"识别到 {len(ranked)} 个研究任务",
    )
    _repo.save_batch(batch)
    return batch


def _decompose_single_study(message: str) -> tuple[StudyIntent, Any]:
    """Decompose a message into a single StudyIntent + capability check."""
    frame = _extractor.extract(message)
    params = _extractor.extract_parameters(message)
    observables = _extractor.extract_observables(message)
    ics, bcs = _extractor.extract_conditions(message)
    goals = _extractor.extract_analysis_goals(message)

    study = StudyIntent(
        study_id=f"study_{uuid.uuid4().hex[:8]}",
        title=message[:60],
        raw_text=message,
        study_type=frame.geometry_type or "unknown",
        research_objective=message,
        geometry={
            "type": frame.geometry_type or "unknown",
            "inclined": frame.is_inclined,
            "near_wall": frame.near_wall,
        },
        physical_models={
            "dimension": frame.dimension,
            "temporal": frame.temporal_type,
            "turbulent": frame.flow_regime == "turbulent",
        },
        initial_conditions=ics,
        boundary_conditions=bcs,
        known_parameters=params,
        observables=observables,
        analysis_goals=goals,
    )
    ambiguities = _detector.detect(study)
    study.ambiguity_report = ambiguities
    check_result = _checker.check(study)
    study.readiness_level = check_result.readiness_level
    return study, check_result


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(request: CreateSessionRequest) -> SessionResponse:
    """Create a new draft session."""
    session = DraftSession(
        session_id=f"session_{uuid.uuid4().hex[:12]}",
        user_id=request.user_id,
        status=DraftSessionStatus.COLLECTING_INTENT,
    )
    _session_store.create_session(session)
    return SessionResponse(session=session)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str) -> SessionResponse:
    """Get session details and messages."""
    session = _session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = _session_store.get_messages(session_id)
    return SessionResponse(session=session, messages=messages)


@router.get("/sessions-list")
def list_sessions() -> dict:
    """List all persisted session IDs."""
    return {"session_ids": _session_persistence.list_sessions()}


# ------------------------------------------------------------------
# Model configuration (v5)
# ------------------------------------------------------------------

# Suggested model IDs per provider.
_PROVIDER_BASE_URLS = {
    "glm": "https://open.bigmodel.cn/api/paas/v4/",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}
_PROVIDER_MODELS = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4"],
    "glm": ["glm-4-plus", "glm-4", "glm-4-flash", "glm-4-long"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
}


class ModelConfigRequest(BaseModel):
    """Request body for configuring the v5 LLM model."""
    provider: str = Field(..., description="openai / glm / deepseek")
    model: str = Field(..., description="Model ID, e.g. glm-4-flash")
    api_key: str = Field(..., description="API key for the provider")


class ModelConfigView(BaseModel):
    """Current model configuration view."""
    configured: bool = False
    provider: str | None = None
    model: str | None = None
    is_mock: bool = True
    suggested_models: dict[str, list[str]] | None = None


@router.get("/model-config", response_model=ModelConfigView)
def get_v5_model_config() -> ModelConfigView:
    """Return the current v5 LLM model configuration."""
    return ModelConfigView(
        configured=not _llm_client.is_mock,
        provider=_llm_client.provider,
        model=_llm_client.model_name,
        is_mock=_llm_client.is_mock,
        suggested_models=_PROVIDER_MODELS,
    )


@router.post("/model-config", response_model=ModelConfigView)
def configure_v5_model(request: ModelConfigRequest) -> ModelConfigView:
    """Configure the v5 LLM model to use a real provider.

    The API key is kept only in memory and never persisted to disk.
    """
    if request.provider not in _PROVIDER_BASE_URLS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider '{request.provider}'. Supported: {list(_PROVIDER_BASE_URLS.keys())}",
        )
    if not request.api_key or len(request.api_key) < 5:
        raise HTTPException(status_code=422, detail="API key is too short")

    _llm_client.reconfigure(
        provider=request.provider,
        model_name=request.model,
        api_key=request.api_key,
        base_url=_PROVIDER_BASE_URLS[request.provider],
    )
    return ModelConfigView(
        configured=not _llm_client.is_mock,
        provider=_llm_client.provider,
        model=_llm_client.model_name,
        is_mock=_llm_client.is_mock,
        suggested_models=_PROVIDER_MODELS,
    )


@router.get("/sessions/{session_id}/llm-records")
def get_llm_records(session_id: str) -> dict[str, Any]:
    """Return LLM call records for a session (debug/auditing endpoint)."""
    session = _session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    records = _llm_client.get_records(session_id)
    return {
        "session_id": session_id,
        "count": len(records),
        "records": [r.model_dump(mode="json") for r in records],
    }


@router.post("/sessions/{session_id}/messages")
def send_message(
    session_id: str, request: UserMessageRequest
) -> dict[str, Any]:
    """Process a user message and return routing result + response actions."""
    session = _session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Route the input
    route = _input_router.route(request.message, session)

    # Store the user message
    msg = SessionMessage(
        message_id=f"msg_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        role="user",
        message_type=_route_to_message_type(route.input_type),
        content=request.message,
    )
    _session_store.add_message(msg)

    # Process based on route
    response_actions: list[dict] = []

    if route.input_type == "batch_research_request":
        # Run deterministic decomposition first (primary path).
        batch = _decompose_message(request.message)

        # Optionally invoke LLM for study decomposition; merge any additional
        # studies it suggests that the deterministic splitter did not find.
        llm_studies_output: list[dict] | None = None
        with contextlib.suppress(Exception):
            llm_output, _record = _llm_client.call(
                purpose="study_decomposition",
                prompt_name="study_decomposer",
                system_prompt="Decompose the user's research request into one or more CFD studies.",
                user_message=request.message,
                session_id=session_id,
                input_refs=[msg.message_id],
            )
            if isinstance(llm_output, dict):
                studies_val = llm_output.get("studies")
                if isinstance(studies_val, list):
                    llm_studies_output = studies_val

        if llm_studies_output:
            existing_titles = {s.title.strip().lower() for s in batch.studies}
            for llm_study in llm_studies_output:
                title = (llm_study.get("title") or "").strip()
                if title and title.lower() not in existing_titles:
                    batch.studies.append(StudyIntent(
                        study_id=f"llm_{uuid.uuid4().hex[:8]}",
                        title=title[:100],
                        raw_text=title,
                        study_type=llm_study.get("study_type", "unknown"),
                        research_objective=llm_study.get("research_objective", title),
                        geometry=llm_study.get("geometry", {"type": "unknown"}),
                        physical_models=llm_study.get("physical_models", {}),
                        confidence=llm_study.get("confidence", 0.3),
                    ))
                    existing_titles.add(title.lower())

        # Transition session to batch_review
        try:
            session = _state_machine.transition(
                session, DraftSessionStatus.BATCH_REVIEW
            )
            session.batch_id = batch.batch_id
            _session_store.update_session(session)
        except TransitionError:
            pass

        response_actions.append({
            "action": "batch_review",
            "batch": batch.model_dump(),
        })

        # Build clarification questions for each study's ambiguities
        clarification_planner = ClarificationPlanner()
        all_questions: list[dict] = []
        for study in batch.studies:
            ambiguities = study.ambiguity_report or []
            questions = clarification_planner.plan(ambiguities)
            for q in questions:
                all_questions.append({
                    "study_id": study.study_id,
                    **q.model_dump(),
                })
        if all_questions:
            response_actions.append({
                "action": "clarification_questions",
                "questions": all_questions,
            })

    elif route.input_type == "new_research_request":
        # Single study — also create a batch so the frontend can use
        # /batches/{batch_id}/select-study uniformly.
        study, check_result = _decompose_single_study(request.message)

        batch = BatchStudyPlan(
            batch_id=f"batch_{uuid.uuid4().hex[:12]}",
            input_type="single_study",
            studies=[study],
            batch_summary=study.title or study.research_objective[:80],
            suggested_next_action="select_one_to_continue",
        )
        _repo.save_batch(batch)
        try:
            session = _state_machine.transition(
                session, DraftSessionStatus.BATCH_REVIEW
            )
            session.batch_id = batch.batch_id
            _session_store.update_session(session)
        except TransitionError:
            pass

        response_actions.append({
            "action": "batch_review",
            "batch": batch.model_dump(),
        })
        response_actions.append({
            "action": "study_decomposed",
            "study": study.model_dump(),
            "capability_check": check_result.model_dump(),
        })

    elif route.input_type == "study_selection":
        response_actions.append({
            "action": "study_selected",
            "message": "请确认研究任务以生成实验草案",
        })

    elif route.input_type == "proposal_confirmation":
        if session.pending_proposal_id:
            response_actions.append({
                "action": "apply_proposal",
                "proposal_id": session.pending_proposal_id,
            })

    elif route.input_type == "proposal_cancel":
        if session.pending_proposal_id:
            proposal = _repo.get_proposal(session.pending_proposal_id)
            if proposal:
                proposal.status = "cancelled"
                _repo.save_proposal(proposal)
            session.pending_proposal_id = None
            _session_store.update_session(session)
            response_actions.append({"action": "proposal_cancelled"})

    return {
        "route": route.model_dump(),
        "actions": response_actions,
        "message_id": msg.message_id,
    }


# ---------------------------------------------------------------------------
# Draft endpoints
# ---------------------------------------------------------------------------


@router.post("/drafts/generate", response_model=ExperimentDraft)
def generate_draft(request: GenerateDraftRequest) -> ExperimentDraft:
    """Generate an experiment draft from a study intent."""
    draft = _draft_generator.generate(request.study)
    draft.session_id = request.session_id
    _repo.save_draft(draft)

    session = _session_store.get_session(request.session_id)
    if session:
        session.current_draft_id = draft.draft_id
        session.current_draft_version = draft.version
        with contextlib.suppress(TransitionError):
            session = _state_machine.transition(
                session, DraftSessionStatus.DRAFT_READY
            )
        _session_store.update_session(session)

    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=request.session_id,
        event_type="draft_generated",
        payload={"draft_id": draft.draft_id, "version": draft.version},
    )
    return draft


@router.get("/drafts/{draft_id}", response_model=ExperimentDraft)
def get_draft(draft_id: str) -> ExperimentDraft:
    """Get a draft by ID."""
    draft = _repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.post("/drafts/{draft_id}/validate", response_model=ValidationResult)
def validate_draft(draft_id: str) -> ValidationResult:
    """Validate a draft."""
    draft = _repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    result = _validator.validate(draft)
    draft.validation_result = result.model_dump()
    _repo.save_draft(draft)
    return result


@router.post("/drafts/{draft_id}/confirm", response_model=ExperimentDraft)
def confirm_draft(draft_id: str, request: ConfirmDraftRequest) -> ExperimentDraft:
    """Confirm a draft, freezing it for compilation.

    Blocking issues are recorded on the draft as advisory information but
    do NOT prevent confirmation — the user can confirm and then resolve
    issues via the change-proposal workflow.
    """
    draft = _repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Run validation but don't block — store results as advisory
    result = _validator.validate(draft)
    draft.validation_result = result.model_dump()
    # Store blocking issues on the draft so the UI can display them
    if not result.valid:
        draft.blocking_issues = result.blocking_issues
    _repo.save_draft(draft)

    confirmed = draft.confirm()
    _repo.save_draft(confirmed)

    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=request.session_id,
        event_type="draft_confirmed",
        payload={"draft_id": draft_id, "version": confirmed.version},
    )

    session = _session_store.get_session(request.session_id)
    if session:
        with contextlib.suppress(TransitionError):
            session = _state_machine.transition(
                session, DraftSessionStatus.CONFIRMED
            )
        _session_store.update_session(session)

    return confirmed


# ---------------------------------------------------------------------------
# Proposal endpoints
# ---------------------------------------------------------------------------


@router.post("/drafts/{draft_id}/changes", response_model=ChangeProposal)
def request_draft_change(
    draft_id: str, request: DraftChangeRequest
) -> ChangeProposal:
    """Generate a change proposal for a draft.

    If the draft is read-only (locked/confirmed), it is automatically
    cloned to a new editable version before proposal generation.
    """
    draft = _repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Auto-clone if draft is read-only / locked / confirmed
    if draft.is_read_only() or draft.locked or draft.status == DraftStatus.CONFIRMED:
        new_draft_id = f"draft_{uuid.uuid4().hex[:12]}"
        cloned = draft.clone(new_draft_id)
        _repo.save_draft(cloned)
        draft = cloned

        session = _session_store.get_session(request.session_id)
        if session:
            session.current_draft_id = cloned.draft_id
            session.current_draft_version = cloned.version
            # After cloning a confirmed/locked draft, transition session back
            # to DRAFT_READY so the workbench knows the draft is editable.
            with contextlib.suppress(TransitionError):
                session = _state_machine.transition(
                    session, DraftSessionStatus.DRAFT_READY
                )
            _session_store.update_session(session)

    proposal = _change_agent.generate(
        draft, request.user_message, request.session_id
    )
    _repo.save_proposal(proposal)

    session = _session_store.get_session(request.session_id)
    if session:
        session.pending_proposal_id = proposal.proposal_id
        with contextlib.suppress(TransitionError):
            session = _state_machine.transition(
                session, DraftSessionStatus.PROPOSAL_PENDING
            )
        _session_store.update_session(session)

    return proposal


@router.post("/proposals/{proposal_id}/apply", response_model=ExperimentDraft)
def apply_proposal(
    proposal_id: str, request: ApplyProposalRequest
) -> ExperimentDraft:
    """Apply a confirmed proposal to create a new draft version."""
    proposal = _repo.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    draft = _repo.get_draft(proposal.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        new_draft, _ = _apply_executor.apply(draft, proposal)
    except ProposalVersionMismatchError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ProposalNotPendingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _repo.save_draft(new_draft)

    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=request.session_id,
        event_type="proposal_applied",
        payload={
            "proposal_id": proposal_id,
            "old_draft_id": proposal.draft_id,
            "new_draft_id": new_draft.draft_id,
            "new_version": new_draft.version,
        },
    )

    session = _session_store.get_session(request.session_id)
    if session:
        session.pending_proposal_id = None
        session.current_draft_id = new_draft.draft_id
        session.current_draft_version = new_draft.version
        with contextlib.suppress(TransitionError):
            session = _state_machine.transition(
                session, DraftSessionStatus.DRAFT_READY
            )
        _session_store.update_session(session)

    return new_draft


# ---------------------------------------------------------------------------
# Case plan endpoints
# ---------------------------------------------------------------------------


@router.post("/case-plans/generate", response_model=CasePlan)
def generate_case_plan(request: GenerateCasePlanRequest) -> CasePlan:
    """Generate a case plan from a confirmed draft."""
    draft = _repo.get_draft(request.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        case_plan = _case_plan_generator.generate(draft)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _repo.save_case_plan(case_plan)

    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=request.session_id,
        event_type="case_plan_generated",
        payload={"case_plan_id": case_plan.case_plan_id, "draft_id": request.draft_id},
    )

    session = _session_store.get_session(request.session_id)
    if session:
        with contextlib.suppress(TransitionError):
            session = _state_machine.transition(
                session, DraftSessionStatus.CASE_PLANNING
            )
        _session_store.update_session(session)

    return case_plan


@router.get("/case-plans/{case_plan_id}", response_model=CasePlan)
def get_case_plan(case_plan_id: str) -> CasePlan:
    """Get a case plan by ID."""
    plan = _repo.get_case_plan(case_plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Case plan not found")
    return plan


# ---------------------------------------------------------------------------
# Code extension endpoints
# ---------------------------------------------------------------------------


@router.post("/code-extensions", response_model=CodeExtensionSpec)
def create_code_extension(
    missing_capability: dict,
    session_id: str = "",
    draft_id: str | None = None,
) -> CodeExtensionSpec:
    """Create a code extension spec from a missing capability."""
    spec = _extension_workflow.create_spec(
        missing_capability, session_id, draft_id
    )
    _repo.save_extension(spec)
    return spec


@router.get("/code-extensions/{extension_id}", response_model=CodeExtensionSpec)
def get_code_extension(extension_id: str) -> CodeExtensionSpec:
    """Get a code extension by ID."""
    spec = _repo.get_extension(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")
    return spec


@router.post("/code-extensions/{extension_id}/approve", response_model=CodeExtensionSpec)
def approve_code_extension(
    extension_id: str, review_notes: str = ""
) -> CodeExtensionSpec:
    """Approve a code extension."""
    spec = _repo.get_extension(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")
    try:
        approved = _extension_workflow.approve(spec, review_notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _repo.save_extension(approved)
    return approved


# ---------------------------------------------------------------------------
# Study decomposition (standalone)
# ---------------------------------------------------------------------------


@router.post("/studies/decompose", response_model=BatchStudyPlan)
def decompose_studies(request: DecomposeRequest) -> BatchStudyPlan:
    """Standalone decomposition: turn a user message into a BatchStudyPlan."""
    return _decompose_message(request.message)


# ---------------------------------------------------------------------------
# Batch endpoints
# ---------------------------------------------------------------------------


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    """Get a batch study plan by ID."""
    batch = _repo.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch.model_dump()


@router.post("/batches/{batch_id}/select-study")
def select_study(batch_id: str, request: SelectStudyRequest) -> dict[str, Any]:
    """Select a study from a batch and trigger draft generation.

    Stores the selection in the session, transitions to draft generation.
    """
    batch = _repo.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    study = None
    for s in batch.studies:
        if s.study_id == request.study_id:
            study = s
            break
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found in batch")

    # Guard against selecting studies that are not yet compilable.
    if study.readiness_level == "not_compilable_yet":
        # Collect blocking issues from ambiguity report and missing capabilities.
        blocking_ambiguities = [
            a.model_dump()
            for a in (study.ambiguity_report or [])
            if a.severity == "blocking_for_case_generation"
        ]
        blocking_caps = [
            c for c in (study.likely_missing_capabilities or [])
            if c.get("severity") == "blocking"
        ]
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Study is not compilable yet",
                "study_id": request.study_id,
                "blocking_issues": blocking_ambiguities + blocking_caps,
                "recommendation": "Resolve blocking issues or select a different study",
            },
        )

    session = _session_store.get_session(request.session_id)
    if session:
        session.selected_study_id = request.study_id
        _session_store.update_session(session)

    # Generate draft from the selected study
    draft = _draft_generator.generate(study)
    draft.session_id = request.session_id
    _repo.save_draft(draft)

    if session:
        session.current_draft_id = draft.draft_id
        session.current_draft_version = draft.version
        with contextlib.suppress(TransitionError):
            session = _state_machine.transition(
                session, DraftSessionStatus.DRAFT_READY
            )
        _session_store.update_session(session)

    return {
        "batch_id": batch_id,
        "selected_study_id": request.study_id,
        "draft": draft.model_dump(),
    }


# ---------------------------------------------------------------------------
# Draft clone endpoint
# ---------------------------------------------------------------------------


@router.post("/drafts/{draft_id}/clone", response_model=ExperimentDraft)
def clone_draft(
    draft_id: str, request: CloneDraftRequest | None = None
) -> ExperimentDraft:
    """Clone a draft to a new editable version."""
    draft = _repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    new_draft_id = f"draft_{uuid.uuid4().hex[:12]}"
    cloned = draft.clone(new_draft_id)
    _repo.save_draft(cloned)

    if request and request.session_id:
        session = _session_store.get_session(request.session_id)
        if session:
            session.current_draft_id = cloned.draft_id
            session.current_draft_version = cloned.version
            _session_store.update_session(session)

    return cloned


# ---------------------------------------------------------------------------
# Additional proposal endpoints
# ---------------------------------------------------------------------------


@router.get("/proposals/{proposal_id}", response_model=ChangeProposal)
def get_proposal(proposal_id: str) -> ChangeProposal:
    """Get a proposal by ID."""
    proposal = _repo.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal


@router.post("/proposals/{proposal_id}/cancel", response_model=ChangeProposal)
def cancel_proposal(
    proposal_id: str, request: CancelProposalRequest | None = None
) -> ChangeProposal:
    """Cancel a pending proposal."""
    proposal = _repo.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel proposal in status '{proposal.status}'",
        )
    proposal.status = "cancelled"
    _repo.save_proposal(proposal)

    if request and request.session_id:
        session = _session_store.get_session(request.session_id)
        if session and session.pending_proposal_id == proposal_id:
            session.pending_proposal_id = None
            _session_store.update_session(session)

    return proposal


# ---------------------------------------------------------------------------
# Case plan compile endpoint
# ---------------------------------------------------------------------------


@router.post("/case-plans/{case_plan_id}/compile")
def compile_case_plan(case_plan_id: str) -> dict[str, Any]:
    """Compile a case plan using NativeCaseCompiler and write to disk.

    Returns the compiled case structure and the directory path where files
    were written.
    """
    case_plan = _repo.get_case_plan(case_plan_id)
    if not case_plan:
        raise HTTPException(status_code=404, detail="Case plan not found")

    try:
        compiled = _case_compiler.compile(case_plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Write to a temporary directory
    case_dir = tempfile.mkdtemp(prefix=f"fluid_case_{case_plan_id}_")
    files_written = _write_case_to_disk(compiled, case_dir)

    _repo.save_compiled_case(case_plan_id, case_dir, compiled)

    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=None,
        event_type="case_compiled",
        payload={"case_plan_id": case_plan_id, "case_dir": case_dir},
    )

    return {
        "case_plan_id": case_plan_id,
        "case_dir": case_dir,
        "files": list(files_written.keys()),
        "file_count": len(files_written),
        "compiled_structure": compiled,
    }


def _write_case_to_disk(compiled: dict[str, Any], case_dir: str) -> dict[str, str]:
    """Write the compiled case structure to disk as valid OpenFOAM dictionary files.

    Returns a dict mapping relative file paths to their content for inspection.
    """
    from fluid_scientist.case_plan.foam_writer import compile_to_files

    files = compile_to_files(compiled)
    os.makedirs(case_dir, exist_ok=True)
    for rel_path, content in files.items():
        full_path = os.path.join(case_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
    return files


# ---------------------------------------------------------------------------
# Capabilities endpoint
# ---------------------------------------------------------------------------


@router.get("/capabilities")
def list_capabilities() -> dict[str, Any]:
    """List registered capabilities in the capability registry."""
    return {
        "capabilities": _capability_registry.list_capabilities(),
        "count": len(_capability_registry.list_capabilities()),
    }


# ---------------------------------------------------------------------------
# Code extension generation / test / review / register endpoints
# ---------------------------------------------------------------------------


@router.post("/code-extensions/{extension_id}/generate", response_model=CodeExtensionSpec)
def generate_code_extension(
    extension_id: str, request: CodeExtensionGenerateRequest | None = None
) -> CodeExtensionSpec:
    """Trigger code generation for a code extension via the LLM client.

    Transitions ``spec_reviewed`` -> ``generating`` -> ``generated`` and
    stores the code returned by the LLM.  When the LLM is unavailable
    the endpoint falls back to a deterministic placeholder string so
    that the workflow can still complete.
    """
    spec = _repo.get_extension(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")

    # Transition to generating
    try:
        spec = spec.transition_to("generating")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Default placeholder (used when LLM is unavailable / fails).
    generated_code = (
        f"# Auto-generated code extension: {spec.extension_id}\n"
        f"# Type: {spec.extension_type}\n"
        f"# Requirement: {spec.requirement}\n"
        f"# TODO: Replace with actual LLM-generated code\n\n"
        f"def run(*args, **kwargs):\n"
        f"    raise NotImplementedError(\n"
        f"        'Code generation placeholder for {spec.extension_id}'\n"
        f"    )\n"
    )
    review_notes = ""
    session_id = (
        request.session_id if request and request.session_id else spec.session_id
    )

    # Invoke LLM for code generation (best-effort).
    try:
        output, _record = _llm_client.call(
            purpose="code_generation",
            prompt_name="code_extension_generate",
            system_prompt="You generate Python code for fluid_scientist code extensions.",
            user_message=(
                f"Extension type: {spec.extension_type}\n"
                f"Requirement: {spec.requirement}\n"
                f"Files to modify: {spec.files_to_create_or_modify}"
            ),
            session_id=session_id,
        )
        # If LLM produced a code block, use it.
        if isinstance(output, dict):
            llm_code = output.get("code") or output.get("generated_code")
            if llm_code:
                generated_code = str(llm_code)
            llm_notes = output.get("notes") or output.get("review_notes")
            if llm_notes:
                review_notes = str(llm_notes)
    except Exception:  # noqa: BLE001 - LLM failures must not break the endpoint
        pass  # LLM is best-effort

    try:
        spec = spec.transition_to("generated")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    spec = spec.model_copy(
        update={
            "generated_code": generated_code,
            "review_notes": review_notes,
        }
    )

    _repo.save_extension(spec)
    return spec


@router.post("/code-extensions/{extension_id}/test", response_model=CodeExtensionSpec)
def test_code_extension(
    extension_id: str, request: CodeExtensionTestRequest | None = None
) -> CodeExtensionSpec:
    """Run tests on generated code (mock).

    Transitions generated -> testing -> tested and stores mock test results.
    """
    spec = _repo.get_extension(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")

    try:
        spec = _extension_workflow.run_tests(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _repo.save_extension(spec)
    return spec


@router.post("/code-extensions/{extension_id}/review", response_model=CodeExtensionSpec)
def review_code_extension(
    extension_id: str, request: CodeExtensionReviewRequest
) -> CodeExtensionSpec:
    """Review generated code and approve or reject it via the LLM client.

    If approved, transitions ``tested`` -> ``approved``.
    If rejected, transitions to ``rejected``.

    Before the human-facing approve/reject happens, an LLM review is
    requested in the background to enrich ``review_notes`` with
    automated feedback.  LLM failures are silently ignored so the
    workflow is never blocked.
    """
    spec = _repo.get_extension(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")

    # Invoke LLM for automated code review (best-effort).
    llm_review: dict | None = None
    try:
        output, _record = _llm_client.call(
            purpose="code_review",
            prompt_name="code_extension_review",
            system_prompt="You review code for safety, correctness, and adherence to spec.",
            user_message=(
                f"Extension: {spec.extension_id}\n"
                f"Code: {spec.generated_code[:500] if spec.generated_code else ''}\n"
                f"Spec: {spec.requirement}"
            ),
            session_id=spec.session_id,
        )
        if isinstance(output, dict):
            llm_review = output
    except Exception:  # noqa: BLE001 - LLM failures must not break the endpoint
        pass

    # Compose the final review notes: start with whatever the human
    # supplied and append the LLM feedback (if any) for traceability.
    final_notes = request.review_notes or ""
    if llm_review:
        llm_feedback = (
            llm_review.get("feedback")
            or llm_review.get("notes")
            or llm_review.get("review_notes")
            or ""
        )
        llm_verdict = llm_review.get("verdict") or llm_review.get("status")
        llm_summary_lines: list[str] = []
        if llm_verdict:
            llm_summary_lines.append(f"LLM verdict: {llm_verdict}")
        if llm_feedback:
            llm_summary_lines.append(f"LLM feedback: {llm_feedback}")
        if llm_summary_lines:
            llm_section = " | ".join(llm_summary_lines)
            final_notes = (
                f"{final_notes}\n{llm_section}" if final_notes else llm_section
            )

    if request.approved:
        try:
            spec = _extension_workflow.approve(spec, final_notes)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    else:
        try:
            spec = _extension_workflow.reject(
                spec, final_notes or "Rejected by reviewer"
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    _repo.save_extension(spec)
    return spec


@router.post("/code-extensions/{extension_id}/register", response_model=CodeExtensionSpec)
def register_code_extension(
    extension_id: str, request: CodeExtensionRegisterRequest | None = None
) -> CodeExtensionSpec:
    """Register an approved extension to the capability registry."""
    spec = _repo.get_extension(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")

    try:
        spec = _extension_workflow.register(spec, _capability_registry)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _repo.save_extension(spec)
    return spec


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _route_to_message_type(route_type: str) -> str:
    mapping = {
        "new_research_request": "research_request",
        "batch_research_request": "research_request",
        "study_selection": "study_selection",
        "clarification_answer": "clarification_answer",
        "draft_change_request": "draft_change_request",
        "proposal_confirmation": "proposal_confirmation",
        "proposal_cancel": "proposal_cancel",
        "question_about_draft": "question_about_draft",
        "compile_request": "compile_request",
        "run_request": "compile_request",
        "unknown": "error",
    }
    return mapping.get(route_type, "error")


# ---------------------------------------------------------------------------
# Workstation submission endpoints (Phase 5)
# ---------------------------------------------------------------------------


def _get_workstation_target(request: Request):
    """Get the workstation execution target from the FastAPI app state."""
    targets = getattr(request.app.state, "execution_targets", ())
    if not targets:
        raise HTTPException(
            status_code=503,
            detail="No workstation configured. Use /api/workstation/configure first.",
        )
    # Use the first available target
    return targets[0]


def _package_case_dir_as_tar(case_dir: str) -> bytes:
    """Package a case directory into a tar.gz archive for remote submission."""
    import tarfile
    import io

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for root, _dirs, files in os.walk(case_dir):
            for fname in files:
                full_path = os.path.join(root, fname)
                arcname = os.path.relpath(full_path, case_dir)
                tar.add(full_path, arcname=arcname)
    buffer.seek(0)
    return buffer.getvalue()


@router.post("/cases/{case_plan_id}/submit")
def submit_case_to_workstation(
    case_plan_id: str,
    request: Request,
    target_id: str = "",
) -> dict[str, Any]:
    """Submit a compiled case to the workstation for execution.

    This endpoint packages the compiled OpenFOAM case directory into a
    tar.gz archive and uploads it to the workstation via the existing
    ``WorkstationOpenFOAMTarget.submit_custom`` method.

    The case must have been previously compiled via
    ``POST /api/v5/case-plans/{case_plan_id}/compile``.
    """
    compiled_record = _repo.get_compiled_case(case_plan_id)
    if not compiled_record:
        raise HTTPException(
            status_code=404,
            detail=f"No compiled case found for case_plan_id={case_plan_id}. "
            "Compile the case first via POST /api/v5/case-plans/{case_plan_id}/compile",
        )

    case_dir = compiled_record["case_dir"]
    if not os.path.isdir(case_dir):
        raise HTTPException(
            status_code=500,
            detail=f"Case directory no longer exists: {case_dir}",
        )

    target = _get_workstation_target(request)

    # Generate a unique job ID
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job_id = f"v5-{timestamp}-{case_plan_id[:8]}"

    # Package the case directory
    archive_bytes = _package_case_dir_as_tar(case_dir)

    # Submit via the existing workstation target adapter
    try:
        submit_fn = getattr(target, "submit_custom", None)
        if submit_fn is None:
            raise HTTPException(
                status_code=501,
                detail="Workstation target does not support submit_custom",
            )
        job_record = submit_fn(job_id, archive_bytes)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Workstation submission failed: {e}",
        ) from e

    # Persist job info as an audit event
    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=None,
        event_type="job_submitted",
        payload={
            "job_id": job_id,
            "case_plan_id": case_plan_id,
            "case_dir": case_dir,
            "state": str(getattr(job_record, "state", "unknown")),
        },
    )

    # Return the job record as a dict
    if hasattr(job_record, "model_dump"):
        job_data = job_record.model_dump()
    elif hasattr(job_record, "__dict__"):
        job_data = job_record.__dict__
    else:
        job_data = {"job_id": job_id, "state": str(job_record)}

    return {
        "success": True,
        "job": job_data,
        "job_id": job_id,
        "case_plan_id": case_plan_id,
        "warnings": [],
        "errors": [],
    }


@router.get("/jobs/{job_id}")
def get_job_status(
    job_id: str,
    request: Request,
    target_id: str = "",
) -> dict[str, Any]:
    """Poll the status of a submitted job on the workstation."""
    target = _get_workstation_target(request)

    try:
        job_record = target.status(job_id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to query job status: {e}",
        ) from e

    if hasattr(job_record, "model_dump"):
        return job_record.model_dump()
    elif hasattr(job_record, "__dict__"):
        return job_record.__dict__
    return {"job_id": job_id, "state": str(job_record)}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    request: Request,
    target_id: str = "",
) -> dict[str, Any]:
    """Cancel a running job on the workstation."""
    target = _get_workstation_target(request)

    try:
        job_record = target.cancel(job_id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to cancel job: {e}",
        ) from e

    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=None,
        event_type="job_cancelled",
        payload={"job_id": job_id},
    )

    if hasattr(job_record, "model_dump"):
        return job_record.model_dump()
    elif hasattr(job_record, "__dict__"):
        return job_record.__dict__
    return {"job_id": job_id, "state": "cancelled"}


@router.get("/jobs/{job_id}/results")
def get_job_results(
    job_id: str,
    request: Request,
    target_id: str = "",
) -> dict[str, Any]:
    """Collect results from a completed job on the workstation."""
    target = _get_workstation_target(request)

    try:
        collection = target.collect(job_id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to collect results: {e}",
        ) from e

    if hasattr(collection, "model_dump"):
        result = collection.model_dump()
    elif hasattr(collection, "__dict__"):
        result = collection.__dict__
    elif isinstance(collection, dict):
        result = collection
    else:
        result = {"raw": str(collection)}

    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=None,
        event_type="results_collected",
        payload={"job_id": job_id},
    )

    return result


__all__ = ["router"]
