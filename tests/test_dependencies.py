"""Comprehensive tests for the ``fluid_scientist.dependencies`` package.

Covers:
* Dependency graph construction with CFD rules.
* Direct and transitive dependent/dependency lookups.
* Cycle detection (default graph is acyclic; custom cycle detected).
* Derived-value computation (Reynolds, Courant, duration, …).
* Missing-input handling (returns ``(None, None)``).
* Material change invalidation cascading to case and results.
* Observation change triggering only postprocess/measurement_plan.
* Report generation with cascading effects.
* Air -> water material change computing a new Reynolds number.
"""

from __future__ import annotations

import pytest

from fluid_scientist.dependencies import (
    DependencyEdge,
    DependencyGraph,
    DependencyNode,
    DependencyReport,
    DependencyRule,
    DerivedValueComputer,
    InvalidationEngine,
    InvalidationStatus,
    ReportBuilder,
    RuleRegistry,
)
from fluid_scientist.dependencies.invalidation import InvalidationRule


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _sv(value, unit=None, status="user_explicit"):
    """Build a SourcedValue-style dict quickly."""
    d = {"value": value, "status": status}
    if unit is not None:
        d["unit"] = unit
    return d


def _make_spec(
    material="water",
    velocity=1.0,
    diameter=0.1,
    nu=None,
    rho=None,
    start_time=0.0,
    end_time=10.0,
    write_interval=0.5,
    delta_t=0.001,
    mesh_size=0.01,
):
    """Build a plain-dict simulation spec for testing."""
    physics = {
        "material": _sv(material),
        "velocity": _sv(velocity, "m/s"),
        "characteristic_length": _sv(diameter, "m"),
    }
    if nu is not None:
        physics["kinematic_viscosity"] = _sv(nu, "m^2/s", status="derived")
    if rho is not None:
        physics["density"] = _sv(rho, "kg/m^3", status="derived")
    return {
        "physics": physics,
        "numerics": {
            "time": {
                "start_time": _sv(start_time, "s"),
                "end_time": _sv(end_time, "s"),
                "write_interval": _sv(write_interval, "s"),
                "delta_t": _sv(delta_t, "s"),
            }
        },
        "mesh": {
            "resolution": _sv(mesh_size, "m"),
        },
    }


# ---------------------------------------------------------------------------
# 1. Dependency graph construction
# ---------------------------------------------------------------------------


class TestGraphConstruction:
    """Verify the dependency graph is built with CFD rules."""

    def test_graph_has_nodes(self):
        graph = DependencyGraph()
        nodes = graph.get_nodes()
        paths = {n.path for n in nodes}
        # A representative subset of expected paths.
        for expected in [
            "/physics/velocity",
            "/physics/reynolds_number",
            "/physics/material",
            "/numerics/time/duration",
            "/numerics/time/courant_number",
            "/geometry",
            "/mesh",
            "/boundaries",
            "/observations/targets",
        ]:
            assert expected in paths, f"Missing node: {expected}"

    def test_graph_has_edges(self):
        graph = DependencyGraph()
        edges = graph.get_edges()
        assert len(edges) > 0
        # Every edge should reference a valid rule id.
        registry = RuleRegistry()
        for edge in edges:
            assert registry.get_rule(edge.rule_id) is not None

    def test_rule_registry_has_expected_rules(self):
        registry = RuleRegistry()
        rule_ids = {r.rule_id for r in registry.list_all_rules()}
        for expected in [
            "reynolds_from_UDnu",
            "viscosity_from_rho_nu",
            "material_to_density",
            "material_to_viscosity",
            "duration_from_start_end",
            "courant_from_dt_U_dx",
            "geometry_to_mesh",
            "mesh_to_boundaries",
        ]:
            assert expected in rule_ids, f"Missing rule: {expected}"

    def test_get_rule_by_id(self):
        registry = RuleRegistry()
        rule = registry.get_rule("reynolds_from_UDnu")
        assert rule is not None
        assert rule.target_path == "/physics/reynolds_number"
        assert rule.formula == "Re = U * D / nu"

    def test_get_rules_for_source(self):
        registry = RuleRegistry()
        rules = registry.get_rules_for_source("/physics/material")
        targets = {r.target_path for r in rules}
        assert "/physics/density" in targets
        assert "/physics/kinematic_viscosity" in targets
        assert "/physics/dynamic_viscosity" in targets

    def test_get_rules_for_target(self):
        registry = RuleRegistry()
        rules = registry.get_rules_for_target("/physics/reynolds_number")
        assert len(rules) == 1
        assert rules[0].rule_id == "reynolds_from_UDnu"


# ---------------------------------------------------------------------------
# 2. Direct dependents
# ---------------------------------------------------------------------------


class TestDependents:
    """Test direct dependent lookups."""

    def test_velocity_dependents_include_reynolds(self):
        graph = DependencyGraph()
        dependents = graph.get_dependents("/physics/velocity")
        assert "/physics/reynolds_number" in dependents

    def test_velocity_dependents_include_courant(self):
        graph = DependencyGraph()
        dependents = graph.get_dependents("/physics/velocity")
        assert "/numerics/time/courant_number" in dependents

    def test_reynolds_dependencies(self):
        """Re depends on U, D, and nu."""
        graph = DependencyGraph()
        deps = graph.get_dependencies("/physics/reynolds_number")
        assert "/physics/velocity" in deps
        assert "/physics/characteristic_length" in deps
        assert "/physics/kinematic_viscosity" in deps

    def test_nonexistent_path_returns_empty(self):
        graph = DependencyGraph()
        assert graph.get_dependents("/nonexistent/path") == []
        assert graph.get_dependencies("/nonexistent/path") == []


# ---------------------------------------------------------------------------
# 3. Transitive dependents
# ---------------------------------------------------------------------------


class TestTransitiveDependents:
    """Test transitive (recursive) dependent lookups."""

    def test_material_transitive_dependents(self):
        """material -> rho, nu, mu -> (mu from rho,nu) -> Re (from nu)."""
        graph = DependencyGraph()
        transitive = graph.get_transitive_dependents("/physics/material")
        # All of these should appear transitively.
        assert "/physics/density" in transitive
        assert "/physics/kinematic_viscosity" in transitive
        assert "/physics/dynamic_viscosity" in transitive
        assert "/physics/reynolds_number" in transitive

    def test_material_transitive_chain_length(self):
        """The transitive set should have at least 4 members."""
        graph = DependencyGraph()
        transitive = graph.get_transitive_dependents("/physics/material")
        assert len(transitive) >= 4

    def test_reynolds_transitive_dependencies(self):
        """Re transitively depends on material (via nu)."""
        graph = DependencyGraph()
        transitive = graph.get_transitive_dependencies("/physics/reynolds_number")
        assert "/physics/velocity" in transitive
        assert "/physics/characteristic_length" in transitive
        assert "/physics/kinematic_viscosity" in transitive
        # Material is a transitive dependency via kinematic_viscosity.
        assert "/physics/material" in transitive

    def test_geometry_transitive_chain(self):
        """geometry -> mesh -> boundaries."""
        graph = DependencyGraph()
        transitive = graph.get_transitive_dependents("/geometry")
        assert "/mesh" in transitive
        assert "/boundaries" in transitive

    def test_transitive_excludes_self(self):
        graph = DependencyGraph()
        transitive = graph.get_transitive_dependents("/physics/material")
        assert "/physics/material" not in transitive


# ---------------------------------------------------------------------------
# 4. Cycle detection
# ---------------------------------------------------------------------------


class TestCycleDetection:
    """Test cycle detection."""

    def test_default_graph_no_cycles(self):
        graph = DependencyGraph()
        for path in [
            "/physics/velocity",
            "/physics/material",
            "/physics/reynolds_number",
            "/numerics/time/duration",
        ]:
            assert not graph.has_cycle(path), f"Unexpected cycle at {path}"

    def test_custom_cycle_detected(self):
        """Build a small graph A -> B -> C -> A and detect the cycle."""
        graph = DependencyGraph()
        graph.add_edge(DependencyEdge(
            source_path="/test/a", target_path="/test/b",
            rule_id="cycle1", edge_type="derives",
        ))
        graph.add_edge(DependencyEdge(
            source_path="/test/b", target_path="/test/c",
            rule_id="cycle2", edge_type="derives",
        ))
        graph.add_edge(DependencyEdge(
            source_path="/test/c", target_path="/test/a",
            rule_id="cycle3", edge_type="derives",
        ))
        assert graph.has_cycle("/test/a")
        assert graph.has_cycle("/test/b")
        assert graph.has_cycle("/test/c")

    def test_no_false_positive_cycle(self):
        """A -> B (no back-edge) should not report a cycle."""
        graph = DependencyGraph()
        graph.add_edge(DependencyEdge(
            source_path="/test/x", target_path="/test/y",
            rule_id="linear1", edge_type="derives",
        ))
        assert not graph.has_cycle("/test/x")
        assert not graph.has_cycle("/test/y")


# ---------------------------------------------------------------------------
# 5. Derived value computation
# ---------------------------------------------------------------------------


class TestDerivedValues:
    """Test the DerivedValueComputer."""

    def test_compute_reynolds_number(self):
        """Re = U * D / nu = 1.0 * 0.1 / 1e-6 = 100000."""
        computer = DerivedValueComputer()
        spec = _make_spec(velocity=1.0, diameter=0.1, nu=1.0e-6)
        value, formula = computer.compute("/physics/reynolds_number", spec)
        assert value == pytest.approx(100000.0)
        assert formula == "Re = U * D / nu"

    def test_compute_reynolds_direct(self):
        """Test the pure helper directly."""
        computer = DerivedValueComputer()
        re = computer.compute_reynolds(2.0, 0.5, 1.0e-5)
        assert re == pytest.approx(100000.0)

    def test_compute_duration(self):
        computer = DerivedValueComputer()
        spec = _make_spec(start_time=0.0, end_time=10.0)
        value, formula = computer.compute("/numerics/time/duration", spec)
        assert value == pytest.approx(10.0)
        assert formula == "duration = end_time - start_time"

    def test_compute_output_count(self):
        computer = DerivedValueComputer()
        spec = _make_spec(end_time=10.0, write_interval=0.5)
        value, formula = computer.compute(
            "/numerics/time/expected_output_count", spec
        )
        assert value == 20
        assert formula == "count = floor(end_time / write_interval)"

    def test_compute_courant(self):
        """Co = U * dt / dx = 1.0 * 0.001 / 0.01 = 0.1."""
        computer = DerivedValueComputer()
        spec = _make_spec(velocity=1.0, delta_t=0.001, mesh_size=0.01)
        value, formula = computer.compute(
            "/numerics/time/courant_number", spec
        )
        assert value == pytest.approx(0.1)
        assert formula == "Co = U * delta_t / dx"

    def test_compute_dynamic_viscosity(self):
        """mu = rho * nu = 998.2 * 1e-6 = 9.982e-4."""
        computer = DerivedValueComputer()
        spec = _make_spec(rho=998.2, nu=1.0e-6)
        value, formula = computer.compute("/physics/dynamic_viscosity", spec)
        assert value == pytest.approx(998.2 * 1.0e-6)
        assert formula == "mu = rho * nu"

    def test_compute_density_from_material(self):
        computer = DerivedValueComputer()
        rho_air, nu_air = computer.compute_density_from_material("air")
        assert rho_air == pytest.approx(1.225)
        assert nu_air == pytest.approx(1.5e-5)

        rho_water, nu_water = computer.compute_density_from_material("water")
        assert rho_water == pytest.approx(998.2)
        assert nu_water == pytest.approx(1.0e-6)

    def test_compute_density_from_unknown_material(self):
        computer = DerivedValueComputer()
        rho, nu = computer.compute_density_from_material("plasma")
        assert rho is None
        assert nu is None

    def test_compute_viscosity_from_re(self):
        """nu = U * D / Re = 1.0 * 0.1 / 100000 = 1e-6."""
        computer = DerivedValueComputer()
        nu = computer.compute_viscosity_from_re(100000.0, 1.0, 0.1)
        assert nu == pytest.approx(1.0e-6)

    def test_compute_density_path(self):
        computer = DerivedValueComputer()
        spec = _make_spec(material="water")
        value, formula = computer.compute("/physics/density", spec)
        assert value == pytest.approx(998.2)
        assert formula is not None

    def test_compute_kinematic_viscosity_path(self):
        computer = DerivedValueComputer()
        spec = _make_spec(material="air")
        value, formula = computer.compute(
            "/physics/kinematic_viscosity", spec
        )
        assert value == pytest.approx(1.5e-5)
        assert formula is not None


# ---------------------------------------------------------------------------
# 6. Missing inputs
# ---------------------------------------------------------------------------


class TestMissingInputs:
    """Derived values with missing inputs must return (None, None)."""

    def test_reynolds_missing_velocity(self):
        computer = DerivedValueComputer()
        spec = {
            "physics": {
                "characteristic_length": _sv(0.1, "m"),
                "kinematic_viscosity": _sv(1.0e-6, "m^2/s"),
            }
        }
        value, formula = computer.compute("/physics/reynolds_number", spec)
        assert value is None
        assert formula is None

    def test_reynolds_missing_viscosity_and_material(self):
        computer = DerivedValueComputer()
        spec = {
            "physics": {
                "velocity": _sv(1.0, "m/s"),
                "characteristic_length": _sv(0.1, "m"),
            }
        }
        value, formula = computer.compute("/physics/reynolds_number", spec)
        assert value is None
        assert formula is None

    def test_duration_missing_end_time(self):
        computer = DerivedValueComputer()
        spec = {
            "numerics": {
                "time": {
                    "start_time": _sv(0.0, "s"),
                }
            }
        }
        value, formula = computer.compute("/numerics/time/duration", spec)
        assert value is None
        assert formula is None

    def test_courant_missing_mesh(self):
        computer = DerivedValueComputer()
        spec = {
            "numerics": {"time": {"delta_t": _sv(0.001, "s")}},
            "physics": {"velocity": _sv(1.0, "m/s")},
        }
        value, formula = computer.compute(
            "/numerics/time/courant_number", spec
        )
        assert value is None
        assert formula is None

    def test_unknown_target_returns_none(self):
        computer = DerivedValueComputer()
        value, formula = computer.compute("/unknown/path", {})
        assert value is None
        assert formula is None


# ---------------------------------------------------------------------------
# 7. Material change invalidation
# ---------------------------------------------------------------------------


class TestInvalidationMaterial:
    """Test that material changes invalidate case and results."""

    def test_material_change_invalidates_case_and_results(self):
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {
            "mesh": "valid",
            "case": "valid",
            "results": "valid",
        }
        result = engine.analyze(["/physics/material"], artifacts)
        assert result["case"] == InvalidationStatus.NEEDS_RECOMPILE
        assert result["results"] == InvalidationStatus.NEEDS_RERUN

    def test_material_change_does_not_invalidate_mesh(self):
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {
            "mesh": "valid",
            "case": "valid",
            "results": "valid",
        }
        result = engine.analyze(["/physics/material"], artifacts)
        # Mesh is not affected by material change.
        assert result.get("mesh", InvalidationStatus.VALID) == InvalidationStatus.VALID


class TestInvalidationGeometry:
    """Test that geometry changes invalidate mesh, case, and results."""

    def test_geometry_change_invalidates_mesh_case_results(self):
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {
            "mesh": "valid",
            "case": "valid",
            "results": "valid",
        }
        result = engine.analyze(["/geometry"], artifacts)
        assert result["mesh"] == InvalidationStatus.NEEDS_RECOMPILE
        assert result["case"] == InvalidationStatus.NEEDS_RECOMPILE
        assert result["results"] == InvalidationStatus.NEEDS_RERUN


class TestInvalidationNumerics:
    """Test that numerics changes invalidate case and results."""

    def test_numerics_change_invalidates_case_and_results(self):
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {"case": "valid", "results": "valid"}
        result = engine.analyze(["/numerics/time/delta_t"], artifacts)
        assert result["case"] == InvalidationStatus.NEEDS_RECOMPILE
        assert result["results"] == InvalidationStatus.NEEDS_RERUN


class TestInvalidationBoundaries:
    """Test that boundary changes invalidate case and results."""

    def test_boundary_change_invalidates_case_and_results(self):
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {"case": "valid", "results": "valid"}
        result = engine.analyze(["/boundaries"], artifacts)
        assert result["case"] == InvalidationStatus.NEEDS_RECOMPILE
        assert result["results"] == InvalidationStatus.NEEDS_RERUN


# ---------------------------------------------------------------------------
# 8. Observation change invalidation
# ---------------------------------------------------------------------------


class TestInvalidationObservation:
    """Test that observation changes trigger only postprocess/measurement_plan."""

    def test_observation_change_triggers_postprocess_and_measurement(self):
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {
            "mesh": "valid",
            "case": "valid",
            "results": "valid",
            "postprocess": "valid",
            "measurement_plan": "valid",
        }
        result = engine.analyze(["/observations/targets"], artifacts)
        assert result["postprocess"] == InvalidationStatus.NEEDS_RECOMPUTE
        assert result["measurement_plan"] == InvalidationStatus.NEEDS_RECOMPUTE

    def test_observation_change_does_not_invalidate_mesh_case_results(self):
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {
            "mesh": "valid",
            "case": "valid",
            "results": "valid",
            "postprocess": "valid",
            "measurement_plan": "valid",
        }
        result = engine.analyze(["/observations/targets"], artifacts)
        assert result.get("mesh", InvalidationStatus.VALID) == InvalidationStatus.VALID
        assert result.get("case", InvalidationStatus.VALID) == InvalidationStatus.VALID
        assert result.get("results", InvalidationStatus.VALID) == InvalidationStatus.VALID

    def test_postprocess_not_invalidated_without_saved_fields(self):
        """If no results are saved, postprocess is not invalidated."""
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {
            "measurement_plan": "valid",
            # results not present — fields not saved.
        }
        result = engine.analyze(["/observations/targets"], artifacts)
        assert result.get("postprocess", InvalidationStatus.VALID) == InvalidationStatus.VALID
        # measurement_plan is always invalidated for observation changes.
        assert result["measurement_plan"] == InvalidationStatus.NEEDS_RECOMPUTE


class TestInvalidationReportTitle:
    """Test that report-title-only changes do not require a re-run."""

    def test_title_change_only_invalidates_report(self):
        engine = InvalidationEngine(RuleRegistry())
        artifacts = {
            "mesh": "valid",
            "case": "valid",
            "results": "valid",
            "report": "valid",
        }
        result = engine.analyze(["/study/title"], artifacts)
        assert result["report"] == InvalidationStatus.NEEDS_RECOMPUTE
        assert result.get("mesh", InvalidationStatus.VALID) == InvalidationStatus.VALID
        assert result.get("case", InvalidationStatus.VALID) == InvalidationStatus.VALID
        assert result.get("results", InvalidationStatus.VALID) == InvalidationStatus.VALID


# ---------------------------------------------------------------------------
# 9. Report generation
# ---------------------------------------------------------------------------


class TestReportGeneration:
    """Test the ReportBuilder."""

    def test_report_velocity_change(self):
        """Changing velocity cascades to Re and Courant."""
        graph = DependencyGraph()
        computer = DerivedValueComputer()
        engine = InvalidationEngine(RuleRegistry())
        builder = ReportBuilder()

        spec = _make_spec(velocity=2.0, nu=1.0e-6, rho=998.2)
        report = builder.build_report(
            changes=["/physics/velocity"],
            spec_dict=spec,
            graph=graph,
            computer=computer,
            invalidation_engine=engine,
        )

        assert isinstance(report, DependencyReport)
        assert report.changed_paths == ["/physics/velocity"]
        # Reynolds number and Courant number should be affected.
        assert "/physics/reynolds_number" in report.affected_paths
        assert "/numerics/time/courant_number" in report.affected_paths
        # Derived recompute should include the Reynolds formula.
        recompute_paths = [p for p, _ in report.derived_recompute_needed]
        assert "/physics/reynolds_number" in recompute_paths
        assert "/numerics/time/courant_number" in recompute_paths
        # Summary should be non-empty.
        assert len(report.summary) > 0

    def test_report_material_change_cascades(self):
        """Changing material cascades to rho, nu, mu, Re and invalidates."""
        graph = DependencyGraph()
        computer = DerivedValueComputer()
        engine = InvalidationEngine(RuleRegistry())
        builder = ReportBuilder()

        spec = _make_spec(material="water", velocity=1.0, diameter=0.1)
        report = builder.build_report(
            changes=["/physics/material"],
            spec_dict=spec,
            graph=graph,
            computer=computer,
            invalidation_engine=engine,
        )

        assert report.changed_paths == ["/physics/material"]
        # Transitive dependents of material.
        assert "/physics/density" in report.affected_paths
        assert "/physics/kinematic_viscosity" in report.affected_paths
        assert "/physics/dynamic_viscosity" in report.affected_paths
        assert "/physics/reynolds_number" in report.affected_paths
        # Invalidation should hit case and results.
        assert report.invalidation_status.get("case") == str(
            InvalidationStatus.NEEDS_RECOMPILE
        )
        assert report.invalidation_status.get("results") == str(
            InvalidationStatus.NEEDS_RERUN
        )

    def test_report_title_change_no_rerun(self):
        """Changing only the title should not require re-run."""
        graph = DependencyGraph()
        computer = DerivedValueComputer()
        engine = InvalidationEngine(RuleRegistry())
        builder = ReportBuilder()

        spec = _make_spec()
        report = builder.build_report(
            changes=["/study/title"],
            spec_dict=spec,
            graph=graph,
            computer=computer,
            invalidation_engine=engine,
        )

        assert report.changed_paths == ["/study/title"]
        # No cascading dependencies.
        assert report.affected_paths == []
        # Report needs recompute but not rerun.
        assert report.invalidation_status.get("report") == str(
            InvalidationStatus.NEEDS_RECOMPUTE
        )
        assert report.invalidation_status.get("results") == str(
            InvalidationStatus.VALID
        )


# ---------------------------------------------------------------------------
# 10. Air -> water material change computes new Re
# ---------------------------------------------------------------------------


class TestAirToWaterReynolds:
    """Test that switching material from air to water yields a new Re."""

    def test_air_vs_water_reynolds(self):
        computer = DerivedValueComputer()

        # Air at 20 C: nu = 1.5e-5
        spec_air = _make_spec(material="air", velocity=1.0, diameter=0.1)
        re_air, formula_air = computer.compute(
            "/physics/reynolds_number", spec_air
        )
        assert re_air is not None
        assert formula_air == "Re = U * D / nu"
        # Re_air = 1.0 * 0.1 / 1.5e-5
        assert re_air == pytest.approx(1.0 * 0.1 / 1.5e-5)

        # Water at 20 C: nu = 1.0e-6
        spec_water = _make_spec(material="water", velocity=1.0, diameter=0.1)
        re_water, formula_water = computer.compute(
            "/physics/reynolds_number", spec_water
        )
        assert re_water is not None
        assert re_water == pytest.approx(1.0 * 0.1 / 1.0e-6)

        # The Reynolds numbers must differ.
        assert re_air != re_water
        # Water has lower nu, so Re should be higher.
        assert re_water > re_air

    def test_material_change_recomputes_density_and_viscosity(self):
        """Switching to water should yield water's rho and nu."""
        computer = DerivedValueComputer()

        spec_water = _make_spec(material="water")
        rho, _ = computer.compute("/physics/density", spec_water)
        nu, _ = computer.compute("/physics/kinematic_viscosity", spec_water)
        assert rho == pytest.approx(998.2)
        assert nu == pytest.approx(1.0e-6)

        spec_air = _make_spec(material="air")
        rho, _ = computer.compute("/physics/density", spec_air)
        nu, _ = computer.compute("/physics/kinematic_viscosity", spec_air)
        assert rho == pytest.approx(1.225)
        assert nu == pytest.approx(1.5e-5)

    def test_full_report_air_to_water(self):
        """End-to-end: changing material from air to water produces a
        report with cascading derived values and invalidation."""
        graph = DependencyGraph()
        computer = DerivedValueComputer()
        engine = InvalidationEngine(RuleRegistry())
        builder = ReportBuilder()

        # Post-change spec (water).
        spec = _make_spec(material="water", velocity=1.0, diameter=0.1)
        report = builder.build_report(
            changes=["/physics/material"],
            spec_dict=spec,
            graph=graph,
            computer=computer,
            invalidation_engine=engine,
        )

        # Derived values should include density, viscosity, and Re.
        recompute_paths = [p for p, _ in report.derived_recompute_needed]
        assert "/physics/density" in recompute_paths
        assert "/physics/kinematic_viscosity" in recompute_paths
        assert "/physics/reynolds_number" in recompute_paths

        # Case and results should be invalidated.
        assert report.invalidation_status["case"] == str(
            InvalidationStatus.NEEDS_RECOMPILE
        )
        assert report.invalidation_status["results"] == str(
            InvalidationStatus.NEEDS_RERUN
        )

        # Summary should mention the cascading effects.
        assert len(report.summary) > 0


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Miscellaneous edge-case coverage."""

    def test_add_custom_node_and_edge(self):
        graph = DependencyGraph()
        node = DependencyNode(
            path="/custom/path", value_type="float", description="custom"
        )
        graph.add_node(node)
        assert graph.get_node("/custom/path") is not None

        graph.add_edge(DependencyEdge(
            source_path="/custom/path",
            target_path="/custom/dependent",
            rule_id="custom_rule",
            edge_type="derives",
        ))
        assert "/custom/dependent" in graph.get_dependents("/custom/path")
        assert "/custom/path" in graph.get_dependencies("/custom/dependent")

    def test_duplicate_edge_not_added(self):
        graph = DependencyGraph()
        initial_edge_count = len(graph.get_edges())
        edge = DependencyEdge(
            source_path="/custom/a",
            target_path="/custom/b",
            rule_id="dup_rule",
            edge_type="derives",
        )
        graph.add_edge(edge)
        after_first = len(graph.get_edges())
        graph.add_edge(edge)  # same edge again
        after_second = len(graph.get_edges())
        assert after_first == after_second

    def test_invalidation_severity_resolution(self):
        """When multiple rules hit the same artifact, the most severe wins."""
        engine = InvalidationEngine(RuleRegistry())
        # Both geometry and material changes -> case gets NEEDS_RECOMPILE.
        # results gets NEEDS_RERUN (from both geometry and material).
        artifacts = {"mesh": "valid", "case": "valid", "results": "valid"}
        result = engine.analyze(
            ["/geometry", "/physics/material"], artifacts
        )
        assert result["case"] == InvalidationStatus.NEEDS_RECOMPILE
        assert result["results"] == InvalidationStatus.NEEDS_RERUN
        assert result["mesh"] == InvalidationStatus.NEEDS_RECOMPILE

    def test_invalidation_rule_model(self):
        """InvalidationRule is a proper pydantic model."""
        rule = InvalidationRule(
            source_path="/physics/material",
            artifact_type="case",
            status=InvalidationStatus.NEEDS_RECOMPILE,
            reason="test",
        )
        assert rule.source_path == "/physics/material"
        assert rule.artifact_type == "case"
        assert rule.status == InvalidationStatus.NEEDS_RECOMPILE
        assert rule.reason == "test"

    def test_dependency_rule_model_frozen(self):
        """DependencyRule is frozen (immutable)."""
        rule = DependencyRule(
            rule_id="test",
            source_paths=["/a"],
            target_path="/b",
            formula="b = f(a)",
            description="test",
            rule_type="derive",
        )
        with pytest.raises(Exception):
            rule.rule_id = "changed"  # type: ignore[misc]

    def test_empty_changes_no_invalidation(self):
        engine = InvalidationEngine(RuleRegistry())
        result = engine.analyze([], {"case": "valid"})
        assert result.get("case", InvalidationStatus.VALID) == InvalidationStatus.VALID

    def test_compute_output_count_exact(self):
        computer = DerivedValueComputer()
        assert computer.compute_output_count(10.0, 0.5) == 20
        assert computer.compute_output_count(10.0, 3.0) == 3
        assert computer.compute_output_count(7.0, 3.0) == 2
