"""Tests for the Dynamic Schema Engine (P2)."""

import pytest

from fluid_scientist.dynamic_schema.ontology import (
    OntologyEntry,
    ParameterCategory,
    ParameterOntology,
    default_ontology,
)
from fluid_scientist.dynamic_schema.physics_composition import (
    PhysicsModuleComposition,
    check_solver_capability,
    compose_physics_modules,
    get_solver_capability,
    get_turbulence_model,
    handle_unknown_scenario,
    list_solvers,
    list_turbulence_models,
    recommend_turbulence_model,
)
from fluid_scientist.dynamic_schema.schema_engine import (
    SchemaGenerationResult,
    detect_experiment_type,
    generate_schema,
)
from fluid_scientist.experiment_spec.models import (
    Compressibility,
    PhaseType,
    PhysicsSpec,
    TemporalType,
)

# --- Ontology tests ---


class TestParameterOntology:
    def test_default_ontology_has_entries(self):
        ont = default_ontology()
        assert len(ont) > 0
        assert "diameter" in ont
        assert "reynolds_number" in ont

    def test_get_entry(self):
        ont = default_ontology()
        entry = ont.get("diameter")
        assert entry is not None
        assert entry.category == ParameterCategory.GEOMETRY
        assert entry.unit == "m"

    def test_get_nonexistent(self):
        ont = default_ontology()
        assert ont.get("nonexistent") is None

    def test_by_category(self):
        ont = default_ontology()
        geometry = ont.by_category(ParameterCategory.GEOMETRY)
        assert len(geometry) >= 2
        assert all(e.category == ParameterCategory.GEOMETRY for e in geometry)

    def test_all_ids_sorted(self):
        ont = default_ontology()
        ids = ont.all_ids()
        assert ids == tuple(sorted(ids))

    def test_register_duplicate_raises(self):
        ont = ParameterOntology()
        entry = OntologyEntry(
            parameter_id="x",
            display_name="X",
            category=ParameterCategory.GEOMETRY,
        )
        ont.register(entry)
        with pytest.raises(ValueError, match="already registered"):
            ont.register(entry)

    def test_dependencies_of(self):
        ont = default_ontology()
        deps = ont.dependencies_of("reynolds_number")
        assert "diameter" in deps
        assert "inlet_velocity" in deps

    def test_dependents_of(self):
        ont = default_ontology()
        dependents = ont.dependents_of("diameter")
        assert "reynolds_number" in dependents

    def test_code_bindings_for(self):
        ont = default_ontology()
        bindings = ont.code_bindings_for(["diameter", "cells_radial"])
        assert "system/blockMeshDict" in bindings

    def test_normalize_unit_length(self):
        ont = default_ontology()
        # 100 mm should be 0.1 m
        result = ont.normalize_unit("diameter", 100.0, "mm")
        assert result == pytest.approx(0.1)

    def test_normalize_unit_velocity(self):
        ont = default_ontology()
        result = ont.normalize_unit("inlet_velocity", 3.6, "km/h")
        assert result == pytest.approx(1.0)

    def test_normalize_unit_unknown_raises(self):
        ont = default_ontology()
        with pytest.raises(ValueError, match="cannot convert"):
            ont.normalize_unit("diameter", 1.0, "lightyear")

    def test_normalize_unit_same_unit_noop(self):
        ont = default_ontology()
        result = ont.normalize_unit("diameter", 0.5, "m")
        assert result == 0.5

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError, match="less than"):
            OntologyEntry(
                parameter_id="bad",
                display_name="Bad",
                category=ParameterCategory.GEOMETRY,
                typical_range_min=10.0,
                typical_range_max=1.0,
            )


# --- Schema engine tests ---


class TestDetectExperimentType:
    def test_detect_cylinder(self):
        physics = PhysicsSpec()
        params = {"diameter": 0.1, "cells_wake": 120}
        assert detect_experiment_type(physics, params) == "cylinder_flow"

    def test_detect_pipe(self):
        physics = PhysicsSpec()
        params = {"length": 1.0, "axial_cells": 80}
        assert detect_experiment_type(physics, params) == "laminar_pipe"

    def test_detect_cavity(self):
        physics = PhysicsSpec()
        params = {"side_length": 0.1, "lid_velocity": 1.0}
        assert detect_experiment_type(physics, params) == "lid_driven_cavity"

    def test_detect_unknown_no_params(self):
        physics = PhysicsSpec()
        assert detect_experiment_type(physics, {}) == "unknown"


class TestGenerateSchema:
    def test_basic_schema_generation(self):
        physics = PhysicsSpec()
        result = generate_schema(physics)
        assert isinstance(result, SchemaGenerationResult)
        assert len(result.parameters) > 0
        assert result.solver_recommendation == "simpleFoam"

    def test_schema_with_reynolds_laminar(self):
        physics = PhysicsSpec()
        result = generate_schema(
            physics,
            existing_params={"reynolds_number": 100.0},
        )
        assert result.turbulence_model is None

    def test_schema_with_reynolds_turbulent(self):
        physics = PhysicsSpec()
        result = generate_schema(
            physics,
            existing_params={"reynolds_number": 10000.0},
        )
        assert result.turbulence_model == "kOmegaSST"

    def test_schema_transient_recommends_pimple(self):
        physics = PhysicsSpec(temporal_type=TemporalType.TRANSIENT)
        result = generate_schema(physics)
        assert result.solver_recommendation == "pimpleFoam"

    def test_schema_compressible_recommends_rho(self):
        physics = PhysicsSpec(compressibility=Compressibility.COMPRESSIBLE)
        result = generate_schema(physics)
        assert "rho" in result.solver_recommendation.lower()

    def test_schema_multi_phase_unsupported(self):
        physics = PhysicsSpec(phases=PhaseType.MULTI_PHASE)
        result = generate_schema(physics)
        assert "multi_phase_flow" in result.unsupported_features
        assert any("multi_phase" in w for w in result.warnings)

    def test_schema_high_reynolds_warning(self):
        physics = PhysicsSpec()
        result = generate_schema(
            physics,
            existing_params={"reynolds_number": 2e6},
        )
        assert any("very high" in w.lower() for w in result.warnings)

    def test_schema_with_existing_values(self):
        physics = PhysicsSpec()
        result = generate_schema(
            physics,
            existing_params={"diameter": 0.2},
        )
        diam = next(p for p in result.parameters if p.parameter_id == "diameter")
        assert diam.value == 0.2

    def test_schema_unknown_type_warning(self):
        physics = PhysicsSpec()
        result = generate_schema(physics)
        assert result.experiment_type == "unknown"
        assert any("could not detect" in w.lower() for w in result.warnings)


# --- Physics composition tests ---


class TestSolverCapability:
    def test_get_solver(self):
        solver = get_solver_capability("simpleFoam")
        assert solver is not None
        assert solver.solver_name == "simpleFoam"

    def test_get_nonexistent_solver(self):
        assert get_solver_capability("nonexistent") is None

    def test_list_solvers(self):
        solvers = list_solvers()
        assert "simpleFoam" in solvers
        assert "pimpleFoam" in solvers

    def test_check_simplefoam_incompressible_steady(self):
        capable, issues = check_solver_capability(
            "simpleFoam", "incompressible", "steady"
        )
        assert capable is True
        assert len(issues) == 0

    def test_check_simplefoam_compressible_fails(self):
        capable, issues = check_solver_capability(
            "simpleFoam", "compressible", "steady"
        )
        assert capable is False
        assert any("compressible" in i for i in issues)

    def test_check_simplefoam_transient_fails(self):
        capable, issues = check_solver_capability(
            "simpleFoam", "incompressible", "transient"
        )
        assert capable is False
        assert any("transient" in i for i in issues)

    def test_check_unknown_solver(self):
        capable, issues = check_solver_capability("nonexistent")
        assert capable is False
        assert any("unknown" in i for i in issues)


class TestTurbulenceModels:
    def test_get_turbulence_model(self):
        model = get_turbulence_model("kOmegaSST")
        assert model is not None
        assert model.model_name == "kOmegaSST"

    def test_list_turbulence_models(self):
        models = list_turbulence_models()
        assert "kOmegaSST" in models
        assert "laminar" in models

    def test_recommend_laminar_low_re(self):
        assert recommend_turbulence_model(100.0) == "laminar"

    def test_recommend_komegasst_default(self):
        assert recommend_turbulence_model(10000.0) == "kOmegaSST"

    def test_recommend_by_features(self):
        features = frozenset({"aerospace", "external_aerodynamics"})
        model = recommend_turbulence_model(1e6, features)
        assert model == "SpalartAllmaras"


class TestComposePhysicsModules:
    def test_compose_steady_laminar(self):
        comp = compose_physics_modules("simpleFoam", 100.0)
        assert isinstance(comp, PhysicsModuleComposition)
        assert comp.solver == "simpleFoam"
        assert comp.turbulence_model is None  # Laminar

    def test_compose_steady_turbulent(self):
        comp = compose_physics_modules("simpleFoam", 10000.0)
        assert comp.turbulence_model == "kOmegaSST"

    def test_compose_transient_schemes(self):
        comp = compose_physics_modules("pimpleFoam", 5000.0)
        assert comp.discretization_schemes["ddtSchemes"] == "backward"

    def test_compose_steady_schemes(self):
        comp = compose_physics_modules("simpleFoam", 100.0)
        assert comp.discretization_schemes["ddtSchemes"] == "steadyState"

    def test_compose_internal_boundary_conditions(self):
        comp = compose_physics_modules("simpleFoam", 100.0, geometry_type="internal")
        assert "inlet" in comp.boundary_conditions
        assert "outlet" in comp.boundary_conditions

    def test_compose_external_boundary_conditions(self):
        comp = compose_physics_modules("simpleFoam", 10000.0, geometry_type="external")
        assert "farfield" in comp.boundary_conditions

    def test_compose_unknown_solver(self):
        comp = compose_physics_modules("nonexistent", 100.0)
        assert len(comp.compatibility_issues) > 0
        assert any("unknown" in i for i in comp.compatibility_issues)

    def test_compose_unknown_reynolds_defaults(self):
        comp = compose_physics_modules("simpleFoam", None)
        assert comp.turbulence_model == "kOmegaSST"
        assert any("unknown" in w.lower() for w in comp.warnings)

    def test_compose_linear_solvers(self):
        comp = compose_physics_modules("simpleFoam", 100.0)
        assert "U" in comp.linear_solvers
        assert "p" in comp.linear_solvers


class TestUnknownScenarioFallback:
    def test_cylinder_keyword_match(self):
        result = handle_unknown_scenario("研究圆柱绕流")
        assert result["closest_match"] == "cylinder_flow"
        assert result["risk_level"] == "low"

    def test_pipe_keyword_match(self):
        result = handle_unknown_scenario("pipe flow analysis")
        assert result["closest_match"] == "laminar_pipe"

    def test_cavity_keyword_match(self):
        result = handle_unknown_scenario("lid driven cavity")
        assert result["closest_match"] == "lid_driven_cavity"

    def test_no_match_high_risk(self):
        result = handle_unknown_scenario("combustion in gas turbine")
        assert result["closest_match"] == "unknown"
        assert result["risk_level"] == "high"
        assert len(result["required_custom_parameters"]) > 0

    def test_recommendations_not_empty(self):
        result = handle_unknown_scenario("unknown flow")
        assert len(result["recommendations"]) > 0

    def test_high_risk_includes_pilot_warning(self):
        result = handle_unknown_scenario("something completely new")
        assert any("pilot" in r.lower() for r in result["recommendations"])
