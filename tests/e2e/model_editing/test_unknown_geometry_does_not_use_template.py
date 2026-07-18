"""Test: unknown geometry does not silently use a template.

This test reproduces the known issue where an unknown geometry type
(e.g. "superellipse") was silently mapped to an existing template
(cylinder, cosine_bell, etc.) instead of being explicitly declared as
an unknown capability.  The new system ensures that:

* The ``declare_unknown_capability`` operation is accepted without
  silently modifying the spec's geometry.
* No existing entity's semantic_type is changed to accommodate the
  unknown geometry.
* The original semantics of existing entities are preserved.

Verifies:
* The patch with ``declare_unknown_capability`` processes successfully.
* The system does NOT silently map superellipse to cylinder, cosine_bell,
  or any other template.
* The original cylinder entity's semantic_type and
  original_user_semantics are preserved.
"""
from __future__ import annotations

import pytest

from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import SimulationStudySpec

from .conftest import make_patch, make_study_spec


class TestUnknownGeometryDoesNotUseTemplate:
    """Verify that unknown geometries are not silently templated."""

    def test_declare_unknown_capability_processes_successfully(self) -> None:
        """A declare_unknown_capability patch processes without errors."""
        spec = make_study_spec()

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="declare_unknown_capability",
                    path="/geometry/entities/superellipse",
                    value={
                        "capability": "geometry_type",
                        "name": "superellipse",
                        "parameters": {"a": 1.0, "b": 0.5, "n": 4},
                        "original_user_description": "超椭圆",
                    },
                    source_quote="使用超椭圆作为几何形状",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)

        assert result.errors == [], (
            f"declare_unknown_capability should process without errors; "
            f"got: {result.errors}"
        )

    def test_no_silent_template_mapping(self) -> None:
        """The spec does NOT silently map superellipse to an existing template."""
        spec = make_study_spec()

        # Record original entities.
        original_entity_ids = set(spec.geometry.entities.keys())
        original_cylinder_type = spec.geometry.entities["cylinder"].semantic_type

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="declare_unknown_capability",
                    path="/geometry/entities/superellipse",
                    value={
                        "capability": "geometry_type",
                        "name": "superellipse",
                        "parameters": {"a": 1.0, "b": 0.5, "n": 4},
                    },
                    source_quote="使用超椭圆作为几何形状",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        new_spec = result.new_spec

        # The cylinder entity must NOT have been changed to superellipse
        # or any other type.
        cyl = new_spec.geometry.entities["cylinder"]
        assert cyl.semantic_type == "cylinder_2d", (
            f"Cylinder semantic_type should still be 'cylinder_2d', "
            f"got '{cyl.semantic_type}'"
        )
        assert cyl.semantic_type != "superellipse_2d"
        assert cyl.semantic_type != "cosine_bell_2d"

        # No new entity should have been silently created as a template
        # for the superellipse.
        new_entity_ids = set(new_spec.geometry.entities.keys())
        assert new_entity_ids == original_entity_ids, (
            f"No new entities should be created; "
            f"original: {original_entity_ids}, new: {new_entity_ids}"
        )

    def test_original_semantics_preserved(self) -> None:
        """The cylinder's original_user_semantics is preserved."""
        spec = make_study_spec()

        original_semantics = spec.geometry.entities["cylinder"].original_user_semantics
        assert original_semantics == "cylinder"

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="declare_unknown_capability",
                    path="/geometry/entities/superellipse",
                    value={
                        "capability": "geometry_type",
                        "name": "superellipse",
                    },
                    source_quote="使用超椭圆作为几何形状",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        cyl = result.new_spec.geometry.entities["cylinder"]
        assert cyl.original_user_semantics == original_semantics, (
            f"original_user_semantics should be '{original_semantics}', "
            f"got '{cyl.original_user_semantics}'"
        )

    def test_cylinder_primitive_not_modified(self) -> None:
        """The cylinder's primitive dict is not modified by the unknown-capability
        declaration."""
        spec = make_study_spec()

        original_primitive = spec.geometry.entities["cylinder"].primitive

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="declare_unknown_capability",
                    path="/geometry/entities/superellipse",
                    value={
                        "capability": "geometry_type",
                        "name": "superellipse",
                    },
                    source_quote="使用超椭圆作为几何形状",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        cyl = result.new_spec.geometry.entities["cylinder"]
        assert cyl.primitive == original_primitive, (
            f"Cylinder primitive should not change; "
            f"original: {original_primitive}, new: {cyl.primitive}"
        )

    def test_unknown_capability_does_not_become_cosine_bell(self) -> None:
        """Specifically verify the unknown geometry is NOT mapped to cosine_bell."""
        spec = make_study_spec()

        patch = make_patch(
            spec,
            operations=[
                PatchOperation(
                    op="declare_unknown_capability",
                    path="/geometry/entities/superellipse",
                    value={
                        "capability": "geometry_type",
                        "name": "superellipse",
                    },
                    source_quote="使用超椭圆作为几何形状",
                ),
            ],
            untouched_guarantee=False,
        )

        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert result.new_spec is not None

        # Check all entities — none should have been mapped to cosine_bell_2d.
        for entity_id, entity in result.new_spec.geometry.entities.items():
            assert entity.semantic_type != "cosine_bell_2d", (
                f"Entity '{entity_id}' was silently mapped to cosine_bell_2d"
            )
            assert entity.semantic_type != "cylinder_2d" or entity_id == "cylinder", (
                f"Entity '{entity_id}' was silently mapped to cylinder_2d"
            )
