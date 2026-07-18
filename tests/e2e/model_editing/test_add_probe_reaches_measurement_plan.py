"""Test: adding a probe reaches the measurement plan (observations).

This test reproduces the known issue where a probe added by the user
did not correctly appear in the spec's observation block.  The new
spec-editing system ensures that an ``add`` operation targeting
``/observations/probes/-`` correctly appends a new :class:`ProbeSpec`
to the spec's observation probes list.

Verifies:
* The probe appears in ``spec.observations.probes``.
* The probe has a ``probe_id``.
* Other observations (targets, postprocessing) are not affected.
"""
from __future__ import annotations

import pytest

from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import (
    ObservationDefinition,
    ObservationTarget,
    ProbeSpec,
)

from .conftest import make_patch, make_study_spec


def _make_spec_without_probes() -> "SimulationStudySpec":
    """Build a spec with no probes but with observation targets."""
    spec = make_study_spec()
    return spec.model_copy(update={
        "observations": ObservationDefinition(
            targets=[
                ObservationTarget(
                    target_id="drag",
                    metric="cd",
                    parameters={"patches": ["cylinder"]},
                    function_object_type="forceCoeffs",
                ),
            ],
            probes=[],  # No probes initially.
            postprocessing=["streamlines"],
        ),
    })


class TestAddProbeReachesMeasurementPlan:
    """Verify that adding a probe reaches the observations block."""

    def test_probe_appears_in_observations(self) -> None:
        """The added probe appears in spec.observations.probes."""
        spec = _make_spec_without_probes()
        assert len(spec.observations.probes) == 0, "Precondition: no probes"

        new_probe = {
            "probe_id": "outlet_velocity_probe",
            "location": {"x": 9.0, "y": 2.5, "z": 0.0},
            "field": "U",
        }
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="add",
                    path="/observations/probes/-",
                    value=new_probe,
                    source_quote="增加出口前1米的速度探针",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == [], f"Patch failed: {result.errors}"
        assert result.new_spec is not None

        probes = result.new_spec.observations.probes
        assert len(probes) == 1, f"Expected 1 probe, got {len(probes)}"

        probe = probes[0]
        assert probe.probe_id == "outlet_velocity_probe"
        assert probe.location["x"] == 9.0
        assert probe.location["y"] == 2.5
        assert probe.field == "U"

    def test_probe_has_probe_id(self) -> None:
        """The added probe has a non-empty probe_id."""
        spec = _make_spec_without_probes()

        new_probe = {
            "probe_id": "wake_probe_2",
            "location": {"x": 9.0, "y": 2.5, "z": 0.0},
            "field": "U",
        }
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="add",
                    path="/observations/probes/-",
                    value=new_probe,
                    source_quote="增加出口前1米的速度探针",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        probe = result.new_spec.observations.probes[0]
        assert probe.probe_id, "probe_id must be non-empty"
        assert probe.probe_id == "wake_probe_2"

    def test_other_observations_not_affected(self) -> None:
        """Existing observation targets and postprocessing are preserved."""
        spec = _make_spec_without_probes()

        # Capture original observation data.
        original_targets = spec.observations.targets
        original_postprocessing = spec.observations.postprocessing
        assert len(original_targets) == 1
        assert original_targets[0].target_id == "drag"
        assert "streamlines" in original_postprocessing

        new_probe = {
            "probe_id": "outlet_probe",
            "location": {"x": 9.0, "y": 2.5, "z": 0.0},
            "field": "U",
        }
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="add",
                    path="/observations/probes/-",
                    value=new_probe,
                    source_quote="增加出口前1米的速度探针",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        obs = result.new_spec.observations

        # Targets unchanged.
        assert len(obs.targets) == len(original_targets)
        assert obs.targets[0].target_id == "drag"
        assert obs.targets[0].metric == "cd"
        assert obs.targets[0].function_object_type == "forceCoeffs"

        # Postprocessing unchanged.
        assert obs.postprocessing == original_postprocessing

        # Only probes changed.
        assert len(obs.probes) == 1

    def test_multiple_probes_can_be_added(self) -> None:
        """Multiple probes can be added in sequence."""
        spec = _make_spec_without_probes()
        engine = PatchEngine()

        probe_data = [
            ("probe_a", 9.0, 2.5),
            ("probe_b", 8.0, 3.0),
            ("probe_c", 7.0, 4.0),
        ]

        current_spec = spec
        for probe_id, x, y in probe_data:
            patch = make_patch(
                current_spec,
                patch_id=f"patch_{probe_id}",
                operations=[
                    PatchOperation(
                        op="add",
                        path="/observations/probes/-",
                        value={
                            "probe_id": probe_id,
                            "location": {"x": x, "y": y, "z": 0.0},
                            "field": "U",
                        },
                        source_quote=f"增加探针{probe_id}",
                    ),
                ],
                untouched_guarantee=False,
            )
            result = engine.process_patch(patch, current_spec)
            assert result.errors == []
            assert result.new_spec is not None
            current_spec = result.new_spec

        assert len(current_spec.observations.probes) == 3
        ids = [p.probe_id for p in current_spec.observations.probes]
        assert ids == ["probe_a", "probe_b", "probe_c"]

    def test_probe_addition_increments_version(self) -> None:
        """Adding a probe increments the spec version."""
        spec = _make_spec_without_probes()
        assert spec.version == 1

        patch = make_patch(
            spec,
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

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None
        assert result.new_spec.version == 2
