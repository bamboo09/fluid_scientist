"""12 time-parameter modification tests proving the generic PatchEngine handles
all time edits through the same schema-driven path, without field-specific if/else.

Covers the plan's Phase 17 time test matrix items 1-12.
"""
from __future__ import annotations

import pytest

from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import (
    NumericsDefinition,
    Quantity,
    SimulationStudySpec,
    TimeControl,
    TimeWindow,
)

from .conftest import make_patch, make_study_spec


def _apply(spec: SimulationStudySpec, ops: list[PatchOperation]):
    """Helper: apply a patch and return the new spec, asserting no errors."""
    patch = make_patch(spec, operations=ops)
    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Unexpected errors: {result.errors}"
    assert result.new_spec is not None, "new_spec must be populated"
    return result


# ---------------------------------------------------------------------------
# 1-3: end_time modifications
# ---------------------------------------------------------------------------

class TestEndTime:
    def test_set_end_time_15s(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(op="replace", path="/numerics/time/end_time",
                           value=15.0, source_quote="仿真时间设为15秒", confidence=0.99),
        ])
        assert result.new_spec.numerics.time.end_time.value == 15.0
        # untouched_guarantee: delta_t not changed
        assert result.new_spec.numerics.time.delta_t.value == 0.01

    def test_set_end_time_20s(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(op="replace", path="/numerics/time/end_time",
                           value=20.0, source_quote="结束时间为20秒", confidence=0.99),
        ])
        assert result.new_spec.numerics.time.end_time.value == 20.0
        assert result.new_spec.numerics.time.start_time.value == 0.0

    def test_run_15s_from_5s_start(self) -> None:
        """When start_time=5s and user says '再运行15秒', clarification is needed."""
        spec = make_study_spec()
        spec_dict = spec.model_dump()
        spec_dict["numerics"]["time"]["start_time"] = {"value": 5.0, "unit": "s"}
        spec = SimulationStudySpec(**spec_dict)

        # Build a patch with a blocking clarification
        patch = make_patch(
            spec,
            operations=[],
            clarifications=[{
                "clarification_id": "time_clarify_1",
                "question": "你希望结束时间为15秒，还是从5秒继续计算15秒到20秒？",
                "alternatives": [
                    {"label": "结束时间为15秒", "operations": [
                        {"op": "replace", "path": "/numerics/time/end_time",
                         "value": 15.0, "source_quote": "再运行15秒", "confidence": 0.9}
                    ]},
                    {"label": "持续计算15秒到20秒", "operations": [
                        {"op": "replace", "path": "/numerics/time/end_time",
                         "value": 20.0, "source_quote": "再运行15秒", "confidence": 0.9}
                    ]},
                ],
                "affected_paths": ["/numerics/time/end_time"],
                "blocking": True,
            }],
        )
        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        # Blocking clarification means no change applied yet
        assert len(result.clarifications) > 0
        assert result.new_spec is None or result.new_spec.version == spec.version

        # After user selects alternative 2 (end_time=20), apply that patch
        alt_patch = make_patch(spec, operations=[
            PatchOperation(op="replace", path="/numerics/time/end_time",
                           value=20.0, source_quote="持续计算15秒到20秒", confidence=0.95),
        ])
        result2 = engine.process_patch(alt_patch, spec)
        assert result2.errors == []
        assert result2.new_spec is not None
        assert result2.new_spec.numerics.time.end_time.value == 20.0


# ---------------------------------------------------------------------------
# 4-7: delta_t and adaptive timestep
# ---------------------------------------------------------------------------

class TestDeltaT:
    def test_set_delta_t_0_005(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(op="replace", path="/numerics/time/delta_t",
                           value=0.005, source_quote="时间步0.005秒", confidence=0.99),
        ])
        assert result.new_spec.numerics.time.delta_t.value == 0.005
        assert result.new_spec.numerics.time.end_time.value == 10.0  # unchanged

    def test_halve_delta_t(self) -> None:
        spec = make_study_spec()
        original_dt = spec.numerics.time.delta_t.value
        assert original_dt == 0.01
        result = _apply(spec, [
            PatchOperation(
                op="replace",
                path="/numerics/time/delta_t",
                value={"expression": {"operator": "multiply",
                                       "path": "/numerics/time/delta_t", "factor": 0.5}},
                source_quote="时间步减半",
                confidence=0.95,
            ),
        ])
        new_dt = result.new_spec.numerics.time.delta_t.value
        assert new_dt == 0.005, f"delta_t should be halved to 0.005, got {new_dt}"

    def test_enable_adaptive_timestep(self) -> None:
        spec = make_study_spec()
        assert spec.numerics.time.adaptive is False
        result = _apply(spec, [
            PatchOperation(op="replace", path="/numerics/time/adaptive",
                           value=True, source_quote="自适应时间步", confidence=0.99),
            PatchOperation(op="replace", path="/numerics/time/max_courant",
                           value=0.5, source_quote="自适应时间步", confidence=0.99),
        ])
        assert result.new_spec.numerics.time.adaptive is True
        assert result.new_spec.numerics.time.max_courant == 0.5

    def test_set_max_co_0_5(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(op="replace", path="/numerics/time/max_courant",
                           value=0.5, source_quote="maxCo=0.5", confidence=0.99),
        ])
        assert result.new_spec.numerics.time.max_courant == 0.5
        assert result.new_spec.numerics.time.delta_t.value == 0.01  # unchanged


# ---------------------------------------------------------------------------
# 8-9: write control
# ---------------------------------------------------------------------------

class TestWriteControl:
    def test_write_every_0_1s(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(op="replace", path="/numerics/time/write_control",
                           value="runTime", source_quote="每0.1秒写出", confidence=0.99),
            PatchOperation(op="replace", path="/numerics/time/write_interval",
                           value=0.1, source_quote="每0.1秒写出", confidence=0.99),
        ])
        assert result.new_spec.numerics.time.write_control == "runTime"
        assert result.new_spec.numerics.time.write_interval.value == 0.1

    def test_write_every_20_steps(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(op="replace", path="/numerics/time/write_control",
                           value="timeStep", source_quote="每20步写出", confidence=0.99),
            PatchOperation(op="replace", path="/numerics/time/write_interval",
                           value=20, source_quote="每20步写出", confidence=0.99),
        ])
        assert result.new_spec.numerics.time.write_control == "timeStep"
        assert result.new_spec.numerics.time.write_interval.value == 20


# ---------------------------------------------------------------------------
# 10-11: statistics windows
# ---------------------------------------------------------------------------

class TestStatisticsWindows:
    def test_add_statistics_window_last_5s(self) -> None:
        spec = make_study_spec()
        # First set end_time to 15 so the statistics window 10-15 is valid
        spec_dict = spec.model_dump()
        spec_dict["numerics"]["time"]["end_time"] = {"value": 15.0, "unit": "s"}
        spec = SimulationStudySpec(**spec_dict)
        assert len(spec.numerics.time.statistics_windows) == 0
        result = _apply(spec, [
            PatchOperation(
                op="append_unique",
                path="/numerics/time/statistics_windows/-",
                value={"start": {"value": 10.0, "unit": "s"},
                       "end": {"value": 15.0, "unit": "s"},
                       "label": "last_5s"},
                source_quote="只统计最后5秒",
                confidence=0.95,
            ),
        ])
        assert len(result.new_spec.numerics.time.statistics_windows) == 1
        w = result.new_spec.numerics.time.statistics_windows[0]
        assert w.start.value == 10.0
        assert w.end.value == 15.0
        assert w.label == "last_5s"

    def test_remove_statistics_window(self) -> None:
        """Create a spec with a statistics window, then remove it."""
        spec = make_study_spec()
        spec_dict = spec.model_dump()
        spec_dict["numerics"]["time"]["statistics_windows"] = [
            {"start": {"value": 5.0, "unit": "s"},
             "end": {"value": 10.0, "unit": "s"},
             "label": "early"},
        ]
        spec = SimulationStudySpec(**spec_dict)
        assert len(spec.numerics.time.statistics_windows) == 1

        result = _apply(spec, [
            PatchOperation(op="remove", path="/numerics/time/statistics_windows/0",
                           source_quote="删除旧统计窗口", confidence=0.95),
        ])
        assert len(result.new_spec.numerics.time.statistics_windows) == 0


# ---------------------------------------------------------------------------
# 12: undo
# ---------------------------------------------------------------------------

class TestUndoTimeChange:
    def test_undo_end_time_change(self) -> None:
        """Apply end_time=15, then undo it back to 10."""
        spec = make_study_spec()
        assert spec.numerics.time.end_time.value == 10.0

        # Apply patch: end_time -> 15
        result1 = _apply(spec, [
            PatchOperation(op="replace", path="/numerics/time/end_time",
                           value=15.0, source_quote="仿真时间设为15秒", confidence=0.99),
        ])
        spec_v2 = result1.new_spec
        assert spec_v2.numerics.time.end_time.value == 15.0

        # Undo: create a reverse patch
        from fluid_scientist.spec_editing.undo import UndoEngine
        undo_engine = UndoEngine()
        original_patch = make_patch(spec, operations=[
            PatchOperation(op="replace", path="/numerics/time/end_time",
                           value=15.0, source_quote="仿真时间设为15秒", confidence=0.99),
        ])
        reverse_patch = undo_engine.create_reverse_patch(original_patch, spec.model_dump())

        engine = PatchEngine()
        result2 = engine.process_patch(reverse_patch, spec_v2)
        assert result2.errors == []
        assert result2.new_spec is not None
        assert result2.new_spec.numerics.time.end_time.value == 10.0
