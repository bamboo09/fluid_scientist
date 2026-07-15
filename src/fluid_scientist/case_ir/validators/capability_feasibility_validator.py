"""Capability feasibility validation for RequestedCaseIR.

This validator checks whether the capabilities implied by the Case IR
(observables, boundary conditions, physics models, mesh strategies,
etc.) are actually feasible given the current capability registry and
platform profile.

It bridges the gap between the semantic Case IR and the concrete
capability registry: an observable with ``capability_status="SUPPORTED"``
should reference a capability that actually exists and is verified in
the registry.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.case_ir.models import RequestedCaseIR
from fluid_scientist.case_ir.validators.schema_validator import ValidationIssue
from fluid_scientist.capabilities.registry import (
    Capability,
    CapabilityRegistry,
    CapabilityStatus,
)
from fluid_scientist.platform.profile import PlatformProfile, get_platform_profile


class CapabilityFeasibilityValidator:
    """Validates that the Case IR's capability requirements are feasible.

    Checks performed:

    - Observable ``capability_ref`` values exist in the registry.
    - Capabilities referenced by observables are VERIFIED.
    - Boundary intent ``capability_ref`` values exist in the registry.
    - Platform version matches the registry's supported versions.
    - Observable ``openfoam_sampling_capability`` maps to a registered
      function-object capability.
    - Observable ``external_analysis_capability`` maps to a registered
      postprocessor capability.
    - Extensions are flagged where required (``EXTENDABLE`` status).
    - Security policy is not violated by any capability requirement.
    - No capability conflicts (e.g., two observables requiring the same
      exclusive resource with incompatible parameters).
    """

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        platform: PlatformProfile | None = None,
    ) -> None:
        self._registry = registry or CapabilityRegistry()
        self._platform = platform or get_platform_profile()

    def validate(self, case_ir: RequestedCaseIR) -> list[ValidationIssue]:
        """Run all capability feasibility checks."""
        issues: list[ValidationIssue] = []

        issues.extend(self._check_observable_capabilities(case_ir))
        issues.extend(self._check_boundary_capabilities(case_ir))
        issues.extend(self._check_physics_capabilities(case_ir))
        issues.extend(self._check_mesh_capabilities(case_ir))
        issues.extend(self._check_platform_version(case_ir))
        issues.extend(self._check_security_policy(case_ir))
        issues.extend(self._check_capability_conflicts(case_ir))
        issues.extend(self._check_extensions_needed(case_ir))

        return issues

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_observable_capabilities(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Check that observables reference valid, verified capabilities."""
        issues: list[ValidationIssue] = []
        for i, obs in enumerate(case_ir.observables):
            # Check capability_ref
            if obs.capability_ref:
                cap = self._registry.get_capability(obs.capability_ref)
                if cap is None:
                    issues.append(
                        ValidationIssue(
                            code="CAPABILITY_NOT_FOUND",
                            path=f"observables[{i}].capability_ref",
                            message=(
                                f"Observable '{obs.id}' references "
                                f"capability '{obs.capability_ref}' which "
                                f"is not in the registry."
                            ),
                        )
                    )
                elif cap.status != CapabilityStatus.VERIFIED:
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            code="CAPABILITY_NOT_VERIFIED",
                            path=f"observables[{i}].capability_ref",
                            message=(
                                f"Observable '{obs.id}' references "
                                f"capability '{obs.capability_ref}' with "
                                f"status '{cap.status}' (expected VERIFIED)."
                            ),
                        )
                    )

            # Check openfoam_sampling_capability
            if obs.openfoam_sampling_capability:
                fo_cap_id = f"fo.{obs.openfoam_sampling_capability}"
                cap = self._registry.get_capability(fo_cap_id)
                if cap is None:
                    # Try without the "fo." prefix
                    cap = self._registry.find_capabilities(
                        keyword=obs.openfoam_sampling_capability,
                        capability_type="function_object_generator",
                    )
                    if not cap:
                        issues.append(
                            ValidationIssue(
                                code="SAMPLING_CAPABILITY_NOT_FOUND",
                                path=f"observables[{i}].openfoam_sampling_capability",
                                message=(
                                    f"Observable '{obs.id}' requires "
                                    f"sampling capability "
                                    f"'{obs.openfoam_sampling_capability}' "
                                    f"which is not registered."
                                ),
                            )
                        )

            # Check external_analysis_capability
            if obs.external_analysis_capability:
                pp_cap_id = f"postprocess.{obs.external_analysis_capability}"
                cap = self._registry.get_capability(pp_cap_id)
                if cap is None:
                    cap = self._registry.find_capabilities(
                        keyword=obs.external_analysis_capability,
                        capability_type="postprocessor",
                    )
                    if not cap:
                        issues.append(
                            ValidationIssue(
                                level="warning",
                                code="ANALYSIS_CAPABILITY_NOT_FOUND",
                                path=f"observables[{i}].external_analysis_capability",
                                message=(
                                    f"Observable '{obs.id}' requires "
                                    f"analysis capability "
                                    f"'{obs.external_analysis_capability}' "
                                    f"which is not registered."
                                ),
                            )
                        )

            # Check capability_status consistency
            if obs.capability_status == "SUPPORTED" and not obs.capability_ref:
                issues.append(
                    ValidationIssue(
                        code="SUPPORTED_WITHOUT_CAPABILITY_REF",
                        path=f"observables[{i}].capability_status",
                        message=(
                            f"Observable '{obs.id}' has "
                            f"capability_status='SUPPORTED' but no "
                            f"capability_ref is set."
                        ),
                    )
                )
            if obs.capability_status == "REQUIRES_NEW_PHYSICS":
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="REQUIRES_NEW_PHYSICS",
                        path=f"observables[{i}].capability_status",
                        message=(
                            f"Observable '{obs.id}' requires new physics "
                            f"that is not yet implemented."
                        ),
                    )
                )
        return issues

    def _check_boundary_capabilities(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Check that boundary intents reference valid capabilities."""
        issues: list[ValidationIssue] = []
        for i, bc in enumerate(case_ir.boundary_intents):
            if bc.capability_ref:
                cap = self._registry.get_capability(bc.capability_ref)
                if cap is None:
                    issues.append(
                        ValidationIssue(
                            code="BOUNDARY_CAPABILITY_NOT_FOUND",
                            path=f"boundary_intents[{i}].capability_ref",
                            message=(
                                f"Boundary intent '{bc.id}' references "
                                f"capability '{bc.capability_ref}' which "
                                f"is not in the registry."
                            ),
                        )
                    )
                elif cap.status != CapabilityStatus.VERIFIED:
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            code="BOUNDARY_CAPABILITY_NOT_VERIFIED",
                            path=f"boundary_intents[{i}].capability_ref",
                            message=(
                                f"Boundary intent '{bc.id}' references "
                                f"capability '{bc.capability_ref}' with "
                                f"status '{cap.status}' (expected VERIFIED)."
                            ),
                        )
                    )
            else:
                # No capability_ref set -- try to infer from semantic_role
                role = bc.semantic_role.lower()
                matching = self._registry.find_capabilities(
                    capability_type="boundary_writer",
                    keyword=role,
                    status=CapabilityStatus.VERIFIED,
                )
                if not matching and role:
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            code="BOUNDARY_CAPABILITY_MISSING",
                            path=f"boundary_intents[{i}].semantic_role",
                            message=(
                                f"Boundary intent '{bc.id}' with semantic "
                                f"role '{bc.semantic_role}' has no matching "
                                f"verified boundary capability."
                            ),
                        )
                    )
        return issues

    def _check_physics_capabilities(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Check that the physics intent maps to registered capabilities."""
        issues: list[ValidationIssue] = []

        # Check turbulence model capability
        turb = case_ir.physics.turbulence
        model = case_ir.physics.turbulence_model

        if turb == "laminar":
            if not self._registry.has_capability("physics.laminar"):
                issues.append(
                    ValidationIssue(
                        code="PHYSICS_CAPABILITY_MISSING",
                        path="physics.turbulence",
                        message="Laminar physics capability not registered.",
                    )
                )
        elif turb == "RANS":
            if model:
                cap_id = f"physics.{model.lower().replace(' ', '_')}_rans"
                if not self._registry.has_capability(cap_id):
                    # Try keyword search
                    found = self._registry.find_capabilities(
                        capability_type="physics_model_compiler",
                        keyword=model,
                        status=CapabilityStatus.VERIFIED,
                    )
                    if not found:
                        issues.append(
                            ValidationIssue(
                                level="warning",
                                code="RANS_CAPABILITY_MISSING",
                                path="physics.turbulence_model",
                                message=(
                                    f"No verified RANS capability found "
                                    f"for model '{model}'."
                                ),
                            )
                        )
        elif turb == "LES":
            if model:
                cap_id = f"physics.{model.lower().replace(' ', '_')}_les"
                if not self._registry.has_capability(cap_id):
                    found = self._registry.find_capabilities(
                        capability_type="physics_model_compiler",
                        keyword=model,
                        status=CapabilityStatus.VERIFIED,
                    )
                    if not found:
                        issues.append(
                            ValidationIssue(
                                level="warning",
                                code="LES_CAPABILITY_MISSING",
                                path="physics.turbulence_model",
                                message=(
                                    f"No verified LES capability found "
                                    f"for model '{model}'."
                                ),
                            )
                        )

        # Check solver capability
        solver_module = self._platform.default_solver_module
        solver_cap_id = f"solver.{solver_module.lower()}"
        if not self._registry.has_capability(solver_cap_id):
            # Try broader search
            found = self._registry.find_capabilities(
                capability_type="solver_adapter",
                keyword=solver_module,
            )
            if not found:
                issues.append(
                    ValidationIssue(
                        code="SOLVER_CAPABILITY_MISSING",
                        path="physics",
                        message=(
                            f"No solver capability found for module "
                            f"'{solver_module}'."
                        ),
                    )
                )
        return issues

    def _check_mesh_capabilities(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Check that the mesh strategy has a registered capability."""
        issues: list[ValidationIssue] = []
        strategy = case_ir.mesh_intent.strategy
        if strategy == "block_mesh":
            if not self._registry.has_capability("mesh.block_mesh"):
                issues.append(
                    ValidationIssue(
                        code="MESH_CAPABILITY_MISSING",
                        path="mesh_intent.strategy",
                        message="blockMesh capability not registered.",
                    )
                )
        elif strategy == "snappy_hex_mesh":
            if not self._registry.has_capability("mesh.snappy_hex_mesh"):
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="MESH_CAPABILITY_NOT_VERIFIED",
                        path="mesh_intent.strategy",
                        message=(
                            "snappyHexMesh capability is registered but "
                            "may not be verified."
                        ),
                    )
                )
        return issues

    def _check_platform_version(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Check that capabilities support the current platform version."""
        issues: list[ValidationIssue] = []
        platform_version = self._platform.version

        checked: set[str] = set()
        for obs in case_ir.observables:
            if obs.capability_ref and obs.capability_ref not in checked:
                checked.add(obs.capability_ref)
                cap = self._registry.get_capability(obs.capability_ref)
                if cap and cap.supported_versions:
                    if platform_version not in cap.supported_versions:
                        issues.append(
                            ValidationIssue(
                                level="warning",
                                code="PLATFORM_VERSION_MISMATCH",
                                path=f"observables",
                                message=(
                                    f"Capability '{cap.capability_id}' "
                                    f"supports versions "
                                    f"{cap.supported_versions} but platform "
                                    f"is version '{platform_version}'."
                                ),
                            )
                        )

        for bc in case_ir.boundary_intents:
            if bc.capability_ref and bc.capability_ref not in checked:
                checked.add(bc.capability_ref)
                cap = self._registry.get_capability(bc.capability_ref)
                if cap and cap.supported_versions:
                    if platform_version not in cap.supported_versions:
                        issues.append(
                            ValidationIssue(
                                level="warning",
                                code="PLATFORM_VERSION_MISMATCH",
                                path="boundary_intents",
                                message=(
                                    f"Capability '{cap.capability_id}' "
                                    f"supports versions "
                                    f"{cap.supported_versions} but platform "
                                    f"is version '{platform_version}'."
                                ),
                            )
                        )
        return issues

    def _check_security_policy(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Check that the Case IR does not violate the security policy."""
        issues: list[ValidationIssue] = []
        policy = self._platform.security_policy

        # Check observable analysis methods for security violations
        for i, obs in enumerate(case_ir.observables):
            analysis_str = str(obs.analysis)
            violations = policy.validate_dict_content(analysis_str)
            for v in violations:
                issues.append(
                    ValidationIssue(
                        code="SECURITY_VIOLATION",
                        path=f"observables[{i}].analysis",
                        message=v,
                    )
                )

        # Check boundary intent parameters
        for i, bc in enumerate(case_ir.boundary_intents):
            for pname, pval in bc.parameters.items():
                val_str = str(pval.value)
                violations = policy.validate_dict_content(val_str)
                for v in violations:
                    issues.append(
                        ValidationIssue(
                            code="SECURITY_VIOLATION",
                            path=f"boundary_intents[{i}].parameters.{pname}",
                            message=v,
                        )
                    )

        # Check derived constraint expressions
        for i, dc in enumerate(case_ir.derived_constraints):
            violations = policy.validate_dict_content(dc.expression)
            for v in violations:
                issues.append(
                    ValidationIssue(
                        code="SECURITY_VIOLATION",
                        path=f"derived_constraints[{i}].expression",
                        message=v,
                    )
                )
        return issues

    def _check_capability_conflicts(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Detect conflicting capability requirements.

        Two observables that require the same exclusive resource (e.g.,
        the same patch with incompatible boundary conditions) are flagged.
        """
        issues: list[ValidationIssue] = []

        # Check for conflicting boundary conditions on the same patch
        patch_bcs: dict[str, list[tuple[int, str]]] = {}
        for i, bc in enumerate(case_ir.boundary_intents):
            patch = bc.target_patch
            if patch:
                patch_bcs.setdefault(patch, []).append((i, bc.semantic_role))

        for patch, entries in patch_bcs.items():
            if len(entries) > 1:
                roles = {role for _, role in entries}
                if len(roles) > 1:
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            code="CONFLICTING_BOUNDARY_INTENTS",
                            path="boundary_intents",
                            message=(
                                f"Patch '{patch}' has {len(entries)} "
                                f"boundary intents with different roles: "
                                f"{roles}. Only one BC per patch is allowed."
                            ),
                        )
                    )

        # Check for conflicting observables on the same region
        region_obs: dict[str, list[str]] = {}
        for obs in case_ir.observables:
            if obs.target_region:
                region_obs.setdefault(obs.target_region, []).append(
                    obs.semantic_type
                )

        for region, types in region_obs.items():
            # Check for mutually exclusive observable types
            if "velocity_field" in types and "pressure_field" in types:
                # These are compatible -- no issue
                pass
            if "frequency_spectrum" in types and "time_average" in types:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="CONFLICTING_OBSERVABLES",
                        path="observables",
                        message=(
                            f"Region '{region}' has both frequency_spectrum "
                            f"and time_average observables. These require "
                            f"conflicting sampling strategies."
                        ),
                    )
                )
        return issues

    def _check_extensions_needed(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Flag observables and boundary intents that need extensions."""
        issues: list[ValidationIssue] = []

        for i, obs in enumerate(case_ir.observables):
            if obs.capability_status == "EXTENDABLE":
                if not any(
                    ext.target_capability
                    and ext.target_capability == (obs.capability_ref or obs.id)
                    for ext in case_ir.extensions
                ):
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            code="EXTENSION_NEEDED",
                            path=f"observables[{i}].capability_status",
                            message=(
                                f"Observable '{obs.id}' is marked EXTENDABLE "
                                f"but no extension spec references it."
                            ),
                        )
                    )

        for i, ext in enumerate(case_ir.extensions):
            if ext.extension_type == "physics":
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="PHYSICS_EXTENSION_REQUIRED",
                        path=f"extensions[{i}]",
                        message=(
                            f"Extension '{ext.id}' requires new physics "
                            f"({ext.description}). This may require "
                            f"significant development."
                        ),
                    )
                )
        return issues


__all__ = ["CapabilityFeasibilityValidator"]
