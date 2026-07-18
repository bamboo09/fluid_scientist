"""Geometry AST -- a formal abstract syntax tree for simulation geometry.

This module provides a Pydantic v2-based AST that captures the geometric
intent of a simulation case in a solver-agnostic, deterministic form.
The AST is built from :class:`~fluid_scientist.study_spec.geometry.GeometryEntity`
dicts and supports boolean operations (union / difference / intersection)
and affine transforms (translate / rotate / scale).

The :class:`GeometryASTBuilder` also provides geometric evaluation
utilities -- bounding-box computation, area computation, and conversion
to line segments for downstream meshing.

Design principles
-----------------
* **Deterministic** -- the same input always produces the same AST.
* **No silent fallback** -- unknown geometry types raise
  :class:`ValueError` rather than being silently coerced.
* **Provenance-friendly** -- the builder works with both raw dicts and
  serialised :class:`SourcedValue` wrappers.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "BooleanNode",
    "CircleNode",
    "CosineBellNode",
    "GeometryAST",
    "GeometryASTBuilder",
    "GeometryNode",
    "ImportedNode",
    "PolygonNode",
    "PrimitiveNode",
    "RectangleNode",
    "TransformNode",
    "TriangleNode",
]


# ---------------------------------------------------------------------------
# Base node
# ---------------------------------------------------------------------------


class GeometryNode(BaseModel):
    """Abstract base class for all geometry AST nodes.

    Every node carries a ``node_type`` discriminator and a list of
    child nodes (empty for leaf primitives).
    """

    node_type: str
    children: list[GeometryNode] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Primitive nodes
# ---------------------------------------------------------------------------


class PrimitiveNode(GeometryNode):
    """Base class for primitive geometry nodes."""

    node_type: str = "primitive"


class CircleNode(PrimitiveNode):
    """A circle defined by centre and radius."""

    node_type: Literal["circle"] = "circle"
    center_x: float
    center_y: float
    radius: float


class RectangleNode(PrimitiveNode):
    """An axis-aligned rectangle defined by bottom-left corner and size."""

    node_type: Literal["rectangle"] = "rectangle"
    x: float
    y: float
    width: float
    height: float


class TriangleNode(PrimitiveNode):
    """A triangle defined by exactly three vertices."""

    node_type: Literal["triangle"] = "triangle"
    vertices: list[dict[str, float]]

    @model_validator(mode="after")
    def _validate_vertex_count(self) -> TriangleNode:
        if len(self.vertices) != 3:
            raise ValueError(
                f"TriangleNode requires exactly 3 vertices, got {len(self.vertices)}"
            )
        return self


class CosineBellNode(PrimitiveNode):
    """A cosine-bell bump defined by centre, amplitude, and width."""

    node_type: Literal["cosine_bell"] = "cosine_bell"
    x: float
    y: float
    amplitude: float
    width: float


class PolygonNode(PrimitiveNode):
    """A polygon defined by any number of vertices (>= 3)."""

    node_type: Literal["polygon"] = "polygon"
    vertices: list[dict[str, float]]

    @model_validator(mode="after")
    def _validate_vertex_count(self) -> PolygonNode:
        if len(self.vertices) < 3:
            raise ValueError(
                f"PolygonNode requires at least 3 vertices, got {len(self.vertices)}"
            )
        return self


class ImportedNode(PrimitiveNode):
    """An imported geometry (e.g. STL surface)."""

    node_type: Literal["imported"] = "imported"
    file_path: str
    format: str


# ---------------------------------------------------------------------------
# Composite nodes
# ---------------------------------------------------------------------------


class BooleanNode(GeometryNode):
    """A boolean operation between two geometry nodes.

    The ``left`` and ``right`` operands are stored as generic
    :class:`GeometryNode` instances.  The builder populates ``children``
    with ``[left, right]`` for convenience.
    """

    node_type: Literal["boolean"] = "boolean"
    op: Literal["union", "difference", "intersection"]
    left: GeometryNode
    right: GeometryNode


class TransformNode(GeometryNode):
    """An affine transform applied to a target geometry node.

    Parameters depend on the operation:

    * ``translate`` -- ``{"dx": float, "dy": float}``
    * ``rotate`` -- ``{"angle": float}`` (radians)
    * ``scale`` -- ``{"sx": float, "sy": float}``
    """

    node_type: Literal["transform"] = "transform"
    op: Literal["translate", "rotate", "scale"]
    target: GeometryNode
    parameters: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# GeometryAST -- the full tree
# ---------------------------------------------------------------------------


class GeometryAST(BaseModel):
    """A complete geometry AST with domain metadata.

    Attributes:
        root: The root :class:`GeometryNode` of the tree.
        domain: Domain specification dict (length, width, height, dimensions).
        metadata: Extra metadata (entity count, source, etc.).
    """

    root: GeometryNode
    domain: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Semantic-type to primitive-type mapping
# ---------------------------------------------------------------------------

_SEMANTIC_TO_PRIMITIVE: dict[str, str] = {
    "cylinder_2d": "circle",
    "cylinder_3d": "circle",
    "circle_2d": "circle",
    "sphere_2d": "circle",
    "sphere_3d": "circle",
    "rectangle_2d": "rectangle",
    "box_3d": "rectangle",
    "triangle_2d": "triangle",
    "triangle_3d": "triangle",
    "cosine_bell_2d": "cosine_bell",
    "cosine_bell_3d": "cosine_bell",
    "polygon_2d": "polygon",
    "polygon_3d": "polygon",
    "imported": "imported",
    "imported_stl": "imported",
    "stl": "imported",
}

_PRIMITIVE_ALIASES: dict[str, str] = {
    "circle": "circle",
    "cylinder": "circle",
    "rectangle": "rectangle",
    "box": "rectangle",
    "triangle": "triangle",
    "cosine_bell": "cosine_bell",
    "cosinebell": "cosine_bell",
    "polygon": "polygon",
    "imported": "imported",
    "stl": "imported",
}


# ---------------------------------------------------------------------------
# GeometryASTBuilder
# ---------------------------------------------------------------------------


class GeometryASTBuilder:
    """Builds and evaluates geometry ASTs from study-spec geometry dicts.

    The builder is the bridge between the high-level
    :class:`~fluid_scientist.study_spec.geometry.GeometryDefinition`
    (which stores semantic types and provenance-wrapped values) and the
    formal, deterministic :class:`GeometryAST`.
    """

    # ------------------------------------------------------------------
    # Value extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_numeric(sourced: Any) -> float | None:
        """Extract a float from a SourcedValue dict or raw number."""
        if sourced is None:
            return None
        if isinstance(sourced, int | float):
            return float(sourced)
        if isinstance(sourced, dict):
            v = sourced.get("value")
            if isinstance(v, int | float):
                return float(v)
        return None

    @staticmethod
    def _resolve_primitive_type(entity: dict[str, Any]) -> str:
        """Determine the primitive type from an entity dict.

        Uses the ``primitive["type"]`` field first, then falls back to
        the ``semantic_type``.  Raises :class:`ValueError` for unknown
        types.
        """
        primitive = entity.get("primitive")
        if primitive and isinstance(primitive, dict):
            ptype = primitive.get("type", "").lower()
            if ptype in _PRIMITIVE_ALIASES:
                return _PRIMITIVE_ALIASES[ptype]

        semantic_type = entity.get("semantic_type", "").lower()
        if semantic_type in _SEMANTIC_TO_PRIMITIVE:
            return _SEMANTIC_TO_PRIMITIVE[semantic_type]

        raise ValueError(
            f"Unknown geometry type: primitive={primitive!r}, "
            f"semantic_type={semantic_type!r}"
        )

    # ------------------------------------------------------------------
    # Build from entity
    # ------------------------------------------------------------------

    def build_from_entity(self, entity: dict[str, Any]) -> GeometryNode:
        """Convert a :class:`GeometryEntity` dict to an AST node.

        Args:
            entity: A serialised ``GeometryEntity`` dict (or compatible
                plain dict with ``semantic_type``, ``primitive``,
                ``polygon_vertices``, and ``placement`` keys).

        Returns:
            A concrete :class:`GeometryNode` subclass instance.

        Raises:
            ValueError: If the geometry type is unknown.
        """
        ptype = self._resolve_primitive_type(entity)
        placement = entity.get("placement") or {}
        cx = self._extract_numeric(placement.get("x")) or 0.0
        cy = self._extract_numeric(placement.get("y")) or 0.0
        primitive = entity.get("primitive") or {}
        polygon_vertices = entity.get("polygon_vertices")

        if ptype == "circle":
            radius = self._extract_numeric(primitive.get("radius"))
            if radius is None:
                diameter = self._extract_numeric(primitive.get("diameter"))
                radius = (diameter or 0.0) / 2.0
            return CircleNode(
                center_x=cx,
                center_y=cy,
                radius=radius,
            )

        if ptype == "rectangle":
            width = self._extract_numeric(primitive.get("width")) or 0.0
            height = self._extract_numeric(primitive.get("height")) or 0.0
            # placement is the centre; convert to bottom-left corner
            x = self._extract_numeric(primitive.get("x"))
            y = self._extract_numeric(primitive.get("y"))
            if x is None:
                x = cx - width / 2.0
            if y is None:
                y = cy - height / 2.0
            return RectangleNode(
                x=x,
                y=y,
                width=width,
                height=height,
            )

        if ptype == "triangle":
            verts = self._extract_vertices(polygon_vertices, primitive, entity)
            if verts is None or len(verts) != 3:
                raise ValueError(
                    f"TriangleNode requires exactly 3 vertices, got "
                    f"{len(verts) if verts else 0}"
                )
            return TriangleNode(vertices=verts)

        if ptype == "cosine_bell":
            amplitude = self._extract_numeric(primitive.get("amplitude")) or 0.0
            width = self._extract_numeric(primitive.get("width")) or 0.0
            return CosineBellNode(
                x=cx,
                y=cy,
                amplitude=amplitude,
                width=width,
            )

        if ptype == "polygon":
            verts = self._extract_vertices(polygon_vertices, primitive, entity)
            if verts is None or len(verts) < 3:
                raise ValueError(
                    f"PolygonNode requires at least 3 vertices, got "
                    f"{len(verts) if verts else 0}"
                )
            return PolygonNode(vertices=verts)

        if ptype == "imported":
            file_path = primitive.get("file_path", entity.get("file_path", ""))
            fmt = primitive.get("format", entity.get("format", "stl"))
            return ImportedNode(
                file_path=str(file_path),
                format=str(fmt),
            )

        # Should not reach here due to _resolve_primitive_type raising,
        # but guard explicitly.
        raise ValueError(f"Unknown geometry type: {ptype}")

    @staticmethod
    def _extract_vertices(
        polygon_vertices: Any,
        primitive: dict[str, Any],
        entity: dict[str, Any],
    ) -> list[dict[str, float]] | None:
        """Extract a list of vertex dicts from various locations."""
        for source in (polygon_vertices, primitive.get("vertices"), entity.get("vertices")):
            if source and isinstance(source, list) and len(source) > 0:
                return [
                    {"x": float(v["x"]), "y": float(v["y"])}
                    for v in source
                ]
        return None

    # ------------------------------------------------------------------
    # Build from full geometry definition
    # ------------------------------------------------------------------

    def build_from_spec(self, geometry_def: dict[str, Any]) -> GeometryAST:
        """Build a full :class:`GeometryAST` from a ``GeometryDefinition`` dict.

        Args:
            geometry_def: A serialised ``GeometryDefinition`` dict with
                ``domain``, ``entities``, and ``relations`` keys.

        Returns:
            A :class:`GeometryAST` whose root is the union of all entity
            nodes (or a single entity if there is only one).
        """
        entities = geometry_def.get("entities", {})
        domain = geometry_def.get("domain", {})

        nodes: list[GeometryNode] = []
        for entity_id, entity_dict in entities.items():
            # Ensure entity_id is present in the dict.
            if "entity_id" not in entity_dict:
                entity_dict = {**entity_dict, "entity_id": entity_id}
            node = self.build_from_entity(entity_dict)
            nodes.append(node)

        if not nodes:
            raise ValueError("GeometryDefinition has no entities")
        if len(nodes) == 1:
            root: GeometryNode = nodes[0]
        else:
            root = nodes[0]
            for node in nodes[1:]:
                root = BooleanNode(op="union", left=root, right=node)

        return GeometryAST(
            root=root,
            domain=domain,
            metadata={
                "entity_count": len(nodes),
                "entity_ids": list(entities.keys()),
            },
        )

    # ------------------------------------------------------------------
    # Bounding-box evaluation
    # ------------------------------------------------------------------

    def evaluate_bbox(self, node: GeometryNode) -> dict[str, float]:
        """Compute the bounding box of a geometry node.

        Returns:
            A dict with ``min_x``, ``min_y``, ``max_x``, ``max_y`` keys.

        Raises:
            ValueError: For :class:`ImportedNode` or unknown node types.
        """
        if isinstance(node, CircleNode):
            return {
                "min_x": node.center_x - node.radius,
                "min_y": node.center_y - node.radius,
                "max_x": node.center_x + node.radius,
                "max_y": node.center_y + node.radius,
            }

        if isinstance(node, RectangleNode):
            return {
                "min_x": node.x,
                "min_y": node.y,
                "max_x": node.x + node.width,
                "max_y": node.y + node.height,
            }

        if isinstance(node, TriangleNode | PolygonNode):
            xs = [v["x"] for v in node.vertices]
            ys = [v["y"] for v in node.vertices]
            return {
                "min_x": min(xs),
                "min_y": min(ys),
                "max_x": max(xs),
                "max_y": max(ys),
            }

        if isinstance(node, CosineBellNode):
            half = node.width / 2.0
            return {
                "min_x": node.x - half,
                "min_y": node.y,
                "max_x": node.x + half,
                "max_y": node.y + node.amplitude,
            }

        if isinstance(node, ImportedNode):
            raise ValueError(
                "Cannot compute bounding box for ImportedNode (geometry unknown)"
            )

        if isinstance(node, BooleanNode):
            left = self.evaluate_bbox(node.left)
            right = self.evaluate_bbox(node.right)
            if node.op == "union":
                return {
                    "min_x": min(left["min_x"], right["min_x"]),
                    "min_y": min(left["min_y"], right["min_y"]),
                    "max_x": max(left["max_x"], right["max_x"]),
                    "max_y": max(left["max_y"], right["max_y"]),
                }
            if node.op == "difference":
                return left
            if node.op == "intersection":
                return {
                    "min_x": max(left["min_x"], right["min_x"]),
                    "min_y": max(left["min_y"], right["min_y"]),
                    "max_x": min(left["max_x"], right["max_x"]),
                    "max_y": min(left["max_y"], right["max_y"]),
                }

        if isinstance(node, TransformNode):
            target = self.evaluate_bbox(node.target)
            if node.op == "translate":
                dx = float(node.parameters.get("dx", 0.0))
                dy = float(node.parameters.get("dy", 0.0))
                return {
                    "min_x": target["min_x"] + dx,
                    "min_y": target["min_y"] + dy,
                    "max_x": target["max_x"] + dx,
                    "max_y": target["max_y"] + dy,
                }
            if node.op == "rotate":
                angle = float(node.parameters.get("angle", 0.0))
                cos_a = math.cos(angle)
                sin_a = math.sin(angle)
                corners = [
                    (target["min_x"], target["min_y"]),
                    (target["max_x"], target["min_y"]),
                    (target["max_x"], target["max_y"]),
                    (target["min_x"], target["max_y"]),
                ]
                rotated = [
                    (x * cos_a - y * sin_a, x * sin_a + y * cos_a)
                    for x, y in corners
                ]
                xs = [p[0] for p in rotated]
                ys = [p[1] for p in rotated]
                return {
                    "min_x": min(xs),
                    "min_y": min(ys),
                    "max_x": max(xs),
                    "max_y": max(ys),
                }
            if node.op == "scale":
                sx = float(node.parameters.get("sx", 1.0))
                sy = float(node.parameters.get("sy", 1.0))
                return {
                    "min_x": target["min_x"] * sx,
                    "min_y": target["min_y"] * sy,
                    "max_x": target["max_x"] * sx,
                    "max_y": target["max_y"] * sy,
                }

        raise ValueError(f"Unknown node type: {node.node_type}")

    # ------------------------------------------------------------------
    # Area evaluation
    # ------------------------------------------------------------------

    def evaluate_area(self, node: GeometryNode) -> float:
        """Compute the area of a geometry node.

        Raises:
            ValueError: For :class:`ImportedNode` or unknown node types.
        """
        if isinstance(node, CircleNode):
            return math.pi * node.radius ** 2

        if isinstance(node, RectangleNode):
            return abs(node.width * node.height)

        if isinstance(node, TriangleNode | PolygonNode):
            return self._shoelace_area(node.vertices)

        if isinstance(node, CosineBellNode):
            # integral of amplitude * cos^2(pi*(x-cx)/width) over [-w/2, w/2]
            # = amplitude * width / 2
            return abs(node.amplitude * node.width / 2.0)

        if isinstance(node, ImportedNode):
            raise ValueError(
                "Cannot compute area for ImportedNode (geometry unknown)"
            )

        if isinstance(node, BooleanNode):
            left_area = self.evaluate_area(node.left)
            right_area = self.evaluate_area(node.right)
            if node.op == "union":
                # Upper-bound approximation (true area requires clipping).
                return left_area + right_area
            if node.op == "difference":
                # Lower-bound approximation.
                return max(left_area - right_area, 0.0)
            if node.op == "intersection":
                return min(left_area, right_area)

        if isinstance(node, TransformNode):
            target_area = self.evaluate_area(node.target)
            if node.op == "translate":
                return target_area
            if node.op == "rotate":
                return target_area
            if node.op == "scale":
                sx = abs(float(node.parameters.get("sx", 1.0)))
                sy = abs(float(node.parameters.get("sy", 1.0)))
                return target_area * sx * sy

        raise ValueError(f"Unknown node type: {node.node_type}")

    @staticmethod
    def _shoelace_area(vertices: list[dict[str, float]]) -> float:
        """Compute polygon area using the shoelace formula."""
        n = len(vertices)
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += vertices[i]["x"] * vertices[j]["y"]
            area -= vertices[j]["x"] * vertices[i]["y"]
        return abs(area) / 2.0

    # ------------------------------------------------------------------
    # STL segment conversion
    # ------------------------------------------------------------------

    def to_stl_segments(self, node: GeometryNode) -> list[dict[str, Any]]:
        """Convert a geometry node to a list of line segments.

        Each segment is a dict with ``start`` and ``end`` keys, each
        containing ``{"x": float, "y": float}``.

        For :class:`ImportedNode`, an empty list is returned (the
        geometry must be read from the external file).
        """
        if isinstance(node, CircleNode):
            return self._circle_segments(
                node.center_x, node.center_y, node.radius, n=32
            )

        if isinstance(node, RectangleNode):
            corners = [
                (node.x, node.y),
                (node.x + node.width, node.y),
                (node.x + node.width, node.y + node.height),
                (node.x, node.y + node.height),
            ]
            return self._polygon_segments(corners)

        if isinstance(node, TriangleNode | PolygonNode):
            corners = [(v["x"], v["y"]) for v in node.vertices]
            return self._polygon_segments(corners)

        if isinstance(node, CosineBellNode):
            return self._cosine_bell_segments(
                node.x, node.y, node.amplitude, node.width, n=16
            )

        if isinstance(node, ImportedNode):
            return []

        if isinstance(node, BooleanNode):
            return self.to_stl_segments(node.left) + self.to_stl_segments(
                node.right
            )

        if isinstance(node, TransformNode):
            segments = self.to_stl_segments(node.target)
            if node.op == "translate":
                dx = float(node.parameters.get("dx", 0.0))
                dy = float(node.parameters.get("dy", 0.0))
                for seg in segments:
                    seg["start"]["x"] += dx
                    seg["start"]["y"] += dy
                    seg["end"]["x"] += dx
                    seg["end"]["y"] += dy
                return segments
            if node.op == "rotate":
                angle = float(node.parameters.get("angle", 0.0))
                cos_a = math.cos(angle)
                sin_a = math.sin(angle)
                for seg in segments:
                    x1, y1 = seg["start"]["x"], seg["start"]["y"]
                    x2, y2 = seg["end"]["x"], seg["end"]["y"]
                    seg["start"]["x"] = x1 * cos_a - y1 * sin_a
                    seg["start"]["y"] = x1 * sin_a + y1 * cos_a
                    seg["end"]["x"] = x2 * cos_a - y2 * sin_a
                    seg["end"]["y"] = x2 * sin_a + y2 * cos_a
                return segments
            if node.op == "scale":
                sx = float(node.parameters.get("sx", 1.0))
                sy = float(node.parameters.get("sy", 1.0))
                for seg in segments:
                    seg["start"]["x"] *= sx
                    seg["start"]["y"] *= sy
                    seg["end"]["x"] *= sx
                    seg["end"]["y"] *= sy
                return segments

        raise ValueError(f"Unknown node type: {node.node_type}")

    @staticmethod
    def _circle_segments(
        cx: float, cy: float, radius: float, n: int = 32
    ) -> list[dict[str, Any]]:
        """Approximate a circle with *n* line segments."""
        segments: list[dict[str, Any]] = []
        for i in range(n):
            a1 = 2.0 * math.pi * i / n
            a2 = 2.0 * math.pi * (i + 1) / n
            segments.append(
                {
                    "start": {
                        "x": cx + radius * math.cos(a1),
                        "y": cy + radius * math.sin(a1),
                    },
                    "end": {
                        "x": cx + radius * math.cos(a2),
                        "y": cy + radius * math.sin(a2),
                    },
                }
            )
        return segments

    @staticmethod
    def _polygon_segments(
        corners: list[tuple[float, float]],
    ) -> list[dict[str, Any]]:
        """Build closed line segments from a list of corner points."""
        n = len(corners)
        segments: list[dict[str, Any]] = []
        for i in range(n):
            j = (i + 1) % n
            segments.append(
                {
                    "start": {"x": corners[i][0], "y": corners[i][1]},
                    "end": {"x": corners[j][0], "y": corners[j][1]},
                }
            )
        return segments

    @staticmethod
    def _cosine_bell_segments(
        x: float,
        y: float,
        amplitude: float,
        width: float,
        n: int = 16,
    ) -> list[dict[str, Any]]:
        """Approximate a cosine-bell profile with *n* line segments."""
        segments: list[dict[str, Any]] = []
        half = width / 2.0
        prev: dict[str, float] | None = None
        for i in range(n + 1):
            t = i / n  # 0 .. 1
            px = x - half + width * t
            # cos^2(pi*(t-0.5)): at t=0.5 -> 1 (peak), at t=0,1 -> 0
            py = y + amplitude * (math.cos(math.pi * (t - 0.5)) ** 2)
            point: dict[str, float] = {"x": px, "y": py}
            if prev is not None:
                segments.append({"start": prev, "end": point})
            prev = point
        return segments
