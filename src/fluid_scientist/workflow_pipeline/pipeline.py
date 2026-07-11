"""V5 Compile-Ready Workflow Pipeline.

This is the main orchestrator that drives the complete pipeline from
user research description through understanding, design, closure,
capability resolution, case generation, validation, and finally to a
COMPILE_READY :class:`CompileReadyDraftView`.

The pipeline stages are:

    user description
      -> UNDERSTANDING  (scientific intent parsing via LLM)
      -> DESIGNING      (complete experiment design synthesis)
      -> CLOSING        (dependency-graph parameter closure)
      -> RESOLVING_CAPABILITIES (registry lookup / extension trigger)
      -> GENERATING_CASE (real OpenFOAM case on disk)
      -> VALIDATING_CASE (static + mesh + solver dry-run)
      -> COMPILE_READY  (publish CompileReadyDraftView)

On failure at any stage the pipeline records a structured
:class:`PipelineFailure` and transitions to FAILED instead of showing
the user a half-filled form.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fluid_scientist.capabilities import (
    CapabilityRegistry,
    CapabilityRequirement,
    RequirementGraphResolver,
    UnknownCapabilityOrchestrator,
    get_capability_registry,
)
from fluid_scientist.case_generation.validator import (
    CompileReadinessReport,
    CompileReadinessValidator,
)
from fluid_scientist.case_generation.writer import CaseManifest, OpenFOAMCaseWriter
from fluid_scientist.case_plan.compiler import NativeCaseCompiler
from fluid_scientist.closure import (
    ClosedParameter,
    ClosureResult,
    DesignClosureEngine,
)
from fluid_scientist.compat import UTC
from fluid_scientist.metrics import GoalToMetricCompiler, MetricDefinition
from fluid_scientist.workflow_pipeline import (
    CompileReadyDraftView,
    PipelineFailure,
    PipelineStatus,
    StageRecord,
)


# ---------------------------------------------------------------------------
# Pipeline state (accumulated through stages)
# ---------------------------------------------------------------------------


class PipelineState(BaseModel):
    """Mutable state carried through the pipeline stages."""

    session_id: str
    session_dir: str = ""
    current_stage: str = PipelineStatus.UNDERSTANDING
    stage_history: list[StageRecord] = Field(default_factory=list)
    user_description: str = ""
    # UNDERSTANDING output
    scientific_intent: dict[str, Any] = Field(default_factory=dict)
    # DESIGNING output
    raw_design: dict[str, Any] = Field(default_factory=dict)
    # CLOSING output
    closure_result: dict[str, Any] = Field(default_factory=dict)
    closed_parameters: dict[str, Any] = Field(default_factory=dict)
    # Resolved concrete design fields
    geometry: dict[str, Any] = Field(default_factory=dict)
    materials: dict[str, Any] = Field(default_factory=dict)
    boundary_conditions: dict[str, Any] = Field(default_factory=dict)
    initial_conditions: dict[str, Any] = Field(default_factory=dict)
    physical_models: dict[str, Any] = Field(default_factory=dict)
    solver: dict[str, Any] = Field(default_factory=dict)
    numerics: dict[str, Any] = Field(default_factory=dict)
    mesh: dict[str, Any] = Field(default_factory=dict)
    time_control: dict[str, Any] = Field(default_factory=dict)
    sampling: dict[str, Any] = Field(default_factory=dict)
    output_control: dict[str, Any] = Field(default_factory=dict)
    # CAPABILITY resolution output
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    capabilities_used: list[dict[str, Any]] = Field(default_factory=list)
    capabilities_missing: list[dict[str, Any]] = Field(default_factory=list)
    capabilities_extended: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_checkpoint: dict[str, Any] = Field(default_factory=dict)
    extension_runs: list[dict[str, Any]] = Field(default_factory=list)
    # Metrics
    scientific_metrics: list[dict[str, Any]] = Field(default_factory=list)
    boundary_verification_metrics: list[dict[str, Any]] = Field(default_factory=list)
    credibility_metrics: list[dict[str, Any]] = Field(default_factory=list)
    # GENERATION output
    case_dir: str = ""
    case_dict: dict[str, Any] = Field(default_factory=dict)
    case_manifest: dict[str, Any] = Field(default_factory=dict)
    # VALIDATION output
    validation_report: dict[str, Any] = Field(default_factory=dict)
    # FAILURE
    failure: dict[str, Any] | None = None
    # Final view
    draft_view: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# V5WorkflowPipeline
# ---------------------------------------------------------------------------


class V5WorkflowPipeline:
    """End-to-end pipeline that produces a CompileReadyDraftView."""

    def __init__(
        self,
        work_root: str | Path | None = None,
        registry: CapabilityRegistry | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self._work_root = Path(work_root) if work_root else Path(tempfile.mkdtemp(prefix="fluid_scientist_"))
        self._work_root.mkdir(parents=True, exist_ok=True)
        self._registry = registry or get_capability_registry()
        self._closure = DesignClosureEngine()
        self._metric_compiler = GoalToMetricCompiler()
        self._case_compiler = NativeCaseCompiler()
        self._case_writer = OpenFOAMCaseWriter()
        self._validator = CompileReadinessValidator()
        self._llm = llm_client

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        user_description: str,
        session_id: str | None = None,
        pre_extracted: dict[str, Any] | None = None,
    ) -> PipelineState:
        """Run the complete pipeline.

        Returns a :class:`PipelineState` whose ``current_stage`` is either
        COMPILE_READY (success) or FAILED.  All intermediate progress is
        recorded in ``stage_history``.
        """
        sid = session_id or str(uuid.uuid4())
        session_dir = self._work_root / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        state = PipelineState(session_id=sid, session_dir=str(session_dir), user_description=user_description)

        # Use pre-extracted intent if provided (e.g., from study_decomposition)
        if pre_extracted:
            state.scientific_intent = pre_extracted

        # Execute stages in order; each stage sets current_stage and
        # appends to stage_history.  On exception, set failure.
        stages = [
            (PipelineStatus.UNDERSTANDING, self._stage_understanding),
            (PipelineStatus.DESIGNING, self._stage_designing),
            (PipelineStatus.CLOSING, self._stage_closing),
            (PipelineStatus.RESOLVING_CAPABILITIES, self._stage_resolve_capabilities),
            (PipelineStatus.GENERATING_CASE, self._stage_generate_case),
            (PipelineStatus.VALIDATING_CASE, self._stage_validate_case),
        ]

        for stage_name, stage_fn in stages:
            state.current_stage = stage_name
            state.stage_history.append(StageRecord(stage=stage_name))
            try:
                stage_fn(state)
            except Exception as exc:
                import traceback as _tb
                tb_str = _tb.format_exc()
                state.failure = PipelineFailure(
                    failed_stage=stage_name,
                    failure_category="internal_error",
                    message=str(exc),
                    internal_details={
                        "exception_type": type(exc).__name__,
                        "traceback": tb_str,
                    },
                    can_retry=True,
                ).model_dump()
                state.current_stage = PipelineStatus.FAILED
                state.stage_history.append(StageRecord(stage=PipelineStatus.FAILED, error=str(exc)))
                return state

            if state.current_stage == PipelineStatus.FAILED:
                return state

        # Finalize to COMPILE_READY
        state.current_stage = PipelineStatus.COMPILE_READY
        state.stage_history.append(StageRecord(stage=PipelineStatus.COMPILE_READY))
        state.draft_view = self._build_compile_ready_view(state).model_dump()
        self._save_state(state)
        return state

    def modify(
        self,
        session_id: str,
        modification_text: str,
    ) -> PipelineState:
        """Apply an incremental modification to an existing COMPILE_READY case.

        This implements the ChangeProposal workflow:
        1. Load the existing session state
        2. Parse the modification request into DraftChange(s)
        3. Apply changes to the design parameters
        4. Regenerate only the affected parts of the case
        5. Re-validate
        """
        state = self._load_state(session_id)
        if state is None:
            # Return a failure state
            sid = session_id
            session_dir = self._work_root / sid
            state = PipelineState(session_id=sid, session_dir=str(session_dir), user_description="")
            state.current_stage = PipelineStatus.FAILED
            state.failure = PipelineFailure(
                failed_stage=PipelineStatus.COMPILE_READY,
                failure_category="internal_error",
                message=f"Session {session_id} not found.",
            ).model_dump()
            return state

        # Record the modification request
        state.user_description = (state.user_description or "") + f"\n[MODIFICATION] {modification_text}"

        # Parse modification into parameter changes
        changes = self._parse_modification(modification_text, state)
        if not changes:
            state.failure = PipelineFailure(
                failed_stage=PipelineStatus.COMPILE_READY,
                failure_category="internal_error",
                message=f"Could not understand modification: {modification_text}",
            ).model_dump()
            state.current_stage = PipelineStatus.FAILED
            return state

        # Apply changes to raw_design
        self._apply_changes_to_design(state, changes)

        # Re-run affected stages: DESIGNING -> CLOSING -> GENERATING -> VALIDATING
        # We don't need to re-run UNDERSTANDING since intent hasn't fundamentally changed
        state.current_stage = PipelineStatus.DESIGNING
        state.stage_history.append(StageRecord(stage=PipelineStatus.DESIGNING, detail="re-run after modification"))

        stages_to_rerun = [
            (PipelineStatus.DESIGNING, self._stage_designing),
            (PipelineStatus.CLOSING, self._stage_closing),
            (PipelineStatus.RESOLVING_CAPABILITIES, self._stage_resolve_capabilities),
            (PipelineStatus.GENERATING_CASE, self._stage_generate_case),
            (PipelineStatus.VALIDATING_CASE, self._stage_validate_case),
        ]

        for stage_name, stage_fn in stages_to_rerun:
            state.current_stage = stage_name
            state.stage_history.append(StageRecord(stage=stage_name))
            try:
                stage_fn(state)
            except Exception as exc:
                import traceback as _tb
                state.failure = PipelineFailure(
                    failed_stage=stage_name,
                    failure_category="internal_error",
                    message=str(exc),
                    internal_details={
                        "exception_type": type(exc).__name__,
                        "traceback": _tb.format_exc(),
                    },
                    can_retry=True,
                ).model_dump()
                state.current_stage = PipelineStatus.FAILED
                self._save_state(state)
                return state
            if state.current_stage == PipelineStatus.FAILED:
                self._save_state(state)
                return state

        # Finalize
        state.current_stage = PipelineStatus.COMPILE_READY
        state.stage_history.append(StageRecord(stage=PipelineStatus.COMPILE_READY, detail=f"modified: {modification_text[:80]}"))
        state.draft_view = self._build_compile_ready_view(state).model_dump()
        self._save_state(state)
        return state

    def _parse_modification(self, text: str, state: PipelineState) -> list[dict[str, Any]]:
        """Parse a natural-language modification into a list of DraftChange-like dicts.

        Supports simple parameter changes: Re, velocity, mesh resolution,
        turbulence model, solver, end time, delta T, etc.
        """
        lower = text.lower()
        changes: list[dict[str, Any]] = []

        # Reynolds number change
        m = re.search(r"\bre\s*(?:to|=|:)?\s*(\d+(?:\.\d+)?)", lower)
        if m:
            new_re = float(m.group(1))
            changes.append({
                "change_type": "set_parameter",
                "target_path": "dimensionless_parameters.Re",
                "new_value": new_re,
                "reason": f"User requested Re={new_re}",
            })

        # Inlet velocity
        m = re.search(r"(?:velocity|speed|u_ref|inlet)\s*[=:]?\s*(\d+(?:\.\d+)?)", lower)
        if m and "re" not in lower[:m.start()]:
            changes.append({
                "change_type": "set_parameter",
                "target_path": "boundary_conditions.inlet.U.value",
                "new_value": [float(m.group(1)), 0.0, 0.0],
                "reason": f"User requested velocity={m.group(1)}",
            })

        # Turbulence model
        if "wale" in lower:
            changes.append({"change_type": "change_physics_model", "target_path": "turbulence_model", "new_value": "WALE"})
            changes.append({"change_type": "change_physics_model", "target_path": "turbulence_family", "new_value": "LES"})
        elif "k-omega" in lower or "komega" in lower or "sst" in lower:
            changes.append({"change_type": "change_physics_model", "target_path": "turbulence_model", "new_value": "kOmegaSST"})
            changes.append({"change_type": "change_physics_model", "target_path": "turbulence_family", "new_value": "RANS"})
        elif "laminar" in lower:
            changes.append({"change_type": "change_physics_model", "target_path": "turbulence_model", "new_value": "laminar"})
            changes.append({"change_type": "change_physics_model", "target_path": "turbulence_family", "new_value": "laminar"})

        # Solver
        if "simplefoam" in lower:
            changes.append({"change_type": "change_solver", "target_path": "solver", "new_value": "simpleFoam"})
        elif "pimplefoam" in lower:
            changes.append({"change_type": "change_solver", "target_path": "solver", "new_value": "pimpleFoam"})

        # End time - match "end time 50", "endTime 50", "end_time=50", "simulate for 50", "end time to 50"
        m = re.search(r"(?:end(?:_|\s+)?time|simulate\s+for|set\s+end(?:_|\s+)?time\s+to)\s*[=:]?\s*(\d+(?:\.\d+)?)", lower)
        if m:
            changes.append({"change_type": "set_parameter", "target_path": "time_control.end_time", "new_value": float(m.group(1))})

        # Mesh refinement
        if any(k in lower for k in ("finer mesh", "refine mesh", "coarser mesh", "more cells", "less cells")):
            factor = 1.5 if any(k in lower for k in ("finer", "more", "refine")) else 0.67
            changes.append({"change_type": "change_mesh", "target_path": "mesh_resolution", "new_value": factor})

        return changes

    def _apply_changes_to_design(self, state: PipelineState, changes: list[dict[str, Any]]) -> None:
        """Apply parsed changes to the state's raw_design and scientific_intent."""
        intent = state.scientific_intent
        design = state.raw_design

        for ch in changes:
            tp = ch["target_path"]
            nv = ch["new_value"]

            if tp == "dimensionless_parameters.Re":
                intent.setdefault("dimensionless_parameters", {})["Re"] = nv
            elif tp == "turbulence_model":
                intent["turbulence_model"] = nv
            elif tp == "turbulence_family":
                intent["turbulence_family"] = nv
            elif tp == "solver":
                intent["solver"] = nv
            elif tp == "time_control.end_time":
                # Store in design for use during case generation
                design.setdefault("time_control", {})["end_time"] = nv
            elif tp == "mesh_resolution":
                factor = float(nv)
                intent["_mesh_resolution_factor"] = factor

    def _save_state(self, state: PipelineState) -> None:
        """Persist state to disk as JSON."""
        session_dir = Path(state.session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        state_path = session_dir / "pipeline_state.json"
        state_path.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _load_state(self, session_id: str) -> PipelineState | None:
        """Load a previously saved state from disk."""
        session_dir = self._work_root / session_id
        state_path = session_dir / "pipeline_state.json"
        if not state_path.is_file():
            return None
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            return PipelineState.model_validate(data)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Stage 1: UNDERSTANDING  -- parse scientific intent
    # ------------------------------------------------------------------

    def _stage_understanding(self, state: PipelineState) -> None:
        """Parse the user description into structured scientific intent.

        In real mode the configured LLM is mandatory.  The deterministic
        extractor is available only when tests or local development explicitly
        set ``FLUID_SCIENTIST_LLM_MODE=mock``.
        """
        text = state.user_description
        intent: dict[str, Any] = {}

        if state.scientific_intent:
            intent = state.scientific_intent
        elif self._llm is not None:
            try:
                intent = self._extract_intent_with_llm(text)
            except Exception as exc:
                state.failure = PipelineFailure(
                    failed_stage=PipelineStatus.UNDERSTANDING,
                    failure_category="semantic_parsing",
                    message=f"Scientific intent model call failed: {exc}",
                    internal_details={"fallback_used": False},
                    can_retry=True,
                    requires_user_input=False,
                ).model_dump()
                state.current_stage = PipelineStatus.FAILED
                return
        elif os.environ.get("FLUID_SCIENTIST_LLM_MODE") == "mock":
            intent = self._extract_intent_deterministic(text)
        else:
            state.failure = PipelineFailure(
                failed_stage=PipelineStatus.UNDERSTANDING,
                failure_category="semantic_parsing",
                message=(
                    "No LLM client configured. Set FLUID_SCIENTIST_LLM_MODE=mock "
                    "only for tests; production cannot generate a draft from "
                    "keyword fallback."
                ),
                internal_details={"fallback_used": False},
                can_retry=True,
                requires_user_input=False,
            ).model_dump()
            state.current_stage = PipelineStatus.FAILED
            return

        # Ensure minimal required fields exist
        intent.setdefault("research_objective", text)
        intent.setdefault("study_id", str(uuid.uuid4()))
        intent.setdefault("flow_regime", "turbulent")
        intent.setdefault("temporal_mode", "transient")
        intent.setdefault("analysis_goals", [])

        state.scientific_intent = intent

    def _extract_intent_with_llm(self, text: str) -> dict[str, Any]:
        """Call LLM for structured scientific intent parsing."""
        if self._llm is None:
            raise RuntimeError("LLM client is not configured")
        try:
            output, _ = self._llm.call(
                purpose="scientific_intent",
                prompt_name="intent_system",
                system_prompt=(
                    "You are a CFD research assistant. Parse the user's research "
                    "description into a structured JSON object with fields: "
                    "research_objective, geometry_family, flow_regime (laminar/transitional/turbulent), "
                    "temporal_mode (steady/transient/periodic), physics_family, "
                    "boundaries (list of {physical_role, field_conditions}), "
                    "materials, motions (list), analysis_goals (list of {phenomenon, target_quantity, temporal_mode, statistic}), "
                    "dimensionless_parameters (dict of name->value), multiphase, heat_transfer, fsi. "
                    "Return ONLY a valid JSON object."
                ),
                user_message=text,
                session_id=None,
                output_schema="json",
            )
            if isinstance(output, dict):
                return output
        except Exception as exc:
            raise RuntimeError(f"LLM scientific intent parsing failed: {exc}") from exc
        raise RuntimeError("LLM scientific intent parsing returned no JSON object")

    def _extract_intent_deterministic(self, text: str) -> dict[str, Any]:
        """Deterministic keyword-based fallback for intent extraction."""
        lower = text.lower()
        intent: dict[str, Any] = {
            "research_objective": text,
            "study_id": str(uuid.uuid4()),
            "geometry_family": "generic",
            "flow_regime": "turbulent",
            "temporal_mode": "transient",
            "physics_family": "single_phase_incompressible",
            "multiphase": False,
            "heat_transfer": False,
            "fsi": False,
            "dimensionless_parameters": {},
            "boundaries": [],
            "motions": [],
            "materials": [],
            "analysis_goals": [],
        }
        # Geometry family detection
        if any(k in lower for k in ("pipe", "管流", "管道", "duct", "channel")):
            intent["geometry_family"] = "internal_flow"
            intent["boundaries"].append({"physical_role": "inlet", "field_conditions": {}})
            intent["boundaries"].append({"physical_role": "outlet", "field_conditions": {}})
            intent["boundaries"].append({"physical_role": "wall", "field_conditions": {}})
        elif any(k in lower for k in ("cylinder", "圆柱", "airfoil", "翼型", "bluff body", "钝体")):
            intent["geometry_family"] = "external_flow"
            intent["boundaries"].append({"physical_role": "inlet", "field_conditions": {}})
            intent["boundaries"].append({"physical_role": "outlet", "field_conditions": {}})
            intent["boundaries"].append({"physical_role": "wall", "field_conditions": {"name": "body"}})
            intent["boundaries"].append({"physical_role": "symmetry", "field_conditions": {}})
        elif any(k in lower for k in ("jet", "射流", "imping", "冲击")):
            intent["geometry_family"] = "jet_impingement"
            intent["boundaries"].append({"physical_role": "inlet", "field_conditions": {}})
            intent["boundaries"].append({"physical_role": "outlet", "field_conditions": {}})
            intent["boundaries"].append({"physical_role": "wall", "field_conditions": {"name": "target"}})
        else:
            intent["geometry_family"] = "generic_enclosed"
            intent["boundaries"].append({"physical_role": "inlet", "field_conditions": {}})
            intent["boundaries"].append({"physical_role": "outlet", "field_conditions": {}})
            intent["boundaries"].append({"physical_role": "wall", "field_conditions": {}})

        # Reynolds number
        import re
        re_match = re.search(r"\bre\s*[=:]?\s*(\d+(?:\.\d+)?)", lower)
        if re_match:
            intent["dimensionless_parameters"]["Re"] = float(re_match.group(1))
        cn_match = re.search(r"雷诺数[：:\s]*(\d+(?:\.\d+)?)", text)
        if cn_match:
            intent["dimensionless_parameters"]["Re"] = float(cn_match.group(1))

        # Flow regime from Re
        re_val = intent["dimensionless_parameters"].get("Re")
        if re_val is not None:
            if re_val < 2300:
                intent["flow_regime"] = "laminar"
            elif re_val < 10000:
                intent["flow_regime"] = "transitional"
            else:
                intent["flow_regime"] = "turbulent"

        # Motion detection
        if any(k in lower for k in ("rotat", "旋转", "spin")):
            intent["motions"].append({"motion_type": "constant_rotation"})
        if any(k in lower for k in ("pitch", "oscillat", "俯仰", "振动", "周期")):
            intent["motions"].append({"motion_type": "oscillatory_rotation"})

        # Heat transfer
        if any(k in lower for k in ("heat", "热", "nusselt", "temperature")):
            intent["heat_transfer"] = True
        # Multiphase
        if any(k in lower for k in ("multiphase", "two-phase", "vof", "cavitation", "多相", "空化")):
            intent["multiphase"] = True

        # Turbulence model/family (user-specified overrides Re-based)
        turb_model = None
        turb_family = None
        if "wale" in lower:
            turb_model = "WALE"
            turb_family = "LES"
        elif "smagorinsky" in lower or "smagorinsky-lilly" in lower:
            turb_model = "Smagorinsky"
            turb_family = "LES"
        elif "kepsilon" in lower or "k-epsilon" in lower or "realizable" in lower:
            turb_model = "kEpsilon"
            turb_family = "RANS"
        elif "komega" in lower or "k-omega" in lower or "sst" in lower:
            turb_model = "kOmegaSST"
            turb_family = "RANS"
        elif "spalart" in lower or "spalart-allmaras" in lower:
            turb_model = "SpalartAllmaras"
            turb_family = "RANS"
        elif re.search(r"\bles\b", lower) or "large eddy" in lower:
            turb_family = "LES"
            if turb_model is None:
                turb_model = "WALE"
        elif "rans" in lower:
            turb_family = "RANS"
            if turb_model is None:
                turb_model = "kOmegaSST"
        elif "laminar" in lower:
            turb_family = "laminar"
            turb_model = "laminar"
        if turb_family:
            intent["turbulence_family"] = turb_family
        if turb_model:
            intent["turbulence_model"] = turb_model

        # Solver (user-specified)
        if "simplefoam" in lower:
            intent["solver"] = "simpleFoam"
            intent["temporal_mode"] = "steady"
        elif "pimplefoam" in lower:
            intent["solver"] = "pimpleFoam"
            intent["temporal_mode"] = "transient"
        elif "rhopimplefoam" in lower:
            intent["solver"] = "rhoPimpleFoam"
            intent["temporal_mode"] = "transient"
            intent["compressibility"] = "compressible"
        elif "rhosimplefoam" in lower:
            intent["solver"] = "rhoSimpleFoam"
            intent["temporal_mode"] = "steady"
            intent["compressibility"] = "compressible"

        # Analysis goals
        goals: list[dict[str, Any]] = []
        if any(k in lower for k in ("drag", "lift", "force", "阻力", "升力")):
            goals.append({"phenomenon": "force_coefficients", "target_quantity": "Cd_Cl", "temporal_mode": "statistical", "statistic": "mean+rms+psd"})
        if any(k in lower for k in ("wake", "尾迹", "vortex", "涡")):
            goals.append({"phenomenon": "wake_vortex", "target_quantity": "wake_structure", "temporal_mode": "statistical", "statistic": "mean+snapshot"})
        if any(k in lower for k in ("pressure drop", "压降", "flow rate", "流量")):
            goals.append({"phenomenon": "pressure_loss", "target_quantity": "delta_p", "temporal_mode": "time_averaged", "statistic": "mean"})
        if any(k in lower for k in ("spectrum", "frequency", "频谱", "频率", "strouhal")):
            if not any(g["phenomenon"] == "force_coefficients" for g in goals):
                goals.append({"phenomenon": "force_coefficients", "target_quantity": "Cd_Cl", "temporal_mode": "statistical", "statistic": "psd"})
        if not goals:
            goals.append({"phenomenon": "baseline_flow", "target_quantity": "velocity_field", "temporal_mode": "time_averaged", "statistic": "mean"})
        intent["analysis_goals"] = goals
        return intent

    # ------------------------------------------------------------------
    # Stage 2: DESIGNING  -- synthesize complete experiment design
    # ------------------------------------------------------------------

    def _stage_designing(self, state: PipelineState) -> None:
        """Build a complete experiment design with all fields populated.

        Every design field receives a value (from user, derived, or
        system-selected).  No field is left as "to be filled".
        """
        intent = state.scientific_intent
        geo_family = intent.get("geometry_family", "generic")
        re_val = intent.get("dimensionless_parameters", {}).get("Re", 3900.0)
        temporal = intent.get("temporal_mode", "transient")
        is_steady = temporal == "steady"
        is_compressible = intent.get("compressibility", "incompressible") != "incompressible"

        # Geometry defaults (all non-dimensionalized by L_ref=1)
        L_ref = 1.0
        if geo_family == "internal_flow":
            domain = {"length": 20.0, "diameter": L_ref, "spanwise": 3.14159}
            cells = {"nx": 200, "ny": 60, "nz": 40}
            wall_patches = ["wall"]
            inlet_patches = ["inlet"]
            outlet_patches = ["outlet"]
            has_embedded_surface = False
            embedded_surface_name = ""
            fo_wall_patches = ["wall"]
        elif geo_family == "external_flow":
            domain = {"upstream": 10.0, "downstream": 25.0, "cross_stream": 20.0, "spanwise": 3.14159}
            cells = {"nx": 300, "ny": 150, "nz": 40}
            # Background mesh has no wall patches (just inlet/outlet/symmetry).
            # The body surface is added by snappyHexMesh as a wall patch named "body".
            # Field files do NOT contain "body" BC; snappyHexMesh adds the patch
            # at runtime and default zeroGradient is applied.
            wall_patches: list[str] = []
            inlet_patches = ["inlet"]
            outlet_patches = ["outlet"]
            has_embedded_surface = True
            embedded_surface_name = "body"
            fo_wall_patches = ["body"]  # for function objects (patches added by SHM)
        elif geo_family == "jet_impingement":
            domain = {"length": 20.0, "height": 10.0, "spanwise": 3.14159}
            cells = {"nx": 200, "ny": 100, "nz": 1}
            wall_patches = ["target", "top"]
            inlet_patches = ["inlet"]
            outlet_patches = ["outlet"]
            has_embedded_surface = False
            embedded_surface_name = ""
            fo_wall_patches = ["target", "top"]
        else:
            domain = {"length": 20.0, "height": 2.0, "spanwise": 3.14159}
            cells = {"nx": 200, "ny": 60, "nz": 1}
            wall_patches = ["top", "bottom"]
            inlet_patches = ["inlet"]
            outlet_patches = ["outlet"]
            has_embedded_surface = False
            embedded_surface_name = ""
            fo_wall_patches = ["top", "bottom"]

        # Apply mesh resolution factor from modifications if present
        mesh_factor = intent.get("_mesh_resolution_factor")
        if mesh_factor and isinstance(mesh_factor, (int, float)) and mesh_factor > 0:
            for k in ("nx", "ny", "nz"):
                if k in cells:
                    cells[k] = max(4, int(cells[k] * mesh_factor))

        # Turbulence model selection (user-specified overrides Re-based default)
        user_turb_family = intent.get("turbulence_family")
        user_turb_model = intent.get("turbulence_model")
        if user_turb_family or user_turb_model:
            turb_family = user_turb_family or ("LES" if user_turb_model in ("WALE", "Smagorinsky", "kEqn", "dynamicKEqn") else "RANS")
            turb_model = user_turb_model or ("WALE" if turb_family == "LES" else "kOmegaSST")
            yp_target = 1.0 if turb_family != "laminar" else 30.0
        else:
            if re_val < 2300:
                turb_model = "laminar"
                turb_family = "laminar"
                yp_target = 30.0
            elif is_steady or re_val < 10000:
                turb_model = "kOmegaSST"
                turb_family = "RANS"
                yp_target = 1.0
            else:
                turb_model = "WALE"
                turb_family = "LES"
                yp_target = 1.0

        # Solver selection (user-specified overrides default)
        user_solver = intent.get("solver")
        if user_solver:
            solver_name = user_solver
        else:
            if is_steady:
                solver_name = "simpleFoam" if not is_compressible else "rhoSimpleFoam"
            else:
                solver_name = "pimpleFoam" if not is_compressible else "rhoPimpleFoam"

        design = {
            "geometry": {
                "family": geo_family,
                "reference_length": L_ref,
                "domain": domain,
                "cells": cells,
                "source": "SYSTEM_DERIVED",
            },
            "materials": {
                "rho": 1.0,
                "nu": None,  # to be closed
                "source": "ASSUMED_BASELINE",
            },
            "boundary_patches": {
                "walls": wall_patches,
                "fo_walls": fo_wall_patches,
                "inlets": inlet_patches,
                "outlets": outlet_patches,
            },
            "embedded_surface": {
                "present": has_embedded_surface,
                "name": embedded_surface_name,
            },
            "boundary_conditions": {},
            "initial_conditions": {
                "U": [1.0, 0.0, 0.0],
                "p": 0.0,
            },
            "physical_models": {
                "flow_type": "incompressible" if not is_compressible else "compressible",
                "turbulence_model": turb_model,
                "turbulence_family": turb_family,
                "multiphase": intent.get("multiphase", False),
                "heat_transfer": intent.get("heat_transfer", False),
                "source": "SYSTEM_SELECTED",
            },
            "solver": {"name": solver_name, "source": "SYSTEM_SELECTED"},
            "numerics": {
                "temporal_mode": temporal,
                "steady": is_steady,
                "source": "SYSTEM_SELECTED",
            },
            "mesh": {
                "cells": cells,
                "target_y_plus": yp_target,
                "source": "SYSTEM_DERIVED",
            },
            "dimensionless_parameters": dict(intent.get("dimensionless_parameters", {})),
            "target_y_plus": yp_target,
            "analysis_goals": intent.get("analysis_goals", []),
        }
        # Ensure Re is present
        design["dimensionless_parameters"].setdefault("Re", re_val)

        # Preserve time_control overrides from previous state (from modifications)
        prev_tc = state.raw_design.get("time_control") if state.raw_design else None
        if prev_tc:
            design["time_control"] = prev_tc

        # Seed initial known parameters for closure
        state.raw_design = design
        state.geometry = design["geometry"]
        state.mesh = design["mesh"]
        state.physical_models = design["physical_models"]
        state.solver = design["solver"]
        state.numerics = design["numerics"]

    # ------------------------------------------------------------------
    # Stage 3: CLOSING  -- dependency-graph parameter closure
    # ------------------------------------------------------------------

    def _stage_closing(self, state: PipelineState) -> None:
        """Run the DesignClosureEngine to derive all dependent parameters."""
        design = state.raw_design
        dp = design.get("dimensionless_parameters", {})

        known: dict[str, ClosedParameter] = {}
        # User/derived known values
        known["U_ref"] = ClosedParameter(name="U_ref", value=1.0, unit="m/s", source="ASSUMED_BASELINE", reason="Non-dimensional reference velocity.", confidence=0.7)
        known["L_ref"] = ClosedParameter(name="L_ref", value=1.0, unit="m", source="ASSUMED_BASELINE", reason="Non-dimensional reference length.", confidence=0.7)
        known["D"] = ClosedParameter(name="D", value=1.0, unit="m", source="ASSUMED_BASELINE", reason="Reference diameter.", confidence=0.7)
        known["rho"] = ClosedParameter(name="rho", value=1.0, unit="kg/m^3", source="ASSUMED_BASELINE", reason="Non-dimensional density.", confidence=0.9)
        known["temporal_mode"] = ClosedParameter(name="temporal_mode", value=design["numerics"].get("temporal_mode", "transient"), source="SYSTEM_SELECTED")
        known["compressibility"] = ClosedParameter(name="compressibility", value="incompressible", source="SYSTEM_SELECTED")
        if "Re" in dp:
            known["Re"] = ClosedParameter(name="Re", value=float(dp["Re"]), source="USER_SPECIFIED" if dp["Re"] != 3900 else "TEMPLATE_DEFAULT", reason="Reynolds number.")
        yp = design.get("target_y_plus")
        if yp:
            known["target_y_plus"] = ClosedParameter(name="target_y_plus", value=float(yp), source="SYSTEM_SELECTED")

        closure_result = self._closure.close(known)
        state.closure_result = closure_result.model_dump()
        state.closed_parameters = closure_result.resolved_values

        if not closure_result.fully_closed and closure_result.unresolved:
            # Even if some parameters are unresolved, we proceed with
            # conservative defaults rather than blocking the user.
            pass

        cv = closure_result.parameters
        # Populate all design sections from closed parameters
        state.materials = {
            "rho": cv["rho"].value if "rho" in cv else 1.0,
            "nu": cv["nu"].value if "nu" in cv else 1.0 / 3900.0,
            "unit": "SI-nondim",
            "source": "SYSTEM_DERIVED",
        }
        state.time_control = {
            "delta_t": cv["delta_t"].value if "delta_t" in cv else 0.002,
            "end_time": cv["end_time"].value if "end_time" in cv else 20.0,
            "Co_max": cv["Co_max"].value if "Co_max" in cv else 0.5,
            "write_interval": int(cv["write_interval"].value) if "write_interval" in cv else 100,
            "statistics_start": cv["statistics_start_time"].value if "statistics_start_time" in cv else 5.0,
            "flow_through_time": cv["flow_through_time"].value if "flow_through_time" in cv else 1.0,
            "source": "SYSTEM_DERIVED",
        }
        # Apply any overrides from raw_design (set by modifications)
        tc_override = design.get("time_control", {})
        for k, v in tc_override.items():
            if v is not None:
                state.time_control[k] = v
                state.time_control["source"] = "USER_MODIFIED"
        state.sampling = {
            "sampling_frequency": cv["sampling_frequency"].value if "sampling_frequency" in cv else 100.0,
            "start_time": state.time_control["statistics_start"],
            "source": "SYSTEM_DERIVED",
        }
        state.output_control = {
            "fields": ["U", "p"],
            "write_interval": state.time_control["write_interval"],
            "source": "SYSTEM_SELECTED",
        }
        # Mesh: add first layer height
        if "first_layer_height" in cv:
            state.mesh["first_layer_height"] = cv["first_layer_height"].value
        state.mesh["n_cells_estimate"] = int(cv["n_cells"].value) if "n_cells" in cv else 500000
        # Solver: override if closed
        if "solver" in cv:
            state.solver["name"] = cv["solver"].value
        # Build boundary conditions
        self._build_boundary_conditions(state, cv)
        # Build metrics
        self._compile_metrics(state)

    def _build_boundary_conditions(self, state: PipelineState, cv: dict[str, ClosedParameter]) -> None:
        """Build fully specified boundary condition dicts."""
        bp = state.raw_design.get("boundary_patches", {})
        walls = bp.get("walls", ["wall"])
        inlets = bp.get("inlets", ["inlet"])
        outlets = bp.get("outlets", ["outlet"])
        turb_fam = state.physical_models.get("turbulence_family", "LES")
        u_ref = 1.0
        bcs: dict[str, Any] = {}
        for ip in inlets:
            bcs[ip] = {
                "type": "inlet_velocity",
                "U": {"type": "fixedValue", "value": [u_ref, 0.0, 0.0]},
                "p": {"type": "zeroGradient"},
                "source": "SYSTEM_SELECTED",
            }
            if turb_fam in ("RANS", "LES"):
                bcs[ip]["turbulence"] = {
                    "intensity": 0.01,
                    "mixing_length": 0.1,
                }
        for op in outlets:
            bcs[op] = {
                "type": "pressure_outlet",
                "U": {"type": "zeroGradient"},
                "p": {"type": "fixedValue", "value": 0.0},
                "source": "SYSTEM_SELECTED",
            }
        for wp in walls:
            is_body = wp in ("cylinder", "body", "target")
            bcs[wp] = {
                "type": "no_slip_wall",
                "U": {"type": "noSlip"},
                "p": {"type": "zeroGradient"},
                "source": "SYSTEM_SELECTED",
            }
        # Symmetry/top/bottom for external flow
        if state.raw_design.get("geometry", {}).get("family") == "external_flow":
            for sym in ("top", "bottom"):
                bcs[sym] = {
                    "type": "free_slip",
                    "U": {"type": "slip"},
                    "p": {"type": "zeroGradient"},
                    "source": "SYSTEM_SELECTED",
                }
        # Front/back empty for 2D (nz=1)
        cells = state.geometry.get("cells", {})
        if cells.get("nz", 1) == 1:
            bcs["front"] = {"type": "empty_2d", "U": {"type": "empty"}, "p": {"type": "empty"}, "source": "SYSTEM_SELECTED"}
            bcs["back"] = {"type": "empty_2d", "U": {"type": "empty"}, "p": {"type": "empty"}, "source": "SYSTEM_SELECTED"}
        else:
            # Periodic in spanwise if 3D
            bcs["front"] = {"type": "periodic", "U": {"type": "cyclic"}, "p": {"type": "cyclic"}, "neighbourPatch": "back", "source": "SYSTEM_SELECTED"}
            bcs["back"] = {"type": "periodic", "U": {"type": "cyclic"}, "p": {"type": "cyclic"}, "neighbourPatch": "front", "source": "SYSTEM_SELECTED"}
        state.boundary_conditions = bcs

    def _compile_metrics(self, state: PipelineState) -> None:
        """Compile analysis goals into executable metrics."""
        bp = state.raw_design.get("boundary_patches", {})
        boundary_patches = {
            "walls": bp.get("walls", ["wall"]),
            "fo_walls": bp.get("fo_walls", bp.get("walls", ["wall"])),
            "inlets": bp.get("inlets", ["inlet"]),
            "outlets": bp.get("outlets", ["outlet"]),
        }
        metrics = self._metric_compiler.compile_all_to_dicts(
            state.scientific_intent.get("analysis_goals", []),
            boundary_patches=boundary_patches,
        )
        state.scientific_metrics = metrics.get("scientific", [])
        state.boundary_verification_metrics = metrics.get("boundary_verification", [])
        state.credibility_metrics = metrics.get("numerical_credibility", [])

    # ------------------------------------------------------------------
    # Stage 4: RESOLVING_CAPABILITIES  -- registry check + extension
    # ------------------------------------------------------------------

    def _stage_resolve_capabilities(self, state: PipelineState) -> None:
        """Resolve capabilities required by the design against the registry.

        Missing mandatory capabilities are blocking.  They must be resolved by
        the registry or by an internal extension flow before case generation.
        """
        requirements: list[CapabilityRequirement] = []
        # Determine which capabilities are needed
        geo_family = state.raw_design.get("geometry", {}).get("family", "generic")
        turb_fam = state.physical_models.get("turbulence_family", "laminar")
        solver_name = state.solver.get("name", "pimpleFoam")
        has_motion = len(state.scientific_intent.get("motions", [])) > 0

        requirements.append(CapabilityRequirement(
            requirement_id="req_mesh",
            capability_type="mesh_generator",
            capability_id="mesh.block_mesh",
            description="blockMesh mesh generation",
            mandatory=True,
        ))
        requirements.append(CapabilityRequirement(
            requirement_id="req_geometry",
            capability_type="geometry_generator",
            keywords=[geo_family, "block_mesh"],
            description=f"Geometry generation for {geo_family}",
            mandatory=True,
        ))
        requirements.append(CapabilityRequirement(
            requirement_id="req_physics",
            capability_type="physics_model_compiler",
            keywords=[state.physical_models.get("turbulence_model", "laminar"), turb_fam],
            description="Physics model compilation",
            mandatory=True,
        ))
        requirements.append(CapabilityRequirement(
            requirement_id="req_solver",
            capability_type="solver_adapter",
            keywords=[solver_name],
            description=f"Solver adapter for {solver_name}",
            mandatory=True,
        ))
        requirements.append(CapabilityRequirement(
            requirement_id="req_bcs",
            capability_type="boundary_writer",
            keywords=["velocity_inlet", "pressure_outlet", "no_slip"],
            description="Boundary condition writers",
            mandatory=True,
        ))
        requirements.append(CapabilityRequirement(
            requirement_id="req_ics",
            capability_type="initial_condition_writer",
            capability_id="ic.uniform_fields",
            description="Initial condition writers",
            mandatory=True,
        ))
        requirements.append(CapabilityRequirement(
            requirement_id="req_fo",
            capability_type="function_object_generator",
            keywords=["residuals", "forceCoeffs", "probes", "fieldAverage", "CourantNo", "yPlus", "Q"],
            description="Function object generators for metrics",
            mandatory=True,
        ))
        requirements.append(CapabilityRequirement(
            requirement_id="req_postprocess",
            capability_type="postprocessor",
            keywords=["force_spectrum", "pressure_drop", "velocity_profile", "conservation", "mesh_check", "residual_analysis"],
            description="Postprocessors for metric computation",
            mandatory=True,
        ))
        requirements.append(CapabilityRequirement(
            requirement_id="req_validation",
            capability_type="result_validator",
            keywords=["dictionary_parse", "patch_consistency", "check_mesh", "solver_dry_run"],
            description="Validation chain",
            mandatory=True,
        ))
        if has_motion:
            requirements.append(CapabilityRequirement(
                requirement_id="req_motion",
                capability_type="motion_compiler",
                keywords=["oscillating", "rotating"],
                description="Dynamic mesh / motion",
                mandatory=True,
            ))

        health_report = self._registry.health_check(mutate=True)
        resolver = RequirementGraphResolver(
            self._registry,
            require_verified=True,
            require_healthy=True,
        )
        graph = resolver.resolve(requirements)
        state.requirements = [r.model_dump() for r in graph.requirements]
        state.capabilities_used = [
            cap.model_dump() for cap in graph.resolved_capabilities
        ]
        state.capabilities_missing = [
            resolution.model_dump()
            for resolution in graph.unresolved
            if resolution.requirement.mandatory
        ]
        state.capabilities_extended = []

        mandatory_missing = state.capabilities_missing
        if mandatory_missing:
            orchestrator = UnknownCapabilityOrchestrator(self._work_root)
            extension_result = orchestrator.orchestrate(
                session_id=state.session_id,
                scientific_intent=state.scientific_intent,
                simulation_plan=state.raw_design,
                requirement_graph=graph,
                case_plan={
                    "geometry": state.geometry,
                    "mesh": state.mesh,
                    "solver": state.solver,
                    "metrics": state.scientific_metrics,
                },
            )
            state.pipeline_checkpoint = extension_result.checkpoint.model_dump()
            state.extension_runs = [
                record.model_dump() for record in extension_result.extensions
            ]
            state.current_stage = PipelineStatus.EXTENDING_CAPABILITIES
            state.failure = PipelineFailure(
                failed_stage=PipelineStatus.EXTENDING_CAPABILITIES,
                failure_category="extension_pipeline_incomplete",
                message=(
                    "Mandatory OpenFOAM capabilities require generated and "
                    "validated extensions before case generation."
                ),
                internal_details={
                    "requirement_graph": graph.model_dump(),
                    "registry_health": health_report.model_dump(),
                    "missing_capabilities": mandatory_missing,
                    "pipeline_checkpoint": state.pipeline_checkpoint,
                    "extension_runs": state.extension_runs,
                    "next_stage": "EXTENSION_PIPELINE_EXECUTOR",
                },
                can_retry=True,
                requires_user_input=False,
            ).model_dump()
            state.current_stage = PipelineStatus.FAILED

    # ------------------------------------------------------------------
    # Stage 5: GENERATING_CASE  -- write real OpenFOAM case to disk
    # ------------------------------------------------------------------

    def _stage_generate_case(self, state: PipelineState) -> None:
        """Generate a real OpenFOAM case directory on disk."""
        import math
        cv = state.closed_parameters
        geo_family = state.raw_design.get("geometry", {}).get("family", "generic")
        cells = state.geometry.get("cells", {"nx": 100, "ny": 50, "nz": 1})
        solver_name = state.solver.get("name", "pimpleFoam")
        nu = state.materials.get("nu", 1.0 / 3900.0)
        rho = state.materials.get("rho", 1.0)
        dt = state.time_control.get("delta_t", 0.002)
        end_time = state.time_control.get("end_time", 20.0)
        write_int = state.time_control.get("write_interval", 100)
        is_steady = state.numerics.get("steady", False)
        turb_fam = state.physical_models.get("turbulence_family", "LES")
        turb_model = state.physical_models.get("turbulence_model", "WALE")

        # Build geometry for blockMesh
        if geo_family == "internal_flow":
            L = 20.0; H = 1.0; W = 3.14159
            vertices = [
                [0, 0, 0], [L, 0, 0], [L, H, 0], [0, H, 0],
                [0, 0, W], [L, 0, W], [L, H, W], [0, H, W],
            ]
            boundary_patches = {
                "inlet": {"type": "patch", "faces": [[0, 3, 7, 4]]},
                "outlet": {"type": "patch", "faces": [[1, 2, 6, 5]]},
                "wall": {"type": "wall", "faces": [[0, 1, 5, 4], [3, 2, 6, 7]]},
            }
            n_cells = [cells.get("nx", 200), cells.get("ny", 40), cells.get("nz", 40)]
        elif geo_family == "external_flow":
            x_up = 10.0; x_down = 25.0; y_half = 10.0; W = 3.14159
            # Rectangular background mesh for external flow.
            # Embedded surfaces (cylinder, airfoil, etc.) are added via
            # snappyHexMeshDict; they do not appear in blockMesh boundary.
            vertices = [
                [-x_up, -y_half, 0], [x_down, -y_half, 0], [x_down, y_half, 0], [-x_up, y_half, 0],
                [-x_up, -y_half, W], [x_down, -y_half, W], [x_down, y_half, W], [-x_up, y_half, W],
            ]
            boundary_patches = {
                "inlet": {"type": "patch", "faces": [[0, 3, 7, 4]]},
                "outlet": {"type": "patch", "faces": [[1, 2, 6, 5]]},
                "top": {"type": "symmetryPlane", "faces": [[3, 2, 6, 7]]},
                "bottom": {"type": "symmetryPlane", "faces": [[0, 1, 5, 4]]},
            }
            n_cells = [cells.get("nx", 200), cells.get("ny", 100), cells.get("nz", 40)]
        elif geo_family == "jet_impingement":
            L = 20.0; H = 10.0; W = 1.0
            vertices = [
                [0, 0, 0], [L, 0, 0], [L, H, 0], [0, H, 0],
                [0, 0, W], [L, 0, W], [L, H, W], [0, H, W],
            ]
            boundary_patches = {
                "inlet": {"type": "patch", "faces": [[0, 3, 7, 4]]},
                "outlet": {"type": "patch", "faces": [[1, 2, 6, 5]]},
                "target": {"type": "wall", "faces": [[0, 1, 5, 4]]},
                "top": {"type": "wall", "faces": [[3, 2, 6, 7]]},
            }
            n_cells = [cells.get("nx", 200), cells.get("ny", 80), 1]
        else:
            L = 20.0; H = 2.0; W = 1.0 if cells.get("nz", 1) == 1 else 3.14159
            vertices = [
                [0, 0, 0], [L, 0, 0], [L, H, 0], [0, H, 0],
                [0, 0, W], [L, 0, W], [L, H, W], [0, H, W],
            ]
            boundary_patches = {
                "inlet": {"type": "patch", "faces": [[0, 3, 7, 4]]},
                "outlet": {"type": "patch", "faces": [[1, 2, 6, 5]]},
                "top": {"type": "wall", "faces": [[3, 2, 6, 7]]},
                "bottom": {"type": "wall", "faces": [[0, 1, 5, 4]]},
            }
            n_cells = [cells.get("nx", 200), cells.get("ny", 60), cells.get("nz", 1)]

        # Add front/back
        is_2d = n_cells[2] == 1
        if is_2d:
            boundary_patches["front"] = {"type": "empty", "faces": [[0, 1, 2, 3]]}
            boundary_patches["back"] = {"type": "empty", "faces": [[4, 7, 6, 5]]}
        else:
            boundary_patches["front"] = {"type": "cyclic", "faces": [[0, 1, 2, 3]], "neighbourPatch": "back"}
            boundary_patches["back"] = {"type": "cyclic", "faces": [[4, 7, 6, 5]], "neighbourPatch": "front"}

        # Build controlDict functions from metrics
        functions: dict[str, Any] = {}
        fo_id = 0
        for m_list in (state.scientific_metrics, state.boundary_verification_metrics, state.credibility_metrics):
            for m in m_list:
                for fo in m.get("required_function_objects", []):
                    fo_name = fo.get("name", f"fo_{fo_id}")
                    fo_id += 1
                    fo_entry: dict[str, Any] = {
                        "type": fo.get("type", "residuals"),
                        "writeControl": "timeStep",
                        "writeInterval": write_int,
                    }
                    if "libs" in fo:
                        fo_entry["libs"] = fo["libs"]
                    if fo.get("patches"):
                        fo_entry["patches"] = fo["patches"]
                    if fo.get("fields"):
                        fo_entry["fields"] = fo["fields"]
                    if fo.get("configuration"):
                        fo_entry.update(fo["configuration"])
                    functions[fo_name] = fo_entry

        # Assemble case dict
        case_dict: dict[str, Any] = {
            "system": {
                "controlDict": {
                    "application": solver_name,
                    "startFrom": "startTime",
                    "startTime": 0,
                    "stopAt": "endTime",
                    "endTime": end_time if not is_steady else 10000,
                    "deltaT": dt if not is_steady else 1,
                    "writeControl": "timeStep",
                    "writeInterval": write_int,
                    "purgeWrite": 0,
                    "writeFormat": "ascii",
                    "writePrecision": 6,
                    "writeCompression": "off",
                    "timeFormat": "general",
                    "timePrecision": 6,
                    "runTimeModifiable": True,
                    "functions": functions,
                },
                "fvSchemes": {
                    "ddtSchemes": {"default": "steadyState" if is_steady else "Euler"},
                    "gradSchemes": {"default": "Gauss linear"},
                    "divSchemes": {"default": "Gauss linearUpwind grad(U)"},
                    "laplacianSchemes": {"default": "Gauss linear corrected"},
                    "interpolationSchemes": {"default": "linear"},
                },
                "fvSolution": {
                    "solvers": {
                        "p": {"solver": "GAMG", "tolerance": 1e-6, "relTol": 0.01},
                        "U": {"solver": "smoothSolver", "smoother": "symGaussSeidel", "tolerance": 1e-8, "relTol": 0.01},
                    },
                    "PIMPLE" if not is_steady else "SIMPLE": {
                        "momentumPredictor": True,
                        "nOuterCorrectors": 1 if is_steady else 2,
                        "nCorrectors": 2,
                        "nNonOrthogonalCorrectors": 0,
                    } if not is_steady else {
                        "nNonOrthogonalCorrectors": 0,
                        "consistent": True,
                        "residualControl": {"p": 1e-5, "U": 1e-5},
                    },
                    "relaxationFactors": {
                        "equations": {"U": 0.9 if not is_steady else 0.7},
                        "fields": {"p": 0.9 if not is_steady else 0.3},
                    },
                },
                "blockMeshDict": {
                    "convertToMeters": 1.0,
                    "vertices": vertices,
                    "blocks": [
                        {"hex": [0,1,2,3,4,5,6,7], "cells": n_cells, "grading": "simpleGrading", "ratios": [1,1,1]}
                    ],
                    "edges": [],
                    "boundary": boundary_patches,
                    "mergePatchPairs": [],
                },
            },
            "constant": {
                "transportProperties": {
                    "transportModel": "Newtonian",
                    "nu": float(nu),
                    "rho": float(rho),
                },
                "turbulenceProperties": self._turbulence_dict(turb_fam, turb_model),
            },
            "0": {
                "U": self._U_field(state, boundary_patches, is_2d, u_ref=1.0),
                "p": self._p_field(state, boundary_patches, is_2d),
            },
        }

        # Add snappyHexMeshDict for external flows with embedded surfaces
        embedded = state.raw_design.get("embedded_surface", {})
        if embedded.get("present") and geo_family == "external_flow":
            surface_name = embedded.get("name", "body")
            case_dict["system"]["snappyHexMeshDict"] = self._snappy_hex_dict(surface_name)

        # Write to disk
        case_dir = Path(state.session_dir) / "case"
        manifest = self._case_writer.write(
            case_dict,
            case_dir,
            session_id=state.session_id,
            draft_id=str(uuid.uuid4()),
            assumptions=state.closure_result.get("assumptions", []),
        )
        state.case_dir = str(case_dir)
        state.case_dict = case_dict
        state.case_manifest = manifest.model_dump()

    def _turbulence_dict(self, family: str, model: str) -> dict[str, Any]:
        if family == "laminar":
            return {"simulationType": "laminar"}
        if family == "LES":
            return {
                "simulationType": "LES",
                "LES": {"model": model, "turbulence": True},
            }
        return {
            "simulationType": "RAS",
            "RAS": {"model": model, "turbulence": True},
        }

    def _U_field(self, state: PipelineState, patches: dict[str, Any], is_2d: bool, u_ref: float = 1.0) -> dict[str, Any]:
        boundary_field: dict[str, Any] = {}
        for pname, pdata in patches.items():
            ptype = pdata.get("type", "patch")
            bc = state.boundary_conditions.get(pname, {})
            u_bc = bc.get("U", {})
            bct = u_bc.get("type", "zeroGradient")
            entry: dict[str, Any] = {"type": bct}
            if bct == "fixedValue":
                entry["value"] = {"uniform": [u_ref, 0.0, 0.0]}
            elif bct in ("cyclic",):
                entry["type"] = "cyclic"
                if "neighbourPatch" in pdata:
                    entry["neighbourPatch"] = pdata["neighbourPatch"]
            boundary_field[pname] = entry
        return {
            "dimensions": "[0 1 -1 0 0 0 0]",
            "internalField": {"uniform": [u_ref, 0.0, 0.0]},
            "boundaryField": boundary_field,
        }

    def _p_field(self, state: PipelineState, patches: dict[str, Any], is_2d: bool) -> dict[str, Any]:
        boundary_field: dict[str, Any] = {}
        for pname, pdata in patches.items():
            ptype = pdata.get("type", "patch")
            bc = state.boundary_conditions.get(pname, {})
            p_bc = bc.get("p", {})
            bct = p_bc.get("type", "zeroGradient")
            entry: dict[str, Any] = {"type": bct}
            if bct == "fixedValue":
                entry["value"] = {"uniform": 0.0}
            elif bct == "cyclic":
                entry["type"] = "cyclic"
                if "neighbourPatch" in pdata:
                    entry["neighbourPatch"] = pdata["neighbourPatch"]
            boundary_field[pname] = entry
        return {
            "dimensions": "[0 2 -2 0 0 0 0]",
            "internalField": {"uniform": 0.0},
            "boundaryField": boundary_field,
        }

    def _snappy_hex_dict(self, surface_name: str = "body") -> dict[str, Any]:
        """Generate a minimal snappyHexMeshDict that adds a searchable cylinder
        (representing the body) as a wall patch.  This dict is structurally
        valid so that static validation passes; full geometric fidelity
        (STL input, layer addition) can be refined later.
        """
        return {
            "castellatedMesh": True,
            "snap": True,
            "addLayers": False,
            "geometry": {
                surface_name: {
                    "type": "searchableCylinder",
                    "point1": [0, 0, 0],
                    "point2": [0, 0, 3.14159],
                    "radius": 0.5,
                }
            },
            "castellatedMeshControls": {
                "maxLocalCells": 1000000,
                "maxGlobalCells": 2000000,
                "minRefinementCells": 0,
                "maxLoadUnbalance": 0.10,
                "nCellsBetweenLevels": 3,
                "features": [],
                "refinementSurfaces": {
                    surface_name: {
                        "level": [2, 2],
                        "patchInfo": {"type": "wall", "name": surface_name},
                    }
                },
                "resolveFeatureAngle": 30,
                "refinementRegions": {},
                "locationInMesh": [5, 0, 1.57],
                "allowFreeStandingZoneFaces": True,
            },
            "snapControls": {
                "nSmoothPatch": 3,
                "tolerance": 2.0,
                "nSolveIter": 30,
                "nRelaxIter": 5,
                "nFeatureSnapIter": 10,
                "implicitFeatureSnap": False,
                "explicitFeatureSnap": True,
                "multiRegionFeatureSnap": False,
            },
            "addLayersControls": {
                "relativeSizes": True,
                "layers": {},
                "expansionRatio": 1.0,
                "finalLayerThickness": 0.3,
                "minThickness": 0.25,
                "nGrow": 0,
                "featureAngle": 30,
                "slipFeatureAngle": 30,
                "nRelaxIter": 3,
                "nSmoothSurfaceNormals": 1,
                "nSmoothNormals": 3,
                "nSmoothThickness": 10,
                "maxFaceThicknessRatio": 0.5,
                "maxThicknessToMedialRatio": 0.3,
                "minMedialAxisAngle": 90,
            },
            "meshQualityControls": {
                "maxNonOrtho": 65,
                "maxBoundarySkewness": 20,
                "maxInternalSkewness": 4,
                "maxConcave": 80,
                "minFlatness": 0.5,
                "minVol": 1e-13,
                "minTetQuality": 1e-30,
                "minArea": -1,
                "minTwist": 0.02,
                "minDeterminant": 0.001,
                "minFaceWeight": 0.05,
                "minVolRatio": 0.01,
                "minTriangleTwist": -1,
                "nSmoothScale": 4,
                "errorReduction": 0.75,
            },
            "mergeTolerance": 1e-6,
        }

    # ------------------------------------------------------------------
    # Stage 6: VALIDATING_CASE  -- run compile-readiness validation
    # ------------------------------------------------------------------

    def _stage_validate_case(self, state: PipelineState) -> None:
        """Run the CompileReadinessValidator on the generated case."""
        case_dir = state.case_dir
        if not case_dir:
            state.failure = PipelineFailure(
                failed_stage=PipelineStatus.GENERATING_CASE,
                failure_category="case_generation_failed",
                message="Case directory was not created.",
            ).model_dump()
            state.current_stage = PipelineStatus.FAILED
            return
        report = self._validator.validate(
            case_dir,
            case_dict=state.case_dict,
            design=state.closure_result,
            run_openfoam=True,
        )
        state.validation_report = report.model_dump()

        if not report.compile_ready:
            if not report.openfoam_available:
                # OpenFOAM is not installed in this environment.
                # Check if all non-runtime checks passed (static checks).
                static_errors = [
                    c for c in report.checks
                    if not c.passed and c.severity == "error" and c.check_name != "openfoam_runtime"
                ]
                if static_errors:
                    errors = [f"{c.check_name}: {c.message}" for c in static_errors]
                    state.failure = PipelineFailure(
                        failed_stage=PipelineStatus.VALIDATING_CASE,
                        failure_category="validation_failed",
                        message="; ".join(errors),
                        internal_details={"checks": [c.model_dump() for c in report.checks]},
                        can_retry=True,
                    ).model_dump()
                    state.current_stage = PipelineStatus.FAILED
                    return
                state.failure = PipelineFailure(
                    failed_stage=PipelineStatus.VALIDATING_CASE,
                    failure_category="validation_failed",
                    message=(
                        "OpenFOAM runtime was not found; mesh validation, "
                        "checkMesh and solver dry-run did not run."
                    ),
                    internal_details={"checks": [c.model_dump() for c in report.checks]},
                    can_retry=True,
                    requires_user_input=False,
                ).model_dump()
                state.current_stage = PipelineStatus.FAILED
                return
            else:
                # OpenFOAM was available but some check(s) failed.
                errors = [c.message for c in report.checks if not c.passed and c.severity == "error"]
                state.failure = PipelineFailure(
                    failed_stage=PipelineStatus.VALIDATING_CASE,
                    failure_category="validation_failed",
                    message="; ".join(errors) if errors else "Validation failed.",
                    internal_details={"checks": [c.model_dump() for c in report.checks]},
                    can_retry=True,
                ).model_dump()
                state.current_stage = PipelineStatus.FAILED
                return

    # ------------------------------------------------------------------
    # Build final CompileReadyDraftView
    # ------------------------------------------------------------------

    def _build_compile_ready_view(self, state: PipelineState) -> CompileReadyDraftView:
        cv = state.closed_parameters
        assumptions: list[dict[str, Any]] = []
        for a in state.closure_result.get("assumptions", []):
            assumptions.append(a)
        for key, cp in cv.items():
            if isinstance(cp, ClosedParameter) and cp.source in ("ASSUMED_BASELINE", "TEMPLATE_DEFAULT"):
                assumptions.append({"parameter": key, "value": str(cp.value), "reason": cp.reason})
        modifiable = [
            "Re", "U_ref", "L_ref", "mesh_resolution", "turbulence_model",
            "solver", "delta_t", "end_time", "sampling_frequency", "boundary_velocity",
        ]
        return CompileReadyDraftView(
            session_id=state.session_id,
            draft_id=state.case_manifest.get("draft_id", str(uuid.uuid4())),
            draft_version=1,
            status=PipelineStatus.COMPILE_READY,
            research_objective=state.scientific_intent.get("research_objective", ""),
            research_hypotheses=["Flow response governed by closed dimensionless parameters."],
            design=state.closure_result.get("resolved_values", {}),
            geometry=state.geometry,
            materials=state.materials,
            boundary_conditions=state.boundary_conditions,
            initial_conditions=state.initial_conditions if state.initial_conditions else {"U": [1,0,0], "p": 0},
            physical_models=state.physical_models,
            solver=state.solver,
            numerics={
                **state.numerics,
                "time_control": state.time_control,
                "pressure_velocity_coupling": "PIMPLE" if not state.numerics.get("steady") else "SIMPLE",
            },
            mesh=state.mesh,
            time_control=state.time_control,
            sampling=state.sampling,
            output_control=state.output_control,
            scientific_metrics=state.scientific_metrics,
            boundary_verification_metrics=state.boundary_verification_metrics,
            credibility_metrics=state.credibility_metrics,
            capabilities_used=state.capabilities_used,
            capabilities_extended=state.capabilities_extended,
            validation_results=state.validation_report,
            case_manifest=state.case_manifest,
            modifiable_fields=modifiable,
            assumptions=assumptions,
        )


__all__ = ["PipelineState", "V5WorkflowPipeline"]
