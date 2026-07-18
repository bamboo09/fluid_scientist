"""Comprehensive tests for the ``fluid_scientist.spec_editing`` package.

Covers:
* PatchOperation creation and validation.
* SimulationSpecPatch "仿真时间设为15秒" scenario (replace end_time).
* Ambiguity clarification (start_time=5s, "仿真时间15秒" -> blocking).
* Relative modification ("时间步减半" -> multiply delta_t by 0.5).
* Geometry change (triangle -> rectangle, only target entity changes).
* untouched_guarantee (modifying end_time doesn't change delta_t).
* Undo (reverse patch restores original values).
* Diff generation.
* Impact analysis (changing material triggers Re recompute).
* Version conflict (base_version mismatch).
* Immutable field rejection.
* Path registry auto-generation from schema.
* Patch history recording.
"""

from __future__ import annotations

import pytest

from fluid_scientist.study_spec import (
    BoundaryCondition,
    BoundaryDefinition,
    ExecutionDefinition,
    GeometryDefinition,
    GeometryEntity,
    MeshDefinition,
    NumericsDefinition,
    ObservationDefinition,
    ObservationTarget,
    PhysicsDefinition,
    PlacementSpec,
    ProbeSpec,
    Quantity,
    SimulationStudySpec,
    SpecProvenance,
    SourcedValue,
    StudyDefinition,
    TimeControl,
    ValidationDefinition,
)
from fluid_scientist.study_spec.geometry import DomainSpec

from fluid_scientist.spec_editing import (
    ClarificationAlternative,
    ClarificationRequest,
    DiffBuilder,
    ImpactAnalyzer,
    PatchEngine,
    PatchExecutor,
    PatchHistory,
    PatchOperation,
    PatchRecord,
    PatchResult,
    PatchValidator,
    PathRegistry,
    QuantityResolver,
    RelationResolver,
    SimulationSpecPatch,
    UndoEngine,
)


# ---------------------------------------------------------------------------
# Helper: build a fully-populated test spec
# ---------------------------------------------------------------------------


def _sourced(value, unit=None, status="user_explicit", confidence=0.9):
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
        research_questions=["What is the Strouhal number at Re=100?"],
    )
    physics = PhysicsDefinition(
        material=_sourced("water", status="user_confirmed"),
        density=_sourced(998.2, unit="kg/m^3", status="user_confirmed"),
        kinematic_viscosity=_sourced(1.0e-6, unit="m^2/s", status="derived"),
        reynolds_number=_sourced(100.0, status="derived"),
        velocity=_sourced(0.1, unit="m/s", status="derived"),
        characteristic_length=_sourced(0.001, unit="m", status="derived"),
    )
    geometry = GeometryDefinition(
        domain=DomainSpec(
            length=_sourced(12.0, unit="m"),
            width=_sourced(8.0, unit="m"),
            dimensions="2d",
        ),
        entities={
            "cylinder": GeometryEntity(
                entity_id="cylinder",
                semantic_type="cylinder_2d",
                primitive={"type": "circle", "radius": 0.2, "diameter": 0.4},
                original_user_semantics="cylinder",
                placement=PlacementSpec(
                    x=_sourced(4.0, unit="m"),
                    y=_sourced(4.0, unit="m"),
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
                patch_name="cylinder",
                role="wall",
                bc_type="noSlipWall",
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
        },
        turbulence_model="laminar",
    )
    mesh = MeshDefinition(
        resolution=_sourced(1200, unit="cells", status="derived"),
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
        ],
        probes=[
            ProbeSpec(
                probe_id="wake_probe_1",
                location={"x": 5.0, "y": 4.0, "z": 0.0},
                field="U",
            ),
        ],
        postprocessing=["streamlines"],
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


def make_patch(
    spec: SimulationStudySpec,
    operations: list[PatchOperation],
    **kwargs,
) -> SimulationSpecPatch:
    """Build a SimulationSpecPatch targeting *spec*."""
    defaults = dict(
        patch_id="patch_001",
        session_id=spec.session_id,
        base_spec_id=spec.spec_id,
        base_version=spec.version,
        intent="modify_existing_spec",
        operations=operations,
        clarifications=[],
        impact_requests=[],
        untouched_guarantee=True,
        assistant_message="Applying user requested changes",
    )
    defaults.update(kwargs)
    return SimulationSpecPatch(**defaults)


# ---------------------------------------------------------------------------
# Tests: PatchOperation creation and validation
# ---------------------------------------------------------------------------


class TestPatchOperation:
    """Test PatchOperation model creation and field validation."""

    def test_create_replace_operation(self):
        op = PatchOperation(
            op="replace",
            path="/numerics/time/end_time",
            value={"value": 15.0, "unit": "s"},
            source_quote="仿真时间设为15秒",
            confidence=0.95,
        )
        assert op.op == "replace"
        assert op.path == "/numerics/time/end_time"
        assert op.value == {"value": 15.0, "unit": "s"}
        assert op.source_quote == "仿真时间设为15秒"
        assert op.confidence == 0.95

    def test_source_quote_is_required(self):
        """source_quote is a required field."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PatchOperation(
                op="replace",
                path="/numerics/time/end_time",
                value=15.0,
            )

    def test_confidence_bounds(self):
        """confidence must be in [0.0, 1.0]."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PatchOperation(
                op="replace",
                path="/test",
                source_quote="test",
                confidence=1.5,
            )
        with pytest.raises(ValidationError):
            PatchOperation(
                op="replace",
                path="/test",
                source_quote="test",
                confidence=-0.1,
            )

    def test_all_operation_types(self):
        """All declared operation types can be instantiated."""
        for op_type in [
            "add", "replace", "remove", "merge", "append_unique",
            "move", "copy", "test", "set_relation", "unset_relation",
            "declare_unknown_capability",
        ]:
            op = PatchOperation(
                op=op_type,  # type: ignore[arg-type]
                path="/test",
                source_quote="test",
            )
            assert op.op == op_type


# ---------------------------------------------------------------------------
# Tests: SimulationSpecPatch "仿真时间设为15秒" scenario
# ---------------------------------------------------------------------------


class TestEndTimeReplace:
    """Test the core '仿真时间设为15秒' scenario."""

    def test_replace_end_time(self):
        """Replace end_time from 10s to 15s, verify version incremented."""
        spec = make_study_spec()
        assert spec.numerics.time.end_time.value == 10.0
        assert spec.version == 1

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="仿真时间设为15秒",
                    confidence=0.95,
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == []
        assert result.new_spec is not None
        assert result.new_spec.numerics.time.end_time.value == 15.0
        assert result.new_spec.version == 2

    def test_diff_shows_end_time_change(self):
        """The diff should show end_time changed from 10.0 to 15.0."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )
        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.diff is not None
        # Find the end_time value diff.
        end_time_diffs = [
            d for d in result.diff.field_diffs
            if "end_time" in d.path and "value" in d.path
        ]
        assert len(end_time_diffs) >= 1
        assert end_time_diffs[0].old_value == 10.0
        assert end_time_diffs[0].new_value == 15.0


# ---------------------------------------------------------------------------
# Tests: Ambiguity clarification
# ---------------------------------------------------------------------------


class TestClarification:
    """Test that ambiguous patches return clarifications without applying."""

    def test_blocking_clarification_returns_without_applying(self):
        """When a patch has blocking clarifications, it returns them
        without applying the patch."""
        spec = make_study_spec()
        spec_dict = spec.model_dump()
        # Set start_time to 5s so "仿真时间15秒" is ambiguous.
        spec_dict["numerics"]["time"]["start_time"] = {"value": 5.0, "unit": "s"}
        spec = SimulationStudySpec.model_validate(spec_dict)
        assert spec.numerics.time.start_time.value == 5.0

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="仿真时间15秒",
                ),
            ],
            clarifications=[
                ClarificationRequest(
                    clarification_id="clr_001",
                    question="您说的仿真时间15秒是指结束时间还是总时长？",
                    alternatives=[
                        ClarificationAlternative(
                            label="结束时间设为15秒",
                            operations=[
                                PatchOperation(
                                    op="replace",
                                    path="/numerics/time/end_time",
                                    value={"value": 15.0, "unit": "s"},
                                    source_quote="仿真时间15秒",
                                ),
                            ],
                        ),
                        ClarificationAlternative(
                            label="总时长15秒（即结束时间=20秒）",
                            operations=[
                                PatchOperation(
                                    op="replace",
                                    path="/numerics/time/end_time",
                                    value={"value": 20.0, "unit": "s"},
                                    source_quote="仿真时间15秒",
                                ),
                            ],
                        ),
                    ],
                    affected_paths=["/numerics/time/end_time"],
                    blocking=True,
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        # Should NOT apply the patch.
        assert result.new_spec is None
        # Should return the blocking clarification.
        assert len(result.clarifications) == 1
        assert result.clarifications[0].clarification_id == "clr_001"
        assert len(result.clarifications[0].alternatives) == 2


# ---------------------------------------------------------------------------
# Tests: Relative modification ("时间步减半")
# ---------------------------------------------------------------------------


class TestRelativeModification:
    """Test relative-expression resolution (multiply delta_t by 0.5)."""

    def test_halve_delta_t(self):
        """Patch with expression {operator: multiply, factor: 0.5}
        should halve delta_t from 0.01 to 0.005."""
        spec = make_study_spec()
        assert spec.numerics.time.delta_t.value == 0.01

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/delta_t",
                    value={
                        "operator": "multiply",
                        "path": "/numerics/time/delta_t",
                        "factor": 0.5,
                    },
                    source_quote="时间步减半",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == []
        assert result.new_spec is not None
        assert result.new_spec.numerics.time.delta_t.value == pytest.approx(0.005)

    def test_quantity_resolver_multiply(self):
        """Directly test QuantityResolver with multiply expression."""
        resolver = QuantityResolver()
        spec_dict = make_study_spec().model_dump()
        result = resolver.resolve(
            {
                "operator": "multiply",
                "path": "/numerics/time/delta_t",
                "factor": 0.5,
            },
            spec_dict,
            "/numerics/time/delta_t",
        )
        assert result == pytest.approx(0.005)

    def test_quantity_resolver_add(self):
        """Test QuantityResolver with add expression."""
        resolver = QuantityResolver()
        spec_dict = make_study_spec().model_dump()
        result = resolver.resolve(
            {
                "operator": "add",
                "path": "/numerics/time/end_time",
                "addend": 5.0,
            },
            spec_dict,
            "/numerics/time/end_time",
        )
        assert result == pytest.approx(15.0)

    def test_quantity_resolver_divide(self):
        """Test QuantityResolver with divide expression."""
        resolver = QuantityResolver()
        spec_dict = make_study_spec().model_dump()
        result = resolver.resolve(
            {
                "operator": "divide",
                "path": "/numerics/time/end_time",
                "factor": 2.0,
            },
            spec_dict,
            "/numerics/time/end_time",
        )
        assert result == pytest.approx(5.0)

    def test_quantity_resolver_subtract(self):
        """Test QuantityResolver with subtract expression."""
        resolver = QuantityResolver()
        spec_dict = make_study_spec().model_dump()
        result = resolver.resolve(
            {
                "operator": "subtract",
                "path": "/numerics/time/end_time",
                "addend": 3.0,
            },
            spec_dict,
            "/numerics/time/end_time",
        )
        assert result == pytest.approx(7.0)

    def test_quantity_resolver_no_silent_fallback(self):
        """Resolver should raise on invalid expression, not silently
        return a default."""
        resolver = QuantityResolver()
        spec_dict = make_study_spec().model_dump()
        with pytest.raises(ValueError, match="Unknown operator"):
            resolver.resolve(
                {"operator": "power", "path": "/numerics/time/end_time", "factor": 2},
                spec_dict,
                "/numerics/time/end_time",
            )

    def test_quantity_resolver_missing_path(self):
        """Resolver should raise when source path is missing."""
        resolver = QuantityResolver()
        spec_dict = make_study_spec().model_dump()
        with pytest.raises(ValueError, match="missing"):
            resolver.resolve(
                {"operator": "multiply", "factor": 0.5},
                spec_dict,
                "/numerics/time/delta_t",
            )

    def test_quantity_resolver_passthrough_non_dict(self):
        """Non-dict values are returned as-is."""
        resolver = QuantityResolver()
        spec_dict = make_study_spec().model_dump()
        result = resolver.resolve(42.0, spec_dict, "/test")
        assert result == 42.0


# ---------------------------------------------------------------------------
# Tests: Geometry change (triangle -> rectangle)
# ---------------------------------------------------------------------------


class TestGeometryChange:
    """Test that geometry entity changes only affect the target entity."""

    def test_change_entity_primitive(self):
        """Change cylinder primitive, verify other fields untouched."""
        spec = make_study_spec()
        original_end_time = spec.numerics.time.end_time.value

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/geometry/entities/cylinder/primitive",
                    value={
                        "type": "rectangle",
                        "width": 0.4,
                        "height": 0.6,
                    },
                    source_quote="把圆柱改成矩形",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == []
        assert result.new_spec is not None
        # The cylinder primitive should change.
        new_prim = result.new_spec.geometry.entities["cylinder"].primitive
        assert new_prim["type"] == "rectangle"
        assert new_prim["width"] == 0.4
        # end_time should be untouched.
        assert result.new_spec.numerics.time.end_time.value == original_end_time


# ---------------------------------------------------------------------------
# Tests: untouched_guarantee
# ---------------------------------------------------------------------------


class TestUntouchedGuarantee:
    """Test that modifying end_time doesn't change delta_t."""

    def test_delta_t_untouched_when_end_time_changes(self):
        spec = make_study_spec()
        original_delta_t = spec.numerics.time.delta_t.value

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 20.0, "unit": "s"},
                    source_quote="仿真时间设为20秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == []
        assert result.new_spec is not None
        assert result.new_spec.numerics.time.end_time.value == 20.0
        assert result.new_spec.numerics.time.delta_t.value == original_delta_t

    def test_untouched_guarantee_violation_detected(self):
        """If the executor accidentally changes a non-touched field,
        the untouched_guarantee check should catch it.

        We simulate this by checking the executor's internal method
        directly with a crafted scenario.
        """
        registry = PathRegistry()
        resolver = QuantityResolver()
        validator = PatchValidator(registry, resolver)
        executor = PatchExecutor(registry, resolver, validator)

        spec = make_study_spec()
        old_dict = spec.model_dump()
        new_dict = spec.model_dump()

        # Simulate an accidental change to delta_t.
        new_dict["numerics"]["time"]["delta_t"]["value"] = 999.0

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 20.0, "unit": "s"},
                    source_quote="change end time",
                ),
            ],
        )

        with pytest.raises(Exception, match="untouched_guarantee"):
            executor._verify_untouched(old_dict, new_dict, patch)


# ---------------------------------------------------------------------------
# Tests: Undo
# ---------------------------------------------------------------------------


class TestUndo:
    """Test that reverse patches restore original values."""

    def test_undo_replace_restores_original(self):
        """Apply a replace, then undo it, verify original value restored."""
        spec = make_study_spec()
        original_end_time = spec.numerics.time.end_time.value

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None
        assert result.new_spec.numerics.time.end_time.value == 15.0

        # Generate reverse patch.  Pass the pre-patch spec so the
        # undo engine can read the original (pre-patch) values.
        undo_engine = engine.undo_engine
        reverse_patch = undo_engine.create_reverse_patch(
            patch, spec.model_dump()
        )

        # Apply reverse patch.
        undo_result = engine.process_patch(
            reverse_patch, result.new_spec
        )
        assert undo_result.errors == []
        assert undo_result.new_spec is not None
        assert undo_result.new_spec.numerics.time.end_time.value == original_end_time

    def test_undo_patch_id_suffix(self):
        """Reverse patch should have '_undo' suffix in its ID."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="test",
                ),
            ],
        )
        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        reverse_patch = engine.undo_engine.create_reverse_patch(
            patch, spec.model_dump()
        )
        assert reverse_patch.patch_id == "patch_001_undo"
        assert reverse_patch.intent == "undo_last_patch"


# ---------------------------------------------------------------------------
# Tests: Diff generation
# ---------------------------------------------------------------------------


class TestDiffGeneration:
    """Test SpecDiff and DiffBuilder."""

    def test_diff_records_changed_field(self):
        """Diff should record the changed field with old and new values."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 20.0, "unit": "s"},
                    source_quote="change to 20s",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.diff is not None
        assert result.diff.base_version == 1
        assert result.diff.new_version == 2
        assert len(result.diff.field_diffs) > 0

    def test_diff_empty_when_no_changes(self):
        """Diff with identical specs should have no field diffs."""
        spec = make_study_spec()
        spec_dict = spec.model_dump()
        patch = make_patch(spec, operations=[])

        builder = DiffBuilder()
        diff = builder.build_diff(spec_dict, spec_dict, patch)
        assert len(diff.field_diffs) == 0

    def test_diff_source_quote_annotation(self):
        """Diff fields should carry the source_quote from the operation."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 25.0, "unit": "s"},
                    source_quote="仿真时间25秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.diff is not None
        end_time_diffs = [
            d for d in result.diff.field_diffs
            if "end_time" in d.path and "value" in d.path
        ]
        assert len(end_time_diffs) >= 1
        assert end_time_diffs[0].source_quote == "仿真时间25秒"


# ---------------------------------------------------------------------------
# Tests: Impact analysis
# ---------------------------------------------------------------------------


class TestImpactAnalysis:
    """Test ImpactAnalyzer."""

    def test_changing_velocity_triggers_re_recompute(self):
        """Changing /physics/velocity should flag reynolds_number for
        recompute."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/physics/velocity",
                    value={"value": 0.2, "unit": "m/s"},
                    source_quote="increase velocity",
                ),
            ],
        )

        analyzer = ImpactAnalyzer(PathRegistry())
        report = analyzer.analyze(patch, spec.model_dump())

        assert "/physics/reynolds_number" in report.derived_recompute_needed

    def test_changing_material_triggers_re_recompute(self):
        """Changing /physics/material should flag reynolds_number for
        recompute (via dependency_tags)."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/physics/material",
                    value={"value": "air", "status": "user_explicit"},
                    source_quote="change to air",
                ),
            ],
        )

        analyzer = ImpactAnalyzer(PathRegistry())
        report = analyzer.analyze(patch, spec.model_dump())

        assert "/physics/reynolds_number" in report.derived_recompute_needed

    def test_high_risk_path_requires_confirmation(self):
        """Changing solver (high-risk, in confirmation paths) should
        require confirmation."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/solver",
                    value="pimpleFoam",
                    source_quote="change solver",
                ),
            ],
        )

        analyzer = ImpactAnalyzer(PathRegistry())
        report = analyzer.analyze(patch, spec.model_dump())

        assert report.requires_user_confirmation is True

    def test_low_risk_path_no_confirmation(self):
        """Changing a low-risk path (study/title) should not require
        confirmation."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/study/title",
                    value="New Title",
                    source_quote="change title",
                ),
            ],
        )

        analyzer = ImpactAnalyzer(PathRegistry())
        report = analyzer.analyze(patch, spec.model_dump())

        assert report.requires_user_confirmation is False

    def test_mesh_change_invalidates_mesh_artifact(self):
        """Changing mesh resolution should invalidate the mesh artifact."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/mesh/resolution",
                    value={"value": 2400, "unit": "cells"},
                    source_quote="refine mesh",
                ),
            ],
        )

        analyzer = ImpactAnalyzer(PathRegistry())
        report = analyzer.analyze(patch, spec.model_dump())

        assert "mesh" in report.invalidation_status
        assert "results" in report.invalidation_status


# ---------------------------------------------------------------------------
# Tests: Version conflict
# ---------------------------------------------------------------------------


class TestVersionConflict:
    """Test that base_version mismatch is rejected."""

    def test_version_conflict_rejected(self):
        """Patch with wrong base_version should return errors."""
        spec = make_study_spec(version=3)

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="change end time",
                ),
            ],
            base_version=2,  # Wrong! Spec is at version 3.
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert len(result.errors) > 0
        assert "Version conflict" in result.errors[0]
        assert result.new_spec is None

    def test_correct_version_accepted(self):
        """Patch with correct base_version should apply successfully."""
        spec = make_study_spec(version=3)

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="change end time",
                ),
            ],
            base_version=3,  # Correct!
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == []
        assert result.new_spec is not None
        assert result.new_spec.version == 4


# ---------------------------------------------------------------------------
# Tests: Immutable field rejection
# ---------------------------------------------------------------------------


class TestImmutableField:
    """Test that immutable fields are rejected."""

    def test_immutable_spec_id_rejected(self):
        """Trying to replace spec_id should fail validation."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/spec_id",
                    value="new_spec_id",
                    source_quote="change spec id",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert len(result.errors) > 0
        assert result.new_spec is None
        assert any("immutable" in e.lower() for e in result.errors)

    def test_immutable_version_rejected(self):
        """Trying to replace version should fail validation."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/version",
                    value=99,
                    source_quote="change version",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert len(result.errors) > 0

    def test_immutable_solver_rejected(self):
        """Solver is marked immutable in the registry."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/solver",
                    value="pimpleFoam",
                    source_quote="change solver",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert len(result.errors) > 0
        assert any("immutable" in e.lower() for e in result.errors)

    def test_test_operation_allowed_on_immutable(self):
        """test operations should be allowed even on immutable fields."""
        spec = make_study_spec()
        registry = PathRegistry()
        resolver = QuantityResolver()
        validator = PatchValidator(registry, resolver)

        op = PatchOperation(
            op="test",
            path="/spec_id",
            value="test_spec_001",
            source_quote="verify spec id",
        )
        errors = validator.validate_operation(op, spec.model_dump())
        assert errors == []


# ---------------------------------------------------------------------------
# Tests: Path registry
# ---------------------------------------------------------------------------


class TestPathRegistry:
    """Test PathRegistry auto-generation from schema."""

    def test_registry_contains_known_paths(self):
        """Registry should contain key paths from the schema."""
        registry = PathRegistry()
        paths = registry.list_paths()
        assert "/numerics/time/end_time" in paths
        assert "/numerics/time/delta_t" in paths
        assert "/physics/material" in paths
        assert "/mesh/resolution" in paths

    def test_get_path_metadata(self):
        """get_path_metadata should return correct metadata."""
        registry = PathRegistry()
        meta = registry.get_path_metadata("/numerics/time/end_time")
        assert meta is not None
        assert meta.mutable is True
        assert meta.risk_level == "high"
        assert meta.unit_dimension == "time"

    def test_is_mutable(self):
        """is_mutable should correctly report mutability."""
        registry = PathRegistry()
        assert registry.is_mutable("/numerics/time/end_time") is True
        assert registry.is_mutable("/spec_id") is False
        assert registry.is_mutable("/version") is False

    def test_get_risk_level(self):
        """get_risk_level should return the correct level."""
        registry = PathRegistry()
        assert registry.get_risk_level("/numerics/time/end_time") == "high"
        assert registry.get_risk_level("/study/title") == "low"

    def test_validate_path(self):
        """validate_path should return True for known paths."""
        registry = PathRegistry()
        assert registry.validate_path("/numerics/time/end_time") is True
        assert registry.validate_path("/nonexistent/path") is False

    def test_entity_placeholder_path(self):
        """Entity paths with {entity_id} should be resolved."""
        registry = PathRegistry()
        meta = registry.get_path_metadata(
            "/geometry/entities/cylinder/primitive/type"
        )
        assert meta is not None
        assert meta.mutable is True

    def test_array_append_path(self):
        """Array append paths with /- should be resolved."""
        registry = PathRegistry()
        meta = registry.get_path_metadata("/observations/probes/-")
        assert meta is not None
        assert meta.mutable is True

    def test_unknown_path_returns_none(self):
        """Unknown paths should return None."""
        registry = PathRegistry()
        assert registry.get_path_metadata("/does/not/exist") is None


# ---------------------------------------------------------------------------
# Tests: Patch history recording
# ---------------------------------------------------------------------------


class TestPatchHistory:
    """Test PatchHistory recording and retrieval."""

    def test_record_and_get(self):
        """Record a patch and retrieve it by ID."""
        history = PatchHistory()
        spec = make_study_spec()

        record = PatchRecord(
            patch_id="patch_001",
            session_id=spec.session_id,
            base_spec_id=spec.spec_id,
            base_version=1,
            new_version=2,
            patch=make_patch(
                spec,
                operations=[
                    PatchOperation(
                        op="replace",
                        path="/numerics/time/end_time",
                        value={"value": 15.0, "unit": "s"},
                        source_quote="test",
                    ),
                ],
            ),
            applied_at="2026-01-01T00:00:00+00:00",
            applied_by="test",
            status="confirmed",
        )
        history.record(record)

        retrieved = history.get("patch_001")
        assert retrieved is not None
        assert retrieved.patch_id == "patch_001"
        assert retrieved.new_version == 2

    def test_list_for_spec(self):
        """list_for_spec should return all patches for a spec in order."""
        history = PatchHistory()
        spec = make_study_spec()

        for i in range(3):
            record = PatchRecord(
                patch_id=f"patch_{i:03d}",
                session_id=spec.session_id,
                base_spec_id=spec.spec_id,
                base_version=i + 1,
                new_version=i + 2,
                patch=make_patch(
                    spec,
                    operations=[
                        PatchOperation(
                            op="replace",
                            path="/numerics/time/end_time",
                            value={"value": 15.0, "unit": "s"},
                            source_quote="test",
                        ),
                    ],
                    patch_id=f"patch_{i:03d}",
                    base_version=i + 1,
                ),
                applied_at="2026-01-01T00:00:00+00:00",
                applied_by="test",
                status="confirmed",
            )
            history.record(record)

        records = history.list_for_spec(spec.spec_id)
        assert len(records) == 3
        assert records[0].patch_id == "patch_000"
        assert records[2].patch_id == "patch_002"

    def test_get_latest(self):
        """get_latest should return the most recent patch."""
        history = PatchHistory()
        spec = make_study_spec()

        for i in range(3):
            history.record(PatchRecord(
                patch_id=f"patch_{i:03d}",
                session_id=spec.session_id,
                base_spec_id=spec.spec_id,
                base_version=i + 1,
                new_version=i + 2,
                patch=make_patch(
                    spec,
                    operations=[
                        PatchOperation(
                            op="replace",
                            path="/numerics/time/end_time",
                            value={"value": 15.0, "unit": "s"},
                            source_quote="test",
                        ),
                    ],
                    patch_id=f"patch_{i:03d}",
                    base_version=i + 1,
                ),
                applied_at="2026-01-01T00:00:00+00:00",
                applied_by="test",
                status="confirmed",
            ))

        latest = history.get_latest(spec.spec_id)
        assert latest is not None
        assert latest.patch_id == "patch_002"

    def test_get_latest_empty(self):
        """get_latest on empty history should return None."""
        history = PatchHistory()
        assert history.get_latest("nonexistent") is None

    def test_engine_records_history(self):
        """PatchEngine should record patches in history after applying."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="change end time",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == []
        assert result.new_spec is not None

        # Check history was recorded.
        latest = engine.history.get_latest(spec.spec_id)
        assert latest is not None
        assert latest.patch_id == "patch_001"
        assert latest.status == "confirmed"


# ---------------------------------------------------------------------------
# Tests: Relation resolver
# ---------------------------------------------------------------------------


class TestRelationResolver:
    """Test RelationResolver for spatial relation resolution."""

    def test_attached_to_bottom_wall(self):
        """attached_to bottom_wall should set y=0."""
        resolver = RelationResolver()
        geometry = {
            "domain": {
                "length": {"value": 10.0, "unit": "m"},
                "width": {"value": 5.0, "unit": "m"},
                "dimensions": "2d",
            },
            "entities": {
                "triangle": {
                    "entity_id": "triangle",
                    "placement": {},
                },
            },
            "relations": [
                {
                    "relation_id": "r1",
                    "type": "attached_to",
                    "subject_id": "triangle",
                    "object_id": "bottom_wall",
                    "parameters": {},
                },
            ],
        }
        result = resolver.resolve_relations(geometry)
        placement = result["entities"]["triangle"]["placement"]
        assert placement["y"]["value"] == 0.0

    def test_centered_in(self):
        """centered_in should place at domain center."""
        resolver = RelationResolver()
        geometry = {
            "domain": {
                "length": {"value": 10.0, "unit": "m"},
                "width": {"value": 5.0, "unit": "m"},
                "dimensions": "2d",
            },
            "entities": {
                "box": {"entity_id": "box", "placement": {}},
            },
            "relations": [
                {
                    "relation_id": "r1",
                    "type": "centered_in",
                    "subject_id": "box",
                    "object_id": "domain",
                    "parameters": {},
                },
            ],
        }
        result = resolver.resolve_relations(geometry)
        placement = result["entities"]["box"]["placement"]
        assert placement["x"]["value"] == pytest.approx(5.0)
        assert placement["y"]["value"] == pytest.approx(2.5)

    def test_aligned_below(self):
        """aligned_below should set same x, y below object."""
        resolver = RelationResolver()
        geometry = {
            "domain": {
                "length": {"value": 10.0, "unit": "m"},
                "width": {"value": 5.0, "unit": "m"},
                "dimensions": "2d",
            },
            "entities": {
                "cylinder": {
                    "entity_id": "cylinder",
                    "placement": {
                        "x": {"value": 4.0, "unit": "m"},
                        "y": {"value": 3.0, "unit": "m"},
                    },
                },
                "triangle": {"entity_id": "triangle", "placement": {}},
            },
            "relations": [
                {
                    "relation_id": "r1",
                    "type": "aligned_below",
                    "subject_id": "triangle",
                    "object_id": "cylinder",
                    "parameters": {"offset": 1.0},
                },
            ],
        }
        result = resolver.resolve_relations(geometry)
        placement = result["entities"]["triangle"]["placement"]
        assert placement["x"]["value"] == pytest.approx(4.0)
        assert placement["y"]["value"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Tests: Errors hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    """Test that all patch errors derive from PatchError."""

    def test_all_errors_are_patch_errors(self):
        from fluid_scientist.spec_editing.errors import (
            ImmutableFieldError,
            PatchApplicationError,
            PatchError,
            PatchValidationError,
            PathNotFoundError,
            TypeMismatchError,
            UnitMismatchError,
            VersionConflictError,
        )
        for exc_class in [
            PatchValidationError,
            PatchApplicationError,
            PathNotFoundError,
            TypeMismatchError,
            UnitMismatchError,
            ImmutableFieldError,
            VersionConflictError,
        ]:
            assert issubclass(exc_class, PatchError)

    def test_patch_error_is_exception(self):
        from fluid_scientist.spec_editing.errors import PatchError
        assert issubclass(PatchError, Exception)


# ---------------------------------------------------------------------------
# Tests: End-to-end through PatchEngine
# ---------------------------------------------------------------------------


class TestPatchEngineEndToEnd:
    """End-to-end tests through the PatchEngine orchestrator."""

    def test_full_pipeline_success(self):
        """Full pipeline: validate -> impact -> apply -> diff -> record."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == []
        assert result.new_spec is not None
        assert result.diff is not None
        assert result.impact is not None
        assert result.new_spec.numerics.time.end_time.value == 15.0
        assert result.new_spec.version == 2

    def test_pipeline_validation_failure(self):
        """When validation fails, no spec, diff, or impact is returned."""
        spec = make_study_spec()
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/nonexistent/path",
                    value=42,
                    source_quote="bad path",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert len(result.errors) > 0
        assert result.new_spec is None
        assert result.diff is None

    def test_engine_sub_components_accessible(self):
        """All sub-components should be accessible via properties."""
        engine = PatchEngine()
        assert engine.path_registry is not None
        assert engine.quantity_resolver is not None
        assert engine.validator is not None
        assert engine.executor is not None
        assert engine.diff_builder is not None
        assert engine.impact_analyzer is not None
        assert engine.undo_engine is not None
        assert engine.history is not None

    def test_multiple_patches_increment_version(self):
        """Applying multiple patches should increment version each time."""
        spec = make_study_spec()
        assert spec.version == 1

        engine = PatchEngine()

        # First patch.
        patch1 = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 15.0, "unit": "s"},
                    source_quote="15s",
                ),
            ],
            patch_id="patch_001",
        )
        result1 = engine.process_patch(patch1, spec)
        assert result1.new_spec.version == 2

        # Second patch.
        patch2 = make_patch(
            result1.new_spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value={"value": 20.0, "unit": "s"},
                    source_quote="20s",
                ),
            ],
            patch_id="patch_002",
            base_version=2,
        )
        result2 = engine.process_patch(patch2, result1.new_spec)
        assert result2.new_spec.version == 3
        assert result2.new_spec.numerics.time.end_time.value == 20.0
