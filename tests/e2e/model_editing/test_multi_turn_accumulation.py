"""Multi-turn accumulation test: 10 consecutive patches in one session.

Proves that the PatchEngine preserves ALL modifications across 10 turns
without losing state.  This is the critical acceptance test for the
"consecutive modifications lose state" failure mode.
"""
from __future__ import annotations

from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import SimulationStudySpec

from .conftest import make_patch, make_study_spec


def test_ten_consecutive_patches_preserved() -> None:
    """Apply 10 patches in sequence and verify all changes survive."""

    # --- Turn 0: Create initial spec with cylinder + triangle obstacle ---
    spec = make_study_spec()
    engine = PatchEngine()

    # Add triangle obstacle to the initial spec
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(
                op="add",
                path="/geometry/entities/wall_obstacle",
                value={
                    "entity_id": "wall_obstacle",
                    "semantic_type": "triangle_2d",
                    "primitive": {"type": "triangle", "base_width": 0.8, "height": 0.5},
                    "polygon_vertices": None,
                    "original_user_semantics": "三角障碍",
                    "placement": {"x": {"value": 4.0, "unit": "m", "status": "user_explicit",
                                         "source_turn_ids": ["t0"], "confidence": 0.9,
                                         "derivation_id": None, "last_modified_by_patch": None},
                                  "y": {"value": 0.0, "unit": "m", "status": "user_explicit",
                                        "source_turn_ids": ["t0"], "confidence": 0.9,
                                        "derivation_id": None, "last_modified_by_patch": None},
                                  "orientation": "apex_up", "attachment": "bottom_wall"},
                },
                source_quote="底部三角障碍",
                confidence=0.95,
            ),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 0 failed: {r.errors}"
    spec = r.new_spec
    assert "wall_obstacle" in spec.geometry.entities

    # --- Turn 1: 仿真时间15秒 ---
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(op="replace", path="/numerics/time/end_time",
                           value=15.0, source_quote="仿真时间设为15秒", confidence=0.99),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 1 failed: {r.errors}"
    spec = r.new_spec
    assert spec.numerics.time.end_time.value == 15.0

    # --- Turn 2: 时间步0.005秒 ---
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(op="replace", path="/numerics/time/delta_t",
                           value=0.005, source_quote="时间步0.005秒", confidence=0.99),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 2 failed: {r.errors}"
    spec = r.new_spec
    assert spec.numerics.time.delta_t.value == 0.005

    # --- Turn 3: 三角改矩形 ---
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(
                op="replace",
                path="/geometry/entities/wall_obstacle/primitive",
                value={"type": "rectangle", "width": 0.8, "height": 0.5},
                source_quote="三角改矩形",
                confidence=0.99,
            ),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 3 failed: {r.errors}"
    spec = r.new_spec
    assert spec.geometry.entities["wall_obstacle"].primitive["type"] == "rectangle"

    # --- Turn 4: 空气改水 ---
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(op="replace", path="/physics/material",
                           value={"value": "water", "status": "user_explicit"},
                           source_quote="空气改水", confidence=0.99),
            PatchOperation(op="replace", path="/physics/kinematic_viscosity",
                           value={"value": 1.0e-6, "unit": "m^2/s", "status": "user_explicit"},
                           source_quote="空气改水", confidence=0.99),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 4 failed: {r.errors}"
    spec = r.new_spec
    assert spec.physics.material.value == "water"

    # --- Turn 5: 增加出口前1米探针 ---
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/probes/-",
                value={"probe_id": "outlet_probe",
                       "location": {"x": 9.0, "y": 2.5, "z": 0},
                       "field": "U"},
                source_quote="增加出口前1米的速度探针",
                confidence=0.95,
            ),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 5 failed: {r.errors}"
    spec = r.new_spec
    probe_ids = [p.probe_id for p in spec.observations.probes]
    assert "outlet_probe" in probe_ids

    # --- Turn 6: 删除压力云图 ---
    # First, add a postprocessing entry to remove
    r_add = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(op="append_unique", path="/observations/postprocessing/-",
                           value="pressure_contour", source_quote="添加压力云图", confidence=0.9),
        ]),
        spec,
    )
    assert r_add.errors == []
    spec = r_add.new_spec
    assert "pressure_contour" in spec.observations.postprocessing

    # Find the index of "pressure_contour" to remove it
    pp_index = spec.observations.postprocessing.index("pressure_contour")
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(op="remove",
                           path=f"/observations/postprocessing/{pp_index}",
                           source_quote="删除压力云图", confidence=0.95),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 6 failed: {r.errors}"
    spec = r.new_spec

    # --- Turn 7: 只分析最后5秒 ---
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(
                op="append_unique",
                path="/numerics/time/statistics_windows/-",
                value={"start": {"value": 10.0, "unit": "s"},
                       "end": {"value": 15.0, "unit": "s"},
                       "label": "last_5s"},
                source_quote="只分析最后5秒",
                confidence=0.95,
            ),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 7 failed: {r.errors}"
    spec = r.new_spec
    assert len(spec.numerics.time.statistics_windows) >= 1
    w = spec.numerics.time.statistics_windows[-1]
    assert w.start.value == 10.0
    assert w.end.value == 15.0

    # --- Turn 8: 增加Cd观测 ---
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value={"target_id": "cd_target", "metric": "cd",
                       "parameters": {}, "function_object_type": "forceCoeffs"},
                source_quote="增加阻力系数观测",
                confidence=0.95,
            ),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 8 failed: {r.errors}"
    spec = r.new_spec
    target_ids = [t.target_id for t in spec.observations.targets]
    assert "cd_target" in target_ids

    # --- Turn 9: 改入口速度0.2 ---
    r = engine.process_patch(
        make_patch(spec, operations=[
            PatchOperation(op="replace", path="/boundaries/conditions/0/parameters",
                           value={"velocity": 0.2}, source_quote="入口速度0.2", confidence=0.99),
        ]),
        spec,
    )
    assert r.errors == [], f"Turn 9 failed: {r.errors}"
    spec = r.new_spec
    assert spec.boundaries.conditions[0].parameters["velocity"] == 0.2

    # ====================================================================
    # Final verification: ALL 10 modifications must be present
    # ====================================================================
    assert spec.version == 12, f"Expected version 12, got {spec.version}"

    # Turn 0: wall_obstacle entity exists
    assert "wall_obstacle" in spec.geometry.entities, "Turn 0 lost: wall_obstacle missing"

    # Turn 1: end_time = 15
    assert spec.numerics.time.end_time.value == 15.0, "Turn 1 lost: end_time != 15"

    # Turn 2: delta_t = 0.005
    assert spec.numerics.time.delta_t.value == 0.005, "Turn 2 lost: delta_t != 0.005"

    # Turn 3: obstacle is rectangle
    assert spec.geometry.entities["wall_obstacle"].primitive["type"] == "rectangle", \
        "Turn 3 lost: obstacle not rectangle"

    # Turn 4: material is water
    assert spec.physics.material.value == "water", "Turn 4 lost: material != water"

    # Turn 5: outlet_probe exists
    probe_ids = [p.probe_id for p in spec.observations.probes]
    assert "outlet_probe" in probe_ids, "Turn 5 lost: outlet_probe missing"

    # Turn 6: pressure_contour removed (should not be in postprocessing)
    # Note: it may have been at index 0 and removed, so check it's gone
    # The original "streamlines" might also have been removed.
    # Just verify the postprocessing list doesn't contain "pressure_contour"
    assert "pressure_contour" not in spec.observations.postprocessing, \
        "Turn 6 lost: pressure_contour still present"

    # Turn 7: statistics window 10-15
    sw = spec.numerics.time.statistics_windows
    has_window = any(w.start.value == 10.0 and w.end.value == 15.0 for w in sw)
    assert has_window, "Turn 7 lost: statistics window missing"

    # Turn 8: cd_target exists
    target_ids = [t.target_id for t in spec.observations.targets]
    assert "cd_target" in target_ids, "Turn 8 lost: cd_target missing"

    # Turn 9: inlet velocity = 0.2
    assert spec.boundaries.conditions[0].parameters["velocity"] == 0.2, \
        "Turn 9 lost: inlet velocity != 0.2"
