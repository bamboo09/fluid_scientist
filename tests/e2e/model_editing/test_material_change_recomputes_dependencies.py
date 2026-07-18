"""Test: material change recomputes dependent derived values.

This test reproduces the known issue where changing the fluid material
did not cascade to dependent derived values (Reynolds number) or
invalidate simulation artifacts.  The new dependency-analysis system
(DependencyGraph, DerivedValueComputer, InvalidationEngine) ensures
that:

* The dependency graph correctly identifies that
  ``/physics/reynolds_number`` depends on
  ``/physics/kinematic_viscosity``.
* The DerivedValueComputer recomputes Re = U * L / nu with the new
  viscosity.
* The InvalidationEngine marks the case as NEEDS_RECOMPILE and the
  results as NEEDS_RERUN.

Scenario:
* Initial: material = air, nu = 1.5e-5, U = 1.0, D = 0.1, Re ~ 6667
* After patch: material = water, nu = 1.0e-6, Re ~ 100000
"""
from __future__ import annotations

import pytest

from fluid_scientist.dependencies import (
    DependencyGraph,
    DerivedValueComputer,
    InvalidationEngine,
    InvalidationStatus,
    RuleRegistry,
)
from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import (
    PhysicsDefinition,
    SourcedValue,
)

from .conftest import make_patch, make_study_spec, _sourced


def _make_air_spec() -> "SimulationStudySpec":
    """Build a spec with air as the fluid material.

    Air at room temperature: nu = 1.5e-5 m^2/s.
    Re = U * D / nu = 1.0 * 0.1 / 1.5e-5 ~ 6667.
    """
    spec = make_study_spec()
    return spec.model_copy(update={
        "physics": PhysicsDefinition(
            material=_sourced("air", status="user_confirmed"),
            density=_sourced(1.225, unit="kg/m^3", status="user_confirmed"),
            kinematic_viscosity=_sourced(1.5e-5, unit="m^2/s", status="derived"),
            reynolds_number=_sourced(6667.0, status="derived"),
            velocity=_sourced(1.0, unit="m/s", status="user_explicit"),
            characteristic_length=_sourced(0.1, unit="m", status="user_explicit"),
        ),
    })


class TestMaterialChangeRecomputesDependencies:
    """Verify that changing material cascades to Re and invalidation."""

    def test_dependency_graph_links_re_to_nu(self) -> None:
        """DependencyGraph shows /physics/reynolds_number depends on
        /physics/kinematic_viscosity."""
        graph = DependencyGraph()

        dependents = graph.get_dependents("/physics/kinematic_viscosity")
        assert "/physics/reynolds_number" in dependents, (
            f"Reynolds number should depend on kinematic_viscosity; "
            f"got dependents: {dependents}"
        )

        # Also verify the reverse lookup.
        deps = graph.get_dependencies("/physics/reynolds_number")
        assert "/physics/kinematic_viscosity" in deps, (
            f"Reynolds number's dependencies should include "
            f"kinematic_viscosity; got: {deps}"
        )

        # Direct dependency check via get_dependents.
        assert "/physics/reynolds_number" in graph.get_dependents(
            "/physics/kinematic_viscosity"
        ), "Re should be a dependent of kinematic_viscosity"

    def test_material_patch_applies_successfully(self) -> None:
        """The patch to change material from air to water applies."""
        spec = _make_air_spec()
        assert spec.physics.material.value == "air"

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/physics/material",
                    value={
                        "value": "water",
                        "unit": None,
                        "status": "user_confirmed",
                        "source_turn_ids": ["turn_1"],
                        "confidence": 0.95,
                    },
                    source_quote="把材料改为水",
                ),
                PatchOperation(
                    op="replace",
                    path="/physics/kinematic_viscosity",
                    value=1.0e-6,
                    source_quote="水的运动粘度为1.0e-6 m^2/s",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == [], f"Patch failed: {result.errors}"
        assert result.new_spec is not None
        assert result.new_spec.physics.material.value == "water"
        assert result.new_spec.physics.kinematic_viscosity.value == 1.0e-6

    def test_derived_value_computer_computes_new_re(self) -> None:
        """DerivedValueComputer computes Re ~ 100000 for water."""
        spec = _make_air_spec()

        # Apply the material + viscosity patch.
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/physics/material",
                    value={
                        "value": "water",
                        "unit": None,
                        "status": "user_confirmed",
                        "source_turn_ids": ["turn_1"],
                        "confidence": 0.95,
                    },
                    source_quote="把材料改为水",
                ),
                PatchOperation(
                    op="replace",
                    path="/physics/kinematic_viscosity",
                    value=1.0e-6,
                    source_quote="水的运动粘度为1.0e-6 m^2/s",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        # Use DerivedValueComputer on the new spec dict.
        new_spec_dict = result.new_spec.model_dump()
        computer = DerivedValueComputer()
        re_value, re_formula = computer.compute(
            "/physics/reynolds_number", new_spec_dict
        )

        assert re_value is not None, "Re should be computable"
        assert re_value == pytest.approx(100000.0, rel=0.01), (
            f"Re for water should be ~100000, got {re_value}"
        )

        # Verify the formula string.
        assert re_formula is not None, "Formula should be provided"
        assert "Re" in re_formula or "U" in re_formula, (
            f"Formula should mention Re or U; got: {re_formula}"
        )

    def test_re_changed_from_air_to_water(self) -> None:
        """The Reynolds number changed from ~6667 (air) to ~100000 (water)."""
        spec = _make_air_spec()
        original_re = spec.physics.reynolds_number.value
        assert original_re == pytest.approx(6667.0, rel=0.01), (
            f"Initial Re for air should be ~6667, got {original_re}"
        )

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/physics/material",
                    value={
                        "value": "water",
                        "unit": None,
                        "status": "user_confirmed",
                        "source_turn_ids": ["turn_1"],
                        "confidence": 0.95,
                    },
                    source_quote="把材料改为水",
                ),
                PatchOperation(
                    op="replace",
                    path="/physics/kinematic_viscosity",
                    value=1.0e-6,
                    source_quote="水的运动粘度为1.0e-6 m^2/s",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        # Compute the new Re.
        computer = DerivedValueComputer()
        re_value, _ = computer.compute(
            "/physics/reynolds_number", result.new_spec.model_dump()
        )

        assert re_value is not None
        assert re_value != pytest.approx(original_re, rel=0.1), (
            "Re should have changed significantly after material change"
        )
        assert re_value > original_re, (
            "Re for water (lower nu) should be higher than for air"
        )

    def test_invalidation_engine_marks_case_and_results(self) -> None:
        """InvalidationEngine marks case as NEEDS_RECOMPILE and results as
        NEEDS_RERUN after material change."""
        spec = _make_air_spec()

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/physics/material",
                    value={
                        "value": "water",
                        "unit": None,
                        "status": "user_confirmed",
                        "source_turn_ids": ["turn_1"],
                        "confidence": 0.95,
                    },
                    source_quote="把材料改为水",
                ),
                PatchOperation(
                    op="replace",
                    path="/physics/kinematic_viscosity",
                    value=1.0e-6,
                    source_quote="水的运动粘度为1.0e-6 m^2/s",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        # Use InvalidationEngine on the changed paths.
        invalidation_engine = InvalidationEngine(RuleRegistry())
        changed_paths = [op.path for op in patch.operations]
        statuses = invalidation_engine.analyze(changed_paths, {})

        case_status = statuses.get("case")
        assert case_status == InvalidationStatus.NEEDS_RECOMPILE, (
            f"Case should need recompile after material change; "
            f"got: {case_status}"
        )
        results_status = statuses.get("results")
        assert results_status == InvalidationStatus.NEEDS_RERUN, (
            f"Results should need re-run after material change; "
            f"got: {results_status}"
        )

    def test_impact_analyzer_flags_re_recompute(self) -> None:
        """The PatchEngine's ImpactAnalyzer flags Re for recomputation."""
        spec = _make_air_spec()

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/physics/kinematic_viscosity",
                    value=1.0e-6,
                    source_quote="水的运动粘度为1.0e-6 m^2/s",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.impact is not None
        assert "/physics/reynolds_number" in result.impact.derived_recompute_needed, (
            f"Re should be flagged for recomputation; "
            f"got: {result.impact.derived_recompute_needed}"
        )
        assert "case" in result.impact.invalidation_status, (
            "Case should be invalidated"
        )
        assert "results" in result.impact.invalidation_status, (
            "Results should be invalidated"
        )
