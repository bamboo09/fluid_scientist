"""Capability gap analysis for atomic requirement sets.

This module implements the :class:`CapabilityGapAnalyzer`, which takes a
set of atomic capability requirements and a capability registry and
classifies each requirement into one of six categories:

- ``EXACT_SUPPORTED`` -- a single capability in the registry matches the
  requirement, is verified, and has no conflicts.
- ``COMPOSABLE_SUPPORTED`` -- no single capability matches, but two or
  more verified capabilities can be combined to satisfy the requirement.
- ``EXTENDABLE`` -- no match exists, but the requirement can be satisfied
  by extending an existing capability via config or code generation.
- ``REQUIRES_NEW_PHYSICS`` -- the requirement needs a new solver module
  or physics implementation that does not exist.
- ``NEEDS_CLARIFICATION`` -- the requirement is missing essential
  information or contains a scientific contradiction.
- ``ENVIRONMENT_BLOCKED`` -- the requirement cannot be satisfied due to
  workstation, MPI, or security constraints.

The output is a :class:`CapabilityResolutionPlan` that contains the
classification for each requirement along with supporting details.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from fluid_scientist.capabilities.registry import (
    Capability,
    CapabilityRegistry,
    CapabilityRequirement,
    CapabilityStatus,
)
from fluid_scientist.platform.profile import PlatformProfile, get_platform_profile

# ---------------------------------------------------------------------------
# Classification type
# ---------------------------------------------------------------------------

RequirementClassification = Literal[
    "EXACT_SUPPORTED",
    "COMPOSABLE_SUPPORTED",
    "EXTENDABLE",
    "REQUIRES_NEW_PHYSICS",
    "NEEDS_CLARIFICATION",
    "ENVIRONMENT_BLOCKED",
]


# ---------------------------------------------------------------------------
# AtomicRequirementSet
# ---------------------------------------------------------------------------


class AtomicRequirementSet(BaseModel):
    """A set of atomic capability requirements derived from a Case IR.

    Attributes:
        requirements: The list of :class:`CapabilityRequirement` objects.
        source_case_ir_version: The version of the Case IR from which
            these requirements were derived.
    """

    requirements: list[CapabilityRequirement] = Field(default_factory=list)
    source_case_ir_version: int = 1

    @property
    def mandatory_requirements(self) -> list[CapabilityRequirement]:
        """Only the mandatory requirements."""
        return [r for r in self.requirements if r.mandatory]

    @property
    def optional_requirements(self) -> list[CapabilityRequirement]:
        """Only the optional requirements."""
        return [r for r in self.requirements if not r.mandatory]


# ---------------------------------------------------------------------------
# RequirementClassificationResult
# ---------------------------------------------------------------------------


class RequirementClassificationResult(BaseModel):
    """The classification result for a single requirement.

    Attributes:
        requirement: The original :class:`CapabilityRequirement`.
        classification: The :class:`RequirementClassification` category.
        matched_capabilities: Capabilities that matched (for
            EXACT_SUPPORTED and COMPOSABLE_SUPPORTED).
        reason: Human-readable explanation of the classification.
        clarification_questions: Questions for the user (for
            NEEDS_CLARIFICATION).
        extension_type: Type of extension needed (for EXTENDABLE).
        blocked_reason: Reason for blocking (for ENVIRONMENT_BLOCKED).
    """

    requirement: CapabilityRequirement
    classification: RequirementClassification
    matched_capabilities: list[Capability] = Field(default_factory=list)
    reason: str = ""
    clarification_questions: list[str] = Field(default_factory=list)
    extension_type: str = ""
    blocked_reason: str = ""


# ---------------------------------------------------------------------------
# CapabilityResolutionPlan
# ---------------------------------------------------------------------------


class CapabilityResolutionPlan(BaseModel):
    """The output of :class:`CapabilityGapAnalyzer`.

    Contains the classification of every requirement in the input set,
    along with aggregate properties for quick decision-making.

    Attributes:
        results: Classification result for each requirement.
        all_supported: True if all mandatory requirements are
            EXACT_SUPPORTED or COMPOSABLE_SUPPORTED.
        needs_extension: True if any mandatory requirement is EXTENDABLE.
        needs_new_physics: True if any mandatory requirement is
            REQUIRES_NEW_PHYSICS.
        needs_clarification: True if any mandatory requirement is
            NEEDS_CLARIFICATION.
        environment_blocked: True if any mandatory requirement is
            ENVIRONMENT_BLOCKED.
    """

    results: list[RequirementClassificationResult] = Field(default_factory=list)

    @property
    def all_supported(self) -> bool:
        """True if all mandatory requirements are supported."""
        return all(
            r.classification in {"EXACT_SUPPORTED", "COMPOSABLE_SUPPORTED"}
            for r in self.results
            if r.requirement.mandatory
        )

    @property
    def needs_extension(self) -> bool:
        """True if any mandatory requirement needs extension."""
        return any(
            r.classification == "EXTENDABLE"
            for r in self.results
            if r.requirement.mandatory
        )

    @property
    def needs_new_physics(self) -> bool:
        """True if any mandatory requirement needs new physics."""
        return any(
            r.classification == "REQUIRES_NEW_PHYSICS"
            for r in self.results
            if r.requirement.mandatory
        )

    @property
    def needs_clarification(self) -> bool:
        """True if any mandatory requirement needs clarification."""
        return any(
            r.classification == "NEEDS_CLARIFICATION"
            for r in self.results
            if r.requirement.mandatory
        )

    @property
    def environment_blocked(self) -> bool:
        """True if any mandatory requirement is environment-blocked."""
        return any(
            r.classification == "ENVIRONMENT_BLOCKED"
            for r in self.results
            if r.requirement.mandatory
        )

    @property
    def supported_requirements(self) -> list[RequirementClassificationResult]:
        """Results for requirements that are fully supported."""
        return [
            r
            for r in self.results
            if r.classification in {"EXACT_SUPPORTED", "COMPOSABLE_SUPPORTED"}
        ]

    @property
    def unsupported_requirements(self) -> list[RequirementClassificationResult]:
        """Results for requirements that are NOT fully supported."""
        return [
            r
            for r in self.results
            if r.classification not in {"EXACT_SUPPORTED", "COMPOSABLE_SUPPORTED"}
        ]

    @property
    def all_clarification_questions(self) -> list[str]:
        """All clarification questions across all results."""
        questions: list[str] = []
        for r in self.results:
            questions.extend(r.clarification_questions)
        return questions

    def get_result(
        self, requirement_id: str
    ) -> RequirementClassificationResult | None:
        """Get the classification result for a specific requirement."""
        for r in self.results:
            if r.requirement.requirement_id == requirement_id:
                return r
        return None


# ---------------------------------------------------------------------------
# Capability types that can be extended via config
# ---------------------------------------------------------------------------

_CONFIG_EXTENSIBLE_TYPES: set[str] = {
    "boundary_writer",
    "function_object_generator",
    "field_sampler",
    "postprocessor",
    "initial_condition_writer",
    "parameter_definition",
    "openfoam_function_object_writer",
}

# Capability types that require new physics modules
_PHYSICS_TYPES: set[str] = {
    "solver_adapter",
    "physics_model_compiler",
    "solver_extension",
    "solver",
}

# Environment-related keywords in requirement metadata
_ENVIRONMENT_BLOCK_KEYWORDS: set[str] = {
    "mpi",
    "gpu",
    "cuda",
    "hpc",
    "cluster",
    "slurm",
    "parallel",
    "distributed",
}


# ---------------------------------------------------------------------------
# CapabilityGapAnalyzer
# ---------------------------------------------------------------------------


class CapabilityGapAnalyzer:
    """Analyzes capability gaps in an atomic requirement set.

    For each requirement, the analyzer attempts to classify it into one
    of six categories by querying the capability registry.

    Args:
        registry: The :class:`CapabilityRegistry` to search.
        platform: Optional :class:`PlatformProfile` for environment and
            version checks.  Defaults to the global singleton.
        require_verified: If True (default), only VERIFIED capabilities
            are considered for EXACT_SUPPORTED and COMPOSABLE_SUPPORTED.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        platform: PlatformProfile | None = None,
        *,
        require_verified: bool = True,
    ) -> None:
        self._registry = registry
        self._platform = platform or get_platform_profile()
        self._require_verified = require_verified

    def analyze(
        self, requirement_set: AtomicRequirementSet
    ) -> CapabilityResolutionPlan:
        """Classify every requirement in *requirement_set*.

        Returns a :class:`CapabilityResolutionPlan` with one
        :class:`RequirementClassificationResult` per requirement.
        """
        results = [
            self._classify_one(req) for req in requirement_set.requirements
        ]
        return CapabilityResolutionPlan(results=results)

    # ------------------------------------------------------------------
    # Classification logic
    # ------------------------------------------------------------------

    def _classify_one(
        self, requirement: CapabilityRequirement
    ) -> RequirementClassificationResult:
        """Classify a single requirement."""

        # 1. Check for environment blocking first
        env_block = self._check_environment_blocked(requirement)
        if env_block:
            return RequirementClassificationResult(
                requirement=requirement,
                classification="ENVIRONMENT_BLOCKED",
                reason=env_block,
                blocked_reason=env_block,
            )

        # 2. Check for missing essential info / contradictions
        clarification = self._check_needs_clarification(requirement)
        if clarification:
            return RequirementClassificationResult(
                requirement=requirement,
                classification="NEEDS_CLARIFICATION",
                reason=clarification[0],
                clarification_questions=clarification,
            )

        # 3. Try exact match
        exact_cap = self._find_exact_match(requirement)
        if exact_cap:
            return RequirementClassificationResult(
                requirement=requirement,
                classification="EXACT_SUPPORTED",
                matched_capabilities=[exact_cap],
                reason=(
                    f"Exact capability match: '{exact_cap.capability_id}' "
                    f"(status: {exact_cap.status})."
                ),
            )

        # 4. Try compositional match
        composed_caps = self._find_composable_match(requirement)
        if composed_caps:
            return RequirementClassificationResult(
                requirement=requirement,
                classification="COMPOSABLE_SUPPORTED",
                matched_capabilities=composed_caps,
                reason=(
                    f"Requirement can be satisfied by composing "
                    f"{len(composed_caps)} capabilities: "
                    f"{', '.join(c.capability_id for c in composed_caps)}."
                ),
            )

        # 5. Try parameterized match (type + keyword match)
        #    Only attempt this when no specific capability_id was
        #    requested.  If a capability_id was given but not found,
        #    falling back to a different capability of the same type
        #    would be incorrect.
        if not requirement.capability_id:
            param_cap = self._find_parameterized_match(requirement)
            if param_cap:
                return RequirementClassificationResult(
                    requirement=requirement,
                    classification="EXACT_SUPPORTED",
                    matched_capabilities=[param_cap],
                    reason=(
                        f"Matched by type and keywords: "
                        f"'{param_cap.capability_id}'."
                    ),
                )

        # 6. Check if extendable
        if self._is_extendable(requirement):
            ext_type = "config" if requirement.capability_type in _CONFIG_EXTENSIBLE_TYPES else "code"
            return RequirementClassificationResult(
                requirement=requirement,
                classification="EXTENDABLE",
                reason=(
                    f"No matching capability found, but requirement can be "
                    f"extended via {ext_type} extension."
                ),
                extension_type=ext_type,
            )

        # 7. Check if requires new physics
        if self._requires_new_physics(requirement):
            return RequirementClassificationResult(
                requirement=requirement,
                classification="REQUIRES_NEW_PHYSICS",
                reason=(
                    f"Requirement type '{requirement.capability_type}' "
                    f"requires a new solver module or physics implementation "
                    f"that does not exist in the registry."
                ),
            )

        # 8. Default: needs clarification
        return RequirementClassificationResult(
            requirement=requirement,
            classification="NEEDS_CLARIFICATION",
            reason=(
                f"Could not classify requirement '{requirement.requirement_id}': "
                f"no exact match, no composable match, not extendable, "
                f"and not a physics type. Additional information needed."
            ),
            clarification_questions=[
                f"Could you provide more details about the required "
                f"capability for: {requirement.description or requirement.requirement_id}?",
            ],
        )

    # ------------------------------------------------------------------
    # Environment blocking
    # ------------------------------------------------------------------

    def _check_environment_blocked(
        self, requirement: CapabilityRequirement
    ) -> str:
        """Check if the requirement is blocked by environment constraints.

        Returns a non-empty string describing the block, or an empty
        string if not blocked.
        """
        # Check openfoam_mapping for environment requirements
        mapping = requirement.openfoam_mapping
        if mapping:
            mapping_str = str(mapping).lower()
            for keyword in _ENVIRONMENT_BLOCK_KEYWORDS:
                if keyword in mapping_str:
                    # Check if the platform supports this
                    if keyword in ("mpi", "parallel", "distributed"):
                        # MPI is generally supported; only block if
                        # explicitly marked as unsupported
                        if mapping.get("mpi_required") and not self._platform.supports_dry_run:
                            # This is a proxy check; in practice the
                            # workstation profile would be consulted
                            pass
                    if keyword in ("gpu", "cuda"):
                        return (
                            f"Requirement requires GPU/CUDA support which "
                            f"is not available on this workstation."
                        )
                    if keyword in ("hpc", "cluster", "slurm"):
                        return (
                            f"Requirement requires HPC/cluster access which "
                            f"is not configured on this workstation."
                        )

        # Check security policy violations in the requirement
        policy = self._platform.security_policy
        requirement_str = str(requirement.model_dump())
        violations = policy.validate_dict_content(requirement_str)
        if violations:
            return (
                f"Security policy violation: {'; '.join(violations)}"
            )

        return ""

    # ------------------------------------------------------------------
    # Clarification check
    # ------------------------------------------------------------------

    def _check_needs_clarification(
        self, requirement: CapabilityRequirement
    ) -> list[str]:
        """Check if the requirement needs user clarification.

        Returns a list of clarification questions (empty if none needed).
        """
        questions: list[str] = []

        # Missing capability_type
        if not requirement.capability_type:
            questions.append(
                f"Requirement '{requirement.requirement_id}' has no "
                f"capability_type specified. What type of capability "
                f"is needed?"
            )

        # Missing both capability_id and keywords
        if not requirement.capability_id and not requirement.keywords:
            if not requirement.description:
                questions.append(
                    f"Requirement '{requirement.requirement_id}' has no "
                    f"capability_id, keywords, or description. Please "
                    f"describe what capability is needed."
                )

        # Scientific contradiction in the requirement
        if requirement.scientific_reason:
            reason_lower = requirement.scientific_reason.lower()
            contradictions = []
            if "steady" in reason_lower and "transient" in reason_lower:
                contradictions.append("steady and transient")
            if "laminar" in reason_lower and "turbulent" in reason_lower:
                contradictions.append("laminar and turbulent")
            if "compressible" in reason_lower and "incompressible" in reason_lower:
                contradictions.append("compressible and incompressible")
            if contradictions:
                questions.append(
                    f"Requirement '{requirement.requirement_id}' contains "
                    f"contradictory terms: {'; '.join(contradictions)}. "
                    f"Please clarify which regime is intended."
                )

        return questions

    # ------------------------------------------------------------------
    # Matching logic
    # ------------------------------------------------------------------

    def _find_exact_match(
        self, requirement: CapabilityRequirement
    ) -> Capability | None:
        """Find an exact capability_id match in the registry."""
        if not requirement.capability_id:
            return None
        cap = self._registry.get_capability(requirement.capability_id)
        if cap and self._is_eligible(cap):
            return cap
        return None

    def _find_parameterized_match(
        self, requirement: CapabilityRequirement
    ) -> Capability | None:
        """Find a match by capability type and keywords."""
        candidates = [
            cap
            for cap in self._registry.find_capabilities(
                capability_type=requirement.capability_type
            )
            if self._is_eligible(cap)
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
                1
                for keyword in requirement.keywords
                if keyword.lower() in haystack
            )
            if score > best_score:
                best = cap
                best_score = score
        return best if best_score > 0 else None

    def _find_composable_match(
        self, requirement: CapabilityRequirement
    ) -> list[Capability]:
        """Find multiple capabilities that can be composed to satisfy the requirement.

        This is used when no single capability matches but the
        requirement's keywords suggest that multiple capabilities could
        be combined.
        """
        if len(requirement.keywords) < 2:
            return []

        candidates = [
            cap
            for cap in self._registry.find_capabilities(
                capability_type=requirement.capability_type
            )
            if self._is_eligible(cap)
        ]

        selected: list[Capability] = []
        seen: set[str] = set()

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

    def _is_eligible(self, capability: Capability) -> bool:
        """Check if a capability is eligible for matching."""
        if capability.status == CapabilityStatus.DEPRECATED:
            return False
        if self._require_verified and capability.status != CapabilityStatus.VERIFIED:
            return False
        return True

    # ------------------------------------------------------------------
    # Extension and new physics checks
    # ------------------------------------------------------------------

    def _is_extendable(
        self, requirement: CapabilityRequirement
    ) -> bool:
        """Check if the requirement can be satisfied by an extension."""
        # Config-extendable types
        if requirement.capability_type in _CONFIG_EXTENSIBLE_TYPES:
            return True

        # Check if there are non-verified capabilities that could be
        # promoted via code extension
        non_verified = self._registry.find_capabilities(
            capability_type=requirement.capability_type,
        )
        if any(
            cap.status in {CapabilityStatus.REGISTERED, CapabilityStatus.TESTED}
            for cap in non_verified
        ):
            return True

        # Check if the requirement has fallback options that suggest
        # an extension is possible
        if requirement.fallback_options:
            return True

        return False

    def _requires_new_physics(
        self, requirement: CapabilityRequirement
    ) -> bool:
        """Check if the requirement needs new physics implementation."""
        if requirement.capability_type in _PHYSICS_TYPES:
            # If it's a physics/solver type and no match was found,
            # it likely needs new physics
            return True

        # Check scientific reason for physics keywords
        if requirement.scientific_reason:
            reason_lower = requirement.scientific_reason.lower()
            physics_keywords = [
                "new solver",
                "new model",
                "custom physics",
                "new physics",
                "non-standard",
                "novel",
            ]
            if any(kw in reason_lower for kw in physics_keywords):
                return True

        return False


__all__ = [
    "AtomicRequirementSet",
    "CapabilityGapAnalyzer",
    "CapabilityResolutionPlan",
    "RequirementClassification",
    "RequirementClassificationResult",
]
