"""Capability resolution engine for RequestedCaseIR.

This module implements the :class:`CapabilityResolutionEngine`, which
takes a :class:`~fluid_scientist.case_ir.models.RequestedCaseIR`, a
:class:`~fluid_scientist.platform.profile.PlatformProfile`, a
:class:`~fluid_scientist.capabilities.registry.CapabilityRegistry`, and
an :class:`~fluid_scientist.capabilities.gap_analyzer.AtomicRequirementSet`
and produces either a fully-resolved
:class:`~fluid_scientist.case_ir.models.ResolvedCaseIR` or a structured
result explaining why resolution could not complete.

The engine delegates classification to
:class:`~fluid_scientist.capabilities.gap_analyzer.CapabilityGapAnalyzer`
and then acts on the plan:

- If all mandatory requirements are ``EXACT_SUPPORTED`` or
  ``COMPOSABLE_SUPPORTED``, a :class:`ResolvedCaseIR` is built with a
  :class:`~fluid_scientist.case_ir.models.CompositionPlan` and a list of
  :class:`~fluid_scientist.case_ir.models.ResolvedCapability` mappings.
- If any mandatory requirement is ``EXTENDABLE``, the result status is
  ``NEEDS_EXTENSION`` with details about what extensions are needed.
- If any mandatory requirement is ``REQUIRES_NEW_PHYSICS``, the result
  status is ``NEEDS_NEW_PHYSICS``.
- If any mandatory requirement is ``NEEDS_CLARIFICATION``, the result
  status is ``NEEDS_CLARIFICATION`` with clarification questions.
- If any mandatory requirement is ``ENVIRONMENT_BLOCKED``, the result
  status is ``ENVIRONMENT_BLOCKED`` with the blocking reason.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from fluid_scientist.capabilities.gap_analyzer import (
    AtomicRequirementSet,
    CapabilityGapAnalyzer,
    CapabilityResolutionPlan,
    RequirementClassificationResult,
)
from fluid_scientist.capabilities.registry import (
    Capability,
    CapabilityRegistry,
    CapabilityStatus,
)
from fluid_scientist.case_ir.models import (
    CompositionPlan,
    RequestedCaseIR,
    ResolvedCapability,
    ResolvedCaseIR,
)
from fluid_scientist.platform.profile import PlatformProfile, get_platform_profile

# ---------------------------------------------------------------------------
# Result status
# ---------------------------------------------------------------------------

ResolutionEngineStatus = Literal[
    "RESOLVED",
    "NEEDS_EXTENSION",
    "NEEDS_NEW_PHYSICS",
    "NEEDS_CLARIFICATION",
    "ENVIRONMENT_BLOCKED",
    "EMPTY_REQUIREMENTS",
]


# ---------------------------------------------------------------------------
# ResolutionEngineResult
# ---------------------------------------------------------------------------


class ResolutionEngineResult(BaseModel):
    """The result of a capability resolution attempt.

    Attributes:
        status: The overall resolution status.
        resolved_case_ir: The :class:`ResolvedCaseIR` if status is
            ``RESOLVED``, otherwise ``None``.
        resolution_plan: The :class:`CapabilityResolutionPlan` produced
            by the gap analyzer.
        clarification_questions: Questions for the user (when status is
            ``NEEDS_CLARIFICATION``).
        extension_details: Details about needed extensions (when status
            is ``NEEDS_EXTENSION``).
        blocked_reasons: Reasons for environment blocking (when status
            is ``ENVIRONMENT_BLOCKED``).
        new_physics_requirements: Descriptions of new physics needed
            (when status is ``NEEDS_NEW_PHYSICS``).
    """

    status: ResolutionEngineStatus
    resolved_case_ir: ResolvedCaseIR | None = None
    resolution_plan: CapabilityResolutionPlan = Field(
        default_factory=CapabilityResolutionPlan
    )
    clarification_questions: list[str] = Field(default_factory=list)
    extension_details: list[dict[str, Any]] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    new_physics_requirements: list[str] = Field(default_factory=list)

    @property
    def resolved(self) -> bool:
        """True if the resolution produced a :class:`ResolvedCaseIR`."""
        return self.status == "RESOLVED" and self.resolved_case_ir is not None

    def raise_if_unresolved(self) -> ResolvedCaseIR:
        """Return the :class:`ResolvedCaseIR` or raise.

        Raises:
            ValueError: If the resolution did not produce a
                :class:`ResolvedCaseIR`.
        """
        if self.resolved_case_ir is not None:
            return self.resolved_case_ir
        raise ValueError(
            f"Capability resolution did not complete. "
            f"Status: {self.status}. "
            f"{' '.join(self.clarification_questions) if self.clarification_questions else ''}"
            f"{' '.join(self.blocked_reasons) if self.blocked_reasons else ''}"
            f"{' '.join(self.new_physics_requirements) if self.new_physics_requirements else ''}"
        )


# ---------------------------------------------------------------------------
# Extension detail
# ---------------------------------------------------------------------------


class ExtensionDetail(BaseModel):
    """Details about a single extension needed.

    Attributes:
        requirement_id: The requirement that needs extension.
        capability_type: The type of capability.
        extension_type: ``"config"`` or ``"code"``.
        description: What the extension should accomplish.
        target_capability_id: The capability id that the extension will
            satisfy.
    """

    requirement_id: str
    capability_type: str
    extension_type: str = "config"
    description: str = ""
    target_capability_id: str = ""


# ---------------------------------------------------------------------------
# CapabilityResolutionEngine
# ---------------------------------------------------------------------------


class CapabilityResolutionEngine:
    """Resolves capability requirements into a :class:`ResolvedCaseIR`.

    The engine uses :class:`CapabilityGapAnalyzer` to classify each
    requirement and then builds the appropriate result based on the
    classification.

    Args:
        registry: The :class:`CapabilityRegistry` to query.
        platform: The :class:`PlatformProfile` for version and security
            checks.  Defaults to the global singleton.
        require_verified: If True (default), only VERIFIED capabilities
            are eligible for resolution.
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
        self._analyzer = CapabilityGapAnalyzer(
            registry=registry,
            platform=self._platform,
            require_verified=require_verified,
        )

    def resolve(
        self,
        case_ir: RequestedCaseIR,
        requirement_set: AtomicRequirementSet,
    ) -> ResolutionEngineResult:
        """Attempt to resolve all requirements and build a ResolvedCaseIR.

        Args:
            case_ir: The :class:`RequestedCaseIR` to resolve.
            requirement_set: The set of atomic requirements derived from
                the Case IR.

        Returns:
            A :class:`ResolutionEngineResult` with either a
            :class:`ResolvedCaseIR` (if fully resolved) or a structured
            explanation of why resolution could not complete.
        """
        # Handle empty requirement sets
        if not requirement_set.requirements:
            resolved_ir = self._build_resolved_case_ir(case_ir, [])
            return ResolutionEngineResult(
                status="EMPTY_REQUIREMENTS",
                resolved_case_ir=resolved_ir,
                resolution_plan=CapabilityResolutionPlan(),
            )

        # Analyze gaps
        plan = self._analyzer.analyze(requirement_set)

        # Check for environment blocking (highest priority)
        if plan.environment_blocked:
            blocked_reasons = [
                r.blocked_reason
                for r in plan.results
                if r.classification == "ENVIRONMENT_BLOCKED"
                and r.requirement.mandatory
            ]
            return ResolutionEngineResult(
                status="ENVIRONMENT_BLOCKED",
                resolution_plan=plan,
                blocked_reasons=blocked_reasons,
            )

        # Check for clarification needs
        if plan.needs_clarification:
            questions = plan.all_clarification_questions
            return ResolutionEngineResult(
                status="NEEDS_CLARIFICATION",
                resolution_plan=plan,
                clarification_questions=questions,
            )

        # Check for new physics requirements
        if plan.needs_new_physics:
            new_physics = [
                r.reason
                for r in plan.results
                if r.classification == "REQUIRES_NEW_PHYSICS"
                and r.requirement.mandatory
            ]
            return ResolutionEngineResult(
                status="NEEDS_NEW_PHYSICS",
                resolution_plan=plan,
                new_physics_requirements=new_physics,
            )

        # Check for extension needs
        if plan.needs_extension:
            extension_details = [
                {
                    "requirement_id": r.requirement.requirement_id,
                    "capability_type": r.requirement.capability_type,
                    "extension_type": r.extension_type,
                    "description": (
                        r.requirement.description
                        or r.requirement.scientific_reason
                        or r.reason
                    ),
                    "target_capability_id": (
                        r.requirement.capability_id
                        or f"generated.{r.requirement.capability_type}"
                    ),
                }
                for r in plan.results
                if r.classification == "EXTENDABLE"
                and r.requirement.mandatory
            ]
            return ResolutionEngineResult(
                status="NEEDS_EXTENSION",
                resolution_plan=plan,
                extension_details=extension_details,
            )

        # All supported -- build the resolved Case IR
        if plan.all_supported:
            resolved_caps = self._build_resolved_capabilities(plan)
            resolved_ir = self._build_resolved_case_ir(case_ir, resolved_caps)
            return ResolutionEngineResult(
                status="RESOLVED",
                resolved_case_ir=resolved_ir,
                resolution_plan=plan,
            )

        # Fallback: some requirements are not supported but not
        # explicitly classified as needing extension/clarification/new physics.
        # This can happen with optional requirements.
        unsupported = plan.unsupported_requirements
        if unsupported:
            # Check if any mandatory requirements are unsupported
            mandatory_unsupported = [
                r for r in unsupported if r.requirement.mandatory
            ]
            if mandatory_unsupported:
                # Re-classify as needs clarification
                questions = [
                    f"Requirement '{r.requirement.requirement_id}' could not "
                    f"be resolved (classification: {r.classification}). "
                    f"Reason: {r.reason}"
                    for r in mandatory_unsupported
                ]
                return ResolutionEngineResult(
                    status="NEEDS_CLARIFICATION",
                    resolution_plan=plan,
                    clarification_questions=questions,
                )

            # Only optional requirements are unsupported -- resolve with
            # what we have
            resolved_caps = self._build_resolved_capabilities(plan)
            resolved_ir = self._build_resolved_case_ir(case_ir, resolved_caps)
            return ResolutionEngineResult(
                status="RESOLVED",
                resolved_case_ir=resolved_ir,
                resolution_plan=plan,
            )

        # Should not reach here, but provide a safe fallback
        return ResolutionEngineResult(
            status="NEEDS_CLARIFICATION",
            resolution_plan=plan,
            clarification_questions=[
                "Unable to resolve capabilities for unknown reasons."
            ],
        )

    # ------------------------------------------------------------------
    # Building resolved objects
    # ------------------------------------------------------------------

    def _build_resolved_capabilities(
        self,
        plan: CapabilityResolutionPlan,
    ) -> list[ResolvedCapability]:
        """Build a list of :class:`ResolvedCapability` from the plan."""
        resolved: list[ResolvedCapability] = []
        seen: set[str] = set()

        for result in plan.results:
            if result.classification not in {
                "EXACT_SUPPORTED",
                "COMPOSABLE_SUPPORTED",
            }:
                continue

            req_id = result.requirement.requirement_id
            if req_id in seen:
                continue
            seen.add(req_id)

            # For EXACT_SUPPORTED, use the single matched capability
            if result.matched_capabilities:
                cap = result.matched_capabilities[0]
                validation_status = (
                    "VERIFIED"
                    if cap.status == CapabilityStatus.VERIFIED
                    else "UNVERIFIED"
                )
                resolved.append(
                    ResolvedCapability(
                        requirement_id=req_id,
                        capability_id=cap.capability_id,
                        validation_status=validation_status,
                    )
                )
            else:
                # Should not happen, but handle gracefully
                resolved.append(
                    ResolvedCapability(
                        requirement_id=req_id,
                        capability_id=result.requirement.capability_id or "unknown",
                        validation_status="UNVERIFIED",
                    )
                )

        return resolved

    def _build_resolved_case_ir(
        self,
        case_ir: RequestedCaseIR,
        resolved_caps: list[ResolvedCapability],
    ) -> ResolvedCaseIR:
        """Build a :class:`ResolvedCaseIR` from the request and resolved caps."""
        composition_plan = self._build_composition_plan(case_ir, resolved_caps)
        runtime = self._build_runtime_config(case_ir)
        resolved_physics = self._build_resolved_physics(case_ir)

        return ResolvedCaseIR(
            requested_case_ir_version=case_ir.case_ir_version,
            runtime=runtime,
            resolved_physics=resolved_physics,
            resolved_capabilities=resolved_caps,
            composition_plan=composition_plan,
        )

    def _build_composition_plan(
        self,
        case_ir: RequestedCaseIR,
        resolved_caps: list[ResolvedCapability],
    ) -> CompositionPlan:
        """Build a :class:`CompositionPlan` from the resolved capabilities."""
        # Determine base pack from physics
        physics = case_ir.physics
        base_parts: list[str] = ["foundation13"]
        base_parts.append(physics.flow_regime)
        if physics.turbulence != "laminar":
            base_parts.append(physics.turbulence.lower())
        base_parts.append(physics.time_mode)
        base_pack = "-".join(base_parts)

        # Categorize resolved capabilities into composition components
        geometry_components: list[str] = []
        boundary_components: list[str] = []
        mesh_components: list[str] = []
        observable_components: list[str] = []
        validation_components: list[str] = []

        for cap in resolved_caps:
            resolved_cap = self._registry.get_capability(cap.capability_id)
            if resolved_cap is None:
                continue

            cap_type = resolved_cap.capability_type
            cap_id = cap.capability_id

            if cap_type == "geometry_generator":
                geometry_components.append(cap_id)
            elif cap_type == "boundary_writer":
                boundary_components.append(cap_id)
            elif cap_type == "mesh_generator":
                mesh_components.append(cap_id)
            elif cap_type in {
                "function_object_generator",
                "field_sampler",
                "postprocessor",
            }:
                observable_components.append(cap_id)
            elif cap_type == "result_validator":
                validation_components.append(cap_id)
            elif cap_type == "physics_model_compiler":
                # Physics compilers are part of the base pack
                pass
            elif cap_type == "solver_adapter":
                # Solver adapters are part of the base pack
                pass
            elif cap_type == "initial_condition_writer":
                boundary_components.append(cap_id)
            elif cap_type == "motion_compiler":
                geometry_components.append(cap_id)

        # Add default mesh capability if none specified
        if not mesh_components:
            strategy = case_ir.mesh_intent.strategy
            if strategy == "block_mesh":
                mesh_components.append("mesh.block_mesh")
            elif strategy == "snappy_hex_mesh":
                mesh_components.append("mesh.snappy_hex_mesh")

        # Add default solver capability
        solver_module = self._platform.default_solver_module
        solver_cap_id = f"solver.{solver_module.lower()}"
        # Solver is implicitly part of the base pack

        return CompositionPlan(
            base_pack=base_pack,
            geometry_components=geometry_components,
            boundary_components=boundary_components,
            mesh_components=mesh_components,
            observable_components=observable_components,
            validation_components=validation_components,
        )

    def _build_runtime_config(
        self, case_ir: RequestedCaseIR
    ) -> dict[str, str]:
        """Build the runtime configuration dictionary."""
        config: dict[str, str] = {
            "platform_id": self._platform.profile_id,
            "platform_version": self._platform.version,
            "application": self._platform.application,
            "solver_module": self._platform.default_solver_module,
        }

        # Add solver-specific info based on physics
        if case_ir.physics.flow_regime == "compressible":
            config["solver_module"] = "fluid"
        elif case_ir.physics.time_mode == "steady":
            config["solver_module"] = "incompressibleFluid"
        else:
            config["solver_module"] = self._platform.default_solver_module

        # Add coupling scheme
        config["coupling"] = case_ir.numerical_intent.pressure_velocity_coupling

        return config

    def _build_resolved_physics(
        self, case_ir: RequestedCaseIR
    ) -> dict[str, Any]:
        """Build the resolved physics configuration dictionary."""
        physics = case_ir.physics
        config: dict[str, Any] = {
            "flow_regime": physics.flow_regime,
            "time_mode": physics.time_mode,
            "turbulence": physics.turbulence,
            "turbulence_model": physics.turbulence_model,
            "heat_transfer": physics.heat_transfer,
            "multiphase": physics.multiphase,
            "porous_media": physics.porous_media,
            "moving_mesh": physics.moving_mesh,
            "additional_physics": list(physics.additional_physics),
        }

        # Add turbulence field dependency info
        if physics.turbulence_model:
            dep = self._platform.get_turbulence_dependency(
                physics.turbulence_model
            )
            if dep:
                config["required_fields"] = list(dep.required_fields)
                config["nut_required"] = dep.nut_required

        # Add numerical scheme info
        config["pressure_velocity_coupling"] = (
            case_ir.numerical_intent.pressure_velocity_coupling
        )
        config["schemes"] = dict(case_ir.numerical_intent.schemes)

        return config


__all__ = [
    "CapabilityResolutionEngine",
    "ExtensionDetail",
    "ResolutionEngineResult",
    "ResolutionEngineStatus",
]
