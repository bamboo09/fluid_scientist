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
from typing import Any

from fastapi import APIRouter, HTTPException, status
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

# Shared in-memory stores (production would use a database)
# Shared JSON-file-backed session persistence (so sessions survive restarts)
_session_persistence = JsonSessionPersistence()
_session_store = DraftSessionStore(persistence=_session_persistence)
_draft_store: dict[str, ExperimentDraft] = {}
_proposal_store: dict[str, ChangeProposal] = {}
_case_plan_store: dict[str, CasePlan] = {}
_extension_store: dict[str, CodeExtensionSpec] = {}
_batch_store: dict[str, BatchStudyPlan] = {}
_case_store: dict[str, dict[str, Any]] = {}  # case_plan_id -> {case_dir, compiled_structure}

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
    ranked = _ranker.rank(study_intents, check_results)

    batch = BatchStudyPlan(
        batch_id=f"batch_{uuid.uuid4().hex[:8]}",
        input_type="batch_study" if len(studies) > 1 else "single_study",
        studies=ranked,
        batch_summary=f"识别到 {len(ranked)} 个研究任务",
    )
    _batch_store[batch.batch_id] = batch
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
        # Record an LLM call for study decomposition (deterministic extractor
        # remains primary; the LLM result is captured for audit/debugging).
        with contextlib.suppress(Exception):
            _llm_client.call(
                purpose="study_decomposition",
                prompt_name="study_decomposer",
                system_prompt="Decompose the user's research request into one or more CFD studies.",
                user_message=request.message,
                session_id=session_id,
                input_refs=[msg.message_id],
            )

        batch = _decompose_message(request.message)

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
        # Single study
        study, check_result = _decompose_single_study(request.message)

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
    """Generate a change proposal for a draft.

    If the draft is read-only (locked/confirmed), it is automatically
    cloned to a new editable version before proposal generation.
    """
    draft = _draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Auto-clone if draft is read-only / locked / confirmed
    if draft.is_read_only() or draft.locked or draft.status == DraftStatus.CONFIRMED:
        new_draft_id = f"draft_{uuid.uuid4().hex[:12]}"
        cloned = draft.clone(new_draft_id)
        _draft_store[cloned.draft_id] = cloned
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
    batch = _batch_store.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch.model_dump()


@router.post("/batches/{batch_id}/select-study")
def select_study(batch_id: str, request: SelectStudyRequest) -> dict[str, Any]:
    """Select a study from a batch and trigger draft generation.

    Stores the selection in the session, transitions to draft generation.
    """
    batch = _batch_store.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    study = None
    for s in batch.studies:
        if s.study_id == request.study_id:
            study = s
            break
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found in batch")

    session = _session_store.get_session(request.session_id)
    if session:
        session.selected_study_id = request.study_id
        _session_store.update_session(session)

    # Generate draft from the selected study
    draft = _draft_generator.generate(study)
    draft.session_id = request.session_id
    _draft_store[draft.draft_id] = draft

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
    draft = _draft_store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    new_draft_id = f"draft_{uuid.uuid4().hex[:12]}"
    cloned = draft.clone(new_draft_id)
    _draft_store[cloned.draft_id] = cloned

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
    proposal = _proposal_store.get(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal


@router.post("/proposals/{proposal_id}/cancel", response_model=ChangeProposal)
def cancel_proposal(
    proposal_id: str, request: CancelProposalRequest | None = None
) -> ChangeProposal:
    """Cancel a pending proposal."""
    proposal = _proposal_store.get(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel proposal in status '{proposal.status}'",
        )
    proposal.status = "cancelled"

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
    case_plan = _case_plan_store.get(case_plan_id)
    if not case_plan:
        raise HTTPException(status_code=404, detail="Case plan not found")

    try:
        compiled = _case_compiler.compile(case_plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Write to a temporary directory
    case_dir = tempfile.mkdtemp(prefix=f"fluid_case_{case_plan_id}_")
    _write_case_to_disk(compiled, case_dir)

    _case_store[case_plan_id] = {
        "case_plan_id": case_plan_id,
        "case_dir": case_dir,
        "compiled_structure": compiled,
    }

    return {
        "case_plan_id": case_plan_id,
        "case_dir": case_dir,
        "compiled": compiled,
    }


def _write_case_to_disk(compiled: dict[str, Any], case_dir: str) -> None:
    """Write the compiled case structure to disk as JSON files."""
    os.makedirs(case_dir, exist_ok=True)
    for section_name, section_content in compiled.items():
        section_dir = os.path.join(case_dir, section_name)
        os.makedirs(section_dir, exist_ok=True)
        if isinstance(section_content, dict):
            for filename, content in section_content.items():
                filepath = os.path.join(section_dir, filename)
                with open(filepath, "w", encoding="utf-8") as f:
                    if isinstance(content, (dict, list)):
                        json.dump(content, f, indent=2, ensure_ascii=False, default=str)
                    else:
                        f.write(str(content))


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
    spec = _extension_store.get(extension_id)
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

    _extension_store[extension_id] = spec
    return spec


@router.post("/code-extensions/{extension_id}/test", response_model=CodeExtensionSpec)
def test_code_extension(
    extension_id: str, request: CodeExtensionTestRequest | None = None
) -> CodeExtensionSpec:
    """Run tests on generated code (mock).

    Transitions generated -> testing -> tested and stores mock test results.
    """
    spec = _extension_store.get(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")

    try:
        spec = _extension_workflow.run_tests(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _extension_store[extension_id] = spec
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
    spec = _extension_store.get(extension_id)
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

    _extension_store[extension_id] = spec
    return spec


@router.post("/code-extensions/{extension_id}/register", response_model=CodeExtensionSpec)
def register_code_extension(
    extension_id: str, request: CodeExtensionRegisterRequest | None = None
) -> CodeExtensionSpec:
    """Register an approved extension to the capability registry."""
    spec = _extension_store.get(extension_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Code extension not found")

    try:
        spec = _extension_workflow.register(spec, _capability_registry)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _extension_store[extension_id] = spec
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


__all__ = ["router"]
