"""Error diagnosis and repair integration for the open-world research IR.

This module bridges the :mod:`fluid_scientist.repair` package (which provides
the full repair-loop orchestrator and LLM-based diagnosis) with the research
IR pipeline.  It exposes a lightweight, LLM-free :class:`OpenFOAMErrorDiagnoser`
that classifies OpenFOAM error logs into semantic categories so that the
extension orchestrator can decide whether a blocking failure can be
auto-repaired or requires user clarification.

The :class:`RepairOrchestrator` is re-exported from
:mod:`fluid_scientist.repair.repair_orchestrator` for convenience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.repair.repair_orchestrator import RepairOrchestrator


# ---------------------------------------------------------------------------
# Diagnosis result
# ---------------------------------------------------------------------------

# Valid category strings.
MESH_ERROR = "MESH_ERROR"
BOUNDARY_CONDITION_ERROR = "BOUNDARY_CONDITION_ERROR"
SOLVER_ERROR = "SOLVER_ERROR"
PHYSICS_ERROR = "PHYSICS_ERROR"
UNKNOWN_ERROR = "UNKNOWN_ERROR"

_VALID_CATEGORIES = frozenset(
    {MESH_ERROR, BOUNDARY_CONDITION_ERROR, SOLVER_ERROR, PHYSICS_ERROR, UNKNOWN_ERROR}
)

# Suggested fixes keyed by category.
_SUGGESTED_FIXES: dict[str, str] = {
    MESH_ERROR: (
        "Check blockMeshDict vertices, blocks, and boundary patch names; "
        "run checkMesh for quality metrics."
    ),
    BOUNDARY_CONDITION_ERROR: (
        "Verify boundary patch names in 0/ folder match constant/polyMesh/boundary; "
        "check that each patch has a valid type and value."
    ),
    SOLVER_ERROR: (
        "Check solver configuration, memory allocation, and mesh quality; "
        "consider switching linear solver or preconditioner."
    ),
    PHYSICS_ERROR: (
        "Reduce time step or enable adjustTimeStep with maxCo=0.5; "
        "check initial/boundary conditions for consistency."
    ),
    UNKNOWN_ERROR: (
        "Inspect the full solver log for additional context; "
        "check case setup, mesh, and physics configuration."
    ),
}


@dataclass
class ErrorDiagnosis:
    """Structured result of diagnosing an OpenFOAM error log.

    Attributes:
        category: Semantic error category (one of the ``*_ERROR`` constants
            defined in this module).
        severity: ``"blocking"`` when the error halts the pipeline,
            ``"warning"`` when the run can potentially continue.
        message: Human-readable description of the detected problem.
        raw_log: The original error log text (may be truncated for storage).
        suggested_fix: Optional recommended action to resolve the error.
        matched_pattern: The keyword or pattern that triggered the diagnosis.
    """

    category: str = UNKNOWN_ERROR
    severity: str = "blocking"
    message: str = ""
    raw_log: str = ""
    suggested_fix: str | None = None
    matched_pattern: str | None = None

    @property
    def is_blocking(self) -> bool:
        """``True`` when *severity* is ``"blocking"``."""
        return self.severity == "blocking"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary (for logging / JSON output)."""
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "raw_log": self.raw_log[:500] if self.raw_log else "",
            "suggested_fix": self.suggested_fix,
            "matched_pattern": self.matched_pattern,
            "is_blocking": self.is_blocking,
        }


# ---------------------------------------------------------------------------
# Diagnoser
# ---------------------------------------------------------------------------

# Keyword rules evaluated in priority order.  Each entry is
# (keyword_test, category, severity, message_template).
#
# Order matters: earlier rules take precedence.  For example, a NaN check
# must fire *before* the generic "FOAM FATAL ERROR" rule so that NaN-related
# physics failures are classified as PHYSICS_ERROR (blocking) rather than
# UNKNOWN_ERROR.
_RULES: list[tuple[Any, str, str, str]] = [
    # -- Solver-level crashes -------------------------------------------
    (
        lambda log: "segmentation fault" in log or "segfault" in log,
        SOLVER_ERROR,
        "blocking",
        "Segmentation fault detected during solver execution",
    ),
    # -- Physics-level failures: NaN (blocking) -------------------------
    # Word-boundary match avoids false positives (e.g. "channel").
    (
        re.compile(r"\bnan\b", re.IGNORECASE).search,
        PHYSICS_ERROR,
        "blocking",
        "NaN values detected in solution field",
    ),
    # -- Physics-level warnings: Courant number -------------------------
    (
        lambda log: "courant number" in log or "courant" in log,
        PHYSICS_ERROR,
        "warning",
        "Courant number exceeds recommended limit",
    ),
    # -- Boundary condition errors -------------------------------------
    (
        lambda log: "boundary condition" in log,
        BOUNDARY_CONDITION_ERROR,
        "blocking",
        "Boundary condition error detected",
    ),
    # -- Mesh errors: FOAM FATAL ERROR + mesh keyword -------------------
    (
        lambda log: "foam fatal error" in log and "mesh" in log,
        MESH_ERROR,
        "blocking",
        "Mesh generation or validation error",
    ),
]


class OpenFOAMErrorDiagnoser:
    """Diagnose OpenFOAM error logs without requiring an LLM client.

    The diagnoser applies a cascade of keyword-based rules to classify
    error logs into semantic categories (:data:`MESH_ERROR`,
    :data:`BOUNDARY_CONDITION_ERROR`, :data:`SOLVER_ERROR`,
    :data:`PHYSICS_ERROR`, or :data:`UNKNOWN_ERROR`).  Each category
    carries a severity (``"blocking"`` or ``"warning"``) that the caller
    can use to decide whether to halt the pipeline or issue a warning.

    Example::

        diagnoser = OpenFOAMErrorDiagnoser()
        diagnosis = diagnoser.diagnose("FOAM FATAL ERROR: mesh not valid")
        assert diagnosis.category == "MESH_ERROR"
        assert diagnosis.is_blocking
    """

    def __init__(self, llm_client: Any = None) -> None:
        """Initialise the diagnoser.

        Args:
            llm_client: Optional LLM client for enhanced diagnosis.
                When ``None`` (the default) only rule-based diagnosis
                is performed, making the diagnoser fully deterministic.
        """
        self._llm_client = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def diagnose(self, error_log: str) -> ErrorDiagnosis:
        """Classify *error_log* into an :class:`ErrorDiagnosis`.

        Args:
            error_log: The raw OpenFOAM error log text.

        Returns:
            An :class:`ErrorDiagnosis` with the best-matching category,
            severity, and suggested fix.
        """
        log_lower = error_log.lower()

        for rule_test, category, severity, message in _RULES:
            matched = self._apply_rule(rule_test, log_lower)
            if matched:
                return ErrorDiagnosis(
                    category=category,
                    severity=severity,
                    message=message,
                    raw_log=error_log,
                    suggested_fix=_SUGGESTED_FIXES.get(category),
                    matched_pattern=matched,
                )

        # No rule matched → unknown error.
        return ErrorDiagnosis(
            category=UNKNOWN_ERROR,
            severity="blocking",
            message="Unrecognised error — no diagnostic pattern matched",
            raw_log=error_log,
            suggested_fix=_SUGGESTED_FIXES.get(UNKNOWN_ERROR),
            matched_pattern=None,
        )

    def diagnose_batch(self, error_logs: list[str]) -> list[ErrorDiagnosis]:
        """Diagnose multiple error logs."""
        return [self.diagnose(log) for log in error_logs]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_rule(
        rule: Any,
        log_lower: str,
    ) -> str | None:
        """Apply a single rule and return the matched substring or ``None``."""
        if isinstance(rule, re.Pattern):
            match = rule(log_lower)
            if match is not None:
                return match.group()
            return None
        if callable(rule):
            if rule(log_lower):
                return "keyword_match"
            return None
        return None


__all__ = [
    "OpenFOAMErrorDiagnoser",
    "ErrorDiagnosis",
    "RepairOrchestrator",
    # Category constants
    "MESH_ERROR",
    "BOUNDARY_CONDITION_ERROR",
    "SOLVER_ERROR",
    "PHYSICS_ERROR",
    "UNKNOWN_ERROR",
]
