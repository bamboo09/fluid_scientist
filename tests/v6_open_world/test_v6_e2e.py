"""E2E tests for v6 open-world architecture.

Tests:
1. Geometry: circle, rectangle, triangle, trapezoid, cosine_bell, half_sine, gaussian, ellipse, unknown
2. Material: water (incompressible), air (compressible), unknown
3. Boundary: velocity_inlet, pressure_outlet, no_slip_wall, slip_wall, periodic, shear_stress
4. Observable: drag, lift, strouhal, velocity_field, section_mean_velocity
5. Multi-turn modification: add entity, change shape, modify boundary
6. Source coverage: all mentions accounted for
7. Capability planning: supported vs missing
8. Error repair: mesh error, boundary error, solver error
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fluid_scientist.research_ir.models import *
from fluid_scientist.research_ir.intent_extractor import OpenWorldIntentExtractor
from fluid_scientist.research_ir.representation_planner import RepresentationPlanner
from fluid_scientist.research_ir.semantic_critic import SemanticCritic
from fluid_scientist.research_ir.coverage import SourceCoverageGuard, CoverageError
from fluid_scientist.research_ir.capability_planner import CapabilityPlanner
from fluid_scientist.research_ir.capability_manifest import get_default_manifest
from fluid_scientist.research_ir.geometry_compiler import PolygonGeometryCompiler
from fluid_scientist.research_ir.dynamic_schema import DynamicSchemaBuilder
from fluid_scientist.research_ir.intent_processors import MaterialProcessor, BoundaryProcessor
from fluid_scientist.research_ir.extension_orchestrator import ExtensionOrchestrator
from fluid_scientist.research_ir.error_repair import OpenFOAMErrorDiagnoser, RepairOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pv(value: float, unit: str = "m") -> ParameterValue:
    """Quick ParameterValue shorthand."""
    return ParameterValue(value=value, unit=unit)


def _domain_2d(length: float = 10.0, width: float = 5.0) -> DomainIntent:
    """A minimal 2-D domain."""
    return DomainIntent(
        dimensionality="2D",
        length=_pv(length),
        width=_pv(width),
    )


def _shape_params(shape: str) -> dict[str, ParameterValue]:
    """Return appropriate parameters for *shape*."""
    if shape == "circle":
        return {"radius": _pv(0.5), "center_x": _pv(2.0), "center_y": _pv(2.0)}
    if shape == "rectangle":
        return {
            "width": _pv(2.0), "height": _pv(1.0),
            "center_x": _pv(5.0), "center_y": _pv(2.0),
        }
    if shape == "triangle":
        return {"base_width": _pv(2.0), "height": _pv(1.0), "center_x": _pv(5.0)}
    if shape == "trapezoid":
        return {
            "top_width": _pv(1.0), "bottom_width": _pv(2.0),
            "height": _pv(0.5), "center_x": _pv(5.0),
        }
    if shape in ("cosine_bell", "half_sine", "gaussian"):
        return {"center_x": _pv(5.0), "width": _pv(2.0), "height": _pv(0.5)}
    if shape == "ellipse":
        return {
            "semi_axis_a": _pv(1.0), "semi_axis_b": _pv(0.5),
            "center_x": _pv(5.0), "center_y": _pv(2.0),
        }
    # unknown / custom
    return {}


def _four_boundaries() -> list[BoundaryIntent]:
    """Return a minimal set of 4 boundaries for a 2-D case."""
    return [
        BoundaryIntent(boundary_id="b_inlet", raw_text="速度入口", physical_role="velocity_inlet"),
        BoundaryIntent(boundary_id="b_outlet", raw_text="压力出口", physical_role="pressure_outlet"),
        BoundaryIntent(boundary_id="b_wall1", raw_text="无滑移壁面", physical_role="no_slip_wall"),
        BoundaryIntent(boundary_id="b_wall2", raw_text="无滑移壁面", physical_role="no_slip_wall"),
    ]


# ---------------------------------------------------------------------------
# 1. Geometry shapes
# ---------------------------------------------------------------------------

class TestGeometryShapes:
    """Tests for geometry shape representation planning and compilation."""

    def test_circle_representation(self):
        entity = GeometryEntity(
            entity_id="geo_circle",
            role="immersed_obstacle",
            raw_name="圆形",
            semantic_shape="circle",
            parameters={
                "radius": _pv(0.5),
                "center_x": _pv(2.0),
                "center_y": _pv(2.0),
            },
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        planner = RepresentationPlanner(llm_client=None)
        planner.plan(entity, ir)

        assert entity.representation.type == "circle"
        assert entity.representation_status == "resolved"

        compiler = PolygonGeometryCompiler()
        result = compiler.compile_entity(entity, domain={"length": 10.0, "width": 5.0})
        assert result["status"] == "compiled"
        assert len(result["vertices"]) == 16  # DEFAULT_CIRCLE_SEGMENTS

    def test_trapezoid_representation(self):
        entity = GeometryEntity(
            entity_id="geo_trap",
            role="wall_attached_obstacle",
            raw_name="梯形",
            semantic_shape="trapezoid",
            parameters={
                "top_width": _pv(1.0),
                "bottom_width": _pv(2.0),
                "height": _pv(0.5),
                "center_x": _pv(5.0),
            },
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        planner = RepresentationPlanner(llm_client=None)
        planner.plan(entity, ir)

        assert entity.representation.type == "explicit_polygon"
        assert entity.representation.subtype == "four_vertex"
        assert entity.representation_status == "resolved"

        compiler = PolygonGeometryCompiler()
        result = compiler.compile_entity(entity, domain={"length": 10.0, "width": 5.0})
        assert result["status"] == "compiled"
        assert len(result["vertices"]) == 4

    def test_triangle_representation(self):
        entity = GeometryEntity(
            entity_id="geo_tri",
            role="wall_attached_obstacle",
            raw_name="三角形",
            semantic_shape="triangle",
            parameters={
                "base_width": _pv(2.0),
                "height": _pv(1.0),
                "center_x": _pv(5.0),
            },
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        planner = RepresentationPlanner(llm_client=None)
        planner.plan(entity, ir)

        assert entity.representation.type == "explicit_polygon"
        assert entity.representation.subtype == "three_vertex"
        assert entity.representation_status == "resolved"

        compiler = PolygonGeometryCompiler()
        result = compiler.compile_entity(entity, domain={"length": 10.0, "width": 5.0})
        assert result["status"] == "compiled"
        assert len(result["vertices"]) == 3

    def test_cosine_bell_representation(self):
        entity = GeometryEntity(
            entity_id="geo_cosine",
            role="wall_attached_obstacle",
            raw_name="余弦凸起",
            semantic_shape="cosine_bell",
            parameters={
                "center_x": _pv(5.0),
                "width": _pv(2.0),
                "height": _pv(0.5),
            },
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        planner = RepresentationPlanner(llm_client=None)
        planner.plan(entity, ir)

        assert entity.representation.type == "profile_function"
        assert entity.representation.subtype == "cosine"
        assert entity.representation_status == "resolved"

        compiler = PolygonGeometryCompiler()
        result = compiler.compile_entity(entity, domain={"length": 10.0, "width": 5.0})
        assert result["status"] == "compiled"
        assert len(result["vertices"]) > 0

    def test_unknown_shape_needs_clarification(self):
        entity = GeometryEntity(
            entity_id="geo_unknown",
            role="immersed_obstacle",
            raw_name="自定义形状",
            semantic_shape="custom_shape",
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        planner = RepresentationPlanner(llm_client=None)
        planner.plan(entity, ir)

        assert entity.representation.type == "unknown"
        assert entity.representation_status == "needs_clarification"

        compiler = PolygonGeometryCompiler()
        result = compiler.compile_entity(entity, domain={"length": 10.0, "width": 5.0})
        assert result["status"] == "needs_clarification"

    @pytest.mark.parametrize(
        "shape",
        [
            "circle", "rectangle", "triangle", "trapezoid",
            "cosine_bell", "half_sine", "gaussian", "ellipse",
            "unknown",
        ],
    )
    def test_all_shapes_compile(self, shape):
        params = _shape_params(shape)
        entity = GeometryEntity(
            entity_id=f"geo_{shape}",
            role="immersed_obstacle",
            semantic_shape=shape,
            parameters=params,
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        planner = RepresentationPlanner(llm_client=None)
        planner.plan(entity, ir)

        compiler = PolygonGeometryCompiler()
        result = compiler.compile_entity(entity, domain={"length": 10.0, "width": 5.0})

        if shape == "unknown":
            assert result["status"] == "needs_clarification"
        else:
            assert result["status"] == "compiled"
            assert len(result["vertices"]) > 0


# ---------------------------------------------------------------------------
# 2. Material processing
# ---------------------------------------------------------------------------

class TestMaterialProcessing:
    """Tests for material intent processing (rule-based, no LLM)."""

    def test_water_incompressible(self):
        material = MaterialIntent(material_id="mat_1", raw_name="水")
        MaterialProcessor().process([material])

        assert material.phase == "liquid"
        assert material.model == "incompressible_newtonian"

    def test_air_compressible(self):
        material = MaterialIntent(material_id="mat_1", raw_name="空气")
        MaterialProcessor().process([material])

        assert material.phase == "gas"
        assert material.model == "compressible_newtonian"

    def test_unknown_material(self):
        material = MaterialIntent(material_id="mat_1", raw_name="未知流体")
        MaterialProcessor().process([material])

        assert material.phase == "unknown"
        assert material.model == "unknown"

    def test_missing_properties(self):
        material = MaterialIntent(
            material_id="mat_1",
            raw_name="水",
            model="incompressible_newtonian",
        )
        MaterialProcessor().process([material])

        assert "density" in material.missing_required_properties
        assert "viscosity" in material.missing_required_properties


# ---------------------------------------------------------------------------
# 3. Boundary processing
# ---------------------------------------------------------------------------

class TestBoundaryProcessing:
    """Tests for boundary intent processing (rule-based, no LLM)."""

    def test_velocity_inlet(self):
        b = BoundaryIntent(boundary_id="b1", raw_text="速度入口")
        BoundaryProcessor().process([b], domain_dim="2D")

        assert b.physical_role == "velocity_inlet"

    def test_pressure_outlet(self):
        b = BoundaryIntent(boundary_id="b1", raw_text="压力出口")
        BoundaryProcessor().process([b], domain_dim="2D")

        assert b.physical_role == "pressure_outlet"

    def test_no_slip_wall(self):
        b = BoundaryIntent(boundary_id="b1", raw_text="无滑移壁面")
        BoundaryProcessor().process([b], domain_dim="2D")

        assert b.physical_role == "no_slip_wall"

    def test_periodic(self):
        b = BoundaryIntent(boundary_id="b1", raw_text="周期边界")
        BoundaryProcessor().process([b], domain_dim="2D")

        assert b.physical_role == "periodic"

    def test_shear_stress(self):
        b = BoundaryIntent(boundary_id="b1", raw_text="切向应力")
        BoundaryProcessor().process([b], domain_dim="2D")

        assert b.physical_role == "shear_stress"
        # shear_stress role requires a shear_stress quantity that was not
        # provided, so the semantic status should be "incomplete".
        assert b.semantic_status == "incomplete"

    def test_unknown_boundary(self):
        b = BoundaryIntent(boundary_id="b1", raw_text="特殊边界")
        BoundaryProcessor().process([b], domain_dim="2D")

        assert b.physical_role == "unknown"
        assert b.semantic_status == "needs_clarification"


# ---------------------------------------------------------------------------
# 4. Observable mapping
# ---------------------------------------------------------------------------

class TestObservableMapping:
    """Tests for observable-to-capability mapping."""

    def test_drag_coefficient(self):
        obs = ObservableIntent(observable_id="obs_1", physical_quantity="阻力系数")
        ir = OpenWorldResearchIR(observables=[obs])
        plan = CapabilityPlanner(llm_client=None).plan(ir)

        assert "observable.drag_coefficient" in plan.supported
        assert not plan.is_blocked

    def test_lift_coefficient(self):
        obs = ObservableIntent(observable_id="obs_1", physical_quantity="升力系数")
        ir = OpenWorldResearchIR(observables=[obs])
        plan = CapabilityPlanner(llm_client=None).plan(ir)

        assert "observable.lift_coefficient" in plan.supported

    def test_strouhal_number(self):
        obs = ObservableIntent(observable_id="obs_1", physical_quantity="涡脱落频率")
        ir = OpenWorldResearchIR(observables=[obs])
        plan = CapabilityPlanner(llm_client=None).plan(ir)

        assert "observable.strouhal_number" in plan.supported

    def test_unknown_observable(self):
        obs = ObservableIntent(observable_id="obs_1", physical_quantity="自定义指标")
        ir = OpenWorldResearchIR(observables=[obs])
        plan = CapabilityPlanner(llm_client=None).plan(ir)

        # Unknown observable → missing capability with warning severity.
        assert any(m.severity == "warning" for m in plan.missing)
        # Warning-level missing capabilities do not block.
        assert not plan.is_blocked


# ---------------------------------------------------------------------------
# 5. Source coverage
# ---------------------------------------------------------------------------

class TestSourceCoverage:
    """Tests for the source coverage guard."""

    def test_complete_coverage(self):
        mention = Mention(
            mention_id="m1", text="cylinder", category="geometry",
            status="mapped",
        )
        ir = OpenWorldResearchIR()
        ir.source_coverage.mention_inventory.mentions.append(mention)

        guard = SourceCoverageGuard()
        assert ir.source_coverage.is_complete
        assert guard.check(ir) is None

    def test_incomplete_coverage(self):
        mention = Mention(
            mention_id="m1", text="unknown thing", category="unknown",
            status="ignored",
        )
        ir = OpenWorldResearchIR()
        ir.source_coverage.mention_inventory.mentions.append(mention)

        guard = SourceCoverageGuard()
        assert not ir.source_coverage.is_complete
        with pytest.raises(CoverageError):
            guard.enforce(ir)

    def test_coverage_report(self):
        m1 = Mention(
            mention_id="m1", text="cylinder", category="geometry",
            status="mapped",
        )
        m2 = Mention(
            mention_id="m2", text="unknown thing", category="unknown",
            status="ignored",
        )
        ir = OpenWorldResearchIR()
        ir.source_coverage.mention_inventory.mentions.extend([m1, m2])

        guard = SourceCoverageGuard()
        report = guard.report(ir)

        assert report["total_mentions"] == 2
        assert report["accounted"] == 1
        assert report["unaccounted"] == 1
        assert report["is_complete"] is False
        assert len(report["mention_details"]) == 2
        assert "unknown thing" in report["unaccounted_texts"]


# ---------------------------------------------------------------------------
# 6. Capability planning
# ---------------------------------------------------------------------------

class TestCapabilityPlanning:
    """Tests for capability planning (supported vs. missing)."""

    def test_all_supported(self):
        entity = GeometryEntity(
            entity_id="geo_1",
            semantic_shape="circle",
            parameters={
                "radius": _pv(0.5),
                "center_x": _pv(2.0),
                "center_y": _pv(2.0),
            },
        )
        material = MaterialIntent(
            material_id="mat_1", model="incompressible_newtonian",
            raw_name="水",
            properties={
                "density": _pv(1000.0, "kg/m3"),
                "viscosity": _pv(1e-3, "Pa.s"),
            },
        )
        boundary = BoundaryIntent(
            boundary_id="bnd_1", physical_role="velocity_inlet",
        )
        ir = OpenWorldResearchIR(
            geometry_entities=[entity],
            materials=[material],
            boundaries=[boundary],
        )
        plan = CapabilityPlanner(llm_client=None).plan(ir)

        assert not plan.is_blocked
        assert "geometry.circle" in plan.supported
        assert "material.incompressible_newtonian" in plan.supported
        assert "boundary.velocity_inlet" in plan.supported

    def test_missing_geometry(self):
        entity = GeometryEntity(entity_id="geo_1", semantic_shape="star")
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        plan = CapabilityPlanner(llm_client=None).plan(ir)

        assert plan.is_blocked
        geo_missing = [m for m in plan.missing if m.category == "geometry"]
        assert len(geo_missing) > 0
        assert geo_missing[0].severity == "blocking"

    def test_missing_material(self):
        material = MaterialIntent(material_id="mat_1", model="unknown")
        ir = OpenWorldResearchIR(materials=[material])
        plan = CapabilityPlanner(llm_client=None).plan(ir)

        assert any("mat_1" in nc for nc in plan.needs_clarification)

    def test_missing_boundary(self):
        boundary = BoundaryIntent(boundary_id="bnd_1", physical_role="unknown")
        ir = OpenWorldResearchIR(boundaries=[boundary])
        plan = CapabilityPlanner(llm_client=None).plan(ir)

        assert any("bnd_1" in nc for nc in plan.needs_clarification)


# ---------------------------------------------------------------------------
# 7. Semantic critic
# ---------------------------------------------------------------------------

class TestSemanticCritic:
    """Tests for semantic critic review."""

    def test_clean_ir_passes(self):
        entity = GeometryEntity(
            entity_id="geo_1",
            role="immersed_obstacle",
            raw_name="圆形",
            semantic_shape="circle",
            representation_status="resolved",
            parameters={
                "center_x": _pv(2.0),
                "center_y": _pv(2.0),
                "radius": _pv(0.5),
            },
        )
        ir = OpenWorldResearchIR(
            dimensionality="2D",
            geometry_entities=[entity],
            boundaries=_four_boundaries(),
        )
        critic = SemanticCritic(llm_client=None)
        result = critic.review(ir, user_text="2D flow past a circle")

        assert result.passed

    def test_geometry_mismatch(self):
        entity = GeometryEntity(
            entity_id="geo_1",
            raw_name="三角形",
            semantic_shape="cosine_bell",
            representation_status="resolved",
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        critic = SemanticCritic(llm_client=None)
        result = critic.review(ir, user_text="三角形 obstacle in the flow")

        codes = [issue["code"] for issue in result.blocking_issues]
        assert "GEOMETRY_TYPE_MISMATCH" in codes

    def test_missing_boundary(self):
        ir = OpenWorldResearchIR(
            dimensionality="2D",
            boundaries=[
                BoundaryIntent(boundary_id="b1", physical_role="velocity_inlet"),
            ],
        )
        critic = SemanticCritic(llm_client=None)
        result = critic.review(ir, user_text="2D flow")

        codes = [issue["code"] for issue in result.blocking_issues]
        assert "MISSING_BOUNDARY" in codes

    def test_duplicate_entity(self):
        e1 = GeometryEntity(
            entity_id="geo_1",
            raw_name="circle A",
            semantic_shape="circle",
            representation_status="resolved",
            parameters={
                "center_x": _pv(3.0),
                "center_y": _pv(3.0),
                "radius": _pv(0.5),
            },
        )
        e2 = GeometryEntity(
            entity_id="geo_2",
            raw_name="circle B",
            semantic_shape="circle",
            representation_status="resolved",
            parameters={
                "center_x": _pv(3.0),
                "center_y": _pv(3.0),
                "radius": _pv(1.0),
            },
        )
        ir = OpenWorldResearchIR(geometry_entities=[e1, e2])
        critic = SemanticCritic(llm_client=None)
        result = critic.review(ir, user_text="two circles at same position")

        codes = [issue["code"] for issue in result.blocking_issues]
        assert "DUPLICATE_ENTITY" in codes

    def test_unaccounted_mention(self):
        mention = Mention(
            mention_id="m1", text="unknown thing", category="unknown",
            status="ignored",
        )
        ir = OpenWorldResearchIR()
        ir.source_coverage.mention_inventory.mentions.append(mention)
        critic = SemanticCritic(llm_client=None)
        result = critic.review(ir, user_text="unknown thing")

        codes = [issue["code"] for issue in result.blocking_issues]
        assert "UNACCOUNTED_MENTION" in codes


# ---------------------------------------------------------------------------
# 8. Extension orchestrator
# ---------------------------------------------------------------------------

class TestExtensionOrchestrator:
    """Tests for the extension orchestrator pipeline."""

    def test_pipeline_without_llm(self):
        orchestrator = ExtensionOrchestrator(llm_client=None)
        result = orchestrator.run("2D flow past a cylinder at Re=100")

        assert len(result.pipeline_log) == 9

    def test_pipeline_with_geometry(self):
        orchestrator = ExtensionOrchestrator(llm_client=None)
        entity = GeometryEntity(
            entity_id="geo_cyl",
            role="immersed_obstacle",
            raw_name="圆柱",
            semantic_shape="cylinder",
            parameters={
                "radius": _pv(0.5),
                "center_x": _pv(5.0),
                "center_y": _pv(2.0),
            },
        )
        ir = OpenWorldResearchIR(
            dimensionality="2D",
            geometry_entities=[entity],
            domain=_domain_2d(),
        )
        # Run geometry sub-pipeline directly (no LLM needed).
        orchestrator.representation_planner.plan_all(ir)
        compiled = orchestrator.geometry_compiler.compile_all(ir)

        assert len(compiled) > 0
        assert compiled[0]["status"] == "compiled"
        assert "vertices" in compiled[0]

    def test_pipeline_blocking_issues(self):
        orchestrator = ExtensionOrchestrator(llm_client=None)
        result = orchestrator.run("some ambiguous text")

        # Without LLM the fallback IR records the full text as an
        # ignored mention, so the critic flags UNACCOUNTED_MENTION and
        # the coverage guard flags COVERAGE_INCOMPLETE.
        assert len(result.blocking_issues) > 0

    def test_pipeline_log(self):
        orchestrator = ExtensionOrchestrator(llm_client=None)
        result = orchestrator.run("test text")

        assert len(result.pipeline_log) == 9
        step_names = [entry["step"] for entry in result.pipeline_log]
        assert "extract" in step_names
        assert "plan_representations" in step_names
        assert "compile_geometry" in step_names
        assert "build_display" in step_names


# ---------------------------------------------------------------------------
# 9. Error repair
# ---------------------------------------------------------------------------

class TestErrorRepair:
    """Tests for OpenFOAM error diagnosis."""

    def test_mesh_error_diagnosis(self):
        diagnoser = OpenFOAMErrorDiagnoser()
        log = "FOAM FATAL ERROR: blockMesh failed, mesh not valid"
        diag = diagnoser.diagnose(log)

        assert diag.category == "MESH_ERROR"
        assert diag.is_blocking

    def test_boundary_error_diagnosis(self):
        diagnoser = OpenFOAMErrorDiagnoser()
        log = "boundary condition error: patch 'inlet' not found"
        diag = diagnoser.diagnose(log)

        assert diag.category == "BOUNDARY_CONDITION_ERROR"
        assert diag.is_blocking

    def test_solver_error_diagnosis(self):
        diagnoser = OpenFOAMErrorDiagnoser()
        log = "segmentation fault (core dumped)"
        diag = diagnoser.diagnose(log)

        assert diag.category == "SOLVER_ERROR"
        assert diag.is_blocking

    def test_physics_error_diagnosis(self):
        diagnoser = OpenFOAMErrorDiagnoser()
        log = "Courant number mean: 5.2 max: 12.3 exceeds limit"
        diag = diagnoser.diagnose(log)

        assert diag.category == "PHYSICS_ERROR"
        assert diag.severity == "warning"

    def test_nan_detection(self):
        diagnoser = OpenFOAMErrorDiagnoser()
        log = "FOAM FATAL ERROR: nan detected in solution field U"
        diag = diagnoser.diagnose(log)

        assert diag.category == "PHYSICS_ERROR"
        assert diag.is_blocking

    def test_unknown_error(self):
        diagnoser = OpenFOAMErrorDiagnoser()
        log = "Some unrecognised error message with no pattern"
        diag = diagnoser.diagnose(log)

        assert diag.category == "UNKNOWN_ERROR"


# ---------------------------------------------------------------------------
# 10. Dynamic schema
# ---------------------------------------------------------------------------

class TestDynamicSchema:
    """Tests for dynamic schema builder."""

    def test_build_schema(self):
        entity = GeometryEntity(
            entity_id="geo_1",
            semantic_shape="circle",
            parameters={"radius": _pv(0.5)},
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        builder = DynamicSchemaBuilder()
        schema = builder.build_schema(ir)

        assert "geometry_entities" in schema["properties"]
        assert schema["properties"]["geometry_entities"]["type"] == "array"

    def test_build_form_layout(self):
        entity = GeometryEntity(
            entity_id="geo_1",
            raw_name="圆形",
            semantic_shape="circle",
            parameters={"radius": _pv(0.5)},
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        builder = DynamicSchemaBuilder()
        layout = builder.build_form_layout(ir)

        assert "geometry" in layout
        assert len(layout["geometry"]) == 1
        assert layout["geometry"][0]["entity_id"] == "geo_1"

    def test_unknown_entity_in_form(self):
        entity = GeometryEntity(
            entity_id="geo_1",
            raw_name="custom shape",
            semantic_shape="custom_shape",
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        planner = RepresentationPlanner(llm_client=None)
        planner.plan(entity, ir)
        assert entity.representation_status == "needs_clarification"

        builder = DynamicSchemaBuilder()
        schema = builder.build_schema(ir)
        entity_schemas = schema["properties"]["geometry_entities"]["items"]
        assert isinstance(entity_schemas, list)
        assert entity_schemas[0]["is_unknown"] is True

    def test_serialize_for_display(self):
        ir = OpenWorldResearchIR()
        builder = DynamicSchemaBuilder()
        display = builder.serialize_ir_for_display(ir)

        assert "source_coverage_report" in display


# ---------------------------------------------------------------------------
# 11. Multi-turn modification
# ---------------------------------------------------------------------------

class TestMultiTurnModification:
    """Tests for multi-turn IR modification."""

    def test_add_entity(self):
        e1 = GeometryEntity(entity_id="geo_1", semantic_shape="circle")
        ir = OpenWorldResearchIR(geometry_entities=[e1])
        assert len(ir.geometry_entities) == 1

        e2 = GeometryEntity(entity_id="geo_2", semantic_shape="rectangle")
        ir.geometry_entities.append(e2)
        assert len(ir.geometry_entities) == 2

    def test_change_shape(self):
        entity = GeometryEntity(
            entity_id="geo_1",
            semantic_shape="rectangle",
            parameters={
                "width": _pv(2.0),
                "height": _pv(1.0),
                "center_x": _pv(5.0),
                "center_y": _pv(2.0),
            },
        )
        ir = OpenWorldResearchIR(geometry_entities=[entity])
        planner = RepresentationPlanner(llm_client=None)
        planner.plan(entity, ir)
        assert entity.representation.subtype == "axis_aligned"

        # Change shape to triangle.
        entity.semantic_shape = "triangle"
        entity.parameters = {
            "base_width": _pv(2.0),
            "height": _pv(1.0),
            "center_x": _pv(5.0),
        }
        entity.representation = GeometryRepresentation()
        entity.representation_status = "needs_clarification"
        planner.plan(entity, ir)
        assert entity.representation.subtype == "three_vertex"

    def test_modify_boundary(self):
        b = BoundaryIntent(
            boundary_id="bnd_1", physical_role="velocity_inlet",
        )
        assert b.physical_role == "velocity_inlet"

        b.physical_role = "pressure_outlet"
        assert b.physical_role == "pressure_outlet"
