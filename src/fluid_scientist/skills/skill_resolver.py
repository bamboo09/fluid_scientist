"""Skill manifest system — defines, loads, and resolves skills.

A Skill is a YAML manifest that:
1. Declares what stage it affects (intent, geometry, physics, mesh, solver, postprocess, report, repair)
2. Contains a prompt_fragment that gets injected into the LLM context
3. Optionally declares compiler_hooks that affect OpenFOAM case generation
4. Has selection criteria (keywords, geometry types, physics models)

Unlike the old SkillExecutor (function wrapper + audit recorder),
this system ensures skills actually affect LLM prompts and compiler behavior.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class SkillResolutionError(RuntimeError):
    """Raised when requested Skill context is missing or irrelevant."""

# Default skills directory
SKILLS_DIR = Path(__file__).resolve().parents[3] / "data" / "skills"


@dataclass
class SkillManifest:
    """A skill manifest loaded from YAML.

    Attributes:
        skill_id: Unique identifier (e.g. "fluid.geometry_reasoning")
        name: Human-readable name
        description: What the skill does
        stage: Pipeline stage (intent, geometry, physics, mesh, solver, postprocess, report, repair)
        prompt_fragment: Text injected into LLM prompt
        selection_keywords: Keywords that trigger skill selection
        selection_geometry: Geometry types that trigger selection
        compiler_hooks: Named hooks into the compiler
        priority: Higher = more important (0-100)
        enabled: Whether the skill is active
        version: Manifest version
    """
    skill_id: str
    name: str
    description: str
    stage: str
    prompt_fragment: str = ""
    selection_keywords: list[str] = field(default_factory=list)
    selection_geometry: list[str] = field(default_factory=list)
    compiler_hooks: dict[str, str] = field(default_factory=dict)
    priority: int = 50
    enabled: bool = True
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "stage": self.stage,
            "prompt_fragment_length": len(self.prompt_fragment),
            "selection_keywords": self.selection_keywords,
            "selection_geometry": self.selection_geometry,
            "compiler_hooks": self.compiler_hooks,
            "priority": self.priority,
            "enabled": self.enabled,
            "version": self.version,
        }


class SkillResolver:
    """Loads skill manifests and selects relevant skills for a given context.

    Selection logic:
    1. Load all manifests from skills directory at startup
    2. For a given user text + spec, match against selection criteria
    3. Return selected skills sorted by priority
    4. Selected skills' prompt_fragments are injected into LLM prompts
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir or SKILLS_DIR
        self._manifests: dict[str, SkillManifest] = {}
        self._load_manifests()

    def _load_manifests(self) -> None:
        """Load all skill manifests from the skills directory."""
        if not self._skills_dir.exists():
            logger.warning("Skills directory does not exist: %s", self._skills_dir)
            return

        for yaml_file in self._skills_dir.glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "skill_id" in data:
                    manifest = SkillManifest(
                        skill_id=data["skill_id"],
                        name=data.get("name", data["skill_id"]),
                        description=data.get("description", ""),
                        stage=data.get("stage", "unknown"),
                        prompt_fragment=data.get("prompt_fragment", ""),
                        selection_keywords=data.get("selection_keywords", []),
                        selection_geometry=data.get("selection_geometry", []),
                        compiler_hooks=data.get("compiler_hooks", {}),
                        priority=data.get("priority", 50),
                        enabled=data.get("enabled", True),
                        version=data.get("version", "1.0"),
                    )
                    self._manifests[manifest.skill_id] = manifest
                    logger.info("Loaded skill manifest: %s (stage=%s, priority=%d)",
                                manifest.skill_id, manifest.stage, manifest.priority)
            except Exception as e:
                logger.error("Failed to load skill manifest %s: %s", yaml_file, e)

    def select_skills(
        self,
        user_text: str = "",
        stage: str = "",
        geometry_types: list[str] | None = None,
        spec: Any | None = None,
    ) -> list[SkillManifest]:
        """Select skills relevant to the given context.

        Args:
            user_text: User input text
            stage: Pipeline stage (intent, geometry, physics, etc.)
            geometry_types: List of geometry types in the spec
            spec: The current spec (optional, for advanced selection)

        Returns:
            List of selected SkillManifests sorted by priority (descending)
        """
        selected: list[SkillManifest] = []
        text_lower = user_text.lower()
        geom_set = set(geometry_types or [])

        for manifest in self._manifests.values():
            if not manifest.enabled:
                continue

            # Stage filter
            if stage and manifest.stage != stage and manifest.stage != "all":
                continue

            # Keyword match
            keyword_match = any(kw.lower() in text_lower for kw in manifest.selection_keywords)

            # Geometry match
            geometry_match = any(g in geom_set for g in manifest.selection_geometry)

            # If no selection criteria, select for all
            no_criteria = not manifest.selection_keywords and not manifest.selection_geometry

            if keyword_match or geometry_match or no_criteria:
                selected.append(manifest)

        # Sort by priority (descending)
        selected.sort(key=lambda s: s.priority, reverse=True)
        return selected

    def build_prompt_injection(
        self,
        user_text: str = "",
        stage: str = "",
        geometry_types: list[str] | None = None,
    ) -> str:
        """Build a prompt fragment from selected skills.

        This fragment is appended to the LLM system prompt to provide
        domain-specific guidance from skills.
        """
        selected = self.select_skills(
            user_text=user_text,
            stage=stage,
            geometry_types=geometry_types,
        )

        if not selected:
            return ""

        parts: list[str] = ["\n\n## Skill 提供的领域知识"]
        for skill in selected:
            if skill.prompt_fragment:
                parts.append(f"\n### {skill.name} (priority={skill.priority})")
                parts.append(skill.prompt_fragment)

        return "\n".join(parts)

    def get_compiler_hooks(
        self,
        geometry_types: list[str] | None = None,
        stage: str = "compile",
    ) -> dict[str, str]:
        """Get compiler hooks from selected skills.

        Returns a dict mapping hook_name to hook_value.
        """
        selected = self.select_skills(
            stage=stage,
            geometry_types=geometry_types,
        )

        hooks: dict[str, str] = {}
        for skill in selected:
            for hook_name, hook_value in skill.compiler_hooks.items():
                if hook_name not in hooks:  # First (highest priority) wins
                    hooks[hook_name] = hook_value

        return hooks

    @property
    def all_manifests(self) -> dict[str, SkillManifest]:
        """Return all loaded manifests."""
        return self._manifests

    def get_manifest(self, skill_id: str) -> SkillManifest | None:
        """Get a specific manifest by ID."""
        return self._manifests.get(skill_id)

    def resolve_documents(
        self,
        skill_ids: list[str],
        *,
        user_text: str | None = None,
    ) -> list[dict[str, str]]:
        """Resolve effective Skill documents or fail closed.

        ``user_text`` enables the Wrong Skill guard: every requested Skill
        must also be selected by the resolver for that message.
        """

        relevant_ids: set[str] | None = None
        if user_text is not None:
            relevant_ids = {
                item.skill_id for item in self.select_skills(user_text=user_text)
            }
        documents: list[dict[str, str]] = []
        for skill_id in skill_ids:
            manifest = self.get_manifest(skill_id)
            if manifest is None or not manifest.enabled or not manifest.prompt_fragment.strip():
                raise SkillResolutionError(f"SKILL_MISSING: {skill_id}")
            if relevant_ids is not None and skill_id not in relevant_ids:
                raise SkillResolutionError(f"WRONG_SKILL: {skill_id}")
            documents.append({
                "skill_id": skill_id,
                "content": manifest.prompt_fragment,
            })
        return documents

    def reload(self) -> None:
        """Reload all manifests (useful for development)."""
        self._manifests.clear()
        self._load_manifests()
