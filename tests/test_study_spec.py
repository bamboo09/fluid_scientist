"""Comprehensive tests for the ``fluid_scientist.study_spec`` package.

Covers:
* SimulationStudySpec creation with all fields populated.
* TimeControl validation (duration = end - start).
* SourcedValue status hierarchy and override logic.
* GeometryEntity preserving semantic_type separately from primitive type.
* Schema export producing a valid JSON Schema.
* Path registry containing expected paths (e.g. /numerics/time/end_time).
* Legacy migration from a cylinder-flow spec dict.
* Version management (create, get, list).
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from fluid_scientist.study_spec import (
    BoundaryCondition,
    BoundaryDefinition,
    ExecutionDefinition,
    GeometryDefinition,
    GeometryEntity,
    LegacyMigrator,
    MeshDefinition,
    NumericsDefinition,
    ObservationDefinition,
    ObservationTarget,
    PhysicsDefinition,
    PlacementSpec,
    ProbeSpec,
    Quantity,
    SchemaExporter,
    SimulationStudySpec,
    SpecProvenance,
    SourcedValue,
    StudyDefinition,
    TimeControl,
    TimeWindow,
    ValidationDefinition,
    VersionedSpecStore,
    should_override,
    status_priority,
)
from fluid_scientist.study_spec.geometry import DomainSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_sourced(value, unit=None, status="user_explicit", confidence=0.9):
    """Helper to build a SourcedValue quickly."""
    return SourcedValue(
        value=value,
        unit=unit,
        status=status,
        source_turn_ids=["turn_0"],
        confidence=confidence,
    )


def make_study_spec(**overrides):
    """Build a fully-populated SimulationStudySpec for testing."""
    study = StudyDefinition(
        title="Cylinder Flow Re=100",
        objective="Investigate vortex shedding behind a cylinder",
        research_questions=[
            "What is the Strouhal number at Re=100?",
            "How does the wake develop downstream?",
        ],
    )
    physics = PhysicsDefinition(
        material=make_sourced("water", status="user_confirmed"),
        density=make_sourced(998.2, unit="kg/m^3", status="user_confirmed"),
        kinematic_viscosity=make_sourced(1.0e-6, unit="m^2/s", status="derived"),
        reynolds_number=make_sourced(100.0, status="derived"),
        velocity=make_sourced(0.1, unit="m/s", status="derived"),
        characteristic_length=make_sourced(0.001, unit="m", status="derived"),
    )
    geometry = GeometryDefinition(
        domain=DomainSpec(
            length=make_sourced(12.0, unit="m"),
            width=make_sourced(8.0, unit="m"),
            dimensions="2d",
        ),
        entities={
            "cylinder": GeometryEntity(
                entity_id="cylinder",
                semantic_type="cylinder_2d",
                primitive={"type": "circle", "radius": 0.2, "diameter": 0.4},
                original_user_semantics="cylinder",
                placement=PlacementSpec(
                    x=make_sourced(4.0, unit="m"),
                    y=make_sourced(4.0, unit="m"),
                ),
            ),
        },
        relations=[],
    )
    boundaries = BoundaryDefinition(
        conditions=[
            BoundaryCondition(
                patch_name="inlet",
                role="inlet",
                bc_type="velocityInlet",
                parameters={"velocity": 0.1},
                source_status="user_explicit",
            ),
            BoundaryCondition(
                patch_name="outlet",
                role="outlet",
                bc_type="pressureOutlet",
                parameters={"pressure": 0.0},
                source_status="derived",
            ),
            BoundaryCondition(
                patch_name="top",
                role="wall",
                bc_type="slipWall",
                parameters={},
                source_status="model_recommended",
            ),
            BoundaryCondition(
                patch_name="bottom",
                role="wall",
                bc_type="slipWall",
                parameters={},
                source_status="model_recommended",
            ),
            BoundaryCondition(
                patch_name="cylinder",
                role="wall",
                bc_type="noSlipWall",
                parameters={},
                source_status="derived",
            ),
            BoundaryCondition(
                patch_name="front",
                role="empty",
                bc_type="empty",
                parameters={},
                source_status="derived",
            ),
            BoundaryCondition(
                patch_name="back",
                role="empty",
                bc_type="empty",
                parameters={},
                source_status="derived",
            ),
        ],
    )
    numerics = NumericsDefinition(
        time=TimeControl(
            mode="transient",
            start_time=Quantity(value=0.0, unit="s"),
            end_time=Quantity(value=10.0, unit="s"),
            delta_t=Quantity(value=0.01, unit="s"),
            adaptive=False,
            max_courant=0.5,
            write_control="runTime",
            write_interval=Quantity(value=0.1, unit="s"),
        ),
        solver="icoFoam",
        discretization={
            "ddtSchemes": {"ddtScheme": "backward"},
            "gradSchemes": {"gradScheme": "Gauss linear"},
        },
        turbulence_model="laminar",
    )
    mesh = MeshDefinition(
        resolution=make_sourced(1200, unit="cells", status="derived"),
        mesh_type="blockMesh",
        refinement_regions=[],
    )
    observations = ObservationDefinition(
        targets=[
            ObservationTarget(
                target_id="drag",
                metric="cd",
                parameters={"patches": ["cylinder"]},
                function_object_type="forceCoeffs",
            ),
            ObservationTarget(
                target_id="lift",
                metric="cl",
                parameters={"patches": ["cylinder"]},
                function_object_type="forceCoeffs",
            ),
            ObservationTarget(
                target_id="strouhal",
                metric="strouhal",
                parameters={"probe": [5.0, 4.0, 0.0]},
                function_object_type="probes",
            ),
        ],
        probes=[
            ProbeSpec(
                probe_id="wake_probe_1",
                location={"x": 5.0, "y": 4.0, "z": 0.0},
                field="U",
            ),
        ],
        postprocessing=["streamlines", "vorticity"],
    )
    execution = ExecutionDefinition(
        target_id="workstation",
        parallel=False,
        cores=None,
    )
    validation = ValidationDefinition(checks=["courant_number", "mass_balance"])
    provenance = SpecProvenance(
        created_at="2026-01-01T00:00:00+00:00",
        created_by="test_user",
        parent_version=None,
        creation_turn_id="turn_0",
    )

    defaults = dict(
        spec_id="test_spec_001",
        session_id="session_001",
        version=1,
        parent_version=None,
        study=study,
        physics=physics,
        geometry=geometry,
        boundaries=boundaries,
        initial_conditions=[],
        numerics=numerics,
        mesh=mesh,
        observations=observations,
        execution=execution,
        validation=validation,
        extensions={},
        provenance=provenance,
    )
    defaults.update(overrides)
    return SimulationStudySpec(**defaults)


def make_legacy_spec():
    """Build a legacy CylinderFlow2DExperimentSpecV1-like dict."""
    return {
        "schema_version": "1.0",
        "spec_version": 1,
        "case_family": "cylinder_flow_2d",
        "pipeline_id": "cylinder-flow-2d-v1",
        "pipeline_stage": "DRAFT_NORMALIZED",
        "experiment_id": "legacy_exp_001",
        "spec_id": "legacy_session_001",
        "title": "Legacy Cylinder Flow",
        "objective": "Study vortex shedding",
        "user_input_text": "simulate flow past a cylinder at Re=200",
        "domain": {
            "length_m": {"value": 12.0, "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.5, "reason": None},
            "height_m": {"value": 8.0, "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.6, "reason": None},
            "thickness_m": {"value": 1.0, "source": "SYSTEM_DEFAULT", "status": "RESOLVED", "confidence": 1.0, "reason": "2D single-layer extrusion default"},
            "dimensionality": "2D",
        },
        "fluid": {
            "type": {"value": "water", "source": "USER_CONFIRMED", "status": "RESOLVED", "confidence": 0.7, "reason": "default water"},
            "temperature_c": {"value": 20.0, "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.7, "reason": None},
            "density_kg_m3": {"value": 998.0, "source": "USER_CONFIRMED", "status": "RESOLVED", "confidence": 0.7, "reason": None},
            "kinematic_viscosity_m2_s": {"value": 0.002, "source": "FORMULA_DERIVED", "status": "RESOLVED", "confidence": 1.0, "reason": "derived from Re=200"},
        },
        "cylinder": {
            "type": "cylinder",
            "radius_m": {"value": 0.2, "source": "USER_EXPLICIT", "status": "RESOLVED", "confidence": 1.0, "reason": "user specified"},
            "diameter_m": {"value": 0.4, "source": "FORMULA_DERIVED", "status": "RESOLVED", "confidence": 1.0, "reason": "D=2R"},
            "characteristic_dimension_m": {"value": 0.4, "source": "FORMULA_DERIVED", "status": "RESOLVED", "confidence": 1.0, "reason": "char dim = diameter"},
            "center_x_m": {"value": 4.0, "source": "USER_EXPLICIT", "status": "RESOLVED", "confidence": 1.0, "reason": "user specified"},
            "center_y_m": {"value": 4.0, "source": "USER_EXPLICIT", "status": "RESOLVED", "confidence": 1.0, "reason": "user specified"},
            "wall_type": "no_slip_wall",
            "angular_velocity_rad_s": 0.0,
            "rotation_direction": "ccw",
        },
        "rectangle": {"enabled": False},
        "triangle": {"enabled": False},
        "trapezoid": {"enabled": False},
        "bottom_profile": {"enabled": False, "profile_type": "flat"},
        "flow_topology": {"mode": "inlet_outlet"},
        "boundaries": {
            "left": {"semantic_type": "uniform_velocity_inlet", "source": "USER_EXPLICIT", "status": "RESOLVED", "confidence": 1.0, "inlet_velocity": 1.0},
            "right": {"semantic_type": "pressure_outlet", "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.8, "pressure_value": 0.0},
            "top": {"semantic_type": "slip_wall", "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.8},
            "bottom_flat": {"semantic_type": "slip_wall", "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.8},
            "bottom_profile_surface": {"semantic_type": None, "source": "SYSTEM_DEFAULT", "status": "UNRESOLVED", "confidence": 0.0},
            "front": {"semantic_type": "empty", "source": "SYSTEM_DERIVED", "status": "RESOLVED", "confidence": 1.0, "reason": "2D"},
            "back": {"semantic_type": "empty", "source": "SYSTEM_DERIVED", "status": "RESOLVED", "confidence": 1.0, "reason": "2D"},
        },
        "forcing": {"pressure_gradient": {"enabled": False}, "body_force": {"enabled": False}},
        "inlet_profile": {"enabled": False, "temporal_type": "constant", "spatial_type": "uniform", "parameters": {}},
        "initial_conditions": {"velocity": {"type": "quiescent", "vector_m_s": [0.0, 0.0, 0.0]}},
        "simulation": {"time_mode": "auto", "flow_regime": "auto", "max_courant_number": 0.5, "end_time": 10.0, "delta_t": 0.01},
        "observables": [
            {"type": "cylinder_drag", "label": "drag", "component": "Ux", "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.5},
            {"type": "cylinder_lift", "label": "lift", "component": "Uy", "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.5},
            {"type": "wake_shedding_frequency", "label": "strouhal", "component": "Uy", "source": "MODEL_RECOMMENDED", "status": "AWAITING_CONFIRMATION", "confidence": 0.5},
        ],
        "analysis_goals": [{"id": "goal1", "description": "What is the Strouhal number?"}],
        "assumptions": ["flow is incompressible"],
        "ambiguities": [],
        "unresolved_fields": [],
        "blocking_issues": [],
        "recommendations": [],
        "decision_summary": {"facts": [], "derived_values": [], "assumptions": [], "alternatives": [], "unresolved_items": [], "rejected_interpretations": [], "confidence": 0.5},
        "draft_status": "NEEDS_CLARIFICATION",
    }


# ---------------------------------------------------------------------------
# Tests: SimulationStudySpec creation
# ---------------------------------------------------------------------------


class TestSimulationStudySpecCreation:
    """Tests for SimulationStudySpec creation with all fields."""

    def test_full_spec_creation(self):
        """A fully-populated spec should be created without error."""
        spec = make_study_spec()
        assert spec.spec_id == "test_spec_001"
        assert spec.schema_version == "1.0"
        assert spec.version == 1
        assert spec.study.title == "Cylinder Flow Re=100"
        assert spec.physics.material.value == "water"
        assert spec.numerics.solver == "icoFoam"
        assert spec.numerics.turbulence_model == "laminar"
        assert len(spec.boundaries.conditions) == 7
        assert len(spec.observations.targets) == 3
        assert len(spec.observations.probes) == 1

    def test_spec_serialization_roundtrip(self):
        """Spec should survive model_dump / model_validate roundtrip."""
        spec = make_study_spec()
        data = spec.model_dump()
        restored = SimulationStudySpec.model_validate(data)
        assert restored.spec_id == spec.spec_id
        assert restored.study.title == spec.study.title
        assert restored.numerics.solver == spec.numerics.solver
        assert restored.geometry.entities["cylinder"].semantic_type == "cylinder_2d"

    def test_spec_json_roundtrip(self):
        """Spec should survive JSON serialization roundtrip."""
        import json

        spec = make_study_spec()
        json_str = spec.model_dump_json()
        restored = SimulationStudySpec.model_validate_json(json_str)
        assert restored.spec_id == spec.spec_id
        assert json.loads(json_str)["spec_id"] == "test_spec_001"

    def test_extra_fields_forbidden(self):
        """Passing an unknown field should raise ValidationError."""
        with pytest.raises(ValidationError):
            make_study_spec(unknown_field="should_fail")

    def test_default_schema_version(self):
        """Schema version should default to '1.0'."""
        spec = make_study_spec()
        assert spec.schema_version == "1.0"

    def test_default_version(self):
        """Version should default to 1."""
        spec = make_study_spec()
        assert spec.version == 1

    def test_extensions_default_empty(self):
        """Extensions dict should default to empty."""
        spec = make_study_spec()
        assert spec.extensions == {}

    def test_provenance_preserved(self):
        """Provenance should be preserved correctly."""
        spec = make_study_spec()
        assert spec.provenance.created_by == "test_user"
        assert spec.provenance.creation_turn_id == "turn_0"
        assert spec.provenance.parent_version is None


# ---------------------------------------------------------------------------
# Tests: TimeControl validation
# ---------------------------------------------------------------------------


class TestTimeControlValidation:
    """Tests for TimeControl model validator (duration = end - start)."""

    def test_duration_derived_from_start_end(self):
        """Duration should be derived as end_time - start_time."""
        tc = TimeControl(
            mode="transient",
            start_time=Quantity(value=0.0, unit="s"),
            end_time=Quantity(value=10.0, unit="s"),
        )
        assert tc.duration is not None
        assert tc.duration.value == 10.0
        assert tc.duration.unit == "s"

    def test_duration_not_overwritten_when_explicit(self):
        """Duration should NOT be overwritten when explicitly provided."""
        tc = TimeControl(
            mode="transient",
            start_time=Quantity(value=0.0, unit="s"),
            end_time=Quantity(value=10.0, unit="s"),
            duration=Quantity(value=5.0, unit="s"),
        )
        assert tc.duration.value == 5.0

    def test_duration_not_derived_without_both_times(self):
        """Duration should not be derived when start or end is missing."""
        tc = TimeControl(
            mode="transient",
            end_time=Quantity(value=10.0, unit="s"),
        )
        assert tc.duration is None

    def test_duration_not_derived_with_unit_mismatch(self):
        """Duration should not be derived when units differ."""
        tc = TimeControl(
            mode="transient",
            start_time=Quantity(value=0.0, unit="s"),
            end_time=Quantity(value=10.0, unit="ms"),
        )
        # Unit mismatch — should not derive.
        assert tc.duration is None

    def test_statistics_window_within_range(self):
        """Statistics window within simulation range should be accepted."""
        tc = TimeControl(
            mode="transient",
            start_time=Quantity(value=0.0, unit="s"),
            end_time=Quantity(value=10.0, unit="s"),
            statistics_windows=[
                TimeWindow(
                    start=Quantity(value=2.0, unit="s"),
                    end=Quantity(value=8.0, unit="s"),
                    label="averaging",
                ),
            ],
        )
        assert len(tc.statistics_windows) == 1
        assert tc.statistics_windows[0].label == "averaging"

    def test_statistics_window_before_start_rejected(self):
        """Statistics window starting before simulation start should raise."""
        with pytest.raises(ValidationError, match="before simulation start"):
            TimeControl(
                mode="transient",
                start_time=Quantity(value=2.0, unit="s"),
                end_time=Quantity(value=10.0, unit="s"),
                statistics_windows=[
                    TimeWindow(
                        start=Quantity(value=0.0, unit="s"),
                        end=Quantity(value=5.0, unit="s"),
                        label="bad_window",
                    ),
                ],
            )

    def test_statistics_window_after_end_rejected(self):
        """Statistics window ending after simulation end should raise."""
        with pytest.raises(ValidationError, match="after simulation end"):
            TimeControl(
                mode="transient",
                start_time=Quantity(value=0.0, unit="s"),
                end_time=Quantity(value=10.0, unit="s"),
                statistics_windows=[
                    TimeWindow(
                        start=Quantity(value=8.0, unit="s"),
                        end=Quantity(value=15.0, unit="s"),
                        label="bad_window",
                    ),
                ],
            )

    def test_steady_mode(self):
        """Steady mode should be accepted without time fields."""
        tc = TimeControl(mode="steady")
        assert tc.mode == "steady"
        assert tc.duration is None

    def test_time_window_start_after_end_rejected(self):
        """TimeWindow with start > end should raise."""
        with pytest.raises(ValidationError, match="must be <="):
            TimeWindow(
                start=Quantity(value=10.0, unit="s"),
                end=Quantity(value=5.0, unit="s"),
            )


# ---------------------------------------------------------------------------
# Tests: SourcedValue status hierarchy
# ---------------------------------------------------------------------------


class TestSourcedValueStatus:
    """Tests for SourcedValue status hierarchy and override logic."""

    def test_status_priority_ordering(self):
        """user_explicit should have the highest priority."""
        assert status_priority("user_explicit") > status_priority("user_confirmed")
        assert status_priority("user_confirmed") > status_priority("derived")
        assert status_priority("derived") > status_priority("model_recommended")
        assert status_priority("model_recommended") > status_priority("default_pending")
        assert status_priority("default_pending") > status_priority("unknown")

    def test_should_override_higher_over_lower(self):
        """Higher-priority status should override lower."""
        assert should_override("unknown", "user_explicit") is True
        assert should_override("default_pending", "model_recommended") is True
        assert should_override("model_recommended", "derived") is True

    def test_should_not_override_lower_over_higher(self):
        """Lower-priority status should NOT override higher."""
        assert should_override("user_explicit", "model_recommended") is False
        assert should_override("user_confirmed", "default_pending") is False

    def test_should_not_override_equal(self):
        """Equal priority should NOT override."""
        assert should_override("user_explicit", "user_explicit") is False

    def test_is_user_provided(self):
        """is_user_provided should detect user-explicit and user-confirmed."""
        explicit = SourcedValue(value=1.0, status="user_explicit")
        confirmed = SourcedValue(value=1.0, status="user_confirmed")
        derived = SourcedValue(value=1.0, status="derived")
        assert explicit.is_user_provided() is True
        assert confirmed.is_user_provided() is True
        assert derived.is_user_provided() is False

    def test_is_resolved(self):
        """is_resolved should detect non-None values."""
        resolved = SourcedValue(value=1.0, status="derived")
        unresolved = SourcedValue(value=None, status="unknown")
        assert resolved.is_resolved() is True
        assert unresolved.is_resolved() is False

    def test_confidence_bounds(self):
        """Confidence should be bounded [0, 1]."""
        SourcedValue(value=1.0, confidence=0.0)
        SourcedValue(value=1.0, confidence=1.0)
        with pytest.raises(ValidationError):
            SourcedValue(value=1.0, confidence=1.5)
        with pytest.raises(ValidationError):
            SourcedValue(value=1.0, confidence=-0.1)

    def test_default_status(self):
        """Default status should be 'unknown'."""
        sv = SourcedValue(value=1.0)
        assert sv.status == "unknown"

    def test_default_source_turn_ids(self):
        """source_turn_ids should default to an empty list."""
        sv = SourcedValue(value=1.0)
        assert sv.source_turn_ids == []


# ---------------------------------------------------------------------------
# Tests: GeometryEntity semantic_type preservation
# ---------------------------------------------------------------------------


class TestGeometryEntity:
    """Tests for GeometryEntity preserving semantic_type separately from primitive."""

    def test_semantic_type_preserved_separately(self):
        """semantic_type and primitive.type should be independently stored."""
        entity = GeometryEntity(
            entity_id="triangle_1",
            semantic_type="triangle_2d",
            primitive={"type": "polygon", "n_vertices": 3},
            original_user_semantics="用户说的三角形",
        )
        assert entity.semantic_type == "triangle_2d"
        assert entity.primitive["type"] == "polygon"
        assert entity.primitive["n_vertices"] == 3
        assert entity.original_user_semantics == "用户说的三角形"

    def test_semantic_survives_primitive_change(self):
        """Changing the primitive should not affect semantic_type."""
        entity = GeometryEntity(
            entity_id="cyl_1",
            semantic_type="cylinder_2d",
            primitive={"type": "circle", "radius": 0.2},
            original_user_semantics="cylinder",
        )
        # Simulate a primitive re-parameterisation (e.g. circle -> polygon).
        new_entity = entity.model_copy(
            update={"primitive": {"type": "polygon", "n_vertices": 64}}
        )
        assert new_entity.semantic_type == "cylinder_2d"
        assert new_entity.primitive["type"] == "polygon"
        assert new_entity.original_user_semantics == "cylinder"

    def test_polygon_vertices_optional(self):
        """polygon_vertices should be optional."""
        entity = GeometryEntity(
            entity_id="rect_1",
            semantic_type="rectangle_2d",
            original_user_semantics="rectangle",
        )
        assert entity.polygon_vertices is None
        assert entity.primitive is None
        assert entity.placement is None

    def test_placement_with_sourced_values(self):
        """Placement should accept SourcedValue coordinates."""
        entity = GeometryEntity(
            entity_id="cyl_1",
            semantic_type="cylinder_2d",
            original_user_semantics="cylinder",
            placement=PlacementSpec(
                x=SourcedValue(value=4.0, unit="m", status="user_explicit"),
                y=SourcedValue(value=4.0, unit="m", status="user_explicit"),
                attachment="centered",
            ),
        )
        assert entity.placement is not None
        assert entity.placement.x.value == 4.0
        assert entity.placement.attachment == "centered"

    def test_geometry_relation_types(self):
        """GeometryRelation should accept all relation types."""
        from fluid_scientist.study_spec import GeometryRelation

        for rel_type in [
            "attached_to", "aligned_below", "aligned_above",
            "centered_in", "distance_to", "tangent_to",
            "inside", "outside", "intersects", "custom",
        ]:
            rel = GeometryRelation(
                relation_id=f"rel_{rel_type}",
                type=rel_type,
                subject_id="entity_a",
                object_id="entity_b",
            )
            assert rel.type == rel_type


# ---------------------------------------------------------------------------
# Tests: Schema export
# ---------------------------------------------------------------------------


class TestSchemaExport:
    """Tests for SchemaExporter producing valid JSON Schema."""

    def test_export_schema_returns_dict(self):
        """export_schema should return a dict with expected keys."""
        exporter = SchemaExporter()
        schema = exporter.export_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "type" in schema
        assert schema["type"] == "object"

    def test_export_schema_has_required_fields(self):
        """export_schema should list required fields."""
        exporter = SchemaExporter()
        schema = exporter.export_schema()
        required = schema.get("required", [])
        assert "spec_id" in required
        assert "session_id" in required
        assert "study" in required
        assert "physics" in required
        assert "geometry" in required
        assert "numerics" in required

    def test_export_schema_contains_top_level_props(self):
        """export_schema should contain all top-level properties."""
        exporter = SchemaExporter()
        schema = exporter.export_schema()
        props = schema["properties"]
        for key in [
            "schema_version", "spec_id", "session_id", "version",
            "parent_version", "study", "physics", "geometry",
            "boundaries", "initial_conditions", "numerics", "mesh",
            "observations", "execution", "validation", "extensions",
            "provenance",
        ]:
            assert key in props, f"Missing top-level property: {key}"

    def test_export_patch_schema_returns_dict(self):
        """export_patch_schema should return a placeholder dict."""
        exporter = SchemaExporter()
        patch_schema = exporter.export_patch_schema()
        assert isinstance(patch_schema, dict)
        assert patch_schema["title"] == "SimulationSpecPatch"
        assert "properties" in patch_schema
        assert "operations" in patch_schema["properties"]
        assert "patch_id" in patch_schema["properties"]

    def test_path_registry_returns_dict(self):
        """get_path_registry should return a non-empty dict."""
        exporter = SchemaExporter()
        registry = exporter.get_path_registry()
        assert isinstance(registry, dict)
        assert len(registry) > 0

    def test_path_registry_contains_expected_paths(self):
        """get_path_registry should contain key paths."""
        exporter = SchemaExporter()
        registry = exporter.get_path_registry()
        expected_paths = [
            "/numerics/time/end_time",
            "/numerics/time/delta_t",
            "/numerics/time/mode",
            "/numerics/solver",
            "/physics/reynolds_number",
            "/physics/kinematic_viscosity",
            "/geometry/domain/dimensions",
            "/mesh/resolution",
            "/execution/parallel",
        ]
        for path in expected_paths:
            assert path in registry, f"Missing path in registry: {path}"

    def test_path_registry_entry_has_required_keys(self):
        """Each registry entry should have all required metadata keys."""
        exporter = SchemaExporter()
        registry = exporter.get_path_registry()
        for path, meta in registry.items():
            assert "json_pointer" in meta
            assert "value_schema" in meta
            assert "required" in meta
            assert "mutable" in meta
            assert "risk_level" in meta
            assert "unit_dimension" in meta
            assert "dependency_tags" in meta
            assert meta["json_pointer"] == path

    def test_path_registry_risk_levels(self):
        """Registry entries should use valid risk levels."""
        exporter = SchemaExporter()
        registry = exporter.get_path_registry()
        valid_levels = {"low", "medium", "high"}
        for meta in registry.values():
            assert meta["risk_level"] in valid_levels

    def test_schema_is_json_serializable(self):
        """The exported schema should be JSON-serializable."""
        import json

        exporter = SchemaExporter()
        schema = exporter.export_schema()
        json_str = json.dumps(schema)
        assert json.loads(json_str) == schema


# ---------------------------------------------------------------------------
# Tests: Legacy migration
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    """Tests for LegacyMigrator.migrate_from_cylinder_flow_spec."""

    def test_migration_returns_simulation_study_spec(self):
        """Migration should return a SimulationStudySpec instance."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert isinstance(result, SimulationStudySpec)

    def test_migration_preserves_spec_id(self):
        """Migration should preserve the legacy experiment_id."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.spec_id == legacy["experiment_id"]

    def test_migration_preserves_session_id(self):
        """Migration should preserve the legacy spec_id as session_id."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.session_id == legacy["spec_id"]

    def test_migration_preserves_title_and_objective(self):
        """Migration should preserve study title and objective."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.study.title == legacy["title"]
        assert result.study.objective == legacy["objective"]

    def test_migration_preserves_fluid_material(self):
        """Migration should preserve the fluid material."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.physics.material.value == "water"
        assert result.physics.material.status == "user_confirmed"

    def test_migration_preserves_density_and_viscosity(self):
        """Migration should preserve density and kinematic viscosity."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.physics.density.value == 998.0
        assert result.physics.density.unit == "kg/m^3"
        assert result.physics.kinematic_viscosity.value == 0.002
        assert result.physics.kinematic_viscosity.unit == "m^2/s"

    def test_migration_preserves_cylinder_geometry(self):
        """Migration should preserve cylinder entity in geometry."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert "cylinder" in result.geometry.entities
        cyl = result.geometry.entities["cylinder"]
        assert cyl.semantic_type == "cylinder_2d"
        assert cyl.primitive["radius"] == 0.2

    def test_migration_preserves_domain(self):
        """Migration should preserve domain dimensions."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.geometry.domain.length.value == 12.0
        assert result.geometry.domain.width.value == 8.0
        assert result.geometry.domain.dimensions == "2d"

    def test_migration_preserves_boundaries(self):
        """Migration should preserve boundary conditions."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        patch_names = [bc.patch_name for bc in result.boundaries.conditions]
        assert "inlet" in patch_names
        assert "outlet" in patch_names
        assert "front" in patch_names
        inlet = next(bc for bc in result.boundaries.conditions if bc.patch_name == "inlet")
        assert inlet.role == "inlet"
        assert inlet.bc_type == "velocityInlet"
        assert inlet.parameters["velocity"] == 1.0

    def test_migration_preserves_observables(self):
        """Migration should preserve observable targets."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        target_ids = [t.target_id for t in result.observations.targets]
        assert "drag" in target_ids
        assert "lift" in target_ids
        assert "strouhal" in target_ids

    def test_migration_preserves_numerics(self):
        """Migration should preserve numerics (solver, end time, delta_t)."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.numerics.time.end_time.value == 10.0
        assert result.numerics.time.delta_t.value == 0.01
        assert result.numerics.time.max_courant == 0.5

    def test_migration_no_data_loss(self):
        """Migration should preserve all legacy data in extensions."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        preserved = result.extensions["legacy_preservation"]
        assert preserved["original_schema"] == "CylinderFlow2DExperimentSpecV1"
        assert preserved["case_family"] == "cylinder_flow_2d"
        assert preserved["raw_spec"] == legacy
        assert result.extensions["legacy_bottom_profile"] == legacy["bottom_profile"]
        assert result.extensions["legacy_forcing"] == legacy["forcing"]
        assert result.extensions["legacy_inlet_profile"] == legacy["inlet_profile"]

    def test_migration_read_only(self):
        """Migration should not mutate the input dict."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        legacy_copy = copy.deepcopy(legacy)
        migrator.migrate_from_cylinder_flow_spec(legacy)
        assert legacy == legacy_copy

    def test_migration_provenance(self):
        """Migration should set provenance correctly."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.provenance.created_by == "legacy_migrator"
        assert result.provenance.parent_version == legacy["spec_version"]
        assert len(result.provenance.modification_history) == 1
        assert result.provenance.modification_history[0]["patch_id"] == "legacy_migration"

    def test_migration_reynolds_estimation(self):
        """Migration should estimate Reynolds number correctly."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        # Re = U * D / nu = 1.0 * 0.4 / 0.002 = 200.0
        assert result.physics.reynolds_number is not None
        assert result.physics.reynolds_number.value == 200.0

    def test_migration_velocity_extraction(self):
        """Migration should extract inlet velocity correctly."""
        migrator = LegacyMigrator()
        legacy = make_legacy_spec()
        result = migrator.migrate_from_cylinder_flow_spec(legacy)
        assert result.physics.velocity is not None
        assert result.physics.velocity.value == 1.0


# ---------------------------------------------------------------------------
# Tests: Version management
# ---------------------------------------------------------------------------


class TestVersionedSpecStore:
    """Tests for VersionedSpecStore create / get / list operations."""

    def test_create_first_version(self):
        """create_version should create version 1 for a new spec."""
        store = VersionedSpecStore()
        spec = make_study_spec()
        result = store.create_version(spec)
        assert result.version == 1
        assert result.parent_version is None

    def test_create_second_version(self):
        """create_version should increment version and set parent."""
        store = VersionedSpecStore()
        spec = make_study_spec()
        v1 = store.create_version(spec)
        v2 = store.create_version(spec)
        assert v1.version == 1
        assert v2.version == 2
        assert v2.parent_version == 1

    def test_get_version_existing(self):
        """get_version should return the spec for an existing version."""
        store = VersionedSpecStore()
        spec = make_study_spec()
        store.create_version(spec)
        store.create_version(spec)
        retrieved = store.get_version("test_spec_001", 1)
        assert retrieved is not None
        assert retrieved.version == 1
        retrieved_v2 = store.get_version("test_spec_001", 2)
        assert retrieved_v2 is not None
        assert retrieved_v2.version == 2

    def test_get_version_nonexistent(self):
        """get_version should return None for a non-existent version."""
        store = VersionedSpecStore()
        result = store.get_version("nonexistent", 1)
        assert result is None

    def test_get_version_wrong_version_number(self):
        """get_version should return None for a non-existent version number."""
        store = VersionedSpecStore()
        spec = make_study_spec()
        store.create_version(spec)
        result = store.get_version("test_spec_001", 99)
        assert result is None

    def test_get_latest(self):
        """get_latest should return the highest version."""
        store = VersionedSpecStore()
        spec = make_study_spec()
        store.create_version(spec)
        store.create_version(spec)
        store.create_version(spec)
        latest = store.get_latest("test_spec_001")
        assert latest is not None
        assert latest.version == 3

    def test_get_latest_empty(self):
        """get_latest should return None for a spec with no versions."""
        store = VersionedSpecStore()
        assert store.get_latest("nonexistent") is None

    def test_list_versions(self):
        """list_versions should return all version numbers sorted."""
        store = VersionedSpecStore()
        spec = make_study_spec()
        store.create_version(spec)
        store.create_version(spec)
        store.create_version(spec)
        versions = store.list_versions("test_spec_001")
        assert versions == [1, 2, 3]

    def test_list_versions_empty(self):
        """list_versions should return an empty list for unknown spec."""
        store = VersionedSpecStore()
        assert store.list_versions("nonexistent") == []

    def test_create_does_not_mutate_original(self):
        """create_version should not mutate the original spec object."""
        store = VersionedSpecStore()
        spec = make_study_spec()
        original_version = spec.version
        store.create_version(spec)
        assert spec.version == original_version

    def test_multiple_specs_independent(self):
        """Different specs should have independent version counters."""
        store = VersionedSpecStore()
        spec_a = make_study_spec(spec_id="spec_a")
        spec_b = make_study_spec(spec_id="spec_b")
        store.create_version(spec_a)
        store.create_version(spec_a)
        store.create_version(spec_b)
        assert store.list_versions("spec_a") == [1, 2]
        assert store.list_versions("spec_b") == [1]

    def test_versioned_spec_roundtrip(self):
        """A versioned spec should be serializable and re-validatable."""
        store = VersionedSpecStore()
        spec = make_study_spec()
        v1 = store.create_version(spec)
        data = v1.model_dump()
        restored = SimulationStudySpec.model_validate(data)
        assert restored.version == v1.version
        assert restored.spec_id == v1.spec_id


# ---------------------------------------------------------------------------
# Tests: Quantity model
# ---------------------------------------------------------------------------


class TestQuantity:
    """Tests for the Quantity model."""

    def test_basic_quantity(self):
        """A basic quantity with value and unit."""
        q = Quantity(value=10.0, unit="m/s")
        assert q.value == 10.0
        assert q.unit == "m/s"
        assert q.is_resolved() is True
        assert q.is_symbolic() is False

    def test_expression_quantity(self):
        """A quantity with a relative expression."""
        q = Quantity(
            value=None,
            expression={
                "operator": "multiply",
                "path": "/numerics/time/delta_t",
                "factor": 0.5,
            },
        )
        assert q.is_symbolic() is True
        assert q.is_resolved() is False
        assert q.expression["operator"] == "multiply"

    def test_dict_value_quantity(self):
        """A quantity with a dict value (inline expression)."""
        q = Quantity(value={"ref": "/physics/velocity", "scale": 2.0})
        assert q.is_resolved() is True

    def test_quantity_extra_forbidden(self):
        """Quantity should reject unknown fields."""
        with pytest.raises(ValidationError):
            Quantity(value=1.0, unknown=True)

    def test_quantity_optional_fields(self):
        """Quantity with all fields optional."""
        q = Quantity()
        assert q.value is None
        assert q.unit is None
        assert q.expression is None
