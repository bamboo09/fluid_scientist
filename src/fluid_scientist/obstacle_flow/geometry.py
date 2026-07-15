"""Geometry builders for bump profiles and cylinder placement.

Implements BumpProfileGenerator and CylinderGeometryBuilder from
the plan Section 9 and Section 8.

These builders compute the geometric data needed by the mesh backend
and the OpenFOAM compiler.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fluid_scientist.obstacle_flow.models import (
    BumpProfileType,
    BumpSpec,
    CylinderSpec,
    DomainSpec,
    RectangleSpec,
    TriangleSpec,
)


@dataclass(frozen=True)
class BumpProfile:
    """Computed bump profile — a list of (x, y) points defining the bottom contour."""

    points: list[tuple[float, float]]
    profile_type: BumpProfileType
    max_height: float


class BumpProfileGenerator:
    """Generates geometric profiles for different bump types.

    Implements Section 9 of the plan:
    - cosine_bell: smooth cosine bell shape
    - half_sine: half-sine bump
    - gaussian: Gaussian distribution bump
    - piecewise_points: user-defined points
    """

    def generate(self, bump: BumpSpec, domain: DomainSpec, n_points: int = 200) -> BumpProfile:
        """Generate the bump profile as a set of (x, y) points."""
        if not bump.enabled:
            return BumpProfile(
                points=[(0.0, 0.0), (domain.length_m, 0.0)],
                profile_type=bump.profile_type,
                max_height=0.0,
            )

        if bump.profile_type == BumpProfileType.COSINE_BELL:
            return self._cosine_bell(bump, domain, n_points)
        elif bump.profile_type == BumpProfileType.HALF_SINE:
            return self._half_sine(bump, domain, n_points)
        elif bump.profile_type == BumpProfileType.GAUSSIAN:
            return self._gaussian(bump, domain, n_points)
        elif bump.profile_type == BumpProfileType.PIECEWISE_POINTS:
            return self._piecewise(bump, domain)
        else:
            raise ValueError(f"Unknown bump profile type: {bump.profile_type}")

    def _cosine_bell(
        self, bump: BumpSpec, domain: DomainSpec, n_points: int
    ) -> BumpProfile:
        assert bump.center_x_m is not None
        assert bump.width_m is not None
        assert bump.height_m is not None

        cx = bump.center_x_m
        w = bump.width_m
        h = bump.height_m
        x_left = cx - w / 2.0
        x_right = cx + w / 2.0

        points: list[tuple[float, float]] = []
        # Before bump
        points.append((0.0, 0.0))
        if x_left > 0:
            points.append((x_left, 0.0))

        # Bump region: y = h/2 * (1 - cos(2*pi*(x - x_left) / w))
        n_bump = max(50, n_points // 2)
        for i in range(n_bump + 1):
            t = i / n_bump
            x = x_left + t * w
            y = h / 2.0 * (1.0 - math.cos(2.0 * math.pi * t))
            points.append((x, y))

        # After bump
        if x_right < domain.length_m:
            points.append((x_right, 0.0))
        points.append((domain.length_m, 0.0))

        return BumpProfile(points=points, profile_type=BumpProfileType.COSINE_BELL, max_height=h)

    def _half_sine(
        self, bump: BumpSpec, domain: DomainSpec, n_points: int
    ) -> BumpProfile:
        assert bump.center_x_m is not None
        assert bump.width_m is not None
        assert bump.height_m is not None

        cx = bump.center_x_m
        w = bump.width_m
        h = bump.height_m
        x_left = cx - w / 2.0
        x_right = cx + w / 2.0

        points: list[tuple[float, float]] = []
        points.append((0.0, 0.0))
        if x_left > 0:
            points.append((x_left, 0.0))

        n_bump = max(50, n_points // 2)
        for i in range(n_bump + 1):
            t = i / n_bump
            x = x_left + t * w
            y = h * math.sin(math.pi * t)
            points.append((x, y))

        if x_right < domain.length_m:
            points.append((x_right, 0.0))
        points.append((domain.length_m, 0.0))

        return BumpProfile(points=points, profile_type=BumpProfileType.HALF_SINE, max_height=h)

    def _gaussian(
        self, bump: BumpSpec, domain: DomainSpec, n_points: int
    ) -> BumpProfile:
        assert bump.center_x_m is not None
        assert bump.height_m is not None
        assert bump.standard_deviation is not None

        cx = bump.center_x_m
        h = bump.height_m
        sigma = bump.standard_deviation
        cutoff = bump.cutoff_width or 3.0 * sigma
        x_left = cx - cutoff
        x_right = cx + cutoff

        points: list[tuple[float, float]] = []
        points.append((0.0, 0.0))
        if x_left > 0:
            points.append((x_left, 0.0))

        n_bump = max(50, n_points // 2)
        for i in range(n_bump + 1):
            t = i / n_bump
            x = x_left + t * 2.0 * cutoff
            y = h * math.exp(-((x - cx) ** 2) / (2.0 * sigma ** 2))
            points.append((x, y))

        if x_right < domain.length_m:
            points.append((x_right, 0.0))
        points.append((domain.length_m, 0.0))

        return BumpProfile(points=points, profile_type=BumpProfileType.GAUSSIAN, max_height=h)

    def _piecewise(self, bump: BumpSpec, domain: DomainSpec) -> BumpProfile:
        points: list[tuple[float, float]] = [(0.0, 0.0)]
        points.extend(bump.custom_points)
        points.append((domain.length_m, 0.0))
        max_h = max(p[1] for p in points) if points else 0.0
        return BumpProfile(
            points=points,
            profile_type=BumpProfileType.PIECEWISE_POINTS,
            max_height=max_h,
        )


@dataclass(frozen=True)
class CylinderGeometry:
    """Computed cylinder geometry for mesh generation."""

    center_x: float
    center_y: float
    radius: float
    diameter: float
    # Bounding box for mesh grading
    bbox_x_min: float
    bbox_x_max: float
    bbox_y_min: float
    bbox_y_max: float


class CylinderGeometryBuilder:
    """Builds cylinder geometry data from spec.

    Implements Section 8 of the plan.
    """

    def build(self, cyl: CylinderSpec) -> CylinderGeometry:
        """Compute cylinder geometry data."""
        if cyl.center_x_m is None or cyl.center_y_m is None or cyl.diameter_m is None:
            raise ValueError(
                f"Cylinder {cyl.id} has unresolved geometry — "
                "center_x_m, center_y_m, and diameter_m are required"
            )

        r = cyl.diameter_m / 2.0
        return CylinderGeometry(
            center_x=cyl.center_x_m,
            center_y=cyl.center_y_m,
            radius=r,
            diameter=cyl.diameter_m,
            bbox_x_min=cyl.center_x_m - r,
            bbox_x_max=cyl.center_x_m + r,
            bbox_y_min=cyl.center_y_m - r,
            bbox_y_max=cyl.center_y_m + r,
        )


@dataclass(frozen=True)
class RectangleGeometry:
    """Computed rectangle geometry for mesh generation."""

    center_x: float
    center_y: float
    width: float
    height: float
    thickness: float
    # Bounding box for mesh grading
    bbox_x_min: float
    bbox_x_max: float
    bbox_y_min: float
    bbox_y_max: float


class RectangleGeometryBuilder:
    """Builds rectangle geometry data from spec.

    Mirrors CylinderGeometryBuilder — computes bounding box from
    center / width / height for use by the mesh backend.
    """

    def build(self, rect: RectangleSpec) -> RectangleGeometry:
        """Compute rectangle geometry data."""
        half_w = rect.width / 2.0
        half_h = rect.height / 2.0
        return RectangleGeometry(
            center_x=rect.center_x,
            center_y=rect.center_y,
            width=rect.width,
            height=rect.height,
            thickness=rect.thickness,
            bbox_x_min=rect.center_x - half_w,
            bbox_x_max=rect.center_x + half_w,
            bbox_y_min=rect.center_y - half_h,
            bbox_y_max=rect.center_y + half_h,
        )


@dataclass(frozen=True)
class TriangleGeometry:
    """Computed triangle geometry for mesh generation."""

    center_x: float  # center of base
    center_y: float  # base y
    base_width: float
    height: float
    thickness: float
    apex_direction: str  # "up", "down", "left", "right"
    # 3 vertices in 2D (x, y)
    v0: tuple[float, float]  # base left
    v1: tuple[float, float]  # base right
    v2: tuple[float, float]  # apex
    # Bounding box
    bbox_x_min: float
    bbox_x_max: float
    bbox_y_min: float
    bbox_y_max: float


class TriangleGeometryBuilder:
    """Builds triangle geometry data from spec.

    Mirrors RectangleGeometryBuilder — computes vertices and bounding
    box from center / base_width / height / apex_direction for use by
    the mesh backend.
    """

    def build(self, tri: TriangleSpec) -> TriangleGeometry:
        """Compute triangle vertices from spec."""
        half_w = tri.base_width / 2.0
        cx = tri.center_x
        cy = tri.center_y
        h = tri.height
        direction = tri.apex_direction

        if direction == "up":
            v0 = (cx - half_w, cy)
            v1 = (cx + half_w, cy)
            v2 = (cx, cy + h)
        elif direction == "down":
            v0 = (cx - half_w, cy + h)
            v1 = (cx + half_w, cy + h)
            v2 = (cx, cy)
        elif direction == "left":
            v0 = (cx, cy - half_w)
            v1 = (cx, cy + half_w)
            v2 = (cx - h, cy)
        else:  # right
            v0 = (cx, cy - half_w)
            v1 = (cx, cy + half_w)
            v2 = (cx + h, cy)

        xs = [v0[0], v1[0], v2[0]]
        ys = [v0[1], v1[1], v2[1]]
        return TriangleGeometry(
            center_x=cx,
            center_y=cy,
            base_width=tri.base_width,
            height=h,
            thickness=tri.thickness,
            apex_direction=direction,
            v0=v0,
            v1=v1,
            v2=v2,
            bbox_x_min=min(xs),
            bbox_x_max=max(xs),
            bbox_y_min=min(ys),
            bbox_y_max=max(ys),
        )


__all__ = [
    "BumpProfile",
    "BumpProfileGenerator",
    "CylinderGeometry",
    "CylinderGeometryBuilder",
    "RectangleGeometry",
    "RectangleGeometryBuilder",
    "TriangleGeometry",
    "TriangleGeometryBuilder",
]
