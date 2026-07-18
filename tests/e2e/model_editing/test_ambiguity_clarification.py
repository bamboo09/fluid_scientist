"""Test: ambiguity clarification for "仿真时间设为15秒".

This test reproduces the known issue where the ambiguous instruction
"仿真时间设为15秒" was applied directly without clarifying whether the
user meant:

a. "结束时间为15秒" — set the end_time to 15 s (duration becomes 10 s).
b. "持续计算15秒" — extend the simulation by 15 s (end_time becomes
   20 s when start_time is 5 s).

The new spec-editing system supports :class:`ClarificationRequest` with
multiple :class:`ClarificationAlternative` options, each carrying
operations that can be applied after the user selects one.

Verifies:
* The patch returns clarifications (not applying directly).
* The clarification has two alternatives with correct labels.
* After selecting alternative A, end_time = 15 s.
* After selecting alternative B, end_time = 20 s (start 5 + duration 15).
"""
from __future__ import annotations

import pytest

from fluid_scientist.spec_editing import (
    ClarificationAlternative,
    ClarificationRequest,
    PatchEngine,
    PatchOperation,
    SimulationSpecPatch,
)
from fluid_scientist.study_spec import (
    NumericsDefinition,
    Quantity,
    TimeControl,
)

from .conftest import make_study_spec


def _make_spec_with_start5_end10() -> "SimulationStudySpec":
    """Build a spec with start_time=5 s, end_time=10 s."""
    spec = make_study_spec()
    return spec.model_copy(update={
        "numerics": NumericsDefinition(
            time=TimeControl(
                mode="transient",
                start_time=Quantity(value=5.0, unit="s"),
                end_time=Quantity(value=10.0, unit="s"),
                delta_t=Quantity(value=0.01, unit="s"),
                adaptive=False,
                max_courant=0.5,
                write_control="runTime",
                write_interval=Quantity(value=0.1, unit="s"),
            ),
            solver="icoFoam",
            discretization={"ddtSchemes": {"ddtScheme": "backward"}},
            turbulence_model="laminar",
        ),
    })


def _make_ambiguous_patch(spec: "SimulationStudySpec") -> SimulationSpecPatch:
    """Build a patch for "仿真时间设为15秒" that requests clarification.

    The patch carries two alternatives:
    A. "结束时间为15秒" — replace end_time with 15.0
    B. "持续计算15秒" — replace end_time with 20.0 (start 5 + 15)
    """
    clarification = ClarificationRequest(
        clarification_id="clarif_end_time",
        question=(
            '"仿真时间设为15秒" 有两种理解：\n'
            "A. 结束时间设为 15 秒（当前结束时间 10 秒）\n"
            "B. 持续计算 15 秒（当前结束时间 10 秒 + 15 秒 = 25 秒）"
        ),
        alternatives=[
            ClarificationAlternative(
                label="结束时间为15秒",
                operations=[
                    PatchOperation(
                        op="replace",
                        path="/numerics/time/end_time",
                        value=15.0,
                        source_quote="结束时间为15秒",
                    ),
                ],
            ),
            ClarificationAlternative(
                label="持续计算15秒",
                operations=[
                    PatchOperation(
                        op="replace",
                        path="/numerics/time/end_time",
                        value=20.0,
                        source_quote="持续计算15秒",
                    ),
                ],
            ),
        ],
        affected_paths=["/numerics/time/end_time"],
        blocking=True,
    )

    return SimulationSpecPatch(
        patch_id="patch_ambiguous_001",
        session_id=spec.session_id,
        base_spec_id=spec.spec_id,
        base_version=spec.version,
        intent="modify_existing_spec",
        operations=[
            PatchOperation(
                op="replace",
                path="/numerics/time/end_time",
                value=15.0,
                source_quote="仿真时间设为15秒",
            ),
        ],
        clarifications=[clarification],
        impact_requests=[],
        untouched_guarantee=True,
        assistant_message='需要澄清"仿真时间设为15秒"的含义',
    )


def _apply_alternative(
    engine: PatchEngine,
    spec: "SimulationStudySpec",
    alt: ClarificationAlternative,
    patch_id: str,
) -> "SimulationStudySpec":
    """Apply a clarification alternative's operations as a new patch."""
    resolved_patch = SimulationSpecPatch(
        patch_id=patch_id,
        session_id=spec.session_id,
        base_spec_id=spec.spec_id,
        base_version=spec.version,
        intent="modify_existing_spec",
        operations=alt.operations,
        clarifications=[],
        impact_requests=[],
        untouched_guarantee=True,
        assistant_message=f"Applying selected alternative: {alt.label}",
    )
    result = engine.process_patch(resolved_patch, spec)
    assert result.errors == [], f"Resolved patch failed: {result.errors}"
    assert result.new_spec is not None
    return result.new_spec


class TestAmbiguityClarification:
    """Verify ambiguity clarification for "仿真时间设为15秒"."""

    def test_patch_returns_clarifications_not_applied(self) -> None:
        """The patch returns clarifications without applying operations."""
        spec = _make_spec_with_start5_end10()
        patch = _make_ambiguous_patch(spec)

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == [], "No errors expected"
        assert len(result.clarifications) > 0, (
            "Clarifications should be returned for ambiguous patch"
        )
        assert result.new_spec is None, (
            "Spec should NOT be modified when clarifications are pending"
        )

    def test_clarification_has_two_alternatives(self) -> None:
        """The clarification has exactly two alternatives."""
        spec = _make_spec_with_start5_end10()
        patch = _make_ambiguous_patch(spec)

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert len(result.clarifications) == 1
        clarif = result.clarifications[0]
        assert len(clarif.alternatives) == 2, (
            f"Expected 2 alternatives, got {len(clarif.alternatives)}"
        )

    def test_alternative_labels(self) -> None:
        """The two alternatives have the correct labels."""
        spec = _make_spec_with_start5_end10()
        patch = _make_ambiguous_patch(spec)

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        clarif = result.clarifications[0]
        labels = [alt.label for alt in clarif.alternatives]
        assert "结束时间为15秒" in labels, (
            f"Expected '结束时间为15秒' in alternatives, got: {labels}"
        )
        assert "持续计算15秒" in labels, (
            f"Expected '持续计算15秒' in alternatives, got: {labels}"
        )

    def test_selecting_alt_a_sets_end_time_15s(self) -> None:
        """Selecting alternative A sets end_time to 15 s."""
        spec = _make_spec_with_start5_end10()
        assert spec.numerics.time.end_time.value == 10.0

        patch = _make_ambiguous_patch(spec)
        engine = PatchEngine()

        # Step 1: get clarifications.
        result = engine.process_patch(patch, spec)
        assert result.clarifications

        clarif = result.clarifications[0]
        alt_a = next(
            a for a in clarif.alternatives if a.label == "结束时间为15秒"
        )

        # Step 2: apply the alternative's operations.
        new_spec = _apply_alternative(engine, spec, alt_a, "patch_alt_a")
        assert new_spec.numerics.time.end_time.value == 15.0, (
            f"end_time should be 15.0 after selecting alt A, "
            f"got {new_spec.numerics.time.end_time.value}"
        )

    def test_selecting_alt_b_sets_end_time_20s(self) -> None:
        """Selecting alternative B sets end_time to 20 s (5 + 15)."""
        spec = _make_spec_with_start5_end10()
        assert spec.numerics.time.end_time.value == 10.0

        patch = _make_ambiguous_patch(spec)
        engine = PatchEngine()

        # Step 1: get clarifications.
        result = engine.process_patch(patch, spec)
        assert result.clarifications

        clarif = result.clarifications[0]
        alt_b = next(
            a for a in clarif.alternatives if a.label == "持续计算15秒"
        )

        # Step 2: apply the alternative's operations.
        new_spec = _apply_alternative(engine, spec, alt_b, "patch_alt_b")
        assert new_spec.numerics.time.end_time.value == 20.0, (
            f"end_time should be 20.0 after selecting alt B, "
            f"got {new_spec.numerics.time.end_time.value}"
        )

    def test_start_time_unchanged_after_clarification(self) -> None:
        """start_time is not changed by either alternative."""
        spec = _make_spec_with_start5_end10()
        original_start = spec.numerics.time.start_time.value
        assert original_start == 5.0

        patch = _make_ambiguous_patch(spec)
        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        clarif = result.clarifications[0]

        for alt in clarif.alternatives:
            new_spec = _apply_alternative(
                engine, spec, alt, f"patch_{alt.label}"
            )
            assert new_spec.numerics.time.start_time.value == original_start, (
                f"start_time should remain {original_start} after selecting "
                f"'{alt.label}'"
            )

    def test_clarification_question_mentions_both_options(self) -> None:
        """The clarification question mentions both interpretations."""
        spec = _make_spec_with_start5_end10()
        patch = _make_ambiguous_patch(spec)

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        clarif = result.clarifications[0]
        assert "结束时间" in clarif.question or "15秒" in clarif.question
        assert "持续计算" in clarif.question or "15秒" in clarif.question

    def test_version_not_incremented_during_clarification(self) -> None:
        """The spec version is NOT incremented while clarifications are pending."""
        spec = _make_spec_with_start5_end10()
        original_version = spec.version

        patch = _make_ambiguous_patch(spec)
        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.new_spec is None, "No new spec during clarification"
        assert spec.version == original_version, (
            "Version should not change during clarification"
        )

    def test_version_incremented_after_resolved_patch(self) -> None:
        """Version increments after the resolved patch is applied."""
        spec = _make_spec_with_start5_end10()
        original_version = spec.version

        patch = _make_ambiguous_patch(spec)
        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        clarif = result.clarifications[0]
        alt_a = next(
            a for a in clarif.alternatives if a.label == "结束时间为15秒"
        )
        new_spec = _apply_alternative(engine, spec, alt_a, "patch_resolved")
        assert new_spec.version == original_version + 1
