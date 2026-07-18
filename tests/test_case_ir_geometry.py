"""Tests for the geometry AST, capability requirements, Case IR converter,
and semantic fidelity checker.

Run with::

    $env:PYTHONPATH = 'src'; python -m pytest tests/test_case_ir_geometry.py -v
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from fluid_scientist.case_ir.capability_requirements import (
    CapabilityRequirement,
    CapabilityRequirementGraph,
)
from fluid_scientist.case_ir.geometry_ast import (
    BooleanNode,
    CircleNode,
    CosineBellNode,
    GeometryAST,
    GeometryASTBuilder,
    GeometryNode,
    ImportedNode,
    PolygonNode,
    RectangleNode,
    TransformNode,
    TriangleNode,
)
from fluid_scientist.case_ir.geometry_to_case_ir import StudySpecToCaseIRConverter
from fluid_scientist.case_ir.semantic_fidelity import (
    SemanticFidelityChecker,
    SemanticFidelityReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sourced(value: Any, status: str = "user_explicit", unit: str = "m") -> dict[str, Any]:
    """Create a minimal SourcedValue dict."""
    return {
        "value": value,
        "unit": unit,
        "status": status,
        "source_turn_ids": ["turn_0"],
        "confidence": 0.9,
        "derivation_id": None,
        "last_modified_by_patch": None,
    }


def _make_circle_entity(
    entity_id: str = "circle_1",
    cx: float = 2.0,
    cy: float = 3.0,
    radius: float = 1.0,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "semantic_type": "cylinder_2d",
        "primitive": {"type": "circle", "radius": radius, "diameter": radius * 2},
        "polygon_vertices": None,
        "original_user_semantics": "cylinder",
        "placement": {
            "x": _sourced(cx),
            "y": _sourced(cy),
            "orientation": None,
            "attachment": None,
        },
    }


def _make_rectangle_entity(
    entity_id: str = "rect_1",
    cx: float = 5.0,
    cy: float = 5.0,
    width: float = 4.0,
    height: float = 2.0,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "semantic_type": "rectangle_2d",
        "primitive": {"type": "rectangle", "width": width, "height": height},
        "polygon_vertices": None,
        "original_user_semantics": "rectangle",
        "placement": {
            "x": _sourced(cx),
            "y": _sourced(cy),
            "orientation": None,
            "attachment": None,
        },
    }


def _make_triangle_entity(
    entity_id: str = "tri_1",
    vertices: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    if vertices is None:
        vertices = [
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 2.0, "y": 3.0},
        ]
    return {
        "entity_id": entity_id,
        "semantic_type": "triangle_2d",
        "primitive": {"type": "triangle"},
        "polygon_vertices": vertices,
        "original_user_semantics": "triangle",
        "placement": {
            "x": _sourced(2.0),
            "y": _sourced(1.0),
            "orientation": None,
            "attachment": None,
        },
    }


def _make_cosine_bell_entity(
    entity_id: str = "bell_1",
    cx: float = 4.0,
    cy: float = 0.0,
    amplitude: float = 1.0,
    width: float = 2.0,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "semantic_type": "cosine_bell_2d",
        "primitive": {"type": "cosine_bell", "amplitude": amplitude, "width": width},
        "polygon_vertices": None,
        "original_user_semantics": "cosine_bell",
        "placement": {
            "x": _sourced(cx),
            "y": _sourced(cy),
            "orientation": None,
            "attachment": None,
        },
    }


def _make_polygon_entity(
    entity_id: str = "poly_1",
    vertices: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    if vertices is None:
        vertices = [
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 4.0, "y": 4.0},
            {"x": 0.0, "y": 4.0},
        ]
    return {
        "entity_id": entity_id,
        "semantic_type": "polygon_2d",
        "primitive": {"type": "polygon"},
        "polygon_vertices": vertices,
        "original_user_semantics": "polygon",
        "placement": {
            "x": _sourced(2.0),
            "y": _sourced(2.0),
            "orientation": None,
            "attachment": None,
        },
    }


def _make_imported_entity(
    entity_id: str = "imp_1",
    file_path: str = "geometry.stl",
    fmt: str = "stl",
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "semantic_type": "imported",
        "primitive": {"type": "imported", "file_path": file_path, "format": fmt},
        "polygon_vertices": None,
        "original_user_semantics": "imported",
        "placement": {
            "x": _sourced(0.0),
            "y": _sourced(0.0),
            "orientation": None,
            "attachment": None,
        },
    }


def _make_spec_dict() -> dict[str, Any]:
    """Create a complete SimulationStudySpec dict for testing."""
    return {
        "spec_id": "test_spec_001",
        "version": 1,
        "geometry": {
            "domain": {
                "length": _sourced(16.0),
                "width": _sourced(8.0),
                "height": None,
                "dimensions": "2d",
            },
            "entities": {
                "cylinder": _make_circle_entity("cylinder", 4.0, 4.0, 0.5),
                "triangle": _make_triangle_entity("triangle"),
            },
            "relations": [
                {
                    "relation_id": "rel_1",
                    "type": "distance_to",
                    "subject_id": "cylinder",
                    "object_id": "triangle",
                    "parameters": {"value": 2.0, "unit": "m"},
                },
            ],
        },
        "physics": {
            "material": _sourced("water", status="user_explicit", unit="dimensionless"),
            "density": _sourced(1000.0, unit="kg/m3"),
            "kinematic_viscosity": _sourced(1e-6, unit="m2/s"),
            "velocity": _sourced(1.0, unit="m/s"),
            "reynolds_number": _sourced(1000.0, unit="dimensionless"),
            "characteristic_length": _sourced(1.0, unit="m"),
        },
        "numerics": {
            "solver": "pimpleFoam",
            "turbulence_model": "LES",
            "time": {"mode": "transient"},
        },
        "boundaries": {
            "conditions": [
                {
                    "patch_name": "inlet",
                    "role": "inlet",
                    "bc_type": "velocityInlet",
                    "parameters": {"velocity": _sourced(1.0, unit="m/s")},
                    "source_status": "user_explicit",
                },
                {
                    "patch_name": "outlet",
                    "role": "outlet",
                    "bc_type": "pressureOutlet",
                    "parameters": {"pressure": _sourced(0.0, unit="Pa")},
                    "source_status": "user_explicit",
                },
            ],
        },
        "observations": {
            "targets": [
                {
                    "target_id": "obs_cd",
                    "metric": "cd",
                    "function_object_type": "forceCoeffs",
                    "parameters": {},
                },
                {
                    "target_id": "obs_cl",
                    "metric": "cl",
                    "function_object_type": "forceCoeffs",
                    "parameters": {},
                },
            ],
        },
    }


# ===========================================================================
# GeometryASTBuilder tests -- primitives
# ===========================================================================


class TestGeometryASTBuilderPrimitives:
    """Tests for building AST nodes from entity dicts."""

    def setup_method(self) -> None:
        self.builder = GeometryASTBuilder()

    def test_build_circle(self) -> None:
        entity = _make_circle_entity("c1", 2.0, 3.0, 1.5)
        node = self.builder.build_from_entity(entity)
        assert isinstance(node, CircleNode)
        assert node.center_x == 2.0
        assert node.center_y == 3.0
        assert node.radius == 1.5
        assert node.node_type == "circle"

    def test_build_circle_from_diameter(self) -> None:
        entity = _make_circle_entity("c1", 0.0, 0.0, 1.0)
        # Override to only have diameter.
        entity["primitive"] = {"type": "circle", "diameter": 4.0}
        node = self.builder.build_from_entity(entity)
        assert isinstance(node, CircleNode)
        assert node.radius == 2.0

    def test_build_rectangle(self) -> None:
        entity = _make_rectangle_entity("r1", 5.0, 5.0, 4.0, 2.0)
        node = self.builder.build_from_entity(entity)
        assert isinstance(node, RectangleNode)
        # Placement is centre -> corner is (5-2, 5-1) = (3, 4).
        assert node.x == pytest.approx(3.0)
        assert node.y == pytest.approx(4.0)
        assert node.width == 4.0
        assert node.height == 2.0
        assert node.node_type == "rectangle"

    def test_build_triangle(self) -> None:
        verts = [
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 2.0, "y": 3.0},
        ]
        entity = _make_triangle_entity("t1", verts)
        node = self.builder.build_from_entity(entity)
        assert isinstance(node, TriangleNode)
        assert len(node.vertices) == 3
        assert node.vertices[0] == {"x": 0.0, "y": 0.0}
        assert node.vertices[1] == {"x": 4.0, "y": 0.0}
        assert node.vertices[2] == {"x": 2.0, "y": 3.0}
        assert node.node_type == "triangle"

    def test_build_cosine_bell(self) -> None:
        entity = _make_cosine_bell_entity("b1", 4.0, 0.0, 1.0, 2.0)
        node = self.builder.build_from_entity(entity)
        assert isinstance(node, CosineBellNode)
        assert node.x == 4.0
        assert node.y == 0.0
        assert node.amplitude == 1.0
        assert node.width == 2.0
        assert node.node_type == "cosine_bell"

    def test_build_polygon(self) -> None:
        verts = [
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 4.0, "y": 4.0},
            {"x": 0.0, "y": 4.0},
        ]
        entity = _make_polygon_entity("p1", verts)
        node = self.builder.build_from_entity(entity)
        assert isinstance(node, PolygonNode)
        assert len(node.vertices) == 4
        assert node.node_type == "polygon"

    def test_build_imported(self) -> None:
        entity = _make_imported_entity("i1", "geom.stl", "stl")
        node = self.builder.build_from_entity(entity)
        assert isinstance(node, ImportedNode)
        assert node.file_path == "geom.stl"
        assert node.format == "stl"
        assert node.node_type == "imported"

    def test_unknown_geometry_type_raises(self) -> None:
        entity = {
            "entity_id": "unknown",
            "semantic_type": "blob_42d",
            "primitive": {"type": "blob"},
            "placement": {"x": _sourced(0.0), "y": _sourced(0.0)},
        }
        with pytest.raises(ValueError, match="Unknown geometry type"):
            self.builder.build_from_entity(entity)

    def test_build_from_spec(self) -> None:
        geometry_def = {
            "domain": {"length": _sourced(10.0), "dimensions": "2d"},
            "entities": {
                "c1": _make_circle_entity("c1", 2.0, 2.0, 1.0),
                "r1": _make_rectangle_entity("r1", 5.0, 5.0, 2.0, 2.0),
            },
            "relations": [],
        }
        ast = self.builder.build_from_spec(geometry_def)
        assert isinstance(ast, GeometryAST)
        assert isinstance(ast.root, BooleanNode)
        assert ast.root.op == "union"
        assert ast.metadata["entity_count"] == 2

    def test_build_from_spec_single_entity(self) -> None:
        geometry_def = {
            "domain": {"dimensions": "2d"},
            "entities": {"c1": _make_circle_entity("c1", 0.0, 0.0, 1.0)},
            "relations": [],
        }
        ast = self.builder.build_from_spec(geometry_def)
        assert isinstance(ast, GeometryAST)
        assert isinstance(ast.root, CircleNode)


# ===========================================================================
# Bounding-box evaluation tests
# ===========================================================================


class TestEvaluateBbox:
    """Tests for bounding-box evaluation of each primitive."""

    def setup_method(self) -> None:
        self.builder = GeometryASTBuilder()

    def test_bbox_circle(self) -> None:
        node = CircleNode(center_x=2.0, center_y=3.0, radius=1.5)
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(0.5)
        assert bbox["min_y"] == pytest.approx(1.5)
        assert bbox["max_x"] == pytest.approx(3.5)
        assert bbox["max_y"] == pytest.approx(4.5)

    def test_bbox_rectangle(self) -> None:
        node = RectangleNode(x=1.0, y=2.0, width=3.0, height=4.0)
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(1.0)
        assert bbox["min_y"] == pytest.approx(2.0)
        assert bbox["max_x"] == pytest.approx(4.0)
        assert bbox["max_y"] == pytest.approx(6.0)

    def test_bbox_triangle(self) -> None:
        node = TriangleNode(vertices=[
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 2.0, "y": 3.0},
        ])
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(0.0)
        assert bbox["min_y"] == pytest.approx(0.0)
        assert bbox["max_x"] == pytest.approx(4.0)
        assert bbox["max_y"] == pytest.approx(3.0)

    def test_bbox_cosine_bell(self) -> None:
        node = CosineBellNode(x=4.0, y=0.0, amplitude=1.0, width=2.0)
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(3.0)
        assert bbox["min_y"] == pytest.approx(0.0)
        assert bbox["max_x"] == pytest.approx(5.0)
        assert bbox["max_y"] == pytest.approx(1.0)

    def test_bbox_polygon(self) -> None:
        node = PolygonNode(vertices=[
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 4.0, "y": 4.0},
            {"x": 0.0, "y": 4.0},
        ])
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(0.0)
        assert bbox["min_y"] == pytest.approx(0.0)
        assert bbox["max_x"] == pytest.approx(4.0)
        assert bbox["max_y"] == pytest.approx(4.0)


# ===========================================================================
# Area evaluation tests
# ===========================================================================


class TestEvaluateArea:
    """Tests for area evaluation of primitives."""

    def setup_method(self) -> None:
        self.builder = GeometryASTBuilder()

    def test_area_circle(self) -> None:
        node = CircleNode(center_x=0.0, center_y=0.0, radius=2.0)
        area = self.builder.evaluate_area(node)
        assert area == pytest.approx(math.pi * 4.0)

    def test_area_rectangle(self) -> None:
        node = RectangleNode(x=0.0, y=0.0, width=3.0, height=4.0)
        area = self.builder.evaluate_area(node)
        assert area == pytest.approx(12.0)

    def test_area_triangle(self) -> None:
        node = TriangleNode(vertices=[
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 2.0, "y": 3.0},
        ])
        area = self.builder.evaluate_area(node)
        assert area == pytest.approx(6.0)  # 0.5 * 4 * 3

    def test_area_cosine_bell(self) -> None:
        node = CosineBellNode(x=4.0, y=0.0, amplitude=1.0, width=2.0)
        area = self.builder.evaluate_area(node)
        assert area == pytest.approx(1.0)  # amplitude * width / 2

    def test_area_polygon(self) -> None:
        node = PolygonNode(vertices=[
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 4.0, "y": 4.0},
            {"x": 0.0, "y": 4.0},
        ])
        area = self.builder.evaluate_area(node)
        assert area == pytest.approx(16.0)


# ===========================================================================
# BooleanNode tests
# ===========================================================================


class TestBooleanNode:
    """Tests for boolean operation nodes."""

    def setup_method(self) -> None:
        self.builder = GeometryASTBuilder()

    def _make_union(self) -> BooleanNode:
        left = CircleNode(center_x=0.0, center_y=0.0, radius=1.0)
        right = CircleNode(center_x=2.0, center_y=0.0, radius=1.0)
        return BooleanNode(op="union", left=left, right=right)

    def _make_difference(self) -> BooleanNode:
        left = RectangleNode(x=0.0, y=0.0, width=4.0, height=4.0)
        right = CircleNode(center_x=2.0, center_y=2.0, radius=1.0)
        return BooleanNode(op="difference", left=left, right=right)

    def _make_intersection(self) -> BooleanNode:
        left = RectangleNode(x=0.0, y=0.0, width=4.0, height=4.0)
        right = RectangleNode(x=2.0, y=2.0, width=4.0, height=4.0)
        return BooleanNode(op="intersection", left=left, right=right)

    def test_union_bbox(self) -> None:
        node = self._make_union()
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(-1.0)
        assert bbox["max_x"] == pytest.approx(3.0)
        assert bbox["min_y"] == pytest.approx(-1.0)
        assert bbox["max_y"] == pytest.approx(1.0)

    def test_union_area(self) -> None:
        node = self._make_union()
        area = self.builder.evaluate_area(node)
        # Approximate: sum of two circle areas.
        assert area == pytest.approx(2 * math.pi)

    def test_difference_bbox(self) -> None:
        node = self._make_difference()
        bbox = self.builder.evaluate_bbox(node)
        # Difference bbox = left bbox.
        assert bbox["min_x"] == pytest.approx(0.0)
        assert bbox["min_y"] == pytest.approx(0.0)
        assert bbox["max_x"] == pytest.approx(4.0)
        assert bbox["max_y"] == pytest.approx(4.0)

    def test_intersection_bbox(self) -> None:
        node = self._make_intersection()
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(2.0)
        assert bbox["min_y"] == pytest.approx(2.0)
        assert bbox["max_x"] == pytest.approx(4.0)
        assert bbox["max_y"] == pytest.approx(4.0)

    def test_intersection_area(self) -> None:
        node = self._make_intersection()
        area = self.builder.evaluate_area(node)
        # Approximate: min of two rectangle areas.
        assert area == pytest.approx(16.0)

    def test_union_segments(self) -> None:
        node = self._make_union()
        segments = self.builder.to_stl_segments(node)
        # Two circles * 32 segments each.
        assert len(segments) == 64


# ===========================================================================
# TransformNode tests
# ===========================================================================


class TestTransformNode:
    """Tests for transform operation nodes."""

    def setup_method(self) -> None:
        self.builder = GeometryASTBuilder()

    def test_translate_bbox(self) -> None:
        target = RectangleNode(x=0.0, y=0.0, width=2.0, height=2.0)
        node = TransformNode(
            op="translate",
            target=target,
            parameters={"dx": 5.0, "dy": 3.0},
        )
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(5.0)
        assert bbox["min_y"] == pytest.approx(3.0)
        assert bbox["max_x"] == pytest.approx(7.0)
        assert bbox["max_y"] == pytest.approx(5.0)

    def test_translate_area(self) -> None:
        target = RectangleNode(x=0.0, y=0.0, width=3.0, height=4.0)
        node = TransformNode(
            op="translate",
            target=target,
            parameters={"dx": 10.0, "dy": -5.0},
        )
        area = self.builder.evaluate_area(node)
        assert area == pytest.approx(12.0)

    def test_rotate_bbox(self) -> None:
        # Use an origin-centred square so that 90-degree rotation
        # preserves the bounding box.
        target = RectangleNode(x=-1.0, y=-1.0, width=2.0, height=2.0)
        node = TransformNode(
            op="rotate",
            target=target,
            parameters={"angle": math.pi / 2.0},
        )
        bbox = self.builder.evaluate_bbox(node)
        # Rotating a centred 2x2 square by 90 degrees: bbox stays the same.
        assert bbox["min_x"] == pytest.approx(-1.0, abs=1e-9)
        assert bbox["max_x"] == pytest.approx(1.0, abs=1e-9)
        assert bbox["min_y"] == pytest.approx(-1.0, abs=1e-9)
        assert bbox["max_y"] == pytest.approx(1.0, abs=1e-9)

    def test_rotate_area(self) -> None:
        target = RectangleNode(x=0.0, y=0.0, width=3.0, height=4.0)
        node = TransformNode(
            op="rotate",
            target=target,
            parameters={"angle": math.pi / 4.0},
        )
        area = self.builder.evaluate_area(node)
        assert area == pytest.approx(12.0)

    def test_scale_bbox(self) -> None:
        target = RectangleNode(x=0.0, y=0.0, width=2.0, height=2.0)
        node = TransformNode(
            op="scale",
            target=target,
            parameters={"sx": 3.0, "sy": 2.0},
        )
        bbox = self.builder.evaluate_bbox(node)
        assert bbox["min_x"] == pytest.approx(0.0)
        assert bbox["min_y"] == pytest.approx(0.0)
        assert bbox["max_x"] == pytest.approx(6.0)
        assert bbox["max_y"] == pytest.approx(4.0)

    def test_scale_area(self) -> None:
        target = RectangleNode(x=0.0, y=0.0, width=2.0, height=3.0)
        node = TransformNode(
            op="scale",
            target=target,
            parameters={"sx": 2.0, "sy": 3.0},
        )
        area = self.builder.evaluate_area(node)
        assert area == pytest.approx(2.0 * 3.0 * 2.0 * 3.0)

    def test_translate_segments(self) -> None:
        target = TriangleNode(vertices=[
            {"x": 0.0, "y": 0.0},
            {"x": 1.0, "y": 0.0},
            {"x": 0.5, "y": 1.0},
        ])
        node = TransformNode(
            op="translate",
            target=target,
            parameters={"dx": 5.0, "dy": 5.0},
        )
        segments = self.builder.to_stl_segments(node)
        assert len(segments) == 3
        assert segments[0]["start"]["x"] == pytest.approx(5.0)
        assert segments[0]["start"]["y"] == pytest.approx(5.0)


# ===========================================================================
# to_stl_segments tests
# ===========================================================================


class TestToStlSegments:
    """Tests for STL segment conversion."""

    def setup_method(self) -> None:
        self.builder = GeometryASTBuilder()

    def test_triangle_segments(self) -> None:
        node = TriangleNode(vertices=[
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 2.0, "y": 3.0},
        ])
        segments = self.builder.to_stl_segments(node)
        assert len(segments) == 3

        # Segment 0: (0,0) -> (4,0)
        assert segments[0]["start"] == {"x": 0.0, "y": 0.0}
        assert segments[0]["end"] == {"x": 4.0, "y": 0.0}

        # Segment 1: (4,0) -> (2,3)
        assert segments[1]["start"] == {"x": 4.0, "y": 0.0}
        assert segments[1]["end"] == {"x": 2.0, "y": 3.0}

        # Segment 2: (2,3) -> (0,0)
        assert segments[2]["start"] == {"x": 2.0, "y": 3.0}
        assert segments[2]["end"] == {"x": 0.0, "y": 0.0}

    def test_circle_segments_count(self) -> None:
        node = CircleNode(center_x=0.0, center_y=0.0, radius=1.0)
        segments = self.builder.to_stl_segments(node)
        assert len(segments) == 32

    def test_rectangle_segments_count(self) -> None:
        node = RectangleNode(x=0.0, y=0.0, width=2.0, height=3.0)
        segments = self.builder.to_stl_segments(node)
        assert len(segments) == 4

    def test_imported_segments_empty(self) -> None:
        node = ImportedNode(file_path="geom.stl", format="stl")
        segments = self.builder.to_stl_segments(node)
        assert segments == []

    def test_each_segment_has_start_end(self) -> None:
        node = PolygonNode(vertices=[
            {"x": 0.0, "y": 0.0},
            {"x": 1.0, "y": 0.0},
            {"x": 1.0, "y": 1.0},
            {"x": 0.0, "y": 1.0},
            {"x": -0.5, "y": 0.5},
        ])
        segments = self.builder.to_stl_segments(node)
        assert len(segments) == 5
        for seg in segments:
            assert "start" in seg
            assert "end" in seg
            assert "x" in seg["start"]
            assert "y" in seg["start"]
            assert "x" in seg["end"]
            assert "y" in seg["end"]


# ===========================================================================
# CapabilityRequirementGraph tests
# ===========================================================================


class TestCapabilityRequirementGraph:
    """Tests for the capability requirement graph."""

    def test_build_from_spec(self) -> None:
        spec = _make_spec_dict()
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        # Should have requirements for:
        # - 2 geometry entities (cylinder_2d, triangle_2d)
        # - 1 turbulence model (LES)
        # - 1 solver (pimpleFoam)
        # - 2 boundary conditions (velocityInlet, pressureOutlet)
        # - 2 observations (cd, cl)
        # - 0 materials (water is standard)
        assert len(requirements) == 8

        keys = {r.capability_key for r in requirements}
        assert "geometry.cylinder_2d" in keys
        assert "geometry.triangle_2d" in keys
        assert "physics.turbulence.LES" in keys
        assert "solver.pimpleFoam" in keys
        assert "boundary.velocityInlet" in keys
        assert "boundary.pressureOutlet" in keys
        assert "observation.cd" in keys
        assert "observation.cl" in keys

        # Water is standard, so no material requirement.
        assert not any(k.startswith("material.") for k in keys)

    def test_build_from_spec_nonstandard_material(self) -> None:
        spec = _make_spec_dict()
        spec["physics"]["material"] = _sourced(
            "glycerin", status="user_explicit", unit="dimensionless"
        )
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        keys = {r.capability_key for r in requirements}
        assert "material.glycerin" in keys

    def test_build_from_spec_no_turbulence_for_laminar(self) -> None:
        spec = _make_spec_dict()
        spec["numerics"]["turbulence_model"] = "laminar"
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        keys = {r.capability_key for r in requirements}
        assert not any(k.startswith("physics.turbulence.") for k in keys)

    def test_all_requirements_start_unknown(self) -> None:
        spec = _make_spec_dict()
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        for req in requirements:
            assert req.status == "unknown"

    def test_check_requirements_satisfied(self) -> None:
        spec = _make_spec_dict()
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        available = {
            "geometry.cylinder_2d",
            "geometry.triangle_2d",
            "physics.turbulence.LES",
            "solver.pimpleFoam",
            "boundary.velocityInlet",
            "boundary.pressureOutlet",
            "observation.cd",
            "observation.cl",
        }
        checked = graph.check_requirements(requirements, available)

        for req in checked:
            assert req.status == "satisfied"

    def test_check_requirements_missing(self) -> None:
        spec = _make_spec_dict()
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        # Only provide some capabilities.
        available = {
            "geometry.cylinder_2d",
            "solver.pimpleFoam",
        }
        checked = graph.check_requirements(requirements, available)

        satisfied = [r for r in checked if r.status == "satisfied"]
        missing = [r for r in checked if r.status == "missing"]
        assert len(satisfied) == 2
        assert len(missing) == 6

    def test_get_missing(self) -> None:
        spec = _make_spec_dict()
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        available = {"geometry.cylinder_2d"}
        checked = graph.check_requirements(requirements, available)
        missing = graph.get_missing(checked)

        assert len(missing) == 7
        missing_keys = {r.capability_key for r in missing}
        assert "geometry.triangle_2d" in missing_keys
        assert "physics.turbulence.LES" in missing_keys

    def test_get_unknown(self) -> None:
        spec = _make_spec_dict()
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        # Without checking, all are unknown.
        unknown = graph.get_unknown(requirements)
        assert len(unknown) == 8

    def test_req_id_sequence(self) -> None:
        spec = _make_spec_dict()
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        ids = [r.req_id for r in requirements]
        assert ids[0] == "REQ-001"
        assert ids[-1] == "REQ-008"

    def test_required_by_values(self) -> None:
        spec = _make_spec_dict()
        graph = CapabilityRequirementGraph()
        requirements = graph.build_from_spec(spec)

        for req in requirements:
            if req.capability_key.startswith("geometry."):
                assert req.required_by == "user_input"
            elif req.capability_key.startswith("physics.turbulence."):
                assert req.required_by == "physics_model"
            elif req.capability_key.startswith("solver."):
                assert req.required_by == "numerics"
            elif req.capability_key.startswith("boundary."):
                assert req.required_by == "boundary"
            elif req.capability_key.startswith("observation."):
                assert req.required_by == "observation"


# ===========================================================================
# StudySpecToCaseIRConverter tests
# ===========================================================================


class TestStudySpecToCaseIRConverter:
    """Tests for the spec -> Case IR converter."""

    def test_convert_basic_structure(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        assert case_ir["schema_version"] == "2.0"
        assert case_ir["study_id"] == "test_spec_001"
        assert "entities" in case_ir
        assert "boundary_intents" in case_ir
        assert "observables" in case_ir
        assert "physics" in case_ir
        assert "relations" in case_ir
        assert "domain" in case_ir

    def test_convert_entities(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        entities = case_ir["entities"]
        assert len(entities) == 2

        ids = {e["id"] for e in entities}
        assert "cylinder" in ids
        assert "triangle" in ids

    def test_convert_entity_semantic_type_preserved(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        for entity in case_ir["entities"]:
            params = entity["parameters"]
            sem = params.get("semantic_type", {})
            assert isinstance(sem, dict)
            assert "value" in sem
            assert sem["source"] == "USER_EXPLICIT"

    def test_convert_entity_kind_mapping(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        kinds = {e["id"]: e["kind"] for e in case_ir["entities"]}
        assert kinds["cylinder"] == "cylinder"
        assert kinds["triangle"] == "custom"

    def test_convert_boundary_intents(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        boundary_intents = case_ir["boundary_intents"]
        assert len(boundary_intents) == 2

        patches = {bi["target_patch"] for bi in boundary_intents}
        assert "inlet" in patches
        assert "outlet" in patches

    def test_convert_observables(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        observables = case_ir["observables"]
        assert len(observables) == 2

        metrics = {obs["semantic_type"] for obs in observables}
        assert "cd" in metrics
        assert "cl" in metrics

    def test_convert_physics(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        physics = case_ir["physics"]
        assert physics["turbulence"] == "LES"
        assert physics["turbulence_model"] == "LES"
        assert physics["time_mode"] == "transient"

    def test_convert_relations(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        relations = case_ir["relations"]
        assert len(relations) == 1
        assert relations[0]["source"] == "cylinder"
        assert relations[0]["target"] == "triangle"

    def test_convert_preserves_provenance(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        # Check that user_explicit values become USER_EXPLICIT ParameterValues.
        for entity in case_ir["entities"]:
            if entity["id"] == "cylinder":
                params = entity["parameters"]
                center_x = params.get("center_x", {})
                assert center_x.get("source") == "USER_EXPLICIT"
                assert center_x.get("status") == "CONFIRMED"
                assert center_x.get("confidence") == pytest.approx(0.9)

    def test_convert_domain(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        domain = case_ir["domain"]
        assert domain["dimensions"] == "2d"
        assert domain["length"] is not None


# ===========================================================================
# SemanticFidelityChecker tests
# ===========================================================================


class TestSemanticFidelityChecker:
    """Tests for the semantic fidelity checker."""

    def test_matching_spec_passes(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        checker = SemanticFidelityChecker()
        report = checker.check(spec, case_ir)

        assert isinstance(report, SemanticFidelityReport)
        assert report.entity_count_match is True
        assert report.geometry_types_preserved is True
        assert report.dimensions_preserved is True
        assert report.source_evidence_complete is True
        assert report.passed is True

    def test_entity_count_mismatch(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        # Remove one entity from case_ir.
        case_ir["entities"] = case_ir["entities"][:1]

        checker = SemanticFidelityChecker()
        report = checker.check(spec, case_ir)

        assert report.entity_count_match is False
        assert any("Entity count mismatch" in v for v in report.violations)

    def test_geometry_type_change(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        # Change the semantic_type of one entity in case_ir.
        for entity in case_ir["entities"]:
            if entity["id"] == "cylinder":
                entity["parameters"]["semantic_type"]["value"] = "triangle_2d"

        checker = SemanticFidelityChecker()
        report = checker.check(spec, case_ir)

        assert report.geometry_types_preserved is False
        assert any("Geometry type mismatch" in v for v in report.violations)

    def test_dimensions_mismatch(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        # Change domain dimensions.
        case_ir["domain"]["dimensions"] = "3d"

        checker = SemanticFidelityChecker()
        report = checker.check(spec, case_ir)

        assert report.dimensions_preserved is False
        assert any("dimensions mismatch" in v for v in report.violations)

    def test_spatial_relationship_not_preserved(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        # Remove all relations from case_ir.
        case_ir["relations"] = []

        checker = SemanticFidelityChecker()
        report = checker.check(spec, case_ir)

        assert report.spatial_relationships_preserved is False
        assert any("Spatial relationship not preserved" in v for v in report.violations)

    def test_source_evidence_incomplete(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        # Remove USER_EXPLICIT source from all entity parameters.
        for entity in case_ir["entities"]:
            for key, pv in entity["parameters"].items():
                if isinstance(pv, dict):
                    pv["source"] = "SYSTEM_DEFAULT"

        checker = SemanticFidelityChecker()
        report = checker.check(spec, case_ir)

        assert report.source_evidence_complete is False
        assert any("Source evidence incomplete" in v for v in report.violations)

    def test_report_has_violations_list(self) -> None:
        spec = _make_spec_dict()
        converter = StudySpecToCaseIRConverter()
        case_ir = converter.convert(spec)

        case_ir["entities"] = []

        checker = SemanticFidelityChecker()
        report = checker.check(spec, case_ir)

        assert isinstance(report.violations, list)
        assert len(report.violations) > 0

    def test_empty_spec_and_case_ir(self) -> None:
        spec = {
            "geometry": {
                "entities": {},
                "domain": {"dimensions": "2d"},
                "relations": [],
            },
            "physics": {},
            "numerics": {},
            "boundaries": {"conditions": []},
            "observations": {"targets": []},
        }
        case_ir = {
            "entities": [],
            "relations": [],
            "domain": {"dimensions": "2d"},
            "boundary_intents": [],
            "materials": [],
        }

        checker = SemanticFidelityChecker()
        report = checker.check(spec, case_ir)

        assert report.entity_count_match is True
        assert report.passed is True
