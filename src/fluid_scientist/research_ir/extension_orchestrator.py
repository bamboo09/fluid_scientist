"""Extension Orchestrator -- runs the full open-world pipeline.

Orchestrates the complete flow from free-form user text to compiled
geometry and frontend display data::

    extract -> plan representations -> process materials -> process
    boundaries -> review -> check coverage -> plan capabilities ->
    compile geometry -> build display

The orchestrator is *resilient*: every step is wrapped in error handling
so that a failure in one stage never short-circuits the pipeline.  All
blocking issues (from the semantic critic, source-coverage guard, and
capability planner) are collected and surfaced in the final
:class:`OrchestratorResult`.

When ``llm_client`` is ``None``, every LLM-dependent sub-component
automatically falls back to its deterministic rule-based implementation,
so the pipeline can run entirely offline.

Typical usage::

    from fluid_scientist.research_ir.extension_orchestrator import (
        ExtensionOrchestrator,
    )

    orchestrator = ExtensionOrchestrator()  # rule-based fallbacks
    result = orchestrator.run("2D flow past a cylinder at Re=100")
    if not result.success:
        for issue in result.blocking_issues:
            print(issue["code"], issue["message"])
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.research_ir.models import OpenWorldResearchIR
from fluid_scientist.research_ir.intent_extractor import OpenWorldIntentExtractor
from fluid_scientist.research_ir.representation_planner import RepresentationPlanner
from fluid_scientist.research_ir.semantic_critic import SemanticCritic
from fluid_scientist.research_ir.coverage import SourceCoverageGuard
from fluid_scientist.research_ir.capability_planner import CapabilityPlanner
from fluid_scientist.research_ir.geometry_compiler import PolygonGeometryCompiler
from fluid_scientist.research_ir.dynamic_schema import DynamicSchemaBuilder
from fluid_scientist.research_ir.intent_processors import MaterialProcessor, BoundaryProcessor
from fluid_scientist.research_ir.prompt_registry import PromptRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorResult:
    """Container for the full orchestration pipeline result.

    Attributes:
        ir: The final (enriched) :class:`OpenWorldResearchIR`.
        critic_result: Serialised output of the :class:`SemanticCritic`.
        coverage_report: Source-coverage report from
            :class:`SourceCoverageGuard`.
        capability_plan: Serialised :class:`CapabilityPlan` from the
            :class:`CapabilityPlanner`.
        compiled_geometry: List of compiled geometry entity dicts from
            :class:`PolygonGeometryCompiler`.
        display_data: Frontend-ready serialised IR from
            :class:`DynamicSchemaBuilder`.
        success: ``True`` when no blocking issues were collected.
        blocking_issues: All blocking issues combined from the critic,
            coverage guard, and capability planner.
        pipeline_log: Ordered log of each step's status and duration.
    """

    ir: OpenWorldResearchIR
    critic_result: dict = field(default_factory=dict)
    coverage_report: dict = field(default_factory=dict)
    capability_plan: dict = field(default_factory=dict)
    compiled_geometry: list[dict] = field(default_factory=list)
    display_data: dict = field(default_factory=dict)
    success: bool = False
    blocking_issues: list[dict] = field(default_factory=list)
    pipeline_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Full serialization to a JSON-compatible dictionary.

        The ``ir`` field is serialised via ``model_dump(mode="json")``
        so that the entire result can be passed to ``json.dumps`` without
        further conversion.
        """
        return {
            "ir": self.ir.model_dump(mode="json"),
            "critic_result": self.critic_result,
            "coverage_report": self.coverage_report,
            "capability_plan": self.capability_plan,
            "compiled_geometry": self.compiled_geometry,
            "display_data": self.display_data,
            "success": self.success,
            "blocking_issues": self.blocking_issues,
            "pipeline_log": self.pipeline_log,
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"OrchestratorResult(success={self.success}, "
            f"blocking={len(self.blocking_issues)}, "
            f"steps={len(self.pipeline_log)})"
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class ExtensionOrchestrator:
    """Orchestrates the full open-world extraction-to-geometry pipeline.

    The orchestrator wires together nine sub-components, each responsible
    for one stage of the pipeline:

    +---+----------------------+----------------------------------------+
    | # | Step                 | Component                              |
    +===+======================+========================================+
    | a | Extract              | :class:`OpenWorldIntentExtractor`      |
    | b | Plan representations | :class:`RepresentationPlanner`          |
    | c | Process materials    | :class:`MaterialProcessor`             |
    | d | Process boundaries   | :class:`BoundaryProcessor`             |
    | e | Review               | :class:`SemanticCritic`                |
    | f | Check coverage       | :class:`SourceCoverageGuard`            |
    | g | Plan capabilities    | :class:`CapabilityPlanner`             |
    | h | Compile geometry     | :class:`PolygonGeometryCompiler`       |
    | i | Build display        | :class:`DynamicSchemaBuilder`          |
    +---+----------------------+----------------------------------------+

    Every step runs even if a previous step failed; failures are logged
    and the pipeline continues with whatever partial state is available.
    Blocking issues are collected from steps (e), (f) and (g).

    Args:
        llm_client: Optional LLM client exposing a ``call(...)`` method.
            When ``None`` (default), every LLM-dependent sub-component
            falls back to its rule-based implementation.
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm_client = llm_client
        self._prompt_registry = PromptRegistry()

        # --- Sub-components (shared prompt registry + LLM client) ---
        self.intent_extractor = OpenWorldIntentExtractor(
            llm_client=llm_client,
            prompt_registry=self._prompt_registry,
        )
        self.representation_planner = RepresentationPlanner(
            llm_client=llm_client,
            prompt_registry=self._prompt_registry,
        )
        self.material_processor = MaterialProcessor()
        self.boundary_processor = BoundaryProcessor()
        self.semantic_critic = SemanticCritic(
            llm_client=llm_client,
            prompt_registry=self._prompt_registry,
        )
        self.source_coverage_guard = SourceCoverageGuard()
        self.capability_planner = CapabilityPlanner(
            llm_client=llm_client,
            prompt_registry=self._prompt_registry,
        )
        self.geometry_compiler = PolygonGeometryCompiler()
        self.schema_builder = DynamicSchemaBuilder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        user_text: str,
        session_id: str = "",
    ) -> OrchestratorResult:
        """Execute the full pipeline and return an :class:`OrchestratorResult`.

        The pipeline runs all nine steps in order.  Each step is timed
        and logged; exceptions are caught so that the pipeline never
        short-circuits.  Blocking issues are accumulated from the
        semantic critic, source-coverage guard, and capability planner.

        Args:
            user_text: The free-form research description supplied by
                the user.
            session_id: Optional session identifier forwarded to
                LLM-dependent sub-components for audit tracing.

        Returns:
            An :class:`OrchestratorResult` containing the enriched IR,
            all intermediate results, combined blocking issues, and a
            step-by-step pipeline log.
        """
        pipeline_log: list[dict] = []
        blocking_issues: list[dict] = []

        # --- Step a: Extract -------------------------------------------
        ir, log_a = self._run_step(
            "extract",
            lambda: self.intent_extractor.extract(user_text, session_id),
        )
        if ir is None:
            ir = self.intent_extractor._fallback_ir(
                user_text, "Orchestrator extract step failed"
            )
        log_a["entities"] = len(ir.geometry_entities)
        log_a["boundaries"] = len(ir.boundaries)
        log_a["materials"] = len(ir.materials)
        pipeline_log.append(log_a)

        # --- Step b: Plan representations ------------------------------
        result_b, log_b = self._run_step(
            "plan_representations",
            lambda: self.representation_planner.plan_all(ir),
        )
        if result_b is not None:
            ir = result_b
        log_b["entities"] = len(ir.geometry_entities)
        log_b["resolved"] = sum(
            1 for e in ir.geometry_entities
            if e.representation_status == "resolved"
        )
        log_b["needs_clarification"] = sum(
            1 for e in ir.geometry_entities
            if e.representation_status == "needs_clarification"
        )
        pipeline_log.append(log_b)

        # --- Step c: Process materials ---------------------------------
        result_c, log_c = self._run_step(
            "process_materials",
            lambda: self.material_processor.process(ir.materials),
        )
        if result_c is not None:
            ir.materials = result_c
        log_c["materials"] = len(ir.materials)
        log_c["supported"] = sum(
            1 for m in ir.materials
            if m.capability_status == "supported"
        )
        log_c["needs_properties"] = sum(
            1 for m in ir.materials
            if m.capability_status == "needs_properties"
        )
        pipeline_log.append(log_c)

        # --- Step d: Process boundaries --------------------------------
        dim = self._effective_dimensionality(ir)
        result_d, log_d = self._run_step(
            "process_boundaries",
            lambda: self.boundary_processor.process(ir.boundaries, dim),
        )
        if result_d is not None:
            ir.boundaries = result_d
        log_d["boundaries"] = len(ir.boundaries)
        log_d["resolved"] = sum(
            1 for b in ir.boundaries
            if b.semantic_status == "resolved"
        )
        log_d["needs_clarification"] = sum(
            1 for b in ir.boundaries
            if b.semantic_status == "needs_clarification"
        )
        pipeline_log.append(log_d)

        # --- Step e: Review --------------------------------------------
        critic_obj, log_e = self._run_step(
            "review",
            lambda: self.semantic_critic.review(ir, user_text),
        )
        if critic_obj is not None:
            critic_result: dict = critic_obj.to_dict()
        else:
            critic_result = {
                "passed": True,
                "blocking_issues": [],
                "warnings": [],
                "blocking_count": 0,
                "warning_count": 0,
            }
        # Collect blocking issues from the critic.
        for issue in critic_result.get("blocking_issues", []):
            issue_copy = dict(issue)
            issue_copy.setdefault("source", "critic")
            blocking_issues.append(issue_copy)
        log_e["blocking"] = critic_result.get("blocking_count", 0)
        log_e["warnings"] = critic_result.get("warning_count", 0)
        pipeline_log.append(log_e)

        # --- Step f: Check coverage -----------------------------------
        coverage_error, log_f = self._run_step(
            "check_coverage",
            lambda: self.source_coverage_guard.check(ir),
        )
        # Always attempt to build the coverage report, even if the
        # check itself threw.
        try:
            coverage_report: dict = self.source_coverage_guard.report(ir)
        except Exception:  # noqa: BLE001 - non-fatal
            coverage_report = {}
        # If the check succeeded and returned an error, it means
        # mentions are unaccounted for -> blocking.
        if log_f.get("status") == "ok" and coverage_error is not None:
            blocking_issues.append({
                "code": "COVERAGE_INCOMPLETE",
                "message": str(coverage_error),
                "field_path": "source_coverage.mention_inventory",
                "severity": "blocking",
                "source": "coverage",
                "unaccounted_count": len(coverage_error.unaccounted),
            })
        log_f["coverage_ratio"] = coverage_report.get(
            "coverage_ratio", 1.0
        )
        log_f["unaccounted"] = coverage_report.get("unaccounted", 0)
        pipeline_log.append(log_f)

        # --- Step g: Plan capabilities --------------------------------
        cap_plan_obj, log_g = self._run_step(
            "plan_capabilities",
            lambda: self.capability_planner.plan(ir),
        )
        if cap_plan_obj is not None:
            capability_plan: dict = cap_plan_obj.to_dict()
            # Collect blocking issues from missing capabilities whose
            # severity is "blocking".
            for mc in cap_plan_obj.missing:
                if mc.severity == "blocking":
                    blocking_issues.append({
                        "code": "MISSING_CAPABILITY",
                        "message": mc.description,
                        "field_path": mc.ir_reference,
                        "severity": "blocking",
                        "source": "capability",
                        "capability_id": mc.capability_id,
                        "category": mc.category,
                        "extension_plan": mc.extension_plan,
                    })
        else:
            capability_plan = {
                "supported": [],
                "missing": [],
                "needs_clarification": [],
                "is_blocked": False,
            }
        log_g["supported"] = len(capability_plan.get("supported", []))
        log_g["missing"] = len(capability_plan.get("missing", []))
        log_g["blocked"] = capability_plan.get("is_blocked", False)
        pipeline_log.append(log_g)

        # --- Step h: Compile geometry ----------------------------------
        compiled, log_h = self._run_step(
            "compile_geometry",
            lambda: self.geometry_compiler.compile_all(ir),
        )
        if compiled is None:
            compiled: list[dict] = []
        log_h["entities"] = len(compiled)
        log_h["compiled"] = sum(
            1 for e in compiled if e.get("status") == "compiled"
        )
        log_h["errors"] = sum(
            1 for e in compiled
            if e.get("status") in ("compile_error", "needs_clarification")
        )
        pipeline_log.append(log_h)

        # --- Step i: Build display -------------------------------------
        display, log_i = self._run_step(
            "build_display",
            lambda: self.schema_builder.serialize_ir_for_display(ir),
        )
        if display is None:
            display: dict = {}
        log_i["sections"] = len(display)
        pipeline_log.append(log_i)

        # --- Assemble result -------------------------------------------
        success = len(blocking_issues) == 0

        logger.info(
            "Pipeline complete: success=%s, blocking=%d, steps=%d",
            success,
            len(blocking_issues),
            len(pipeline_log),
        )

        return OrchestratorResult(
            ir=ir,
            critic_result=critic_result,
            coverage_report=coverage_report,
            capability_plan=capability_plan,
            compiled_geometry=compiled,
            display_data=display,
            success=success,
            blocking_issues=blocking_issues,
            pipeline_log=pipeline_log,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_step(
        step_name: str,
        fn: Any,
    ) -> tuple[Any, dict]:
        """Execute *fn*, timing it and catching any exception.

        Returns a ``(result, log_entry)`` tuple.  On success the log
        entry has ``status="ok"``; on failure it has ``status="error"``
        and an ``error`` key with the exception message.

        Args:
            step_name: Human-readable name for the pipeline log.
            fn: A zero-argument callable to execute.

        Returns:
            A tuple of ``(result_or_None, log_dict)``.
        """
        start = time.perf_counter()
        try:
            result = fn()
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            return result, {
                "step": step_name,
                "status": "ok",
                "duration_ms": duration_ms,
            }
        except Exception as exc:  # noqa: BLE001 - pipeline must not crash
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.error(
                "Pipeline step '%s' failed: %s",
                step_name,
                exc,
                exc_info=True,
            )
            return None, {
                "step": step_name,
                "status": "error",
                "duration_ms": duration_ms,
                "error": str(exc),
            }

    @staticmethod
    def _effective_dimensionality(
        ir: OpenWorldResearchIR,
    ) -> str:
        """Return the effective dimensionality from the IR or its domain.

        Falls back to ``ir.domain.dimensionality`` when the top-level
        ``ir.dimensionality`` is ``"unknown"``.
        """
        dim = ir.dimensionality
        if dim in ("2D", "3D", "axisymmetric"):
            return dim
        if ir.domain is not None and ir.domain.dimensionality in (
            "2D",
            "3D",
            "axisymmetric",
        ):
            return ir.domain.dimensionality
        return "unknown"


__all__ = ["ExtensionOrchestrator", "OrchestratorResult"]
