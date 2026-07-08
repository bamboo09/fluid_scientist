"""Tests for experiment-type-specific parameter list generation (Commit 1).

Verifies that the Dynamic Schema Engine generates experiment-type-specific
parameter lists instead of including ALL parameters from ALL categories.
"""

from __future__ import annotations

from types import SimpleNamespace

from fluid_scientist.dynamic_schema.ontology import default_ontology
from fluid_scientist.dynamic_schema.schema_engine import (
    detect_experiment_type,
    generate_schema,
)
from fluid_scientist.experiment_spec.models import (
    Compressibility,
    PhaseType,
    PhysicsSpec,
    TemporalType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_physics() -> PhysicsSpec:
    """Create a PhysicsSpec with valid enum values for schema generation."""
    return PhysicsSpec(
        compressibility=Compressibility.INCOMPRESSIBLE,
        temporal_type=TemporalType.TRANSIENT,
        phases=PhaseType.SINGLE_PHASE,
    )


def _param_ids(result) -> set[str]:
    """Extract parameter IDs from a schema generation result."""
    return {p.parameter_id for p in result.parameters}


# ---------------------------------------------------------------------------
# Test 1: cylinder_flow excludes pipe and cavity parameters
# ---------------------------------------------------------------------------


class TestCylinderFlowExcludesPipeAndCavityParams:
    """Cylinder flow should NOT include pipe or cavity specific parameters."""

    def test_cylinder_flow_excludes_pipe_params(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"diameter": 0.1, "domain_width": 10.0},
        )
        assert result.experiment_type == "cylinder_flow"
        param_ids = _param_ids(result)

        # Cylinder-specific params should be present
        assert "diameter" in param_ids
        assert "domain_width" in param_ids
        assert "cells_wake" in param_ids

        # Pipe-specific params should NOT be present
        assert "length" not in param_ids
        assert "axial_cells" not in param_ids
        assert "radial_cells" not in param_ids
        assert "mass_flow_rate" not in param_ids
        assert "outlet_pressure" not in param_ids
        assert "mean_velocity" not in param_ids

    def test_cylinder_flow_excludes_cavity_params(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"diameter": 0.1, "cells_wake": 120},
        )
        assert result.experiment_type == "cylinder_flow"
        param_ids = _param_ids(result)

        # Cavity-specific params should NOT be present
        assert "side_length" not in param_ids
        assert "lid_velocity" not in param_ids
        assert "cells_per_side" not in param_ids


# ---------------------------------------------------------------------------
# Test 2: laminar_pipe excludes cylinder and cavity parameters
# ---------------------------------------------------------------------------


class TestLaminarPipeExcludesCylinderAndCavityParams:
    """Laminar pipe flow should NOT include cylinder or cavity specific parameters."""

    def test_laminar_pipe_excludes_cylinder_params(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"length": 1.0, "axial_cells": 80},
        )
        assert result.experiment_type == "laminar_pipe"
        param_ids = _param_ids(result)

        # Pipe-specific params should be present
        assert "diameter" in param_ids
        assert "length" in param_ids
        assert "axial_cells" in param_ids
        assert "radial_cells" in param_ids
        assert "mean_velocity" in param_ids

        # Cylinder-specific params should NOT be present
        assert "domain_width" not in param_ids
        assert "domain_height" not in param_ids
        assert "cells_wake" not in param_ids
        assert "cells_radial" not in param_ids
        assert "inlet_velocity" not in param_ids
        assert "strouhal_number" not in param_ids

    def test_laminar_pipe_excludes_cavity_params(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"length": 1.0, "mean_velocity": 0.1},
        )
        assert result.experiment_type == "laminar_pipe"
        param_ids = _param_ids(result)

        # Cavity-specific params should NOT be present
        assert "side_length" not in param_ids
        assert "lid_velocity" not in param_ids
        assert "cells_per_side" not in param_ids


# ---------------------------------------------------------------------------
# Test 3: lid_driven_cavity excludes pipe and cylinder parameters
# ---------------------------------------------------------------------------


class TestLidDrivenCavityExcludesPipeAndCylinderParams:
    """Lid-driven cavity should NOT include pipe or cylinder specific parameters."""

    def test_cavity_excludes_pipe_params(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"side_length": 0.1, "lid_velocity": 1.0},
        )
        assert result.experiment_type == "lid_driven_cavity"
        param_ids = _param_ids(result)

        # Cavity-specific params should be present
        assert "side_length" in param_ids
        assert "lid_velocity" in param_ids
        assert "cells_per_side" in param_ids

        # Pipe-specific params should NOT be present
        assert "diameter" not in param_ids
        assert "length" not in param_ids
        assert "mean_velocity" not in param_ids
        assert "axial_cells" not in param_ids
        assert "radial_cells" not in param_ids
        assert "mass_flow_rate" not in param_ids
        assert "outlet_pressure" not in param_ids

    def test_cavity_excludes_cylinder_params(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"side_length": 0.1, "lid_velocity": 1.0},
        )
        param_ids = _param_ids(result)

        # Cylinder-specific params should NOT be present
        assert "domain_width" not in param_ids
        assert "domain_height" not in param_ids
        assert "inlet_velocity" not in param_ids
        assert "cells_radial" not in param_ids
        assert "cells_wake" not in param_ids
        assert "strouhal_number" not in param_ids


# ---------------------------------------------------------------------------
# Test 4: unknown experiment type includes all parameters (fallback)
# ---------------------------------------------------------------------------


class TestUnknownIncludesAllParameters:
    """Unknown experiment type should include all parameters as a fallback."""

    def test_unknown_includes_all_ontology_params(self):
        physics = _make_physics()
        result = generate_schema(physics)
        assert result.experiment_type == "unknown"
        param_ids = _param_ids(result)

        # Should include ALL parameters from the ontology
        ont = default_ontology()
        all_ids = set(ont.all_ids())
        assert param_ids == all_ids

    def test_unknown_includes_pipe_and_cylinder_and_cavity_params(self):
        physics = _make_physics()
        result = generate_schema(physics)
        param_ids = _param_ids(result)

        # Should include params from all experiment types
        assert "diameter" in param_ids
        assert "length" in param_ids
        assert "side_length" in param_ids
        assert "domain_width" in param_ids
        assert "domain_height" in param_ids
        assert "cells_wake" in param_ids
        assert "axial_cells" in param_ids
        assert "cells_per_side" in param_ids
        assert "lid_velocity" in param_ids
        assert "mass_flow_rate" in param_ids
        assert "outlet_pressure" in param_ids


# ---------------------------------------------------------------------------
# Test 5: cylinder_flow includes domain_width and domain_height
# ---------------------------------------------------------------------------


class TestCylinderFlowIncludesDomainParams:
    """Cylinder flow should include domain_width and domain_height."""

    def test_cylinder_flow_includes_domain_width(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"diameter": 0.1, "domain_width": 10.0},
        )
        param_ids = _param_ids(result)
        assert "domain_width" in param_ids

    def test_cylinder_flow_includes_domain_height(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"diameter": 0.1, "domain_width": 10.0},
        )
        param_ids = _param_ids(result)
        assert "domain_height" in param_ids


# ---------------------------------------------------------------------------
# Test 6: laminar_pipe includes mass_flow_rate and outlet_pressure
# ---------------------------------------------------------------------------


class TestLaminarPipeIncludesNewBoundaryConditions:
    """Laminar pipe flow should include mass_flow_rate and outlet_pressure."""

    def test_laminar_pipe_includes_mass_flow_rate(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"length": 1.0, "axial_cells": 80},
        )
        param_ids = _param_ids(result)
        assert "mass_flow_rate" in param_ids

    def test_laminar_pipe_includes_outlet_pressure(self):
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"length": 1.0, "axial_cells": 80},
        )
        param_ids = _param_ids(result)
        assert "outlet_pressure" in param_ids


# ---------------------------------------------------------------------------
# Test 7: detect_experiment_type correctly identifies types from geometry params
# ---------------------------------------------------------------------------


class TestDetectExperimentTypeFromGeometryParams:
    """detect_experiment_type should identify types from geometry parameters."""

    def test_detect_cylinder_from_diameter_and_cells_wake(self):
        physics = PhysicsSpec()
        params = {"diameter": 0.1, "cells_wake": 120}
        assert detect_experiment_type(physics, params) == "cylinder_flow"

    def test_detect_cylinder_from_diameter_and_domain_width(self):
        physics = PhysicsSpec()
        params = {"diameter": 0.1, "domain_width": 10.0}
        assert detect_experiment_type(physics, params) == "cylinder_flow"

    def test_detect_cylinder_from_diameter_and_domain_height(self):
        physics = PhysicsSpec()
        params = {"diameter": 0.1, "domain_height": 20.0}
        assert detect_experiment_type(physics, params) == "cylinder_flow"

    def test_detect_pipe_from_length_and_axial_cells(self):
        physics = PhysicsSpec()
        params = {"length": 1.0, "axial_cells": 80}
        assert detect_experiment_type(physics, params) == "laminar_pipe"

    def test_detect_pipe_from_length_and_mean_velocity(self):
        physics = PhysicsSpec()
        params = {"length": 1.0, "mean_velocity": 0.1}
        assert detect_experiment_type(physics, params) == "laminar_pipe"

    def test_detect_pipe_from_length_and_mass_flow_rate(self):
        physics = PhysicsSpec()
        params = {"length": 1.0, "mass_flow_rate": 0.5}
        assert detect_experiment_type(physics, params) == "laminar_pipe"

    def test_detect_cavity_from_side_length_and_lid_velocity(self):
        physics = PhysicsSpec()
        params = {"side_length": 0.1, "lid_velocity": 1.0}
        assert detect_experiment_type(physics, params) == "lid_driven_cavity"

    def test_detect_unknown_with_no_matching_params(self):
        physics = PhysicsSpec()
        params = {"diameter": 0.1}
        assert detect_experiment_type(physics, params) == "unknown"

    def test_detect_unknown_with_empty_params(self):
        physics = PhysicsSpec()
        assert detect_experiment_type(physics, {}) == "unknown"


# ---------------------------------------------------------------------------
# Test 8: detect_experiment_type correctly identifies types from flow_regime
# ---------------------------------------------------------------------------


class TestDetectExperimentTypeFromFlowRegime:
    """detect_experiment_type should identify types from physics spec flow_regime.

    Uses SimpleNamespace to simulate physics objects with flow_regime values
    that are not part of the standard FlowRegime enum but are recognized by
    the detection logic (e.g., 'external_flow', 'internal_pipe', 'cavity_flow').
    """

    def test_external_flow_detected_as_cylinder(self):
        physics = SimpleNamespace(
            flow_regime=SimpleNamespace(value="external_flow"),
        )
        assert detect_experiment_type(physics, {}) == "cylinder_flow"

    def test_external_detected_as_cylinder(self):
        physics = SimpleNamespace(
            flow_regime=SimpleNamespace(value="external"),
        )
        assert detect_experiment_type(physics, {}) == "cylinder_flow"

    def test_internal_pipe_detected_as_laminar_pipe(self):
        physics = SimpleNamespace(
            flow_regime=SimpleNamespace(value="internal_pipe"),
        )
        assert detect_experiment_type(physics, {}) == "laminar_pipe"

    def test_internal_flow_detected_as_laminar_pipe(self):
        physics = SimpleNamespace(
            flow_regime=SimpleNamespace(value="internal_flow"),
        )
        assert detect_experiment_type(physics, {}) == "laminar_pipe"

    def test_cavity_flow_detected_as_lid_driven_cavity(self):
        physics = SimpleNamespace(
            flow_regime=SimpleNamespace(value="cavity_flow"),
        )
        assert detect_experiment_type(physics, {}) == "lid_driven_cavity"

    def test_cavity_detected_as_lid_driven_cavity(self):
        physics = SimpleNamespace(
            flow_regime=SimpleNamespace(value="cavity"),
        )
        assert detect_experiment_type(physics, {}) == "lid_driven_cavity"

    def test_standard_flow_regime_values_return_unknown(self):
        """Standard FlowRegime enum values (laminar, turbulent, transitional)
        should not match any specific experiment type via flow_regime."""
        from fluid_scientist.experiment_spec.models import FlowRegime

        for regime in FlowRegime:
            physics = PhysicsSpec(flow_regime=regime)
            assert detect_experiment_type(physics, {}) == "unknown"
