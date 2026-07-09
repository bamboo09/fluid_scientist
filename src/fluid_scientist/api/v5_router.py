"""API router for the v5 study-decomposer draft workflow.

This module exposes REST endpoints that wire together the new v5 components:
study decomposition, draft session management, experiment draft generation,
change proposals, case plan generation, and code extension workflow.

The router is designed to be included by the main FastAPI application.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

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
    ExperimentDraft,
    ValidationResult,
)
from fluid_scientist.draft.validator import DraftValidator
from fluid_scientist.draft_session.input_router import InputRouter
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
    SessionMessage,
)
from fluid_scientist.draft_session.session_store import DraftSessionStore
from fluid_scientist.draft_session.state_machine import (
    DraftSessionStateMachine,
    TransitionError,
)
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

# Shared in-memory stores (production would use a database)
_session_store = DraftSessionStore()
_draft_store: dict[str, ExperimentDraft] = {}
_proposal_store: dict[str, ChangeProposal] = {}
_case_plan_store: dict[str, CasePlan] = {}
_extension_store: dict[str, CodeExtensionSpec] = {}

# Shared service instances
_splitter = StudySplitter()
_extractor = PhysicsFrameExtractor()
_detector = AmbiguityDetector()
_checker = CapabilityPreChecker()
_ranker = PriorityRanker()
_draft_generator = DraftGenerator()
_validator = DraftValidator()
_change_agent = DraftChangeAgent()
_apply_executor = ApplyProposalExecutor()
_case_plan_generator = CasePlanGenerator()
_state_machine = DraftSessionStateMachine()
_input_router = InputRouter()
_extension_workflow = CodeExtensionWorkflow()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------


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
        studies = _splitter.split(request.message)
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
        ranked = _ranker.rank(study_intents, check_results)

        batch = BatchStudyPlan(
            batch_id=f"batch_{uuid.uuid4().hex[:8]}",
            input_type="batch_study" if len(studies) > 1 else "single_study",
            studies=ranked,
            batch_summary=f"识别到 {len(ranked)} 个研究任务",
        )

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

    elif route.input_type == "new_research_request":
        # Single study
        frame = _extractor.extract(request.message)
        params = _extractor.extract_parameters(request.message)
        observables = _extractor.extract_observables(request.message)
        ics, bcs = _extractor.extract_conditions(request.message)
        goals = _extractor.extract_analysis_goals(request.message)

        study = StudyIntent(
            study_id=f"study_{uuid.uuid4().hex[:8]}",
            title=request.message[:60],
            raw_text=request.message,
            study_type=frame.geometry_type or "unknown",
            research_objective=request.message,
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
            proposal = _proposal_store.get(session.pending_proposal_id)
            if proposal:
                proposal.status = "cancelled"
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
    _draft_store[draft.draft_id] = draft

    session = _session_store.get_session(request.session_id)
    if session:
        session.current_draft_id = draft.draft_id
        session.current_draft_version = draft.version
        with contextlib.suppress(TransitionError):
            session = _state_machine.transition(
                session, DraftSessionStatus.DRAFT_READY
            )
        _session_store.update_session(session)

    return draft


@router.get("/drafts/{draft_id}", response_model=ExperimentDraft)
def get_draft(draft_id: str) -> ExperimentDraft:
    """Get a draft by ID."""
    draft = _draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.post("/drafts/{draft_id}/validate", response_model=ValidationResult)
def validate_draft(draft_id: str) -> ValidationResult:
    """Validate a draft."""
    draft = _draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    result = _validator.validate(draft)
    draft.validation_result = result.model_dump()
    _draft_store[draft_id] = draft
    return result


@router.post("/drafts/{draft_id}/confirm", response_model=ExperimentDraft)
def confirm_draft(draft_id: str, request: ConfirmDraftRequest) -> ExperimentDraft:
    """Confirm a draft, freezing it for compilation."""
    draft = _draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    result = _validator.validate(draft)
    if not result.valid:
        raise HTTPException(
            status_code=400,
            detail=f"Draft has blocking issues: {result.blocking_issues}",
        )

    confirmed = draft.confirm()
    _draft_store[draft_id] = confirmed

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
    """Generate a change proposal for a draft."""
    draft = _draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    proposal = _change_agent.generate(
        draft, request.user_message, request.session_id
    )
    _proposal_store[proposal.proposal_id] = proposal

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
    proposal = _proposal_store.get(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    draft = _draft_store.get(proposal.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        new_draft, _ = _apply_executor.apply(draft, proposal)
    except ProposalVersionMismatchError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ProposalNotPendingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _draft_store[new_draft.draft_id] = new_draft

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
    draft = _draft_store.get(request.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        case_plan = _case_plan_generator.generate(draft)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _case_plan_store[case_plan.case_plan_id] = case_plan

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
    plan = _case_plan_store.get(case_plan_id)
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
    _extension_store[spec.extension_id] = spec
    return spec


@router.get("/code-extensions/{extension_id}", response_model=CodeExtensionSpec)
def get_code_extension(extension_id: str) -> CodeExtensionSpec:
    """Get a code extension by ID."""
    spec = _extension_store.get(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")
    return spec


@router.post("/code-extensions/{extension_id}/approve", response_model=CodeExtensionSpec)
def approve_code_extension(
    extension_id: str, review_notes: str = ""
) -> CodeExtensionSpec:
    """Approve a code extension."""
    spec = _extension_store.get(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")
    try:
        approved = _extension_workflow.approve(spec, review_notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _extension_store[extension_id] = approved
    return approved


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


__all__ = ["router"]
