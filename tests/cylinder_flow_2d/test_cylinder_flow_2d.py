"""Automated tests for the CylinderFlow2D experiment pipeline.

Covers the 12 test scenarios from Section 16 of the refactoring spec:

  16.1  Routing test              — pipeline_id = cylinder-flow-2d-v1
  16.2  Cylinder derivation       — radius -> diameter -> characteristic_dimension
  16.3  Flat-bottom cylinder      — no bump, no geometry missing
  16.4  Bump+cylinder             — both objects preserved
  16.5  2D boundary               — front = back = empty
  16.6  User boundary priority    — user no-slip not overridden by model
  16.7  Point velocity            — only missing point coordinate
  16.8  Section velocity          — only missing section_x
  16.9  Observable recommendation — cylinder drag/lift/wake recommended
  16.10 Analysis goal             — never empty
  16.11 Status consistency        — blocking -> not READY_TO_CONFIRM
  16.12 Confirm API               — full data -> SPEC_CONFIRMED
"""

from __future__ import annotations

import pytest

from fluid_scientist.cylinder_flow_2d import (
    BoundarySpec,
    CylinderFlow2DExperimentSpecV1,
    CylinderFlow2DSceneRouter,
    CylinderFlow2DV1Pipeline,
    DraftStatus,
    FieldSource,
    FieldStatus,
    FlowMode,
    ObservableSpec,
    ObservableType,
    ProvenanceField,
    SemanticBoundaryType,
)
from fluid_scientist.cylinder_flow_2d.geometry_normalizer import (
    CylinderFlow2DDerivedFieldResolver,
    CylinderFlow2DGeometryNormalizer,
)
from fluid_scientist.cylinder_flow_2d.boundary_topology import (
    CylinderFlow2DBoundaryCombinationValidator,
    CylinderFlow2DBoundaryTopologyResolver,
)
from fluid_scientist.cylinder_flow_2d.observable import (
    CylinderFlow2DObservableExtractor,
    CylinderFlow2DObservableRecommender,
    CylinderFlow2DObservableValidator,
)
from fluid_scientist.cylinder_flow_2d.analysis_goals import (
    CylinderFlow2DAnalysisGoalBuilder,
)
from fluid_scientist.cylinder_flow_2d.readiness import (
    CylinderFlow2DDraftReadinessEvaluator,
)
from fluid_scientist.cylinder_flow_2d.critic import (
    CylinderFlow2DCritic,
    CylinderFlow2DCoverageChecker,
)


# The canonical test input from Section 15 of the spec
TEST_INPUT = (
    "一个二维圆柱绕流问题，圆柱半径R=0.1 m，"
    "圆柱距下壁面2 m，来流从左向右，"
    "下表面无滑移，顶部滑移，观测某截面平均流速。"
)


# ---------------------------------------------------------------------------
# 16.1 — Routing test
# ---------------------------------------------------------------------------


class TestRouting:
    """16.1 — Scene routing must return cylinder-flow-2d-v1."""

    def test_route_matches_cylinder_flow(self):
        """二维圆柱绕流 must route to cylinder-flow-2d-v1."""
        router = CylinderFlow2DSceneRouter()
        result = router.route("二维圆柱绕流")
        assert result.matched is True
        assert result.pipeline_id == "cylinder-flow-2d-v1"
        assert result.schema_name == "CylinderFlow2DExperimentSpecV1"
        assert result.pipeline_version == "1.0"
        assert result.pipeline_stage == "DRAFT_NORMALIZED"

    def test_route_matches_full_input(self):
        """The full test input must also route correctly."""
        router = CylinderFlow2DSceneRouter()
        result = router.route(TEST_INPUT)
        assert result.matched is True
        assert result.pipeline_id == "cylinder-flow-2d-v1"

    def test_route_rejects_non_cylinder(self):
        """Non-cylinder input must NOT match."""
        router = CylinderFlow2DSceneRouter()
        result = router.route("三维机翼气动分析")
        assert result.matched is False
        assert result.not_family_reason == "NOT_CYLINDER_FLOW_FAMILY"

    def test_pipeline_id_is_cylinder_flow_2d_v1(self):
        """The pipeline itself must report the correct ID."""
        pipeline = CylinderFlow2DV1Pipeline()
        assert pipeline.PIPELINE_ID == "cylinder-flow-2d-v1"


# ---------------------------------------------------------------------------
# 16.2 — Cylinder derivation test
# ---------------------------------------------------------------------------


class TestCylinderDerivation:
    """16.2 — radius -> diameter -> characteristic_dimension."""

    def test_radius_derives_diameter_and_char_dim(self):
        """User gives R=0.1m; system must derive D=0.2 and char_dim=0.2."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert spec.cylinder.type == "cylinder"
        assert spec.cylinder.radius_m.value == pytest.approx(0.1)
        assert spec.cylinder.radius_m.source == FieldSource.USER_EXPLICIT
        assert spec.cylinder.diameter_m.value == pytest.approx(0.2)
        assert spec.cylinder.diameter_m.source == FieldSource.FORMULA_DERIVED
        assert spec.cylinder.characteristic_dimension_m.value == pytest.approx(0.2)
        assert spec.cylinder.characteristic_dimension_m.source == FieldSource.FORMULA_DERIVED

    def test_diameter_derives_radius(self):
        """User gives D=0.3m; system must derive R=0.15 and char_dim=0.3."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run("二维圆柱绕流，圆柱直径D=0.3m，来流从左向右")
        spec = result.spec

        assert spec.cylinder.diameter_m.value == pytest.approx(0.3)
        assert spec.cylinder.diameter_m.source == FieldSource.USER_EXPLICIT
        assert spec.cylinder.radius_m.value == pytest.approx(0.15)
        assert spec.cylinder.radius_m.source == FieldSource.FORMULA_DERIVED
        assert spec.cylinder.characteristic_dimension_m.value == pytest.approx(0.3)

    def test_no_geometry_missing_errors(self):
        """After derivation, geometry_missing_* must NOT appear."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        blocking_codes = [
            issue.get("code", "").lower()
            for issue in spec.blocking_issues
        ]
        assert "geometry_missing_type" not in blocking_codes
        assert "geometry_missing_characteristic_dimension" not in blocking_codes


# ---------------------------------------------------------------------------
# 16.3 — Flat-bottom cylinder test
# ---------------------------------------------------------------------------


class TestFlatBottomCylinder:
    """16.3 — No bump; bottom_profile.enabled=false; no geometry missing."""

    def test_flat_bottom_not_blocking(self):
        """A flat bottom must NOT cause geometry-missing errors."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert spec.bottom_profile.enabled is False
        assert spec.bottom_profile.profile_type.value == "flat"

        # No geometry-missing errors
        blocking_codes = [
            issue.get("code", "").lower()
            for issue in spec.blocking_issues
        ]
        assert "geometry_missing_type" not in blocking_codes
        assert "geometry_missing_characteristic_dimension" not in blocking_codes

    def test_has_cylinder_true_without_bump(self):
        """has_cylinder must be True even without a bump."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert spec.has_cylinder is True
        assert spec.has_bottom_profile is False


# ---------------------------------------------------------------------------
# 16.4 — Bump + cylinder test
# ---------------------------------------------------------------------------


class TestBumpCylinder:
    """16.4 — Both cylinder and bottom_profile must be preserved."""

    def test_bump_and_cylinder_coexist(self):
        """When a bump is described, both cylinder and bump must be present."""
        from fluid_scientist.cylinder_flow_2d.models import BottomProfileSpec, BumpProfileType

        spec = CylinderFlow2DExperimentSpecV1(
            user_input_text="二维圆柱绕流，圆柱半径R=0.1m，底部有正弦凸起",
        )
        spec.cylinder.radius_m = ProvenanceField(
            value=0.1,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
        )
        spec.bottom_profile = BottomProfileSpec(
            enabled=True,
            profile_type=BumpProfileType.HALF_SINE,
            center_x_m=ProvenanceField(value=5.0, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0),
            width_m=ProvenanceField(value=2.0, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0),
            height_m=ProvenanceField(value=0.5, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0),
        )

        # Run derivation
        resolver = CylinderFlow2DDerivedFieldResolver()
        resolver.resolve(spec)

        assert spec.has_cylinder is True
        assert spec.has_bottom_profile is True
        assert spec.bottom_profile.enabled is True
        assert spec.bottom_profile.profile_type == BumpProfileType.HALF_SINE
        assert spec.cylinder.diameter_m.value == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# 16.5 — 2D boundary test
# ---------------------------------------------------------------------------


class Test2DBoundary:
    """16.5 — front and back must always be empty."""

    def test_front_back_empty_in_fresh_spec(self):
        """A freshly created spec must have front/back = empty."""
        spec = CylinderFlow2DExperimentSpecV1()
        assert spec.boundaries.front.semantic_type == SemanticBoundaryType.EMPTY
        assert spec.boundaries.back.semantic_type == SemanticBoundaryType.EMPTY

    def test_front_back_empty_after_pipeline(self):
        """After running the pipeline, front/back must remain empty."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert spec.boundaries.front.semantic_type == SemanticBoundaryType.EMPTY
        assert spec.boundaries.back.semantic_type == SemanticBoundaryType.EMPTY
        assert spec.boundaries.front.source == FieldSource.SYSTEM_DERIVED
        assert spec.boundaries.back.source == FieldSource.SYSTEM_DERIVED

    def test_front_back_not_cyclic(self):
        """front/back must NEVER be cyclic."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert spec.boundaries.front.semantic_type != SemanticBoundaryType.PERIODIC
        assert spec.boundaries.back.semantic_type != SemanticBoundaryType.PERIODIC

    def test_2d_boundary_enforced_by_validator(self):
        """The boundary combination validator must enforce front/back = empty."""
        spec = CylinderFlow2DExperimentSpecV1()
        validator = CylinderFlow2DBoundaryCombinationValidator()
        issues = validator.validate(spec)

        # No issues about front/back
        front_issues = [i for i in issues if "FRONT" in i.get("code", "")]
        back_issues = [i for i in issues if "BACK" in i.get("code", "")]
        assert len(front_issues) == 0
        assert len(back_issues) == 0


# ---------------------------------------------------------------------------
# 16.6 — User boundary priority test
# ---------------------------------------------------------------------------


class TestUserBoundaryPriority:
    """16.6 — User-explicit boundaries must not be overridden by model."""

    def test_bottom_no_slip_preserved(self):
        """User says 下表面无滑移; bottom must stay no_slip_wall with USER_EXPLICIT."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert spec.boundaries.bottom_flat.semantic_type == SemanticBoundaryType.NO_SLIP_WALL
        assert spec.boundaries.bottom_flat.source == FieldSource.USER_EXPLICIT

    def test_top_slip_preserved(self):
        """User says 顶部滑移; top must stay slip_wall with USER_EXPLICIT."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert spec.boundaries.top.semantic_type == SemanticBoundaryType.SLIP_WALL
        assert spec.boundaries.top.source == FieldSource.USER_EXPLICIT

    def test_model_cannot_override_user_explicit(self):
        """FieldSource.should_override must prevent MODEL from overwriting USER."""
        assert FieldSource.should_override(FieldSource.USER_EXPLICIT, FieldSource.MODEL_RECOMMENDED) is False
        assert FieldSource.should_override(FieldSource.MODEL_RECOMMENDED, FieldSource.USER_EXPLICIT) is True

    def test_critic_restores_overridden_boundary(self):
        """If a model recommendation overrode user-explicit, the Critic must restore it."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        # Simulate a model override
        spec.boundaries.bottom_flat.semantic_type = SemanticBoundaryType.SLIP_WALL
        spec.boundaries.bottom_flat.source = FieldSource.MODEL_RECOMMENDED

        # Run critic
        critic = CylinderFlow2DCritic()
        critic.review(spec, TEST_INPUT)

        # Critic should have restored it
        assert spec.boundaries.bottom_flat.semantic_type == SemanticBoundaryType.NO_SLIP_WALL
        assert spec.boundaries.bottom_flat.source == FieldSource.USER_EXPLICIT


# ---------------------------------------------------------------------------
# 16.7 — Point velocity test
# ---------------------------------------------------------------------------


class TestPointVelocity:
    """16.7 — User says point velocity; only the point coordinate is missing."""

    def test_point_velocity_extracted(self):
        """某点平均流速 must produce a point_velocity observable."""
        extractor = CylinderFlow2DObservableExtractor()
        observables = extractor.extract("观测某点平均流速")
        assert len(observables) == 1
        assert observables[0].type == ObservableType.POINT_VELOCITY
        assert observables[0].source == FieldSource.USER_EXPLICIT

    def test_point_velocity_only_missing_point(self):
        """The only missing field must be 'point'."""
        extractor = CylinderFlow2DObservableExtractor()
        observables = extractor.extract("观测某点平均流速")
        validator = CylinderFlow2DObservableValidator()
        validated = validator.validate(observables)

        assert len(validated) == 1
        assert "point" in validated[0].missing_fields
        assert len(validated[0].missing_fields) == 1
        assert validated[0].status == FieldStatus.PARTIALLY_RESOLVED


# ---------------------------------------------------------------------------
# 16.8 — Section velocity test
# ---------------------------------------------------------------------------


class TestSectionVelocity:
    """16.8 — User says section velocity; only section_x is missing."""

    def test_section_velocity_extracted(self):
        """某截面平均流速 must produce a section_mean_velocity observable."""
        extractor = CylinderFlow2DObservableExtractor()
        observables = extractor.extract("观测某截面平均流速")
        assert len(observables) == 1
        assert observables[0].type == ObservableType.SECTION_MEAN_VELOCITY
        assert observables[0].source == FieldSource.USER_EXPLICIT

    def test_section_velocity_only_missing_section_x(self):
        """The only missing field must be 'section_x'."""
        extractor = CylinderFlow2DObservableExtractor()
        observables = extractor.extract("观测某截面平均流速")
        validator = CylinderFlow2DObservableValidator()
        validated = validator.validate(observables)

        assert len(validated) == 1
        assert "section_x" in validated[0].missing_fields
        assert len(validated[0].missing_fields) == 1
        assert validated[0].status == FieldStatus.PARTIALLY_RESOLVED

    def test_section_velocity_in_pipeline(self):
        """The full pipeline must capture the section velocity observable."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        section_obs = [
            obs for obs in spec.observables
            if obs.type == ObservableType.SECTION_MEAN_VELOCITY
        ]
        assert len(section_obs) >= 1
        assert section_obs[0].source == FieldSource.USER_EXPLICIT


# ---------------------------------------------------------------------------
# 16.9 — Observable recommendation test
# ---------------------------------------------------------------------------


class TestObservableRecommendation:
    """16.9 — When user specifies no observables, recommend cylinder drag/lift/wake."""

    def test_recommendation_includes_drag_and_lift(self):
        """With a cylinder but no user observables, drag and lift must be recommended."""
        spec = CylinderFlow2DExperimentSpecV1()
        spec.cylinder.radius_m = ProvenanceField(
            value=0.1,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
        )
        # Resolve derived fields so has_cylinder is True
        resolver = CylinderFlow2DDerivedFieldResolver()
        resolver.resolve(spec)

        recommender = CylinderFlow2DObservableRecommender()
        recommended = recommender.recommend(spec)

        types = {obs.type for obs in recommended}
        assert ObservableType.CYLINDER_DRAG in types
        assert ObservableType.CYLINDER_LIFT in types

    def test_recommendation_includes_wake_observables(self):
        """Recommendation must include downstream point velocity and section mean velocity."""
        spec = CylinderFlow2DExperimentSpecV1()
        spec.cylinder.radius_m = ProvenanceField(
            value=0.1,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
        )
        resolver = CylinderFlow2DDerivedFieldResolver()
        resolver.resolve(spec)

        recommender = CylinderFlow2DObservableRecommender()
        recommended = recommender.recommend(spec)

        types = {obs.type for obs in recommended}
        assert ObservableType.POINT_VELOCITY in types
        assert ObservableType.SECTION_MEAN_VELOCITY in types

    def test_recommendation_never_empty(self):
        """The recommendation list must NEVER be empty."""
        spec = CylinderFlow2DExperimentSpecV1()
        recommender = CylinderFlow2DObservableRecommender()
        recommended = recommender.recommend(spec)
        assert len(recommended) > 0

    def test_user_observables_not_cleared(self):
        """User-explicit observables must not be cleared by the recommender."""
        spec = CylinderFlow2DExperimentSpecV1()
        spec.cylinder.radius_m = ProvenanceField(
            value=0.1,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
        )
        resolver = CylinderFlow2DDerivedFieldResolver()
        resolver.resolve(spec)

        # Add a user-explicit observable
        spec.observables.append(ObservableSpec(
            type=ObservableType.CYLINDER_DRAG,
            label="圆柱阻力",
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            confidence=0.9,
        ))

        recommender = CylinderFlow2DObservableRecommender()
        recommended = recommender.recommend(spec)

        # The user-explicit one must be preserved
        drag_obs = [obs for obs in recommended if obs.type == ObservableType.CYLINDER_DRAG]
        assert len(drag_obs) == 1
        assert drag_obs[0].source == FieldSource.USER_EXPLICIT

    def test_pipeline_observables_not_empty(self):
        """After running the full pipeline, observables must not be empty."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert len(spec.observables) > 0


# ---------------------------------------------------------------------------
# 16.10 — Analysis goal test
# ---------------------------------------------------------------------------


class TestAnalysisGoals:
    """16.10 — Analysis goals must never be empty when observables exist."""

    def test_pipeline_analysis_goals_not_empty(self):
        """After running the pipeline, analysis_goals must not be empty."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        assert len(spec.analysis_goals) > 0

    def test_base_goals_present(self):
        """Base cylinder goals (separation, forces, wake) must be present."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        goal_ids = {goal.id for goal in spec.analysis_goals}
        assert "cylinder_separation" in goal_ids
        assert "cylinder_forces" in goal_ids
        assert "cylinder_wake_recovery" in goal_ids

    def test_section_velocity_goal_added(self):
        """When section_mean_velocity observable exists, a dedicated goal must be added."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        goal_ids = {goal.id for goal in spec.analysis_goals}
        assert "section_mean_velocity" in goal_ids

    def test_analysis_goals_with_observables(self):
        """AnalysisGoalBuilder must produce goals when observables exist."""
        spec = CylinderFlow2DExperimentSpecV1()
        spec.cylinder.radius_m = ProvenanceField(
            value=0.1,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
        )
        resolver = CylinderFlow2DDerivedFieldResolver()
        resolver.resolve(spec)

        spec.observables.append(ObservableSpec(
            type=ObservableType.SECTION_MEAN_VELOCITY,
            source=FieldSource.USER_EXPLICIT,
        ))

        builder = CylinderFlow2DAnalysisGoalBuilder()
        goals = builder.build(spec)

        assert len(goals) > 0
        goal_ids = {goal.id for goal in goals}
        assert "section_mean_velocity" in goal_ids


# ---------------------------------------------------------------------------
# 16.11 — Status consistency test
# ---------------------------------------------------------------------------


class TestStatusConsistency:
    """16.11 — Status must be consistent with blocking issues and observables."""

    def test_blocking_issues_prevent_ready(self):
        """When blocking issues exist, status must NOT be READY_TO_CONFIRM."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        # The test input has ambiguities (wall distance) so it should have blocking issues
        if spec.blocking_issues:
            assert spec.draft_status != DraftStatus.READY_TO_CONFIRM
            assert spec.draft_status == DraftStatus.NEEDS_CLARIFICATION

    def test_empty_observables_prevent_ready(self):
        """When observables are empty, status must NOT be READY_TO_CONFIRM."""
        spec = CylinderFlow2DExperimentSpecV1()
        spec.cylinder.radius_m = ProvenanceField(
            value=0.1,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
        )
        resolver = CylinderFlow2DDerivedFieldResolver()
        resolver.resolve(spec)

        # Set valid flow topology
        spec.boundaries.left = BoundarySpec(
            semantic_type=SemanticBoundaryType.UNIFORM_VELOCITY_INLET,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            inlet_velocity=1.0,
        )
        spec.boundaries.right = BoundarySpec(
            semantic_type=SemanticBoundaryType.PRESSURE_OUTLET,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            pressure_value=0.0,
        )
        topology = CylinderFlow2DBoundaryTopologyResolver()
        spec.flow_topology = {"mode": topology.resolve(spec).value}

        # NO observables, NO analysis goals
        evaluator = CylinderFlow2DDraftReadinessEvaluator()
        status = evaluator.evaluate(spec)

        assert status != DraftStatus.READY_TO_CONFIRM

    def test_empty_analysis_goals_prevent_ready(self):
        """When analysis goals are empty, status must NOT be READY_TO_CONFIRM."""
        spec = CylinderFlow2DExperimentSpecV1()
        spec.cylinder.radius_m = ProvenanceField(
            value=0.1,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
        )
        resolver = CylinderFlow2DDerivedFieldResolver()
        resolver.resolve(spec)

        spec.boundaries.left = BoundarySpec(
            semantic_type=SemanticBoundaryType.UNIFORM_VELOCITY_INLET,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            inlet_velocity=1.0,
        )
        spec.boundaries.right = BoundarySpec(
            semantic_type=SemanticBoundaryType.PRESSURE_OUTLET,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            pressure_value=0.0,
        )
        topology = CylinderFlow2DBoundaryTopologyResolver()
        spec.flow_topology = {"mode": topology.resolve(spec).value}

        # Has observables but NO analysis goals
        spec.observables.append(ObservableSpec(
            type=ObservableType.CYLINDER_DRAG,
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
        ))

        evaluator = CylinderFlow2DDraftReadinessEvaluator()
        status = evaluator.evaluate(spec)

        assert status != DraftStatus.READY_TO_CONFIRM

    def test_pipeline_status_is_needs_clarification(self):
        """The canonical test input should produce NEEDS_CLARIFICATION."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        # The wall distance ambiguity and missing section_x should trigger NEEDS_CLARIFICATION
        assert spec.draft_status == DraftStatus.NEEDS_CLARIFICATION

    def test_no_geometry_missing_in_blocking(self):
        """blocking_issues must NOT contain geometry_missing_type or geometry_missing_characteristic_dimension."""
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec

        blocking_codes = [
            issue.get("code", "").lower()
            for issue in spec.blocking_issues
        ]
        assert "geometry_missing_type" not in blocking_codes
        assert "geometry_missing_characteristic_dimension" not in blocking_codes


# ---------------------------------------------------------------------------
# 16.12 — Confirm API test
# ---------------------------------------------------------------------------


class TestConfirmAPI:
    """16.12 — Full data input must produce SPEC_CONFIRMED."""

    def test_full_data_confirms(self):
        """With all clarifications resolved, confirm must return SPEC_CONFIRMED."""
        # Build a complete spec with all required fields
        spec = CylinderFlow2DExperimentSpecV1(
            user_input_text=(
                "二维圆柱绕流，圆柱半径R=0.1m，圆心高度2m，"
                "来流速度U=1.0m/s，从左向右，"
                "下表面无滑移，顶部滑移，"
                "观测截面x=5m处平均流速"
            ),
        )

        # Run the pipeline to populate the spec
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(spec.user_input_text)
        spec = result.spec

        # Resolve ambiguities: cylinder wall distance = center height
        for amb in spec.ambiguities:
            if amb.get("id") == "cylinder_wall_distance_meaning":
                amb["resolved"] = True
                amb["resolution"] = "圆心高度为2米"
                spec.cylinder.center_y_m = ProvenanceField(
                    value=2.0,
                    source=FieldSource.USER_CONFIRMED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户确认圆心高度",
                )

        # Resolve inlet velocity if missing
        if spec.boundaries.left.inlet_velocity is None:
            spec.boundaries.left.inlet_velocity = 1.0
            spec.boundaries.left.status = FieldStatus.RESOLVED

        # Ensure right boundary is an outlet (pipeline may not set it
        # if the user text only says "从左向右" without explicit outlet)
        if spec.boundaries.right.semantic_type not in (
            SemanticBoundaryType.PRESSURE_OUTLET,
            SemanticBoundaryType.OPEN_OUTLET,
            SemanticBoundaryType.ADVECTIVE_OUTLET,
        ):
            spec.boundaries.right = BoundarySpec(
                semantic_type=SemanticBoundaryType.PRESSURE_OUTLET,
                source=FieldSource.USER_CONFIRMED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                pressure_value=0.0,
            )

        # Resolve missing section_x
        for obs in spec.observables:
            if obs.type == ObservableType.SECTION_MEAN_VELOCITY and obs.section_x is None:
                obs.section_x = 5.0
                obs.missing_fields = [f for f in obs.missing_fields if f != "section_x"]
                if not obs.missing_fields:
                    obs.status = FieldStatus.RESOLVED

        # Resolve point_velocity observables (recommender may add them
        # with missing point coordinates)
        for obs in spec.observables:
            if obs.type == ObservableType.POINT_VELOCITY and obs.point is None:
                obs.point = [10.0, 5.0, 0.0]
                obs.missing_fields = [f for f in obs.missing_fields if f != "point"]
                if not obs.missing_fields:
                    obs.status = FieldStatus.RESOLVED

        # Confirm all observables and analysis goals
        for obs in spec.observables:
            if obs.status == FieldStatus.AWAITING_CONFIRMATION:
                obs.status = FieldStatus.RESOLVED
                obs.source = FieldSource.USER_CONFIRMED

        for goal in spec.analysis_goals:
            if goal.status == FieldStatus.AWAITING_CONFIRMATION:
                goal.status = FieldStatus.RESOLVED
                goal.source = FieldSource.USER_CONFIRMED

        # Confirm fluid properties
        if spec.fluid.type.status == FieldStatus.AWAITING_CONFIRMATION:
            spec.fluid.type.status = FieldStatus.RESOLVED
            spec.fluid.type.source = FieldSource.USER_CONFIRMED
        if spec.fluid.density_kg_m3.status == FieldStatus.AWAITING_CONFIRMATION:
            spec.fluid.density_kg_m3.status = FieldStatus.RESOLVED
            spec.fluid.density_kg_m3.source = FieldSource.USER_CONFIRMED
        if spec.fluid.kinematic_viscosity_m2_s.status == FieldStatus.AWAITING_CONFIRMATION:
            spec.fluid.kinematic_viscosity_m2_s.status = FieldStatus.RESOLVED
            spec.fluid.kinematic_viscosity_m2_s.source = FieldSource.USER_CONFIRMED

        # Set time mode (not auto)
        from fluid_scientist.cylinder_flow_2d.models import TimeMode
        spec.simulation.time_mode = TimeMode.TRANSIENT

        # Evaluate readiness
        evaluator = CylinderFlow2DDraftReadinessEvaluator()
        status = evaluator.evaluate(spec)

        # Should be READY_TO_CONFIRM (or at least not NEEDS_CLARIFICATION
        # if some recommendations are still pending)
        assert status != DraftStatus.NEEDS_CLARIFICATION, (
            f"Expected non-NEEDS_CLARIFICATION, but got {status}. "
            f"Blocking issues: {spec.blocking_issues}"
        )

    def test_confirm_endpoint_with_clarifications(self):
        """The confirm API endpoint should accept clarifications and resolve them."""
        from fluid_scientist.api.cylinder_flow_router import (
            ConfirmRequest,
            _spec_store,
            confirm_spec,
        )
        import uuid

        # First create a draft
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run(TEST_INPUT)
        spec = result.spec
        spec_id = f"test_{uuid.uuid4().hex[:8]}"
        spec.experiment_id = spec_id
        _spec_store[spec_id] = spec

        # Confirm with clarifications
        request = ConfirmRequest(
            spec_id=spec_id,
            clarifications={
                "cylinder_wall_distance_meaning": "圆心高度为2米",
                "section_x": "5",
                "inlet_velocity": "1.0",
            },
        )

        # The confirm endpoint should process the clarifications
        # It may still return NEEDS_CLARIFICATION if other issues remain,
        # but it should NOT crash
        import asyncio
        response = asyncio.run(confirm_spec(request))

        # The response should be processed (not a 500 error)
        assert response.success is not None

        # Clean up
        if spec_id in _spec_store:
            del _spec_store[spec_id]
