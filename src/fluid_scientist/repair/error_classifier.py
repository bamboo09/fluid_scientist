"""Error classification for OpenFOAM execution failures.

Classifies errors into categories for the repair loop:
- MESH_ERROR: blockMesh/snappyHexMesh/checkMesh failures
- BOUNDARY_CONDITION_ERROR: Incorrect boundary types or missing patches
- SOLVER_ERROR: Numerical instability, NaN, divergence
- PHYSICS_ERROR: CFL violation, time step too large
- FILE_ERROR: Missing files, permission errors
- SYNTAX_ERROR: Malformed OpenFOAM dictionaries
- MEMORY_ERROR: Out of memory
- TIMEOUT_ERROR: Execution timeout
- UNKNOWN_ERROR: Unclassified errors
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    MESH_ERROR = "mesh_error"
    BOUNDARY_CONDITION_ERROR = "boundary_condition_error"
    SOLVER_ERROR = "solver_error"
    PHYSICS_ERROR = "physics_error"
    FILE_ERROR = "file_error"
    SYNTAX_ERROR = "syntax_error"
    MEMORY_ERROR = "memory_error"
    TIMEOUT_ERROR = "timeout_error"
    UNKNOWN_ERROR = "unknown_error"


class ErrorSeverity(str, Enum):
    FATAL = "fatal"           # Cannot proceed, needs repair
    RECOVERABLE = "recoverable"  # Can potentially be fixed automatically
    WARNING = "warning"       # Non-fatal, but should be addressed


@dataclass
class ClassifiedError:
    """A classified OpenFOAM error."""
    category: ErrorCategory
    severity: ErrorSeverity
    error_message: str
    raw_log: str
    line_number: int | None = None
    file_path: str | None = None
    suggested_fix: str | None = None
    is_repairable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "severity": self.severity.value,
            "error_message": self.error_message,
            "raw_log": self.raw_log[:500],
            "line_number": self.line_number,
            "file_path": self.file_path,
            "suggested_fix": self.suggested_fix,
            "is_repairable": self.is_repairable,
        }


# Error patterns for classification
_ERROR_PATTERNS: list[tuple[re.Pattern, ErrorCategory, ErrorSeverity, str]] = [
    # Mesh errors
    (re.compile(r"blockMesh.*error|blockMesh.*failed|cannot find patch", re.IGNORECASE),
     ErrorCategory.MESH_ERROR, ErrorSeverity.FATAL, "Check blockMeshDict patch names and vertices"),
    (re.compile(r"snappyHexMesh.*error|snappyHexMesh.*failed", re.IGNORECASE),
     ErrorCategory.MESH_ERROR, ErrorSeverity.FATAL, "Check snappyHexMeshDict geometry and refinement"),
    (re.compile(r"checkMesh.*error|mesh.*not.*valid|cells.*not.*closed", re.IGNORECASE),
     ErrorCategory.MESH_ERROR, ErrorSeverity.FATAL, "Check mesh quality and topology"),

    # Boundary condition errors
    (re.compile(r"boundary.*condition.*error|patch.*not.*found|unknown.*patch", re.IGNORECASE),
     ErrorCategory.BOUNDARY_CONDITION_ERROR, ErrorSeverity.FATAL, "Verify boundary patch names match between files"),
    (re.compile(r"FOAM FATAL IO ERROR.*boundary|inletOutlet.*not.*found", re.IGNORECASE),
     ErrorCategory.BOUNDARY_CONDITION_ERROR, ErrorSeverity.FATAL, "Check boundary condition type spelling"),

    # Solver errors
    (re.compile(r"NaN|not a number|infinit", re.IGNORECASE),
     ErrorCategory.SOLVER_ERROR, ErrorSeverity.FATAL, "Reduce time step or improve mesh quality"),
    (re.compile(r"divergence detected|solution diverging", re.IGNORECASE),
     ErrorCategory.SOLVER_ERROR, ErrorSeverity.FATAL, "Reduce time step, check boundary conditions"),
    (re.compile(r"FOAM FATAL ERROR.*solver|unknown.*solver", re.IGNORECASE),
     ErrorCategory.SOLVER_ERROR, ErrorSeverity.FATAL, "Check solver name and availability"),

    # Physics errors
    (re.compile(r"Courant.*exceed|CFL.*exceed|maximum.*Courant", re.IGNORECASE),
     ErrorCategory.PHYSICS_ERROR, ErrorSeverity.RECOVERABLE, "Reduce deltaT or enable adjustTimeStep"),
    (re.compile(r"time step.*too.*large|deltaT.*too.*large", re.IGNORECASE),
     ErrorCategory.PHYSICS_ERROR, ErrorSeverity.RECOVERABLE, "Reduce deltaT"),

    # File errors
    (re.compile(r"cannot.*open.*file|file.*not.*found|No such file", re.IGNORECASE),
     ErrorCategory.FILE_ERROR, ErrorSeverity.FATAL, "Verify file paths in case directory"),
    (re.compile(r"permission.*denied|cannot.*write", re.IGNORECASE),
     ErrorCategory.FILE_ERROR, ErrorSeverity.FATAL, "Check file permissions"),

    # Syntax errors
    (re.compile(r"parse.*error|syntax.*error|unexpected.*token|malformed", re.IGNORECASE),
     ErrorCategory.SYNTAX_ERROR, ErrorSeverity.FATAL, "Check OpenFOAM dictionary syntax"),
    (re.compile(r"FOAM FATAL IO ERROR.*parse|keyword.*not.*found", re.IGNORECASE),
     ErrorCategory.SYNTAX_ERROR, ErrorSeverity.FATAL, "Check dictionary keywords and syntax"),

    # Memory errors
    (re.compile(r"out.*of.*memory|Cannot.*allocate|bad_alloc", re.IGNORECASE),
     ErrorCategory.MEMORY_ERROR, ErrorSeverity.FATAL, "Reduce mesh size or use parallel decomposition"),

    # Timeout errors
    (re.compile(r"timeout|timed.*out|killed.*signal.*9", re.IGNORECASE),
     ErrorCategory.TIMEOUT_ERROR, ErrorSeverity.FATAL, "Increase timeout or reduce problem size"),
]


class OpenFOAMErrorClassifier:
    """Classifies OpenFOAM error logs into categories for repair."""

    # Categories that are potentially repairable by LLM
    REPAIRABLE_CATEGORIES = {
        ErrorCategory.BOUNDARY_CONDITION_ERROR,
        ErrorCategory.PHYSICS_ERROR,
        ErrorCategory.SYNTAX_ERROR,
        ErrorCategory.MESH_ERROR,
    }

    def classify(self, log: str, stage: str = "unknown") -> list[ClassifiedError]:
        """Classify errors from an OpenFOAM log.

        Args:
            log: The raw log output (stdout + stderr)
            stage: Which stage produced this log (mesh, smoke, full_run)

        Returns:
            List of classified errors found in the log
        """
        errors: list[ClassifiedError] = []
        lines = log.split("\n")

        # First check for FOAM FATAL ERROR / FOAM FATAL IO ERROR
        for i, line in enumerate(lines):
            if "FOAM FATAL ERROR" in line or "FOAM FATAL IO ERROR" in line:
                # Extract context (current line + next 10 lines)
                context_start = max(0, i - 2)
                context_end = min(len(lines), i + 12)
                context = "\n".join(lines[context_start:context_end])

                # Try to extract file and line number
                file_match = re.search(r"file:\s*(.+?)(?:\s|$)", context)
                line_match = re.search(r"line:\s*(\d+)", context)

                # Classify based on context
                category = self._classify_context(context)
                severity = ErrorSeverity.FATAL
                suggested = self._suggest_fix(category, context)

                errors.append(ClassifiedError(
                    category=category,
                    severity=severity,
                    error_message=lines[i].strip(),
                    raw_log=context,
                    line_number=int(line_match.group(1)) if line_match else None,
                    file_path=file_match.group(1).strip() if file_match else None,
                    suggested_fix=suggested,
                    is_repairable=category in self.REPAIRABLE_CATEGORIES,
                ))

        # Check for NaN even without FATAL ERROR
        if not any(e.category == ErrorCategory.SOLVER_ERROR for e in errors):
            if re.search(r"\bNaN\b", log, re.IGNORECASE):
                errors.append(ClassifiedError(
                    category=ErrorCategory.SOLVER_ERROR,
                    severity=ErrorSeverity.FATAL,
                    error_message="NaN detected in solution",
                    raw_log=log[-500:] if len(log) > 500 else log,
                    suggested_fix="Reduce time step, check boundary conditions, improve mesh quality",
                    is_repairable=True,
                ))

        # Check for Courant number issues
        courant_matches = re.findall(r"Courant Number mean:\s*([\d.]+)\s*max:\s*([\d.]+)", log)
        for mean_str, max_str in courant_matches:
            try:
                max_courant = float(max_str)
                if max_courant > 1.0:
                    errors.append(ClassifiedError(
                        category=ErrorCategory.PHYSICS_ERROR,
                        severity=ErrorSeverity.RECOVERABLE,
                        error_message=f"Courant number too high: max={max_courant}",
                        raw_log=f"Courant Number mean: {mean_str} max: {max_str}",
                        suggested_fix="Reduce deltaT or enable adjustTimeStep with maxCo=0.5",
                        is_repairable=True,
                    ))
            except ValueError:
                pass

        # Check for return code if no errors found
        if not errors and "Return code:" in log:
            rc_match = re.search(r"Return code:\s*(\d+)", log)
            if rc_match and int(rc_match.group(1)) != 0:
                errors.append(ClassifiedError(
                    category=ErrorCategory.UNKNOWN_ERROR,
                    severity=ErrorSeverity.FATAL,
                    error_message=f"Process exited with code {rc_match.group(1)}",
                    raw_log=log[-500:] if len(log) > 500 else log,
                    is_repairable=False,
                ))

        return errors

    def _classify_context(self, context: str) -> ErrorCategory:
        """Classify error based on surrounding context."""
        context_lower = context.lower()

        for pattern, category, _, _ in _ERROR_PATTERNS:
            if pattern.search(context):
                return category

        # Default classification based on keywords
        if "boundary" in context_lower or "patch" in context_lower:
            return ErrorCategory.BOUNDARY_CONDITION_ERROR
        if "mesh" in context_lower or "cell" in context_lower:
            return ErrorCategory.MESH_ERROR
        if "courant" in context_lower or "timestep" in context_lower:
            return ErrorCategory.PHYSICS_ERROR

        return ErrorCategory.UNKNOWN_ERROR

    def _suggest_fix(self, category: ErrorCategory, context: str) -> str:
        """Suggest a fix based on error category and context."""
        suggestions = {
            ErrorCategory.MESH_ERROR: "Check blockMeshDict vertices, blocks, and boundary patch names. Verify snappyHexMeshDict geometry files exist.",
            ErrorCategory.BOUNDARY_CONDITION_ERROR: "Verify boundary patch names in 0/ folder match those in constant/polyMesh/boundary. Check boundary condition type spelling.",
            ErrorCategory.SOLVER_ERROR: "Reduce deltaT, check physical properties (nu, rho), verify solver compatibility with turbulence model.",
            ErrorCategory.PHYSICS_ERROR: "Reduce deltaT or enable adjustTimeStep with maxCo=0.5. Check if flow regime (laminar/turbulent) matches Reynolds number.",
            ErrorCategory.FILE_ERROR: "Verify all required files exist in the case directory (0/, constant/, system/).",
            ErrorCategory.SYNTAX_ERROR: "Check OpenFOAM dictionary syntax: semicolons, braces, keyword spelling.",
            ErrorCategory.MEMORY_ERROR: "Reduce mesh cell count or use parallel decomposition (decomposePar).",
            ErrorCategory.TIMEOUT_ERROR: "Increase timeout or reduce simulation end time.",
            ErrorCategory.UNKNOWN_ERROR: "Check the full log for more details.",
        }
        return suggestions.get(category, "Check the full log for more details.")

    def get_primary_error(self, errors: list[ClassifiedError]) -> ClassifiedError | None:
        """Get the most important error from a list."""
        if not errors:
            return None
        # Fatal errors first, then by category priority
        fatal_errors = [e for e in errors if e.severity == ErrorSeverity.FATAL]
        if fatal_errors:
            # Prefer repairable errors
            repairable = [e for e in fatal_errors if e.is_repairable]
            if repairable:
                return repairable[0]
            return fatal_errors[0]
        return errors[0]
