"""Test: "仿真时间设为15秒" — single-field edit with untouched-guarantee.

This test reproduces the known issue where a simple time-parameter edit
could inadvertently clobber unrelated fields.  The new spec-editing
system guarantees that only the targeted path changes.

Verifies:
* The new spec has end_time = 15 s.
* The version number incremented (1 -> 2).
* delta_t was NOT changed (untouched_guarantee holds).
* The diff records the change with the user's source_quote.
"""
from __future__ import annotations

import pytest

from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import SimulationStudySpec

from .conftest import make_patch, make_study_spec


class TestSetEndTime15s:
    """Verify that setting end_time to 15 s works correctly."""

    def test_end_time_changed_to_15s(self) -> None:
        """The new spec's end_time is 15.0 s."""
        spec = make_study_spec()
        original_end_time = spec.numerics.time.end_time.value
        assert original_end_time == 10.0, "Precondition: end_time starts at 10 s"

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                    confidence=0.95,
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == [], f"Patch should succeed, got errors: {result.errors}"
        assert result.clarifications == [], "No clarifications expected"
        assert result.new_spec is not None, "new_spec must be populated"

        new_end_time = result.new_spec.numerics.time.end_time.value
        assert new_end_time == 15.0, f"end_time should be 15.0, got {new_end_time}"

    def test_version_incremented(self) -> None:
        """The version number incremented from 1 to 2."""
        spec = make_study_spec()
        assert spec.version == 1, "Precondition: version starts at 1"

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.new_spec is not None
        assert result.new_spec.version == 2, (
            f"Version should be 2, got {result.new_spec.version}"
        )
        assert result.new_spec.parent_version == 1, (
            "parent_version should point to the previous version"
        )

    def test_delta_t_unchanged(self) -> None:
        """delta_t was NOT changed — untouched_guarantee holds."""
        spec = make_study_spec()
        original_delta_t = spec.numerics.time.delta_t.value
        assert original_delta_t == 0.01, "Precondition: delta_t starts at 0.01"

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.new_spec is not None
        new_delta_t = result.new_spec.numerics.time.delta_t.value
        assert new_delta_t == original_delta_t, (
            f"delta_t should be unchanged ({original_delta_t}), got {new_delta_t}"
        )

    def test_diff_shows_change_with_source_quote(self) -> None:
        """The diff records the end_time change and includes the source_quote."""
        spec = make_study_spec()

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.diff is not None, "diff must be populated"
        assert result.diff.base_version == 1
        assert result.diff.new_version == 2

        # Find the field diff for end_time/value.
        end_time_diffs = [
            d for d in result.diff.field_diffs
            if "end_time" in d.path
        ]
        assert len(end_time_diffs) > 0, (
            "Expected at least one field diff mentioning 'end_time'"
        )

        # The leaf diff should show old=10.0, new=15.0.
        value_diff = next(
            (d for d in end_time_diffs if d.path.endswith("/value")),
            None,
        )
        assert value_diff is not None, "Expected a field diff for end_time/value"
        assert value_diff.old_value == 10.0, (
            f"Old end_time value should be 10.0, got {value_diff.old_value}"
        )
        assert value_diff.new_value == 15.0, (
            f"New end_time value should be 15.0, got {value_diff.new_value}"
        )
        assert value_diff.source_quote == "仿真时间设为15秒", (
            f"source_quote should be preserved, got {value_diff.source_quote!r}"
        )

    def test_unit_preserved_after_replace(self) -> None:
        """The unit 's' is preserved when replacing just the numeric value."""
        spec = make_study_spec()

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.new_spec is not None
        assert result.new_spec.numerics.time.end_time.unit == "s", (
            "Unit should be preserved as 's'"
        )
