"""API router for the CylinderFlow2D experiment family.

Exposes REST endpoints for the dedicated cylinder flow pipeline.

Legacy endpoints (backward-compatible):
  POST /api/v5/cylinder-flow/route       — Check if input matches cylinder flow
  POST /api/v5/cylinder-flow/draft       — Create draft from natural language
  POST /api/v5/cylinder-flow/revalidate  — Re-validate existing spec
  POST /api/v5/cylinder-flow/modify      — Apply natural language modification
  POST /api/v5/cylinder-flow/confirm     — Confirm the draft spec
  GET  /api/v5/cylinder-flow/{spec_id}   — Read stored spec
  GET  /api/v5/cylinder-flow/health      — Health check
  GET  /api/v5/cylinder-flow/schema      — JSON schema
  POST /api/v5/cylinder-flow/compile     — Compile confirmed spec
  POST /api/v5/cylinder-flow/execute     — Execute compiled case
  GET  /api/v5/cylinder-flow/jobs/{id}/status  — Poll job status
  GET  /api/v5/cylinder-flow/jobs/{id}/results — Get job results
  GET  /api/v5/cylinder-flow/jobs/{id}/plots   — List plots

Gated workflow endpoints (three-stage confirmation + persistence):
  POST /api/v5/cylinder-flow/{spec_id}/confirm-plan    — Gate 1: confirm research plan
  GET  /api/v5/cylinder-flow/{spec_id}/compile-preview  — Preview compile configuration
  POST /api/v5/cylinder-flow/{spec_id}/confirm-compile — Gate 2: compile + validate + smoke test
  POST /api/v5/cylinder-flow/{job_id}/confirm-run      — Gate 3: start formal simulation
  GET  /api/v5/cylinder-flow/{job_id}/results           — Get complete results with analysis
  GET  /api/v5/cylinder-flow/{job_id}/report            — Get structured analysis report
"""

from __future__ import annotations

import json
import os
import re as _re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from fluid_scientist.cylinder_flow_2d import (
    BoundarySpec,
    BumpProfileType,
    CylinderFlow2DExperimentSpecV1,
    CylinderFlow2DSceneRouter,
    CylinderFlow2DV1Pipeline,
    DraftStatus,
    FieldSource,
    FieldStatus,
    ModelPolicy,
    ProvenanceField,
)
from fluid_scientist.intent.conflict_resolver import (
    ConflictResolver,
    LLMCandidateExtractor,
    RegexCandidateExtractor,
)
from fluid_scientist.skills.skill_resolver import SkillResolver
from fluid_scientist.analysis.llm_report import LLMReportGenerator, PhysicsValidator

router = APIRouter(prefix="/api/v5/cylinder-flow", tags=["cylinder-flow-2d"])

# In-memory store (backed by SQLite for persistence across restarts)
_spec_store: dict[str, CylinderFlow2DExperimentSpecV1] = {}

# SQLite persistence layer
from fluid_scientist.persistence.store import get_persistence as _get_persistence

def _persist_spec(spec_id: str, spec: Any, session_id: str = "", user_input: str = "") -> None:
    """Save spec to both in-memory store and SQLite."""
    _spec_store[spec_id] = spec
    try:
        _get_persistence().save_spec(spec_id, spec, session_id, user_input)
    except Exception as _e:
        pass  # Non-fatal: in-memory store still works

def _load_spec(spec_id: str) -> CylinderFlow2DExperimentSpecV1 | None:
    """Load spec from in-memory store, fallback to SQLite."""
    spec = _spec_store.get(spec_id)
    if spec is not None:
        return spec
    # Fallback: try SQLite
    try:
        spec_dict = _get_persistence().load_spec(spec_id)
        if spec_dict:
            spec = CylinderFlow2DExperimentSpecV1(**spec_dict)
            _spec_store[spec_id] = spec  # Warm the cache
            return spec
    except Exception:
        pass
    return None

# Recover specs from SQLite on startup
try:
    _recovered = _get_persistence().recover_all_specs()
    for _sid, _sdict in _recovered.items():
        try:
            _spec_store[_sid] = CylinderFlow2DExperimentSpecV1(**_sdict)
        except Exception:
            pass
    if _recovered:
        import logging as _logging
        _logging.getLogger(__name__).info("Recovered %d specs from SQLite", len(_recovered))
except Exception:
    pass


# ---------------------------------------------------------------------------
# LLM client access — bridges to the global LLM configured via v5_router
# ---------------------------------------------------------------------------

def _get_llm_client():
    """Return the globally configured LLMClient, or None if not configured."""
    try:
        from fluid_scientist.api.v5_router import _llm_client
        return _llm_client
    except (ImportError, AttributeError):
        return None


def _require_llm_client():
    """Return the LLM client or raise 503 LLM_UNAVAILABLE."""
    client = _get_llm_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "LLM_UNAVAILABLE",
                "message": "大模型未配置。请先在页面右上角配置模型（如 glm/glm-4-flash），否则无法进行语义理解。",
            },
        )
    return client


# System prompt for structured CFD experiment parsing
_LLM_PARSE_SYSTEM_PROMPT = """你是一个CFD（计算流体力学）实验理解专家。请从用户的自然语言描述中提取结构化的实验参数。

你必须返回严格的JSON，包含以下字段：
{
  "scene": {
    "dimension": "2D或3D",
    "flow_type": "external_flow/internal_flow/other",
    "confidence": 0.0-1.0
  },
  "geometry": {
    "domain": {"length": {"value":0,"unit":"m"}, "height": {"value":0,"unit":"m"}},
    "objects": [
      {"id":"cylinder_1","type":"cylinder","radius":{"value":0,"unit":"m"},"center":{"x":{"value":0,"unit":"m"},"y":{"value":0,"unit":"m"}}},
      {"id":"trapezoid_1","type":"trapezoid","top_width":{"value":0,"unit":"m"},"bottom_width":{"value":0,"unit":"m"},"height":{"value":0,"unit":"m"},"center_x":{"value":0,"unit":"m"},"relation":"","attached_boundary":"bottom"}
    ]
  },
  "physics": {
    "fluid_model": "incompressible_newtonian等",
    "density": {"value":0,"unit":"kg/m3","source":"USER_EXPLICIT"},
    "kinematic_viscosity": {"value":0,"unit":"m2/s"},
    "reynolds_number": {"value":0},
    "inlet_velocity": {"value":0,"unit":"m/s"}
  },
  "boundaries": [
    {"name":"left","type":"velocity_inlet","details":{}},
    {"name":"right","type":"pressure_outlet","details":{}},
    {"name":"top","type":"unknown","details":{}},
    {"name":"bottom","type":"no_slip_wall","details":{}}
  ],
  "research_goals": ["研究目标1","研究目标2"],
  "requested_metrics": ["drag_coefficient","lift_coefficient","vorticity","shedding_frequency"],
  "missing_fields": ["缺失的字段1"],
  "ambiguities": ["歧义描述1"],
  "unsupported_capabilities": ["不支持的能力1"]
}

重要规则：
1. 数值字段必须携带value、unit、source（USER_EXPLICIT/MODEL_INFERRED/UNKNOWN）
2. 如果用户没有明确说流体是水，不要假设是水
3. 几何类型必须忠实于用户描述，绝不替换：
   - 用户说"矩形"→type="rectangle"，不能变成其他形状
   - 用户说"三角形"/"三角障碍物"/"三角凸起"→type="triangle"，不能变成其他形状
   - 用户说"余弦钟形"/"余弦丘"/"cosine bell"→type="cosine_bell"
   - 用户说"梯形"/"梯形凸起"→type="trapezoid"
   - 梯形必须提取 top_width（上底）、bottom_width（下底）、height（高）三个独立参数
   - 梯形的"上底"="顶宽"，"下底"="底宽"，不要混为"width"
   - 用户说"障碍物"但未指定形状→type="unknown_obstacle"
   - 禁止将三角形替换为cosine_bell、rectangle或其他形状
   - 禁止将不支持的几何替换为最接近的已知形状
   - 对于unknown_obstacle，必须在ambiguities中说明需要用户澄清形状类型
4. 如果坐标缺失，必须列入missing_fields
5. 如果边界条件有歧义（如"自由出流"），必须列入ambiguities
6. 如果用户要求涡街/涡脱落频率/升阻力，必须列入requested_metrics
7. 不支持的几何或物理模型必须列入unsupported_capabilities
8. 可推导的参数不要列入missing_fields：
   - 如果用户提供了Re、来流速度和圆柱直径，运动黏度可由nu=U*D/Re推导，不要列为缺失
   - 圆柱直径可由半径推导，反之亦然
9. 如果存在位置冲突（如"距下壁面2m"和"位于5m高流场正中央"给出不同的y坐标），必须列入ambiguities
"""


def _llm_structured_parse(user_text: str, session_id: str = "") -> dict:
    """Call LLM to parse user input into structured experiment spec.

    Returns the parsed JSON dict. Raises HTTPException on failure.
    Skill prompt fragments are injected into the system prompt for domain-specific guidance.
    """
    llm = _require_llm_client()

    # Inject skill prompt fragments into system prompt
    _skill_resolver = SkillResolver()
    skill_injection = _skill_resolver.build_prompt_injection(
        user_text=user_text,
        stage="intent",
    )
    full_system_prompt = _LLM_PARSE_SYSTEM_PROMPT + skill_injection

    output_schema = {
        "type": "object",
        "properties": {
            "scene": {"type": "object"},
            "geometry": {"type": "object"},
            "physics": {"type": "object"},
            "boundaries": {"type": "array"},
            "research_goals": {"type": "array"},
            "requested_metrics": {"type": "array"},
            "missing_fields": {"type": "array"},
            "ambiguities": {"type": "array"},
            "unsupported_capabilities": {"type": "array"},
        },
        "required": ["scene", "geometry", "physics", "boundaries", "research_goals", "requested_metrics"],
    }

    parsed, record = llm.call(
        purpose="study_decomposition",
        prompt_name="cyl_flow_structured_parse",
        system_prompt=full_system_prompt,
        user_message=user_text,
        output_schema=output_schema,
        session_id=session_id,
        prompt_version="cyl-parse-v2-with-skills",
    )

    if not record.success:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "LLM_STRUCTURED_OUTPUT_FAILED",
                "message": f"大模型调用失败: {record.error}",
                "request_id": record.call_id,
            },
        )

    return parsed


# ---------------------------------------------------------------------------
# JSON file persistence layer
# ---------------------------------------------------------------------------

PERSISTENCE_DIR = Path(r"d:\desktop\AI FOR SCIENCE\data\cylinder_flow")
SKILLS_DIR = Path(r"d:\desktop\AI FOR SCIENCE\data\cylinder_flow\skills")


# ---------------------------------------------------------------------------
# SkillExecutor — records executable skill invocations with evidence
# ---------------------------------------------------------------------------

class SkillInvocation:
    """Record of a single skill execution."""

    def __init__(
        self,
        skill_id: str,
        skill_version: str = "1.0",
        entrypoint: str = "",
        input_data: dict | None = None,
    ):
        self.invocation_id = f"inv_{uuid.uuid4().hex[:12]}"
        self.skill_id = skill_id
        self.skill_version = skill_version
        self.entrypoint = entrypoint
        self.input = input_data or {}
        self.output: dict = {}
        self.status = "RUNNING"
        self.evidence: list[str] = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None
        self.error: str | None = None

    def to_dict(self) -> dict:
        return {
            "invocation_id": self.invocation_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "entrypoint": self.entrypoint,
            "input": self.input,
            "output": self.output,
            "status": self.status,
            "evidence": self.evidence,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class SkillExecutor:
    """Executes skills and records invocation manifests.

    Each invocation is written to
    ``SKILLS_DIR/<invocation_id>.json`` so that the frontend
    can display real skill execution evidence.
    """

    def __init__(self, base_dir: Path = SKILLS_DIR):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._invocations: list[SkillInvocation] = []

    def execute(
        self,
        skill_id: str,
        entrypoint_fn: Any,
        input_data: dict | None = None,
        skill_version: str = "1.0",
        evidence: list[str] | None = None,
    ) -> SkillInvocation:
        """Execute a skill and record the result.

        Args:
            skill_id: Logical skill identifier (e.g. "fluid.intent_to_spec").
            entrypoint_fn: Callable that performs the actual work.
            input_data: Input dict passed to the entrypoint.
            skill_version: Version string.
            evidence: List of evidence file paths to record.

        Returns:
            SkillInvocation with status PASSED or FAILED.
        """
        inv = SkillInvocation(
            skill_id=skill_id,
            skill_version=skill_version,
            entrypoint=entrypoint_fn.__name__ if hasattr(entrypoint_fn, '__name__') else str(entrypoint_fn),
            input_data=input_data or {},
        )

        try:
            result = entrypoint_fn(input_data) if input_data else entrypoint_fn()
            if isinstance(result, dict):
                inv.output = result
            elif isinstance(result, tuple) and len(result) == 2:
                inv.output = {"result": result[0], "metadata": result[1]}
            else:
                inv.output = {"result": str(result)}
            inv.status = "PASSED"
            if evidence:
                inv.evidence.extend(evidence)
        except Exception as exc:
            inv.status = "FAILED"
            inv.error = str(exc)
            inv.output = {"error": str(exc)}

        inv.finished_at = datetime.now(timezone.utc).isoformat()

        # Persist invocation manifest
        inv_path = self.base_dir / f"{inv.invocation_id}.json"
        with open(inv_path, "w", encoding="utf-8") as fh:
            json.dump(inv.to_dict(), fh, ensure_ascii=False, indent=2, default=str)

        self._invocations.append(inv)
        return inv

    def get_invocations(self) -> list[dict]:
        """Return all recorded invocations as dicts."""
        return [inv.to_dict() for inv in self._invocations]

    def summary(self) -> dict:
        """Return a summary of all skill executions."""
        total = len(self._invocations)
        passed = sum(1 for inv in self._invocations if inv.status == "PASSED")
        failed = sum(1 for inv in self._invocations if inv.status == "FAILED")
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "invocations": self.get_invocations(),
        }

    def clear(self):
        """Clear all invocations (for new session)."""
        self._invocations.clear()


# Global SkillExecutor instance
_skill_executor = SkillExecutor()


class SessionStore:
    """Thread-safe JSON file persistence with an in-memory cache.

    Each session is stored as ``{session_id}.json`` under *PERSISTENCE_DIR*.
    A re-entrant lock guards both the cache and the file I/O so that
    concurrent FastAPI requests do not corrupt session files.
    """

    def __init__(self, base_dir: Path = PERSISTENCE_DIR) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = threading.RLock()

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _safe_id(session_id: str) -> str:
        """Sanitise *session_id* to prevent path traversal."""
        return (
            session_id.replace("/", "_")
            .replace("\\", "_")
            .replace("..", "_")
        )

    def _session_path(self, session_id: str) -> Path:
        return self.base_dir / f"{self._safe_id(session_id)}.json"

    # -- public API --------------------------------------------------------

    def save(self, session_id: str, data: dict) -> None:
        """Persist *data* for *session_id* to cache and disk."""
        with self._lock:
            data_to_store = {**data}
            data_to_store["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._cache[session_id] = data_to_store
            path = self._session_path(session_id)
            tmp_path = path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(data_to_store, fh, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_path, path)

    def load(self, session_id: str) -> dict | None:
        """Load session data, preferring cache then disk."""
        with self._lock:
            if session_id in self._cache:
                return self._cache[session_id]
            path = self._session_path(session_id)
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    self._cache[session_id] = data
                    return data
                except (json.JSONDecodeError, OSError):
                    return None
            return None

    def update(self, session_id: str, updates: dict) -> dict | None:
        """Merge *updates* into an existing session and persist."""
        with self._lock:
            current = self.load(session_id)
            if current is None:
                return None
            current.update(updates)
            self.save(session_id, current)
            return current

    def delete(self, session_id: str) -> bool:
        """Delete a session from cache and disk."""
        with self._lock:
            self._cache.pop(session_id, None)
            path = self._session_path(session_id)
            if path.exists():
                path.unlink()
                return True
            return False

    def list_sessions(self) -> list[str]:
        """Return all known session IDs (cache + disk)."""
        with self._lock:
            sessions: set[str] = set(self._cache.keys())
            for p in self.base_dir.glob("*.json"):
                sessions.add(p.stem)
            return sorted(sessions)


_session_store = SessionStore()

# In-memory reverse mapping: job_id -> spec_id (rebuilt from sessions on demand)
_job_to_spec: dict[str, str] = {}
_job_to_spec_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class RouteRequest(BaseModel):
    user_text: str


class RouteResponse(BaseModel):
    matched: bool
    pipeline_id: str = "cylinder-flow-2d-v1"
    schema_name: str = "CylinderFlow2DExperimentSpecV1"
    pipeline_version: str = "1.0"
    pipeline_stage: str = "DRAFT_NORMALIZED"
    confidence: float = 0.0
    reason: str = ""
    not_family_reason: str = ""


class DraftRequest(BaseModel):
    user_text: str
    session_id: str | None = None


class DraftResponse(BaseModel):
    success: bool
    spec_id: str | None = None
    pipeline_id: str = "cylinder-flow-2d-v1"
    schema_name: str = "CylinderFlow2DExperimentSpecV1"
    pipeline_version: str = "1.0"
    pipeline_stage: str = "DRAFT_NORMALIZED"
    spec_version: int = 1
    draft_status: str = "NEEDS_CLARIFICATION"
    spec: dict[str, Any] | None = None
    semantic_display: dict[str, Any] | None = None
    blocking_issues: list[dict] = Field(default_factory=list)
    clarification_questions: list[dict] = Field(default_factory=list)
    observables: list[dict] = Field(default_factory=list)
    analysis_goals: list[dict] = Field(default_factory=list)
    stage_history: list[dict] = Field(default_factory=list)
    decision_summary: dict[str, Any] | None = None
    semantic_coverage: dict[str, Any] | None = None
    llm_call_info: dict[str, Any] | None = None
    skill_summary: dict[str, Any] | None = None
    # New: derived values, assumptions, and audit issues
    derived_values: list[str] = Field(default_factory=list)
    non_blocking_assumptions: list[dict] = Field(default_factory=list)
    derived_value_issues: list[dict] = Field(default_factory=list)
    audit_issues: list[dict] = Field(default_factory=list)
    # New: intent candidate set for traceability
    intent_candidates: dict[str, Any] | None = None
    error: str | None = None


class RevalidateRequest(BaseModel):
    spec_id: str
    spec: dict[str, Any] | None = None


class ModifyRequest(BaseModel):
    spec_id: str
    modification_text: str
    user_input: str | None = None  # For recovery when spec_store is cleared


class ConfirmRequest(BaseModel):
    spec_id: str
    clarifications: dict[str, str] | None = None
    end_time: float | None = None
    max_courant: float | None = None
    accept_recommendations: bool = True
    user_input: str | None = None  # For recovery when spec_store is cleared (e.g. server restart)


class ConfirmResponse(BaseModel):
    success: bool
    spec_id: str | None = None
    draft_status: str = "SPEC_CONFIRMED"
    spec: dict[str, Any] | None = None
    semantic_display: dict[str, Any] | None = None
    blocking_issues: list[dict] = Field(default_factory=list)
    clarification_questions: list[dict] = Field(default_factory=list)
    # New: derived values, assumptions, and audit issues
    derived_values: list[str] = Field(default_factory=list)
    non_blocking_assumptions: list[dict] = Field(default_factory=list)
    derived_value_issues: list[dict] = Field(default_factory=list)
    error: str | None = None
    debug_details: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    module: str = "cylinder_flow_2d"
    pipeline_id: str = "cylinder-flow-2d-v1"
    version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Gated-workflow request / response models
# ---------------------------------------------------------------------------


class ConfirmPlanResponse(BaseModel):
    """Response for Gate 1 — confirm the research plan."""

    success: bool
    spec_id: str
    already_confirmed: bool = False
    confirmed_at: str | None = None
    compile_preview: dict[str, Any] | None = None
    blocking_issues: list[dict] = Field(default_factory=list)
    error: str | None = None


class CompilePreviewResponse(BaseModel):
    """Response for the compile-preview endpoint."""

    success: bool
    spec_id: str
    preview: dict[str, Any] | None = None
    error: str | None = None


class ConfirmCompileResponse(BaseModel):
    """Response for Gate 2 — compile, validate, checkMesh, smoke test."""

    success: bool
    spec_id: str
    job_id: str | None = None
    compilation: dict[str, Any] | None = None
    static_validation: dict[str, Any] | None = None
    mesh_report: dict[str, Any] | None = None
    smoke_test_report: dict[str, Any] | None = None
    error: str | None = None
    debug_details: str | None = None


class ConfirmRunResponse(BaseModel):
    """Response for Gate 3 — start the formal simulation."""

    success: bool
    job_id: str
    status: str = "RUNNING"
    error: str | None = None


class ResultsResponse(BaseModel):
    """Complete results including metrics, analysis and artifacts."""

    success: bool
    job_id: str
    status: str = "UNKNOWN"
    metrics: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    mesh_report: dict[str, Any] | None = None
    smoke_test_report: dict[str, Any] | None = None
    run_report: dict[str, Any] | None = None
    error: str | None = None


class AnalysisReportResponse(BaseModel):
    """Structured analysis report for a completed job."""

    success: bool
    job_id: str
    report: dict[str, Any] | None = None
    error: str | None = None


class ScientificReportResponse(BaseModel):
    """LLM-generated scientific report with physics validation."""

    success: bool
    job_id: str
    report: dict[str, Any] | None = None
    physics_validation: dict[str, Any] | None = None
    result_summary: dict[str, Any] | None = None
    report_source: str = "rule_based"
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_clarification_questions(blocking_issues: list[dict], spec: Any) -> list[dict]:
    """Convert blocking issues into user-facing clarification questions.

    Each question has: id, message, type (choice|number|text), options (for choice).
    Category determines the UI rendering:
    - BLOCKING_CONFLICT: structured choice with conflict explanation
    - SOLVER_CRITICAL_AMBIGUITY: structured choice with recommendation
    - TRUE_MISSING_FIELD: input field
    """
    questions: list[dict] = []

    for issue in blocking_issues:
        code = issue.get("code", "")
        msg = issue.get("message", "")
        qid = issue.get("question_id", code)
        category = issue.get("category", "")
        options = issue.get("options", [])
        recommendation = issue.get("recommendation", "")

        # --- BLOCKING_CONFLICT: structured choice ---
        if category == "BLOCKING_CONFLICT" or "CONFLICT" in code:
            questions.append({
                "id": qid.lower() if qid else code.lower(),
                "category": "BLOCKING_CONFLICT",
                "message": msg,
                "recommendation": recommendation,
                "type": "choice",
                "options": options if options else ["选项A", "选项B", "自定义"],
            })

        # --- SOLVER_CRITICAL_AMBIGUITY: structured choice ---
        elif category == "SOLVER_CRITICAL_AMBIGUITY" or code in (
            "LLM_AMBIGUITY", "TOP_BOUNDARY_AMBIGUITY", "BOTTOM_BOUNDARY_AMBIGUITY",
        ):
            questions.append({
                "id": qid.lower() if qid else code.lower(),
                "category": "SOLVER_CRITICAL_AMBIGUITY",
                "message": msg,
                "recommendation": recommendation,
                "type": "choice",
                "options": options if options else [
                    "symmetryPlane / slip（推荐）",
                    "freestream 自由流边界",
                    "open 开放边界",
                    "自定义",
                ],
            })

        elif code == "CYLINDER_WALL_DISTANCE_AMBIGUOUS" or qid == "cylinder_wall_distance_meaning":
            # Extract distance from description
            m = _re.search(r"(\d+\.?\d*)", msg)
            dist = m.group(1) if m else "X"
            questions.append({
                "id": "cylinder_wall_distance_meaning",
                "message": f"圆柱距壁面的距离 {dist}m 是指圆心高度还是表面间隙？",
                "type": "choice",
                "options": [
                    f"圆心高度为{dist}米",
                    f"圆柱表面与壁面间隙为{dist}米",
                ],
            })

        elif code == "INLET_VELOCITY_MISSING" or code == "INLET_VELOCITY_TRULY_MISSING":
            questions.append({
                "id": "inlet_velocity",
                "message": "入口速度未指定，请输入来流速度（m/s）：",
                "type": "number",
                "placeholder": "例如: 1.0",
            })

        elif code == "OBSERVATION_SECTION_MISSING":
            questions.append({
                "id": "section_x",
                "message": "观测截面位置未指定，请输入截面x坐标（m）：",
                "type": "number",
                "placeholder": "例如: 8.0",
            })

        elif code == "OBSERVATION_POINT_MISSING":
            questions.append({
                "id": "observation_point",
                "message": "观测点坐标未指定，请输入观测点x,y坐标（m，用逗号分隔）：",
                "type": "text",
                "placeholder": "例如: 6.0, 1.0",
            })

        elif code == "CYLINDER_DIMENSION_MISSING":
            questions.append({
                "id": "cylinder_diameter",
                "message": "圆柱尺寸未指定，请输入圆柱直径或半径（m）：",
                "type": "text",
                "placeholder": "例如: 直径0.2 / 半径0.1",
            })

        elif code == "CYLINDER_TYPE_MISSING":
            questions.append({
                "id": "cylinder_type",
                "message": "障碍物类型未确认，是否为圆柱？",
                "type": "choice",
                "options": ["是圆柱", "不是圆柱（清除障碍物）"],
            })

        elif code == "FLOW_TOPOLOGY_UNRESOLVED":
            questions.append({
                "id": "flow_topology",
                "message": "流动拓扑结构未确定，请选择驱动方式：",
                "type": "choice",
                "options": ["入口-出口驱动", "压力梯度驱动", "壁面驱动", "组合驱动"],
            })

        elif code == "PRESSURE_GRADIENT_MISSING_MAGNITUDE":
            questions.append({
                "id": "pressure_gradient_magnitude",
                "message": "压力梯度幅值未指定，请输入压力梯度（Pa/m）：",
                "type": "number",
                "placeholder": "例如: 1.0",
            })

        elif code == "SHEAR_STRESS_MISSING_MAGNITUDE":
            questions.append({
                "id": "shear_magnitude",
                "message": "壁面剪切应力幅值未指定，请输入剪切应力（Pa）：",
                "type": "number",
                "placeholder": "例如: 0.5",
            })

        elif "boundary" in code.lower() or "FRONT" in code or "BACK" in code:
            # Boundary combination issues — auto-suggest fix
            questions.append({
                "id": "boundary_fix",
                "message": f"边界条件问题：{msg}。建议自动修正为2D标准边界（前后面=empty）？",
                "type": "choice",
                "options": ["是，自动修正", "不，保持当前设置"],
            })

        else:
            # Generic question for any unhandled blocking issue
            questions.append({
                "id": qid,
                "message": msg,
                "type": "text",
                "placeholder": "请输入补充信息",
            })

    return questions


def _apply_clarification(spec: Any, question_id: str, answer: str) -> None:
    """Apply a single clarification answer to the spec in-place."""
    if question_id == "cylinder_wall_distance_meaning":
        for amb in spec.ambiguities:
            if amb.get("id") == question_id:
                m = _re.search(r"(\d+\.?\d*)", amb.get("description", ""))
                if m:
                    wall_dist = float(m.group(1))
                    if "圆心" in answer:
                        spec.cylinder.center_y_m = ProvenanceField(
                            value=wall_dist,
                            source=FieldSource.USER_CONFIRMED,
                            status=FieldStatus.RESOLVED,
                            confidence=1.0,
                            reason="用户确认圆心高度",
                        )
                    elif "间隙" in answer:
                        radius = spec.get_cylinder_radius()
                        if radius is not None:
                            center_y = wall_dist + radius
                            spec.cylinder.center_y_m = ProvenanceField(
                                value=center_y,
                                source=FieldSource.USER_CONFIRMED,
                                status=FieldStatus.RESOLVED,
                                confidence=1.0,
                                reason=f"用户确认表面间隙{wall_dist}m，圆心高度={center_y}m",
                            )
                amb["resolved"] = True
                amb["resolution"] = answer
                break

    elif question_id == "cylinder_position_conflict":
        # Resolve cylinder Y position conflict
        domain_h = spec.domain.height_m.value or 5.0
        domain_l = spec.domain.length_m.value or 10.0
        center_y_from_wall = None
        # Extract wall distance from blocking issue
        for issue in spec.blocking_issues:
            if issue.get("code") == "CYLINDER_POSITION_CONFLICT":
                m = _re.search(r"距下壁面([\d.]+)m", issue.get("message", ""))
                if m:
                    center_y_from_wall = float(m.group(1))
                break
        if "水平" in answer or "距壁面" in answer or "保留" in answer:
            # Option A: keep wall distance, horizontal center
            if center_y_from_wall is not None:
                spec.cylinder.center_y_m = ProvenanceField(
                    value=center_y_from_wall,
                    source=FieldSource.USER_CONFIRMED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason=f"用户确认圆心y={center_y_from_wall}m，水平居中",
                )
            spec.cylinder.center_x_m = ProvenanceField(
                value=domain_l / 2.0,
                source=FieldSource.USER_CONFIRMED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                reason="用户确认水平居中",
            )
        elif "正中央" in answer or "几何" in answer:
            # Option B: geometric center
            spec.cylinder.center_y_m = ProvenanceField(
                value=domain_h / 2.0,
                source=FieldSource.USER_CONFIRMED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                reason=f"用户确认几何正中央 y={domain_h / 2.0}m",
            )
            spec.cylinder.center_x_m = ProvenanceField(
                value=domain_l / 2.0,
                source=FieldSource.USER_CONFIRMED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                reason="用户确认几何正中央",
            )
        else:
            # Option C: custom — extract numbers from answer
            nums = _re.findall(r"(\d+\.?\d*)", answer)
            if len(nums) >= 2:
                spec.cylinder.center_x_m = ProvenanceField(
                    value=float(nums[0]),
                    source=FieldSource.USER_CONFIRMED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户自定义位置",
                )
                spec.cylinder.center_y_m = ProvenanceField(
                    value=float(nums[1]),
                    source=FieldSource.USER_CONFIRMED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户自定义位置",
                )
        # Remove the conflict issue
        spec.blocking_issues = [
            i for i in spec.blocking_issues
            if i.get("code") != "CYLINDER_POSITION_CONFLICT"
        ]

    elif question_id == "top_boundary_ambiguity":
        from fluid_scientist.cylinder_flow_2d.models import SemanticBoundaryType
        if "slip" in answer.lower() or "symmetry" in answer.lower():
            new_type = SemanticBoundaryType.SLIP_WALL
            reason = "用户确认上边界为symmetryPlane/slip"
        elif "freestream" in answer.lower() or "自由流" in answer:
            new_type = SemanticBoundaryType.FREESTREAM
            reason = "用户确认上边界为freestream"
        elif "open" in answer.lower() or "开放" in answer:
            new_type = SemanticBoundaryType.OPEN_BOUNDARY
            reason = "用户确认上边界为开放边界"
        else:
            new_type = SemanticBoundaryType.SLIP_WALL
            reason = "默认上边界为slip"
        spec.boundaries.top = BoundarySpec(
            semantic_type=new_type,
            source=FieldSource.USER_CONFIRMED,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
            reason=reason,
        )
        # Remove related ambiguity issues
        spec.blocking_issues = [
            i for i in spec.blocking_issues
            if i.get("code") != "LLM_AMBIGUITY" or "出流" not in i.get("message", "")
        ]

    elif question_id in ("INLET_VELOCITY_MISSING", "inlet_velocity"):
        m = _re.search(r"(\d+\.?\d*)", answer)
        if m:
            velocity = float(m.group(1))
            spec.boundaries.left.inlet_velocity = velocity
            spec.boundaries.left.status = FieldStatus.RESOLVED

    elif question_id in ("OBSERVATION_SECTION_MISSING", "section_x"):
        m = _re.search(r"(\d+\.?\d*)", answer)
        if m:
            section_x = float(m.group(1))
            for obs in spec.observables:
                if obs.section_x is None:
                    obs.section_x = section_x
                    obs.missing_fields = [f for f in obs.missing_fields if f != "section_x"]
                    if not obs.missing_fields:
                        obs.status = FieldStatus.RESOLVED

    elif question_id == "observation_point":
        # Parse "x, y" format
        parts = _re.findall(r"(\d+\.?\d*)", answer)
        if len(parts) >= 2:
            px, py = float(parts[0]), float(parts[1])
            for obs in spec.observables:
                if obs.point_x is None:
                    obs.point_x = px
                    obs.point_y = py
                    obs.missing_fields = [f for f in obs.missing_fields if f not in ("point_x", "point_y")]
                    if not obs.missing_fields:
                        obs.status = FieldStatus.RESOLVED

    elif question_id == "cylinder_diameter":
        m = _re.search(r"(\d+\.?\d*)", answer)
        if m:
            val = float(m.group(1))
            if "半径" in answer or "radius" in answer.lower():
                spec.cylinder.radius_m = ProvenanceField(
                    value=val, source=FieldSource.USER_CONFIRMED,
                    status=FieldStatus.RESOLVED, confidence=1.0, reason="用户确认半径",
                )
                spec.cylinder.diameter_m = ProvenanceField(
                    value=val * 2, source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED, confidence=1.0, reason="由半径推导直径",
                )
            else:
                spec.cylinder.diameter_m = ProvenanceField(
                    value=val, source=FieldSource.USER_CONFIRMED,
                    status=FieldStatus.RESOLVED, confidence=1.0, reason="用户确认直径",
                )
                spec.cylinder.radius_m = ProvenanceField(
                    value=val / 2, source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED, confidence=1.0, reason="由直径推导半径",
                )
            spec.cylinder.characteristic_dimension_m = spec.cylinder.diameter_m

    elif question_id == "cylinder_type":
        if "不是" in answer or "清除" in answer:
            spec.has_cylinder = False
        else:
            spec.has_cylinder = True

    elif question_id == "flow_topology":
        topology_map = {
            "入口": "inlet_outlet", "出口": "inlet_outlet",
            "压力梯度": "pressure_difference", "压力": "pressure_difference",
            "壁面": "wall_driven",
            "组合": "combined_driving",
        }
        for key, mode in topology_map.items():
            if key in answer:
                spec.flow_topology = {"mode": mode}
                break

    elif question_id == "pressure_gradient_magnitude":
        m = _re.search(r"(\d+\.?\d*)", answer)
        if m:
            spec.forcing.pressure_gradient_magnitude = float(m.group(1))
            spec.forcing.enabled = True

    elif question_id == "shear_magnitude":
        m = _re.search(r"(\d+\.?\d*)", answer)
        if m:
            spec.forcing.wall_shear_magnitude = float(m.group(1))

    elif question_id == "boundary_fix":
        if "是" in answer or "修正" in answer:
            # Fix 2D boundaries
            from fluid_scientist.cylinder_flow_2d.models import SemanticBoundaryType
            spec.boundaries.front = type(spec.boundaries.front)(
                semantic_type=SemanticBoundaryType.EMPTY,
                source=FieldSource.SYSTEM_DERIVED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
            )
            spec.boundaries.back = type(spec.boundaries.back)(
                semantic_type=SemanticBoundaryType.EMPTY,
                source=FieldSource.SYSTEM_DERIVED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
            )

    elif question_id in ("INLET_OUTLET_NO_OUTLET", "boundary_fix"):
        # User is fixing the right boundary type
        from fluid_scientist.cylinder_flow_2d.models import SemanticBoundaryType
        if "压力出口" in answer or "pressure" in answer.lower():
            new_type = SemanticBoundaryType.PRESSURE_OUTLET
        elif "开放" in answer or "open" in answer.lower():
            new_type = SemanticBoundaryType.OPEN_OUTLET
        elif "对流" in answer or "advective" in answer.lower():
            new_type = SemanticBoundaryType.ADVECTIVE_OUTLET
        elif "自动" in answer or "修正" in answer or "是" in answer:
            new_type = SemanticBoundaryType.PRESSURE_OUTLET  # default fix
        else:
            new_type = SemanticBoundaryType.PRESSURE_OUTLET
        spec.boundaries.right = type(spec.boundaries.right)(
            semantic_type=new_type,
            source=FieldSource.USER_CONFIRMED,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
            reason=f"用户确认右边界为{answer}",
        )

    elif question_id in ("CYLINDER_CENTER_X_NULL", "CYLINDER_CENTER_X_MISSING"):
        m = _re.search(r"(\d+\.?\d*)", answer)
        if m:
            cx = float(m.group(1))
            spec.cylinder.center_x_m = ProvenanceField(
                value=cx,
                source=FieldSource.USER_CONFIRMED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                reason=f"用户确认圆柱x坐标为{cx}m",
            )

    elif question_id in ("CYLINDER_CENTER_Y_NULL", "CYLINDER_CENTER_Y_MISSING"):
        m = _re.search(r"(\d+\.?\d*)", answer)
        if m:
            cy = float(m.group(1))
            spec.cylinder.center_y_m = ProvenanceField(
                value=cy,
                source=FieldSource.USER_CONFIRMED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                reason=f"用户确认圆柱y坐标为{cy}m",
            )

    elif question_id in ("LLM_AMBIGUITY", "LLM_MISSING_FIELD"):
        # LLM-identified ambiguities and missing fields are advisory;
        # user's clarification answer resolves them.  No spec field
        # to update — the readiness evaluator does not re-generate
        # these issues, so they are effectively cleared.
        pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check if the cylinder flow module is available."""
    return HealthResponse()


@router.post("/route", response_model=RouteResponse)
async def route_scene(request: RouteRequest) -> RouteResponse:
    """Check if the input matches the cylinder flow scene."""
    scene_router = CylinderFlow2DSceneRouter()
    result = scene_router.route(request.user_text)
    return RouteResponse(
        matched=result.matched,
        pipeline_id=result.pipeline_id,
        schema_name=result.schema_name,
        pipeline_version=result.pipeline_version,
        pipeline_stage=result.pipeline_stage,
        confidence=result.confidence,
        reason=result.reason,
        not_family_reason=result.not_family_reason,
    )


# ---------------------------------------------------------------------------
# LLM → Spec field application
# ---------------------------------------------------------------------------

def _apply_llm_to_spec(spec, llm_parsed: dict, user_text: str) -> None:
    """Apply LLM-extracted structured output onto the pipeline-produced spec.

    LLM takes precedence for semantic fields. Regex-extracted values
    are kept as cross-validation evidence.
    """
    from fluid_scientist.cylinder_flow_2d.models import (
        FieldSource, FieldStatus, ProvenanceField,
        ObservableSpec, ObservableType,
    )

    # --- Domain dimensions ---
    domain = llm_parsed.get("domain", {})
    dom_len = domain.get("length", {})
    dom_h = domain.get("height", {})
    dom_len_val = dom_len.get("value") if isinstance(dom_len, dict) else None
    dom_h_val = dom_h.get("value") if isinstance(dom_h, dict) else None
    if dom_len_val and dom_len_val > 0:
        spec.domain.length_m = ProvenanceField(
            value=float(dom_len_val),
            source=FieldSource.MODEL_RECOMMENDED,
            status=FieldStatus.RESOLVED,
            confidence=0.9,
            reason="LLM提取计算域长度",
        )
    if dom_h_val and dom_h_val > 0:
        spec.domain.height_m = ProvenanceField(
            value=float(dom_h_val),
            source=FieldSource.MODEL_RECOMMENDED,
            status=FieldStatus.RESOLVED,
            confidence=0.9,
            reason="LLM提取计算域高度",
        )

    # --- Geometry: cylinder center ---
    geometry = llm_parsed.get("geometry", {})
    objects = geometry.get("objects", [])
    for obj in objects:
        if obj.get("type") == "cylinder":
            center = obj.get("center", {})
            cx = center.get("x", {})
            cy = center.get("y", {})
            # Only apply center_x if LLM provided a non-zero value with evidence,
            # or if user text explicitly mentions an x coordinate.
            # LLM often defaults to 0 when it can't find the value.
            cx_val = cx.get("value")
            cx_evidence = cx.get("evidence_text", "")
            existing_x_field = spec.cylinder.center_x_m
            existing_x_is_explicit = (
                existing_x_field is not None
                and existing_x_field.source == FieldSource.USER_EXPLICIT
                and existing_x_field.is_resolved()
            )
            user_mentions_x = any(kw in user_text for kw in [
                "x=", "x=", "横坐标", "流向位置", "距入口",
                "center_x", "centre_x", "x坐标",
            ])
            if cx_val is not None and (cx_val != 0 or user_mentions_x) and not existing_x_is_explicit:
                spec.cylinder.center_x_m = ProvenanceField(
                    value=float(cx_val),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(cx.get("confidence", 0.9)),
                    reason=f"LLM提取: {cx_evidence}",
                )
            else:
                # Missing x coordinate — add blocking issue
                spec.blocking_issues.append({
                    "code": "CYLINDER_CENTER_X_MISSING",
                    "message": "圆柱x坐标缺失，无法确定圆柱在流向方向的位置。建议圆心距入口至少5D，下游保留至少15D。",
                })
            cy_val = cy.get("value")
            cy_evidence = cy.get("evidence_text", "")
            # Only override pipeline value if LLM has a non-zero value AND
            # the pipeline didn't already extract a USER_EXPLICIT value
            existing_y_field = spec.cylinder.center_y_m
            existing_y = existing_y_field.value if existing_y_field else None
            existing_y_is_explicit = (
                existing_y_field is not None
                and existing_y_field.source == FieldSource.USER_EXPLICIT
                and existing_y_field.is_resolved()
            )
            if cy_val is not None and (cy_val != 0 or existing_y is None) and not existing_y_is_explicit:
                spec.cylinder.center_y_m = ProvenanceField(
                    value=float(cy_val),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(cy.get("confidence", 0.9)),
                    reason=f"LLM提取: {cy_evidence}",
                )

        elif obj.get("type") == "rectangle":
            # Rectangle obstacle — now supported via snappyHexMesh + STL
            spec.rectangle.enabled = True
            # Only apply LLM values when pipeline hasn't already extracted
            # a USER_EXPLICIT value (which is more reliable than LLM parsing)
            w = obj.get("width", {})
            if w.get("value") is not None and not spec.rectangle.width_m.is_resolved():
                spec.rectangle.width_m = ProvenanceField(
                    value=float(w["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(w.get("confidence", 0.9)),
                    reason=f"LLM提取: {w.get('evidence_text', '')}",
                )
            h = obj.get("height", {})
            if h.get("value") is not None and not spec.rectangle.height_m.is_resolved():
                spec.rectangle.height_m = ProvenanceField(
                    value=float(h["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(h.get("confidence", 0.9)),
                    reason=f"LLM提取: {h.get('evidence_text', '')}",
                )
            # Rectangle position — may need clarification
            rx = obj.get("center_x", {})
            ry = obj.get("center_y", {})
            if rx.get("value") is not None and not spec.rectangle.center_x_m.is_resolved():
                spec.rectangle.center_x_m = ProvenanceField(
                    value=float(rx["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(rx.get("confidence", 0.9)),
                    reason=f"LLM提取: {rx.get('evidence_text', '')}",
                )
            if ry.get("value") is not None and not spec.rectangle.center_y_m.is_resolved():
                spec.rectangle.center_y_m = ProvenanceField(
                    value=float(ry["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(ry.get("confidence", 0.9)),
                    reason=f"LLM提取: {ry.get('evidence_text', '')}",
                )
            # Relation to cylinder
            relation = obj.get("relation", "")
            if relation:
                spec.rectangle.relation_to_cylinder = str(relation)

        elif obj.get("type") == "triangle":
            # Triangle obstacle — now supported via snappyHexMesh + STL
            # semantic_type = triangle_2d, solver_representation = polygon
            spec.triangle.enabled = True
            spec.triangle.semantic_type = "triangle_2d"
            spec.triangle.solver_representation = "polygon"
            spec.triangle.source_text = user_text
            # Clear any bottom_profile that regex pipeline might have set
            from fluid_scientist.cylinder_flow_2d.models import BottomProfileSpec
            spec.bottom_profile = BottomProfileSpec()
            # Extract dimensions
            bw = obj.get("base_width", obj.get("width", {}))
            if isinstance(bw, dict) and bw.get("value") is not None and not spec.triangle.base_width_m.is_resolved():
                spec.triangle.base_width_m = ProvenanceField(
                    value=float(bw["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(bw.get("confidence", 0.9)),
                    reason=f"LLM提取: {bw.get('evidence_text', '')}",
                )
            h = obj.get("height", {})
            if isinstance(h, dict) and h.get("value") is not None and not spec.triangle.height_m.is_resolved():
                spec.triangle.height_m = ProvenanceField(
                    value=float(h["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(h.get("confidence", 0.9)),
                    reason=f"LLM提取: {h.get('evidence_text', '')}",
                )
            # Triangle position
            tx = obj.get("center_x", {})
            if isinstance(tx, dict) and tx.get("value") is not None and not spec.triangle.center_x_m.is_resolved():
                spec.triangle.center_x_m = ProvenanceField(
                    value=float(tx["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(tx.get("confidence", 0.9)),
                    reason=f"LLM提取: {tx.get('evidence_text', '')}",
                )
            # Apex direction
            apex = obj.get("apex_direction", "")
            if apex:
                spec.triangle.apex_direction = str(apex)
            # Relation to cylinder
            relation = obj.get("relation", "")
            if relation:
                spec.triangle.relation_to_cylinder = str(relation)
            # Attached boundary
            attached = obj.get("attached_boundary", "")
            if attached:
                spec.triangle.attached_boundary = str(attached)

        elif obj.get("type") == "trapezoid":
            # Trapezoid obstacle — supported via parametric_polygon representation
            # semantic_type = trapezoid_2d, solver_representation = parametric_polygon
            # Uses generic PolygonGeometryCompiler (no dedicated TrapezoidCompiler)
            spec.trapezoid.enabled = True
            spec.trapezoid.semantic_type = "trapezoid_2d"
            spec.trapezoid.solver_representation = "parametric_polygon"
            spec.trapezoid.source_text = user_text
            # Clear any bottom_profile that regex pipeline might have set
            from fluid_scientist.cylinder_flow_2d.models import BottomProfileSpec
            spec.bottom_profile = BottomProfileSpec()
            # Extract top_width (上底)
            tw = obj.get("top_width", obj.get("upper_width", {}))
            if isinstance(tw, dict) and tw.get("value") is not None and not spec.trapezoid.top_width_m.is_resolved():
                spec.trapezoid.top_width_m = ProvenanceField(
                    value=float(tw["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(tw.get("confidence", 0.9)),
                    reason=f"LLM提取: {tw.get('evidence_text', '')}",
                )
            # Extract bottom_width (下底)
            bw = obj.get("bottom_width", obj.get("lower_width", obj.get("base_width", {})))
            if isinstance(bw, dict) and bw.get("value") is not None and not spec.trapezoid.bottom_width_m.is_resolved():
                spec.trapezoid.bottom_width_m = ProvenanceField(
                    value=float(bw["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(bw.get("confidence", 0.9)),
                    reason=f"LLM提取: {bw.get('evidence_text', '')}",
                )
            # Extract height
            h = obj.get("height", {})
            if isinstance(h, dict) and h.get("value") is not None and not spec.trapezoid.height_m.is_resolved():
                spec.trapezoid.height_m = ProvenanceField(
                    value=float(h["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(h.get("confidence", 0.9)),
                    reason=f"LLM提取: {h.get('evidence_text', '')}",
                )
            # Center X — default to cylinder center_x if "below cylinder"
            tx = obj.get("center_x", {})
            if isinstance(tx, dict) and tx.get("value") is not None and not spec.trapezoid.center_x_m.is_resolved():
                spec.trapezoid.center_x_m = ProvenanceField(
                    value=float(tx["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(tx.get("confidence", 0.9)),
                    reason=f"LLM提取: {tx.get('evidence_text', '')}",
                )
            elif spec.cylinder.center_x_m and spec.cylinder.center_x_m.is_resolved():
                # Default: aligned below cylinder
                spec.trapezoid.center_x_m = ProvenanceField(
                    value=spec.cylinder.center_x_m.value,
                    source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED,
                    confidence=0.7,
                    reason=f"默认位置 = 圆柱圆心x = {spec.cylinder.center_x_m.value}m",
                )
            # Relation to cylinder
            relation = obj.get("relation", "")
            if relation:
                spec.trapezoid.relation_to_cylinder = str(relation)
            # Attached boundary
            attached = obj.get("attached_boundary", "")
            if attached:
                spec.trapezoid.attached_boundary = str(attached)

        elif obj.get("type") in ("cosine_bell", "half_sine", "gaussian", "bump"):
            # Bump / bottom profile — apply LLM values only when pipeline
            # hasn't already extracted USER_EXPLICIT values
            from fluid_scientist.cylinder_flow_2d.models import BumpProfileType
            spec.bottom_profile.enabled = True
            # Set profile type from LLM only if pipeline hasn't set it
            if spec.bottom_profile.profile_type == BumpProfileType.FLAT:
                l_type = obj.get("type", "")
                type_map = {
                    "cosine_bell": BumpProfileType.COSINE_BELL,
                    "half_sine": BumpProfileType.HALF_SINE,
                    "gaussian": BumpProfileType.GAUSSIAN,
                }
                if l_type in type_map:
                    spec.bottom_profile.profile_type = type_map[l_type]
            # Height — only if not already resolved
            h = obj.get("height", {})
            if isinstance(h, dict) and h.get("value") is not None and not spec.bottom_profile.height_m.is_resolved():
                spec.bottom_profile.height_m = ProvenanceField(
                    value=float(h["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(h.get("confidence", 0.9)),
                    reason=f"LLM提取: {h.get('evidence_text', '')}",
                )
            # Width — only if not already resolved
            w = obj.get("width", {})
            if isinstance(w, dict) and w.get("value") is not None and not spec.bottom_profile.width_m.is_resolved():
                spec.bottom_profile.width_m = ProvenanceField(
                    value=float(w["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(w.get("confidence", 0.9)),
                    reason=f"LLM提取: {w.get('evidence_text', '')}",
                )
            # Center X — only if not already resolved
            cx = obj.get("center_x", {})
            if isinstance(cx, dict) and cx.get("value") is not None and not spec.bottom_profile.center_x_m.is_resolved():
                spec.bottom_profile.center_x_m = ProvenanceField(
                    value=float(cx["value"]),
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.RESOLVED,
                    confidence=float(cx.get("confidence", 0.9)),
                    reason=f"LLM提取: {cx.get('evidence_text', '')}",
                )

        elif obj.get("type") in ("unknown_obstacle", "obstacle", "block"):
            # Unknown or unsupported obstacle type — LLM identified it but
            # we don't have a specific geometry builder for it yet.
            # Record it as a blocking issue so the user can clarify,
            # rather than silently dropping it.
            obj_id = obj.get("id", "obstacle_unknown")
            obj_type = obj.get("type", "unknown")
            # Extract any dimensions the LLM found
            h = obj.get("height", {})
            h_val = h.get("value") if isinstance(h, dict) else None
            w = obj.get("width", {})
            w_val = w.get("value") if isinstance(w, dict) else None
            cx = obj.get("center_x", {})
            cx_val = cx.get("value") if isinstance(cx, dict) else None
            cy = obj.get("center_y", {})
            cy_val = cy.get("value") if isinstance(cy, dict) else None
            relation = obj.get("relation", "")

            dim_parts = []
            if h_val is not None:
                dim_parts.append(f"高={h_val}m")
            if w_val is not None:
                dim_parts.append(f"宽={w_val}m")
            if cx_val is not None:
                dim_parts.append(f"x={cx_val}m")
            if cy_val is not None:
                dim_parts.append(f"y={cy_val}m")
            dim_str = ", ".join(dim_parts) if dim_parts else "无尺寸信息"

            spec.blocking_issues.append({
                "code": "UNRECOGNIZED_OBSTACLE",
                "message": (
                    f"检测到未指定形状的障碍物（类型: {obj_type}, {dim_str}）。"
                    f"请明确障碍物的几何形状（如：矩形、三角形、梯形、圆柱等）。"
                    f"{' 与圆柱关系: ' + relation if relation else ''}"
                ),
                "category": "GEOMETRY_CLARIFICATION",
                "obstacle_type": obj_type,
                "dimensions": {"height": h_val, "width": w_val,
                               "center_x": cx_val, "center_y": cy_val},
                "relation_to_cylinder": relation,
            })

    # --- Physics: don't assume water ---
    physics = llm_parsed.get("physics", {})
    # Only set fluid type if LLM detected explicit mention
    fluid_model = physics.get("fluid_model", "")
    if "water" not in user_text.lower() and "水" not in user_text:
        # User didn't explicitly mention water — don't auto-assign
        if spec.fluid.type.value == "water":
            # Pipeline auto-assumed water — override to unknown
            spec.fluid.type = ProvenanceField(
                value="incompressible_newtonian",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.7,
                reason="LLM: 用户未明确指定流体为水，设为一般不可压缩牛顿流体",
            )
            spec.fluid.density_kg_m3.status = FieldStatus.AWAITING_CONFIRMATION
            spec.fluid.kinematic_viscosity_m2_s.status = FieldStatus.AWAITING_CONFIRMATION

    # --- Metrics: ensure user-requested metrics are included ---
    requested_metrics = llm_parsed.get("requested_metrics", [])
    existing_obs_labels = {obs.label or obs.type.value for obs in spec.observables}

    # Map LLM metric keys to (ObservableType, label)
    metric_mapping = {
        "drag_coefficient": (ObservableType.CYLINDER_DRAG, "圆柱阻力"),
        "lift_coefficient": (ObservableType.CYLINDER_LIFT, "圆柱升力"),
        "vorticity": (ObservableType.VORTICITY_FIELD, "涡量场"),
        "shedding_frequency": (ObservableType.WAKE_SHEDDING_FREQUENCY, "涡脱落频率"),
        "strouhal_number": (ObservableType.WAKE_SHEDDING_FREQUENCY, "Strouhal数"),
        "force_time_series": (ObservableType.DRAG_LIFT_TIME_SERIES, "阻力升力时间序列"),
        "wake_visualization": (ObservableType.STREAMLINES, "尾迹可视化"),
        "pressure_coefficient": (ObservableType.PRESSURE_FIELD, "压力场"),
        "velocity_field": (ObservableType.VELOCITY_MAGNITUDE_FIELD, "速度场"),
        "recirculation": (ObservableType.RECIRCULATION_LENGTH, "回流区长度"),
    }
    for metric_key in requested_metrics:
        mapping = metric_mapping.get(metric_key)
        if mapping is None:
            continue
        obs_type, label = mapping
        if label not in existing_obs_labels:
            spec.observables.append(ObservableSpec(
                type=obs_type,
                label=label,
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=0.9,
            ))
            existing_obs_labels.add(label)

    # --- Ambiguities: add as blocking issues ---
    ambiguities = llm_parsed.get("ambiguities", [])
    for amb in ambiguities:
        spec.blocking_issues.append({
            "code": "LLM_AMBIGUITY",
            "message": f"LLM识别的歧义: {amb}",
        })

    # --- Missing fields: filter out derivable parameters before blocking ---
    missing = llm_parsed.get("missing_fields", [])
    # Check derivability from the SPEC itself (not LLM output), because the
    # pipeline has already extracted Re/U/D from user text and derived nu.
    # If the spec already has a resolved kinematic_viscosity (formula-derived
    # or user-provided), it is NOT missing regardless of what the LLM says.
    nu_field = spec.fluid.kinematic_viscosity_m2_s
    nu_already_resolved = nu_field.is_resolved()
    # Also check if we CAN derive it right now from spec values
    spec_re = spec.estimate_reynolds()
    spec_u = spec.boundaries.left.inlet_velocity
    spec_d = spec.get_cylinder_diameter()
    can_derive_viscosity = (
        nu_already_resolved
        or (spec_re is not None and spec_re > 0
            and spec_u is not None and spec_u > 0
            and spec_d is not None and spec_d > 0)
    )
    # Check if diameter/radius is derivable from the other
    can_derive_diameter = spec.get_cylinder_radius() is not None
    can_derive_radius = spec.get_cylinder_diameter() is not None
    # Check if inlet_velocity is derivable (U = Re * nu / D)
    can_derive_velocity = (
        spec_re is not None and spec_re > 0
        and nu_field.value is not None and nu_field.value > 0
        and spec_d is not None and spec_d > 0
    )
    for field in missing:
        field_lower = str(field).lower().strip()
        # Skip kinematic_viscosity if already resolved or derivable
        if field_lower in ("kinematic_viscosity", "运动粘度", "运动黏度", "nu", "viscosity") and can_derive_viscosity:
            # Already derived in pipeline Pass 4 — skip blocking
            continue
        # Skip diameter/radius if the other is available
        if field_lower in ("cylinder_diameter", "圆柱直径", "diameter") and can_derive_diameter:
            continue
        if field_lower in ("cylinder_radius", "圆柱半径", "radius") and can_derive_radius:
            continue
        # Skip inlet_velocity if derivable from Re, nu, D
        if field_lower in ("inlet_velocity", "来流速度", "velocity", "velocity_inlet") and can_derive_velocity:
            continue
        # Skip reference_length, reference_area, characteristic_dimension (always derivable from D)
        if field_lower in ("reference_length", "参考长度", "characteristic_dimension", "特征尺度") and spec_d is not None:
            continue
        if field_lower in ("reference_area", "参考面积") and spec_d is not None:
            continue
        spec.blocking_issues.append({
            "code": "LLM_MISSING_FIELD",
            "message": f"LLM识别的缺失字段: {field}",
        })


def _check_unsupported_geometry(spec) -> None:
    """Check if spec contains unsupported geometry and block if so.

    Rectangle and triangle obstacles are now supported via snappyHexMesh + STL.
    If the regex pipeline set bottom_profile to COSINE_BELL for a
    rectangle or triangle input, but the LLM correctly identified it,
    clear the bottom_profile to avoid conflict.
    """
    from fluid_scientist.cylinder_flow_2d.models import BumpProfileType, BottomProfileSpec

    # If rectangle is enabled (LLM identified it), clear any
    # bottom_profile that the regex pipeline might have set
    if spec.rectangle.enabled and spec.bottom_profile.profile_type:
        if spec.bottom_profile.profile_type == BumpProfileType.COSINE_BELL:
            spec.bottom_profile = BottomProfileSpec()

    # If triangle is enabled (LLM or regex identified it), clear any
    # bottom_profile that the regex pipeline might have set
    if spec.triangle.enabled and spec.bottom_profile.profile_type:
        if spec.bottom_profile.profile_type != BumpProfileType.FLAT:
            spec.bottom_profile = BottomProfileSpec()

    # Triangle is now SUPPORTED — do not block it
    # Only block truly unsupported geometries (not triangle/rectangle/cosine_bell)


def _detect_position_conflicts(spec, user_text: str) -> None:
    """Detect conflicting position descriptions in user text.

    Example: "圆心距下壁面2m" (y=2.0) vs "位于流场正中央" (y=2.5 for 5m height)
    These are BLOCKING_CONFLICT issues that must be resolved by the user.
    """
    import re as _re

    # Detect cylinder Y position conflict:
    # "圆心距下壁面Xm" gives y=X
    # "位于流场正中央" gives y=height/2
    wall_dist_match = _re.search(r'圆心距.*?壁面\s*([\d.]+)\s*m', user_text)
    has_center = "正中央" in user_text or "几何正中央" in user_text or "中心" in user_text

    if wall_dist_match and has_center:
        wall_y = float(wall_dist_match.group(1))
        domain_h = spec.domain.height_m.value
        if domain_h and domain_h > 0:
            center_y = domain_h / 2.0
            if abs(wall_y - center_y) > 0.01:  # conflict
                spec.blocking_issues.append({
                    "code": "CYLINDER_POSITION_CONFLICT",
                    "message": (
                        f"圆柱纵向位置存在冲突："
                        f"'圆心距下壁面{wall_y}m' 对应 y={wall_y}m，"
                        f"而'位于流场正中央'对应 y={center_y}m（流场高度{domain_h}m的中央）。"
                        f"建议保留精确数值'{wall_y}m'，将'正中央'解释为仅在水平方向居中。"
                    ),
                    "conflict_type": "BLOCKING_CONFLICT",
                    "option_a": f"圆心设为 y={wall_y}m（保留'距下壁面{wall_y}m'），水平方向居中",
                    "option_b": f"圆心设为 y={center_y}m（几何正中央）",
                    "option_c": "自定义位置",
                })


def _validate_null_fields(spec) -> list[dict]:
    """Validate that critical spec fields are not null.

    Returns a list of blocking issues for any null critical fields.
    This prevents specs with null coordinates from passing Gate 1.
    """
    issues = []

    # Cylinder center coordinates must not be null
    if spec.has_cylinder:
        if spec.cylinder.center_x_m.value is None:
            issues.append({
                "code": "CYLINDER_CENTER_X_NULL",
                "message": "圆柱x坐标为null，无法确定圆柱在流向上的位置。请指定圆柱中心x坐标或距入口的距离。",
            })
        if spec.cylinder.center_y_m.value is None:
            issues.append({
                "code": "CYLINDER_CENTER_Y_NULL",
                "message": "圆柱y坐标为null，无法确定圆柱在垂直方向的位置。请指定圆柱中心y坐标或距壁面的距离。",
            })

    # Cylinder radius/diameter must not be null
    if spec.has_cylinder:
        if (spec.cylinder.radius_m.value is None and
                spec.cylinder.diameter_m.value is None):
            issues.append({
                "code": "CYLINDER_DIMENSION_NULL",
                "message": "圆柱半径和直径均为null，无法确定圆柱尺寸。",
            })

    # Inlet velocity must not be null
    if (spec.boundaries and spec.boundaries.left and
            spec.boundaries.left.inlet_velocity is None):
        issues.append({
            "code": "INLET_VELOCITY_NULL",
            "message": "入口速度为null，无法确定来流条件。",
        })

    # Rectangle dimensions must not be null if rectangle is enabled
    if spec.has_rectangle:
        if spec.rectangle.width_m.value is None:
            issues.append({
                "code": "RECTANGLE_WIDTH_NULL",
                "message": "矩形宽度为null，无法确定矩形尺寸。",
            })
        if spec.rectangle.height_m.value is None:
            issues.append({
                "code": "RECTANGLE_HEIGHT_NULL",
                "message": "矩形高度为null，无法确定矩形尺寸。",
            })

    # Triangle dimensions must not be null if triangle is enabled
    if spec.triangle.enabled:
        if spec.triangle.base_width_m.value is None:
            issues.append({
                "code": "TRIANGLE_BASE_WIDTH_NULL",
                "message": "三角形底宽为null，无法确定三角形尺寸。",
            })
        if spec.triangle.height_m.value is None:
            issues.append({
                "code": "TRIANGLE_HEIGHT_NULL",
                "message": "三角形高度为null，无法确定三角形尺寸。",
            })

    # Trapezoid dimensions must not be null if trapezoid is enabled
    if spec.trapezoid.enabled:
        if spec.trapezoid.top_width_m.value is None:
            issues.append({
                "code": "TRAPEZOID_TOP_WIDTH_NULL",
                "message": "梯形上底为null，无法确定梯形尺寸。",
            })
        if spec.trapezoid.bottom_width_m.value is None:
            issues.append({
                "code": "TRAPEZOID_BOTTOM_WIDTH_NULL",
                "message": "梯形下底为null，无法确定梯形尺寸。",
            })
        if spec.trapezoid.height_m.value is None:
            issues.append({
                "code": "TRAPEZOID_HEIGHT_NULL",
                "message": "梯形高度为null，无法确定梯形尺寸。",
            })

    return issues


def _has_blocking_substitution(spec) -> bool:
    """Check if spec has blocking geometry substitution issues."""
    for issue in spec.blocking_issues:
        code = issue.get("code", "") if isinstance(issue, dict) else ""
        if code in ("SILENT_GEOMETRY_SUBSTITUTION", "UNSUPPORTED_GEOMETRY",
                     "UNSUPPORTED_CAPABILITY"):
            return True
    return False


def _semantic_consistency_gate(spec, user_text: str) -> dict:
    """SemanticConsistencyGate — check user intent matches compiled spec.

    This gate runs BEFORE compilation to prevent silent semantic substitution.
    If user input mentions "triangle" but spec has cosine_bell, this gate
    blocks compilation with SEMANTIC_GEOMETRY_MISMATCH.

    Returns:
        {"passed": bool, "violations": list[dict]}
    """
    import re as _re
    violations = []
    text_lower = user_text.lower()

    # 1. Check triangle vs cosine_bell substitution
    user_says_triangle = any(kw in text_lower for kw in ["三角", "triangle", "triangular"])
    user_says_cosine = any(kw in text_lower for kw in ["余弦", "cosine", "cosine bell", "余弦钟", "余弦丘"])
    user_says_rectangle = any(kw in text_lower for kw in ["矩形", "rectangle", "rectangular"])

    if user_says_triangle:
        if spec.has_bottom_profile and spec.bottom_profile.profile_type:
            from fluid_scientist.cylinder_flow_2d.models import BumpProfileType
            if spec.bottom_profile.profile_type == BumpProfileType.COSINE_BELL:
                violations.append({
                    "code": "SEMANTIC_GEOMETRY_MISMATCH",
                    "message": "用户输入为三角形(triangle_2d)，但spec中几何被替换为cosine_bell。禁止编译。",
                    "expected": "triangle_2d",
                    "actual": "cosine_bell_2d",
                })
        if not spec.has_triangle and not spec.has_bottom_profile:
            violations.append({
                "code": "SEMANTIC_GEOMETRY_NOT_FOUND",
                "message": "用户输入为三角形(triangle_2d)，但spec中未找到三角形几何。",
                "expected": "triangle_2d",
                "actual": "none",
            })

    if user_says_rectangle and not user_says_triangle:
        if spec.has_bottom_profile and spec.bottom_profile.profile_type:
            from fluid_scientist.cylinder_flow_2d.models import BumpProfileType
            if spec.bottom_profile.profile_type == BumpProfileType.COSINE_BELL:
                violations.append({
                    "code": "SEMANTIC_GEOMETRY_MISMATCH",
                    "message": "用户输入为矩形(rectangle_2d)，但spec中几何被替换为cosine_bell。禁止编译。",
                    "expected": "rectangle_2d",
                    "actual": "cosine_bell_2d",
                })

    # 2. Check entity count (basic)
    if user_says_triangle and not spec.has_triangle:
        if not violations:  # Only add if not already caught above
            violations.append({
                "code": "ENTITY_COUNT_MISMATCH",
                "message": "用户描述了三角形障碍物，但spec中未包含三角形实体。",
            })

    return {
        "passed": len(violations) == 0,
        "violations": violations,
    }


def _semantic_coverage_check(spec, user_text: str) -> dict:
    """Check that all user-stated claims are covered in the spec.

    Returns a dict with:
      coverage_rate: float 0-1
      mapped_claims: list of {claim, field}
      unmapped_claims: list of {claim, reason}
      contradictions: list of {claim, expected, actual}
      silent_substitutions: list of {claim, expected, actual}
    """
    import re as _regex
    from fluid_scientist.cylinder_flow_2d.models import (
        BumpProfileType, SemanticBoundaryType,
    )

    mapped = []
    unmapped = []
    contradictions = []
    silent_subs = []
    text_lower = user_text.lower()

    def _pf_val(pf):
        """Safely extract value from ProvenanceField."""
        return pf.value if pf and hasattr(pf, 'value') else None

    # 1. Dimension
    if "二维" in user_text or "2d" in text_lower:
        if spec.domain.dimensionality == "2D":
            mapped.append({"claim": "二维流场", "field": "domain.dimensionality"})
        else:
            unmapped.append({"claim": "二维流场", "reason": f"dimensionality={spec.domain.dimensionality}"})

    # 2. Cylinder radius
    radius_match = _regex.search(r'半径\s*[:=]?\s*([\d.]+)\s*m?', user_text)
    if radius_match:
        r_val = float(radius_match.group(1))
        spec_r = _pf_val(spec.cylinder.radius_m)
        if spec_r is not None and abs(spec_r - r_val) < 1e-6:
            mapped.append({"claim": f"圆柱半径{r_val}m", "field": "cylinder.radius_m"})
        elif spec_r is not None:
            contradictions.append({"claim": f"圆柱半径{r_val}m", "expected": r_val, "actual": spec_r})
        else:
            unmapped.append({"claim": f"圆柱半径{r_val}m", "reason": "radius_m is null"})

    # 3. Cylinder center Y (wall distance)
    wall_match = _regex.search(r'圆心距.*?壁面\s*([\d.]+)\s*m', user_text)
    if wall_match:
        y_val = float(wall_match.group(1))
        spec_y = _pf_val(spec.cylinder.center_y_m)
        if spec_y is not None and abs(spec_y - y_val) < 1e-6:
            mapped.append({"claim": f"圆心距下壁面{y_val}m", "field": "cylinder.center_y_m"})
        elif spec_y is not None:
            contradictions.append({"claim": f"圆心距下壁面{y_val}m", "expected": y_val, "actual": spec_y})
        else:
            unmapped.append({"claim": f"圆心距下壁面{y_val}m", "reason": "center_y_m is null"})

    # 4. Inlet velocity
    vel_match = _regex.search(r'(?:来流速度|入口速度|速度)\s*[:=]?\s*([\d.]+)\s*m/s', user_text)
    if vel_match:
        v_val = float(vel_match.group(1))
        spec_v = spec.boundaries.left.inlet_velocity
        if spec_v is not None and abs(spec_v - v_val) < 1e-6:
            mapped.append({"claim": f"来流速度{v_val}m/s", "field": "boundaries.left.inlet_velocity"})
        else:
            unmapped.append({"claim": f"来流速度{v_val}m/s", "reason": f"inlet_velocity={spec_v}"})

    # 5. Reynolds number
    re_match = _regex.search(r'Re\s*[:=]?\s*([\d.]+)', user_text)
    if re_match:
        re_val = float(re_match.group(1))
        spec_re = spec.estimate_reynolds()
        if spec_re is not None and abs(spec_re - re_val) / max(re_val, 1) < 0.05:
            mapped.append({"claim": f"Re={re_val}", "field": "reynolds_number"})
        else:
            unmapped.append({"claim": f"Re={re_val}", "reason": f"estimated Re={spec_re}"})

    # 6. Rectangle obstacle
    if any(kw in user_text for kw in ["矩形", "rectangle", "rectangular"]):
        # Rectangle is now supported — check if it's in spec.rectangle
        if spec.rectangle.enabled:
            mapped.append({"claim": "矩形障碍物", "field": "rectangle.enabled"})
        elif spec.bottom_profile and spec.bottom_profile.profile_type:
            if spec.bottom_profile.profile_type == BumpProfileType.COSINE_BELL:
                silent_subs.append({
                    "claim": "矩形障碍物",
                    "expected": "rectangle",
                    "actual": "cosine_bell",
                })
            else:
                unmapped.append({"claim": "矩形障碍物", "reason": f"profile_type={spec.bottom_profile.profile_type}"})
        else:
            unmapped.append({"claim": "矩形障碍物", "reason": "rectangle not enabled and no bottom_profile"})

    # 7. Rectangle dimensions
    rect_h = _regex.search(r'高\s*([\d.]+)\s*m', user_text)
    rect_w = _regex.search(r'宽\s*([\d.]+)\s*m', user_text)
    if rect_h:
        h_val = float(rect_h.group(1))
        spec_h = spec.rectangle.height_m.value if spec.rectangle.height_m else None
        if spec_h is not None and abs(spec_h - h_val) < 1e-6:
            mapped.append({"claim": f"矩形高{h_val}m", "field": "rectangle.height_m"})
        else:
            unmapped.append({"claim": f"矩形高{h_val}m", "reason": f"height_m={spec_h}"})
    if rect_w:
        w_val = float(rect_w.group(1))
        spec_w = spec.rectangle.width_m.value if spec.rectangle.width_m else None
        if spec_w is not None and abs(spec_w - w_val) < 1e-6:
            mapped.append({"claim": f"矩形宽{w_val}m", "field": "rectangle.width_m"})
        else:
            unmapped.append({"claim": f"矩形宽{w_val}m", "reason": f"width_m={spec_w}"})

    # 7b. Triangle obstacle — detect silent substitution to cosine_bell
    if any(kw in user_text for kw in ["三角", "triangle", "triangular"]):
        if spec.has_triangle:
            mapped.append({"claim": "三角形障碍物", "field": "triangle.enabled"})
        elif spec.has_bottom_profile and spec.bottom_profile.profile_type:
            if spec.bottom_profile.profile_type == BumpProfileType.COSINE_BELL:
                silent_subs.append({
                    "claim": "三角形障碍物",
                    "expected": "triangle_2d",
                    "actual": "cosine_bell",
                })
            else:
                unmapped.append({"claim": "三角形障碍物", "reason": f"profile_type={spec.bottom_profile.profile_type}"})
        else:
            unmapped.append({"claim": "三角形障碍物", "reason": "triangle not enabled and no bottom_profile"})

    # 7c. Trapezoid obstacle — detect silent dropping
    if any(kw in user_text for kw in ["梯形", "trapezoid", "trapezoidal"]):
        if spec.trapezoid and spec.trapezoid.enabled:
            mapped.append({"claim": "梯形障碍物", "field": "trapezoid.enabled"})
        else:
            unmapped.append({"claim": "梯形障碍物", "reason": "trapezoid not enabled in spec"})

    # 8. Boundary conditions
    if "速度入口" in user_text or "velocity inlet" in text_lower:
        if "左" in user_text:
            left_type = spec.boundaries.left.semantic_type
            if left_type in (SemanticBoundaryType.UNIFORM_VELOCITY_INLET,
                             SemanticBoundaryType.TIME_VARYING_VELOCITY_INLET):
                mapped.append({"claim": "左速度入口", "field": "boundaries.left"})
            else:
                unmapped.append({"claim": "左速度入口", "reason": f"left={left_type}"})

    if "压力出口" in user_text or "pressure outlet" in text_lower:
        if "右" in user_text:
            right_type = spec.boundaries.right.semantic_type
            if right_type == SemanticBoundaryType.PRESSURE_OUTLET:
                mapped.append({"claim": "右压力出口", "field": "boundaries.right"})
            else:
                unmapped.append({"claim": "右压力出口", "reason": f"right={right_type}"})

    if "无滑移" in user_text:
        if "下" in user_text or "底" in user_text:
            bottom_type = spec.boundaries.bottom_flat.semantic_type
            if bottom_type == SemanticBoundaryType.NO_SLIP_WALL:
                mapped.append({"claim": "下无滑移", "field": "boundaries.bottom_flat"})
            else:
                unmapped.append({"claim": "下无滑移", "reason": f"bottom={bottom_type}"})

    # 9. Observables / metrics
    obs_names = {obs.label or obs.type.value for obs in spec.observables}
    if "升力" in user_text or "lift" in text_lower:
        if any("升力" in n for n in obs_names):
            mapped.append({"claim": "升力系数", "field": "observables"})
        else:
            unmapped.append({"claim": "升力系数", "reason": "not in observables"})

    if "阻力" in user_text or "drag" in text_lower:
        if any("阻力" in n for n in obs_names):
            mapped.append({"claim": "阻力系数", "field": "observables"})
        else:
            unmapped.append({"claim": "阻力系数", "reason": "not in observables"})

    if "涡街" in user_text or "涡脱落" in user_text or "vortex" in text_lower or "shedding" in text_lower:
        if any("涡" in n or "频率" in n or "shedding" in n.lower() for n in obs_names):
            mapped.append({"claim": "涡街/涡脱落频率", "field": "observables"})
        else:
            unmapped.append({"claim": "涡街/涡脱落频率", "reason": "not in observables"})

    # Calculate coverage rate
    total = len(mapped) + len(unmapped) + len(contradictions)
    coverage_rate = len(mapped) / total if total > 0 else 1.0

    return {
        "coverage_rate": round(coverage_rate, 4),
        "mapped_claims": mapped,
        "unmapped_claims": unmapped,
        "contradictions": contradictions,
        "silent_substitutions": silent_subs,
    }


@router.post("/draft", response_model=DraftResponse)
async def create_draft(request: DraftRequest) -> DraftResponse:
    """Create a draft spec from natural language input.

    This runs the full pipeline:
    0. LLM structured parsing (real model call)
    1. Fact extraction (regex cross-validation)
    2. Ambiguity detection
    3. Scientific normalization
    4. Deterministic field derivation
    5. Observable extraction + recommendation
    6. Critic review

    Returns the complete spec with draft_status, blocking_issues,
    observables, and analysis_goals.
    """
    # --- Step 0: Real LLM structured parsing ---
    # This is mandatory — no fallback to regex-only.
    _skill_executor.clear()
    llm_parsed = _skill_executor.execute(
        skill_id="fluid.intent_to_spec",
        entrypoint_fn=lambda data: _llm_structured_parse(data["user_text"]),
        input_data={"user_text": request.user_text},
    ).output

    # Check for unsupported capabilities identified by LLM
    # Only block on truly unsupported capabilities, not on standard CFD
    # observables that the LLM might misclassify as unsupported.
    _TRULY_UNSUPPORTED_KEYWORDS = [
        "倾斜", "inclined", "射流", "jet",
        "冲击", "impingement", "加热", "heated", "heating",
        "努塞尔", "nusselt", "沸腾", "boiling",
        "多相流", "multiphase", "燃烧", "combustion",
        "动网格", "dynamic mesh", "fsi", "流固耦合",
        "3d", "3D", "三维",
    ]
    unsupported = llm_parsed.get("unsupported_capabilities", [])
    truly_unsupported = []
    for cap in unsupported:
        cap_lower = str(cap).lower()
        if any(kw.lower() in cap_lower for kw in _TRULY_UNSUPPORTED_KEYWORDS):
            truly_unsupported.append(cap)
    if truly_unsupported:
        return DraftResponse(
            success=False,
            error=f"UNSUPPORTED_CAPABILITY: {', '.join(truly_unsupported)}",
            blocking_issues=[{
                "code": "UNSUPPORTED_CAPABILITY",
                "message": f"系统不支持以下能力: {', '.join(truly_unsupported)}",
                "unsupported": truly_unsupported,
            }],
            skill_summary=_skill_executor.summary(),
        )

    # First check if this is a cylinder flow scene
    scene_router = CylinderFlow2DSceneRouter()
    route_result = scene_router.route(request.user_text)
    if not route_result.matched:
        return DraftResponse(
            success=False,
            error="Input does not match cylinder flow scene",
            skill_summary=_skill_executor.summary(),
        )

    # Run the pipeline (regex-based, used as cross-validation)
    pipeline = CylinderFlow2DV1Pipeline()
    run_result = pipeline.run(request.user_text)
    spec = run_result.spec

    # Record skill execution for the pipeline run
    _skill_executor.execute(
        skill_id="fluid.geometry_reasoning",
        entrypoint_fn=lambda data: {"stages": len(data.get("result", {}).stage_history) if hasattr(data.get("result"), 'stage_history') else 0},
        input_data={"user_text": request.user_text, "result": run_result},
    )

    # --- Intent candidate extraction and conflict resolution ---
    # Extract independent candidates from regex pipeline and LLM
    regex_extractor = RegexCandidateExtractor()
    llm_extractor = LLMCandidateExtractor()
    regex_cands = regex_extractor.extract(spec, request.user_text)
    llm_cands = llm_extractor.extract(llm_parsed, request.user_text)

    # Resolve conflicts between regex and LLM candidates
    resolver = ConflictResolver()
    candidate_set = resolver.resolve(regex_cands, llm_cands, request.user_text)

    # Handle duplicate entity conflicts (e.g., sine bump creating both rectangle and bump)
    for conflict in candidate_set.conflicts:
        if conflict.conflict_type.value == "duplicate_entity":
            if conflict.resolution == "keep_bottom_profile_remove_rectangle":
                # Disable rectangle when bump is the intended geometry
                spec.rectangle.enabled = False
                spec.rectangle.width_m = ProvenanceField(
                    value=None, source=FieldSource.SYSTEM_DEFAULT,
                    status=FieldStatus.UNRESOLVED, reason="Removed: duplicate with bottom_profile",
                )
                spec.rectangle.height_m = ProvenanceField(
                    value=None, source=FieldSource.SYSTEM_DEFAULT,
                    status=FieldStatus.UNRESOLVED, reason="Removed: duplicate with bottom_profile",
                )
        elif conflict.conflict_type.value == "semantic_type_conflict" and conflict.severity.value == "blocking":
            # Add blocking conflicts to spec
            spec.blocking_issues.append({
                "code": "CANDIDATE_CONFLICT",
                "message": f"Regex and LLM disagree on {conflict.field_path}: regex={conflict.regex_value}, llm={conflict.llm_value}",
                "conflict": conflict.to_dict(),
            })

    # --- Apply LLM-extracted values over regex output ---
    # LLM takes precedence for semantic fields (with is_resolved() guards)
    _skill_executor.execute(
        skill_id="fluid.metric_spec_builder",
        entrypoint_fn=lambda data: _apply_llm_to_spec(data["spec"], data["llm_parsed"], data["user_text"]),
        input_data={"spec": spec, "llm_parsed": llm_parsed, "user_text": request.user_text},
    )

    # Check for unsupported geometry (rectangle and triangle now supported)
    _skill_executor.execute(
        skill_id="openfoam.geometry_compiler",
        entrypoint_fn=lambda data: _check_unsupported_geometry(data["spec"]),
        input_data={"spec": spec},
    )

    # Run capability resolution — check if spec requires unsupported capabilities
    from fluid_scientist.capabilities.capability_resolver import CapabilityResolver
    _cap_resolver = CapabilityResolver()
    _cap_result = _cap_resolver.check(spec)
    if not _cap_result.all_supported:
        # Add unsupported capabilities as blocking issues
        for cap in _cap_result.unsupported:
            spec.blocking_issues.append({
                "code": "UNSUPPORTED_CAPABILITY",
                "message": f"不支持的能力: {cap}",
                "category": "CAPABILITY",
                "capability": cap,
                "extendable": cap in _cap_result.extendable,
            })

    # Run full ambiguity and conflict audit with 5-category classification
    # This replaces the old simple _detect_position_conflicts
    from fluid_scientist.cylinder_flow_2d.physics_dependency import (
        PhysicsDependencyResolver,
    )
    from fluid_scientist.cylinder_flow_2d.ambiguity_audit import (
        AmbiguityAndConflictAuditor,
    )

    _physics_resolver = PhysicsDependencyResolver()
    _derivation_result = _physics_resolver.resolve(spec)

    _auditor = AmbiguityAndConflictAuditor()
    _audit_result = _auditor.audit(spec, request.user_text, derivation_result=_derivation_result)

    # Run semantic fidelity guard — verify user intent is preserved
    from fluid_scientist.intent.semantic_fidelity_guard import SemanticFidelityGuard
    _fidelity_guard = SemanticFidelityGuard()
    _fidelity_result = _fidelity_guard.check_spec(spec, request.user_text)

    # Add blocking violations from fidelity guard
    for v in _fidelity_result.violations:
        existing_codes = [i.get("code") for i in spec.blocking_issues if isinstance(i, dict)]
        if v.code not in existing_codes:
            spec.blocking_issues.append({
                "code": v.code,
                "message": v.message,
                "category": "SEMANTIC_FIDELITY",
                "severity": v.severity,
                "field_path": v.field_path,
                "evidence": v.evidence,
            })

    # Add blocking issues from audit
    for issue in _audit_result.blocking_issues:
        # Avoid duplicates
        existing_codes = [i.get("code") for i in spec.blocking_issues if isinstance(i, dict)]
        if issue.code not in existing_codes:
            spec.blocking_issues.append({
                "code": issue.code,
                "message": issue.description,
                "category": issue.category.value,
                "options": issue.options,
                "recommendation": issue.recommendation,
            })

    # Store derived values and assumptions for frontend display
    _derived_values_display = [d.to_display() for d in _derivation_result.derivations]
    _non_blocking_assumptions = [
        i.to_dict() for i in _audit_result.assumptions
    ]
    _derived_value_issues = [
        i.to_dict() for i in _audit_result.derived_values
    ]

    # Filter LLM missing_fields: remove fields that were derived
    _derived_field_names = _derivation_result.derived_field_names
    _blocked_missing = set(_derivation_result.blocked_missing_fields)
    spec.blocking_issues = [
        i for i in spec.blocking_issues
        if not (
            isinstance(i, dict)
            and i.get("code") == "LLM_MISSING_FIELD"
            and any(
                kw in str(i.get("message", "")).lower()
                for kw in [f.lower() for f in _blocked_missing]
            )
        )
    ]

    # Deduplicate: remove LLM_AMBIGUITY issues that are already covered
    # by more specific audit issues (e.g., TOP_BOUNDARY_AMBIGUITY)
    _audit_codes = {i.code for i in _audit_result.issues}
    spec.blocking_issues = [
        i for i in spec.blocking_issues
        if not (
            isinstance(i, dict)
            and i.get("code") == "LLM_AMBIGUITY"
            and any(
                ac in _audit_codes
                for ac in ["TOP_BOUNDARY_AMBIGUITY", "BOTTOM_BOUNDARY_AMBIGUITY",
                           "CYLINDER_Y_POSITION_CONFLICT", "CYLINDER_X_POSITION_CONFLICT"]
            )
            and ("出流" in str(i.get("message", "")) or "边界" in str(i.get("message", ""))
                 or "位置" in str(i.get("message", "")) or "中央" in str(i.get("message", "")))
        )
    ]

    # Deduplicate: remove CYLINDER_CENTER_X_NULL if CYLINDER_CENTER_X_MISSING exists
    _has_x_missing = any(
        isinstance(i, dict) and i.get("code") == "CYLINDER_CENTER_X_MISSING"
        for i in spec.blocking_issues
    )
    if _has_x_missing:
        spec.blocking_issues = [
            i for i in spec.blocking_issues
            if not (isinstance(i, dict) and i.get("code") == "CYLINDER_CENTER_X_NULL")
        ]

    # Validate null fields and add as blocking issues
    null_issues = _validate_null_fields(spec)
    if null_issues:
        spec.blocking_issues.extend(null_issues)

    # Re-evaluate draft status: if blocking issues exist, status must be NEEDS_CLARIFICATION
    if spec.blocking_issues:
        spec.draft_status = DraftStatus.NEEDS_CLARIFICATION
    else:
        # Re-run readiness evaluator to get correct status
        from fluid_scientist.cylinder_flow_2d.readiness import (
            CylinderFlow2DDraftReadinessEvaluator,
        )
        evaluator = CylinderFlow2DDraftReadinessEvaluator()
        evaluator.evaluate(spec)

    # Store the spec
    spec_id = f"spec_{uuid.uuid4().hex[:12]}"
    spec.experiment_id = spec_id
    _persist_spec(spec_id, spec)

    # Store LLM call record on spec for traceability
    llm_client = _get_llm_client()
    if llm_client:
        last_record = llm_client.get_last_record()
        if last_record:
            if not hasattr(spec, '_llm_call_records') or spec._llm_call_records is None:
                spec._llm_call_records = []
            spec._llm_call_records.append({
                "call_id": last_record.call_id,
                "provider": last_record.provider,
                "model": last_record.model_name,
                "purpose": last_record.purpose,
                "prompt_version": last_record.prompt_version,
                "input_summary": last_record.input_summary[:200],
                "latency_ms": last_record.latency_ms,
                "success": last_record.success,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    return DraftResponse(
        success=True,
        spec_id=spec_id,
        pipeline_id=run_result.pipeline_id,
        schema_name=run_result.schema_name,
        pipeline_version=run_result.pipeline_version,
        pipeline_stage=run_result.pipeline_stage,
        spec_version=spec.spec_version,
        draft_status=spec.draft_status.value,
        spec=spec.model_dump(),
        semantic_display=spec.to_semantic_display(),
        blocking_issues=spec.blocking_issues,
        clarification_questions=_generate_clarification_questions(spec.blocking_issues, spec),
        observables=[obs.model_dump() for obs in spec.observables],
        analysis_goals=[goal.model_dump() for goal in spec.analysis_goals],
        stage_history=[
            {
                "stage": s.stage_name,
                "success": s.success,
                "errors": s.errors,
                "warnings": s.warnings,
            }
            for s in run_result.stage_history
        ],
        decision_summary=spec.decision_summary.model_dump(),
        semantic_coverage=_semantic_coverage_check(spec, request.user_text),
        derived_values=_derived_values_display,
        non_blocking_assumptions=_non_blocking_assumptions,
        derived_value_issues=_derived_value_issues,
        audit_issues=[i.to_dict() for i in _audit_result.issues],
        llm_call_info={
            "call_id": spec._llm_call_records[-1]["call_id"] if hasattr(spec, '_llm_call_records') and spec._llm_call_records else None,
            "provider": spec._llm_call_records[-1]["provider"] if hasattr(spec, '_llm_call_records') and spec._llm_call_records else None,
            "model": spec._llm_call_records[-1]["model"] if hasattr(spec, '_llm_call_records') and spec._llm_call_records else None,
            "latency_ms": spec._llm_call_records[-1]["latency_ms"] if hasattr(spec, '_llm_call_records') and spec._llm_call_records else None,
            "success": spec._llm_call_records[-1]["success"] if hasattr(spec, '_llm_call_records') and spec._llm_call_records else None,
        } if hasattr(spec, '_llm_call_records') and spec._llm_call_records else None,
        skill_summary=_skill_executor.summary(),
        intent_candidates=candidate_set.to_dict(),
    )


@router.post("/revalidate", response_model=DraftResponse)
async def revalidate_spec(request: RevalidateRequest) -> DraftResponse:
    """Re-validate an existing spec after changes."""
    spec = _load_spec(request.spec_id)
    if spec is None and request.spec is not None:
        spec = CylinderFlow2DExperimentSpecV1(**request.spec)
    if spec is None:
        return DraftResponse(
            success=False,
            error=f"Spec not found: {request.spec_id}",
        )

    # Re-run pipeline on the existing spec's user input
    pipeline = CylinderFlow2DV1Pipeline()
    run_result = pipeline.run(spec.user_input_text or "")
    new_spec = run_result.spec
    new_spec.experiment_id = spec.experiment_id
    new_spec.spec_version = spec.spec_version + 1
    _persist_spec(request.spec_id, new_spec)

    return DraftResponse(
        success=True,
        spec_id=request.spec_id,
        spec_version=new_spec.spec_version,
        draft_status=new_spec.draft_status.value,
        spec=new_spec.model_dump(),
        semantic_display=new_spec.to_semantic_display(),
        blocking_issues=new_spec.blocking_issues,
        observables=[obs.model_dump() for obs in new_spec.observables],
        analysis_goals=[goal.model_dump() for goal in new_spec.analysis_goals],
        decision_summary=new_spec.decision_summary.model_dump(),
    )


@router.post("/modify", response_model=DraftResponse)
async def modify_spec(request: ModifyRequest) -> DraftResponse:
    """Apply a natural language modification to an existing spec.

    Uses incremental modification: only parameters explicitly mentioned in
    the modification_text are changed. All previously confirmed values
    (USER_CONFIRMED source) are preserved. Derived fields are re-derived.
    """
    spec = _load_spec(request.spec_id)
    if spec is None:
        # Recovery: re-run pipeline from user_input if provided
        if request.user_input:
            pipeline_recovery = CylinderFlow2DV1Pipeline()
            spec = pipeline_recovery.run(request.user_input).spec
            _persist_spec(request.spec_id, spec)
        else:
            return DraftResponse(
                success=False,
                error=f"Spec not found: {request.spec_id}. Server may have restarted.",
            )
    pipeline = CylinderFlow2DV1Pipeline()
    # Preprocess: strip modification keywords so extraction patterns match
    mod_text = request.modification_text
    for kw in ["改为", "换成", "调整为", "变更", "改成", "修改为", "设为", "设置为"]:
        mod_text = mod_text.replace(kw, "为")
    for kw in ["增大", "增加"]:
        mod_text = mod_text.replace(kw, "改为")
    for kw in ["减小", "减少"]:
        mod_text = mod_text.replace(kw, "改为")
    run_result = pipeline.run(request.modification_text)
    mod_spec = run_result.spec

    # --- Incremental merge: only apply fields that are explicitly set in mod_spec ---
    import re as _re_modify

    mod_lower = mod_text.lower()

    # Domain
    domain = pipeline._extract_domain(mod_text)
    if "length" in domain:
        spec.domain.length_m = ProvenanceField(
            value=domain["length"], source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改域长度",
        )
    if "height" in domain:
        spec.domain.height_m = ProvenanceField(
            value=domain["height"], source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改域高度",
        )

    # Cylinder diameter/radius
    diameter = pipeline._extract_diameter(mod_text)
    if diameter is None:
        radius = pipeline._extract_radius(mod_text)
        if radius is not None:
            diameter = radius * 2
    if diameter is not None:
        spec.cylinder.diameter_m = ProvenanceField(
            value=diameter, source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改圆柱直径",
        )
        spec.cylinder.radius_m = ProvenanceField(
            value=diameter / 2, source=FieldSource.FORMULA_DERIVED,
            status=FieldStatus.RESOLVED, confidence=1.0, reason="由直径推导半径",
        )
        spec.cylinder.characteristic_dimension_m = spec.cylinder.diameter_m

    # Cylinder center
    center = pipeline._extract_cylinder_position(mod_text)
    if center:
        if "x" in center:
            spec.cylinder.center_x_m = ProvenanceField(
                value=center["x"], source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改圆心x",
            )
        if "y" in center:
            spec.cylinder.center_y_m = ProvenanceField(
                value=center["y"], source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改圆心y",
            )

    # Angular velocity
    angular_vel = pipeline._extract_angular_velocity(mod_text)
    if angular_vel is not None:
        spec.cylinder.angular_velocity_rad_s = angular_vel
        from fluid_scientist.cylinder_flow_2d.models import CylinderWallType
        spec.cylinder.wall_type = CylinderWallType.ROTATING_WALL

    # Inlet velocity
    inlet_vel = pipeline._extract_inlet_velocity(mod_text)
    if inlet_vel is not None:
        spec.boundaries.left.inlet_velocity = inlet_vel
        spec.boundaries.left.status = FieldStatus.RESOLVED

    # End time
    end_time = pipeline._extract_end_time(mod_text)
    if end_time is not None:
        spec.simulation.end_time = end_time

    # Fluid type
    if "水" in mod_text or "water" in mod_lower:
        spec.fluid.type = ProvenanceField(
            value="water", source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改流体为水",
        )
    if "空气" in mod_text or "air" in mod_lower:
        spec.fluid.type = ProvenanceField(
            value="air", source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改流体为空气",
        )

    # Density
    density = pipeline._extract_density(mod_text)
    if density is not None:
        spec.fluid.density_kg_m3 = ProvenanceField(
            value=density, source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改密度",
        )

    # Viscosity
    viscosity = pipeline._extract_viscosity(mod_text)
    if viscosity is not None:
        spec.fluid.kinematic_viscosity_m2_s = ProvenanceField(
            value=viscosity, source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改粘度",
        )

    # Bump
    bump = pipeline._extract_bump(mod_text)
    if bump:
        spec.bottom_profile.enabled = True
        # Profile type
        from fluid_scientist.cylinder_flow_2d.models import BumpProfileType
        if bump.get("profile_type") == "cosine_bell":
            spec.bottom_profile.profile_type = BumpProfileType.COSINE_BELL
        elif bump.get("profile_type") == "half_sine":
            spec.bottom_profile.profile_type = BumpProfileType.HALF_SINE
        elif bump.get("profile_type") == "gaussian":
            spec.bottom_profile.profile_type = BumpProfileType.GAUSSIAN
        if "height" in bump:
            spec.bottom_profile.height_m = ProvenanceField(
                value=bump["height"], source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改凸起高度",
            )
        if "width" in bump:
            spec.bottom_profile.width_m = ProvenanceField(
                value=bump["width"], source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改凸起宽度",
            )
        if "center_x" in bump:
            spec.bottom_profile.center_x_m = ProvenanceField(
                value=bump["center_x"], source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED, confidence=1.0, reason="用户修改凸起位置",
            )
        if bump.get("aligned_below_cylinder"):
            spec.bottom_profile.aligned_below_cylinder = True

    # Re-derive dependent fields
    from fluid_scientist.cylinder_flow_2d.geometry_normalizer import (
        CylinderFlow2DGeometryNormalizer,
        CylinderFlow2DDerivedFieldResolver,
    )
    normalizer = CylinderFlow2DGeometryNormalizer()
    normalizer.normalize(spec, spec.user_input_text or "")
    resolver = CylinderFlow2DDerivedFieldResolver()
    resolver.resolve(spec)

    # Re-run readiness
    from fluid_scientist.cylinder_flow_2d.readiness import (
        CylinderFlow2DDraftReadinessEvaluator,
    )
    evaluator = CylinderFlow2DDraftReadinessEvaluator()
    spec.draft_status = evaluator.evaluate(spec)

    # Increment version
    spec.spec_version += 1
    # Append modification text to user_input_text for future reference
    if spec.user_input_text:
        spec.user_input_text = spec.user_input_text + "\n[修改] " + mod_text

    _persist_spec(request.spec_id, spec)

    return DraftResponse(
        success=True,
        spec_id=request.spec_id,
        spec_version=spec.spec_version,
        draft_status=spec.draft_status.value,
        spec=spec.model_dump(),
        semantic_display=spec.to_semantic_display(),
        blocking_issues=spec.blocking_issues,
        clarification_questions=_generate_clarification_questions(spec.blocking_issues, spec),
        observables=[obs.model_dump() for obs in spec.observables],
        analysis_goals=[goal.model_dump() for goal in spec.analysis_goals],
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_spec(request: ConfirmRequest) -> ConfirmResponse:
    """Confirm the draft spec.

    This runs the full confirmation chain:
    1. Load current spec
    2. Run geometry normalizer
    3. Run derived-field resolver
    4. Run flow-topology resolver
    5. Run boundary normalizer
    6. Run observable extractor
    7. Run observable recommender
    8. Run analysis-goal builder
    9. Run critic
    10. Run coverage checker
    11. Run readiness evaluator
    12. Run blocking validator
    13. Persist confirmed spec version

    If there are blocking issues, returns NEEDS_CLARIFICATION with
    user-facing questions.
    """
    spec = _load_spec(request.spec_id)
    if spec is None:
        # Recovery: if user_input is provided, re-run pipeline to restore spec
        if request.user_input:
            pipeline = CylinderFlow2DV1Pipeline()
            run_result = pipeline.run(request.user_input)
            spec = run_result.spec
            _persist_spec(request.spec_id, spec)
        else:
            return ConfirmResponse(
                success=False,
                error=f"Spec not found: {request.spec_id}. Server may have restarted. Please re-submit your request.",
            )

    # Apply clarifications if provided
    if request.clarifications:
        for question_id, answer in request.clarifications.items():
            _apply_clarification(spec, question_id, answer)

    # Run the full confirmation chain
    from fluid_scientist.cylinder_flow_2d.geometry_normalizer import (
        CylinderFlow2DGeometryNormalizer,
        CylinderFlow2DDerivedFieldResolver,
    )
    from fluid_scientist.cylinder_flow_2d.boundary_topology import (
        CylinderFlow2DBoundaryTopologyResolver,
        CylinderFlow2DBoundaryCombinationValidator,
    )
    from fluid_scientist.cylinder_flow_2d.observable import (
        CylinderFlow2DObservableExtractor,
        CylinderFlow2DObservableRecommender,
        CylinderFlow2DObservableValidator,
    )
    from fluid_scientist.cylinder_flow_2d.analysis_goals import (
        CylinderFlow2DAnalysisGoalBuilder,
    )
    from fluid_scientist.cylinder_flow_2d.critic import (
        CylinderFlow2DCritic,
        CylinderFlow2DCoverageChecker,
    )
    from fluid_scientist.cylinder_flow_2d.readiness import (
        CylinderFlow2DDraftReadinessEvaluator,
    )

    # 1. Geometry normalizer
    normalizer = CylinderFlow2DGeometryNormalizer()
    normalizer.normalize(spec, spec.user_input_text or "")

    # 2. Derived field resolver
    resolver = CylinderFlow2DDerivedFieldResolver()
    resolver.resolve(spec)

    # 3. Flow topology resolver
    topology_resolver = CylinderFlow2DBoundaryTopologyResolver()
    try:
        flow_mode = topology_resolver.resolve(spec)
        spec.flow_topology = {"mode": flow_mode.value}
    except Exception:
        pass

    # 4. Boundary combination validator
    boundary_validator = CylinderFlow2DBoundaryCombinationValidator()
    boundary_issues = boundary_validator.validate(spec)

    # 5. Observable extractor (re-extract from text)
    extractor = CylinderFlow2DObservableExtractor()
    user_obs = extractor.extract(spec.user_input_text or "")
    existing_types = {obs.type for obs in spec.observables}
    for obs in user_obs:
        if obs.type not in existing_types:
            spec.observables.append(obs)

    # 6. Observable recommender
    recommender = CylinderFlow2DObservableRecommender()
    recommended = recommender.recommend(spec)
    existing_types = {obs.type for obs in spec.observables}
    for rec in recommended:
        if rec.type not in existing_types:
            spec.observables.append(rec)

    # 7. Observable validator
    validator = CylinderFlow2DObservableValidator()
    spec.observables = validator.validate(spec.observables)

    # 8. Analysis goal builder
    goal_builder = CylinderFlow2DAnalysisGoalBuilder()
    goals = goal_builder.build(spec)
    if not spec.analysis_goals:
        spec.analysis_goals.extend(goals)

    # 9. Critic
    critic = CylinderFlow2DCritic()
    critic.review(spec, spec.user_input_text or "")

    # 10. Coverage checker
    coverage = CylinderFlow2DCoverageChecker()
    gaps = coverage.check(spec, spec.user_input_text or "")

    # 11. Readiness evaluator
    evaluator = CylinderFlow2DDraftReadinessEvaluator()
    final_status = evaluator.evaluate(spec)

    # 11b. --- Explicit user confirmation of recommended values ---
    # When the user calls /confirm (accept_recommendations=True),
    # resolve all AWAITING_CONFIRMATION fields to RESOLVED with
    # USER_CONFIRMED source. This is explicit user action, NOT auto-accept.
    if request.accept_recommendations and final_status == DraftStatus.AWAITING_CONFIRMATION:
        _auto_accept_recommendations(spec)
        final_status = evaluator.evaluate(spec)

    # 12. Null field validation: block if critical fields are null
    _blocking_null_issues = _validate_null_fields(spec)
    if _blocking_null_issues:
        spec.blocking_issues.extend(_blocking_null_issues)
        final_status = DraftStatus.NEEDS_CLARIFICATION

    # 12b. Block on silent geometry substitution
    if _has_blocking_substitution(spec):
        final_status = DraftStatus.NEEDS_CLARIFICATION

    # 13. Check if ready to confirm
    if final_status not in (DraftStatus.READY_TO_CONFIRM, DraftStatus.AWAITING_CONFIRMATION):
        # Generate user-facing questions from blocking issues
        questions = _generate_clarification_questions(spec.blocking_issues, spec)

        return ConfirmResponse(
            success=False,
            spec_id=request.spec_id,
            draft_status=final_status.value,
            spec=spec.model_dump(),
            semantic_display=spec.to_semantic_display(),
            blocking_issues=spec.blocking_issues,
            clarification_questions=questions,
            error=spec.blocking_issues[0]["message"] if spec.blocking_issues else "存在未解决的问题",
            debug_details=str(spec.blocking_issues),
        )

    # 13. Confirm
    spec.draft_status = DraftStatus.SPEC_CONFIRMED
    spec.spec_version += 1

    # Apply simulation parameter overrides
    if request.end_time is not None:
        spec.simulation.end_time = request.end_time
    if request.max_courant is not None:
        spec.simulation.max_courant_number = request.max_courant

    _persist_spec(request.spec_id, spec)

    return ConfirmResponse(
        success=True,
        spec_id=request.spec_id,
        draft_status=spec.draft_status.value,
        spec=spec.model_dump(),
        semantic_display=spec.to_semantic_display(),
    )


@router.get("/{spec_id}", response_model=DraftResponse)
async def get_spec(spec_id: str) -> DraftResponse:
    """Read a stored spec by ID."""
    spec = _load_spec(spec_id)
    if spec is None:
        return DraftResponse(
            success=False,
            error=f"Spec not found: {spec_id}",
        )

    return DraftResponse(
        success=True,
        spec_id=spec_id,
        spec_version=spec.spec_version,
        draft_status=spec.draft_status.value,
        spec=spec.model_dump(),
        semantic_display=spec.to_semantic_display(),
        blocking_issues=spec.blocking_issues,
        observables=[obs.model_dump() for obs in spec.observables],
        analysis_goals=[goal.model_dump() for goal in spec.analysis_goals],
    )


@router.get("/schema")
async def get_schema() -> dict[str, Any]:
    """Get the CylinderFlow2DExperimentSpecV1 JSON schema."""
    return CylinderFlow2DExperimentSpecV1.model_json_schema()


# ---------------------------------------------------------------------------
# Execution endpoints: compile → mesh → smoke test → run → postprocess
# ---------------------------------------------------------------------------


class CompileRequest(BaseModel):
    spec_id: str


class CompileResponse(BaseModel):
    success: bool
    spec_id: str
    job_id: str | None = None
    archive_sha256: str | None = None
    file_count: int = 0
    file_list: list[str] = Field(default_factory=list)
    flow_mode: str | None = None
    has_cylinder: bool = False
    has_bump: bool = False
    error: str | None = None


# In-memory store for compiled cases and execution results
_compiled_store: dict[str, dict] = {}
_execution_store: dict[str, dict] = {}


@router.post("/compile", response_model=CompileResponse)
async def compile_spec(request: CompileRequest) -> CompileResponse:
    """Compile a confirmed spec into OpenFOAM case files."""
    spec = _load_spec(request.spec_id)
    if spec is None:
        return CompileResponse(
            success=False,
            spec_id=request.spec_id,
            error=f"Spec not found: {request.spec_id}",
        )

    if spec.draft_status != DraftStatus.SPEC_CONFIRMED:
        return CompileResponse(
            success=False,
            spec_id=request.spec_id,
            error=f"Spec must be SPEC_CONFIRMED, current status: {spec.draft_status}",
        )

    # SemanticConsistencyGate — prevent silent geometry substitution
    user_text = spec.user_input_text or ""
    gate_result = _semantic_consistency_gate(spec, user_text)
    if not gate_result["passed"]:
        violation_msgs = [v["message"] for v in gate_result["violations"]]
        return CompileResponse(
            success=False,
            spec_id=request.spec_id,
            error=f"SEMANTIC_GEOMETRY_MISMATCH: {'; '.join(violation_msgs)}",
        )

    try:
        from fluid_scientist.cylinder_flow_2d.execution import SpecAdapter

        adapter = SpecAdapter()
        obs_spec = adapter.adapt(spec)

        from fluid_scientist.obstacle_flow.compiler import ObstacleFlowCompiler

        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(obs_spec)

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        _compiled_store[job_id] = {
            "spec_id": request.spec_id,
            "archive": compiled.archive,
            "archive_sha256": compiled.archive_sha256,
            "files": compiled.files,
            "manifest": {
                "compilation_id": manifest.compilation_id,
                "spec_version": manifest.spec_version,
                "spec_hash": manifest.spec_hash,
                "case_hash": manifest.case_hash,
                "flow_mode": manifest.flow_mode,
                "has_cylinder": manifest.has_cylinder,
                "has_bump": manifest.has_bump,
            },
        }

        return CompileResponse(
            success=True,
            spec_id=request.spec_id,
            job_id=job_id,
            archive_sha256=compiled.archive_sha256,
            file_count=len(compiled.files),
            file_list=sorted(compiled.files.keys()),
            flow_mode=manifest.flow_mode,
            has_cylinder=manifest.has_cylinder,
            has_bump=manifest.has_bump,
        )
    except Exception as exc:
        return CompileResponse(
            success=False,
            spec_id=request.spec_id,
            error=f"Compilation failed: {exc}",
        )


class ExecuteRequest(BaseModel):
    job_id: str
    parallel: bool = False
    np: int = 4
    skip_smoke: bool = False
    stop_after_smoke: bool = False


class ExecuteResponse(BaseModel):
    success: bool
    job_id: str
    status: str = "PENDING"
    mesh_status: str | None = None
    smoke_test_status: str | None = None
    run_status: str | None = None
    plot_paths: list[str] = Field(default_factory=list)
    mesh_report: dict[str, Any] | None = None
    smoke_test_report: dict[str, Any] | None = None
    run_report: dict[str, Any] | None = None
    error: str | None = None
    debug_details: str | None = None


@router.post("/execute", response_model=ExecuteResponse)
async def execute_case(request: ExecuteRequest) -> ExecuteResponse:
    """Execute the full pipeline asynchronously: upload → mesh → smoke test → run → postprocess.

    Returns immediately with status RUNNING. Poll GET /jobs/{job_id}/status for progress.
    """
    compiled = _compiled_store.get(request.job_id)
    if compiled is None:
        return ExecuteResponse(
            success=False,
            job_id=request.job_id,
            error=f"Compiled case not found: {request.job_id}",
        )

    spec = _load_spec(compiled["spec_id"])
    if spec is None:
        return ExecuteResponse(
            success=False,
            job_id=request.job_id,
            error="Original spec not found",
        )

    # If already running or done, return current status
    existing = _execution_store.get(request.job_id)
    if existing and existing.get("status") in ("RUNNING", "SUCCESS", "PARTIAL", "FAILED"):
        return ExecuteResponse(
            success=existing["status"] in ("SUCCESS", "PARTIAL"),
            job_id=request.job_id,
            status=existing["status"],
            plot_paths=existing.get("plot_paths", []),
            error=existing.get("errors", [None])[0] if existing.get("errors") else None,
        )

    # Initialize execution store
    _execution_store[request.job_id] = {
        "status": "RUNNING",
        "mesh_report": None,
        "smoke_test_report": None,
        "run_report": None,
        "plot_paths": [],
        "remote_case_path": None,
        "errors": [],
        "progress": "Starting execution...",
    }

    # Run in background thread
    import threading

    def _run_execution():
        try:
            from fluid_scientist.cylinder_flow_2d.execution import ExecutionOrchestrator

            orchestrator = ExecutionOrchestrator()
            result = orchestrator.run(
                spec=spec,
                job_id=request.job_id,
                skip_smoke=request.skip_smoke,
                parallel=request.parallel,
                np=request.np,
                stop_after_smoke=request.stop_after_smoke,
            )

            _execution_store[request.job_id] = {
                "status": result.status,
                "mesh_report": result.mesh_report,
                "smoke_test_report": result.smoke_test_report,
                "run_report": result.simulation_report,
                "plot_paths": result.plot_paths,
                "remote_case_path": result.remote_case_path,
                "errors": [result.error] if result.error else result.warnings,
                "progress": "Completed" if result.status != "SMOKE_PASSED" else "Smoke test passed — awaiting user confirmation",
            }
        except Exception as exc:
            import traceback
            _execution_store[request.job_id] = {
                "status": "FAILED",
                "mesh_report": None,
                "smoke_test_report": None,
                "run_report": None,
                "plot_paths": [],
                "errors": [str(exc)],
                "progress": traceback.format_exc(),
            }

    thread = threading.Thread(target=_run_execution, daemon=True)
    thread.start()

    return ExecuteResponse(
        success=True,
        job_id=request.job_id,
        status="RUNNING",
    )


class ResumeRunRequest(BaseModel):
    job_id: str
    parallel: bool = False
    np: int = 4


@router.post("/jobs/{job_id}/resume-run")
async def resume_run(job_id: str, request: ResumeRunRequest):
    """Resume execution after smoke test — start the full simulation.

    This endpoint is called after stop_after_smoke=True returned with
    status=SMOKE_PASSED. It runs the full simulation and postprocessing.
    """
    execution = _execution_store.get(job_id)
    if execution is None:
        return {"success": False, "error": f"Job not found: {job_id}"}

    if execution.get("status") != "SMOKE_PASSED":
        return {
            "success": False,
            "error": f"Job status is {execution.get('status')}, expected SMOKE_PASSED",
        }

    compiled = _compiled_store.get(job_id)
    if compiled is None:
        return {"success": False, "error": "Compiled case not found"}

    spec = _load_spec(compiled.get("spec_id", ""))
    if spec is None:
        return {"success": False, "error": "Original spec not found"}

    case_path = execution.get("remote_case_path")
    if not case_path:
        return {"success": False, "error": "No remote case path found"}

    # Update status to RUNNING
    _execution_store[job_id]["status"] = "RUNNING"
    _execution_store[job_id]["progress"] = "Starting full simulation..."

    import threading

    def _run_resume():
        try:
            from fluid_scientist.cylinder_flow_2d.execution import ExecutionOrchestrator

            orchestrator = ExecutionOrchestrator()
            result = orchestrator.resume_run(
                job_id=job_id,
                case_path=case_path,
                spec=spec,
                parallel=request.parallel,
                np=request.np,
            )

            _execution_store[job_id] = {
                "status": result.status,
                "mesh_report": _execution_store[job_id].get("mesh_report"),
                "smoke_test_report": _execution_store[job_id].get("smoke_test_report"),
                "run_report": result.simulation_report,
                "plot_paths": result.plot_paths,
                "remote_case_path": case_path,
                "errors": [result.error] if result.error else result.warnings,
                "progress": "Completed",
            }

            # Persist to session store
            _session_store.update(job_id, {
                "execution": _execution_store[job_id],
            })

        except Exception as exc:
            import traceback
            _execution_store[job_id] = {
                "status": "FAILED",
                "mesh_report": _execution_store[job_id].get("mesh_report"),
                "smoke_test_report": _execution_store[job_id].get("smoke_test_report"),
                "run_report": None,
                "plot_paths": [],
                "errors": [str(exc)],
                "progress": traceback.format_exc(),
            }

    thread = threading.Thread(target=_run_resume, daemon=True)
    thread.start()

    return {"success": True, "job_id": job_id, "status": "RUNNING"}


class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # RUNNING, SUCCESS, PARTIAL, FAILED
    progress: str | None = None
    mesh_status: str | None = None
    smoke_test_status: str | None = None
    run_status: str | None = None
    plot_paths: list[str] = Field(default_factory=list)
    mesh_report: dict[str, Any] | None = None
    smoke_test_report: dict[str, Any] | None = None
    run_report: dict[str, Any] | None = None
    error: str | None = None


@router.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Poll execution status for an async job."""
    execution = _execution_store.get(job_id)
    if execution is None:
        return JobStatusResponse(
            job_id=job_id,
            status="NOT_FOUND",
            error=f"Job not found: {job_id}",
        )

    mesh_errors = execution.get("mesh_report", {}).get("errors", []) if execution.get("mesh_report") else []
    smoke_passed = execution.get("smoke_test_report", {}).get("status") == "PASSED" if execution.get("smoke_test_report") else False
    sim_status = execution.get("run_report", {}).get("status", "FAILED") if execution.get("run_report") else None

    return JobStatusResponse(
        job_id=job_id,
        status=execution.get("status", "UNKNOWN"),
        progress=execution.get("progress"),
        mesh_status="PASSED" if not mesh_errors else ("FAILED" if mesh_errors else None),
        smoke_test_status="PASSED" if smoke_passed else None,
        run_status="COMPLETED" if sim_status == "SUCCESS" else sim_status,
        plot_paths=execution.get("plot_paths", []),
        mesh_report=execution.get("mesh_report"),
        smoke_test_report=execution.get("smoke_test_report"),
        run_report=execution.get("run_report"),
        error=execution.get("errors", [None])[0] if execution.get("errors") else None,
    )


@router.get("/jobs/{job_id}/results", response_model=ExecuteResponse)
async def get_results(job_id: str) -> ExecuteResponse:
    """Get execution results for a job."""
    execution = _execution_store.get(job_id)
    if execution is None:
        return ExecuteResponse(
            success=False,
            job_id=job_id,
            error=f"Execution not found: {job_id}",
        )

    return ExecuteResponse(
        success=execution["status"] in ("SUCCESS", "PARTIAL"),
        job_id=job_id,
        status=execution["status"],
        plot_paths=execution.get("plot_paths", []),
        mesh_report=execution.get("mesh_report"),
        smoke_test_report=execution.get("smoke_test_report"),
        run_report=execution.get("run_report"),
        error=execution["errors"][0] if execution.get("errors") else None,
    )


# ---------------------------------------------------------------------------
# Gated-workflow helper functions
# ---------------------------------------------------------------------------


def _unwrap_pf(pf: Any, default: Any = None) -> Any:
    """Unwrap a ProvenanceField to its plain value."""
    if pf is None:
        return default
    if hasattr(pf, "value"):
        val = pf.value
        return val if val is not None else default
    return pf if pf is not None else default


# NOTE: _load_spec is already defined at line 79. The duplicate below was
# removed because it called itself recursively, causing RecursionError.


def _run_confirmation_chain(spec: CylinderFlow2DExperimentSpecV1) -> None:
    """Run the full confirmation chain on *spec* in-place.

    Mirrors steps 2–12 of the legacy ``/confirm`` endpoint without
    applying user clarifications or simulation overrides.
    """
    from fluid_scientist.cylinder_flow_2d.geometry_normalizer import (
        CylinderFlow2DGeometryNormalizer,
        CylinderFlow2DDerivedFieldResolver,
    )
    from fluid_scientist.cylinder_flow_2d.boundary_topology import (
        CylinderFlow2DBoundaryTopologyResolver,
        CylinderFlow2DBoundaryCombinationValidator,
    )
    from fluid_scientist.cylinder_flow_2d.observable import (
        CylinderFlow2DObservableExtractor,
        CylinderFlow2DObservableRecommender,
        CylinderFlow2DObservableValidator,
    )
    from fluid_scientist.cylinder_flow_2d.analysis_goals import (
        CylinderFlow2DAnalysisGoalBuilder,
    )
    from fluid_scientist.cylinder_flow_2d.critic import (
        CylinderFlow2DCritic,
        CylinderFlow2DCoverageChecker,
    )

    # 1. Geometry normalizer
    normalizer = CylinderFlow2DGeometryNormalizer()
    normalizer.normalize(spec, spec.user_input_text or "")

    # 2. Derived field resolver
    resolver = CylinderFlow2DDerivedFieldResolver()
    resolver.resolve(spec)

    # 3. Flow topology resolver
    topology_resolver = CylinderFlow2DBoundaryTopologyResolver()
    try:
        flow_mode = topology_resolver.resolve(spec)
        spec.flow_topology = {"mode": flow_mode.value}
    except Exception:
        pass

    # 4. Boundary combination validator
    boundary_validator = CylinderFlow2DBoundaryCombinationValidator()
    boundary_validator.validate(spec)

    # 5. Observable extractor
    extractor = CylinderFlow2DObservableExtractor()
    user_obs = extractor.extract(spec.user_input_text or "")
    existing_types = {obs.type for obs in spec.observables}
    for obs in user_obs:
        if obs.type not in existing_types:
            spec.observables.append(obs)

    # 6. Observable recommender
    recommender = CylinderFlow2DObservableRecommender()
    recommended = recommender.recommend(spec)
    existing_types = {obs.type for obs in spec.observables}
    for rec in recommended:
        if rec.type not in existing_types:
            spec.observables.append(rec)

    # 7. Observable validator
    validator = CylinderFlow2DObservableValidator()
    spec.observables = validator.validate(spec.observables)

    # 8. Analysis goal builder
    goal_builder = CylinderFlow2DAnalysisGoalBuilder()
    goals = goal_builder.build(spec)
    if not spec.analysis_goals:
        spec.analysis_goals.extend(goals)

    # 9. Critic
    critic = CylinderFlow2DCritic()
    critic.review(spec, spec.user_input_text or "")

    # 10. Coverage checker
    coverage = CylinderFlow2DCoverageChecker()
    coverage.check(spec, spec.user_input_text or "")


def _auto_accept_recommendations(spec: CylinderFlow2DExperimentSpecV1) -> None:
    """Auto-accept all AWAITING_CONFIRMATION fields and observables."""
    from fluid_scientist.cylinder_flow_2d.models import TimeMode

    for obs in spec.observables:
        if obs.status == FieldStatus.AWAITING_CONFIRMATION:
            obs.status = FieldStatus.RESOLVED
            obs.source = FieldSource.USER_CONFIRMED
    for goal in spec.analysis_goals:
        if goal.status == FieldStatus.AWAITING_CONFIRMATION:
            goal.status = FieldStatus.RESOLVED
            goal.source = FieldSource.USER_CONFIRMED
    if spec.fluid.type.status == FieldStatus.AWAITING_CONFIRMATION:
        spec.fluid.type.status = FieldStatus.RESOLVED
        spec.fluid.type.source = FieldSource.USER_CONFIRMED
    if spec.fluid.density_kg_m3.status == FieldStatus.AWAITING_CONFIRMATION:
        spec.fluid.density_kg_m3.status = FieldStatus.RESOLVED
        spec.fluid.density_kg_m3.source = FieldSource.USER_CONFIRMED
    if spec.fluid.kinematic_viscosity_m2_s.status == FieldStatus.AWAITING_CONFIRMATION:
        spec.fluid.kinematic_viscosity_m2_s.status = FieldStatus.RESOLVED
        spec.fluid.kinematic_viscosity_m2_s.source = FieldSource.USER_CONFIRMED
    if spec.simulation.time_mode.value == "auto":
        spec.simulation.time_mode = TimeMode.TRANSIENT


def _generate_compile_preview(spec: CylinderFlow2DExperimentSpecV1) -> dict[str, Any]:
    """Generate a compile preview without executing the compilation.

    Returns a dict with solver, mesh, turbulence, time-step and
    observable-implementation details so the user can review the
    plan before committing to Gate 2.
    """
    from fluid_scientist.cylinder_flow_2d.models import FlowRegime

    # --- domain / cylinder / fluid ---
    length = _unwrap_pf(spec.domain.length_m, 30.0)
    height = _unwrap_pf(spec.domain.height_m, 10.0)
    thickness = _unwrap_pf(spec.domain.thickness_m, 1.0)
    has_cylinder = spec.has_cylinder
    diameter = spec.get_cylinder_diameter()
    radius = spec.get_cylinder_radius()
    nu = _unwrap_pf(spec.fluid.kinematic_viscosity_m2_s, 1.004e-6)
    rho = _unwrap_pf(spec.fluid.density_kg_m3, 998.0)
    fluid_type = _unwrap_pf(spec.fluid.type, "water")

    # --- flow regime / turbulence ---
    flow_regime = spec.simulation.flow_regime
    is_turbulent = flow_regime == FlowRegime.TURBULENT

    # --- temporal ---
    is_transient = spec.is_transient
    reynolds = spec.estimate_reynolds()

    # --- mesh estimation (mirrors ObstacleFlowMeshBackend._auto_params) ---
    nx = max(20, int(length / max(length / 100, 0.5)))
    ny = max(10, int(height / max(height / 30, 0.5)))
    if has_cylinder and diameter is not None:
        cell_size_cyl = diameter / 20
        nx = max(nx, int(length / cell_size_cyl))
        ny = max(ny, int(height / cell_size_cyl))
    nx = min(nx, 500)
    ny = min(ny, 200)
    estimated_cells = nx * ny  # 2D: single layer in z

    # --- time-step estimation (mirrors compiler _compile_control_dict) ---
    if is_transient:
        end_time = spec.simulation.end_time or 10.0
        if spec.simulation.delta_t is not None:
            delta_t = spec.simulation.delta_t
        else:
            char_len = diameter if diameter else height
            u = (reynolds * nu / char_len) if (reynolds and char_len and char_len > 0) else 1.0
            cell_size_ts = char_len / 200 if char_len and char_len > 0 else 0.01
            delta_t = spec.simulation.max_courant_number * cell_size_ts / max(u, 0.001)
            delta_t = min(delta_t, end_time / 200)
        n_steps = int(end_time / delta_t) if delta_t > 0 else 0
    else:
        end_time = 1000.0
        delta_t = 1.0
        n_steps = 1000

    # rough wall-clock estimate (seconds)
    estimated_time = n_steps * estimated_cells * 1.5e-5

    # --- observable → functionObject mapping ---
    _OBS_IMPL: dict[str, tuple[str, str]] = {
        "cylinder_drag": ("forceCoeffs functionObject", "postProcessing/forceCoeffs1/0/forceCoeffs.dat"),
        "cylinder_lift": ("forceCoeffs functionObject", "postProcessing/forceCoeffs1/0/forceCoeffs.dat"),
        "drag_lift_time_series": ("forceCoeffs functionObject", "postProcessing/forceCoeffs1/0/forceCoeffs.dat"),
        "wake_shedding_frequency": ("forceCoeffs functionObject (FFT post-analysis)", "postProcessing/forceCoeffs1/0/forceCoeffs.dat"),
        "point_velocity": ("probes functionObject", "postProcessing/probes1/0/U"),
        "section_mean_velocity": ("surfaces functionObject (cuttingPlane)", "postProcessing/surfaces1/"),
        "section_flow_rate": ("surfaces functionObject (cuttingPlane)", "postProcessing/surfaces1/"),
        "velocity_magnitude_field": ("foamToVTK field export + postProcess", "VTK/"),
        "pressure_field": ("foamToVTK field export", "VTK/"),
        "vorticity_field": ("postProcess -func vorticity + foamToVTK", "VTK/"),
        "streamlines": ("matplotlib streamplot from VTK data", "VTK/"),
        "wall_shear_stress": ("wallShearStress functionObject", "postProcessing/wallShearStress1/"),
        "recirculation_length": ("post-hoc analysis from U field", "VTK/"),
    }
    observable_impl: list[dict[str, Any]] = []
    for obs in spec.observables:
        impl_info = _OBS_IMPL.get(obs.type.value)
        if impl_info:
            observable_impl.append({
                "observable": obs.type.value,
                "label": obs.label,
                "implementation": impl_info[0],
                "output_file": impl_info[1],
            })

    return {
        "openfoam_version": "13",
        "application": "incompressibleFluid",
        "solver_module": "foamRun -solver incompressibleFluid",
        "temporal_type": "transient" if is_transient else "steady",
        "turbulence_model": "kOmegaSST" if is_turbulent else "laminar",
        "mesh_backend": "blockMesh + snappyHexMesh" if has_cylinder else "blockMesh",
        "estimated_mesh_count": estimated_cells,
        "mesh_detail": {"nx": nx, "ny": ny, "nz": 1},
        "time_step": delta_t,
        "end_time": end_time,
        "estimated_steps": n_steps,
        "estimated_computation_time_s": round(estimated_time, 1),
        "reynolds_number": reynolds,
        "max_courant_number": spec.simulation.max_courant_number,
        "observables_implementation": observable_impl,
        "domain": {
            "length_m": length,
            "height_m": height,
            "thickness_m": thickness,
        },
        "cylinder": {
            "has_cylinder": has_cylinder,
            "diameter_m": diameter,
            "radius_m": radius,
            "center_x_m": _unwrap_pf(spec.cylinder.center_x_m),
            "center_y_m": _unwrap_pf(spec.cylinder.center_y_m),
        },
        "fluid": {
            "type": fluid_type,
            "density_kg_m3": rho,
            "kinematic_viscosity_m2_s": nu,
        },
    }


def _parse_force_coefficients(dat_path: str) -> dict | None:
    """Parse an OpenFOAM ``forceCoeffs.dat`` file.

    Returns a dict with time, Cd, Cl arrays plus statistics
    (mean, amplitude, min, max) computed over the second half of
    the data to skip the transient start-up.
    """
    try:
        import numpy as np

        data = np.loadtxt(dat_path, comments="#")
        if data.size == 0:
            return None
        if data.ndim == 1:
            data = data.reshape(1, -1)

        time_arr = data[:, 0]
        cd = data[:, 1] if data.shape[1] > 1 else None
        # Foundation 13 forceCoeffs columns: time Cd Cs Cl CmRoll CmPitch CmYaw
        cl = data[:, 3] if data.shape[1] > 3 else (data[:, 2] if data.shape[1] > 2 else None)

        result: dict[str, Any] = {
            "time": time_arr.tolist(),
            "cd": cd.tolist() if cd is not None else None,
            "cl": cl.tolist() if cl is not None else None,
            "n_samples": len(time_arr),
        }

        # statistics on second half (skip transient)
        n = len(time_arr)
        start = max(n // 2, 1)

        if cd is not None and n > 0:
            cd_tail = cd[start:]
            if len(cd_tail) > 0:
                result["cd_mean"] = float(np.mean(cd_tail))
                result["cd_amplitude"] = float((np.max(cd_tail) - np.min(cd_tail)) / 2.0)
                result["cd_min"] = float(np.min(cd_tail))
                result["cd_max"] = float(np.max(cd_tail))

        if cl is not None and n > 0:
            cl_tail = cl[start:]
            if len(cl_tail) > 0:
                result["cl_mean"] = float(np.mean(cl_tail))
                result["cl_amplitude"] = float((np.max(cl_tail) - np.min(cl_tail)) / 2.0)
                result["cl_min"] = float(np.min(cl_tail))
                result["cl_max"] = float(np.max(cl_tail))

        return result
    except Exception:
        return None


def _calculate_strouhal(
    time_arr: list[float],
    cl_arr: list[float],
    char_length: float,
    char_velocity: float,
) -> float | None:
    """Estimate the Strouhal number from the Cl oscillation frequency.

    Uses an FFT on the second half of the Cl signal (after removing the
    mean) to find the dominant vortex-shedding frequency.

    St = f_shedding * D / U
    """
    try:
        import numpy as np

        if len(cl_arr) < 10 or char_length <= 0 or char_velocity <= 0:
            return None

        n = len(cl_arr)
        start = n // 2
        t = np.array(time_arr[start:], dtype=float)
        cl = np.array(cl_arr[start:], dtype=float)

        if len(t) < 5:
            return None

        # Remove mean
        cl = cl - np.mean(cl)

        # Uniform time step
        dt = float(t[1] - t[0]) if len(t) > 1 else 1.0
        if dt <= 0:
            return None

        # FFT
        freqs = np.fft.rfftfreq(len(cl), d=dt)
        spectrum = np.abs(np.fft.rfft(cl))

        if len(spectrum) < 2:
            return None

        # Skip the DC bin (index 0)
        peak_idx = int(np.argmax(spectrum[1:])) + 1
        dominant_freq = float(freqs[peak_idx])

        if dominant_freq <= 0:
            return None

        st = dominant_freq * char_length / char_velocity
        return round(st, 4)
    except Exception:
        return None


def _generate_analysis_report(
    job_id: str,
    execution_data: dict,
    spec_dict: dict | None = None,
) -> dict[str, Any]:
    """Generate a structured analysis report from execution results.

    Extracts real numerical values from ``forceCoeffs.dat`` and the
    simulation / mesh reports.
    """
    report: dict[str, Any] = {
        "job_id": job_id,
        "metrics": {},
        "flow_features": {},
        "convergence": {},
        "quality": {},
        "warnings": [],
        "limitations": [],
    }

    results_dir = os.path.join(RESULTS_DIR, job_id)
    fc_path = os.path.join(results_dir, "forceCoeffs.dat")

    # --- extract characteristic length / velocity for Strouhal ---
    char_length: float | None = None
    char_velocity: float | None = None
    if spec_dict:
        cyl = spec_dict.get("cylinder", {})
        d_pf = cyl.get("diameter_m")
        if isinstance(d_pf, dict):
            char_length = d_pf.get("value")
        elif isinstance(d_pf, (int, float)):
            char_length = d_pf
        boundaries = spec_dict.get("boundaries", {})
        left = boundaries.get("left", {})
        char_velocity = left.get("inlet_velocity")
        # Also check inlet_profile
        if not char_velocity:
            inlet = spec_dict.get("inlet_profile", {})
            params = inlet.get("parameters", {})
            char_velocity = params.get("velocity") or params.get("max_velocity") or params.get("mean_velocity")

    # --- force coefficients ---
    if os.path.exists(fc_path):
        fc_data = _parse_force_coefficients(fc_path)
        if fc_data:
            m = report["metrics"]
            for key in ("cd_mean", "cd_amplitude", "cd_min", "cd_max",
                        "cl_mean", "cl_amplitude", "cl_min", "cl_max"):
                if fc_data.get(key) is not None:
                    m[key] = fc_data[key]

            # Strouhal number
            if fc_data.get("cl") and fc_data.get("time"):
                if char_length and char_velocity:
                    st = _calculate_strouhal(
                        fc_data["time"], fc_data["cl"], char_length, char_velocity,
                    )
                    m["strouhal_number"] = st
                    if st is not None:
                        if 0.1 <= st <= 0.3:
                            report["flow_features"]["vortex_shedding"] = "detected"
                            report["flow_features"]["shedding_regime"] = "von Karman vortex street"
                        else:
                            report["flow_features"]["vortex_shedding"] = f"detected (atypical St={st})"
                    else:
                        report["flow_features"]["vortex_shedding"] = "insufficient data"
                else:
                    report["limitations"].append(
                        "Missing characteristic length or velocity for Strouhal calculation"
                    )
            else:
                report["limitations"].append("No Cl time series available for Strouhal calculation")

            # Flow feature assessment from Cd/Cl
            if fc_data.get("cd_mean") is not None:
                report["flow_features"]["mean_drag_coefficient"] = fc_data["cd_mean"]
            if fc_data.get("cl_amplitude") is not None:
                if fc_data["cl_amplitude"] > 0.01:
                    report["flow_features"]["oscillating_lift"] = "significant"
                else:
                    report["flow_features"]["oscillating_lift"] = "negligible"
    else:
        report["limitations"].append("No forceCoeffs.dat found — force metrics unavailable")

    # --- convergence from simulation report ---
    sim_report = execution_data.get("run_report") or {}
    if sim_report:
        conv = report["convergence"]
        conv["status"] = sim_report.get("status")
        conv["final_time"] = sim_report.get("final_time")
        conv["courant_max"] = sim_report.get("courant_max")
        conv["has_nan"] = sim_report.get("has_nan", False)
        conv["has_error"] = sim_report.get("has_error", False)

        # Parse continuity errors from log tail
        output = sim_report.get("output_tail", "") or ""
        cont_matches = _re.findall(
            r"continuity errors.*?sum local\s*=\s*([\d.eE+-]+).*?"
            r"global\s*=\s*([\d.eE+-]+).*?"
            r"cumulative\s*=\s*([\d.eE+-]+)",
            output,
        )
        if cont_matches:
            last = cont_matches[-1]
            conv["continuity_sum_local"] = float(last[0])
            conv["continuity_global"] = float(last[1])
            conv["continuity_cumulative"] = float(last[2])
            report["quality"]["mass_conservation"] = {
                "sum_local": float(last[0]),
                "global": float(last[1]),
                "cumulative": float(last[2]),
                "acceptable": abs(float(last[1])) < 1e-4,
            }
        else:
            report["limitations"].append("Could not parse continuity errors from simulation log")

        if sim_report.get("status") == "SUCCESS":
            conv["assessment"] = "converged"
        elif sim_report.get("has_nan"):
            conv["assessment"] = "diverged (NaN detected)"
            report["warnings"].append("Simulation produced NaN values")
        elif sim_report.get("has_error"):
            conv["assessment"] = "diverged (FOAM error)"
            report["warnings"].append("Simulation encountered FOAM FATAL ERROR")
        else:
            conv["assessment"] = "incomplete"

    # --- mesh quality ---
    mesh_report = execution_data.get("mesh_report") or {}
    if mesh_report:
        stats = mesh_report.get("stats", {}) or {}
        report["quality"]["mesh"] = {
            "cells": stats.get("cells"),
            "points": stats.get("points"),
            "max_aspect_ratio": stats.get("max_aspect_ratio"),
            "min_volume": stats.get("min_volume"),
            "total_volume": stats.get("total_volume"),
            "mesh_ok": mesh_report.get("mesh_ok", False),
        }
        ar = stats.get("max_aspect_ratio")
        if ar is not None and isinstance(ar, (int, float)) and ar > 100:
            report["warnings"].append(f"High mesh aspect ratio: {ar:.1f}")

    # --- smoke test ---
    smoke_report = execution_data.get("smoke_test_report") or {}
    if smoke_report:
        report["quality"]["smoke_test"] = {
            "status": smoke_report.get("status"),
            "courant_mean": smoke_report.get("courant_mean"),
            "courant_max": smoke_report.get("courant_max"),
            "completed_timesteps": smoke_report.get("completed_timesteps"),
        }

    # --- general warnings ---
    if not report["metrics"]:
        report["warnings"].append("No force coefficient metrics available")
    if execution_data.get("status") == "PARTIAL":
        report["warnings"].append("Simulation completed partially — some steps may have failed")

    return report


def _list_artifacts(job_id: str) -> list[dict[str, Any]]:
    """List plot / animation artifacts for *job_id* with display URLs."""
    plots_dir = os.path.join(RESULTS_DIR, job_id)
    if not os.path.isdir(plots_dir):
        return []

    supported_exts = (".png", ".gif", ".mp4")
    artifacts: list[dict[str, Any]] = []
    for fname in sorted(os.listdir(plots_dir)):
        if fname.endswith(supported_exts):
            artifacts.append({
                "name": fname,
                "display_url": f"/api/v5/cylinder-flow/jobs/{job_id}/plots/{fname}",
                "size_bytes": os.path.getsize(os.path.join(plots_dir, fname)),
            })
    return artifacts


def _find_session_for_job(job_id: str) -> str | None:
    """Find the spec_id (session key) associated with *job_id*.

    Checks the in-memory ``_job_to_spec`` mapping first, then falls
    back to scanning persisted session files.
    """
    with _job_to_spec_lock:
        if job_id in _job_to_spec:
            return _job_to_spec[job_id]

    # Scan persisted sessions
    for session_id in _session_store.list_sessions():
        session = _session_store.load(session_id)
        if session is None:
            continue
        comp = session.get("compilation") or {}
        if comp.get("job_id") == job_id:
            with _job_to_spec_lock:
                _job_to_spec[job_id] = session_id
            return session_id

    return None


# ---------------------------------------------------------------------------
# Gated-workflow endpoints
# ---------------------------------------------------------------------------


class ConfirmPlanRequest(BaseModel):
    """Request body for Gate 1 (confirm-plan).

    ``accept_recommendations`` defaults to True because clicking
    "确认研究方案" is an explicit user action that confirms all
    recommended values. This is NOT auto-accept — the user must
    click the button; the pipeline never calls this automatically.
    """
    accept_recommendations: bool = True


@router.post("/{spec_id}/confirm-plan", response_model=ConfirmPlanResponse)
async def confirm_plan(
    spec_id: str,
    request: ConfirmPlanRequest = ConfirmPlanRequest(),
) -> ConfirmPlanResponse:
    """Gate 1 — Confirm the research plan.

    Validates that the spec exists and is ``READY_TO_CONFIRM`` (or
    ``AWAITING_CONFIRMATION``), runs the full confirmation chain,
    freezes the spec (``SPEC_CONFIRMED``), returns a compile preview,
    and persists the confirmation record to disk.

    When ``accept_recommendations`` is True (default), all
    ``AWAITING_CONFIRMATION`` fields are resolved to ``RESOLVED``
    with ``USER_CONFIRMED`` source. This is explicit user confirmation
    triggered by the "确认研究方案" button — not silent auto-accept.
    """
    spec = _load_spec(spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Spec not found: {spec_id}")

    # If already confirmed, return cached state
    if spec.draft_status == DraftStatus.SPEC_CONFIRMED:
        existing_session = _session_store.load(spec_id) or {}
        plan_conf = existing_session.get("plan_confirmation") or {}
        preview = existing_session.get("compile_preview") or _generate_compile_preview(spec)
        return ConfirmPlanResponse(
            success=True,
            spec_id=spec_id,
            already_confirmed=True,
            confirmed_at=plan_conf.get("confirmed_at"),
            compile_preview=preview,
        )

    if spec.draft_status not in (DraftStatus.READY_TO_CONFIRM, DraftStatus.AWAITING_CONFIRMATION):
        return ConfirmPlanResponse(
            success=False,
            spec_id=spec_id,
            error=(
                f"Spec must be READY_TO_CONFIRM or AWAITING_CONFIRMATION, "
                f"current status: {spec.draft_status.value}"
            ),
            blocking_issues=spec.blocking_issues,
        )

    # Run the full confirmation chain
    _run_confirmation_chain(spec)

    # Evaluate readiness
    from fluid_scientist.cylinder_flow_2d.readiness import (
        CylinderFlow2DDraftReadinessEvaluator,
    )
    evaluator = CylinderFlow2DDraftReadinessEvaluator()
    final_status = evaluator.evaluate(spec)

    # --- Explicit user confirmation of recommended values ---
    # When the user clicks "确认研究方案" (accept_recommendations=True),
    # resolve all AWAITING_CONFIRMATION fields to RESOLVED with
    # USER_CONFIRMED source. This is explicit user action, NOT auto-accept.
    if request.accept_recommendations and final_status == DraftStatus.AWAITING_CONFIRMATION:
        _auto_accept_recommendations(spec)
        final_status = evaluator.evaluate(spec)

    # --- Null field validation: block if critical fields are null ---
    _blocking_null_issues = _validate_null_fields(spec)
    if _blocking_null_issues:
        spec.blocking_issues.extend(_blocking_null_issues)
        final_status = DraftStatus.NEEDS_CLARIFICATION

    # --- Block on silent geometry substitution ---
    if _has_blocking_substitution(spec):
        final_status = DraftStatus.NEEDS_CLARIFICATION

    # --- Semantic coverage check ---
    # Verify that all user-stated claims are mapped to the spec
    coverage = _semantic_coverage_check(spec, spec.user_input_text or "")
    if coverage["silent_substitutions"]:
        # Silent substitution detected — block Gate 1
        for sub in coverage["silent_substitutions"]:
            spec.blocking_issues.append({
                "code": "SILENT_SUBSTITUTION_DETECTED",
                "message": f"语义覆盖检查发现静默替换: {sub['claim']} 期望={sub['expected']}, 实际={sub['actual']}",
            })
        final_status = DraftStatus.NEEDS_CLARIFICATION
    elif coverage["unmapped_claims"]:
        # Unmapped claims — block Gate 1
        for claim in coverage["unmapped_claims"]:
            spec.blocking_issues.append({
                "code": "SEMANTIC_COVERAGE_GAP",
                "message": f"语义覆盖不完整: {claim['claim']} 原因: {claim['reason']}",
            })
        final_status = DraftStatus.NEEDS_CLARIFICATION

    if final_status not in (DraftStatus.READY_TO_CONFIRM, DraftStatus.AWAITING_CONFIRMATION):
        return ConfirmPlanResponse(
            success=False,
            spec_id=spec_id,
            error=(
                f"Spec has blocking issues and cannot be confirmed. "
                f"Status: {final_status.value}. "
                f"Issues: {[i.get('code','') if isinstance(i,dict) else str(i) for i in spec.blocking_issues]}"
            ),
            blocking_issues=spec.blocking_issues,
        )

    # Freeze the spec
    spec.draft_status = DraftStatus.SPEC_CONFIRMED
    spec.spec_version += 1
    _persist_spec(spec_id, spec)

    # Generate compile preview
    preview = _generate_compile_preview(spec)

    # Persist
    now_iso = datetime.now(timezone.utc).isoformat()
    session_data = _session_store.load(spec_id) or {
        "session_id": spec_id,
        "created_at": now_iso,
    }
    session_data["spec_id"] = spec_id
    session_data["spec"] = spec.model_dump()
    session_data["plan_confirmation"] = {
        "confirmed": True,
        "confirmed_at": now_iso,
        "spec_version": spec.spec_version,
    }
    session_data["compile_preview"] = preview
    _session_store.save(spec_id, session_data)

    return ConfirmPlanResponse(
        success=True,
        spec_id=spec_id,
        confirmed_at=now_iso,
        compile_preview=preview,
    )


@router.get("/{spec_id}/compile-preview", response_model=CompilePreviewResponse)
async def get_compile_preview(spec_id: str) -> CompilePreviewResponse:
    """Get a compile-plan preview without executing the compilation.

    Returns OpenFOAM version, application, solver module, temporal
    type, turbulence model, mesh backend, estimated mesh count,
    time-step, estimated computation time, and how each observable
    will be implemented.
    """
    spec = _load_spec(spec_id)
    if spec is None:
        return CompilePreviewResponse(
            success=False,
            spec_id=spec_id,
            error=f"Spec not found: {spec_id}",
        )

    # Return cached preview if available, otherwise generate fresh
    session = _session_store.load(spec_id)
    if session and session.get("compile_preview"):
        return CompilePreviewResponse(
            success=True,
            spec_id=spec_id,
            preview=session["compile_preview"],
        )

    preview = _generate_compile_preview(spec)
    return CompilePreviewResponse(
        success=True,
        spec_id=spec_id,
        preview=preview,
    )


@router.post("/{spec_id}/confirm-compile", response_model=ConfirmCompileResponse)
async def confirm_compile(spec_id: str) -> ConfirmCompileResponse:
    """Gate 2 — Compile, static-validate, checkMesh, and smoke-test.

    Validates that the plan has been confirmed (Gate 1 passed),
    then:
      1. Adapts the spec to ``ObstacleFlowExperimentSpecV1``
      2. Compiles OpenFOAM case files (``ObstacleFlowCompiler``)
      3. Runs static validation (``ObstacleFlowStaticValidator``)
      4. Uploads the case to the remote workstation
      5. Runs blockMesh + snappyHexMesh + checkMesh
      6. Runs a 2-timestep smoke test
      7. Persists the compilation record to disk

    Returns the validation results.  If any step fails, the error
    is returned without proceeding to subsequent steps.
    """
    spec = _load_spec(spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Spec not found: {spec_id}")

    if spec.draft_status != DraftStatus.SPEC_CONFIRMED:
        return ConfirmCompileResponse(
            success=False,
            spec_id=spec_id,
            error=(
                f"Spec must be SPEC_CONFIRMED (Gate 1). "
                f"Current status: {spec.draft_status.value}"
            ),
        )

    try:
        from fluid_scientist.cylinder_flow_2d.execution import (
            SpecAdapter,
            WorkstationExecutor,
        )
        from fluid_scientist.obstacle_flow.compiler import ObstacleFlowCompiler
        from fluid_scientist.obstacle_flow.static_validator import (
            ObstacleFlowStaticValidator,
        )

        # Step 1: Adapt spec
        adapter = SpecAdapter()
        adapted_spec = adapter.adapt(spec)

        # Step 2: Compile
        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(adapted_spec)

        compilation_info = {
            "archive_sha256": compiled.archive_sha256,
            "file_count": len(compiled.files),
            "file_list": sorted(compiled.files.keys()),
            "manifest": {
                "compilation_id": manifest.compilation_id,
                "spec_version": manifest.spec_version,
                "spec_hash": manifest.spec_hash,
                "case_hash": manifest.case_hash,
                "flow_mode": manifest.flow_mode,
                "has_cylinder": manifest.has_cylinder,
                "has_bump": manifest.has_bump,
            },
        }

        # Step 3: Static validation
        static_validator = ObstacleFlowStaticValidator()
        sv_result = static_validator.validate(adapted_spec, compiled.files)
        static_validation_info = {
            "passed": sv_result.passed,
            "errors": sv_result.errors,
            "warnings": sv_result.warnings,
        }

        if not sv_result.passed:
            return ConfirmCompileResponse(
                success=False,
                spec_id=spec_id,
                compilation=compilation_info,
                static_validation=static_validation_info,
                error="Static validation failed",
                debug_details="; ".join(sv_result.errors),
            )

        # Step 4: Upload to workstation
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        executor = WorkstationExecutor()
        case_path = executor.upload_case(job_id, compiled.archive)

        # Step 5: Run mesh (blockMesh + snappyHexMesh + checkMesh)
        mesh_report = executor.run_mesh(case_path)

        if mesh_report.get("returncode", 1) != 0:
            return ConfirmCompileResponse(
                success=False,
                spec_id=spec_id,
                job_id=job_id,
                compilation=compilation_info,
                static_validation=static_validation_info,
                mesh_report=mesh_report,
                error="Mesh generation failed (checkMesh returned non-zero)",
                debug_details=str(mesh_report.get("stderr", ""))[:500],
            )

        # Step 6: Smoke test
        smoke_report = executor.run_smoke_test(case_path)

        # Step 7: Store compiled case in memory (backward compat with /execute)
        _compiled_store[job_id] = {
            "spec_id": spec_id,
            "archive": compiled.archive,
            "archive_sha256": compiled.archive_sha256,
            "files": compiled.files,
            "manifest": compilation_info["manifest"],
        }

        # Register job_id -> spec_id mapping
        with _job_to_spec_lock:
            _job_to_spec[job_id] = spec_id

        # Persist to session
        session_data = _session_store.load(spec_id) or {"session_id": spec_id}
        session_data["compilation"] = {
            "job_id": job_id,
            "remote_case_path": case_path,
            "compilation_info": compilation_info,
            "static_validation": static_validation_info,
            "mesh_report": mesh_report,
            "smoke_test_report": smoke_report,
            "adapted_spec": adapted_spec.model_dump(),
            "compiled_at": datetime.now(timezone.utc).isoformat(),
        }
        _session_store.save(spec_id, session_data)

        return ConfirmCompileResponse(
            success=True,
            spec_id=spec_id,
            job_id=job_id,
            compilation=compilation_info,
            static_validation=static_validation_info,
            mesh_report=mesh_report,
            smoke_test_report=smoke_report,
        )

    except Exception as exc:
        import traceback
        return ConfirmCompileResponse(
            success=False,
            spec_id=spec_id,
            error=f"Compilation/validation failed: {exc}",
            debug_details=traceback.format_exc()[-800:],
        )


@router.post("/{job_id}/confirm-run", response_model=ConfirmRunResponse)
async def confirm_run(job_id: str) -> ConfirmRunResponse:
    """Gate 3 — Start the formal simulation.

    Validates that the smoke test passed (Gate 2 completed), then
    starts the full simulation in a background thread.  Poll
    ``GET /{job_id}/results`` for progress.
    """
    spec_id = _find_session_for_job(job_id)
    if spec_id is None:
        return ConfirmRunResponse(
            success=False,
            job_id=job_id,
            error=f"Job not found: {job_id}",
        )

    session = _session_store.load(spec_id)
    if session is None:
        return ConfirmRunResponse(
            success=False,
            job_id=job_id,
            error=f"Session not found for job: {job_id}",
        )

    compilation = session.get("compilation") or {}
    smoke_report = compilation.get("smoke_test_report") or {}
    smoke_status = smoke_report.get("status")

    if smoke_status != "PASSED":
        return ConfirmRunResponse(
            success=False,
            job_id=job_id,
            error=(
                f"Smoke test must be PASSED before running. "
                f"Current smoke test status: {smoke_status}"
            ),
        )

    # If already running or completed, return current status
    existing = _execution_store.get(job_id)
    if existing and existing.get("status") in ("RUNNING", "SUCCESS", "PARTIAL", "FAILED"):
        return ConfirmRunResponse(
            success=existing["status"] in ("SUCCESS", "PARTIAL"),
            job_id=job_id,
            status=existing["status"],
            error=existing.get("errors", [None])[0] if existing.get("errors") else None,
        )

    # Initialize execution store
    _execution_store[job_id] = {
        "status": "RUNNING",
        "mesh_report": compilation.get("mesh_report"),
        "smoke_test_report": compilation.get("smoke_test_report"),
        "run_report": None,
        "plot_paths": [],
        "remote_case_path": compilation.get("remote_case_path"),
        "errors": [],
        "progress": "Starting formal simulation...",
    }

    case_path = compilation.get("remote_case_path")
    adapted_spec_dict = compilation.get("adapted_spec")

    def _run_formal_simulation():
        try:
            from fluid_scientist.cylinder_flow_2d.execution import (
                Postprocessor,
                WorkstationExecutor,
            )
            from fluid_scientist.obstacle_flow.models import (
                ObstacleFlowExperimentSpecV1,
            )

            executor = WorkstationExecutor()
            postprocessor = Postprocessor(executor=executor)

            # Run full simulation
            sim_report = executor.run_full(case_path)

            # Reconstruct adapted spec for plot generation
            adapted_spec = None
            if adapted_spec_dict:
                try:
                    adapted_spec = ObstacleFlowExperimentSpecV1(**adapted_spec_dict)
                except Exception:
                    adapted_spec = None

            # Generate plots
            plot_paths: list[str] = []
            try:
                plot_paths = postprocessor.generate_plots(
                    case_path, job_id, adapted_spec,
                )
            except Exception as exc:
                _execution_store[job_id]["errors"].append(f"Plot generation failed: {exc}")

            # Determine final status
            if sim_report.get("status") == "SUCCESS":
                final_status = "SUCCESS" if plot_paths else "PARTIAL"
            elif sim_report.get("final_time"):
                final_status = "PARTIAL"
            else:
                final_status = "FAILED"

            _execution_store[job_id].update({
                "status": final_status,
                "run_report": sim_report,
                "plot_paths": plot_paths,
                "errors": _execution_store[job_id].get("errors", []),
                "progress": "Completed",
            })

            # Generate analysis report
            spec = _load_spec(spec_id)
            spec_dict = spec.model_dump() if spec else None
            analysis_report = _generate_analysis_report(
                job_id, _execution_store[job_id], spec_dict,
            )

            # Persist to session
            session_update = {
                "execution": {
                    "status": final_status,
                    "run_report": sim_report,
                    "plot_paths": plot_paths,
                    "analysis_report": analysis_report,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            _session_store.update(spec_id, session_update)

        except Exception as exc:
            import traceback
            _execution_store[job_id].update({
                "status": "FAILED",
                "errors": [str(exc)],
                "progress": traceback.format_exc(),
            })
            try:
                _session_store.update(spec_id, {
                    "execution": {
                        "status": "FAILED",
                        "error": str(exc),
                        "executed_at": datetime.now(timezone.utc).isoformat(),
                    },
                })
            except Exception:
                pass

    thread = threading.Thread(target=_run_formal_simulation, daemon=True)
    thread.start()

    return ConfirmRunResponse(
        success=True,
        job_id=job_id,
        status="RUNNING",
    )


@router.get("/{job_id}/results", response_model=ResultsResponse)
async def get_results_v2(job_id: str) -> ResultsResponse:
    """Get complete results for a job.

    Returns status, metrics, analysis report, and a list of
    artifacts (plots / animations) with ``display_url`` for
    persistent access.
    """
    # Try in-memory first, then session persistence
    execution = _execution_store.get(job_id)
    if execution is None:
        spec_id = _find_session_for_job(job_id)
        if spec_id is not None:
            session = _session_store.load(spec_id) or {}
            exec_data = session.get("execution") or {}
            if exec_data:
                execution = {
                    "status": exec_data.get("status", "UNKNOWN"),
                    "mesh_report": (session.get("compilation") or {}).get("mesh_report"),
                    "smoke_test_report": (session.get("compilation") or {}).get("smoke_test_report"),
                    "run_report": exec_data.get("run_report"),
                    "plot_paths": exec_data.get("plot_paths", []),
                    "errors": [],
                }

    if execution is None:
        return ResultsResponse(
            success=False,
            job_id=job_id,
            error=f"Execution not found: {job_id}",
        )

    status = execution.get("status", "UNKNOWN")

    # Get or generate analysis report
    spec_id = _find_session_for_job(job_id)
    analysis_report: dict | None = None
    if spec_id:
        session = _session_store.load(spec_id) or {}
        exec_data = session.get("execution") or {}
        analysis_report = exec_data.get("analysis_report")
        if analysis_report is None and status in ("SUCCESS", "PARTIAL"):
            spec = _load_spec(spec_id)
            spec_dict = spec.model_dump() if spec else None
            analysis_report = _generate_analysis_report(job_id, execution, spec_dict)

    # Build metrics from analysis report
    metrics: dict[str, Any] = {}
    if analysis_report:
        metrics = analysis_report.get("metrics", {})

    # List artifacts
    artifacts = _list_artifacts(job_id)

    return ResultsResponse(
        success=status in ("SUCCESS", "PARTIAL"),
        job_id=job_id,
        status=status,
        metrics=metrics,
        analysis=analysis_report,
        artifacts=artifacts,
        mesh_report=execution.get("mesh_report"),
        smoke_test_report=execution.get("smoke_test_report"),
        run_report=execution.get("run_report"),
        error=execution.get("errors", [None])[0] if execution.get("errors") else None,
    )


@router.get("/{job_id}/report", response_model=AnalysisReportResponse)
async def get_report(job_id: str) -> AnalysisReportResponse:
    """Get a structured analysis report for a completed job.

    Returns metrics, flow features, convergence assessment, mesh
    quality, mass-conservation check, warnings, and limitations.
    """
    # Try session first
    spec_id = _find_session_for_job(job_id)
    if spec_id is not None:
        session = _session_store.load(spec_id) or {}
        exec_data = session.get("execution") or {}
        if exec_data.get("analysis_report"):
            return AnalysisReportResponse(
                success=True,
                job_id=job_id,
                report=exec_data["analysis_report"],
            )

    # Fall back to in-memory execution store + generate on the fly
    execution = _execution_store.get(job_id)
    if execution is None and spec_id is not None:
        session = _session_store.load(spec_id) or {}
        exec_data = session.get("execution") or {}
        if exec_data:
            execution = {
                "status": exec_data.get("status", "UNKNOWN"),
                "run_report": exec_data.get("run_report"),
                "mesh_report": (session.get("compilation") or {}).get("mesh_report"),
                "smoke_test_report": (session.get("compilation") or {}).get("smoke_test_report"),
            }

    if execution is None:
        return AnalysisReportResponse(
            success=False,
            job_id=job_id,
            error=f"Job not found: {job_id}",
        )

    spec = _load_spec(spec_id) if spec_id else None
    spec_dict = spec.model_dump() if spec else None
    report = _generate_analysis_report(job_id, execution, spec_dict)

    return AnalysisReportResponse(
        success=True,
        job_id=job_id,
        report=report,
    )


@router.get("/jobs/{job_id}/scientific-report", response_model=ScientificReportResponse)
async def get_scientific_report(job_id: str) -> ScientificReportResponse:
    """Get an LLM-generated scientific report with physics validation.

    Uses ResultSummaryBuilder to extract Cd/Cl/St from simulation outputs,
    PhysicsValidator to compare against empirical correlations, and
    LLMReportGenerator to produce a structured scientific report.
    Falls back to rule-based report if LLM is unavailable.
    """
    # Locate execution data
    spec_id = _find_session_for_job(job_id)
    execution: dict[str, Any] | None = None

    if spec_id is not None:
        session = _session_store.load(spec_id) or {}
        exec_data = session.get("execution") or {}
        if exec_data:
            execution = {
                "status": exec_data.get("status", "UNKNOWN"),
                "run_report": exec_data.get("run_report"),
                "mesh_report": (session.get("compilation") or {}).get("mesh_report"),
                "smoke_test_report": (session.get("compilation") or {}).get("smoke_test_report"),
            }

    if execution is None:
        execution = _execution_store.get(job_id)

    if execution is None:
        return ScientificReportResponse(
            success=False,
            job_id=job_id,
            error=f"Job not found: {job_id}",
        )

    spec = _load_spec(spec_id) if spec_id else None

    # Build the report using LLMReportGenerator
    llm_client = _get_llm_client()
    generator = LLMReportGenerator(llm_client=llm_client)

    report = generator.generate_report(
        execution_result=execution,
        mesh_report=execution.get("mesh_report"),
        smoke_report=execution.get("smoke_test_report"),
        sim_report=execution.get("run_report") or execution.get("simulation_report"),
        spec=spec,
        plot_paths=_list_plot_paths(job_id),
    )

    return ScientificReportResponse(
        success=True,
        job_id=job_id,
        report=report,
        physics_validation=report.get("physics_validation"),
        result_summary=report.get("result_summary"),
        report_source=report.get("report_source", "rule_based"),
    )


def _list_plot_paths(job_id: str) -> list[str]:
    """List plot file paths for a job."""
    results_dir = os.path.join("d:\\desktop\\AI FOR SCIENCE\\results", job_id)
    plots: list[str] = []
    if os.path.isdir(results_dir):
        for fname in os.listdir(results_dir):
            if fname.endswith((".png", ".gif", ".mp4")):
                plots.append(os.path.join(results_dir, fname))
    return plots

RESULTS_DIR = "d:\\desktop\\AI FOR SCIENCE\\results"


@router.get("/jobs/{job_id}/plots/{plot_name}")
async def get_plot(job_id: str, plot_name: str):
    """Serve a generated plot image or animation."""
    import os
    from fastapi.responses import FileResponse

    # Sanitize plot_name to prevent path traversal
    if ".." in plot_name or "/" in plot_name or "\\" in plot_name:
        raise HTTPException(status_code=400, detail="Invalid plot name")

    # Determine media type by extension
    ext_media = {
        ".png": "image/png",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
    }
    ext = None
    for e in ext_media:
        if plot_name.endswith(e):
            ext = e
            break
    if ext is None:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    file_path = os.path.join(RESULTS_DIR, job_id, plot_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Plot not found: {plot_name}")

    return FileResponse(file_path, media_type=ext_media[ext])


@router.get("/jobs/{job_id}/plots")
async def list_plots(job_id: str):
    """List all available plots and animations for a job."""
    import os

    plots_dir = os.path.join(RESULTS_DIR, job_id)
    if not os.path.exists(plots_dir):
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    supported_exts = (".png", ".gif", ".mp4")
    plots = []
    for fname in sorted(os.listdir(plots_dir)):
        if fname.endswith(supported_exts):
            plots.append({
                "name": fname,
                "url": f"/api/v5/cylinder-flow/jobs/{job_id}/plots/{fname}",
                "size_bytes": os.path.getsize(os.path.join(plots_dir, fname)),
            })

    return {"job_id": job_id, "plots": plots}

