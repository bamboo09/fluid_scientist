"""ObstacleFlowMeshBackend — mesh generation for 2D obstacle flow.

Implements Section 17 of the plan.  Uses blockMesh for the background
mesh and snappyHexMesh for cylinder refinement when a cylinder is present.

For V1, the primary backend is blockMesh + snappyHexMesh:
  - blockMesh creates the rectangular domain with optional bump
  - snappyHexMesh refines around the cylinder and creates the cylinder patch
  - An STL surface is generated programmatically for the cylinder

This approach handles all four geometry combinations:
  - flat + no cylinder
  - flat + cylinder
  - bump + no cylinder
  - bump + cylinder
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fluid_scientist.obstacle_flow.geometry import (
    BumpProfile,
    BumpProfileGenerator,
    CylinderGeometry,
    CylinderGeometryBuilder,
    RectangleGeometry,
    RectangleGeometryBuilder,
    TriangleGeometry,
    TriangleGeometryBuilder,
)
from fluid_scientist.obstacle_flow.models import (
    BoundaryType,
    BumpProfileType,
    BumpSpec,
    CylinderSpec,
    DomainSpec,
    ObstacleFlowExperimentSpecV1,
    TriangleSpec,
)


@dataclass(frozen=True)
class MeshManifest:
    """Manifest of generated mesh files."""

    block_mesh_dict: str
    snappy_hex_mesh_dict: str | None = None
    cylinder_stl: str | None = None
    rectangle_stl: str | None = None
    triangle_stl: str | None = None
    has_cylinder: bool = False
    has_bump: bool = False
    n_blocks: int = 1
    expected_cell_count: int = 0
    mesh_backend: str = "blockMesh+snappyHexMesh"


@dataclass
class MeshParams:
    """Mesh configuration parameters."""

    base_cell_size: float = 1.0
    cylinder_refinement_level: int = 2
    bump_refinement_level: int = 1
    wake_refinement_level: int = 1
    n_layers_x: int = 100
    n_layers_y: int = 30
    n_layers_z: int = 1
    grading_x: float = 1.0
    grading_y: float = 1.0


class ObstacleFlowMeshBackend:
    """Mesh backend for obstacle flow experiments.

    Generates blockMeshDict and optionally snappyHexMeshDict + cylinder STL
    based on the experiment spec.
    """

    def __init__(self) -> None:
        self._bump_gen = BumpProfileGenerator()
        self._cyl_builder = CylinderGeometryBuilder()
        self._rect_builder = RectangleGeometryBuilder()
        self._tri_builder = TriangleGeometryBuilder()

    def generate(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        params: MeshParams | None = None,
    ) -> MeshManifest:
        """Generate mesh files for the given spec."""
        if params is None:
            params = self._auto_params(spec)

        domain = spec.domain
        is_periodic = spec.is_periodic

        # Generate bump profile if needed
        bump_profile: BumpProfile | None = None
        if spec.has_bump:
            bump_profile = self._bump_gen.generate(spec.geometry_bump, domain)

        # Generate blockMeshDict
        block_mesh = self._generate_block_mesh_dict(
            spec, domain, params, bump_profile, is_periodic
        )

        # Generate snappyHexMeshDict and STL files if cylinder, rectangle, or triangle present
        snappy_dict: str | None = None
        cylinder_stl: str | None = None
        rectangle_stl: str | None = None
        triangle_stl: str | None = None
        if spec.has_cylinder or spec.has_rectangle or spec.has_triangle:
            cyl_geom: CylinderGeometry | None = None
            if spec.has_cylinder:
                cyl_geom = self._cyl_builder.build(spec.cylinders[0])
            rect_geom: RectangleGeometry | None = None
            if spec.has_rectangle:
                rect_geom = self._rect_builder.build(spec.rectangles[0])
            tri_geom: TriangleGeometry | None = None
            if spec.has_triangle:
                tri_geom = self._tri_builder.build(spec.triangles[0])
            snappy_dict = self._generate_snappy_hex_mesh_dict(
                spec, cyl_geom, params, is_periodic, rect_geom, tri_geom
            )
            if cyl_geom is not None:
                cylinder_stl = self._generate_cylinder_stl(cyl_geom, domain.thickness_m)
            if rect_geom is not None:
                rectangle_stl = self._generate_rectangle_stl(rect_geom)
            if tri_geom is not None:
                triangle_stl = self._generate_triangle_stl(tri_geom)

        n_blocks = self._count_blocks(spec, bump_profile)
        expected_cells = params.n_layers_x * params.n_layers_y * params.n_layers_z

        return MeshManifest(
            block_mesh_dict=block_mesh,
            snappy_hex_mesh_dict=snappy_dict,
            cylinder_stl=cylinder_stl,
            rectangle_stl=rectangle_stl,
            triangle_stl=triangle_stl,
            has_cylinder=spec.has_cylinder,
            has_bump=spec.has_bump,
            n_blocks=n_blocks,
            expected_cell_count=expected_cells,
        )

    def _auto_params(self, spec: ObstacleFlowExperimentSpecV1) -> MeshParams:
        """Auto-determine mesh parameters based on domain size."""
        domain = spec.domain
        # Target ~50 cells per domain length, ~20 per height
        nx = max(20, int(domain.length_m / max(domain.length_m / 100, 0.5)))
        ny = max(10, int(domain.height_m / max(domain.height_m / 30, 0.5)))

        # Refine if cylinder present
        if spec.has_cylinder and spec.cylinders[0].diameter_m is not None:
            d = spec.cylinders[0].diameter_m
            # Ensure at least 20 cells across cylinder diameter
            cell_size = d / 20
            nx = max(nx, int(domain.length_m / cell_size))
            ny = max(ny, int(domain.height_m / cell_size))

        # Cap to reasonable limit
        nx = min(nx, 500)
        ny = min(ny, 200)

        return MeshParams(
            n_layers_x=nx,
            n_layers_y=ny,
            n_layers_z=1,
        )

    def _generate_block_mesh_dict(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        domain: DomainSpec,
        params: MeshParams,
        bump_profile: BumpProfile | None,
        is_periodic: bool,
    ) -> str:
        """Generate the blockMeshDict content.

        For cases without a bump, uses a simple single-block mesh.
        For cases with a bump, uses a 2-block vertical split where the
        bottom block follows the bump profile.
        """
        L = domain.length_m
        H = domain.height_m
        T = domain.thickness_m
        nx = params.n_layers_x
        ny = params.n_layers_y

        # Determine left/right patch types
        if is_periodic:
            left_patch = "cyclic"
            right_patch = "cyclic"
            left_neighbor = "right"
            right_neighbor = "left"
        else:
            left_patch = "patch"
            right_patch = "patch"
            left_neighbor = ""
            right_neighbor = ""

        # Determine top patch type
        top_type = spec.boundaries.top.type
        if top_type in (BoundaryType.SYMMETRY,):
            top_patch = "symmetry"
        elif top_type in (BoundaryType.FREESTREAM, BoundaryType.OPEN_BOUNDARY):
            top_patch = "patch"
        else:
            top_patch = "wall"

        bottom_patch = "wall"
        front_back_patch = "empty"

        lines: list[str] = []
        lines.append("/*--------------------------------*- C++ -*----------------------------------*\\")
        lines.append("| =========                 |                                                 |")
        lines.append("| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |")
        lines.append("|  \\\\    /   O peration     | Version:  13                                    |")
        lines.append("|   \\\\  /    A nd           | Web:      www.openfoam.org                      |")
        lines.append("|    \\/     M anipulation  |                                                 |")
        lines.append("\\*---------------------------------------------------------------------------*/")
        lines.append("FoamFile")
        lines.append("{")
        lines.append("    version     2.0;")
        lines.append("    format      ascii;")
        lines.append("    class       dictionary;")
        lines.append("    object      blockMeshDict;")
        lines.append("}")
        lines.append("// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //")
        lines.append("")

        if bump_profile is not None and spec.has_bump:
            return self._generate_bump_block_mesh(
                spec, domain, params, bump_profile, is_periodic
            )

        # Simple rectangular mesh (no bump)
        lines.append(f"scale   1;")
        lines.append("")
        lines.append("vertices")
        lines.append("(")
        lines.append(f"    (0    0    0)    // 0")
        lines.append(f"    ({self._fmt(L)}    0    0)    // 1")
        lines.append(f"    ({self._fmt(L)}    {self._fmt(H)}    0)    // 2")
        lines.append(f"    (0    {self._fmt(H)}    0)    // 3")
        lines.append(f"    (0    0    {self._fmt(T)})    // 4")
        lines.append(f"    ({self._fmt(L)}    0    {self._fmt(T)})    // 5")
        lines.append(f"    ({self._fmt(L)}    {self._fmt(H)}    {self._fmt(T)})    // 6")
        lines.append(f"    (0    {self._fmt(H)}    {self._fmt(T)})    // 7")
        lines.append(");")
        lines.append("")

        lines.append("blocks")
        lines.append("(")
        lines.append(
            f"    hex (0 1 2 3 4 5 6 7) ({nx} {ny} 1) simpleGrading ({self._fmt(params.grading_x)} {self._fmt(params.grading_y)} 1)"
        )
        lines.append(");")
        lines.append("")

        lines.append("edges")
        lines.append("(")
        lines.append(");")
        lines.append("")

        lines.append("boundary")
        lines.append("(")
        if is_periodic:
            lines.append("    left")
            lines.append("    {")
            lines.append(f"        type {left_patch};")
            lines.append(f"        neighbourPatch {left_neighbor};")
            lines.append("        faces")
            lines.append("        (")
            lines.append("            (0 4 7 3)")
            lines.append("        );")
            lines.append("    }")
            lines.append("    right")
            lines.append("    {")
            lines.append(f"        type {right_patch};")
            lines.append(f"        neighbourPatch {right_neighbor};")
            lines.append("        faces")
            lines.append("        (")
            lines.append("            (1 2 6 5)")
            lines.append("        );")
            lines.append("    }")
        else:
            lines.append("    left")
            lines.append("    {")
            lines.append(f"        type {left_patch};")
            lines.append("        faces")
            lines.append("        (")
            lines.append("            (0 4 7 3)")
            lines.append("        );")
            lines.append("    }")
            lines.append("    right")
            lines.append("    {")
            lines.append(f"        type {right_patch};")
            lines.append("        faces")
            lines.append("        (")
            lines.append("            (1 2 6 5)")
            lines.append("        );")
            lines.append("    }")

        lines.append("    top")
        lines.append("    {")
        lines.append(f"        type {top_patch};")
        lines.append("        faces")
        lines.append("        (")
        lines.append("            (3 7 6 2)")
        lines.append("        );")
        lines.append("    }")
        lines.append("    bottom")
        lines.append("    {")
        lines.append(f"        type {bottom_patch};")
        lines.append("        faces")
        lines.append("        (")
        lines.append("            (0 1 5 4)")
        lines.append("        );")
        lines.append("    }")
        lines.append("    frontAndBack")
        lines.append("    {")
        lines.append(f"        type {front_back_patch};")
        lines.append("        faces")
        lines.append("        (")
        lines.append("            (0 3 2 1)")
        lines.append("            (4 5 6 7)")
        lines.append("        );")
        lines.append("    }")
        lines.append(");")
        lines.append("")
        lines.append("// ************************************************************************* //")

        return "\n".join(lines)

    def _generate_bump_block_mesh(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        domain: DomainSpec,
        params: MeshParams,
        bump_profile: BumpProfile,
        is_periodic: bool,
    ) -> str:
        """Generate blockMeshDict with bump profile on bottom.

        Uses a 3-block topology:
        - Block 0: left of bump (flat bottom)
        - Block 1: bump region (bottom follows bump profile)
        - Block 2: right of bump (flat bottom)

        Each block spans the full height.
        """
        L = domain.length_m
        H = domain.height_m
        T = domain.thickness_m
        nx = params.n_layers_x
        ny = params.n_layers_y

        bump = spec.geometry_bump
        assert bump.center_x_m is not None
        assert bump.width_m is not None

        bump_left = bump.center_x_m - bump.width_m / 2.0
        bump_right = bump.center_x_m + bump.width_m / 2.0
        bump_h = bump.height_m or 0.0

        # Ensure bump is within domain
        bump_left = max(0.0, bump_left)
        bump_right = min(L, bump_right)

        # Cell distribution
        nx_left = max(10, int(nx * bump_left / L))
        nx_bump = max(20, int(nx * (bump_right - bump_left) / L))
        nx_right = max(10, nx - nx_left - nx_bump)

        left_patch = "cyclic" if is_periodic else "patch"
        right_patch = "cyclic" if is_periodic else "patch"

        lines: list[str] = []
        lines.append("/*--------------------------------*- C++ -*----------------------------------*\\")
        lines.append("| =========                 |                                                 |")
        lines.append("| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |")
        lines.append("|  \\\\    /   O peration     | Version:  13                                    |")
        lines.append("|   \\\\  /    A nd           | Web:      www.openfoam.org                      |")
        lines.append("|    \\/     M anipulation  |                                                 |")
        lines.append("\\*---------------------------------------------------------------------------*/")
        lines.append("FoamFile")
        lines.append("{")
        lines.append("    version     2.0;")
        lines.append("    format      ascii;")
        lines.append("    class       dictionary;")
        lines.append("    object      blockMeshDict;")
        lines.append("}")
        lines.append("// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //")
        lines.append("")
        lines.append("scale   1;")
        lines.append("")

        # Vertices: 3 blocks x 8 vertices = 24, but shared vertices reduce this
        # Block 0: (0,0) to (bump_left, H)
        # Block 1: (bump_left, 0) to (bump_right, H) with bump on bottom
        # Block 2: (bump_right, 0) to (L, H)
        lines.append("vertices")
        lines.append("(")
        # Block 0 vertices (0-7)
        lines.append(f"    (0    0    0)                              // 0  v0")
        lines.append(f"    ({self._fmt(bump_left)}    0    0)                    // 1  v1")
        lines.append(f"    ({self._fmt(bump_left)}    {self._fmt(H)}    0)              // 2  v2")
        lines.append(f"    (0    {self._fmt(H)}    0)                          // 3  v3")
        lines.append(f"    (0    0    {self._fmt(T)})                          // 4  v4")
        lines.append(f"    ({self._fmt(bump_left)}    0    {self._fmt(T)})            // 5  v5")
        lines.append(f"    ({self._fmt(bump_left)}    {self._fmt(H)}    {self._fmt(T)})      // 6  v6")
        lines.append(f"    (0    {self._fmt(H)}    {self._fmt(T)})                  // 7  v7")

        # Block 1 vertices (8-15) — bottom at bump_left is 0, bottom at bump_right is 0
        # but the bottom-middle vertex is at bump height
        lines.append(f"    ({self._fmt(bump_right)}    0    0)                   // 8  v8")
        lines.append(f"    ({self._fmt(bump_right)}    {self._fmt(H)}    0)             // 9  v9")
        lines.append(f"    ({self._fmt(bump_right)}    0    {self._fmt(T)})           // 10 v10")
        lines.append(f"    ({self._fmt(bump_right)}    {self._fmt(H)}    {self._fmt(T)})       // 11 v11")

        # Block 2 vertices (12-15)
        lines.append(f"    ({self._fmt(L)}    0    0)                           // 12 v12")
        lines.append(f"    ({self._fmt(L)}    {self._fmt(H)}    0)                     // 13 v13")
        lines.append(f"    ({self._fmt(L)}    0    {self._fmt(T)})                     // 14 v14")
        lines.append(f"    ({self._fmt(L)}    {self._fmt(H)}    {self._fmt(T)})             // 15 v15")
        lines.append(");")
        lines.append("")

        # Blocks
        lines.append("blocks")
        lines.append("(")
        # Block 0: left flat region
        lines.append(
            f"    hex (0 1 2 3 4 5 6 7) ({nx_left} {ny} 1) simpleGrading (1 1 1)"
        )
        # Block 1: bump region — use a curved edge for the bottom
        lines.append(
            f"    hex (1 8 9 2 5 10 11 6) ({nx_bump} {ny} 1) simpleGrading (1 1 1)"
        )
        # Block 2: right flat region
        lines.append(
            f"    hex (8 12 13 9 10 14 15 11) ({nx_right} {ny} 1) simpleGrading (1 1 1)"
        )
        lines.append(");")
        lines.append("")

        # Edges — define the bump profile as a spline edge on the bottom of block 1
        lines.append("edges")
        lines.append("(")
        # Bottom edge of block 1: from v1 (bump_left, 0) to v8 (bump_right, 0)
        # Use a spline to represent the bump
        if bump.profile_type == BumpProfileType.COSINE_BELL:
            spline_points = self._compute_spline_points(
                bump_left, bump_right, bump_h,
                lambda t: bump_h / 2.0 * (1.0 - math.cos(2.0 * math.pi * t)),
            )
        elif bump.profile_type == BumpProfileType.HALF_SINE:
            spline_points = self._compute_spline_points(
                bump_left, bump_right, bump_h,
                lambda t: bump_h * math.sin(math.pi * t),
            )
        elif bump.profile_type == BumpProfileType.GAUSSIAN:
            sigma = bump.standard_deviation or bump.width_m / 6.0
            cx = bump.center_x_m
            spline_points = self._compute_spline_points(
                bump_left, bump_right, bump_h,
                lambda t: bump_h * math.exp(-(((bump_left + t * (bump_right - bump_left)) - cx) ** 2) / (2.0 * sigma ** 2)),
            )
        else:
            spline_points = self._compute_spline_points(
                bump_left, bump_right, bump_h,
                lambda t: bump_h / 2.0 * (1.0 - math.cos(2.0 * math.pi * t)),
            )

        # Write spline edge
        lines.append(f"    spline 1 8")
        lines.append("    (")
        for x, y in spline_points:
            lines.append(f"        ({self._fmt(x)} {self._fmt(y)} 0)")
        lines.append("    )")
        # Also on the back face
        lines.append(f"    spline 5 10")
        lines.append("    (")
        for x, y in spline_points:
            lines.append(f"        ({self._fmt(x)} {self._fmt(y)} {self._fmt(T)})")
        lines.append("    )")
        lines.append(");")
        lines.append("")

        # Boundary
        lines.append("boundary")
        lines.append("(")
        if is_periodic:
            lines.append("    left")
            lines.append("    {")
            lines.append(f"        type {left_patch};")
            lines.append("        neighbourPatch right;")
            lines.append("        faces")
            lines.append("        (")
            lines.append("            (0 4 7 3)")
            lines.append("        );")
            lines.append("    }")
            lines.append("    right")
            lines.append("    {")
            lines.append(f"        type {right_patch};")
            lines.append("        neighbourPatch left;")
            lines.append("        faces")
            lines.append("        (")
            lines.append("            (12 13 15 14)")
            lines.append("        );")
            lines.append("    }")
        else:
            lines.append("    left")
            lines.append("    {")
            lines.append(f"        type {left_patch};")
            lines.append("        faces")
            lines.append("        (")
            lines.append("            (0 4 7 3)")
            lines.append("        );")
            lines.append("    }")
            lines.append("    right")
            lines.append("    {")
            lines.append(f"        type {right_patch};")
            lines.append("        faces")
            lines.append("        (")
            lines.append("            (12 13 15 14)")
            lines.append("        );")
            lines.append("    }")

        lines.append("    top")
        lines.append("    {")
        lines.append("        type wall;")
        lines.append("        faces")
        lines.append("        (")
        lines.append("            (3 2 6 7)")
        lines.append("            (2 9 11 6)")
        lines.append("            (9 13 15 11)")
        lines.append("        );")
        lines.append("    }")
        lines.append("    bottom")
        lines.append("    {")
        lines.append("        type wall;")
        lines.append("        faces")
        lines.append("        (")
        lines.append("            (0 1 5 4)")
        lines.append("            (1 8 10 5)")
        lines.append("            (8 12 14 10)")
        lines.append("        );")
        lines.append("    }")
        lines.append("    frontAndBack")
        lines.append("    {")
        lines.append("        type empty;")
        lines.append("        faces")
        lines.append("        (")
        lines.append("            (0 3 2 1)")
        lines.append("            (1 2 9 8)")
        lines.append("            (8 9 13 12)")
        lines.append("            (4 5 6 7)")
        lines.append("            (5 10 11 6)")
        lines.append("            (10 14 15 11)")
        lines.append("        );")
        lines.append("    }")
        lines.append(");")
        lines.append("")
        lines.append("// ************************************************************************* //")

        return "\n".join(lines)

    def _generate_snappy_hex_mesh_dict(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        cyl: CylinderGeometry | None,
        params: MeshParams,
        is_periodic: bool,
        rect: RectangleGeometry | None = None,
        tri: TriangleGeometry | None = None,
    ) -> str:
        """Generate snappyHexMeshDict for cylinder and/or rectangle and/or triangle refinement."""
        domain = spec.domain

        # Refinement region around cylinder
        x_min = y_min = x_max = y_max = 0.0
        if cyl is not None:
            r = cyl.radius
            refine_dist = r * 3.0
            x_min = cyl.bbox_x_min - refine_dist
            x_max = cyl.bbox_x_max + refine_dist
            y_min = max(0, cyl.bbox_y_min - refine_dist)
            y_max = min(domain.height_m, cyl.bbox_y_max + refine_dist)

        # Refinement region around rectangle
        rect_x_min = rect_y_min = rect_x_max = rect_y_max = 0.0
        if rect is not None:
            rect_refine_dist = max(rect.width, rect.height) * 1.5
            rect_x_min = rect.bbox_x_min - rect_refine_dist
            rect_x_max = rect.bbox_x_max + rect_refine_dist
            rect_y_min = max(0, rect.bbox_y_min - rect_refine_dist)
            rect_y_max = min(domain.height_m, rect.bbox_y_max + rect_refine_dist)

        # Refinement region around triangle
        tri_x_min = tri_y_min = tri_x_max = tri_y_max = 0.0
        if tri is not None:
            tri_refine_dist = max(tri.base_width, tri.height) * 1.5
            tri_x_min = tri.bbox_x_min - tri_refine_dist
            tri_x_max = tri.bbox_x_max + tri_refine_dist
            tri_y_min = max(0, tri.bbox_y_min - tri_refine_dist)
            tri_y_max = min(domain.height_m, tri.bbox_y_max + tri_refine_dist)

        lines: list[str] = []
        lines.append("/*--------------------------------*- C++ -*----------------------------------*\\")
        lines.append("| =========                 |                                                 |")
        lines.append("| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |")
        lines.append("|  \\\\    /   O peration     | Version:  13                                    |")
        lines.append("|   \\\\  /    A nd           | Web:      www.openfoam.org                      |")
        lines.append("|    \\/     M anipulation  |                                                 |")
        lines.append("\\*---------------------------------------------------------------------------*/")
        lines.append("FoamFile")
        lines.append("{")
        lines.append("    version     2.0;")
        lines.append("    format      ascii;")
        lines.append("    class       dictionary;")
        lines.append("    object      snappyHexMeshDict;")
        lines.append("}")
        lines.append("// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //")
        lines.append("")
        lines.append("castellatedMesh   true;")
        lines.append("snap              true;")
        lines.append("addLayers         false;")
        lines.append("")
        lines.append("geometry")
        lines.append("{")
        if cyl is not None:
            lines.append("    cylinder")
            lines.append("    {")
            lines.append("        type triSurfaceMesh;")
            lines.append('        file "cylinder.stl";')
            lines.append("    }")
        if rect is not None:
            lines.append("    rectangle")
            lines.append("    {")
            lines.append("        type triSurfaceMesh;")
            lines.append('        file "rectangle.stl";')
            lines.append("    }")
        if tri is not None:
            lines.append("    triangle")
            lines.append("    {")
            lines.append("        type triSurfaceMesh;")
            lines.append('        file "triangle.stl";')
            lines.append("    }")
        if cyl is not None:
            lines.append("    refinementBox")
            lines.append("    {")
            lines.append("        type searchableBox;")
            lines.append(f"        min ({self._fmt(x_min)} {self._fmt(y_min)} 0);")
            lines.append(f"        max ({self._fmt(x_max)} {self._fmt(y_max)} {self._fmt(domain.thickness_m)});")
            lines.append("    }")
        if rect is not None:
            lines.append("    rectangleRefinementBox")
            lines.append("    {")
            lines.append("        type searchableBox;")
            lines.append(f"        min ({self._fmt(rect_x_min)} {self._fmt(rect_y_min)} 0);")
            lines.append(f"        max ({self._fmt(rect_x_max)} {self._fmt(rect_y_max)} {self._fmt(domain.thickness_m)});")
            lines.append("    }")
        if tri is not None:
            lines.append("    triangleRefinementBox")
            lines.append("    {")
            lines.append("        type searchableBox;")
            lines.append(f"        min ({self._fmt(tri_x_min)} {self._fmt(tri_y_min)} 0);")
            lines.append(f"        max ({self._fmt(tri_x_max)} {self._fmt(tri_y_max)} {self._fmt(domain.thickness_m)});")
            lines.append("    }")
        lines.append("};")
        lines.append("")
        lines.append("castellatedMeshControls")
        lines.append("{")
        lines.append("    maxLocalCells 1000000;")
        lines.append("    maxGlobalCells 5000000;")
        lines.append("    minRefinementCells 10;")
        lines.append("    maxLoadUnbalance 0.10;")
        lines.append("    nCellsBetweenLevels 3;")
        lines.append("    resolveFeatureAngle 30;")
        lines.append("")
        lines.append("    features")
        lines.append("    (")
        lines.append("    );")
        lines.append("")
        lines.append("    refinementSurfaces")
        lines.append("    {")
        if cyl is not None:
            lines.append("        cylinder")
            lines.append("        {")
            lines.append(f"            level ({params.cylinder_refinement_level} {params.cylinder_refinement_level});")
            lines.append("            patchInfo { type wall; }")
            lines.append("        }")
        if rect is not None:
            lines.append("        rectangle")
            lines.append("        {")
            lines.append(f"            level ({params.cylinder_refinement_level} {params.cylinder_refinement_level});")
            lines.append("            patchInfo { type wall; }")
            lines.append("        }")
        if tri is not None:
            lines.append("        triangle")
            lines.append("        {")
            lines.append(f"            level ({params.cylinder_refinement_level} {params.cylinder_refinement_level});")
            lines.append("            patchInfo { type wall; }")
            lines.append("        }")
        lines.append("    }")
        lines.append("")
        lines.append("    refinementRegions")
        lines.append("    {")
        if cyl is not None:
            lines.append("        refinementBox")
            lines.append("        {")
            lines.append(f"            mode inside;")
            lines.append(f"            levels (({params.cylinder_refinement_level - 1} {params.cylinder_refinement_level - 1}));")
            lines.append("        }")
        if rect is not None:
            lines.append("        rectangleRefinementBox")
            lines.append("        {")
            lines.append(f"            mode inside;")
            lines.append(f"            levels (({params.cylinder_refinement_level - 1} {params.cylinder_refinement_level - 1}));")
            lines.append("        }")
        if tri is not None:
            lines.append("        triangleRefinementBox")
            lines.append("        {")
            lines.append(f"            mode inside;")
            lines.append(f"            levels (({params.cylinder_refinement_level - 1} {params.cylinder_refinement_level - 1}));")
            lines.append("        }")
        lines.append("    }")
        lines.append("")
        if cyl is not None:
            inside_x = cyl.center_x
            inside_y = cyl.center_y + cyl.radius * 3
        elif rect is not None:
            inside_x = rect.center_x
            inside_y = rect.bbox_y_max + rect.height
        elif tri is not None:
            inside_x = tri.center_x
            inside_y = tri.bbox_y_max + tri.height
        else:
            inside_x = domain.length_m / 2
            inside_y = domain.height_m / 2
        lines.append("    insidePoint")
        lines.append(f"    ({self._fmt(inside_x)} {self._fmt(inside_y)} {self._fmt(domain.thickness_m / 2)});")
        lines.append("")
        lines.append("    allowFreeStandingZoneFaces true;")
        lines.append("}")
        lines.append("")
        lines.append("snapControls")
        lines.append("{")
        lines.append("    nSmoothPatch 3;")
        lines.append("    tolerance 1.0;")
        lines.append("    nSolveIter 30;")
        lines.append("    nRelaxIter 5;")
        lines.append("    nFeatureSnapIter 10;")
        lines.append("    implicitFeatureSnap false;")
        lines.append("    explicitFeatureSnap true;")
        lines.append("    multiRegionFeatureSnap false;")
        lines.append("}")
        lines.append("")
        lines.append("addLayersControls")
        lines.append("{")
        lines.append("}")
        lines.append("")
        lines.append("meshQualityControls")
        lines.append("{")
        lines.append('    #includeEtc "caseDicts/mesh/generation/meshQualityDict"')
        lines.append("}")
        lines.append("")
        lines.append("writeFlags")
        lines.append("(")
        lines.append("    layerSets")
        lines.append(");")
        lines.append("")
        lines.append("mergeTolerance   1e-6;")
        lines.append("")
        lines.append("// ************************************************************************* //")

        return "\n".join(lines)

    def _generate_cylinder_stl(
        self, cyl: CylinderGeometry, thickness: float
    ) -> str:
        """Generate a cylinder STL surface for snappyHexMesh.

        Creates a hollow cylinder (just the side surface) as a triangular
        STL mesh, extruded in z for the 2D thickness.
        """
        n_segments = 64
        z0 = 0.0
        z1 = thickness

        lines: list[str] = []
        lines.append("solid cylinder")
        for i in range(n_segments):
            angle1 = 2.0 * math.pi * i / n_segments
            angle2 = 2.0 * math.pi * (i + 1) / n_segments

            x1 = cyl.center_x + cyl.radius * math.cos(angle1)
            y1 = cyl.center_y + cyl.radius * math.sin(angle1)
            x2 = cyl.center_x + cyl.radius * math.cos(angle2)
            y2 = cyl.center_y + cyl.radius * math.sin(angle2)

            # Triangle 1: (x1,y1,z0) (x2,y2,z0) (x2,y2,z1)
            # Triangle 2: (x1,y1,z0) (x2,y2,z1) (x1,y1,z1)
            # Compute normal (outward)
            nx = math.cos((angle1 + angle2) / 2)
            ny = math.sin((angle1 + angle2) / 2)
            nz = 0.0

            lines.append(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}")
            lines.append("    outer loop")
            lines.append(f"      vertex {x1:.6e} {y1:.6e} {z0:.6e}")
            lines.append(f"      vertex {x2:.6e} {y2:.6e} {z0:.6e}")
            lines.append(f"      vertex {x2:.6e} {y2:.6e} {z1:.6e}")
            lines.append("    endloop")
            lines.append("  endfacet")
            lines.append(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}")
            lines.append("    outer loop")
            lines.append(f"      vertex {x1:.6e} {y1:.6e} {z0:.6e}")
            lines.append(f"      vertex {x2:.6e} {y2:.6e} {z1:.6e}")
            lines.append(f"      vertex {x1:.6e} {y1:.6e} {z1:.6e}")
            lines.append("    endloop")
            lines.append("  endfacet")

        lines.append("endsolid cylinder")
        return "\n".join(lines)

    def _generate_rectangle_stl(
        self, rect: RectangleGeometry
    ) -> str:
        """Generate a rectangle box STL surface for snappyHexMesh.

        Creates a closed box (8 vertices, 12 triangles) as a triangular
        STL mesh, extruded in z for the 2D thickness.
        """
        x_min = rect.bbox_x_min
        x_max = rect.bbox_x_max
        y_min = rect.bbox_y_min
        y_max = rect.bbox_y_max
        z0 = 0.0
        z1 = rect.thickness

        # 8 vertices
        verts = [
            (x_min, y_min, z0),  # 0
            (x_max, y_min, z0),  # 1
            (x_max, y_max, z0),  # 2
            (x_min, y_max, z0),  # 3
            (x_min, y_min, z1),  # 4
            (x_max, y_min, z1),  # 5
            (x_max, y_max, z1),  # 6
            (x_min, y_max, z1),  # 7
        ]

        # 12 triangles: (normal, v0_idx, v1_idx, v2_idx)
        triangles = [
            # Bottom (normal -z)
            ((0.0, 0.0, -1.0), 0, 3, 2),
            ((0.0, 0.0, -1.0), 0, 2, 1),
            # Top (normal +z)
            ((0.0, 0.0, 1.0), 4, 5, 6),
            ((0.0, 0.0, 1.0), 4, 6, 7),
            # Left (normal -x)
            ((-1.0, 0.0, 0.0), 0, 4, 7),
            ((-1.0, 0.0, 0.0), 0, 7, 3),
            # Right (normal +x)
            ((1.0, 0.0, 0.0), 1, 2, 6),
            ((1.0, 0.0, 0.0), 1, 6, 5),
            # Front (normal -y)
            ((0.0, -1.0, 0.0), 0, 1, 5),
            ((0.0, -1.0, 0.0), 0, 5, 4),
            # Back (normal +y)
            ((0.0, 1.0, 0.0), 3, 7, 6),
            ((0.0, 1.0, 0.0), 3, 6, 2),
        ]

        lines: list[str] = []
        lines.append("solid rectangle")
        for normal, i0, i1, i2 in triangles:
            nx, ny, nz = normal
            v0 = verts[i0]
            v1 = verts[i1]
            v2 = verts[i2]
            lines.append(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}")
            lines.append("    outer loop")
            lines.append(f"      vertex {v0[0]:.6e} {v0[1]:.6e} {v0[2]:.6e}")
            lines.append(f"      vertex {v1[0]:.6e} {v1[1]:.6e} {v1[2]:.6e}")
            lines.append(f"      vertex {v2[0]:.6e} {v2[1]:.6e} {v2[2]:.6e}")
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append("endsolid rectangle")
        return "\n".join(lines)

    def _generate_triangle_stl(
        self, tri: TriangleGeometry
    ) -> str:
        """Generate a triangular prism STL surface for snappyHexMesh.

        Creates a closed prism (6 vertices, 8 triangles) as a triangular
        STL mesh, extruded in z for the 2D thickness.

        The prism has:
          - 3 bottom vertices (v0, v1, v2 at z0)
          - 3 top vertices (v0, v1, v2 at z1)
          - Bottom face: 1 triangle
          - Top face: 1 triangle
          - 3 side faces: 2 triangles each
          - Total: 8 triangles
        """
        z0 = 0.0
        z1 = tri.thickness

        # 6 vertices: 3 bottom + 3 top
        verts = [
            (tri.v0[0], tri.v0[1], z0),  # 0: base left bottom
            (tri.v1[0], tri.v1[1], z0),  # 1: base right bottom
            (tri.v2[0], tri.v2[1], z0),  # 2: apex bottom
            (tri.v0[0], tri.v0[1], z1),  # 3: base left top
            (tri.v1[0], tri.v1[1], z1),  # 4: base right top
            (tri.v2[0], tri.v2[1], z1),  # 5: apex top
        ]

        # 8 triangles: bottom + top + 3 sides (2 each)
        # Normal directions are approximate (axis-aligned) following the
        # rectangle STL pattern — snappyHexMesh only needs a closed surface.
        triangles = [
            # Bottom (normal -z)
            ((0.0, 0.0, -1.0), 0, 2, 1),
            # Top (normal +z)
            ((0.0, 0.0, 1.0), 3, 4, 5),
            # Side 0-1 (base, normal -y for "up")
            ((0.0, -1.0, 0.0), 0, 1, 4),
            ((0.0, -1.0, 0.0), 0, 4, 3),
            # Side 1-2 (right, normal +x approx)
            ((1.0, 0.0, 0.0), 1, 2, 5),
            ((1.0, 0.0, 0.0), 1, 5, 4),
            # Side 2-0 (left, normal -x approx)
            ((-1.0, 0.0, 0.0), 2, 0, 3),
            ((-1.0, 0.0, 0.0), 2, 3, 5),
        ]

        lines: list[str] = []
        lines.append("solid triangle")
        for normal, i0, i1, i2 in triangles:
            nx, ny, nz = normal
            v0 = verts[i0]
            v1 = verts[i1]
            v2 = verts[i2]
            lines.append(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}")
            lines.append("    outer loop")
            lines.append(f"      vertex {v0[0]:.6e} {v0[1]:.6e} {v0[2]:.6e}")
            lines.append(f"      vertex {v1[0]:.6e} {v1[1]:.6e} {v1[2]:.6e}")
            lines.append(f"      vertex {v2[0]:.6e} {v2[1]:.6e} {v2[2]:.6e}")
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append("endsolid triangle")
        return "\n".join(lines)

    def _compute_spline_points(
        self,
        x_left: float,
        x_right: float,
        max_h: float,
        height_func,
    ) -> list[tuple[float, float]]:
        """Compute spline points for a bump edge."""
        n = 20
        points: list[tuple[float, float]] = []
        for i in range(1, n):
            t = i / n
            x = x_left + t * (x_right - x_left)
            y = height_func(t)
            points.append((x, y))
        return points

    def _count_blocks(
        self, spec: ObstacleFlowExperimentSpecV1, bump_profile: BumpProfile | None
    ) -> int:
        """Count the number of blocks in the mesh."""
        if bump_profile is not None and spec.has_bump:
            return 3
        return 1

    @staticmethod
    def _fmt(v: float) -> str:
        """Format a float for OpenFOAM output."""
        return f"{v:.12g}"


__all__ = [
    "MeshManifest",
    "MeshParams",
    "ObstacleFlowMeshBackend",
]
