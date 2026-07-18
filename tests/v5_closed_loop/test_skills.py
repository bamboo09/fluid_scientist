"""Current UTF-8 contract tests for Skill resolution and prompt injection."""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from fluid_scientist.skills.skill_resolver import SkillResolver
from fluid_scientist.skills.skill_resolver import SkillResolutionError


def test_all_manifests_load_and_have_effective_prompt_content() -> None:
    resolver = SkillResolver()
    yaml_files = list((Path(__file__).parents[2] / "data" / "skills").glob("*.yaml"))
    assert len(resolver.all_manifests) == len(yaml_files) >= 10
    for manifest in resolver.all_manifests.values():
        assert manifest.skill_id
        assert manifest.stage
        assert manifest.prompt_fragment.strip()
        assert manifest.enabled


def test_core_model_native_skills_are_readable_utf8() -> None:
    resolver = SkillResolver()
    expected = {
        "fluid.intent_to_spec": "Intent-to-Spec rules",
        "fluid.geometry_reasoning": "Geometry semantic rules",
        "fluid.physics_derivation": "Physics derivation rules",
        "fluid.spatial_reasoning": "Spatial reasoning rules",
    }
    for skill_id, marker in expected.items():
        manifest = resolver.get_manifest(skill_id)
        assert manifest is not None
        assert marker in manifest.prompt_fragment
        assert "�" not in manifest.name
        assert "�" not in manifest.prompt_fragment


def test_chinese_trapezoid_case_selects_semantic_skills() -> None:
    resolver = SkillResolver()
    selected = resolver.select_skills(
        user_text="二维圆柱绕流，在圆柱下方有梯形凸起，Re=200"
    )
    ids = [item.skill_id for item in selected]
    assert "fluid.intent_to_spec" in ids
    assert "fluid.geometry_reasoning" in ids
    assert "fluid.spatial_reasoning" in ids
    assert "fluid.physics_derivation" in ids
    assert ids == [item.skill_id for item in sorted(selected, key=lambda item: item.priority, reverse=True)]


def test_geometry_type_selection_is_not_keyword_dependent() -> None:
    resolver = SkillResolver()
    ids = [item.skill_id for item in resolver.select_skills(geometry_types=["cosine_bell"])]
    assert "fluid.geometry_reasoning" in ids


def test_prompt_injection_contains_actual_skill_documents() -> None:
    resolver = SkillResolver()
    injection = resolver.build_prompt_injection(
        user_text="二维圆柱绕流，在圆柱下方有梯形凸起，Re=200"
    )
    assert "Geometry semantic rules" in injection
    assert "Spatial reasoning rules" in injection
    assert "Physics derivation rules" in injection
    assert "Intent-to-Spec rules" in injection
    assert "priority=" in injection


def test_irrelevant_input_has_no_selected_skill_or_injection() -> None:
    resolver = SkillResolver()
    assert resolver.select_skills(user_text="zzzqqqxxx_no_match") == []
    assert resolver.build_prompt_injection(user_text="zzzqqqxxx_no_match") == ""


def test_disabled_skill_is_not_selected_or_injected(tmp_path: Path) -> None:
    manifest = {
        "skill_id": "test.disabled",
        "name": "Disabled",
        "description": "must not run",
        "stage": "intent",
        "prompt_fragment": "UNIQUE_DISABLED_FRAGMENT",
        "selection_keywords": ["trigger"],
        "enabled": False,
    }
    (tmp_path / "disabled.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )
    resolver = SkillResolver(skills_dir=tmp_path)
    assert resolver.select_skills(user_text="trigger") == []
    assert "UNIQUE_DISABLED_FRAGMENT" not in resolver.build_prompt_injection(user_text="trigger")


def test_geometry_compiler_hooks_are_selected_deterministically() -> None:
    resolver = SkillResolver()
    hooks = resolver.get_compiler_hooks(geometry_types=["triangle"], stage="")
    assert hooks["enforce_semantic_type"] == "strict"
    assert hooks["prevent_geometry_substitution"] == "true"


def test_reload_reflects_manifest_change(tmp_path: Path) -> None:
    path = tmp_path / "reload.yaml"
    base = {
        "skill_id": "test.reload",
        "name": "Reload",
        "description": "reload test",
        "stage": "intent",
        "prompt_fragment": "version one",
        "selection_keywords": ["reload"],
        "enabled": True,
    }
    path.write_text(yaml.safe_dump(base), encoding="utf-8")
    resolver = SkillResolver(skills_dir=tmp_path)
    assert resolver.get_manifest("test.reload").prompt_fragment == "version one"
    base["prompt_fragment"] = "version two"
    path.write_text(yaml.safe_dump(base), encoding="utf-8")
    resolver.reload()
    assert resolver.get_manifest("test.reload").prompt_fragment == "version two"


def test_skill_missing_fails_closed() -> None:
    resolver = SkillResolver()
    with pytest.raises(SkillResolutionError, match="SKILL_MISSING"):
        resolver.resolve_documents(["fluid.not_installed"], user_text="圆柱绕流")


def test_wrong_skill_fails_closed() -> None:
    resolver = SkillResolver()
    with pytest.raises(SkillResolutionError, match="WRONG_SKILL"):
        resolver.resolve_documents(
            ["fluid.error_diagnosis"], user_text="创建二维圆柱绕流仿真"
        )
