"""Scenario 2: 45-degree inclined impinging jet at Re=23000.

End-to-end test of a heat-transfer case: a jet impinges on a wall at 45
degrees.  The key behaviours under test are:

* requesting wall heat flux triggers the energy equation (``heat_transfer``
  becomes ``True``);
* a wall-heat-flux observable that is *not* backed by an active energy
  equation is classified ``REQUIRES_NEW_PHYSICS`` (and conversely becomes
  ``SUPPORTED`` once heat transfer is enabled);
* the fully-developed pipe-inlet intent is preserved as a candidate;
* non-thermal capabilities are not discarded when heat transfer is enabled;
* no fake ``wallHeatFlux`` capability is invented by the registry.

User intent
-----------
* Re=23000, 45-degree inclined impinging jet, nozzle diameter D, 2D from wall.
* Quiescent initial field, fully-developed inlet, no-slip wall, pressure
  outlet, spanwise periodic.
* Study wall pressure, wall-heat-flux asymmetry, horseshoe vortex and
  asymmetric separation.
"""
from __future__ import annotations

import pytest

from fluid_scientist.capabilities import get_capability_registry
from fluid_scientist.llm_pipeline import LLMPipeline, ObservableDecomposer
from fluid_scientist.llm_pipeline.models import ExtractedFact, PhysicsDecomposition

USER_TEXT = """
Re=23000的45度倾斜冲击射流，喷嘴直径D，距壁面2D。
初始静止，充分发展入口，壁面无滑移，压力出口，展向周期。
研究壁面压力、壁面热流不对称、马蹄涡和非对称分离。
"""


def _whf_fact() -> ExtractedFact:
    """A standalone wall-heat-flux observable fact."""
    return ExtractedFact(
        fact_id="F_whf",
        category="observable",
        raw_text="壁面热流",
        value="wall_heat_flux",
    )


class TestScenario2InclinedJet:
    """Heat-transfer triggering and capability classification for the jet."""

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
        assert result.errors == []

    def test_reynolds_number_extracted(self, result):
        re_facts = [
            f for f in result.facts
            if f.category == "parameter" and "23000" in str(f.value)
        ]
        assert re_facts, "Reynolds number Re=23000 was not extracted"

    # ------------------------------------------------------------------
    # heat-transfer triggering
    # ------------------------------------------------------------------
    def test_heat_transfer_detected(self, result):
        """Wall heat flux must trigger the energy equation (heat_transfer)."""
        assert result.physics_decomposition.heat_transfer is True

    def test_energy_equation_included(self, result):
        """The energy equation must appear in the selected equations."""
        assert "energy_equation" in result.physics_decomposition.equations

    def test_solver_module_is_isothermal_fluid(self, result):
        """With heat transfer the solver must be isothermalFluid."""
        assert result.physics_decomposition.recommended_solver_module == "isothermalFluid"

    # ------------------------------------------------------------------
    # boundary / initial conditions
    # ------------------------------------------------------------------
    def test_no_slip_wall_detected(self, result):
        facts = [
            f for f in result.facts
            if f.category == "boundary" and f.value == "no_slip_wall"
        ]
        assert facts, "No-slip wall not detected"

    def test_pressure_outlet_detected(self, result):
        facts = [
            f for f in result.facts
            if f.category == "boundary" and f.value == "pressure_outlet"
        ]
        assert facts, "Pressure outlet not detected"

    def test_periodic_boundary_detected(self, result):
        facts = [
            f for f in result.facts
            if f.category == "boundary" and f.value == "periodic"
        ]
        assert facts, "Spanwise periodic boundary not detected"

    def test_fully_developed_inlet_preserved(self, result):
        """The fully-developed pipe-inlet intent must be preserved."""
        facts = [
            f for f in result.facts
            if f.category == "initial_condition" and "develop" in str(f.value).lower()
        ]
        assert facts, "Fully-developed inlet not preserved"

        # ...and it must survive as an atomic requirement candidate.
        developed_reqs = [
            r for r in result.atomic_requirements
            if any("develop" in str(k).lower() for k in r.keywords)
        ]
        assert developed_reqs, "Developed-inlet atomic requirement not generated"

    # ------------------------------------------------------------------
    # observables
    # ------------------------------------------------------------------
    def test_wall_heat_flux_observable_present(self, result):
        types = {o["semantic_type"] for o in result.observable_decomposition.observables}
        assert "wall_heat_flux" in types

    def test_wall_heat_flux_supported_when_heat_enabled(self, result):
        """With the energy equation active the heat-flux observable is supported."""
        whf = [
            o for o in result.observable_decomposition.observables
            if o["semantic_type"] == "wall_heat_flux"
        ]
        assert whf, "wall_heat_flux observable missing"
        assert whf[0]["capability_status"] == "SUPPORTED"

    # ------------------------------------------------------------------
    # REQUIRES_NEW_PHYSICS classification for heat flux (unit-level)
    # ------------------------------------------------------------------
    def test_heat_flux_requires_new_physics_when_energy_off(self):
        """A heat-flux observable without an active energy equation must be
        classified ``REQUIRES_NEW_PHYSICS`` by the observable decomposer."""
        decomposer = ObservableDecomposer()
        physics_off = PhysicsDecomposition(heat_transfer=False)
        out = decomposer.decompose([_whf_fact()], physics_off)
        assert out.observables, "observable not decomposed"
        assert out.observables[0]["capability_status"] == "REQUIRES_NEW_PHYSICS"

    def test_heat_flux_supported_when_energy_on(self):
        """Once the energy equation is on, the same observable is supported."""
        decomposer = ObservableDecomposer()
        physics_on = PhysicsDecomposition(heat_transfer=True)
        out = decomposer.decompose([_whf_fact()], physics_on)
        assert out.observables[0]["capability_status"] == "SUPPORTED"

    # ------------------------------------------------------------------
    # non-thermal capabilities must not be discarded
    # ------------------------------------------------------------------
    def test_non_thermal_capabilities_not_discarded(self, result):
        """Enabling heat transfer must not remove geometry/boundary/mesh reqs."""
        cats = {r.category for r in result.atomic_requirements}
        assert "boundary" in cats
        assert "physics" in cats
        # The heat-transfer requirement itself must be present.
        heat_reqs = [
            r for r in result.atomic_requirements
            if "energy" in str(r.capability_type).lower()
            or any("energy" in str(k).lower() or "heat" in str(k).lower() for k in r.keywords)
        ]
        assert heat_reqs, "heat-transfer atomic requirement missing"

    def test_atomic_requirements_generated(self, result):
        assert len(result.atomic_requirements) >= 10

    # ------------------------------------------------------------------
    # registry: no fake wallHeatFlux capability
    # ------------------------------------------------------------------
    def test_no_fake_wall_heat_flux_capability(self):
        """The registry must not invent a fake wallHeatFlux capability."""
        registry = get_capability_registry()
        ids = {c.capability_id for c in registry.list_all()}
        fakes = {
            i for i in ids
            if "wallheatflux" in i.lower() or "wall_heat_flux" in i.lower()
        }
        assert not fakes, f"Fake wallHeatFlux capabilities present: {fakes}"
