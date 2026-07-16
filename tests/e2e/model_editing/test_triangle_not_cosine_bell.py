"""Test: triangle geometry is not silently changed to cosine_bell.

This test reproduces the known issue where modifying an unrelated
parameter could inadvertently change a geometry entity's semantic type
(e.g. a triangle being silently reclassified as a cosine_bell).  The
new spec-editing system guarantees that geometry semantic types are
preserved across unrelated edits and tracked separately from the
primitive representation.

Verifies:
* A triangle entity's semantic_type survives an unrelated end_time edit.
* Changing a triangle to a rectangle only affects the target entity.
* The primitive type changes but the original_user_semantics is tracked
  separately.
"""
from __future__ import annotations

import pytest

from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import (
    GeometryDefinition,
    GeometryEntity,
    PlacementSpec,
    SourcedValue,
)

from .conftest import make_patch, make_study_spec, _sourced


def _make_spec_with_triangle_and_cylinder() -> "SimulationStudySpec":
    """Build a spec containing both a triangle and a cylinder entity."""
    from fluid_scientist.study_spec import DomainSpec

    spec = make_study_spec()
    # Replace geometry with one containing both entities.
    spec = spec.model_copy(update={
        "geometry": GeometryDefinition(
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
                "triangle": GeometryEntity(
                    entity_id="triangle",
                    semantic_type="triangle_2d",
                    primitive={"type": "polygon", "n_vertices": 3},
                    polygon_vertices=[
                        {"x": 6.0, "y": 2.0},
                        {"x": 7.0, "y": 2.0},
                        {"x": 6.5, "y": 3.0},
                    ],
                    original_user_semantics="triangle",
                    placement=PlacementSpec(
                        x=_sourced(6.5, unit="m"),
                        y=_sourced(2.0, unit="m"),
                    ),
                ),
            },
            relations=[],
        ),
    })
    return spec


class TestTriangleNotCosineBell:
    """Verify geometry semantic-type preservation."""

    def test_triangle_survives_unrelated_edit(self) -> None:
        """Modifying end_time does not change the triangle's semantic_type."""
        spec = _make_spec_with_triangle_and_cylinder()

        # Precondition: triangle entity exists with correct semantic_type.
        triangle = spec.geometry.entities["triangle"]
        assert triangle.semantic_type == "triangle_2d"

        # Apply an unrelated patch: change end_time.
        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == []
        assert result.new_spec is not None

        # The triangle's semantic_type must NOT have changed.
        new_triangle = result.new_spec.geometry.entities["triangle"]
        assert new_triangle.semantic_type == "triangle_2d", (
            f"triangle semantic_type should still be 'triangle_2d', "
            f"got '{new_triangle.semantic_type}'"
        )
        # It must definitely NOT be cosine_bell_2d.
        assert new_triangle.semantic_type != "cosine_bell_2d"

    def test_cylinder_also_preserved(self) -> None:
        """The cylinder entity is also preserved across the unrelated edit."""
        spec = _make_spec_with_triangle_and_cylinder()

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/numerics/time/end_time",
                    value=15.0,
                    source_quote="仿真时间设为15秒",
                ),
            ],
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        cyl = result.new_spec.geometry.entities["cylinder"]
        assert cyl.semantic_type == "cylinder_2d"
        assert cyl.primitive == {"type": "circle", "radius": 0.2, "diameter": 0.4}

    def test_change_triangle_to_rectangle_only_affects_target(self) -> None:
        """Changing triangle to rectangle only affects the triangle entity."""
        spec = _make_spec_with_triangle_and_cylinder()

        # Replace the triangle entity with a rectangle entity.
        new_triangle_entity = {
            "entity_id": "triangle",
            "semantic_type": "rectangle_2d",
            "primitive": {"type": "polygon", "n_vertices": 4},
            "polygon_vertices": [
                {"x": 6.0, "y": 2.0},
                {"x": 7.0, "y": 2.0},
                {"x": 7.0, "y": 3.0},
                {"x": 6.0, "y": 3.0},
            ],
            "original_user_semantics": "triangle",
            "placement": {
                "x": {"value": 6.5, "unit": "m", "status": "user_explicit",
                      "source_turn_ids": ["turn_0"], "confidence": 0.9},
                "y": {"value": 2.0, "unit": "m", "status": "user_explicit",
                      "source_turn_ids": ["turn_0"], "confidence": 0.9},
            },
        }

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/geometry/entities/triangle",
                    value=new_triangle_entity,
                    source_quote="把三角形改成矩形",
                ),
            ],
            untouched_guarantee=False,  # Full entity replacement.
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == [], f"Patch failed: {result.errors}"
        assert result.new_spec is not None

        new_spec = result.new_spec

        # The triangle entity is now a rectangle.
        changed_entity = new_spec.geometry.entities["triangle"]
        assert changed_entity.semantic_type == "rectangle_2d", (
            f"semantic_type should be 'rectangle_2d', "
            f"got '{changed_entity.semantic_type}'"
        )
        assert changed_entity.primitive["n_vertices"] == 4, (
            "Primitive should have 4 vertices for a rectangle"
        )

        # The cylinder entity is NOT affected.
        cyl = new_spec.geometry.entities["cylinder"]
        assert cyl.semantic_type == "cylinder_2d", (
            "Cylinder semantic_type should be unchanged"
        )
        assert cyl.primitive == {"type": "circle", "radius": 0.2, "diameter": 0.4}

    def test_original_user_semantics_tracked_separately(self) -> None:
        """original_user_semantics is preserved even when semantic_type changes."""
        spec = _make_spec_with_triangle_and_cylinder()

        # The original_user_semantics is "triangle".
        assert spec.geometry.entities["triangle"].original_user_semantics == "triangle"

        new_triangle_entity = {
            "entity_id": "triangle",
            "semantic_type": "rectangle_2d",
            "primitive": {"type": "polygon", "n_vertices": 4},
            "polygon_vertices": [
                {"x": 6.0, "y": 2.0},
                {"x": 7.0, "y": 2.0},
                {"x": 7.0, "y": 3.0},
                {"x": 6.0, "y": 3.0},
            ],
            "original_user_semantics": "triangle",  # Preserved!
            "placement": {
                "x": {"value": 6.5, "unit": "m", "status": "user_explicit",
                      "source_turn_ids": ["turn_0"], "confidence": 0.9},
                "y": {"value": 2.0, "unit": "m", "status": "user_explicit",
                      "source_turn_ids": ["turn_0"], "confidence": 0.9},
            },
        }

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/geometry/entities/triangle",
                    value=new_triangle_entity,
                    source_quote="把三角形改成矩形",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        changed = result.new_spec.geometry.entities["triangle"]

        # semantic_type changed to rectangle_2d.
        assert changed.semantic_type == "rectangle_2d"

        # But original_user_semantics still records the original intent.
        assert changed.original_user_semantics == "triangle", (
            "original_user_semantics should preserve the original 'triangle' intent"
        )

    def test_primitive_type_changed(self) -> None:
        """The primitive type changes from 3-vertex polygon to 4-vertex polygon."""
        spec = _make_spec_with_triangle_and_cylinder()

        original = spec.geometry.entities["triangle"]
        assert original.primitive["n_vertices"] == 3

        new_triangle_entity = {
            "entity_id": "triangle",
            "semantic_type": "rectangle_2d",
            "primitive": {"type": "polygon", "n_vertices": 4},
            "polygon_vertices": [
                {"x": 6.0, "y": 2.0},
                {"x": 7.0, "y": 2.0},
                {"x": 7.0, "y": 3.0},
                {"x": 6.0, "y": 3.0},
            ],
            "original_user_semantics": "triangle",
            "placement": {
                "x": {"value": 6.5, "unit": "m", "status": "user_explicit",
                      "source_turn_ids": ["turn_0"], "confidence": 0.9},
                "y": {"value": 2.0, "unit": "m", "status": "user_explicit",
                      "source_turn_ids": ["turn_0"], "confidence": 0.9},
            },
        }

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="replace",
                    path="/geometry/entities/triangle",
                    value=new_triangle_entity,
                    source_quote="把三角形改成矩形",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        changed = result.new_spec.geometry.entities["triangle"]
        assert changed.primitive["n_vertices"] == 4, (
            f"Primitive should have 4 vertices, got {changed.primitive['n_vertices']}"
        )
        assert changed.primitive["type"] == "polygon"
