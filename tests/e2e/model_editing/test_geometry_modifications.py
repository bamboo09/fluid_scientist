"""14 geometry modification tests proving the generic PatchEngine handles
all geometry edits through the same schema-driven path.

Covers the plan's Phase 17 geometry test matrix items 13-26.
"""
from __future__ import annotations

from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import SimulationStudySpec

from .conftest import make_patch, make_study_spec


def _apply(spec: SimulationStudySpec, ops: list[PatchOperation]):
    """Helper: apply a patch and return the new spec, asserting no errors."""
    patch = make_patch(spec, operations=ops)
    engine = PatchEngine()
    result = engine.process_patch(patch, spec)
    assert result.errors == [], f"Unexpected errors: {result.errors}"
    assert result.new_spec is not None, "new_spec must be populated"
    return result


def _spec_with_obstacle() -> SimulationStudySpec:
    """Build a spec that already has a 'wall_obstacle' triangle entity."""
    spec = make_study_spec()
    spec_dict = spec.model_dump()
    spec_dict["geometry"]["entities"]["wall_obstacle"] = {
        "entity_id": "wall_obstacle",
        "semantic_type": "triangle_2d",
        "primitive": {"type": "triangle", "base_width": 0.1, "height": 0.05},
        "polygon_vertices": None,
        "original_user_semantics": "三角障碍",
        "placement": {"x": {"value": 4.0, "unit": "m", "status": "user_explicit",
                             "source_turn_ids": ["t0"], "confidence": 0.9,
                             "derivation_id": None, "last_modified_by_patch": None},
                      "y": {"value": 0.0, "unit": "m", "status": "user_explicit",
                            "source_turn_ids": ["t0"], "confidence": 0.9,
                            "derivation_id": None, "last_modified_by_patch": None},
                      "orientation": None, "attachment": "bottom_wall"},
    }
    return SimulationStudySpec(**spec_dict)


# ---------------------------------------------------------------------------
# 13-15: entity type changes
# ---------------------------------------------------------------------------

class TestAddAndChangeGeometry:
    def test_add_triangle_obstacle(self) -> None:
        spec = make_study_spec()
        assert "wall_obstacle" not in spec.geometry.entities
        result = _apply(spec, [
            PatchOperation(
                op="add",
                path="/geometry/entities/wall_obstacle",
                value={
                    "entity_id": "wall_obstacle",
                    "semantic_type": "triangle_2d",
                    "primitive": {"type": "triangle", "base_width": 0.1, "height": 0.05},
                    "polygon_vertices": None,
                    "original_user_semantics": "三角障碍",
                    "placement": None,
                },
                source_quote="增加三角障碍",
                confidence=0.95,
            ),
        ])
        assert "wall_obstacle" in result.new_spec.geometry.entities
        ent = result.new_spec.geometry.entities["wall_obstacle"]
        assert ent.semantic_type == "triangle_2d"
        # cylinder unchanged
        assert "cylinder" in result.new_spec.geometry.entities

    def test_triangle_to_rectangle(self) -> None:
        spec = _spec_with_obstacle()
        result = _apply(spec, [
            PatchOperation(
                op="replace",
                path="/geometry/entities/wall_obstacle/primitive",
                value={"type": "rectangle", "width": 0.1, "height": 0.05},
                source_quote="三角改矩形",
                confidence=0.99,
            ),
        ])
        ent = result.new_spec.geometry.entities["wall_obstacle"]
        assert ent.primitive["type"] == "rectangle"
        assert ent.semantic_type == "triangle_2d"  # semantic type preserved
        # cylinder unchanged
        cyl = result.new_spec.geometry.entities["cylinder"]
        assert cyl.semantic_type == "cylinder_2d"

    def test_rectangle_to_cosine_bell(self) -> None:
        spec = _spec_with_obstacle()
        # First change to rectangle
        spec_dict = spec.model_dump()
        spec_dict["geometry"]["entities"]["wall_obstacle"]["primitive"] = {
            "type": "rectangle", "width": 0.1, "height": 0.05
        }
        spec = SimulationStudySpec(**spec_dict)
        # Now change to cosine_bell
        result = _apply(spec, [
            PatchOperation(
                op="replace",
                path="/geometry/entities/wall_obstacle/primitive",
                value={"type": "cosine_bell", "amplitude": 0.05, "width": 0.1},
                source_quote="矩形改正弦凸起",
                confidence=0.95,
            ),
        ])
        ent = result.new_spec.geometry.entities["wall_obstacle"]
        assert ent.primitive["type"] == "cosine_bell"


# ---------------------------------------------------------------------------
# 16-19: entity placement and add/remove
# ---------------------------------------------------------------------------

class TestEntityPlacement:
    def test_move_cylinder_up(self) -> None:
        spec = make_study_spec()
        assert spec.geometry.entities["cylinder"].placement.y.value == 4.0
        result = _apply(spec, [
            PatchOperation(op="replace", path="/geometry/entities/cylinder/placement/y",
                           value=4.5, source_quote="圆柱上移", confidence=0.99),
        ])
        assert result.new_spec.geometry.entities["cylinder"].placement.y.value == 4.5
        # x unchanged
        assert result.new_spec.geometry.entities["cylinder"].placement.x.value == 4.0

    def test_move_cylinder_sideways(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(op="replace", path="/geometry/entities/cylinder/placement/x",
                           value=5.0, source_quote="圆柱横向移动", confidence=0.99),
        ])
        assert result.new_spec.geometry.entities["cylinder"].placement.x.value == 5.0
        # y unchanged
        assert result.new_spec.geometry.entities["cylinder"].placement.y.value == 4.0

    def test_add_second_cylinder(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(
                op="add",
                path="/geometry/entities/cylinder_2",
                value={
                    "entity_id": "cylinder_2",
                    "semantic_type": "cylinder_2d",
                    "primitive": {"type": "circle", "radius": 0.15, "diameter": 0.3},
                    "polygon_vertices": None,
                    "original_user_semantics": "第二个圆柱",
                    "placement": {"x": {"value": 6.0, "unit": "m", "status": "user_explicit",
                                         "source_turn_ids": ["t1"], "confidence": 0.9,
                                         "derivation_id": None, "last_modified_by_patch": None},
                                  "y": {"value": 4.0, "unit": "m", "status": "user_explicit",
                                        "source_turn_ids": ["t1"], "confidence": 0.9,
                                        "derivation_id": None, "last_modified_by_patch": None},
                                  "orientation": None, "attachment": None},
                },
                source_quote="增加第二个圆柱",
                confidence=0.95,
            ),
        ])
        assert "cylinder_2" in result.new_spec.geometry.entities
        assert "cylinder" in result.new_spec.geometry.entities  # original preserved

    def test_remove_obstacle(self) -> None:
        spec = _spec_with_obstacle()
        assert "wall_obstacle" in spec.geometry.entities
        result = _apply(spec, [
            PatchOperation(op="remove", path="/geometry/entities/wall_obstacle",
                           source_quote="删除障碍", confidence=0.95),
        ])
        assert "wall_obstacle" not in result.new_spec.geometry.entities
        assert "cylinder" in result.new_spec.geometry.entities  # preserved


# ---------------------------------------------------------------------------
# 20-22: parameter changes and new shapes
# ---------------------------------------------------------------------------

class TestEntityParameters:
    def test_change_obstacle_width(self) -> None:
        spec = _spec_with_obstacle()
        result = _apply(spec, [
            PatchOperation(op="merge",
                           path="/geometry/entities/wall_obstacle/primitive",
                           value={"base_width": 0.2},
                           source_quote="改障碍宽度", confidence=0.95),
        ])
        ent = result.new_spec.geometry.entities["wall_obstacle"]
        assert ent.primitive["base_width"] == 0.2
        # height unchanged
        assert ent.primitive["height"] == 0.05

    def test_change_obstacle_height(self) -> None:
        spec = _spec_with_obstacle()
        result = _apply(spec, [
            PatchOperation(op="merge",
                           path="/geometry/entities/wall_obstacle/primitive",
                           value={"height": 0.08},
                           source_quote="改障碍高度", confidence=0.95),
        ])
        ent = result.new_spec.geometry.entities["wall_obstacle"]
        assert ent.primitive["height"] == 0.08
        # width unchanged
        assert ent.primitive["base_width"] == 0.1

    def test_add_custom_polygon(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(
                op="add",
                path="/geometry/entities/custom_poly_1",
                value={
                    "entity_id": "custom_poly_1",
                    "semantic_type": "custom_polygon_2d",
                    "primitive": None,
                    "polygon_vertices": [
                        {"x": 3.0, "y": 0.0},
                        {"x": 3.5, "y": 0.0},
                        {"x": 3.25, "y": 0.3},
                    ],
                    "original_user_semantics": "自定义多边形",
                    "placement": None,
                },
                source_quote="新增polygon",
                confidence=0.95,
            ),
        ])
        ent = result.new_spec.geometry.entities["custom_poly_1"]
        assert ent.semantic_type == "custom_polygon_2d"
        assert len(ent.polygon_vertices) == 3


# ---------------------------------------------------------------------------
# 23-24: unknown capability and imported geometry
# ---------------------------------------------------------------------------

class TestUnknownAndImported:
    def test_superellipse_unknown_capability(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(
                op="declare_unknown_capability",
                path="/geometry/entities/unknown_superellipse",
                value={
                    "capability_key": "geometry.superellipse",
                    "original_semantics": "超椭圆障碍",
                    "requested_parameters": {"a": 0.1, "b": 0.05, "n": 4},
                },
                source_quote="superellipse",
                confidence=0.98,
            ),
        ])
        # The spec should still be valid, the unknown capability is recorded
        assert result.new_spec is not None
        # Original cylinder preserved
        assert "cylinder" in result.new_spec.geometry.entities

    def test_import_stl(self) -> None:
        spec = make_study_spec()
        result = _apply(spec, [
            PatchOperation(
                op="add",
                path="/geometry/entities/imported_stl_1",
                value={
                    "entity_id": "imported_stl_1",
                    "semantic_type": "imported_stl",
                    "primitive": {"type": "imported", "file": "obstacle.stl", "format": "STL"},
                    "polygon_vertices": None,
                    "original_user_semantics": "导入STL",
                    "placement": None,
                },
                source_quote="导入STL",
                confidence=0.95,
            ),
        ])
        ent = result.new_spec.geometry.entities["imported_stl_1"]
        assert ent.primitive["type"] == "imported"
        assert ent.primitive["file"] == "obstacle.stl"


# ---------------------------------------------------------------------------
# 25-26: spatial relations and conflicts
# ---------------------------------------------------------------------------

class TestRelations:
    def test_set_relation_below(self) -> None:
        spec = _spec_with_obstacle()
        # Add cylinder to the spec if not present
        assert "cylinder" in spec.geometry.entities
        result = _apply(spec, [
            PatchOperation(
                op="append_unique",
                path="/geometry/relations/-",
                value={
                    "relation_id": "rel_1",
                    "type": "aligned_below",
                    "subject_id": "wall_obstacle",
                    "object_id": "cylinder",
                    "parameters": {},
                },
                source_quote="正下方",
                confidence=0.95,
            ),
        ])
        # The relation should be stored
        assert len(result.new_spec.geometry.relations) >= 1
        rel = result.new_spec.geometry.relations[-1]
        assert rel.type == "aligned_below"
        assert rel.subject_id == "wall_obstacle"
        assert rel.object_id == "cylinder"

    def test_conflict_center_vs_explicit_coord(self) -> None:
        """When user says '流场中央' but also gives explicit x=3.0, detect conflict."""
        spec = make_study_spec()
        # The cylinder has explicit x=4.0 in a 12m domain
        # "流场中央" would mean x=6.0 (domain_length/2)
        # This is a conflict that should be resolved via clarification
        patch = make_patch(
            spec,
            operations=[],
            clarifications=[{
                "clarification_id": "geo_conflict_1",
                "question": "圆柱位置冲突：'流场中央'对应x=6.0m，但之前指定x=4.0m。使用哪个？",
                "alternatives": [
                    {"label": "使用流场中央 x=6.0m", "operations": [
                        {"op": "replace", "path": "/geometry/entities/cylinder/placement/x",
                         "value": 6.0, "source_quote": "流场中央", "confidence": 0.9}
                    ]},
                    {"label": "保持 x=4.0m", "operations": []},
                ],
                "affected_paths": ["/geometry/entities/cylinder/placement/x"],
                "blocking": True,
            }],
        )
        engine = PatchEngine()
        result = engine.process_patch(patch, spec)
        assert len(result.clarifications) > 0

        # After user selects "流场中央", apply the patch
        alt_patch = make_patch(spec, operations=[
            PatchOperation(op="replace", path="/geometry/entities/cylinder/placement/x",
                           value=6.0, source_quote="流场中央", confidence=0.99),
        ])
        result2 = engine.process_patch(alt_patch, spec)
        assert result2.errors == []
        assert result2.new_spec.geometry.entities["cylinder"].placement.x.value == 6.0
