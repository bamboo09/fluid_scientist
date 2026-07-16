"""Test: two (three) consecutive edits without state loss.

This test reproduces the known issue where consecutive edits to a spec
could lose state from earlier edits.  The new spec-editing system
guarantees that every patch builds on the previous version and no
state is lost between patches.

Applies three patches in sequence:
1. "仿真时间设为15秒" — replace end_time 10 -> 15
2. "时间步改为0.005秒" — replace delta_t 0.01 -> 0.005
3. "增加出口前1米的速度探针" — add a velocity probe near the outlet

Verifies:
* ALL three changes are present in the final spec.
* version is 4 (initial v1 + 3 patches).
* No state was lost between patches (earlier changes survive).
"""
from __future__ import annotations

import pytest

from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import SimulationStudySpec

from .conftest import make_patch, make_study_spec


class TestTwoConsecutiveEdits:
    """Verify that consecutive patches accumulate correctly."""

    def test_three_consecutive_edits_all_present(self) -> None:
        """All three changes are present after applying three patches."""
        spec = make_study_spec()

        # --- Precondition checks ---
        assert spec.numerics.time.end_time.value == 10.0
        assert spec.numerics.time.delta_t.value == 0.01
        assert len(spec.observations.probes) == 1

        engine = PatchEngine()

        # --- Patch 1: "仿真时间设为15秒" ---
        patch1 = make_patch(
            spec,
            patch_id="patch_001",
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )
        result1 = engine.process_patch(patch1, spec)
        assert result1.errors == [], f"Patch 1 failed: {result1.errors}"
        assert result1.new_spec is not None
        spec_v2 = result1.new_spec

        # Verify patch 1 took effect.
        assert spec_v2.numerics.time.end_time.value == 15.0
        assert spec_v2.version == 2

        # --- Patch 2: "时间步改为0.005秒" ---
        patch2 = make_patch(
            spec_v2,
            patch_id="patch_002",
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/delta_t",
                    value=0.005,
                    source_quote="时间步改为0.005秒",
                ),
            ],
        )
        result2 = engine.process_patch(patch2, spec_v2)
        assert result2.errors == [], f"Patch 2 failed: {result2.errors}"
        assert result2.new_spec is not None
        spec_v3 = result2.new_spec

        # Verify patch 2 took effect AND patch 1's change survived.
        assert spec_v3.numerics.time.delta_t.value == 0.005
        assert spec_v3.numerics.time.end_time.value == 15.0, (
            "end_time from patch 1 must survive patch 2"
        )
        assert spec_v3.version == 3

        # --- Patch 3: "增加出口前1米的速度探针" ---
        new_probe = {
            "probe_id": "outlet_velocity_probe",
            "location": {"x": 9.0, "y": 2.5, "z": 0.0},
            "field": "U",
        }
        patch3 = make_patch(
            spec_v3,
            patch_id="patch_003",
            operations=[
                PatchOperation(
                    op="add",
                    path="/observations/probes/-",
                    value=new_probe,
                    source_quote="增加出口前1米的速度探针",
                ),
            ],
            untouched_guarantee=False,  # Adding to array creates new paths.
        )
        result3 = engine.process_patch(patch3, spec_v3)
        assert result3.errors == [], f"Patch 3 failed: {result3.errors}"
        assert result3.new_spec is not None
        spec_v4 = result3.new_spec

        # --- Final assertions: ALL three changes present ---
        # Change 1: end_time = 15.0
        assert spec_v4.numerics.time.end_time.value == 15.0, (
            "end_time from patch 1 must survive patches 2 and 3"
        )
        # Change 2: delta_t = 0.005
        assert spec_v4.numerics.time.delta_t.value == 0.005, (
            "delta_t from patch 2 must survive patch 3"
        )
        # Change 3: new probe added
        assert len(spec_v4.observations.probes) == 2, (
            f"Expected 2 probes, got {len(spec_v4.observations.probes)}"
        )
        outlet_probe = spec_v4.observations.probes[1]
        assert outlet_probe.probe_id == "outlet_velocity_probe"
        assert outlet_probe.location["x"] == 9.0
        assert outlet_probe.location["y"] == 2.5
        assert outlet_probe.field == "U"

    def test_final_version_is_4(self) -> None:
        """The final version is 4 (initial v1 + 3 patches)."""
        spec = make_study_spec()
        engine = PatchEngine()

        # Patch 1
        p1 = make_patch(
            spec,
            patch_id="p1",
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )
        r1 = engine.process_patch(p1, spec)
        assert r1.new_spec is not None
        assert r1.new_spec.version == 2

        # Patch 2
        p2 = make_patch(
            r1.new_spec,
            patch_id="p2",
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/delta_t",
                    value=0.005,
                    source_quote="时间步改为0.005秒",
                ),
            ],
        )
        r2 = engine.process_patch(p2, r1.new_spec)
        assert r2.new_spec is not None
        assert r2.new_spec.version == 3

        # Patch 3
        p3 = make_patch(
            r2.new_spec,
            patch_id="p3",
            operations=[
                PatchOperation(
                    op="add",
                    path="/observations/probes/-",
                    value={
                        "probe_id": "outlet_probe",
                        "location": {"x": 9.0, "y": 2.5, "z": 0.0},
                        "field": "U",
                    },
                    source_quote="增加出口前1米的速度探针",
                ),
            ],
            untouched_guarantee=False,
        )
        r3 = engine.process_patch(p3, r2.new_spec)
        assert r3.new_spec is not None
        assert r3.new_spec.version == 4, (
            f"Final version should be 4, got {r3.new_spec.version}"
        )

    def test_no_state_lost_between_patches(self) -> None:
        """Earlier edits survive later edits — no state loss."""
        spec = make_study_spec()
        engine = PatchEngine()

        # Capture original values that should NOT be changed by any patch.
        original_start_time = spec.numerics.time.start_time.value
        original_solver = spec.numerics.solver
        original_material = spec.physics.material.value
        original_cylinder_type = spec.geometry.entities["cylinder"].semantic_type

        # Apply three patches.
        p1 = make_patch(
            spec,
            patch_id="p1",
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )
        r1 = engine.process_patch(p1, spec)
        assert r1.new_spec is not None

        p2 = make_patch(
            r1.new_spec,
            patch_id="p2",
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/delta_t",
                    value=0.005,
                    source_quote="时间步改为0.005秒",
                ),
            ],
        )
        r2 = engine.process_patch(p2, r1.new_spec)
        assert r2.new_spec is not None

        p3 = make_patch(
            r2.new_spec,
            patch_id="p3",
            operations=[
                PatchOperation(
                    op="add",
                    path="/observations/probes/-",
                    value={
                        "probe_id": "outlet_probe",
                        "location": {"x": 9.0, "y": 2.5, "z": 0.0},
                        "field": "U",
                    },
                    source_quote="增加出口前1米的速度探针",
                ),
            ],
            untouched_guarantee=False,
        )
        r3 = engine.process_patch(p3, r2.new_spec)
        assert r3.new_spec is not None
        final = r3.new_spec

        # Untouched fields survive all three patches.
        assert final.numerics.time.start_time.value == original_start_time
        assert final.numerics.solver == original_solver
        assert final.physics.material.value == original_material
        assert final.geometry.entities["cylinder"].semantic_type == original_cylinder_type

    def test_patch_history_records_all_three(self) -> None:
        """The patch history contains all three patch records."""
        spec = make_study_spec()
        engine = PatchEngine()

        for i, (path, value, quote, guarantee) in enumerate([
            ("/numerics/time/end_time", 15.0, "仿真时间设为15秒", True),
            ("/numerics/time/delta_t", 0.005, "时间步改为0.005秒", True),
            ("/observations/probes/-", {
                "probe_id": "outlet_probe",
                "location": {"x": 9.0, "y": 2.5, "z": 0.0},
                "field": "U",
            }, "增加出口前1米的速度探针", False),
        ], start=1):
            p = make_patch(
                spec,
                patch_id=f"p{i}",
                operations=[
                    PatchOperation(
                        op="replace" if guarantee else "add",
                        path=path,
                        value=value,
                        source_quote=quote,
                    ),
                ],
                untouched_guarantee=guarantee,
            )
            r = engine.process_patch(p, spec)
            assert r.errors == []
            assert r.new_spec is not None
            spec = r.new_spec

        history = engine.history
        records = history.list_for_spec(spec.spec_id)
        assert len(records) == 3, (
            f"Expected 3 patch records, got {len(records)}"
        )
        assert records[0].patch_id == "p1"
        assert records[1].patch_id == "p2"
        assert records[2].patch_id == "p3"
