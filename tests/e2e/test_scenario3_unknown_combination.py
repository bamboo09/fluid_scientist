"""Scenario 3: complex unknown combination.

A deliberately exotic, multi-physics user request that no single template
can satisfy:

    脉动旋流椭圆射流以30度冲击带孔恒温移动壁面，研究壁面热流非对称、
    旋涡进动频率和孔隙附近压力脉动。

The pipeline must *decompose* this into atomic requirements rather than
failing with a "template not found" style error, and the
``CapabilityGapAnalyzer`` must classify every atom into a concrete status
(``EXACT_SUPPORTED`` / ``COMPOSABLE_SUPPORTED`` / ``EXTENDABLE`` /
``REQUIRES_NEW_PHYSICS`` / ``NEEDS_CLARIFICATION``).

Two layers are exercised:

1. The full ``LLMPipeline`` on the raw text -- it must not crash and must
   emit a non-empty atomic-requirement set plus a blocking unknown for the
   missing inlet (graceful degradation, never "template not found").
2. The ``CapabilityGapAnalyzer`` on the 14 representative atoms the scenario
   is meant to decompose into -- verifying the classification taxonomy and
   that each status is reachable.
"""
from __future__ import annotations

import pytest

from fluid_scientist.capabilities import (
    AtomicRequirementSet,
    CapabilityGapAnalyzer,
    CapabilityRequirement,
    get_capability_registry,
)
from fluid_scientist.llm_pipeline import LLMPipeline

USER_TEXT = (
    "脉动旋流椭圆射流以30度冲击带孔恒温移动壁面，"
    "研究壁面热流非对称、旋涡进动频率和孔隙附近压力脉动。"
)

# The 14 atomic capabilities the scenario is expected to decompose into,
# plus one genuinely-novel physics model to exercise REQUIRES_NEW_PHYSICS.
ATOMS: list[tuple[str, str, list[str]]] = [
    ("incompressible_transient", "physics_model_compiler", ["incompressible", "transient"]),
    ("heat_transfer", "physics_model_compiler", ["heat_transfer", "energy"]),
    ("elliptical_nozzle", "geometry_generator", ["elliptical", "nozzle"]),
    ("geometry_rotation", "geometry_generator", ["rotation", "rotating"]),
    ("thirty_deg_positioning", "geometry_generator", ["angle", "inclined"]),
    ("pulsating_inlet", "boundary_writer", ["pulsating", "inlet"]),
    ("swirling_inlet", "boundary_writer", ["swirl", "inlet"]),
    ("moving_wall", "boundary_writer", ["moving", "wall"]),
    ("constant_temp_wall", "boundary_writer", ["constant", "temperature", "wall"]),
    ("perforated_geometry", "geometry_generator", ["perforated", "porous"]),
    ("heat_flux_sampling", "field_sampler", ["wall_heat_flux", "sampling"]),
    ("pressure_probes", "field_sampler", ["pressure", "probe"]),
    ("frequency_spectrum", "postprocessor", ["frequency", "spectrum"]),
    ("vortex_precession_detection", "postprocessor", ["vortex", "precession"]),
    # A physics-type requirement with no matching capability -> REQUIRES_NEW_PHYSICS
    ("novel_combustion_solver", "solver_extension", ["custom_combustion"]),
]

_VALID_STATUSES = {
    "EXACT_SUPPORTED",
    "COMPOSABLE_SUPPORTED",
    "EXTENDABLE",
    "REQUIRES_NEW_PHYSICS",
    "NEEDS_CLARIFICATION",
}


def _build_requirement_set() -> AtomicRequirementSet:
    reqs = [
        CapabilityRequirement(
            requirement_id=rid,
            capability_type=ctype,
            keywords=kws,
            description=f"{rid} capability for the pulsating swirling elliptical jet scenario",
            mandatory=True,
        )
        for rid, ctype, kws in ATOMS
    ]
    return AtomicRequirementSet(requirements=reqs)


class TestScenario3UnknownCombination:
    """Decomposition + capability classification of an exotic combination."""

    @pytest.fixture(scope="module")
    def pipeline(self) -> LLMPipeline:
        return LLMPipeline()

    @pytest.fixture(scope="module")
    def result(self, pipeline: LLMPipeline):
        return pipeline.run(USER_TEXT)

    @pytest.fixture(scope="module")
    def registry(self):
        return get_capability_registry()

    @pytest.fixture(scope="module")
    def analyzer(self, registry):
        return CapabilityGapAnalyzer(registry)

    @pytest.fixture(scope="module")
    def requirement_set(self):
        return _build_requirement_set()

    @pytest.fixture(scope="module")
    def resolution_plan(self, analyzer, requirement_set):
        return analyzer.analyze(requirement_set)

    # ------------------------------------------------------------------
    # pipeline layer: never "template not found"
    # ------------------------------------------------------------------
    def test_pipeline_does_not_return_template_not_found(self, result):
        """The pipeline must decompose instead of erroring with template-not-found."""
        assert result.errors == []
        blob = str(result.errors) + str(result.atomic_requirements)
        assert "template not found" not in blob.lower()

    def test_pipeline_decomposes_into_atomic_requirements(self, result):
        assert len(result.atomic_requirements) > 0

    def test_pipeline_flags_missing_inlet_as_blocking_unknown(self, result):
        """The scenario never states an inlet; the ambiguity pass must flag it."""
        types = {u.get("unknown_type") for u in result.ambiguity_detection.blocking_unknowns}
        assert "missing_inlet" in types or any(
            "inlet" in str(t).lower() for t in types
        ), "Missing-inlet blocking unknown not raised"

    # ------------------------------------------------------------------
    # gap-analyzer layer: every atom is classified
    # ------------------------------------------------------------------
    def test_every_atom_classified(self, resolution_plan):
        results = resolution_plan.results
        assert len(results) == len(ATOMS)
        for r in results:
            assert r.classification in _VALID_STATUSES, (
                f"{r.requirement.requirement_id} -> unexpected status {r.classification!r}"
            )

    def test_no_template_not_found_in_classification(self, resolution_plan):
        blob = str([(r.requirement.requirement_id, r.classification) for r in resolution_plan.results])
        assert "template not found" not in blob.lower()
        assert "not found" not in blob.lower()

    def test_some_atoms_exact_supported(self, resolution_plan):
        statuses = {r.classification for r in resolution_plan.results}
        assert "EXACT_SUPPORTED" in statuses

    def test_some_atoms_composable_supported(self, resolution_plan):
        statuses = {r.classification for r in resolution_plan.results}
        assert "COMPOSABLE_SUPPORTED" in statuses

    def test_some_atoms_extendable(self, resolution_plan):
        statuses = {r.classification for r in resolution_plan.results}
        assert "EXTENDABLE" in statuses

    def test_novel_physics_requires_new_physics(self, resolution_plan):
        """The novel solver-extension atom must be REQUIRES_NEW_PHYSICS."""
        by_id = {r.requirement.requirement_id: r for r in resolution_plan.results}
        assert "novel_combustion_solver" in by_id
        assert by_id["novel_combustion_solver"].classification == "REQUIRES_NEW_PHYSICS"

    def test_truly_unknown_geometry_needs_clarification(self, resolution_plan):
        """Atoms with no native capability and no physics mapping must be
        flagged NEEDS_CLARIFICATION (graceful, never a hard crash)."""
        by_id = {r.requirement.requirement_id: r for r in resolution_plan.results}
        assert by_id["elliptical_nozzle"].classification == "NEEDS_CLARIFICATION"
        assert by_id["perforated_geometry"].classification == "NEEDS_CLARIFICATION"

    # ------------------------------------------------------------------
    # representative per-atom expectations
    # ------------------------------------------------------------------
    def test_incompressible_transient_supported(self, resolution_plan):
        by_id = {r.requirement.requirement_id: r for r in resolution_plan.results}
        assert by_id["incompressible_transient"].classification == "EXACT_SUPPORTED"

    def test_pressure_probes_supported(self, resolution_plan):
        by_id = {r.requirement.requirement_id: r for r in resolution_plan.results}
        assert by_id["pressure_probes"].classification == "EXACT_SUPPORTED"

    def test_solver_adapter_supported(self, resolution_plan):
        by_id = {r.requirement.requirement_id: r for r in resolution_plan.results}
        assert by_id["incompressible_transient"].classification == "EXACT_SUPPORTED"

    # ------------------------------------------------------------------
    # resolution-plan aggregation
    # ------------------------------------------------------------------
    def test_resolution_plan_flags_extension_and_new_physics(self, resolution_plan):
        assert resolution_plan.needs_extension is True
        assert resolution_plan.needs_new_physics is True

    def test_resolution_plan_returns_one_result_per_requirement(self, resolution_plan):
        assert len(resolution_plan.results) == len(ATOMS)
        assert all(r.matched_capabilities is not None for r in resolution_plan.results)
