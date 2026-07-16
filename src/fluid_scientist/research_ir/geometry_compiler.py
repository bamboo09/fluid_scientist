"""Geometry Compiler -- compiles :class:`GeometryEntity` objects from the
Research IR into ``blockMeshDict`` entries or OpenFOAM geometry definitions.

The compiler is the bridge between the *open-world* semantic representation
(``GeometryRepresentation``) and the *concrete* numerical geometry that
OpenFOAM consumes.  It handles **all** representation types declared on
:class:`GeometryRepresentation`:

================== ==========================================================
Representation     Strategy
================== ==========================================================
``circle``         Polygon approximation (octagon / N-gon) of the circle.
``explicit_polygon`` Direct vertex computation from the definition.  Supports
                   trapezoid, triangle and rectangle sub-shapes, as well as
                   explicitly provided vertex lists.
``profile_function`` Discrete sampling of cosine / half-sine / gaussian
                   profiles, closed into a polygon along the baseline.
``ellipse``        Polygon approximation from the two semi-axes.
``unknown``        Placeholder result with ``status="needs_clarification"``.
================== ==========================================================

Every :meth:`PolygonGeometryCompiler.compile_entity` call returns a plain
``dict`` with the keys ``entity_id``, ``raw_name``, ``representation_type``,
``vertices``, ``n_vertices``, ``blockmesh_entry``, ``stl_entry`` and
``status``.  On success ``status`` is ``"compiled"``; missing parameters
produce ``"compile_error"`` and an unresolvable representation produces
``"needs_clarification"``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import Any

from fluid_scientist.research_ir.models import (
    GeometryEntity,
    GeometryRepresentation,
    OpenWorldResearchIR,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

#: Default number of segments when approximating a circle with a polygon.
DEFAULT_CIRCLE_SEGMENTS: int = 16

#: Number of segments used for the blockMesh octagon approximation of a circle.
DEFAULT_BLOCKMESH_CIRCLE_SEGMENTS: int = 8

#: Default number of segments when approximating an ellipse with a polygon.
DEFAULT_ELLIPSE_SEGMENTS: int = 32

#: Default number of sample points for profile functions.
DEFAULT_PROFILE_POINTS: int = 32

#: Default (nx, ny, nz) cell counts for generated blockMesh blocks.
DEFAULT_BLOCK_CELLS: tuple[int, int, int] = (50, 50, 1)

#: Default extrusion depth (metres) used when generating STL solids for 2D
#: geometries.
DEFAULT_STL_DEPTH: float = 1.0

#: Tolerance (metres) for vertex de-duplication.
_VERTEX_TOL: float = 1e-9


# ---------------------------------------------------------------------------
# Vertex computation functions
# ---------------------------------------------------------------------------


def _trapezoid_vertices(
    top_width: float,
    bottom_width: float,
    height: float,
    center_x: float,
) -> list[list[float]]:
    """Compute 4 vertices of a trapezoid sitting on the bottom wall.

    The vertices are returned counter-clockwise starting from the
    bottom-left corner::

        bottom-left -> bottom-right -> top-right -> top-left

    The trapezoid rests on the line ``y = 0`` and rises to ``y = height``.
    ``center_x`` is the horizontal centre of the (symmetric) trapezoid.
    """
    return [
        [center_x - bottom_width / 2, 0.0, 0.0],
        [center_x + bottom_width / 2, 0.0, 0.0],
        [center_x + top_width / 2, height, 0.0],
        [center_x - top_width / 2, height, 0.0],
    ]


def _triangle_vertices(
    base_width: float,
    height: float,
    center_x: float,
) -> list[list[float]]:
    """Compute 3 vertices of a triangle sitting on the bottom wall.

    Vertices are returned counter-clockwise::

        bottom-left -> bottom-right -> apex
    """
    return [
        [center_x - base_width / 2, 0.0, 0.0],
        [center_x + base_width / 2, 0.0, 0.0],
        [center_x, height, 0.0],
    ]


def _rectangle_vertices(
    width: float,
    height: float,
    center_x: float,
    center_y: float,
) -> list[list[float]]:
    """Compute 4 vertices of an axis-aligned rectangle.

    Vertices are returned counter-clockwise starting from the bottom-left
    corner.
    """
    return [
        [center_x - width / 2, center_y - height / 2, 0.0],
        [center_x + width / 2, center_y - height / 2, 0.0],
        [center_x + width / 2, center_y + height / 2, 0.0],
        [center_x - width / 2, center_y + height / 2, 0.0],
    ]


def _circle_to_polygon(
    cx: float,
    cy: float,
    r: float,
    n_segments: int = DEFAULT_CIRCLE_SEGMENTS,
) -> list[list[float]]:
    """Approximate a circle with a regular polygon.

    The polygon vertices are sampled counter-clockwise starting from the
    point ``(cx + r, cy)``.
    """
    return [
        [
            cx + r * math.cos(2.0 * math.pi * i / n_segments),
            cy + r * math.sin(2.0 * math.pi * i / n_segments),
            0.0,
        ]
        for i in range(n_segments)
    ]


def _ellipse_to_polygon(
    cx: float,
    cy: float,
    semi_axis_a: float,
    semi_axis_b: float,
    n_segments: int = DEFAULT_ELLIPSE_SEGMENTS,
) -> list[list[float]]:
    """Approximate an ellipse with a polygon.

    ``semi_axis_a`` is the semi-axis along *x* and ``semi_axis_b`` along *y*.
    """
    return [
        [
            cx + semi_axis_a * math.cos(2.0 * math.pi * i / n_segments),
            cy + semi_axis_b * math.sin(2.0 * math.pi * i / n_segments),
            0.0,
        ]
        for i in range(n_segments)
    ]


def _profile_to_polygon(
    profile_type: str,
    center_x: float,
    width: float,
    height: float,
    n_points: int = DEFAULT_PROFILE_POINTS,
) -> list[list[float]]:
    """Generate polygon points for a profile function.

    Supported ``profile_type`` values are ``"cosine"`` (cosine bell),
    ``"half_sine"`` and ``"gaussian"``.  The profile is sampled left-to-right
    across ``[center_x - width/2, center_x + width/2]`` and rises from the
    baseline ``y = 0`` up to ``y = height``.

    The returned list contains ``n_points + 1`` open points along the top
    curve.  Use :func:`_close_profile_polygon` to obtain a closed polygon.
    """
    points: list[list[float]] = []
    for i in range(n_points + 1):
        x = center_x - width / 2 + width * i / n_points
        local_x = (x - center_x) / (width / 2)  # ranges from -1 to 1
        if profile_type == "cosine":
            y = height * (1.0 + math.cos(math.pi * local_x)) / 2.0
        elif profile_type == "half_sine":
            y = height * math.sin(math.pi * (local_x + 1.0) / 2.0)
        elif profile_type == "gaussian":
            y = height * math.exp(-2.0 * local_x * local_x)
        else:
            y = 0.0
        points.append([x, y, 0.0])
    return points


def _close_profile_polygon(points: list[list[float]]) -> list[list[float]]:
    """Close an open profile curve into a polygon along the ``y = 0`` baseline.

    The input ``points`` are assumed to run left-to-right along the top of
    the profile.  Baseline corners are only appended when the corresponding
    endpoint does not already sit on the baseline, avoiding degenerate
    duplicate vertices.
    """
    if not points:
        return []
    closed = list(points)
    right = points[-1]
    left = points[0]
    if abs(right[1]) > _VERTEX_TOL:
        closed.append([right[0], 0.0, 0.0])
    if abs(left[1]) > _VERTEX_TOL:
        closed.append([left[0], 0.0, 0.0])
    return closed


# ---------------------------------------------------------------------------
# Value / parameter extraction helpers
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    """Coerce *value* to ``float``.

    Handles raw numbers, numeric strings, ``ParameterValue``-like objects
    (anything exposing a ``.value`` attribute) and serialized dicts of the
    form ``{"value": ...}``.  Returns ``None`` when the value cannot be
    interpreted as a float.
    """
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    elif isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_param(
    entity: GeometryEntity,
    definition: dict[str, Any] | None,
    key: str,
    default: float | None = None,
) -> float | None:
    """Resolve a numeric parameter for *entity*.

    The representation ``definition`` (pre-filled by the
    :class:`RepresentationPlanner`) is consulted first; if the key is absent
    or ``None`` there, ``entity.parameters`` is used as a fallback.  When
    neither source yields a value, *default* is returned.
    """
    if definition:
        raw = definition.get(key)
        if raw is not None:
            val = _to_float(raw)
            if val is not None:
                return val
    params = getattr(entity, "parameters", None)
    if params and key in params:
        val = _to_float(params[key])
        if val is not None:
            return val
    return default


def _require_param(
    entity: GeometryEntity,
    definition: dict[str, Any] | None,
    key: str,
) -> float:
    """Resolve a *required* parameter or raise :class:`ValueError`.

    Positional parameters (``center_x``/``center_y``) should use
    :func:`_resolve_param` with a default instead.
    """
    val = _resolve_param(entity, definition, key)
    if val is None:
        raise ValueError(f"missing required parameter '{key}' for entity '{entity.entity_id}'")
    return val


def _domain_value(domain: dict[str, Any] | None, *keys: str) -> float | None:
    """Return the first available numeric value for *keys* in *domain*."""
    if not domain:
        return None
    for key in keys:
        if key in domain:
            val = _to_float(domain[key])
            if val is not None:
                return val
    return None


def _domain_bounds(
    domain: dict[str, Any] | None,
) -> tuple[float, float, float, float, float, float]:
    """Compute ``(xmin, xmax, ymin, ymax, zmin, zmax)`` from a domain dict.

    The domain is assumed to start at the origin.  ``length`` maps to the
    *x* extent, ``width`` (a.k.a. 2-D height) to *y*, and ``height``/depth
    to *z*.  Sensible defaults are used when dimensions are missing.
    """
    length = _domain_value(
        domain, "length", "x_length", "x", "domain_length", "x_extent"
    )
    width = _domain_value(
        domain, "width", "y_length", "y", "domain_width", "height_2d"
    )
    height = _domain_value(
        domain, "height", "z_length", "z", "depth", "domain_depth"
    )

    if length is None or length <= 0.0:
        length = 10.0
    if width is None or width <= 0.0:
        width = 5.0
    if height is None or height <= 0.0:
        # Thin extrusion for a 2-D case.
        height = max(width * 0.1, 0.1)

    return (0.0, length, 0.0, width, 0.0, height)


# ---------------------------------------------------------------------------
# Formatting helpers (blockMesh / STL)
# ---------------------------------------------------------------------------


def _format_blockmesh_entry(
    entity_id: str,
    raw_name: str,
    rep_type: str,
    vertices: list[list[float]],
) -> str:
    """Render a human-readable ``blockMeshDict`` comment entry."""
    header = f"// entity {entity_id}: {raw_name or '(unnamed)'} ({rep_type})"
    if not vertices:
        return header + "\n//   (no vertices)"
    coord_lines = [
        f"//   v{i}: ({v[0]:.6f}, {v[1]:.6f}, {v[2]:.6f})"
        for i, v in enumerate(vertices)
    ]
    return "\n".join([header, *coord_lines])


def _stl_triangle(
    a: list[float],
    b: list[float],
    c: list[float],
) -> str:
    """Render a single ASCII STL facet (triangle) with a computed normal."""
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length > _VERTEX_TOL:
        nx /= length
        ny /= length
        nz /= length
    else:
        nx = ny = nz = 0.0

    def _v(p: list[float]) -> str:
        return f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f}"

    return (
        f"  facet normal {nx:.6f} {ny:.6f} {nz:.6f}\n"
        f"    outer loop\n"
        f"      vertex {_v(a)}\n"
        f"      vertex {_v(b)}\n"
        f"      vertex {_v(c)}\n"
        f"    endloop\n"
        f"  endfacet"
    )


def _polygon_to_stl(
    vertices: list[list[float]],
    name: str,
    depth: float = DEFAULT_STL_DEPTH,
) -> str:
    """Generate an ASCII STL solid by extruding a 2-D polygon along *z*.

    The polygon (assumed planar at ``z = 0``) is extruded to ``z = depth``.
    The resulting closed surface comprises the front cap, back cap and one
    side quad (two triangles) per polygon edge.
    """
    n = len(vertices)
    if n < 3:
        return ""

    front = [[v[0], v[1], 0.0] for v in vertices]
    back = [[v[0], v[1], depth] for v in vertices]

    lines: list[str] = [f"solid {name}"]

    # Front cap (z = 0) -- fan triangulation, outward normal -z.
    for i in range(1, n - 1):
        lines.append(_stl_triangle(front[0], front[i + 1], front[i]))

    # Back cap (z = depth) -- fan triangulation, outward normal +z.
    for i in range(1, n - 1):
        lines.append(_stl_triangle(back[0], back[i], back[i + 1]))

    # Side walls -- one quad (two triangles) per edge.
    for i in range(n):
        j = (i + 1) % n
        lines.append(_stl_triangle(front[i], front[j], back[j]))
        lines.append(_stl_triangle(front[i], back[j], back[i]))

    lines.append(f"endsolid {name}")
    return "\n".join(lines)


def _dedup_vertices(
    vertices: Iterable[list[float]],
    tol: float = _VERTEX_TOL,
) -> list[list[float]]:
    """Return *vertices* with near-duplicate points removed (order preserved)."""
    unique: list[list[float]] = []
    for v in vertices:
        if not any(
            abs(v[0] - u[0]) < tol
            and abs(v[1] - u[1]) < tol
            and abs(v[2] - u[2]) < tol
            for u in unique
        ):
            unique.append([float(v[0]), float(v[1]), float(v[2])])
    return unique


# ---------------------------------------------------------------------------
# Main compiler class
# ---------------------------------------------------------------------------


class PolygonGeometryCompiler:
    """Compile :class:`GeometryEntity` objects into OpenFOAM geometry.

    Parameters
    ----------
    circle_segments:
        Number of polygon segments used when approximating circles.
    blockmesh_circle_segments:
        Number of segments used for the blockMesh (octagon-style) circle
        approximation.  Defaults to ``8``.
    ellipse_segments:
        Number of polygon segments used when approximating ellipses.
    profile_points:
        Number of sample points used for profile functions.
    default_cells:
        ``(nx, ny, nz)`` cell counts for generated blockMesh blocks.
    stl_depth:
        Default extrusion depth (metres) for generated STL solids.
    """

    def __init__(
        self,
        circle_segments: int = DEFAULT_CIRCLE_SEGMENTS,
        blockmesh_circle_segments: int = DEFAULT_BLOCKMESH_CIRCLE_SEGMENTS,
        ellipse_segments: int = DEFAULT_ELLIPSE_SEGMENTS,
        profile_points: int = DEFAULT_PROFILE_POINTS,
        default_cells: tuple[int, int, int] = DEFAULT_BLOCK_CELLS,
        stl_depth: float = DEFAULT_STL_DEPTH,
    ) -> None:
        self.circle_segments = circle_segments
        self.blockmesh_circle_segments = blockmesh_circle_segments
        self.ellipse_segments = ellipse_segments
        self.profile_points = profile_points
        self.default_cells = default_cells
        self.stl_depth = stl_depth

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile_entity(
        self,
        entity: GeometryEntity,
        domain: dict[str, Any],
    ) -> dict[str, Any]:
        """Compile a single :class:`GeometryEntity` to a geometry definition.

        The returned dict always contains the keys ``entity_id``,
        ``raw_name``, ``representation_type``, ``vertices``,
        ``n_vertices``, ``blockmesh_entry``, ``stl_entry`` and ``status``.
        """
        rep: GeometryRepresentation = entity.representation
        rep_type = rep.type if rep is not None else "unknown"
        definition = rep.definition if rep is not None else {}

        try:
            if rep_type == "circle":
                vertices = self._compile_circle(entity, definition)
            elif rep_type == "explicit_polygon":
                vertices = self._compile_explicit_polygon(entity, definition)
            elif rep_type == "profile_function":
                vertices = self._compile_profile_function(entity, definition)
            elif rep_type == "ellipse":
                vertices = self._compile_ellipse(entity, definition)
            elif rep_type == "unknown":
                return self._placeholder(entity, rep_type)
            else:
                # csg / imported_mesh / implicit_surface / parametric_polygon
                logger.info(
                    "Representation type '%s' for entity '%s' is not directly "
                    "compilable; returning placeholder.",
                    rep_type,
                    entity.entity_id,
                )
                return self._placeholder(entity, rep_type)
        except ValueError as exc:
            logger.warning(
                "Compile error for entity '%s' (%s): %s",
                entity.entity_id,
                rep_type,
                exc,
            )
            return self._error(entity, rep_type, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Unexpected error compiling entity '%s': %s",
                entity.entity_id,
                exc,
            )
            return self._error(entity, rep_type, f"unexpected error: {exc}")

        vertices = [[float(c) for c in v] for v in vertices]
        blockmesh_entry = _format_blockmesh_entry(
            entity.entity_id, entity.raw_name, rep_type, vertices
        )
        stl_entry = _polygon_to_stl(
            vertices, entity.entity_id, depth=self.stl_depth
        )

        logger.debug(
            "Compiled entity '%s' (%s): %d vertices",
            entity.entity_id,
            rep_type,
            len(vertices),
        )
        return {
            "entity_id": entity.entity_id,
            "raw_name": entity.raw_name,
            "representation_type": rep_type,
            "vertices": vertices,
            "n_vertices": len(vertices),
            "blockmesh_entry": blockmesh_entry,
            "stl_entry": stl_entry,
            "status": "compiled",
        }

    def compile_all(
        self,
        ir: OpenWorldResearchIR,
    ) -> list[dict[str, Any]]:
        """Compile every geometry entity in *ir*.

        The IR's ``domain`` is serialized to a plain dict and passed to
        :meth:`compile_entity` for each entity.
        """
        domain = self._domain_to_dict(ir.domain)
        results: list[dict[str, Any]] = []
        for entity in ir.geometry_entities:
            results.append(self.compile_entity(entity, domain))
        return results

    def to_blockmesh_vertices(
        self,
        entities: list[dict[str, Any]],
        domain: dict[str, Any],
    ) -> list[list[float]]:
        """Generate the ``blockMeshDict`` vertex list.

        The list always starts with the 8 corner vertices of the domain
        bounding box (front and back faces), followed by the vertices of
        every successfully compiled entity.  Near-duplicate vertices are
        removed.
        """
        xmin, xmax, ymin, ymax, zmin, zmax = _domain_bounds(domain)
        corners = [
            [xmin, ymin, zmin],
            [xmax, ymin, zmin],
            [xmax, ymax, zmin],
            [xmin, ymax, zmin],
            [xmin, ymin, zmax],
            [xmax, ymin, zmax],
            [xmax, ymax, zmax],
            [xmin, ymax, zmax],
        ]
        all_vertices: list[list[float]] = list(corners)
        for ent in entities:
            if ent.get("status") != "compiled":
                continue
            for v in ent.get("vertices", []):
                all_vertices.append(v)
        return _dedup_vertices(all_vertices)

    def to_blockmesh_blocks(
        self,
        entities: list[dict[str, Any]],
        domain: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate the ``blockMeshDict`` block definitions.

        A primary hex block covering the full domain is always produced.
        Wall-attached obstacle entities (trapezoids, triangles, profiles)
        that sit on the bottom wall additionally yield refined sub-blocks
        whose vertex indices reference the de-duplicated vertex list
        produced by :meth:`to_blockmesh_vertices`.
        """
        xmin, xmax, ymin, ymax, zmin, zmax = _domain_bounds(domain)
        length = xmax - xmin
        width = ymax - ymin
        depth = zmax - zmin

        nx, ny, nz = self.default_cells
        blocks: list[dict[str, Any]] = [
            {
                "label": "domain",
                "hex": [0, 1, 2, 3, 4, 5, 6, 7],
                "n_cells": [nx, ny, max(nz, 1)],
                "grading": [1.0, 1.0, 1.0],
            }
        ]

        # Optional refinement blocks for wall-attached obstacles.
        vertex_index = self._build_vertex_index(entities, domain)
        for ent in entities:
            if ent.get("status") != "compiled":
                continue
            role = ent.get("role", "")
            if role not in ("wall_attached_obstacle", "immersed_obstacle"):
                continue
            verts = ent.get("vertices", [])
            if len(verts) < 3:
                continue
            indices = vertex_index.get(ent["entity_id"], [])
            if len(indices) < 3:
                continue
            # Build a hex from the polygon footprint extruded in z.
            footprint = indices[:4] if len(indices) >= 4 else indices
            front = footprint
            back = [i + 1 for i in front]  # placeholder offset; see note below
            blocks.append(
                {
                    "label": f"{ent['entity_id']}_obstacle",
                    "hex": front + back,
                    "n_cells": [
                        max(nx // 5, 4),
                        max(ny // 5, 4),
                        max(nz, 1),
                    ],
                    "grading": [1.0, 1.0, 1.0],
                    "footprint_vertices": footprint,
                    "length": length,
                    "width": width,
                    "depth": depth,
                }
            )
        return blocks

    # ------------------------------------------------------------------
    # Representation-type handlers
    # ------------------------------------------------------------------

    def _compile_circle(
        self,
        entity: GeometryEntity,
        definition: dict[str, Any],
    ) -> list[list[float]]:
        """Compile a ``circle`` representation.

        ``center_x``, ``center_y`` and ``radius`` are resolved from the
        definition (falling back to ``entity.parameters``).  ``center_x``
        and ``center_y`` default to ``0.0`` when not specified.
        """
        cx = _resolve_param(entity, definition, "center_x", default=0.0)
        cy = _resolve_param(entity, definition, "center_y", default=0.0)
        radius = _require_param(entity, definition, "radius")
        if radius <= 0.0:
            raise ValueError(
                f"circle radius must be positive for entity '{entity.entity_id}'"
            )
        return _circle_to_polygon(cx, cy, radius, self.circle_segments)

    def _compile_explicit_polygon(
        self,
        entity: GeometryEntity,
        definition: dict[str, Any],
    ) -> list[list[float]]:
        """Compile an ``explicit_polygon`` representation.

        Dispatches on ``representation.subtype`` (with ``semantic_shape`` as
        a fallback) to the trapezoid, triangle or rectangle vertex
        computations.  An explicit ``"vertices"`` entry in the definition is
        used directly when present.
        """
        # 1. Explicitly provided vertices take priority.
        explicit = definition.get("vertices")
        if explicit:
            verts = self._coerce_vertex_list(explicit)
            if len(verts) >= 3:
                return verts
            logger.warning(
                "Explicit vertices for entity '%s' have fewer than 3 points; "
                "falling back to shape-based computation.",
                entity.entity_id,
            )

        subtype = (entity.representation.subtype or "").lower()
        semantic = (entity.semantic_shape or "").lower()

        # 2. Trapezoid.
        if subtype == "four_vertex" or semantic == "trapezoid":
            top_width = _require_param(entity, definition, "top_width")
            bottom_width = _require_param(entity, definition, "bottom_width")
            height = _require_param(entity, definition, "height")
            center_x = _resolve_param(
                entity, definition, "center_x", default=0.0
            )
            if height <= 0.0:
                raise ValueError(
                    f"trapezoid height must be positive for entity "
                    f"'{entity.entity_id}'"
                )
            return _trapezoid_vertices(
                top_width, bottom_width, height, center_x
            )

        # 3. Triangle.
        if subtype == "three_vertex" or semantic == "triangle":
            base_width = _require_param(entity, definition, "base_width")
            height = _require_param(entity, definition, "height")
            center_x = _resolve_param(
                entity, definition, "center_x", default=0.0
            )
            if height <= 0.0:
                raise ValueError(
                    f"triangle height must be positive for entity "
                    f"'{entity.entity_id}'"
                )
            return _triangle_vertices(base_width, height, center_x)

        # 4. Rectangle (axis-aligned).
        if subtype == "axis_aligned" or semantic in ("rectangle", "rect"):
            width = _require_param(entity, definition, "width")
            height = _require_param(entity, definition, "height")
            center_x = _resolve_param(
                entity, definition, "center_x", default=0.0
            )
            center_y = _resolve_param(
                entity, definition, "center_y", default=0.0
            )
            if width <= 0.0 or height <= 0.0:
                raise ValueError(
                    f"rectangle width/height must be positive for entity "
                    f"'{entity.entity_id}'"
                )
            return _rectangle_vertices(width, height, center_x, center_y)

        # 5. Generic parametric polygon with explicit vertex count.
        if subtype == "parametric" or semantic in (
            "polygon",
            "pentagon",
            "hexagon",
        ):
            verts = self._coerce_vertex_list(definition.get("vertices"))
            if len(verts) >= 3:
                return verts
            raise ValueError(
                f"explicit_polygon entity '{entity.entity_id}' with "
                f"subtype='{subtype}' / semantic='{semantic}' requires "
                f"explicit 'vertices' in the definition"
            )

        # 6. Unrecognized subtype.
        raise ValueError(
            f"explicit_polygon entity '{entity.entity_id}' has unrecognized "
            f"subtype='{subtype}' / semantic_shape='{semantic}' and no "
            f"explicit vertices"
        )

    def _compile_profile_function(
        self,
        entity: GeometryEntity,
        definition: dict[str, Any],
    ) -> list[list[float]]:
        """Compile a ``profile_function`` representation.

        The ``subtype`` (or ``semantic_shape``) selects the profile type:
        ``cosine`` / ``cosine_bell``, ``half_sine`` and ``gaussian``.  The
        profile is sampled and closed into a polygon along the baseline.
        """
        profile_type = (
            entity.representation.subtype or entity.semantic_shape or ""
        ).lower()
        # Normalise aliases.
        if profile_type in ("cosine_bell", "cosine", "bell"):
            profile_type = "cosine"
        elif profile_type in ("half_sine", "half sine", "sine"):
            profile_type = "half_sine"
        elif profile_type == "gaussian":
            profile_type = "gaussian"

        if profile_type not in ("cosine", "half_sine", "gaussian"):
            raise ValueError(
                f"unsupported profile_function type '{profile_type}' for "
                f"entity '{entity.entity_id}'"
            )

        center_x = _resolve_param(
            entity, definition, "center_x", default=0.0
        )
        width = _require_param(entity, definition, "width")
        height = _require_param(entity, definition, "height")
        if width <= 0.0:
            raise ValueError(
                f"profile width must be positive for entity "
                f"'{entity.entity_id}'"
            )

        points = _profile_to_polygon(
            profile_type,
            center_x,
            width,
            height,
            n_points=self.profile_points,
        )
        return _close_profile_polygon(points)

    def _compile_ellipse(
        self,
        entity: GeometryEntity,
        definition: dict[str, Any],
    ) -> list[list[float]]:
        """Compile an ``ellipse`` representation from its two semi-axes."""
        cx = _resolve_param(entity, definition, "center_x", default=0.0)
        cy = _resolve_param(entity, definition, "center_y", default=0.0)
        semi_a = _require_param(entity, definition, "semi_axis_a")
        semi_b = _require_param(entity, definition, "semi_axis_b")
        if semi_a <= 0.0 or semi_b <= 0.0:
            raise ValueError(
                f"ellipse semi-axes must be positive for entity "
                f"'{entity.entity_id}'"
            )
        return _ellipse_to_polygon(
            cx, cy, semi_a, semi_b, self.ellipse_segments
        )

    # ------------------------------------------------------------------
    # Result builders
    # ------------------------------------------------------------------

    @staticmethod
    def _placeholder(
        entity: GeometryEntity,
        rep_type: str,
    ) -> dict[str, Any]:
        """Build a ``needs_clarification`` placeholder result."""
        return {
            "entity_id": entity.entity_id,
            "raw_name": entity.raw_name,
            "representation_type": rep_type,
            "vertices": [],
            "n_vertices": 0,
            "blockmesh_entry": _format_blockmesh_entry(
                entity.entity_id, entity.raw_name, rep_type, []
            ),
            "stl_entry": "",
            "status": "needs_clarification",
            "message": (
                f"representation type '{rep_type}' for entity "
                f"'{entity.entity_id}' could not be compiled; clarification "
                f"required"
            ),
        }

    @staticmethod
    def _error(
        entity: GeometryEntity,
        rep_type: str,
        message: str,
    ) -> dict[str, Any]:
        """Build a ``compile_error`` result."""
        return {
            "entity_id": entity.entity_id,
            "raw_name": entity.raw_name,
            "representation_type": rep_type,
            "vertices": [],
            "n_vertices": 0,
            "blockmesh_entry": _format_blockmesh_entry(
                entity.entity_id, entity.raw_name, rep_type, []
            ),
            "stl_entry": "",
            "status": "compile_error",
            "message": message,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_vertex_list(raw: Any) -> list[list[float]]:
        """Coerce a raw vertices value into a list of ``[x, y, z]`` floats."""
        if not raw:
            return []
        verts: list[list[float]] = []
        for item in raw:
            coords = list(item)
            x = _to_float(coords[0]) if len(coords) > 0 else 0.0
            y = _to_float(coords[1]) if len(coords) > 1 else 0.0
            z = _to_float(coords[2]) if len(coords) > 2 else 0.0
            if x is None or y is None or z is None:
                continue
            verts.append([x, y, z])
        return verts

    @staticmethod
    def _domain_to_dict(domain: Any) -> dict[str, Any]:
        """Serialize a :class:`DomainIntent` (or dict) to a plain dict."""
        if domain is None:
            return {}
        if isinstance(domain, dict):
            return domain
        result: dict[str, Any] = {}
        for attr in ("length", "width", "height"):
            pv = getattr(domain, attr, None)
            if pv is not None:
                result[attr] = pv
        dim = getattr(domain, "dimensionality", None)
        if dim is not None:
            result["dimensionality"] = dim
        return result

    def _build_vertex_index(
        self,
        entities: list[dict[str, Any]],
        domain: dict[str, Any],
    ) -> dict[str, list[int]]:
        """Map each compiled entity's vertices to indices in the global list.

        The global vertex list is the one produced by
        :meth:`to_blockmesh_vertices`.  Vertex matching uses the same
        tolerance as :func:`_dedup_vertices`.
        """
        global_vertices = self.to_blockmesh_vertices(entities, domain)
        index_map: dict[str, list[int]] = {}

        for ent in entities:
            if ent.get("status") != "compiled":
                continue
            indices: list[int] = []
            for v in ent.get("vertices", []):
                idx = self._find_vertex(global_vertices, v)
                if idx is not None:
                    indices.append(idx)
            index_map[ent["entity_id"]] = indices
        return index_map

    @staticmethod
    def _find_vertex(
        pool: list[list[float]],
        target: list[float],
        tol: float = _VERTEX_TOL,
    ) -> int | None:
        """Return the index of *target* in *pool*, or ``None``."""
        for i, v in enumerate(pool):
            if (
                abs(v[0] - target[0]) < tol
                and abs(v[1] - target[1]) < tol
                and abs(v[2] - target[2]) < tol
            ):
                return i
        return None


__all__ = [
    "PolygonGeometryCompiler",
    "_trapezoid_vertices",
    "_triangle_vertices",
    "_rectangle_vertices",
    "_circle_to_polygon",
    "_ellipse_to_polygon",
    "_profile_to_polygon",
]
