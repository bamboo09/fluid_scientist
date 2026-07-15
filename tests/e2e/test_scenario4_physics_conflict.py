"""Scenario 4: physics conflict detection and blocking.

User intent
-----------
    二维稳态圆柱绕流，研究展向翻转和涡脱落频谱。

This request is internally contradictory:

* **2D + spanwise flip** -- a spanwise flip observable is meaningless for a
  two-dimensional case (there is no spanwise dimension).
* **steady + vortex-shedding spectrum** -- a frequency spectrum requires a
  transient computation; a steady time mode cannot resolve vortex shedding.

The system must *detect* both conflicts, *block* compilation, and must **not**
silently rewrite the user's goals (e.g. by flipping steady -> transient or by
dropping the spanwise-flip observable).  The ``ScientificConsistencyValidator``
must report the conflicts as errors.

Two layers are exercised:

1. The full ``LLMPipeline`` on the raw text (ambiguity/conflict detection +
   observable status downgrade).
2. The ``ScientificConsistencyValidator`` on an explicit conflicting
   ``RequestedCaseIR`` (validator-level conflict reporting + blocking).
"""
from __future__ import annotations

import pytest

from fluid_scientist.case_ir.models import (
    BoundaryIntent,
    Entity,
    Observable,
    PhysicsIntent,
    RequestedCaseIR,
)
from fluid_scientist.case_ir.validators import (
    CaseIRValidationReport,
    SchemaValidator,
    ScientificConsistencyValidator,
)
from fluid_scientist.llm_pipeline import LLMPipeline

USER_TEXT = "二维稳态圆柱绕流，研究展向翻转和涡脱落频谱。"


def _build_conflict_ir() -> RequestedCaseIR:
    """An explicit, contradictory Case IR mirroring the user intent."""
    return RequestedCaseIR(
        study_id="S4",
        case_id="C4",
        physics=PhysicsIntent(
            flow_regime="incompressible",
            time_mode="steady",
            turbulence="laminar",
        ),
        entities=[Entity(id="cyl", kind="cylinder")],
        boundary_intents=[
            BoundaryIntent(id="bc_front", target_patch="front", semantic_role="empty 2D"),
            BoundaryIntent(id="bc_back", target_patch="back", semantic_role="empty 2D"),
        ],
        observables=[
            Observable(id="obs_flip", semantic_type="spanwise_flip"),
            Observable(id="obs_spec", semantic_type="frequency_spectrum"),
        ],
    )


class TestScenario4PhysicsConflict:
    """Conflict detection and blocking for the 2D steady cylinder request."""

    @pytest.fixture(scope="module")
    def pipeline(self) -> LLMPipeline:
        return LLMPipeline()

    @pytest.fixture(scope="module")
    def result(self, pipeline: LLMPipeline):
        return pipeline.run(USER_TEXT)

    @pytest.fixture(scope="module")
    def conflict_ir(self) -> RequestedCaseIR:
        return _build_conflict_ir()

    @pytest.fixture(scope="module")
    def consistency_issues(self, conflict_ir):
        return ScientificConsistencyValidator().validate(conflict_ir)

    @pytest.fixture(scope="module")
    def validation_report(self, conflict_ir, consistency_issues):
        return CaseIRValidationReport(
            schema_issues=SchemaValidator().validate(conflict_ir),
            consistency_issues=consistency_issues,
        )

    # ------------------------------------------------------------------
    # pipeline-level conflict detection
    # ------------------------------------------------------------------
    def test_pipeline_detects_2d_spanwise_conflict(self, result):
        """2D + spanwise-flip must raise a conflict in the ambiguity pass."""
        conflict_types = {c.get("conflict_type") for c in result.ambiguity_detection.conflicts}
        assert any("2d" in str(t).lower() and "spanwise" in str(t).lower()
                   for t in conflict_types), \
            f"2D-vs-spanwise conflict not detected; conflicts={conflict_types}"

    def test_pipeline_detects_steady_spectrum_conflict(self, result):
        """Steady + frequency-spectrum must raise a conflict."""
        conflict_types = {c.get("conflict_type") for c in result.ambiguity_detection.conflicts}
        assert any("steady" in str(t).lower() for t in conflict_types), \
            f"steady-vs-transient-observable conflict not detected; conflicts={conflict_types}"

    def test_pipeline_blocks_via_blocking_unknowns(self, result):
        """Conflicts must produce blocking unknowns that block compilation."""
        assert len(result.ambiguity_detection.blocking_unknowns) > 0

    def test_pipeline_does_not_silently_change_time_mode(self, result):
        """The user asked for steady; the system must not rewrite it to transient."""
        assert result.physics_decomposition.time_mode == "steady"

    def test_frequency_spectrum_observable_downgraded(self, result):
        """The frequency-spectrum observable must be flagged REQUIRES_NEW_PHYSICS
        (it cannot be satisfied under a steady time mode)."""
        spec = [
            o for o in result.observable_decomposition.observables
            if o["semantic_type"] == "frequency_spectrum"
        ]
        assert spec, "frequency_spectrum observable missing"
        assert spec[0]["capability_status"] == "REQUIRES_NEW_PHYSICS"

    def test_spanwise_concept_preserved_as_fact(self, result):
        """The spanwise concept must not be silently dropped.

        The fact extractor captures ``展向`` as a *constraint* fact (not an
        observable, because ``spanwise_flip`` is not in the observable
        catalog).  The ambiguity detector then uses this constraint to flag
        the 2D-vs-spanwise conflict.  This test verifies the concept survives
        in the pipeline output rather than being silently discarded.
        """
        spanwise_facts = [
            f for f in result.facts
            if f.category == "constraint" and "spanwise" in str(f.value).lower()
        ]
        assert spanwise_facts, "Spanwise constraint fact was silently dropped"

    # ------------------------------------------------------------------
    # validator-level conflict reporting
    # ------------------------------------------------------------------
    def test_consistency_validator_reports_2d_spanwise_error(self, consistency_issues):
        codes = {i.code for i in consistency_issues if i.level == "error"}
        assert "TWO_D_SPANWISE_FLIP_CONFLICT" in codes, \
            f"2D-spanwise conflict not reported; error codes={codes}"

    def test_consistency_validator_reports_steady_spectrum_error(self, consistency_issues):
        codes = {i.code for i in consistency_issues if i.level == "error"}
        assert "STEADY_FREQUENCY_SPECTRUM_CONFLICT" in codes, \
            f"steady-spectrum conflict not reported; error codes={codes}"

    def test_consistency_errors_are_blocking_level(self, consistency_issues):
        """Conflict issues must be raised at the 'error' (blocking) level."""
        error_codes = {i.code for i in consistency_issues if i.level == "error"}
        assert len(error_codes) >= 2

    # ------------------------------------------------------------------
    # validation report must block compilation
    # ------------------------------------------------------------------
    def test_validation_report_blocks_compilation(self, validation_report):
        assert validation_report.passed is False
        assert validation_report.error_count >= 1

    def test_validation_report_error_count(self, validation_report):
        assert validation_report.error_count >= 2

    # ------------------------------------------------------------------
    # no silent goal modification
    # ------------------------------------------------------------------
    def test_ir_time_mode_unchanged_by_validation(self, conflict_ir):
        """The validator must report errors but must not mutate the IR."""
        assert conflict_ir.physics.time_mode == "steady"
        assert {o.semantic_type for o in conflict_ir.observables} == \
            {"spanwise_flip", "frequency_spectrum"}
