"""Requirement-graph based capability resolution for the V5 pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from fluid_scientist.capabilities.registry import (
    Capability,
    CapabilityRegistry,
    CapabilityRequirement,
    CapabilityStatus,
)

ResolutionStatus = Literal[
    "RESOLVED",
    "COMPOSED",
    "CONFIG_EXTENSION_PENDING",
    "CONFIG_EXTENSION_RESOLVED",
    "CODE_EXTENSION_REQUIRED",
    "UNSUPPORTED",
]

ResolutionStrategy = Literal[
    "EXACT_MATCH",
    "PARAMETERIZED_REUSE",
    "COMPOSED_VERIFIED_CAPABILITIES",
    "OPENFOAM_CONFIG_EXTENSION",
    "NEW_EXTENSION_SPEC",
    "UNSUPPORTED",
]


class CapabilityResolution(BaseModel):
    """Resolution result for one capability requirement."""

    requirement: CapabilityRequirement
    status: ResolutionStatus
    strategy: ResolutionStrategy
    selected_capabilities: list[Capability] = Field(default_factory=list)
    extension_required: bool = False
    reason: str = ""


class CapabilityRequirementGraph(BaseModel):
    """Structured graph of capability requirements and resolver decisions."""

    requirements: list[CapabilityRequirement] = Field(default_factory=list)
    resolutions: list[CapabilityResolution] = Field(default_factory=list)

    @property
    def unresolved(self) -> list[CapabilityResolution]:
        return [
            item
            for item in self.resolutions
            if item.status in {
                "CONFIG_EXTENSION_PENDING",
                "CODE_EXTENSION_REQUIRED",
                "UNSUPPORTED",
            }
        ]

    @property
    def resolved_capabilities(self) -> list[Capability]:
        caps: list[Capability] = []
        seen: set[str] = set()
        for resolution in self.resolutions:
            for cap in resolution.selected_capabilities:
                if cap.capability_id not in seen:
                    caps.append(cap)
                    seen.add(cap.capability_id)
        return caps


class RequirementGraphResolver:
    """Resolve requirements without falling back to case-title heuristics."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        require_verified: bool = True,
        require_healthy: bool = True,
    ) -> None:
        self._registry = registry
        self._require_verified = require_verified
        self._require_healthy = require_healthy

    def resolve(
        self, requirements: list[CapabilityRequirement]
    ) -> CapabilityRequirementGraph:
        resolutions = [self._resolve_one(req) for req in requirements]
        return CapabilityRequirementGraph(
            requirements=requirements,
            resolutions=resolutions,
        )

    def _resolve_one(self, requirement: CapabilityRequirement) -> CapabilityResolution:
        exact = self._resolve_exact(requirement)
        if exact:
            return CapabilityResolution(
                requirement=requirement.model_copy(
                    update={
                        "satisfied_by": exact.capability_id,
                        "extension_needed": False,
                    }
                ),
                status="RESOLVED",
                strategy="EXACT_MATCH",
                selected_capabilities=[exact],
                reason=f"Exact capability_id match: {exact.capability_id}",
            )

        composed = self._resolve_composed(requirement)
        if composed:
            return CapabilityResolution(
                requirement=requirement.model_copy(
                    update={
                        "satisfied_by": "+".join(
                            cap.capability_id for cap in composed
                        ),
                        "extension_needed": False,
                    }
                ),
                status="COMPOSED",
                strategy="COMPOSED_VERIFIED_CAPABILITIES",
                selected_capabilities=composed,
                reason="Requirement can be satisfied by composing capabilities.",
            )

        parameterized = self._resolve_parameterized(requirement)
        if parameterized:
            return CapabilityResolution(
                requirement=requirement.model_copy(
                    update={
                        "satisfied_by": parameterized.capability_id,
                        "extension_needed": False,
                    }
                ),
                status="RESOLVED",
                strategy="PARAMETERIZED_REUSE",
                selected_capabilities=[parameterized],
                reason=(
                    "Matched by capability type and requirement keywords: "
                    f"{parameterized.capability_id}"
                ),
            )

        if self._can_use_config_extension(requirement):
            return CapabilityResolution(
                requirement=requirement.model_copy(
                    update={"extension_needed": True}
                ),
                status="CONFIG_EXTENSION_PENDING",
                strategy="OPENFOAM_CONFIG_EXTENSION",
                extension_required=True,
                reason=(
                    "No healthy verified capability matched; OpenFOAM config "
                    "extension must be generated and validated before use."
                ),
            )

        return CapabilityResolution(
            requirement=requirement.model_copy(update={"extension_needed": True}),
            status="CODE_EXTENSION_REQUIRED",
            strategy="NEW_EXTENSION_SPEC",
            extension_required=True,
            reason="No reusable or composable healthy verified capability matched.",
        )

    def _resolve_exact(self, requirement: CapabilityRequirement) -> Capability | None:
        if not requirement.capability_id:
            return None
        cap = self._registry.get_capability(requirement.capability_id)
        if cap and self._eligible(cap):
            return cap
        return None

    def _resolve_parameterized(
        self, requirement: CapabilityRequirement
    ) -> Capability | None:
        candidates = [
            cap
            for cap in self._registry.find_capabilities(
                capability_type=requirement.capability_type
            )
            if self._eligible(cap)
        ]
        if not candidates:
            return None
        if not requirement.keywords:
            return candidates[0]

        best: Capability | None = None
        best_score = 0
        for cap in candidates:
            haystack = (
                f"{cap.capability_id} {cap.name} {cap.description}"
            ).lower()
            score = sum(
                1 for keyword in requirement.keywords
                if keyword.lower() in haystack
            )
            if score > best_score:
                best = cap
                best_score = score
        return best if best_score > 0 else None

    def _resolve_composed(
        self, requirement: CapabilityRequirement
    ) -> list[Capability]:
        if len(requirement.keywords) < 2:
            return []
        selected: list[Capability] = []
        seen: set[str] = set()
        candidates = [
            cap
            for cap in self._registry.find_capabilities(
                capability_type=requirement.capability_type
            )
            if self._eligible(cap)
        ]
        for keyword in requirement.keywords:
            lower = keyword.lower()
            for cap in candidates:
                haystack = (
                    f"{cap.capability_id} {cap.name} {cap.description}"
                ).lower()
                if lower in haystack and cap.capability_id not in seen:
                    selected.append(cap)
                    seen.add(cap.capability_id)
                    break
        return selected if len(selected) >= 2 else []

    def _can_use_config_extension(self, requirement: CapabilityRequirement) -> bool:
        return requirement.capability_type in {
            "boundary_writer",
            "function_object_generator",
            "field_sampler",
            "postprocessor",
            "initial_condition_writer",
        }

    def _eligible(self, capability: Capability) -> bool:
        if capability.status == CapabilityStatus.DEPRECATED:
            return False
        if self._require_verified and capability.status != CapabilityStatus.VERIFIED:
            return False
        if self._require_healthy:
            report = self._registry.health_check(mutate=False)
            record = next(
                (
                    item
                    for item in report.records
                    if item.capability_id == capability.capability_id
                ),
                None,
            )
            return bool(record and record.healthy)
        return True


__all__ = [
    "CapabilityRequirementGraph",
    "CapabilityResolution",
    "RequirementGraphResolver",
    "ResolutionStatus",
    "ResolutionStrategy",
]
