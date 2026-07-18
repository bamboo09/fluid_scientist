"""E2E tests: observation modifications via the generic PatchEngine.

These tests prove that the generic PatchEngine handles observation target,
probe, postprocessing, and statistics-window modifications without any
field-specific if/else logic.

Default spec observation layout:
  - targets:        [ {target_id: "cd", metric: "cd", ...} ]
  - probes:         [ {probe_id: "wake_probe", location: {x:8,y:2.5,z:0}, field: "U"} ]
  - postprocessing:  [ "streamlines" ]

14 scenarios covered:
  1. Add Cd target
  2. Add Cl target
  3. Add Strouhal target
  4. Add FFT target
  5. Add point probe
  6. Move point probe
  7. Remove point probe
  8. Add section mean target
  9. Add wall shear target
 10. Add y+ target
 11. Add vorticity animation (target + postprocessing)
 12. Remove pressure contour from postprocessing
 13. Add time-average target
 14. Add output time window (statistics_windows)
"""
from __future__ import annotations

from tests.e2e.model_editing.conftest import make_study_spec, make_patch
from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import Quantity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec_with_end_time(end_time: float):
    """Build a spec with a custom end_time (needed for statistics windows)."""
    base = make_study_spec()
    return base.model_copy(update={
        "numerics": base.numerics.model_copy(update={
            "time": base.numerics.time.model_copy(update={
                "end_time": Quantity(value=end_time, unit="s"),
            }),
        }),
    })


def _spec_with_postprocessing(items: list[str]):
    """Build a spec with a specific postprocessing list."""
    base = make_study_spec()
    return base.model_copy(update={
        "observations": base.observations.model_copy(update={
            "postprocessing": items,
        }),
    })


# ---------------------------------------------------------------------------
# Tests 1-4: add observation targets
# ---------------------------------------------------------------------------

def test_add_cd():
    """Append_unique to /observations/targets/- with a Cd target."""
    spec = make_study_spec()
    original_count = len(spec.observations.targets)
    original_probes = list(spec.observations.probes)

    new_target = {
        "target_id": "cd",
        "metric": "cd",
        "parameters": {"patches": ["cylinder"]},
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加阻力系数观测",
                confidence=0.95,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    targets = result.new_spec.observations.targets
    assert len(targets) == original_count + 1
    assert any(t.target_id == "cd" for t in targets)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.probes) == original_probes


def test_add_cl():
    """Append_unique to /observations/targets/- with a Cl target."""
    spec = make_study_spec()
    original_count = len(spec.observations.targets)
    original_probes = list(spec.observations.probes)

    new_target = {
        "target_id": "cl",
        "metric": "cl",
        "parameters": {"patches": ["cylinder"]},
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加升力系数观测",
                confidence=0.95,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    targets = result.new_spec.observations.targets
    assert len(targets) == original_count + 1
    assert any(t.target_id == "cl" for t in targets)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.probes) == original_probes


def test_add_strouhal():
    """Append_unique to /observations/targets/- with a Strouhal target."""
    spec = make_study_spec()
    original_count = len(spec.observations.targets)
    original_probes = list(spec.observations.probes)

    new_target = {
        "target_id": "st",
        "metric": "strouhal",
        "parameters": {"signal_source": "cl_time_series"},
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加Strouhal数观测",
                confidence=0.95,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    targets = result.new_spec.observations.targets
    assert len(targets) == original_count + 1
    assert any(t.target_id == "st" for t in targets)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.probes) == original_probes


def test_add_fft():
    """Append_unique to /observations/targets/- with an FFT custom target."""
    spec = make_study_spec()
    original_count = len(spec.observations.targets)
    original_probes = list(spec.observations.probes)

    new_target = {
        "target_id": "fft",
        "metric": "custom",
        "parameters": {"type": "fft", "source": "cl_time_series"},
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加FFT频谱分析",
                confidence=0.9,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    targets = result.new_spec.observations.targets
    assert len(targets) == original_count + 1
    assert any(t.target_id == "fft" for t in targets)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.probes) == original_probes


# ---------------------------------------------------------------------------
# Tests 5-7: point probe operations
# ---------------------------------------------------------------------------

def test_add_point_probe():
    """Append_unique to /observations/probes/- with a new point probe."""
    spec = make_study_spec()
    original_count = len(spec.observations.probes)
    original_targets = list(spec.observations.targets)

    new_probe = {
        "probe_id": "probe_2",
        "location": {"x": 9, "y": 2.5, "z": 0},
        "field": "U",
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/probes/-",
                value=new_probe,
                source_quote="增加探针probe_2",
                confidence=0.95,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    probes = result.new_spec.observations.probes
    assert len(probes) == original_count + 1
    assert any(p.probe_id == "probe_2" for p in probes)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.targets) == original_targets


def test_move_point_probe():
    """Replace /observations/probes/0/location with {x: 6, y: 4, z: 0}."""
    spec = make_study_spec()
    original_probe_id = spec.observations.probes[0].probe_id
    original_field = spec.observations.probes[0].field

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="replace",
            path="/observations/probes/0/location",
            value={"x": 6, "y": 4, "z": 0},
            source_quote="移动探针位置到(6,4,0)",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    probe = result.new_spec.observations.probes[0]
    assert probe.location == {"x": 6, "y": 4, "z": 0}

    # Unrelated fields unchanged.
    assert probe.probe_id == original_probe_id
    assert probe.field == original_field


def test_remove_point_probe():
    """Remove /observations/probes/0."""
    spec = make_study_spec()
    original_count = len(spec.observations.probes)
    original_targets = list(spec.observations.targets)

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="remove",
            path="/observations/probes/0",
            source_quote="删除探针probe_0",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    probes = result.new_spec.observations.probes
    assert len(probes) == original_count - 1

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.targets) == original_targets


# ---------------------------------------------------------------------------
# Tests 8-10: additional observation targets
# ---------------------------------------------------------------------------

def test_add_section_mean():
    """Append_unique to /observations/targets/- with a section mean target."""
    spec = make_study_spec()
    original_count = len(spec.observations.targets)
    original_probes = list(spec.observations.probes)

    new_target = {
        "target_id": "section_mean",
        "metric": "section_mean_velocity",
        "parameters": {"location": {"x": 10, "y": 0, "z": 0}},
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加截面平均速度观测",
                confidence=0.9,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    targets = result.new_spec.observations.targets
    assert len(targets) == original_count + 1
    assert any(t.target_id == "section_mean" for t in targets)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.probes) == original_probes


def test_add_wall_shear():
    """Append_unique to /observations/targets/- with a wall shear target."""
    spec = make_study_spec()
    original_count = len(spec.observations.targets)
    original_probes = list(spec.observations.probes)

    new_target = {
        "target_id": "wall_shear",
        "metric": "wall_shear",
        "parameters": {"patches": ["cylinder"]},
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加壁面剪切应力观测",
                confidence=0.9,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    targets = result.new_spec.observations.targets
    assert len(targets) == original_count + 1
    assert any(t.target_id == "wall_shear" for t in targets)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.probes) == original_probes


def test_add_y_plus():
    """Append_unique to /observations/targets/- with a y+ target."""
    spec = make_study_spec()
    original_count = len(spec.observations.targets)
    original_probes = list(spec.observations.probes)

    new_target = {
        "target_id": "y_plus",
        "metric": "y_plus",
        "parameters": {"patches": ["cylinder"]},
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加y+观测",
                confidence=0.9,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    targets = result.new_spec.observations.targets
    assert len(targets) == original_count + 1
    assert any(t.target_id == "y_plus" for t in targets)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.probes) == original_probes


# ---------------------------------------------------------------------------
# Tests 11-12: postprocessing operations
# ---------------------------------------------------------------------------

def test_add_vorticity_animation():
    """Add vorticity target AND 'vorticity_animation' to postprocessing."""
    spec = make_study_spec()
    original_target_count = len(spec.observations.targets)
    original_postprocessing = list(spec.observations.postprocessing)

    new_target = {
        "target_id": "vort",
        "metric": "vorticity",
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加涡量动画",
                confidence=0.9,
            ),
            PatchOperation(
                op="append_unique",
                path="/observations/postprocessing/-",
                value="vorticity_animation",
                source_quote="增加涡量动画",
                confidence=0.9,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Target added.
    targets = result.new_spec.observations.targets
    assert len(targets) == original_target_count + 1
    assert any(t.target_id == "vort" for t in targets)

    # Postprocessing entry added.
    postproc = result.new_spec.observations.postprocessing
    assert "vorticity_animation" in postproc
    # Original postprocessing entries preserved.
    for item in original_postprocessing:
        assert item in postproc


def test_remove_pressure_plot():
    """Remove 'pressure_contour' from postprocessing (index 1)."""
    spec = _spec_with_postprocessing(["streamlines", "pressure_contour"])
    assert spec.observations.postprocessing == ["streamlines", "pressure_contour"]
    original_targets = list(spec.observations.targets)

    patch = make_patch(spec, operations=[
        PatchOperation(
            op="remove",
            path="/observations/postprocessing/1",
            source_quote="移除压力云图后处理",
            confidence=0.99,
        ),
    ])

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied: pressure_contour removed.
    postproc = result.new_spec.observations.postprocessing
    assert "pressure_contour" not in postproc
    assert "streamlines" in postproc

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.targets) == original_targets


# ---------------------------------------------------------------------------
# Tests 13-14: advanced observation targets and time windows
# ---------------------------------------------------------------------------

def test_add_time_average():
    """Append_unique to /observations/targets/- with a fieldAverage target."""
    spec = make_study_spec()
    original_count = len(spec.observations.targets)
    original_probes = list(spec.observations.probes)

    new_target = {
        "target_id": "time_avg",
        "metric": "custom",
        "parameters": {
            "type": "fieldAverage",
            "fields": ["U", "p"],
        },
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/observations/targets/-",
                value=new_target,
                source_quote="增加时间平均后处理",
                confidence=0.9,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    targets = result.new_spec.observations.targets
    assert len(targets) == original_count + 1
    assert any(t.target_id == "time_avg" for t in targets)

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.probes) == original_probes


def test_add_output_time_window():
    """Append_unique to /numerics/time/statistics_windows/- with a TimeWindow."""
    # Need end_time >= 15 for the window [8, 15] to be valid.
    spec = _spec_with_end_time(20.0)
    original_targets = list(spec.observations.targets)

    time_window = {
        "start": {"value": 8, "unit": "s"},
        "end": {"value": 15, "unit": "s"},
        "label": "output_window",
    }

    patch = make_patch(
        spec,
        operations=[
            PatchOperation(
                op="append_unique",
                path="/numerics/time/statistics_windows/-",
                value=time_window,
                source_quote="增加输出时间窗口[8,15]",
                confidence=0.95,
            ),
        ],
        untouched_guarantee=False,
    )

    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Patch failed: {result.errors}"
    assert result.new_spec is not None

    # Change applied.
    windows = result.new_spec.numerics.time.statistics_windows
    assert len(windows) >= 1
    win = windows[-1]
    assert win.label == "output_window"
    assert win.start.value == 8
    assert win.end.value == 15

    # Unrelated field unchanged.
    assert list(result.new_spec.observations.targets) == original_targets
