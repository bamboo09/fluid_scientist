"""Tests for the error-classification (P3) and controlled-repair loop (P4).

This suite exercises the repair subsystem end-to-end *without* a running
OpenFOAM installation or a live LLM/SSH server.  It covers:

* P3 — ``OpenFOAMErrorClassifier`` classifying mock OpenFOAM error logs
  (mesh / boundary-condition / solver / physics / syntax errors), the
  ``ClassifiedError`` severity & repairability flags, and
  ``get_primary_error`` prioritisation.
* P4 — ``RepairPolicy`` level progression (CONFIG_ONLY ->
  DICTIONARY_SYNTAX -> PARTIAL_REGENERATION), per-phase freezing after
  ``max_attempts_per_phase=3`` attempts, the global limit
  ``max_global_attempts=10``, and the ``can_attempt`` / ``record_attempt`` /
  ``get_repair_level`` API.
* P4 — ``RepairContextBuilder`` assembling diagnosis context from an error,
  a (duck-typed) CaseSpec, file contents and previous attempts.
* P4 — ``RepairOrchestrator`` full flow: classify -> build context ->
  diagnose -> apply -> validate, including the success, phase-frozen and
  global-limit branches.

Scenario anchors referenced by the closed-loop design:

* **Test F** — a boundary *patch mismatch* error (``LOG_PATCH_MISMATCH_TEST_F``).
* **Test G** — *numerical divergence* (NaN) during the full run
  (``LOG_NAN_STANDALONE_TEST_G``).

A central invariant asserted throughout is that **RETRY_WITHOUT_REPAIR is
never allowed**: every retry of a failed stage must be preceded by a recorded
repair attempt with a documented ``fix_applied``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure the project ``src`` tree wins on sys.path (matches the e2e conftest
# defensive pattern) so the tests do not depend on an editable install.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fluid_scientist.repair.controlled_repair_executor import RepairResult
from fluid_scientist.repair.error_classifier import (
    ClassifiedError,
    ErrorCategory,
    ErrorSeverity,
    OpenFOAMErrorClassifier,
)
from fluid_scientist.repair.repair_context_builder import RepairContextBuilder
from fluid_scientist.repair.repair_orchestrator import RepairOrchestrator
from fluid_scientist.repair.repair_policy import (
    RepairAttempt,
    RepairLevel,
    RepairPhase,
    RepairPolicy,
    RepairStatus,
)


# ---------------------------------------------------------------------------
# Mock OpenFOAM error logs (modelled on real OpenFOAM stderr)
# ---------------------------------------------------------------------------

# Mesh error — blockMesh failure.  Carries file/line metadata.
LOG_BLOCKMESH_FAIL = """\
Create mesh
--> FOAM FATAL ERROR
blockMesh failed to create mesh
    From function blockMesh::createMesh(...)
    file: mesh/blockMesh/blockMesh.C  line: 124
"""

# Test F — boundary-condition error: a patch present in 0/U is missing from
# the polyMesh/boundary dictionary.
LOG_PATCH_MISMATCH_TEST_F = """\
--> FOAM FATAL IO ERROR
patch 'outlet' not found in 0/U
    file: fields/U  line: 200
"""

# Test G — numerical divergence manifesting as NaN (no FOAM FATAL ERROR line).
LOG_NAN_STANDALONE_TEST_G = """\
Time = 0.05
 Courant Number mean: 0.4 max: 0.9
NaN detected in solution at cell 1234
Return code: 1
"""

# Solver error — divergence reported inside a FOAM FATAL ERROR block.
LOG_DIVERGENCE_FATAL = """\
--> FOAM FATAL ERROR
divergence detected in pEqn
    file: finiteVolume/solvers/pEqn.C  line: 88
"""

# Physics error — Courant number above the stable limit (>1.0).
LOG_COURANT_HIGH = """\
Time = 0.01
 Courant Number mean: 0.6 max: 2.75
"""

# Syntax error — malformed dictionary (parse error).
LOG_SYNTAX_MALFORMED = """\
--> FOAM FATAL IO ERROR
parse error in dictionary system/controlDict
    file: system/controlDict  line: 42
"""

# File error — missing case file (not repairable by the repair loop).
LOG_FILE_NOT_FOUND = """\
--> FOAM FATAL ERROR
cannot open file 0/U: No such file or directory
    file: db/registry  line: 305
"""

# Memory error — out of memory (not repairable).
LOG_MEMORY = """\
--> FOAM FATAL ERROR
out of memory: Cannot allocate 4096 bytes (bad_alloc)
    file: memory/alloc  line: 12
"""

# Timeout error — execution timed out (not repairable).
LOG_TIMEOUT = """\
--> FOAM FATAL ERROR
blockMesh timed out after 600 seconds
    file: system/controlDict  line: 1
"""


# ---------------------------------------------------------------------------
# Duck-typed test doubles
# ---------------------------------------------------------------------------


class _Resolved:
    """Stand-in for a resolved spec field.

    ``RepairContextBuilder._summarize_spec`` reads ``.value`` (attribute) and
    calls ``.is_resolved()`` (method) on quantity-like fields such as
    ``domain.length_m``.
    """

    def __init__(self, value: object, resolved: bool = True) -> None:
        self.value = value
        self._resolved = resolved

    def is_resolved(self) -> bool:
        return self._resolved


class _FakeLLMRecord:
    """Mimics the ``record`` object returned by the real LLM client."""

    def __init__(self, success: bool = True, error: str | None = None) -> None:
        self.success = success
        self.error = error


_DEFAULT_FIXES = [
    {
        "file": "system/controlDict",
        "parameter": "deltaT",
        "old_value": "0.01",
        "new_value": "0.001",
        "reason": "Courant number too high, reduce time step",
    }
]


class _FakeLLMClient:
    """Mimics the ``client.call(...)`` contract used by ``LLMDiagnoser``.

    Returns a diagnosis dict whose ``fixes`` list is configurable so the
    repair loop can exercise both the "fixes suggested" and "no fixes"
    branches without a real LLM.
    """

    def __init__(
        self,
        fixes: list[dict] | None = None,
        success: bool = True,
        root_cause: str = "diagnosed root cause",
    ) -> None:
        self._fixes = _DEFAULT_FIXES if fixes is None else fixes
        self._success = success
        self._root_cause = root_cause
        self.call_count = 0

    def call(self, **kwargs):  # noqa: ANN003 - matches flexible client API
        self.call_count += 1
        parsed = {
            "root_cause": self._root_cause,
            "error_category": "physics_error",
            "fix_strategy": "config_only",
            "fixes": self._fixes,
            "confidence": 0.8,
            "warnings": [],
        }
        return parsed, _FakeLLMRecord(success=self._success)


class _FakeExecutorImpl:
    """Stand-in for ``ControlledRepairExecutor`` used to drive the success
    branch of ``RepairOrchestrator``.

    The real executor can never set ``retry_passed=True`` without a live
    workstation, so this fake records an attempt with a configurable outcome
    and returns a controlled ``RepairResult``.  This isolates the
    orchestrator's classify -> context -> diagnose -> apply -> validate
    wiring while keeping the classifier, context-builder and diagnoser real.
    """

    def __init__(self, retry_passed: bool = True) -> None:
        self.retry_passed = retry_passed
        self.call_count = 0

    def execute_repair(  # noqa: PLR0913 - matches real signature
        self,
        diagnosis: dict,
        context: dict,
        policy: RepairPolicy,
        phase: RepairPhase,
        case_path: str = "",
        remote_case_path: str = "",
    ) -> RepairResult:
        self.call_count += 1
        attempt = RepairAttempt(
            attempt_number=policy.current_global_attempts + 1,
            phase=phase,
            level=policy.get_repair_level(
                phase, policy.phase_attempts.get(phase.value, 0) + 1
            ),
            error_summary=context.get("error", {}).get("error_message", "unknown"),
            fix_applied="deltaT 0.01->0.001 in system/controlDict",
            retry_passed=self.retry_passed,
        )
        status = policy.record_attempt(attempt)
        return RepairResult(
            success=self.retry_passed,
            status=status,
            diagnosis=diagnosis,
            fixes_applied=_DEFAULT_FIXES,
            files_modified=["system/controlDict"],
            validation_passed=True,
            retry_passed=self.retry_passed,
            attempt=attempt,
        )


def _make_spec(*, with_obstacles: bool = False) -> SimpleNamespace:
    """Build a lightweight CaseSpec satisfying the duck-typed contract that
    ``RepairContextBuilder._summarize_spec`` relies on."""
    spec = SimpleNamespace(
        domain=SimpleNamespace(
            length_m=_Resolved(2.0),
            height_m=_Resolved(0.5),
        ),
        cylinder=SimpleNamespace(
            radius_m=_Resolved(0.05),
            center_x_m=_Resolved(0.5),
            center_y_m=_Resolved(0.25),
        ),
        has_cylinder=True,
        boundaries=SimpleNamespace(
            left=SimpleNamespace(semantic_type=SimpleNamespace(value="velocity_inlet")),
            right=SimpleNamespace(semantic_type=SimpleNamespace(value="pressure_outlet")),
            top=SimpleNamespace(semantic_type=SimpleNamespace(value="slip_wall")),
            bottom=SimpleNamespace(semantic_type=SimpleNamespace(value="no_slip_wall")),
        ),
        simulation=SimpleNamespace(
            delta_t_s=0.01,
            end_time_s=1.0,
            max_courant=0.5,
        ),
        fluid=SimpleNamespace(kinematic_viscosity_m2_s=_Resolved(1e-6)),
        has_triangle=False,
        has_rectangle=False,
        has_bottom_profile=False,
    )
    if with_obstacles:
        spec.has_triangle = True
        spec.triangle = SimpleNamespace(
            base_width_m=_Resolved(0.1),
            height_m=_Resolved(0.05),
            center_x_m=_Resolved(0.3),
        )
        spec.has_rectangle = True
        spec.rectangle = SimpleNamespace(
            width_m=_Resolved(0.1),
            height_m=_Resolved(0.05),
        )
        spec.has_bottom_profile = True
        spec.bottom_profile = SimpleNamespace(
            profile_type=SimpleNamespace(value="cosine_bell"),
            height_m=_Resolved(0.02),
            width_m=_Resolved(0.2),
        )
    return spec


# ---------------------------------------------------------------------------
# P3 — OpenFOAMErrorClassifier
# ---------------------------------------------------------------------------


class TestErrorClassification:
    """Classify mock OpenFOAM logs into the expected categories."""

    @pytest.mark.parametrize(
        "log, expected_category, expected_severity, expected_repairable",
        [
            # Mesh — blockMesh failure (repairable).
            (LOG_BLOCKMESH_FAIL, ErrorCategory.MESH_ERROR, ErrorSeverity.FATAL, True),
            # Test F — boundary-condition / patch mismatch (repairable).
            (
                LOG_PATCH_MISMATCH_TEST_F,
                ErrorCategory.BOUNDARY_CONDITION_ERROR,
                ErrorSeverity.FATAL,
                True,
            ),
            # Test G — NaN (solver, repairable via the standalone NaN path).
            (
                LOG_NAN_STANDALONE_TEST_G,
                ErrorCategory.SOLVER_ERROR,
                ErrorSeverity.FATAL,
                True,
            ),
            # Divergence reported inside a FOAM FATAL ERROR block.  NOTE:
            # SOLVER_ERROR is *not* in REPAIRABLE_CATEGORIES, so the FATAL-ERROR
            # branch marks it non-repairable (unlike the standalone NaN path).
            (LOG_DIVERGENCE_FATAL, ErrorCategory.SOLVER_ERROR, ErrorSeverity.FATAL, False),
            # Physics — Courant number too high (recoverable, repairable).
            (
                LOG_COURANT_HIGH,
                ErrorCategory.PHYSICS_ERROR,
                ErrorSeverity.RECOVERABLE,
                True,
            ),
            # Syntax — malformed dictionary (repairable).
            (LOG_SYNTAX_MALFORMED, ErrorCategory.SYNTAX_ERROR, ErrorSeverity.FATAL, True),
            # File — missing file (not repairable by the loop).
            (LOG_FILE_NOT_FOUND, ErrorCategory.FILE_ERROR, ErrorSeverity.FATAL, False),
            # Memory — out of memory (not repairable).
            (LOG_MEMORY, ErrorCategory.MEMORY_ERROR, ErrorSeverity.FATAL, False),
            # Timeout — execution timeout (not repairable).
            (LOG_TIMEOUT, ErrorCategory.TIMEOUT_ERROR, ErrorSeverity.FATAL, False),
        ],
    )
    def test_primary_error_classification(
        self,
        log: str,
        expected_category: ErrorCategory,
        expected_severity: ErrorSeverity,
        expected_repairable: bool,
    ) -> None:
        classifier = OpenFOAMErrorClassifier()
        errors = classifier.classify(log)
        assert errors, f"no errors classified from log:\n{log}"

        primary = classifier.get_primary_error(errors)
        assert primary is not None
        assert primary.category == expected_category
        assert primary.severity == expected_severity
        assert primary.is_repairable == expected_repairable
        # A suggested fix should always accompany a classified error.
        assert primary.suggested_fix

    def test_severity_and_repairability_flags(self) -> None:
        """Spot-check the severity/repairability matrix directly."""
        classifier = OpenFOAMErrorClassifier()

        # FATAL + repairable.
        bc = classifier.get_primary_error(classifier.classify(LOG_PATCH_MISMATCH_TEST_F))
        assert bc.severity == ErrorSeverity.FATAL
        assert bc.is_repairable is True

        # RECOVERABLE + repairable.
        cfl = classifier.get_primary_error(classifier.classify(LOG_COURANT_HIGH))
        assert cfl.severity == ErrorSeverity.RECOVERABLE
        assert cfl.is_repairable is True

        # FATAL + non-repairable.
        mem = classifier.get_primary_error(classifier.classify(LOG_MEMORY))
        assert mem.severity == ErrorSeverity.FATAL
        assert mem.is_repairable is False

    def test_repairable_categories_set(self) -> None:
        """Only mesh/boundary/physics/syntax errors are LLM-repairable."""
        repairable = OpenFOAMErrorClassifier.REPAIRABLE_CATEGORIES
        assert repairable == {
            ErrorCategory.BOUNDARY_CONDITION_ERROR,
            ErrorCategory.PHYSICS_ERROR,
            ErrorCategory.SYNTAX_ERROR,
            ErrorCategory.MESH_ERROR,
        }
        # Solver / file / memory / timeout / unknown are explicitly excluded.
        for excluded in (
            ErrorCategory.SOLVER_ERROR,
            ErrorCategory.FILE_ERROR,
            ErrorCategory.MEMORY_ERROR,
            ErrorCategory.TIMEOUT_ERROR,
            ErrorCategory.UNKNOWN_ERROR,
        ):
            assert excluded not in repairable

    def test_extracts_file_and_line_number(self) -> None:
        """FOAM FATAL ERROR context should expose file_path / line_number."""
        errors = OpenFOAMErrorClassifier().classify(LOG_BLOCKMESH_FAIL)
        assert len(errors) == 1
        err = errors[0]
        assert err.file_path == "mesh/blockMesh/blockMesh.C"
        assert err.line_number == 124

    def test_courant_message_includes_max_value(self) -> None:
        err = OpenFOAMErrorClassifier().classify(LOG_COURANT_HIGH)[0]
        assert err.category == ErrorCategory.PHYSICS_ERROR
        assert "2.75" in err.error_message

    def test_courant_at_limit_does_not_raise_error(self) -> None:
        """max Courant == 1.0 is not strictly greater than 1.0 -> no error."""
        log = " Courant Number mean: 0.2 max: 1.0\n"
        assert OpenFOAMErrorClassifier().classify(log) == []

    def test_return_code_zero_yields_no_error(self) -> None:
        """A clean return code must not be classified as an error."""
        errors = OpenFOAMErrorClassifier().classify("Simulation complete.\nReturn code: 0\n")
        assert errors == []

    def test_unknown_nonzero_return_code(self) -> None:
        """A non-zero return code with no other signal -> UNKNOWN_ERROR."""
        errors = OpenFOAMErrorClassifier().classify("Return code: 139\n")
        assert len(errors) == 1
        assert errors[0].category == ErrorCategory.UNKNOWN_ERROR
        assert errors[0].severity == ErrorSeverity.FATAL
        assert errors[0].is_repairable is False

    def test_multiple_errors_are_all_returned(self) -> None:
        """A log may surface more than one error (e.g. mesh + CFL)."""
        log = LOG_BLOCKMESH_FAIL + "\nCourant Number mean: 0.5 max: 2.0\n"
        errors = OpenFOAMErrorClassifier().classify(log)
        categories = {e.category for e in errors}
        assert ErrorCategory.MESH_ERROR in categories
        assert ErrorCategory.PHYSICS_ERROR in categories

    def test_to_dict_roundtrip(self) -> None:
        err = OpenFOAMErrorClassifier().classify(LOG_PATCH_MISMATCH_TEST_F)[0]
        d = err.to_dict()
        assert d["category"] == "boundary_condition_error"
        assert d["severity"] == "fatal"
        assert d["is_repairable"] is True
        assert d["error_message"]
        # raw_log is truncated to 500 chars in the dict form.
        assert len(d["raw_log"]) <= 500


class TestGetPrimaryError:
    """Prioritisation logic for ``get_primary_error``."""

    def test_empty_list_returns_none(self) -> None:
        assert OpenFOAMErrorClassifier().get_primary_error([]) is None

    def test_prefers_repairable_fatal_even_when_later(self) -> None:
        classifier = OpenFOAMErrorClassifier()
        fatal_non_repairable = ClassifiedError(
            category=ErrorCategory.FILE_ERROR,
            severity=ErrorSeverity.FATAL,
            error_message="missing file",
            raw_log="",
            is_repairable=False,
        )
        fatal_repairable = ClassifiedError(
            category=ErrorCategory.BOUNDARY_CONDITION_ERROR,
            severity=ErrorSeverity.FATAL,
            error_message="patch mismatch",
            raw_log="",
            is_repairable=True,
        )
        primary = classifier.get_primary_error([fatal_non_repairable, fatal_repairable])
        assert primary is fatal_repairable

    def test_returns_fatal_when_none_repairable(self) -> None:
        classifier = OpenFOAMErrorClassifier()
        fatal = ClassifiedError(
            category=ErrorCategory.MEMORY_ERROR,
            severity=ErrorSeverity.FATAL,
            error_message="oom",
            raw_log="",
            is_repairable=False,
        )
        assert classifier.get_primary_error([fatal]) is fatal

    def test_returns_first_when_only_non_fatal(self) -> None:
        classifier = OpenFOAMErrorClassifier()
        recoverable = ClassifiedError(
            category=ErrorCategory.PHYSICS_ERROR,
            severity=ErrorSeverity.RECOVERABLE,
            error_message="cfl",
            raw_log="",
            is_repairable=True,
        )
        assert classifier.get_primary_error([recoverable]) is recoverable


# ---------------------------------------------------------------------------
# P4 — RepairPolicy
# ---------------------------------------------------------------------------


class TestRepairPolicy:
    """Retry limits, level escalation and phase freezing."""

    def test_default_limits(self) -> None:
        policy = RepairPolicy()
        assert policy.max_attempts_per_phase == 3
        assert policy.max_global_attempts == 10
        assert policy.current_global_attempts == 0
        assert all(v == 0 for v in policy.phase_attempts.values())
        assert all(v is False for v in policy.phase_frozen.values())
        assert policy.has_repair_been_attempted is False

    def test_can_attempt_initially_true_for_all_phases(self) -> None:
        policy = RepairPolicy()
        for phase in RepairPhase:
            assert policy.can_attempt(phase) is True

    def test_repair_level_progression(self) -> None:
        """Levels escalate CONFIG_ONLY -> DICTIONARY_SYNTAX -> PARTIAL_REGEN."""
        policy = RepairPolicy()
        assert policy.get_repair_level(RepairPhase.SMOKE, 1) == RepairLevel.CONFIG_ONLY
        assert policy.get_repair_level(RepairPhase.SMOKE, 2) == RepairLevel.DICTIONARY_SYNTAX
        assert policy.get_repair_level(RepairPhase.SMOKE, 3) == RepairLevel.PARTIAL_REGENERATION
        # Stays at the highest level beyond attempt 3.
        assert policy.get_repair_level(RepairPhase.SMOKE, 4) == RepairLevel.PARTIAL_REGENERATION
        assert policy.get_repair_level(RepairPhase.MESH, 1) == RepairLevel.CONFIG_ONLY

    def test_record_attempt_success(self) -> None:
        policy = RepairPolicy()
        attempt = RepairAttempt(
            attempt_number=1,
            phase=RepairPhase.SMOKE,
            level=RepairLevel.CONFIG_ONLY,
            error_summary="CFL too high",
            fix_applied="deltaT 0.01->0.001",
            retry_passed=True,
        )
        assert policy.record_attempt(attempt) == RepairStatus.SUCCESS
        assert policy.current_global_attempts == 1
        assert policy.has_repair_been_attempted is True

    def test_phase_freezes_after_max_attempts(self) -> None:
        """Three failed attempts freeze the phase (max_attempts_per_phase=3)."""
        policy = RepairPolicy()
        statuses: list[RepairStatus] = []
        for i in range(1, 4):
            attempt = RepairAttempt(
                attempt_number=i,
                phase=RepairPhase.SMOKE,
                level=policy.get_repair_level(RepairPhase.SMOKE, i),
                error_summary="err",
                fix_applied=f"fix_{i}",
                retry_passed=False,
            )
            statuses.append(policy.record_attempt(attempt))

        assert statuses == [RepairStatus.FAILED, RepairStatus.FAILED, RepairStatus.PHASE_FROZEN]
        assert policy.phase_attempts["smoke"] == 3
        assert policy.phase_frozen["smoke"] is True
        assert policy.current_global_attempts == 3
        # Frozen phase blocks further attempts.
        assert policy.can_attempt(RepairPhase.SMOKE) is False
        # ... but other phases remain available.
        assert policy.can_attempt(RepairPhase.MESH) is True
        assert policy.can_attempt(RepairPhase.FULL_RUN) is True

    def test_other_phase_unaffected_by_freeze(self) -> None:
        policy = RepairPolicy()
        for i in range(1, 4):
            policy.record_attempt(
                RepairAttempt(
                    attempt_number=i,
                    phase=RepairPhase.MESH,
                    level=RepairLevel.CONFIG_ONLY,
                    error_summary="err",
                    fix_applied=f"fix_{i}",
                )
            )
        assert policy.phase_frozen["mesh"] is True
        assert policy.can_attempt(RepairPhase.MESH) is False
        assert policy.can_attempt(RepairPhase.SMOKE) is True

    def test_global_limit_reached(self) -> None:
        """With the default per-phase limit (3) and only three phases, the
        global limit (10) is unreachable because every phase freezes first.
        Isolate the GLOBAL_LIMIT_REACHED branch with a custom config."""
        policy = RepairPolicy(max_attempts_per_phase=10, max_global_attempts=2)
        a1 = RepairAttempt(
            attempt_number=1,
            phase=RepairPhase.SMOKE,
            level=RepairLevel.CONFIG_ONLY,
            error_summary="err",
            fix_applied="fix_1",
        )
        a2 = RepairAttempt(
            attempt_number=2,
            phase=RepairPhase.SMOKE,
            level=RepairLevel.DICTIONARY_SYNTAX,
            error_summary="err",
            fix_applied="fix_2",
        )
        assert policy.record_attempt(a1) == RepairStatus.FAILED
        assert policy.record_attempt(a2) == RepairStatus.GLOBAL_LIMIT_REACHED
        assert policy.current_global_attempts == 2
        # Global limit blocks all further attempts.
        assert policy.can_attempt(RepairPhase.SMOKE) is False
        assert policy.can_attempt(RepairPhase.MESH) is False

    def test_global_limit_takes_precedence_over_phase_freeze(self) -> None:
        """Once the global limit is hit, every phase is blocked even if it has
        not individually frozen."""
        policy = RepairPolicy(max_attempts_per_phase=10, max_global_attempts=1)
        attempt = RepairAttempt(
            attempt_number=1,
            phase=RepairPhase.MESH,
            level=RepairLevel.CONFIG_ONLY,
            error_summary="err",
            fix_applied="fix_1",
        )
        assert policy.record_attempt(attempt) == RepairStatus.GLOBAL_LIMIT_REACHED
        # mesh only used 1/10 attempts, but the global limit still blocks it.
        assert policy.phase_frozen["mesh"] is False
        assert policy.can_attempt(RepairPhase.MESH) is False
        assert policy.can_attempt(RepairPhase.FULL_RUN) is False

    def test_attempt_history_and_to_dict(self) -> None:
        policy = RepairPolicy()
        for i in range(1, 3):
            policy.record_attempt(
                RepairAttempt(
                    attempt_number=i,
                    phase=RepairPhase.SMOKE,
                    level=policy.get_repair_level(RepairPhase.SMOKE, i),
                    error_summary="err",
                    fix_applied=f"fix_{i}",
                )
            )
        assert len(policy.attempt_history) == 2
        assert all(a.fix_applied for a in policy.attempt_history)

        snapshot = policy.to_dict()
        assert snapshot["attempt_count"] == 2
        assert snapshot["current_global_attempts"] == 2
        assert [a["level"] for a in snapshot["attempts"]] == [
            "config_only",
            "dictionary_syntax",
        ]

    def test_has_repair_been_attempted_flag(self) -> None:
        """Guards against RETRY_WITHOUT_REPAIR: before any attempt the flag is
        False; after the first recorded attempt it is True."""
        policy = RepairPolicy()
        assert policy.has_repair_been_attempted is False
        policy.record_attempt(
            RepairAttempt(
                attempt_number=1,
                phase=RepairPhase.SMOKE,
                level=RepairLevel.CONFIG_ONLY,
                error_summary="err",
                fix_applied="deltaT->0.001",
            )
        )
        assert policy.has_repair_been_attempted is True


# ---------------------------------------------------------------------------
# P4 — RepairContextBuilder
# ---------------------------------------------------------------------------


class TestRepairContextBuilder:
    """Assembling LLM diagnosis context from error / spec / files / history."""

    def test_basic_context_keys(self) -> None:
        error = OpenFOAMErrorClassifier().classify(LOG_PATCH_MISMATCH_TEST_F)[0]
        ctx = RepairContextBuilder().build_context(
            error=error, stage="smoke", user_text="inlet-outlet cylinder flow"
        )
        assert ctx["stage"] == "smoke"
        assert ctx["error"]["category"] == "boundary_condition_error"
        assert ctx["user_original_input"] == "inlet-outlet cylinder flow"

    def test_no_spec_omits_spec_summary(self) -> None:
        error = OpenFOAMErrorClassifier().classify(LOG_COURANT_HIGH)[0]
        ctx = RepairContextBuilder().build_context(error=error, stage="smoke", spec=None)
        assert "spec_summary" not in ctx

    def test_spec_summary_for_boundary_error_includes_obstacles(self) -> None:
        """Mesh / boundary-condition errors surface obstacle geometry."""
        error = OpenFOAMErrorClassifier().classify(LOG_PATCH_MISMATCH_TEST_F)[0]
        spec = _make_spec(with_obstacles=True)
        ctx = RepairContextBuilder().build_context(
            error=error, stage="mesh", spec=spec, user_text="..."
        )
        summary = ctx["spec_summary"]
        assert summary["domain"] == {"length": 2.0, "height": 0.5}
        assert summary["cylinder"] == {
            "radius": 0.05,
            "center_x": 0.5,
            "center_y": 0.25,
        }
        assert "triangle" in summary
        assert "rectangle" in summary
        assert "bottom_profile" in summary
        assert summary["boundaries"] == {
            "left": "velocity_inlet",
            "right": "pressure_outlet",
            "top": "slip_wall",
            "bottom": "no_slip_wall",
        }

    def test_spec_summary_for_physics_error_includes_simulation(self) -> None:
        """Physics / solver errors surface simulation + fluid parameters and
        do NOT include obstacle geometry."""
        error = OpenFOAMErrorClassifier().classify(LOG_COURANT_HIGH)[0]
        spec = _make_spec()
        ctx = RepairContextBuilder().build_context(error=error, stage="smoke", spec=spec)
        summary = ctx["spec_summary"]
        assert summary["simulation"] == {
            "delta_t": 0.01,
            "end_time": 1.0,
            "max_courant": 0.5,
        }
        assert summary["fluid"] == {"nu": 1e-6}
        # Obstacles are only relevant to mesh/BC errors.
        assert "triangle" not in summary
        assert "rectangle" not in summary
        assert "bottom_profile" not in summary

    def test_file_contents_truncated_to_2000_chars(self) -> None:
        error = OpenFOAMErrorClassifier().classify(LOG_SYNTAX_MALFORMED)[0]
        ctx = RepairContextBuilder().build_context(
            error=error,
            stage="mesh",
            file_contents={"system/controlDict": "x" * 5000},
        )
        assert len(ctx["files"]["system/controlDict"]) == 2000

    def test_user_text_truncated_to_500_chars(self) -> None:
        error = OpenFOAMErrorClassifier().classify(LOG_COURANT_HIGH)[0]
        ctx = RepairContextBuilder().build_context(
            error=error, stage="smoke", user_text="y" * 1000
        )
        assert len(ctx["user_original_input"]) == 500

    def test_previous_attempts_limited_to_last_three(self) -> None:
        error = OpenFOAMErrorClassifier().classify(LOG_COURANT_HIGH)[0]
        attempts = [{"fix_applied": f"fix_{i}"} for i in range(5)]
        ctx = RepairContextBuilder().build_context(
            error=error, stage="smoke", previous_attempts=attempts
        )
        assert len(ctx["previous_attempts"]) == 3
        # The most recent three attempts are retained.
        assert ctx["previous_attempts"][-1]["fix_applied"] == "fix_4"
        assert ctx["previous_attempts"][0]["fix_applied"] == "fix_2"

    def test_context_references_error_raw_log(self) -> None:
        error = OpenFOAMErrorClassifier().classify(LOG_BLOCKMESH_FAIL)[0]
        ctx = RepairContextBuilder().build_context(error=error, stage="mesh")
        assert "blockMesh" in ctx["error"]["raw_log"]


# ---------------------------------------------------------------------------
# P4 — RepairOrchestrator (classify -> context -> diagnose -> apply -> validate)
# ---------------------------------------------------------------------------


class TestRepairOrchestrator:
    """Full repair-loop orchestration against mock logs and doubles."""

    def test_non_repairable_error_skips_repair_loop(self) -> None:
        """A non-repairable error (FILE_ERROR) must not enter the loop."""
        orchestrator = RepairOrchestrator()
        result = orchestrator.attempt_repair(
            error_log=LOG_FILE_NOT_FOUND, stage="smoke"
        )
        assert result.repaired is False
        assert result.final_status == RepairStatus.FAILED
        assert "not repairable" in result.error
        # The early-return path leaves the policy snapshot unset (None) but,
        # crucially, no attempt was ever recorded against the live policy.
        assert result.policy_snapshot is None
        assert orchestrator.policy.current_global_attempts == 0
        assert orchestrator.policy.has_repair_been_attempted is False

    def test_no_classifiable_error_returns_failure(self) -> None:
        orchestrator = RepairOrchestrator()
        result = orchestrator.attempt_repair(
            error_log="Simulation finished.\nReturn code: 0\n", stage="smoke"
        )
        assert result.repaired is False
        assert result.final_status == RepairStatus.FAILED
        assert "No classifiable error" in result.error
        assert result.policy_snapshot is None
        assert orchestrator.policy.current_global_attempts == 0

    def test_unknown_error_is_not_repairable(self) -> None:
        orchestrator = RepairOrchestrator()
        result = orchestrator.attempt_repair(error_log="Return code: 139\n", stage="smoke")
        assert result.repaired is False
        assert result.final_status == RepairStatus.FAILED
        assert "not repairable" in result.error
        assert result.policy_snapshot is None
        assert orchestrator.policy.current_global_attempts == 0
        assert orchestrator.policy.has_repair_been_attempted is False

    def test_test_f_freezes_when_no_llm_available(self) -> None:
        """Test F (patch error): repairable, but with no LLM the diagnoser
        returns no fixes -> three no-fix attempts -> phase frozen."""
        orchestrator = RepairOrchestrator()  # llm_client=None
        result = orchestrator.attempt_repair(
            error_log=LOG_PATCH_MISMATCH_TEST_F,
            stage="smoke",
            user_text="inlet-outlet cylinder flow",
        )
        assert result.repaired is False
        # The "no fixes" continue-path exhausts the loop and reports FAILED,
        # while the policy snapshot shows the phase has frozen.
        assert result.final_status == RepairStatus.FAILED
        assert result.attempts == 3
        snap = result.policy_snapshot
        assert snap["phase_attempts"]["smoke"] == 3
        assert snap["phase_frozen"]["smoke"] is True
        # The diagnoser ran once per attempt, each time reporting no fixes.
        assert len(result.diagnosis_history) == 3
        assert result.diagnosis_history[0]["fixes"] == []
        assert "LLM client not available" in result.diagnosis_history[0]["root_cause"]

    def test_test_g_freezes_when_executor_cannot_apply(self) -> None:
        """Test G (NaN): the LLM suggests fixes, but the real executor with no
        workstation cannot apply them -> no_fixes_applied -> phase frozen,
        exercising the CONFIG_ONLY -> DICTIONARY_SYNTAX -> PARTIAL_REGEN
        escalation through the live executor."""
        client = _FakeLLMClient()
        orchestrator = RepairOrchestrator(llm_client=client)  # executor=None
        result = orchestrator.attempt_repair(
            error_log=LOG_NAN_STANDALONE_TEST_G,
            stage="full_run",
            user_text="cylinder wake",
        )
        assert result.repaired is False
        assert result.final_status == RepairStatus.PHASE_FROZEN
        snap = result.policy_snapshot
        assert snap["attempt_count"] == 3
        assert snap["phase_attempts"]["full_run"] == 3
        assert snap["phase_frozen"]["full_run"] is True
        # LLM consulted once per attempt.
        assert client.call_count == 3
        # Nothing could be applied without a workstation.
        assert result.fixes_applied == []
        # Level escalation is visible in the recorded attempts.
        levels = [a["level"] for a in snap["attempts"]]
        assert levels == ["config_only", "dictionary_syntax", "partial_regeneration"]

    def test_test_f_success_path(self) -> None:
        """Test F (patch error): full happy path classify -> context ->
        diagnose -> apply -> validate -> SUCCESS.

        The real ``ControlledRepairExecutor`` can never set
        ``retry_passed=True`` without a live workstation, so the executor
        boundary is replaced with a fake to exercise the orchestrator's
        success branch while keeping classifier/context-builder/diagnoser real.
        """
        client = _FakeLLMClient()
        orchestrator = RepairOrchestrator(llm_client=client)
        orchestrator._executor_impl = _FakeExecutorImpl(retry_passed=True)

        result = orchestrator.attempt_repair(
            error_log=LOG_PATCH_MISMATCH_TEST_F,
            stage="smoke",
            user_text="inlet-outlet flow",
        )
        assert result.repaired is True
        assert result.final_status == RepairStatus.SUCCESS
        assert result.attempts == 1
        assert result.fixes_applied  # documented change present
        snap = result.policy_snapshot
        assert snap["attempt_count"] == 1
        assert snap["attempts"][0]["retry_passed"] is True
        assert snap["attempts"][0]["fix_applied"]
        # Exactly one diagnosis was performed.
        assert client.call_count == 1

    def test_global_limit_propagated_from_executor(self) -> None:
        """A low global limit reaches GLOBAL_LIMIT_REACHED via the real
        executor's record_attempt (no_fixes_applied -> FAILED -> limit)."""
        client = _FakeLLMClient()
        orchestrator = RepairOrchestrator(llm_client=client)
        orchestrator._policy = RepairPolicy(max_attempts_per_phase=10, max_global_attempts=2)

        result = orchestrator.attempt_repair(
            error_log=LOG_COURANT_HIGH, stage="smoke", user_text="CFL too high"
        )
        assert result.repaired is False
        assert result.final_status == RepairStatus.GLOBAL_LIMIT_REACHED
        assert result.policy_snapshot["attempt_count"] == 2
        assert "Global repair limit" in result.error

    def test_stage_maps_to_repair_phase(self) -> None:
        """Valid stages map to RepairPhase; unknown stages fall back to SMOKE."""
        orchestrator = RepairOrchestrator()
        result = orchestrator.attempt_repair(
            error_log=LOG_COURANT_HIGH, stage="full_run", user_text="cfl"
        )
        snap = result.policy_snapshot
        # full_run attempts were recorded against the full_run phase.
        assert snap["phase_attempts"]["full_run"] == 3

    def test_reset_policy_clears_history(self) -> None:
        orchestrator = RepairOrchestrator()
        orchestrator.attempt_repair(
            error_log=LOG_PATCH_MISMATCH_TEST_F, stage="smoke"
        )
        assert orchestrator.policy.current_global_attempts > 0
        orchestrator.reset_policy()
        assert orchestrator.policy.current_global_attempts == 0
        assert orchestrator.policy.has_repair_been_attempted is False


# ---------------------------------------------------------------------------
# Invariant — RETRY_WITHOUT_REPAIR is never allowed
# ---------------------------------------------------------------------------


class TestNoRetryWithoutRepair:
    """Every retry must be preceded by a recorded repair attempt with a
    documented ``fix_applied``.  No silent retries are permitted."""

    def test_policy_flag_guards_first_retry(self) -> None:
        """``has_repair_been_attempted`` flips to True only after an attempt is
        recorded, proving a retry cannot occur without a prior repair."""
        policy = RepairPolicy()
        assert policy.has_repair_been_attempted is False
        assert policy.can_attempt(RepairPhase.SMOKE) is True  # first attempt allowed
        policy.record_attempt(
            RepairAttempt(
                attempt_number=1,
                phase=RepairPhase.SMOKE,
                level=RepairLevel.CONFIG_ONLY,
                error_summary="err",
                fix_applied="deltaT->0.001",
            )
        )
        assert policy.has_repair_been_attempted is True

    def test_every_recorded_attempt_has_documented_fix(self) -> None:
        """No orchestrator attempt is ever recorded without a fix_applied
        entry (the only legal "no-op" markers are explicit failed-attempt
        records, never a silent RETRY_WITHOUT_REPAIR)."""
        orchestrator = RepairOrchestrator()  # no LLM -> no_fixes_suggested path
        result = orchestrator.attempt_repair(
            error_log=LOG_PATCH_MISMATCH_TEST_F, stage="smoke"
        )
        attempts = result.policy_snapshot["attempts"]
        assert len(attempts) == 3
        for attempt in attempts:
            assert attempt["fix_applied"], "attempt recorded with empty fix_applied"
            assert attempt["fix_applied"] != "retry_without_repair"
            assert attempt["fix_applied"] != ""

    def test_loop_iteration_count_matches_recorded_attempts(self) -> None:
        """Each loop iteration both diagnoses AND records an attempt: the
        number of LLM calls equals the number of recorded attempts, proving
        no iteration advanced without recording a repair attempt."""
        client = _FakeLLMClient()
        orchestrator = RepairOrchestrator(llm_client=client)  # executor=None
        result = orchestrator.attempt_repair(
            error_log=LOG_NAN_STANDALONE_TEST_G, stage="full_run"
        )
        snap = result.policy_snapshot
        # Every diagnosis corresponded to exactly one recorded attempt.
        assert client.call_count == snap["attempt_count"]
        assert snap["attempt_count"] == 3

    def test_frozen_phase_blocks_all_retries(self) -> None:
        """After a phase freezes, ``can_attempt`` is False, so no further
        retries (with or without repair) can sneak through that phase."""
        policy = RepairPolicy()
        for i in range(1, 4):
            policy.record_attempt(
                RepairAttempt(
                    attempt_number=i,
                    phase=RepairPhase.SMOKE,
                    level=RepairLevel.CONFIG_ONLY,
                    error_summary="err",
                    fix_applied=f"fix_{i}",
                )
            )
        assert policy.can_attempt(RepairPhase.SMOKE) is False
        assert policy.has_repair_been_attempted is True

    def test_no_fixes_path_still_records_before_retrying(self) -> None:
        """The orchestrator's "LLM suggested no fixes" branch records an
        attempt (no_fixes_suggested) *before* continuing the loop — it never
        retries the stage without first going through the repair machinery."""
        client = _FakeLLMClient(fixes=[])  # LLM always returns no fixes
        orchestrator = RepairOrchestrator(llm_client=client)
        result = orchestrator.attempt_repair(
            error_log=LOG_COURANT_HIGH, stage="smoke"
        )
        attempts = result.policy_snapshot["attempts"]
        assert len(attempts) == 3
        # Every recorded attempt is explicitly a "no fixes" attempt, not a
        # silent retry.
        assert all(a["fix_applied"] == "no_fixes_suggested" for a in attempts)
        assert client.call_count == 3
