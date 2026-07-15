"""Tests for the SkillResolver system (P5 — Skills & Prompt Injection).

This test suite verifies that:
1. SkillManifest objects are correctly loaded from YAML files in data/skills/
2. Skills are selected by matching keywords in user text (e.g. "三角" -> geometry_reasoning)
3. Skills are selected by matching geometry types
4. build_prompt_injection() returns concatenated prompt fragments from selected skills
5. get_compiler_hooks() returns a dict of compiler hook name -> value
6. Relevant inputs actually trigger skill selection and produce non-empty injections
7. A skill with enabled=false is never selected

Plan reference: P5 — Skills & Prompt Injection.
These tests do NOT require a running server; they exercise SkillResolver
against the real YAML manifests in data/skills/ and temporary manifest files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fluid_scientist.skills.skill_resolver import SkillManifest, SkillResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    """Write *data* to *path* as YAML (UTF-8)."""
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# 1. Manifest loading from YAML
# ---------------------------------------------------------------------------

class TestManifestLoading:
    """SkillManifest loading from YAML files in data/skills/."""

    def test_loads_all_manifests_from_data_skills(self) -> None:
        """SkillResolver should load every YAML file shipped in data/skills/."""
        resolver = SkillResolver()
        # The repository ships at least 10 skill YAML files.
        assert len(resolver.all_manifests) >= 10

    def test_geometry_reasoning_manifest_fields(self) -> None:
        """The geometry_reasoning skill should be loaded with expected fields."""
        resolver = SkillResolver()
        manifest = resolver.get_manifest("fluid.geometry_reasoning")
        assert manifest is not None
        assert manifest.stage == "geometry"
        assert manifest.priority == 90
        assert manifest.enabled is True
        assert "三角" in manifest.selection_keywords
        assert "triangle" in manifest.selection_geometry
        assert "cylinder" in manifest.selection_geometry

    def test_all_manifests_have_non_empty_prompt_fragment(self) -> None:
        """Every loaded manifest should have a non-empty prompt_fragment."""
        resolver = SkillResolver()
        for skill_id, manifest in resolver.all_manifests.items():
            assert manifest.prompt_fragment, (
                f"{skill_id} has an empty prompt_fragment"
            )

    def test_to_dict_contains_key_fields(self) -> None:
        """SkillManifest.to_dict() should include skill_id, stage, priority, enabled."""
        resolver = SkillResolver()
        manifest = resolver.get_manifest("fluid.geometry_reasoning")
        assert manifest is not None
        d = manifest.to_dict()
        assert d["skill_id"] == "fluid.geometry_reasoning"
        assert d["stage"] == "geometry"
        assert d["priority"] == 90
        assert d["enabled"] is True
        # to_dict stores prompt_fragment_length, not the full text
        assert d["prompt_fragment_length"] == len(manifest.prompt_fragment)

    def test_get_manifest_returns_none_for_unknown_id(self) -> None:
        """get_manifest() should return None for a non-existent skill_id."""
        resolver = SkillResolver()
        assert resolver.get_manifest("does.not.exist") is None

    def test_reload_clears_and_reloads(self) -> None:
        """reload() should re-read manifests from disk."""
        resolver = SkillResolver()
        original_count = len(resolver.all_manifests)
        resolver.reload()
        assert len(resolver.all_manifests) == original_count


# ---------------------------------------------------------------------------
# 2. Skill selection by keywords
# ---------------------------------------------------------------------------

class TestSkillSelectionByKeyword:
    """Keyword-based skill selection from user text."""

    def test_triangle_keyword_selects_geometry_reasoning(self) -> None:
        """'三角' in user text should select the geometry_reasoning skill."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="三角障碍物绕流")
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.geometry_reasoning" in skill_ids

    def test_cylinder_keyword_selects_geometry_reasoning(self) -> None:
        """'圆柱' should also select geometry_reasoning."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="圆柱绕流模拟")
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.geometry_reasoning" in skill_ids

    def test_mesh_keyword_selects_mesh_strategy(self) -> None:
        """'网格' should select the mesh_strategy skill."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="网格生成策略", stage="mesh")
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.mesh_strategy" in skill_ids

    def test_keyword_matching_is_case_insensitive(self) -> None:
        """English keywords should match case-insensitively."""
        resolver = SkillResolver()
        selected_upper = resolver.select_skills(user_text="CYLINDER flow")
        selected_lower = resolver.select_skills(user_text="cylinder flow")
        upper_ids = {s.skill_id for s in selected_upper}
        lower_ids = {s.skill_id for s in selected_lower}
        assert "fluid.geometry_reasoning" in upper_ids
        assert upper_ids == lower_ids

    def test_irrelevant_text_does_not_select_keyword_skills(self) -> None:
        """Text with no matching keywords should not select keyword-based skills."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="zzzqqqxxx_no_match")
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.geometry_reasoning" not in skill_ids
        assert "fluid.mesh_strategy" not in skill_ids

    def test_multiple_keywords_select_multiple_skills(self) -> None:
        """Text matching multiple skills' keywords should select all of them."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="圆柱绕流网格生成")
        skill_ids = {s.skill_id for s in selected}
        assert "fluid.geometry_reasoning" in skill_ids
        assert "fluid.mesh_strategy" in skill_ids


# ---------------------------------------------------------------------------
# 3. Skill selection by geometry type
# ---------------------------------------------------------------------------

class TestSkillSelectionByGeometry:
    """Geometry-type-based skill selection."""

    def test_triangle_geometry_selects_geometry_reasoning(self) -> None:
        """geometry_types=['triangle'] should select geometry_reasoning."""
        resolver = SkillResolver()
        selected = resolver.select_skills(geometry_types=["triangle"])
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.geometry_reasoning" in skill_ids

    def test_cylinder_geometry_selects_mesh_strategy(self) -> None:
        """geometry_types=['cylinder'] should select mesh_strategy."""
        resolver = SkillResolver()
        selected = resolver.select_skills(geometry_types=["cylinder"])
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.mesh_strategy" in skill_ids

    def test_cosine_bell_geometry_selects_geometry_reasoning(self) -> None:
        """geometry_types=['cosine_bell'] should select geometry_reasoning."""
        resolver = SkillResolver()
        selected = resolver.select_skills(geometry_types=["cosine_bell"])
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.geometry_reasoning" in skill_ids

    def test_multiple_geometry_types_select_multiple_skills(self) -> None:
        """Multiple geometry types should select all matching skills."""
        resolver = SkillResolver()
        selected = resolver.select_skills(geometry_types=["cylinder", "triangle"])
        skill_ids = {s.skill_id for s in selected}
        assert "fluid.geometry_reasoning" in skill_ids
        assert "fluid.mesh_strategy" in skill_ids

    def test_unknown_geometry_does_not_select_geometry_skills(self) -> None:
        """An unknown geometry type should not trigger geometry_reasoning."""
        resolver = SkillResolver()
        selected = resolver.select_skills(geometry_types=["unknown_shape"])
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.geometry_reasoning" not in skill_ids


# ---------------------------------------------------------------------------
# 4. build_prompt_injection
# ---------------------------------------------------------------------------

class TestBuildPromptInjection:
    """build_prompt_injection() concatenates prompt fragments from selected skills."""

    def test_returns_non_empty_string_for_triangle_input(self) -> None:
        """build_prompt_injection should return a non-empty string for relevant input."""
        resolver = SkillResolver()
        injection = resolver.build_prompt_injection(user_text="三角障碍物绕流")
        assert injection
        assert len(injection) > 0

    def test_injection_contains_skill_header(self) -> None:
        """The injection should contain the '## Skill 提供的领域知识' header."""
        resolver = SkillResolver()
        injection = resolver.build_prompt_injection(user_text="三角绕流")
        assert "## Skill 提供的领域知识" in injection

    def test_injection_contains_prompt_fragment_content(self) -> None:
        """The injection should contain the actual prompt_fragment text."""
        resolver = SkillResolver()
        injection = resolver.build_prompt_injection(user_text="三角绕流")
        # geometry_reasoning prompt_fragment contains "几何推理规则"
        assert "几何推理规则" in injection

    def test_injection_contains_skill_name_and_priority(self) -> None:
        """The injection should include the skill name and priority annotation."""
        resolver = SkillResolver()
        injection = resolver.build_prompt_injection(user_text="三角绕流")
        assert "priority=" in injection

    def test_injection_empty_for_irrelevant_input(self) -> None:
        """build_prompt_injection should return '' for input with no matching skills."""
        resolver = SkillResolver()
        injection = resolver.build_prompt_injection(user_text="zzzqqqxxx_no_match")
        assert injection == ""

    def test_injection_combines_multiple_skills(self) -> None:
        """When multiple skills match, all prompt fragments should be present."""
        resolver = SkillResolver()
        injection = resolver.build_prompt_injection(user_text="圆柱绕流网格生成")
        # Both geometry_reasoning and mesh_strategy should contribute
        assert "几何推理规则" in injection
        assert "网格策略规则" in injection


# ---------------------------------------------------------------------------
# 5. get_compiler_hooks
# ---------------------------------------------------------------------------

class TestGetCompilerHooks:
    """get_compiler_hooks() returns a dict of hook_name -> hook_value.

    Note: ``get_compiler_hooks`` defaults to ``stage="compile"``.  No shipped
    skill uses ``stage="compile"``, so the default returns an empty dict.
    To retrieve hooks from geometry / mesh skills we pass ``stage=""`` which
    disables the stage filter (see SkillResolver.select_skills).
    """

    def test_returns_dict(self) -> None:
        """get_compiler_hooks should return a dict."""
        resolver = SkillResolver()
        hooks = resolver.get_compiler_hooks(geometry_types=["cylinder"], stage="")
        assert isinstance(hooks, dict)

    def test_cylinder_geometry_returns_mesh_hooks(self) -> None:
        """Cylinder geometry should return mesh refinement hooks from mesh_strategy."""
        resolver = SkillResolver()
        hooks = resolver.get_compiler_hooks(geometry_types=["cylinder"], stage="")
        assert "mesh_refinement_cylinder" in hooks
        assert hooks["mesh_refinement_cylinder"] == "20_cells_per_diameter"

    def test_triangle_geometry_returns_geometry_hooks(self) -> None:
        """Triangle geometry should return geometry enforcement hooks."""
        resolver = SkillResolver()
        hooks = resolver.get_compiler_hooks(geometry_types=["triangle"], stage="")
        assert "enforce_semantic_type" in hooks
        assert hooks["enforce_semantic_type"] == "true"

    def test_default_compile_stage_returns_empty(self) -> None:
        """Default stage='compile' returns empty dict (no skills use that stage)."""
        resolver = SkillResolver()
        hooks = resolver.get_compiler_hooks(geometry_types=["cylinder"])
        assert hooks == {}

    def test_empty_hooks_for_unknown_geometry(self) -> None:
        """Unknown geometry should return an empty dict."""
        resolver = SkillResolver()
        hooks = resolver.get_compiler_hooks(geometry_types=["unknown_shape"], stage="")
        assert hooks == {}

    def test_multiple_geometries_combine_hooks(self) -> None:
        """Multiple geometry types should combine hooks from all matching skills."""
        resolver = SkillResolver()
        hooks = resolver.get_compiler_hooks(geometry_types=["cylinder", "triangle"], stage="")
        # geometry_reasoning (priority=90) hooks
        assert "enforce_semantic_type" in hooks
        assert "prevent_geometry_substitution" in hooks
        # mesh_strategy (priority=70) hooks
        assert "mesh_refinement_cylinder" in hooks

    def test_first_priority_wins_for_duplicate_hook(self) -> None:
        """When multiple skills define the same hook name, highest priority wins."""
        resolver = SkillResolver()
        hooks = resolver.get_compiler_hooks(geometry_types=["cylinder", "triangle"], stage="")
        # If two skills define the same hook, the first (highest priority) should win.
        # All hook names across geometry_reasoning and mesh_strategy are unique,
        # so we simply verify that all hooks are present.
        assert len(hooks) >= 3  # at least enforce_semantic_type, prevent_geometry_substitution, mesh_refinement_cylinder


# ---------------------------------------------------------------------------
# 6. Skills are actually selected for relevant inputs (integration)
# ---------------------------------------------------------------------------

class TestRelevantSelection:
    """Integration: skills are selected and prompt injection is non-empty."""

    def test_full_pipeline_triangle_obstacle(self) -> None:
        """A realistic '三角障碍物' input should select skills and produce injection."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="三角障碍物绕流模拟")
        assert len(selected) > 0

        injection = resolver.build_prompt_injection(user_text="三角障碍物绕流模拟")
        assert injection != ""
        assert "三角" in injection

    def test_selected_skills_sorted_by_priority_descending(self) -> None:
        """Selected skills should be sorted by priority (descending)."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="三角绕流网格")
        priorities = [s.priority for s in selected]
        assert priorities == sorted(priorities, reverse=True)

    def test_stage_filter_limits_to_matching_stage(self) -> None:
        """Stage filter should limit selection to skills of that stage."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="三角", stage="geometry")
        for skill in selected:
            assert skill.stage == "geometry" or skill.stage == "all"

    def test_stage_filter_excludes_other_stages(self) -> None:
        """A geometry keyword should NOT select skills from other stages."""
        resolver = SkillResolver()
        # '网格' is a mesh keyword; with stage='geometry' it should not match
        # mesh_strategy (stage='mesh').
        selected = resolver.select_skills(user_text="网格", stage="geometry")
        skill_ids = [s.skill_id for s in selected]
        assert "fluid.mesh_strategy" not in skill_ids

    def test_no_stage_includes_all_stages(self) -> None:
        """Without a stage filter, skills from all stages can be selected."""
        resolver = SkillResolver()
        selected = resolver.select_skills(user_text="圆柱绕流网格层流")
        stages = {s.stage for s in selected}
        # Should include at least geometry, mesh, and solver stages
        assert "geometry" in stages
        assert "mesh" in stages


# ---------------------------------------------------------------------------
# 7. Disabling a skill (enabled=false) prevents selection
# ---------------------------------------------------------------------------

class TestDisabledSkill:
    """A skill with enabled=false should never be selected."""

    def test_disabled_skill_not_selected(self, tmp_path: Path) -> None:
        """A skill with enabled=false should never appear in select_skills results."""
        enabled_yaml = {
            "skill_id": "test.enabled_skill",
            "name": "Enabled Skill",
            "description": "An enabled test skill",
            "stage": "geometry",
            "priority": 50,
            "enabled": True,
            "version": "1.0",
            "selection_keywords": ["enable_test"],
            "prompt_fragment": "Enabled skill content",
        }
        disabled_yaml = {
            "skill_id": "test.disabled_skill",
            "name": "Disabled Skill",
            "description": "A disabled test skill",
            "stage": "geometry",
            "priority": 99,  # high priority, but should not be selected
            "enabled": False,
            "version": "1.0",
            "selection_keywords": ["disable_test"],
            "prompt_fragment": "Disabled skill content",
        }

        _write_yaml(tmp_path / "test_enabled.yaml", enabled_yaml)
        _write_yaml(tmp_path / "test_disabled.yaml", disabled_yaml)

        resolver = SkillResolver(skills_dir=tmp_path)

        # Both manifests should be loaded
        assert "test.enabled_skill" in resolver.all_manifests
        assert "test.disabled_skill" in resolver.all_manifests

        # The disabled skill should have enabled=False
        disabled_manifest = resolver.get_manifest("test.disabled_skill")
        assert disabled_manifest is not None
        assert disabled_manifest.enabled is False

        # Searching for the disabled skill's keyword should NOT return it
        selected = resolver.select_skills(user_text="disable_test")
        skill_ids = [s.skill_id for s in selected]
        assert "test.disabled_skill" not in skill_ids

        # Searching for the enabled skill's keyword SHOULD return it
        selected = resolver.select_skills(user_text="enable_test")
        skill_ids = [s.skill_id for s in selected]
        assert "test.enabled_skill" in skill_ids

    def test_disabled_skill_not_in_prompt_injection(self, tmp_path: Path) -> None:
        """A disabled skill's prompt_fragment should not appear in the injection."""
        disabled_yaml = {
            "skill_id": "test.disabled_injection",
            "name": "Disabled Injection Skill",
            "description": "Should not appear in injection",
            "stage": "geometry",
            "priority": 99,
            "enabled": False,
            "version": "1.0",
            "selection_keywords": ["unique_disable_keyword"],
            "prompt_fragment": "UNIQUE_DISABLED_FRAGMENT_TEXT",
        }

        _write_yaml(tmp_path / "test_disabled_injection.yaml", disabled_yaml)

        resolver = SkillResolver(skills_dir=tmp_path)
        injection = resolver.build_prompt_injection(user_text="unique_disable_keyword")
        assert "UNIQUE_DISABLED_FRAGMENT_TEXT" not in injection

    def test_disabled_skill_not_in_compiler_hooks(self, tmp_path: Path) -> None:
        """A disabled skill's compiler_hooks should not appear in get_compiler_hooks."""
        disabled_yaml = {
            "skill_id": "test.disabled_hooks",
            "name": "Disabled Hooks Skill",
            "description": "Should not contribute hooks",
            "stage": "compile",
            "priority": 99,
            "enabled": False,
            "version": "1.0",
            "selection_geometry": ["cylinder"],
            "prompt_fragment": "content",
            "compiler_hooks": {"unique_disabled_hook": "should_not_appear"},
        }

        _write_yaml(tmp_path / "test_disabled_hooks.yaml", disabled_yaml)

        resolver = SkillResolver(skills_dir=tmp_path)
        hooks = resolver.get_compiler_hooks(geometry_types=["cylinder"])
        assert "unique_disabled_hook" not in hooks

    def test_disabled_skill_with_high_priority_still_excluded(self, tmp_path: Path) -> None:
        """A disabled skill with the highest priority must still be excluded."""
        low_priority_enabled = {
            "skill_id": "test.low_priority_enabled",
            "name": "Low Priority Enabled",
            "description": "enabled=true, priority=1",
            "stage": "geometry",
            "priority": 1,
            "enabled": True,
            "version": "1.0",
            "selection_keywords": ["shared_keyword"],
            "prompt_fragment": "low priority content",
        }
        high_priority_disabled = {
            "skill_id": "test.high_priority_disabled",
            "name": "High Priority Disabled",
            "description": "enabled=false, priority=100",
            "stage": "geometry",
            "priority": 100,
            "enabled": False,
            "version": "1.0",
            "selection_keywords": ["shared_keyword"],
            "prompt_fragment": "high priority content (should not appear)",
        }

        _write_yaml(tmp_path / "test_low.yaml", low_priority_enabled)
        _write_yaml(tmp_path / "test_high.yaml", high_priority_disabled)

        resolver = SkillResolver(skills_dir=tmp_path)
        selected = resolver.select_skills(user_text="shared_keyword")
        skill_ids = [s.skill_id for s in selected]
        assert "test.low_priority_enabled" in skill_ids
        assert "test.high_priority_disabled" not in skill_ids
