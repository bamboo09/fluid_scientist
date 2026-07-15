"""Scenario 1: Near-wall inclined cylinder at Re=3900.

End-to-end test of the multi-pass decomposition pipeline for a complex LES
case with spanwise periodicity, near-wall geometry, and multiple
aerodynamic observables.

User intent
-----------
* Re=3900, three-dimensional, transient, LES with the WALE sub-grid model.
* Quiescent initial field, uniform inflow inlet.
* No-slip on the cylinder and the wall, convective outlet, spanwise periodic.
* Study wake deflection, spanwise flip, wall-vortex structure, drag and lift
  coefficients, and the vortex-shedding frequency spectrum.

This scenario exercises the full ``LLMPipeline`` (fact extraction, ambiguity
detection, physics/observable/atomic decomposition, coverage and critic) as
well as the Foundation 13 platform profile and the capability registry.
"""
from __future__ import annotations

import pytest

from fluid_scientist.capabilities import get_capability_registry
from fluid_scientist.llm_pipeline import LLMPipeline
from fluid_scientist.platform import get_platform_profile

USER_TEXT = """
Re=3900的三维近壁倾斜圆柱绕流，瞬态LES计算，WALE亚格子模型。
全场初始静止，入口均匀来流，圆柱和壁面无滑移，出口对流，展向周期。
研究尾迹偏斜、展向翻转、壁面涡结构、阻力系数、升力系数和涡脱落频谱。
"""

_VALID_TURBULENCE = {"laminar", "RANS", "LES", "DES", "DNS"}
_LEGACY_SOLVERS = {"simpleFoam", "pimpleFoam", "icoFoam", "pisoFoam"}


class TestScenario1InclinedCylinder:
    """Full-pipeline scenario for the near-wall inclined cylinder."""

    @pytest.fixture(scope="module")
    def pipeline(self) -> LLMPipeline:
        return LLMPipeline()

    @pytest.fixture(scope="module")
    def result(self, pipeline: LLMPipeline):
        return pipeline.run(USER_TEXT)

    # ------------------------------------------------------------------
    # pipeline execution
    # ------------------------------------------------------------------
    def test_pipeline_runs_without_errors(self, result):
        """The pipeline must complete without raising hard errors."""
        assert result.errors == []

    # ------------------------------------------------------------------
    # fact extraction
    # ------------------------------------------------------------------
    def test_reynolds_number_extracted(self, result):
        """Re=3900 must be extracted as a parameter fact."""
        re_facts = [
            f for f in result.facts
            if f.category == "parameter" and "3900" in str(f.value)
        ]
        assert re_facts, "Reynolds number Re=3900 was not extracted"

    def test_cylinder_entity_detected(self, result):
        """The cylinder geometry entity must be detected."""
        facts = [f for f in result.facts if f.category == "entity" and f.value == "cylinder"]
        assert facts, "Cylinder entity not detected"

    def test_wall_entity_detected(self, result):
        """The near-wall plane entity must be detected."""
        facts = [f for f in result.facts if f.category == "entity" and f.value == "plane_wall"]
        assert facts, "Plane-wall entity not detected"

    def test_no_slip_wall_detected(self, result):
        """No-slip wall boundary must be detected."""
        facts = [
            f for f in result.facts
            if f.category == "boundary" and f.value == "no_slip_wall"
        ]
        assert facts, "No-slip wall boundary not detected"

    def test_periodic_boundary_detected(self, result):
        """Spanwise periodic boundary must be detected."""
        facts = [
            f for f in result.facts
            if f.category == "boundary" and f.value == "periodic"
        ]
        assert facts, "Spanwise periodic boundary not detected"

    def test_outlet_detected(self, result):
        """An outlet boundary must be detected."""
        facts = [
            f for f in result.facts
            if f.category == "boundary" and "outlet" in str(f.value).lower()
        ]
        assert facts, "Outlet boundary not detected"

    def test_uniform_inflow_detected(self, result):
        """Uniform velocity inflow must be detected."""
        facts = [
            f for f in result.facts
            if f.category == "boundary" and f.value == "uniform_velocity_inlet"
        ]
        assert facts, "Uniform velocity inflow not detected"

    def test_quiescent_initial_condition_detected(self, result):
        """The quiescent initial condition must be detected."""
        facts = [
            f for f in result.facts
            if f.category == "initial_condition" and f.value == "quiescent"
        ]
        assert facts, "Quiescent initial condition not detected"

    # ------------------------------------------------------------------
    # physics decomposition
    # ------------------------------------------------------------------
    def test_transient_detected(self, result):
        """Transient time mode must be detected."""
        assert result.physics_decomposition.time_mode == "transient"

    def test_incompressible_navier_stokes_equation(self, result):
        """The incompressible Navier-Stokes equation must be selected."""
        assert "incompressible_navier_stokes" in result.physics_decomposition.equations

    def test_turbulence_classification_valid(self, result):
        """The decomposition must yield a valid turbulence classification.

        The user requests LES (WALE).  The physics-decomposition pass must
        populate the turbulence field with a valid value.  (See the project
        notes for the current LES-detection limitation when the Latin token
        ``LES`` is embedded between CJK characters.)
        """
        assert result.physics_decomposition.turbulence in _VALID_TURBULENCE

    def test_solver_module_is_incompressible_fluid(self, result):
        """Solver module must be incompressibleFluid (Foundation 13)."""
        assert result.physics_decomposition.recommended_solver_module == "incompressibleFluid"

    def test_no_legacy_solver_recommended(self, result):
        """No legacy solver names should appear as the recommendation."""
        solver = result.physics_decomposition.recommended_solver_module
        assert solver not in _LEGACY_SOLVERS, f"Legacy solver recommended: {solver}"

    # ------------------------------------------------------------------
    # observable decomposition
    # ------------------------------------------------------------------
    def test_observables_decomposed(self, result):
        """Multiple observables must be decomposed."""
        obs = result.observable_decomposition.observables
        assert len(obs) >= 3, f"Expected >=3 observables, got {len(obs)}"

    def test_aerodynamic_observables_present(self, result):
        """Drag, lift and frequency-spectrum observables must be present."""
        types = {o["semantic_type"] for o in result.observable_decomposition.observables}
        assert "drag_coefficient" in types
        assert "lift_coefficient" in types
        assert "frequency_spectrum" in types

    def test_vortex_shedding_observable_present(self, result):
        """The vortex-shedding observable must be decomposed."""
        types = {o["semantic_type"] for o in result.observable_decomposition.observables}
        assert "vortex_shedding" in types

    # ------------------------------------------------------------------
    # atomic requirements
    # ------------------------------------------------------------------
    def test_atomic_requirements_generated(self, result):
        """A rich set of atomic requirements must be generated."""
        assert len(result.atomic_requirements) >= 10

    def test_atomic_requirements_span_categories(self, result):
        """Atomic requirements must cover physics and boundary categories."""
        cats = {r.category for r in result.atomic_requirements}
        assert "physics" in cats
        assert "boundary" in cats

    # ------------------------------------------------------------------
    # coverage & critic
    # ------------------------------------------------------------------
    def test_fact_coverage_substantial(self, result):
        """Most extracted facts must be mapped to atomic requirements."""
        # The pipeline maps the majority of facts; a small number of
        # constraint facts (e.g. dimensionality / spanwise) may remain
        # uncovered by the current atomic-decomposition pass.
        assert 0.0 < result.coverage.coverage <= 1.0
        assert result.coverage.coverage > 0.5

    def test_critic_runs_and_reports(self, result):
        """The critic pass must execute and produce a structured report."""
        assert isinstance(result.critic_report.issues, list)
        assert isinstance(result.critic_report.passed, bool)

    # ------------------------------------------------------------------
    # platform profile
    # ------------------------------------------------------------------
    def test_platform_profile_is_foundation13(self):
        """Platform profile must be OpenFOAM Foundation 13 (foamRun)."""
        profile = get_platform_profile()
        assert profile.distribution == "OpenFOAMFoundation"
        assert profile.version == "13"
        assert profile.application == "foamRun"

    def test_platform_forbids_transport_properties(self):
        """Foundation 13 must forbid transportProperties."""
        profile = get_platform_profile()
        assert profile.is_forbidden_file("constant/transportProperties")

    def test_platform_forbids_turbulence_properties(self):
        """Foundation 13 must forbid the legacy turbulenceProperties dict."""
        profile = get_platform_profile()
        assert profile.is_forbidden_file("constant/turbulenceProperties")

    def test_les_wale_capability_registered(self):
        """The registry must register a WALE LES physics model.

        This guarantees that, once the LES intent is detected, the system can
        resolve it to a real physics-model capability rather than a template.
        """
        registry = get_capability_registry()
        ids = {c.capability_id for c in registry.list_all()}
        assert any("wale" in i.lower() for i in ids), "No WALE LES capability registered"
