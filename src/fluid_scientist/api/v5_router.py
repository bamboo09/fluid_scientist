"""API router for the v5 study-decomposer draft workflow.

This module exposes REST endpoints that wire together the new v5 components:
study decomposition, draft session management, experiment draft generation,
change proposals, case plan generation, and code extension workflow.

The router is designed to be included by the main FastAPI application.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
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
from fluid_scientist.measurement.boundary_verification_compiler import (
    BoundaryVerificationCompiler,
)
from fluid_scientist.measurement.goal_metric_compiler import GoalMetricCompiler
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
from fluid_scientist.workbench.design_closure_engine import DesignClosureEngine
from fluid_scientist.workbench.experiment_design_synthesizer import (
    ExperimentDesignSynthesizer,
)

router = APIRouter(prefix="/api/v5", tags=["v5-workflow"])

# Shared JSON-file-backed session persistence (so sessions survive restarts)
_session_persistence = JsonSessionPersistence()
_session_store = DraftSessionStore(persistence=_session_persistence)

# SQLite-backed repository for all V5 workflow entities (drafts, proposals,
# case plans, batches, compiled cases, code extensions, audit events).
# Replaces the former in-memory dictionaries so data survives restarts.
_repo = V5Repository()

# Shared LLM client. It is configured by the main model-settings endpoint.
_llm_client: LLMClient | None = None

# Shared service instances
_splitter = StudySplitter()
_extractor = PhysicsFrameExtractor()
_detector = AmbiguityDetector()
_checker = CapabilityPreChecker()
_ranker = PriorityRanker()
_draft_generator = DraftGenerator(llm_client=None)
_validator = DraftValidator()
_change_agent = DraftChangeAgent()
_apply_executor = ApplyProposalExecutor()
_case_plan_generator = CasePlanGenerator()
_state_machine = DraftSessionStateMachine()
_input_router = InputRouter()
_extension_workflow = CodeExtensionWorkflow()
_case_compiler = NativeCaseCompiler()
_capability_registry = CapabilityRegistry()
_design_synthesizer = ExperimentDesignSynthesizer()
_design_closure_engine = DesignClosureEngine()
_goal_metric_compiler = GoalMetricCompiler()
_boundary_metric_compiler = BoundaryVerificationCompiler()

_logger = logging.getLogger(__name__)

_PROVIDER_BASE_URLS = {
    "openai": None,
    "glm": "https://open.bigmodel.cn/api/paas/v4/",
    "deepseek": "https://api.deepseek.com",
}

# Suggested model IDs per provider (for the model-config endpoint).
_PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4"],
    "glm": ["glm-4-plus", "glm-4", "glm-4-flash", "glm-4-long"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
}

# Path for persisting LLM model configuration (provider, model, api_key).
# The file is stored in the user's .fluid_scientist config directory with
# restrictive permissions.  This allows the LLM config to survive server
# restarts without requiring the user to re-enter the API key each time.
_LLM_CONFIG_PATH = Path.home() / ".fluid_scientist" / "llm_config.json"


def _save_llm_config(provider: str, model: str, api_key: str) -> None:
    """Persist LLM configuration to disk so it survives server restarts."""
    import json
    try:
        _LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config = {"provider": provider, "model": model, "api_key": api_key}
        _LLM_CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")
        # Restrict permissions on the config file (best-effort on Windows).
        try:
            _LLM_CONFIG_PATH.chmod(0o600)
        except OSError:
            pass
    except Exception:
        pass  # Non-fatal: in-memory config still works for this session.


def _load_llm_config() -> dict[str, str] | None:
    """Load persisted LLM configuration from disk."""
    import json
    try:
        if not _LLM_CONFIG_PATH.is_file():
            return None
        config = json.loads(_LLM_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(config, dict) and config.get("provider") and config.get("api_key"):
            return config
    except Exception:
        pass
    return None


def _try_auto_configure_llm() -> None:
    """Attempt to restore LLM configuration from disk on startup."""
    global _llm_client, _draft_generator
    config = _load_llm_config()
    if config is None:
        return
    try:
        provider = config["provider"]
        model = config["model"]
        api_key = config["api_key"]
        if provider not in _PROVIDER_BASE_URLS:
            return
        _llm_client = LLMClient(
            provider=provider,
            model_name=model,
            api_key=api_key,
            base_url=_PROVIDER_BASE_URLS[provider],
        )
        _draft_generator = DraftGenerator(llm_client=_llm_client)
    except Exception:
        pass


# Auto-restore LLM config on module import (server startup).
_try_auto_configure_llm()


def configure_llm_client(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    timeout_seconds: float = 120.0,
    client: Any | None = None,
) -> LLMClient:
    """Configure the v5 workflow LLM from the page's model settings."""
    global _llm_client, _draft_generator
    resolved_base_url = base_url if base_url is not None else _PROVIDER_BASE_URLS.get(provider)
    _llm_client = LLMClient(
        provider=provider,
        model_name=model,
        api_key=api_key,
        base_url=resolved_base_url,
        timeout_seconds=timeout_seconds,
        client=client,
    )
    _draft_generator = DraftGenerator(llm_client=_llm_client)
    return _llm_client


def _require_llm_client() -> LLMClient:
    if _llm_client is None:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not configured for the v5 workflow",
        )
    return _llm_client


def _get_llm_client() -> LLMClient | None:
    """Get the LLM client or None if not configured."""
    return _llm_client


def _reset_repo_for_testing() -> None:
    """Clear all V5 entities for test isolation.

    This helper wipes every table managed by :class:`V5Repository` so that
    tests start from a clean slate.  It is intended **only** for use in
    test fixtures; production code should never call it.
    """
    _repo.clear_all()


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
        study = _complete_experiment_design(study)
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


def _merge_llm_study(study: StudyIntent, llm_study: dict[str, Any]) -> StudyIntent:
    """Merge model analysis into deterministic extraction without overwriting facts."""
    from fluid_scientist.study_decomposition.models import ObservableSpec

    updates: dict[str, Any] = {}
    for field in ("study_type", "research_objective"):
        value = llm_study.get(field)
        if value and (field != "study_type" or study.study_type == "unknown"):
            updates[field] = value
    for field in (
        "geometry",
        "physical_models",
        "initial_conditions",
        "boundary_conditions",
        "observables",
        "analysis_goals",
    ):
        value = llm_study.get(field)
        current = getattr(study, field)
        if value and (not current or current == {"type": "unknown"}):
            # analysis_goals is list[str] — extract strings from dicts.
            if field == "analysis_goals" and isinstance(value, list):
                value = [
                    str(item.get("goal", item.get("description", item.get("title", ""))))
                    if isinstance(item, dict) else str(item)
                    for item in value
                ]
            # Validate that list fields contain dicts, not bare strings.
            elif isinstance(value, list):
                value = [
                    item if isinstance(item, dict) else {"type": str(item), "source": "SYSTEM_DERIVED"}
                    for item in value
                ]
            elif field in ("geometry", "physical_models") and not isinstance(value, dict):
                # Coerce non-dict scalars into a minimal dict.
                value = {"type": str(value), "source": "SYSTEM_DERIVED"}

            # Convert observable dicts to ObservableSpec objects.
            if field == "observables" and isinstance(value, list):
                converted = []
                for item in value:
                    if isinstance(item, dict):
                        try:
                            converted.append(ObservableSpec(**item))
                        except Exception:
                            converted.append(ObservableSpec(
                                observable_id=str(item.get("observable_id", item.get("id", "unknown"))),
                                display_name=str(item.get("display_name", item.get("name", "unknown"))),
                                category=str(item.get("category", "custom")),
                            ))
                    elif hasattr(item, "observable_id"):
                        converted.append(item)
                value = converted

            updates[field] = value
    missing = llm_study.get("missing_information") or llm_study.get("missing_info")
    if isinstance(missing, list):
        updates["ambiguity_report"] = [
            *study.ambiguity_report,
            *[
                {
                    "field": str(item.get("field", "unknown")) if isinstance(item, dict) else "unknown",
                    "issue": str(item.get("issue", item)) if isinstance(item, dict) else str(item),
                    "severity": "blocking_for_case_generation",
                    "reason": "model_inferred_missing_information",
                }
                for item in missing
            ],
        ]
    merged = study.model_copy(update=updates)
    assumptions = llm_study.get("model_inferences")
    if isinstance(assumptions, dict):
        merged.physical_models.setdefault("_model_inferred", assumptions)
    return merged


def _complete_experiment_design(study: StudyIntent) -> StudyIntent:
    """Attach complete design and layered metrics before capability checks."""
    design = _design_synthesizer.synthesize(study)
    design = _design_closure_engine.close(design)
    metric_layers = _goal_metric_compiler.compile(design)
    boundary_metrics = _boundary_metric_compiler.compile(design)
    design.scientific_metrics = metric_layers["scientific"]
    design.boundary_verification_metrics = boundary_metrics
    design.credibility_metrics = metric_layers["credibility"]
    return study.model_copy(
        update={
            "experiment_design": design.model_dump(),
            "target_phenomena": list(design.target_phenomena),
            "boundary_facts": dict(design.boundary_facts),
            "scientific_metrics": metric_layers["scientific"],
            "boundary_verification_metrics": boundary_metrics,
            "credibility_metrics": metric_layers["credibility"],
            "comparison_metrics": metric_layers["comparison"],
            "optional_diagnostics": metric_layers["optional_diagnostics"],
            "analysis_goals": [goal.description for goal in design.analysis_goals],
        }
    )


def _decompose_single_study(
    message: str,
    *,
    session_id: str = "",
    input_refs: list[str] | None = None,
) -> tuple[StudyIntent, Any]:
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
    llm_output, _record = _require_llm_client().call(
        purpose="study_decomposition",
        prompt_name="v5_single_study_analysis",
        system_prompt=(
            "Analyze a CFD research request as structured JSON. Include "
            "research object/type, geometry, physical parameters, initial "
            "conditions, boundary conditions, turbulence/physics models, "
            "observables, analysis goals, missing information, required "
            "system capabilities, and model_inferences for inferred values. "
            "Do not treat inferred values as user-confirmed."
        ),
        user_message=message,
        output_schema={
            "type": "object",
            "properties": {
                "study": {"type": "object"},
                "missing_information": {"type": "array"},
                "required_capabilities": {"type": "array"},
            },
        },
        session_id=session_id,
        input_refs=input_refs or [],
    )
    llm_study = llm_output.get("study") if isinstance(llm_output, dict) else None
    if isinstance(llm_study, dict):
        study = _merge_llm_study(study, llm_study)

    # Normalize study fields to ensure correct types for downstream consumers.
    study = _normalize_study(study)

    ambiguities = _detector.detect(study)
    study.ambiguity_report = ambiguities
    study = _complete_experiment_design(study)
    check_result = _checker.check(study)
    study.readiness_level = check_result.readiness_level
    return study, check_result


def _normalize_study(study: StudyIntent) -> StudyIntent:
    """Ensure all study fields have correct types after LLM merge.

    The LLM may return fields with inconsistent types (strings instead of
    dicts, dicts instead of objects, etc.). This function normalizes them
    so downstream consumers can rely on the typed model.
    """
    from fluid_scientist.study_decomposition.models import ObservableSpec

    # boundary_conditions and initial_conditions must be list[dict].
    for attr in ("boundary_conditions", "initial_conditions"):
        items = getattr(study, attr, [])
        if items:
            setattr(study, attr, [
                item if isinstance(item, dict)
                else {"type": str(item), "source": "SYSTEM_DERIVED"}
                for item in items
            ])

    # observables must be list[ObservableSpec].
    if study.observables:
        converted = []
        for o in study.observables:
            if isinstance(o, ObservableSpec):
                converted.append(o)
            elif isinstance(o, dict):
                try:
                    converted.append(ObservableSpec(**o))
                except Exception:
                    converted.append(ObservableSpec(
                        observable_id=str(o.get("observable_id", o.get("id", "unknown"))),
                        display_name=str(o.get("display_name", o.get("name", "unknown"))),
                        category=str(o.get("category", "custom")),
                    ))
            else:
                converted.append(ObservableSpec(
                    observable_id=str(o),
                    display_name=str(o),
                    category="custom",
                ))
        study.observables = converted

    # analysis_goals must be list[str].
    if study.analysis_goals:
        study.analysis_goals = [
            str(g.get("goal", g.get("description", g.get("title", str(g))))) if isinstance(g, dict)
            else str(g)
            for g in study.analysis_goals
        ]

    # geometry and physical_models must be dict.
    for attr in ("geometry", "physical_models"):
        val = getattr(study, attr, {})
        if val and not isinstance(val, dict):
            setattr(study, attr, {"type": str(val), "source": "SYSTEM_DERIVED"})

    return study


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
    records = _llm_client.get_records(session_id) if _llm_client is not None else []
    return {
        "session_id": session_id,
        "count": len(records),
        "records": [
            {**r.model_dump(mode="json"), "model": r.model_name}
            for r in records
        ],
    }


def _recent_messages(session_id: str, limit: int = 6) -> list[dict[str, str]]:
    messages = _session_store.get_messages(session_id)
    return [
        {"role": m.role, "type": m.message_type, "content": m.content[:500]}
        for m in messages[-limit:]
    ]


def _draft_summary(draft: ExperimentDraft | None) -> dict[str, Any] | None:
    if draft is None:
        return None
    return {
        "draft_id": draft.draft_id,
        "version": draft.version,
        "objective": draft.objective,
        "study_type": draft.study_type,
        "geometry": draft.geometry,
        "boundary_conditions": draft.boundary_conditions,
        "solver": draft.solver,
        "mesh": draft.mesh,
        "requested_outputs": draft.requested_outputs,
    }


def _allowed_actions(session: DraftSession) -> list[str]:
    actions = ["NEW_RESEARCH", "UNRESOLVED"]
    if session.pending_question_ids:
        actions.insert(0, "ANSWER_CLARIFICATION")
    if session.current_draft_id:
        actions[0:0] = ["MODIFY_DRAFT", "SUPPLEMENT_DRAFT", "ASK_ABOUT_DRAFT"]
    if session.pending_proposal_id:
        actions[0:0] = ["CONFIRM_PROPOSAL", "REJECT_PROPOSAL"]
    if session.status is DraftSessionStatus.BATCH_REVIEW:
        actions.insert(0, "SELECT_STUDY")
    return list(dict.fromkeys(actions))


def _classify_with_llm(
    route: Any,
    *,
    session: DraftSession,
    user_message: str,
    message_id: str,
) -> Any:
    """Refine ambiguous routing with the configured model."""
    if not route.should_call_llm:
        return route
    if route.input_type == "batch_research_request" and route.confidence >= 0.9:
        return route
    if route.confidence >= 0.9 and route.intent != "NEW_RESEARCH":
        return route

    draft = _repo.get_draft(session.current_draft_id or "")
    payload = {
        "session": session.model_dump(mode="json"),
        "active_study_id": session.selected_study_id,
        "active_draft": _draft_summary(draft),
        "pending_clarification": session.pending_question_ids,
        "pending_proposal": session.pending_proposal_id,
        "recent_messages": _recent_messages(session.session_id),
        "allowed_actions": _allowed_actions(session),
        "rule_route": route.model_dump(),
        "user_message": user_message,
    }
    try:
        output, _record = _require_llm_client().call(
            purpose="input_routing",
            prompt_name="v5_message_intent",
            system_prompt=(
                "Classify the user turn for a conversational CFD draft workflow. "
                "Return JSON with intent, confidence, reason. Prefer active draft "
                "modification/question/clarification over NEW_RESEARCH unless the "
                "user explicitly asks for a new study."
            ),
            user_message=json.dumps(payload, ensure_ascii=False),
            output_schema={
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["intent", "confidence", "reason"],
            },
            session_id=session.session_id,
            input_refs=[message_id],
        )
    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("input_routing failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    intent = str(output.get("intent", "UNRESOLVED")).upper()
    confidence = float(output.get("confidence", route.confidence))
    reason = str(output.get("reason", "Model classified the message intent."))
    mapping = {
        "NEW_RESEARCH": "new_research_request",
        "MODIFY_DRAFT": "draft_change_request",
        "SUPPLEMENT_DRAFT": "draft_change_request",
        "ANSWER_CLARIFICATION": "clarification_answer",
        "ASK_ABOUT_DRAFT": "question_about_draft",
        "CONFIRM_PROPOSAL": "proposal_confirmation",
        "REJECT_PROPOSAL": "proposal_cancel",
        "CONFIRM_DRAFT": "unknown",
        "SELECT_STUDY": "study_selection",
        "UNRESOLVED": route.input_type,
    }
    if (
        session.current_draft_id
        and intent == "NEW_RESEARCH"
        and confidence < 0.85
        and not any(k in user_message.lower() for k in ("新建", "另一个", "new", "another"))
    ):
        intent = "UNRESOLVED"
    # If the session has no draft yet, but the LLM classified the message as
    # MODIFY_DRAFT or SUPPLEMENT_DRAFT, treat it as NEW_RESEARCH instead —
    # there is nothing to modify.
    if (
        not session.current_draft_id
        and intent in ("MODIFY_DRAFT", "SUPPLEMENT_DRAFT")
    ):
        intent = "NEW_RESEARCH"
        reason = "No existing draft in session; treating as new research request."
    return route.model_copy(
        update={
            "input_type": mapping.get(intent, route.input_type),
            "intent": intent,
            "confidence": confidence,
            "reason": reason,
            "should_call_llm": False,
        }
    )


def _answer_draft_question(session: DraftSession, user_message: str) -> str:
    draft = _repo.get_draft(session.current_draft_id or "")
    if draft is None:
        return "当前没有可解释的草案。"
    lower = user_message.lower()
    if "自由滑移" in lower or "free slip" in lower or "free_slip" in lower:
        return (
            "自由滑移边界通常用于表示切向速度梯度为零、法向无穿透的理想滑移边界。"
            "是否适用取决于你的物理场景；如果要修改它，我会先生成变更提案等待确认。"
        )
    return (
        "我会基于当前草案回答，不会修改草案。当前草案目标是："
        f"{draft.objective or '尚未填写'}"
    )


@router.post("/sessions/{session_id}/messages")
def send_message(
    session_id: str, request: UserMessageRequest
) -> dict[str, Any]:
    """Process a user message and return routing result + response actions."""
    session = _session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Route the input. Ambiguous routes are refined with the configured model
    # before mutating session state.
    route = _input_router.route(request.message, session)
    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    route = _classify_with_llm(
        route,
        session=session,
        user_message=request.message,
        message_id=message_id,
    )

    # Store the user message
    msg = SessionMessage(
        message_id=message_id,
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
        try:
            llm_output, _record = _require_llm_client().call(
                purpose="study_decomposition",
                prompt_name="study_decomposer",
                system_prompt="Decompose the user's research request into one or more CFD studies.",
                user_message=request.message,
                session_id=session_id,
                input_refs=[msg.message_id],
            )
        except HTTPException:
            raise
        except Exception as exc:
            _logger.error("batch_decomposition failed: %s\n%s", exc, traceback.format_exc())
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if isinstance(llm_output, dict):
            studies_val = llm_output.get("studies")
            if isinstance(studies_val, list):
                llm_studies_output = studies_val

        if llm_studies_output:
            existing_titles = {s.title.strip().lower() for s in batch.studies}
            for llm_study in llm_studies_output:
                # Coerce string items to dicts (LLM may return bare strings).
                if isinstance(llm_study, str):
                    llm_study = {"title": llm_study, "research_objective": llm_study}
                if not isinstance(llm_study, dict):
                    continue
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
        # Single study
        try:
            study, check_result = _decompose_single_study(
                request.message,
                session_id=session_id,
                input_refs=[msg.message_id],
            )
        except HTTPException:
            raise
        except Exception as exc:
            _logger.error("single_study_decomposition failed: %s\n%s", exc, traceback.format_exc())
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        # Create and persist a batch so the study can be selected later.
        from fluid_scientist.study_decomposition.models import BatchStudyPlan
        batch = BatchStudyPlan(
            batch_id=f"batch_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            studies=[study],
        )
        study.batch_id = batch.batch_id
        _repo.save_batch(batch)

        # Update session with batch_id.
        try:
            session = _state_machine.transition(
                session, DraftSessionStatus.BATCH_REVIEW
            )
            session.batch_id = batch.batch_id
            _session_store.update_session(session)
        except TransitionError:
            session.batch_id = batch.batch_id
            _session_store.update_session(session)

        response_actions.append({
            "action": "study_decomposed",
            "study": study.model_dump(),
            "capability_check": check_result.model_dump(),
        })

    elif route.input_type == "draft_change_request":
        if not session.current_draft_id:
            response_actions.append({
                "action": "clarification_required",
                "question": "当前没有可修改的草案。请先选择或生成一个草案。",
            })
        else:
            proposal = request_draft_change(
                session.current_draft_id,
                DraftChangeRequest(
                    session_id=session_id,
                    draft_id=session.current_draft_id,
                    user_message=request.message,
                ),
            )
            if proposal.clarification_required and not proposal.changes:
                proposal.status = "cancelled"
                _repo.save_proposal(proposal)
                refreshed = _session_store.get_session(session_id)
                if refreshed and refreshed.pending_proposal_id == proposal.proposal_id:
                    refreshed.pending_proposal_id = None
                    refreshed.status = DraftSessionStatus.DRAFT_READY
                    _session_store.update_session(refreshed)
                response_actions.append({
                    "action": "clarification_required",
                    "questions": proposal.clarification_required,
                    "message": proposal.clarification_required[0].get(
                        "suggested_question", "请补充需要修改的具体位置。"
                    ),
                })
            else:
                response_actions.append({
                    "action": "change_proposal",
                    "proposal": proposal.model_dump(),
                })

    elif route.input_type == "question_about_draft":
        response_actions.append({
            "action": "answer",
            "message": _answer_draft_question(session, request.message),
        })

    elif route.input_type == "study_selection":
        response_actions.append({
            "action": "study_selected",
            "message": "请确认研究任务以生成实验草案",
        })

    elif route.input_type == "proposal_confirmation":
        if session.pending_proposal_id:
            new_draft = apply_proposal(
                session.pending_proposal_id,
                ApplyProposalRequest(
                    session_id=session_id,
                    proposal_id=session.pending_proposal_id,
                ),
            )
            response_actions.append({
                "action": "draft_updated",
                "draft": new_draft.model_dump(),
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
    """Confirm a draft, freezing it for compilation."""
    draft = _repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if not _draft_is_compile_ready(draft):
        raise HTTPException(
            status_code=409,
            detail=(
                "Draft is not compile-ready. Please ensure the draft has "
                "passed static validation checks before confirming."
            ),
        )

    result = _validator.validate(draft)
    # Filter out non-critical blocking issues that can be resolved later:
    # - OpenFOAM runtime errors (validated on remote workstation)
    # - Geometry field issues (pipeline-generated cases have different schema)
    non_blocking_keys = ("openfoam", "geometry_missing", "characteristic_dimension")
    blocking = [
        issue for issue in (result.blocking_issues or [])
        if not any(k in str(issue).lower() for k in non_blocking_keys)
    ]
    if blocking:
        raise HTTPException(
            status_code=400,
            detail=f"Draft has blocking issues: {blocking}",
        )

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
            "draft_id": new_draft.draft_id,
            "version": new_draft.version,
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
    if not _draft_is_compile_ready(draft):
        raise HTTPException(
            status_code=409,
            detail=(
                "Draft is not compile-ready. CasePlan generation is disabled "
                "until mesh/checkMesh/solver dry-run validation has passed."
            ),
        )

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

    pipeline_result = _run_compile_ready_pipeline_for_study(study, request.session_id)
    if pipeline_result["type"] == "pipeline_failed":
        return {
            "batch_id": batch_id,
            "selected_study_id": request.study_id,
            **pipeline_result,
        }

    draft = pipeline_result["draft"]
    if session:
        session.current_draft_id = draft["draft_id"]
        session.current_draft_version = draft.get("version", 1)
        with contextlib.suppress(TransitionError):
            session = _state_machine.transition(
                session, DraftSessionStatus.DRAFT_READY
            )
        _session_store.update_session(session)

    return {
        "batch_id": batch_id,
        "selected_study_id": request.study_id,
        **pipeline_result,
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
    _write_case_to_disk(compiled, case_dir)

    _repo.save_compiled_case(case_plan_id, case_dir, compiled)

    _repo.log_audit(
        event_id=f"audit_{uuid.uuid4().hex[:12]}",
        session_id=None,
        event_type="case_compiled",
        payload={"case_plan_id": case_plan_id, "case_dir": case_dir},
    )

    # Count files written to disk
    file_list: list[str] = []
    for section_name, section_content in compiled.items():
        if isinstance(section_content, dict):
            for filename in section_content:
                file_list.append(f"{section_name}/{filename}")

    return {
        "case_plan_id": case_plan_id,
        "case_dir": case_dir,
        "compiled": compiled,
        "file_count": len(file_list),
        "files": file_list,
    }


def _format_openfoam_dict(data: Any, indent: int = 0) -> str:
    """Format a Python dict/list/value as an OpenFOAM dictionary string.

    Handles special OpenFOAM syntax:
    - {"uniform": value} -> "uniform value;"
    - "[0 1 -1 0 0 0 0]" (dimensions string) -> "[0 1 -1 0 0 0 0]"
    - Lists of vectors -> "(x y z)" format
    """
    pad = "    " * indent
    if isinstance(data, dict):
        lines = []
        for key, val in data.items():
            # Special case: {"uniform": value} should be "uniform value;"
            if key == "uniform" and not isinstance(val, dict):
                if isinstance(val, list):
                    vec = " ".join(_fmt_num(v) for v in val)
                    lines.append(f"{pad}uniform ({vec});")
                else:
                    lines.append(f"{pad}uniform {_fmt_num(val)};")
            # Special case: dimensions string like "[0 1 -1 0 0 0 0]"
            elif key == "dimensions" and isinstance(val, str) and val.startswith("["):
                lines.append(f"{pad}dimensions {val};")
            elif isinstance(val, dict):
                # Check if it's a "uniform" wrapper
                if "uniform" in val and len(val) == 1:
                    uv = val["uniform"]
                    if isinstance(uv, list):
                        vec = " ".join(_fmt_num(v) for v in uv)
                        lines.append(f"{pad}{key} uniform ({vec});")
                    else:
                        lines.append(f"{pad}{key} uniform {_fmt_num(uv)};")
                else:
                    lines.append(f"{pad}{key}")
                    lines.append(f"{pad}{{")
                    lines.append(_format_openfoam_dict(val, indent + 1))
                    lines.append(f"{pad}}}")
            elif isinstance(val, list):
                # OpenFOAM lists: ( item1 item2 item3 )
                if val and isinstance(val[0], list):
                    # List of vectors
                    items = " ".join(f"({' '.join(_fmt_num(v) for v in vec)})" for vec in val)
                    lines.append(f"{pad}{key}\n{pad}(\n{pad}    {items}\n{pad});")
                else:
                    items = " ".join(_fmt_num(v) if not isinstance(v, str) else v for v in val)
                    lines.append(f"{pad}{key} ({items});")
            else:
                if isinstance(val, bool):
                    lines.append(f"{pad}{key} {'true' if val else 'false'};")
                elif isinstance(val, (int, float)):
                    lines.append(f"{pad}{key} {_fmt_num(val)};")
                else:
                    lines.append(f"{pad}{key} {val};")
        return "\n".join(lines)
    elif isinstance(data, list):
        items = " ".join(_fmt_num(v) if not isinstance(v, str) else v for v in data)
        return f"({items})"
    else:
        return str(data)


def _fmt_num(v: Any) -> str:
    """Format a number nicely for OpenFOAM output."""
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return f"{v:.1f}"
        return repr(v)
    return str(v)


def _write_case_to_disk(compiled: dict[str, Any], case_dir: str) -> None:
    """Write the compiled case structure to disk as OpenFOAM native dictionary files."""
    os.makedirs(case_dir, exist_ok=True)
    for section_name, section_content in compiled.items():
        section_dir = os.path.join(case_dir, section_name)
        os.makedirs(section_dir, exist_ok=True)
        if isinstance(section_content, dict):
            for filename, content in section_content.items():
                filepath = os.path.join(section_dir, filename)
                with open(filepath, "w", encoding="utf-8", newline="\n") as f:
                    if isinstance(content, str):
                        # Already a pre-formatted OpenFOAM string (e.g. blockMeshDict)
                        f.write("/*--------------------------------*- C++ -*----------------------------------*\\\n")
                        f.write("| =========                 |                                                 |\n")
                        f.write("| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |\n")
                        f.write("|  \\\\    /   O peration     | Version:  v2406                                 |\n")
                        f.write("|   \\\\  /    A nd           | Web:      www.OpenFOAM.com                      |\n")
                        f.write("|    \\/     M anipulation   |                                                 |\n")
                        f.write("\\*---------------------------------------------------------------------------*/\n")
                        f.write(f"// Generated by Fluid Scientist\n\n")
                        f.write(content)
                        f.write("\n")
                    elif isinstance(content, (dict, list)):
                        f.write("/*--------------------------------*- C++ -*----------------------------------*\\\n")
                        f.write("| =========                 |                                                 |\n")
                        f.write("| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |\n")
                        f.write("|  \\\\    /   O peration     | Version:  v2406                                 |\n")
                        f.write("|   \\\\  /    A nd           | Web:      www.OpenFOAM.com                      |\n")
                        f.write("|    \\/     M anipulation   |                                                 |\n")
                        f.write("\\*---------------------------------------------------------------------------*/\n")
                        f.write(f"// Generated by Fluid Scientist\n\n")
                        f.write(_format_openfoam_dict(content))
                        f.write("\n")
                    else:
                        f.write(str(content))


# ---------------------------------------------------------------------------
# Case Review endpoint (LLM-powered pre-submission check)
# ---------------------------------------------------------------------------

@router.post("/case-plans/{case_plan_id}/review")
def review_case_plan(case_plan_id: str) -> dict[str, Any]:
    """Review a compiled case using LLM to catch OpenFOAM issues before submission.

    This endpoint reads all generated OpenFOAM files from the compiled case
    directory and sends them to an LLM for review. The LLM checks for:
    - Syntax errors in OpenFOAM dictionary format
    - Security policy violations (libs, $, codeStream, etc.)
    - Missing required parameters
    - Invalid configurations that would cause runtime failures
    """
    case_plan = _repo.get_case_plan(case_plan_id)
    if case_plan is None:
        raise HTTPException(status_code=404, detail="Case plan not found")

    # Find the compiled case directory — search temp dir for matching prefix
    import glob
    pattern = os.path.join(tempfile.gettempdir(), f"fluid_case_{case_plan_id}*")
    matching_dirs = glob.glob(pattern)
    if not matching_dirs:
        raise HTTPException(
            status_code=409,
            detail="Case has not been compiled yet. Compile first via "
            "POST /api/v5/case-plans/{case_plan_id}/compile",
        )
    case_dir = matching_dirs[0]  # Use the first match

    from fluid_scientist.llm.case_reviewer import review_case_with_llm

    llm = _get_llm_client()
    result = review_case_with_llm(llm, case_dir, session_id="")

    return {
        "case_plan_id": case_plan_id,
        "case_dir": case_dir,
        "has_issues": result.get("has_issues", False),
        "issues": result.get("issues", []),
        "summary": result.get("summary", ""),
    }


@router.post("/case-plans/{case_plan_id}/fix")
def fix_case_plan(
    case_plan_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Auto-fix OpenFOAM case files based on review issues.

    Takes the review result (issues list) and uses an LLM to fix the
    problematic files. After fixing, automatically re-reviews the case
    to verify the fixes were successful.

    Request body (optional):
        {
            "issues": [...]  # Review issues to fix. If omitted, runs review first.
        }

    Returns:
        {
            "case_plan_id": str,
            "fixed": bool,
            "fixed_files": list[str],
            "remaining_issues": list,
            "post_fix_review": {...},  # Re-review result after fixes
            "summary": str,
        }
    """
    case_plan = _repo.get_case_plan(case_plan_id)
    if case_plan is None:
        raise HTTPException(status_code=404, detail="Case plan not found")

    # Find the compiled case directory
    import glob as _glob
    pattern = os.path.join(tempfile.gettempdir(), f"fluid_case_{case_plan_id}*")
    matching_dirs = _glob.glob(pattern)
    if not matching_dirs:
        raise HTTPException(
            status_code=409,
            detail="Case has not been compiled yet. Compile first via "
            "POST /api/v5/case-plans/{case_plan_id}/compile",
        )
    case_dir = matching_dirs[0]

    from fluid_scientist.llm.case_reviewer import (
        fix_case_with_llm,
        review_case_with_llm,
    )

    llm = _get_llm_client()

    # Get issues from request body or run review first
    if payload and payload.get("issues"):
        review_result = {"has_issues": True, "issues": payload["issues"]}
    else:
        review_result = review_case_with_llm(llm, case_dir, session_id="")

    if not review_result.get("has_issues"):
        return {
            "case_plan_id": case_plan_id,
            "fixed": False,
            "fixed_files": [],
            "remaining_issues": [],
            "post_fix_review": review_result,
            "summary": "No issues found — nothing to fix",
        }

    # Apply fixes
    fix_result = fix_case_with_llm(llm, case_dir, review_result, session_id="")

    # Re-review after fixes
    post_fix_review = review_case_with_llm(llm, case_dir, session_id="")

    return {
        "case_plan_id": case_plan_id,
        "case_dir": case_dir,
        "fixed": fix_result.get("fixed", False),
        "fixed_files": fix_result.get("fixed_files", []),
        "remaining_issues": fix_result.get("remaining_issues", []),
        "post_fix_review": {
            "has_issues": post_fix_review.get("has_issues", False),
            "issues": post_fix_review.get("issues", []),
            "summary": post_fix_review.get("summary", ""),
        },
        "summary": fix_result.get("summary", ""),
    }


@router.get("/capabilities")
def list_capabilities() -> dict[str, Any]:
    """List registered capabilities in the capability registry."""
    return {
        "capabilities": _capability_registry.list_capabilities(),
        "count": len(_capability_registry.list_capabilities()),
    }


@router.get("/capabilities/health")
def capability_registry_health() -> dict[str, Any]:
    """Return concrete registry health for the compile-ready pipeline."""
    from fluid_scientist.capabilities import get_capability_registry

    report = get_capability_registry().health_check(mutate=False)
    verified = [
        record.capability_id
        for record in report.records
        if record.status_after == "verified" and record.healthy
    ]
    unverified = [
        record.model_dump()
        for record in report.records
        if not record.healthy
    ]
    declared_or_unverified = [
        record.model_dump()
        for record in report.records
        if record.status_after != "verified"
    ]
    return {
        "healthy": report.healthy,
        "summary": {
            "total": report.total,
            "verified": report.verified,
            "unverified": report.unverified,
            "degraded": report.degraded,
        },
        "verified_capabilities": verified,
        "unverified_capabilities": unverified,
        "declared_or_unverified_capabilities": declared_or_unverified,
        "records": [record.model_dump() for record in report.records],
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


# ---------------------------------------------------------------------------
# Compile-Ready Pipeline endpoint  (new V5 workflow)
# ---------------------------------------------------------------------------


class PipelineRunRequest(BaseModel):
    session_id: str | None = None
    user_description: str
    pre_extracted: dict[str, Any] | None = None
    work_root: str | None = None


class PipelineRunResponse(BaseModel):
    session_id: str
    status: str
    current_stage: str
    stage_history: list[dict[str, Any]] = Field(default_factory=list)
    compile_ready_view: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    case_dir: str | None = None
    generated_files: list[str] = Field(default_factory=list)


def _draft_is_compile_ready(draft: ExperimentDraft) -> bool:
    result = draft.validation_result or {}
    # Allow confirmation when draft status is READY and no ERROR-level
    # checks failed. OpenFOAM runtime availability is not required locally.
    has_blocking_errors = any(
        not c.get("passed", False) and c.get("severity") == "error"
        for c in (result.get("checks") or [])
    )
    return (
        draft.status in {DraftStatus.READY, DraftStatus.CONFIRMED}
        and not has_blocking_errors
    )


def _study_description(study: StudyIntent) -> str:
    parts = [
        study.raw_text,
        study.research_objective,
        study.title,
    ]
    return "\n".join(part for part in parts if part).strip() or study.study_id


def _run_compile_ready_pipeline_for_study(
    study: StudyIntent,
    session_id: str,
) -> dict[str, Any]:
    from fluid_scientist.capabilities import get_capability_registry
    from fluid_scientist.workflow_pipeline import PipelineStatus, V5WorkflowPipeline

    pipeline = V5WorkflowPipeline(
        work_root=os.path.join(tempfile.gettempdir(), "fluid_scientist_v5_pipeline"),
        registry=get_capability_registry(),
        llm_client=_llm_client,
    )
    state = pipeline.run(
        user_description=_study_description(study),
        session_id=session_id,
        pre_extracted=study.model_dump(mode="json"),
    )
    payload = {
        "session_id": state.session_id,
        "status": state.current_stage,
        "current_stage": state.current_stage,
        "stage_history": [s.model_dump(mode="json") for s in state.stage_history],
        "failure": state.failure,
        "case_dir": state.case_dir,
        "generated_files": state.case_manifest.get("generated_files", []),
    }
    if state.current_stage != PipelineStatus.COMPILE_READY or state.draft_view is None:
        return {"type": "pipeline_failed", **payload}

    draft = _legacy_draft_from_view(state.draft_view)
    draft = draft.model_copy(update={"session_id": session_id, "study_id": study.study_id})
    _repo.save_draft(draft)
    return {
        "type": "draft_ready",
        **payload,
        "compile_ready_view": state.draft_view,
        "draft": draft.model_dump(mode="json"),
    }


@router.post("/pipeline/run", response_model=PipelineRunResponse)
def run_compile_ready_pipeline(request: PipelineRunRequest) -> PipelineRunResponse:
    """Run the full Compile-Ready pipeline end-to-end.

    Accepts a natural-language research description and returns either a
    fully validated COMPILE_READY draft view or a structured failure.
    Progress stages are returned in ``stage_history``.
    """
    from fluid_scientist.workflow_pipeline import (
        PipelineStatus,
        V5WorkflowPipeline,
    )
    from fluid_scientist.capabilities import get_capability_registry

    registry = get_capability_registry()
    pipeline = V5WorkflowPipeline(
        work_root=request.work_root,
        registry=registry,
        llm_client=_llm_client,
    )
    state = pipeline.run(
        user_description=request.user_description,
        session_id=request.session_id,
        pre_extracted=request.pre_extracted,
    )
    # Store only truly compile-ready draft views.
    if state.current_stage == PipelineStatus.COMPILE_READY and state.draft_view:
        _repo.save_draft(_legacy_draft_from_view(state.draft_view))
    return PipelineRunResponse(
        session_id=state.session_id,
        status=state.current_stage,
        current_stage=state.current_stage,
        stage_history=[s.model_dump() for s in state.stage_history],
        compile_ready_view=state.draft_view,
        failure=state.failure,
        case_dir=state.case_dir,
        generated_files=state.case_manifest.get("generated_files", []),
    )


@router.get("/pipeline/sessions/{session_id}/progress")
def get_pipeline_progress(session_id: str) -> dict[str, Any]:
    """Return progress information for a pipeline session.

    This is a lightweight endpoint the frontend polls during long-running
    pipeline stages.
    """
    # For the initial implementation progress is embedded in the run response;
    # this endpoint provides a stable URL for future websocket/SSE progress.
    session = _session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "status": session.status,
        "current_draft_id": session.current_draft_id,
    }


class PipelineModifyRequest(BaseModel):
    session_id: str
    modification_text: str
    work_root: str | None = None


@router.post("/pipeline/modify", response_model=PipelineRunResponse)
def modify_compile_ready_pipeline(request: PipelineModifyRequest) -> PipelineRunResponse:
    """Apply an incremental modification to an existing COMPILE_READY case.

    This re-runs the affected pipeline stages (design -> closure -> case
    generation -> validation) and returns the updated draft view.
    """
    from fluid_scientist.workflow_pipeline import (
        V5WorkflowPipeline,
    )
    from fluid_scientist.capabilities import get_capability_registry

    # Find work_root from an existing session directory or use request value
    work_root = request.work_root
    if not work_root:
        # Look for the session in common work roots
        for candidate in [tempfile.gettempdir(), os.getcwd()]:
            if os.path.isdir(os.path.join(candidate, request.session_id)):
                work_root = candidate
                break
        if not work_root:
            work_root = tempfile.gettempdir()

    registry = get_capability_registry()
    pipeline = V5WorkflowPipeline(
        work_root=work_root,
        registry=registry,
        llm_client=_llm_client,
    )
    state = pipeline.modify(
        session_id=request.session_id,
        modification_text=request.modification_text,
    )
    if state.draft_view:
        _repo.save_draft(_legacy_draft_from_view(state.draft_view))
    return PipelineRunResponse(
        session_id=state.session_id,
        status=state.current_stage,
        current_stage=state.current_stage,
        stage_history=[s.model_dump() for s in state.stage_history],
        compile_ready_view=state.draft_view,
        failure=state.failure,
        case_dir=state.case_dir,
        generated_files=state.case_manifest.get("generated_files", []),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _legacy_draft_from_view(view: dict[str, Any]) -> ExperimentDraft:
    """Best-effort conversion from CompileReadyDraftView to legacy ExperimentDraft."""
    from fluid_scientist.draft.models import DraftParameter, ParameterSource
    params: list[DraftParameter] = []
    for name, value in view.get("design", {}).items():
        params.append(DraftParameter(
            parameter_id=name,
            display_name=name,
            value=value.get("value") if isinstance(value, dict) else value,
            unit=value.get("unit") if isinstance(value, dict) else None,
            source=ParameterSource.DERIVED,
            source_reason=value.get("reason", "") if isinstance(value, dict) else "",
        ))
    return ExperimentDraft(
        draft_id=view.get("draft_id", str(uuid.uuid4())),
        session_id=view.get("session_id", ""),
        version=view.get("draft_version", 1),
        status=DraftStatus.READY if view.get("status") == "compile_ready" else DraftStatus.DRAFT,
        objective=view.get("objective") or view.get("research_objective", ""),
        study_type=view.get("study_type", "cfd_simulation"),
        geometry=view.get("geometry", {}),
        materials=view.get("materials", {}),
        physics_models=view.get("physical_models", {}),
        boundary_conditions=view.get("boundary_conditions", {}),
        initial_conditions=view.get("initial_conditions", {}),
        solver=view.get("solver", {}),
        numerics=view.get("numerics", {}),
        mesh=view.get("mesh", {}),
        measurement_plan={
            "scientific_metrics": view.get("scientific_metrics", []),
            "boundary_verification_metrics": view.get("boundary_verification_metrics", []),
            "credibility_metrics": view.get("credibility_metrics", []),
        },
        control_parameters=params,
        validation_result=view.get("validation_results", {}),
        requested_outputs=view.get("requested_outputs", []),
        analysis_goals=[
            g if isinstance(g, str) else str(g.get("phenomenon", g.get("target_quantity", str(g))))
            for g in view.get("analysis_goals", [])
        ],
    )


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
# Model configuration endpoints (v5)
# ---------------------------------------------------------------------------


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
    if _llm_client is None:
        return ModelConfigView(
            configured=False,
            provider=None,
            model=None,
            is_mock=True,
            suggested_models=_PROVIDER_MODELS,
        )
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

    The API key is persisted to ``~/.fluid_scientist/llm_config.json`` so
    it survives server restarts.  The file has restrictive permissions.
    """
    global _llm_client, _draft_generator
    if request.provider not in _PROVIDER_BASE_URLS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider '{request.provider}'. Supported: {list(_PROVIDER_BASE_URLS.keys())}",
        )
    if not request.api_key or len(request.api_key) < 5:
        raise HTTPException(status_code=422, detail="API key is too short")

    if _llm_client is None:
        _llm_client = LLMClient()
        _draft_generator = DraftGenerator(llm_client=_llm_client)

    _llm_client.reconfigure(
        provider=request.provider,
        model_name=request.model,
        api_key=request.api_key,
        base_url=_PROVIDER_BASE_URLS[request.provider],
    )
    # Persist to disk so the config survives server restarts.
    _save_llm_config(request.provider, request.model, request.api_key)
    return ModelConfigView(
        configured=not _llm_client.is_mock,
        provider=_llm_client.provider,
        model=_llm_client.model_name,
        is_mock=_llm_client.is_mock,
        suggested_models=_PROVIDER_MODELS,
    )


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
    import io
    import tarfile

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


@router.post("/jobs/{job_id}/diagnose-fix")
def diagnose_and_fix_job(
    job_id: str,
    request: Request,
    target_id: str = "",
) -> dict[str, Any]:
    """Diagnose a failed job's runtime error and auto-fix the case files.

    This endpoint is called when a workstation job has failed. It:
    1. Queries the job status to get the error message
    2. Finds the compiled case directory
    3. Uses LLM to diagnose the root cause and fix the files
    4. Returns the diagnosis + fix result for user confirmation

    After reviewing the fixes, the user can re-submit via
    POST /api/v5/cases/{case_plan_id}/submit
    """
    target = _get_workstation_target(request)
    try:
        job_record = target.status(job_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to query job status: {e}") from e

    # Extract error and case_plan_id from job record
    if hasattr(job_record, "model_dump"):
        job_data = job_record.model_dump()
    elif hasattr(job_record, "__dict__"):
        job_data = job_record.__dict__
    else:
        job_data = {"job_id": job_id, "state": str(job_record)}

    error_message = job_data.get("error", "")
    if not error_message:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} has no error message (state={job_data.get('state', 'unknown')}). Only failed jobs can be diagnosed.",
        )

    # Extract case_plan_id from job_id (format: v5-{timestamp}-{case_plan_id[:8]})
    parts = job_id.split("-")
    case_plan_prefix = parts[-1] if len(parts) >= 3 else ""

    # Find the compiled case directory by matching prefix
    import glob as _glob
    pattern = os.path.join(tempfile.gettempdir(), f"fluid_case_*{case_plan_prefix}*")
    matching_dirs = _glob.glob(pattern)
    if not matching_dirs:
        # Try to find by looking at all case dirs
        all_pattern = os.path.join(tempfile.gettempdir(), "fluid_case_*")
        all_dirs = _glob.glob(all_pattern)
        # Use the most recently modified one
        if all_dirs:
            matching_dirs = [max(all_dirs, key=os.path.getmtime)]
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Could not find compiled case directory for job {job_id}",
            )
    case_dir = matching_dirs[0]

    # Get case plan summary for context
    case_plan_summary: dict[str, Any] = {}
    # Try to find case_plan_id from compiled case store
    for cp_id, record in _repo._compiled_cases.items() if hasattr(_repo, '_compiled_cases') else []:
        if record.get("case_dir") == case_dir:
            plan = _repo.get_case_plan(cp_id)
            if plan:
                case_plan_summary = {
                    "solver": plan.solver,
                    "physics_family": plan.physics_plan.get("physics_family", ""),
                    "dimensions": plan.dimensions,
                    "endTime": plan.numerics_plan.get("endTime", ""),
                    "deltaT": plan.numerics_plan.get("deltaT", ""),
                }
                break

    from fluid_scientist.llm.case_reviewer import diagnose_and_fix_runtime_error

    llm = _get_llm_client()
    result = diagnose_and_fix_runtime_error(
        llm, case_dir, error_message, case_plan_summary, session_id=""
    )

    return {
        "job_id": job_id,
        "error_message": error_message,
        "case_dir": case_dir,
        "diagnosis": result.get("diagnosis", {}),
        "fixed": result.get("fixed", False),
        "fixed_files": result.get("fixed_files", []),
        "summary": result.get("summary", ""),
    }


__all__ = ["router"]
