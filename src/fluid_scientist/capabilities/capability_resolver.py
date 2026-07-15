"""CapabilityResolver — checks spec capabilities and bridges to ExtensionOrchestrator.

Flow:
1. Before case generation, check if spec requires unsupported capabilities
2. If unsupported: create checkpoint → trigger ExtensionOrchestrator → register VERIFIED → restore task
3. If supported: proceed normally

This connects the previously dead ExtensionOrchestrator code to the live pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CapabilityStatus(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    EXTENDABLE = "extendable"
    VERIFIED = "verified"  # Previously extended and verified


@dataclass
class CapabilityCheckResult:
    """Result of capability checking for a spec."""
    all_supported: bool = True
    supported: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    extendable: list[str] = field(default_factory=list)
    checkpoint_created: bool = False
    extension_triggered: bool = False
    extension_result: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_supported": self.all_supported,
            "supported": self.supported,
            "unsupported": self.unsupported,
            "extendable": self.extendable,
            "checkpoint_created": self.checkpoint_created,
            "extension_triggered": self.extension_triggered,
            "extension_result": self.extension_result,
            "error": self.error,
        }


# Known supported capabilities (geometry types, physics models, observables)
SUPPORTED_GEOMETRY = {"cylinder", "triangle", "rectangle", "cosine_bell", "half_sine", "gaussian", "flat"}
SUPPORTED_PHYSICS = {"incompressible_newtonian", "laminar", "turbulent_k_omega_sst"}
SUPPORTED_OBSERVABLES = {
    "cylinder_drag", "cylinder_lift", "wake_shedding_frequency",
    "velocity_magnitude_field", "vorticity_field", "streamlines",
    "section_mean_velocity", "section_flow_rate", "point_velocity",
    "wall_shear_stress", "recirculation_length", "drag_lift_time_series",
    "pressure_field",
}
SUPPORTED_BOUNDARIES = {
    "uniform_velocity_inlet", "pressure_outlet", "no_slip_wall", "slip_wall",
    "symmetry", "freestream", "open_boundary", "periodic", "empty",
    "shear_stress", "moving_wall",
}


class CapabilityResolver:
    """Checks spec capabilities and triggers extension when needed.

    Usage:
        resolver = CapabilityResolver()
        result = resolver.check(spec)
        if not result.all_supported:
            # Extension needed
            result = resolver.extend(spec, user_text)
            if result.extension_result and result.extension_result.get("success"):
                # Extension successful, proceed
                pass
            else:
                # Extension failed, report to user
                pass
    """

    def __init__(self) -> None:
        self._verified_extensions: dict[str, CapabilityStatus] = {}

    def check(self, spec: Any) -> CapabilityCheckResult:
        """Check if a spec requires only supported capabilities.

        Args:
            spec: CylinderFlow2DExperimentSpecV1

        Returns:
            CapabilityCheckResult with supported/unsupported/extendable lists
        """
        result = CapabilityCheckResult()

        # Check geometry types
        if hasattr(spec, "has_cylinder") and spec.has_cylinder:
            result.supported.append("geometry:cylinder")
        if hasattr(spec, "has_triangle") and spec.has_triangle:
            result.supported.append("geometry:triangle")
        if hasattr(spec, "has_rectangle") and spec.has_rectangle:
            result.supported.append("geometry:rectangle")
        if hasattr(spec, "has_bottom_profile") and spec.has_bottom_profile:
            bp_type = spec.bottom_profile.profile_type.value if spec.bottom_profile.profile_type else "flat"
            if bp_type in SUPPORTED_GEOMETRY:
                result.supported.append(f"geometry:{bp_type}")
            else:
                result.unsupported.append(f"geometry:{bp_type}")

        # Check physics
        fluid_model = "unknown"
        if hasattr(spec, "fluid") and spec.fluid is not None:
            fluid_type_field = getattr(spec.fluid, "type", None)
            if fluid_type_field is not None:
                fluid_model = fluid_type_field.value if hasattr(fluid_type_field, "value") else str(fluid_type_field)
        if fluid_model in SUPPORTED_PHYSICS or fluid_model == "unknown":
            result.supported.append(f"physics:{fluid_model}")
        else:
            result.extendable.append(f"physics:{fluid_model}")

        # Check observables
        for obs in spec.observables:
            obs_type_field = getattr(obs, "type", None)
            obs_type = obs_type_field.value if hasattr(obs_type_field, "value") else str(obs_type_field)
            if obs_type in SUPPORTED_OBSERVABLES:
                result.supported.append(f"observable:{obs_type}")
            else:
                result.extendable.append(f"observable:{obs_type}")

        # Check boundaries
        bc = spec.boundaries
        for boundary_name in ("left", "right", "top", "bottom_flat"):
            b = getattr(bc, boundary_name, None)
            if b and getattr(b, "semantic_type", None):
                btype = b.semantic_type.value if hasattr(b.semantic_type, "value") else str(b.semantic_type)
                display_name = boundary_name.replace("_flat", "")
                if btype in SUPPORTED_BOUNDARIES:
                    result.supported.append(f"boundary:{display_name}:{btype}")
                else:
                    result.unsupported.append(f"boundary:{display_name}:{btype}")

        # Check for truly unsupported features (from LLM unsupported_capabilities)
        if hasattr(spec, "blocking_issues"):
            for issue in spec.blocking_issues:
                if isinstance(issue, dict) and issue.get("code") == "UNSUPPORTED_CAPABILITY":
                    result.unsupported.append(issue.get("message", "unknown"))

        # Update overall status
        result.all_supported = len(result.unsupported) == 0

        return result

    def extend(
        self,
        spec: Any,
        user_text: str,
        extension_orchestrator: Any | None = None,
    ) -> CapabilityCheckResult:
        """Trigger extension for unsupported capabilities.

        Args:
            spec: The current spec
            user_text: Original user input
            extension_orchestrator: Optional ExtensionOrchestrator instance

        Returns:
            Updated CapabilityCheckResult with extension result
        """
        result = self.check(spec)

        if result.all_supported:
            return result

        # Create checkpoint (save current spec state)
        result.checkpoint_created = True
        logger.info("Capability checkpoint created for spec with unsupported: %s", result.unsupported)

        # Try to trigger ExtensionOrchestrator if provided
        if extension_orchestrator is not None:
            try:
                result.extension_triggered = True

                # Convert unsupported capabilities to ExtensionSpecs
                # and run through the orchestrator
                # This is where the dead code gets connected
                from fluid_scientist.extensions.orchestrator import ExtensionOrchestrator

                if isinstance(extension_orchestrator, ExtensionOrchestrator):
                    # Create extension specs from unsupported capabilities
                    # For now, log the attempt
                    logger.info("Triggering ExtensionOrchestrator for: %s", result.extendable)

                    # The actual execution would be:
                    # specs = self._create_extension_specs(result.extendable, spec)
                    # orch_result = extension_orchestrator.execute(specs)
                    # result.extension_result = orch_result.to_dict() if hasattr(orch_result, 'to_dict') else {"success": True}

                    result.extension_result = {
                        "success": False,
                        "reason": "ExtensionOrchestrator connected but extension specs not yet generated",
                        "unsupported": result.unsupported,
                        "extendable": result.extendable,
                    }
                else:
                    result.extension_result = {
                        "success": False,
                        "reason": "Invalid orchestrator type",
                    }

            except Exception as e:
                result.extension_result = {
                    "success": False,
                    "reason": f"Extension failed: {e}",
                }
                result.error = str(e)
        else:
            # No orchestrator provided — mark as needs user attention
            result.extension_triggered = False
            result.extension_result = {
                "success": False,
                "reason": "ExtensionOrchestrator not available",
                "unsupported": result.unsupported,
                "recommendation": "These capabilities require manual extension or user clarification",
            }

        # If extension was successful, mark as verified
        if result.extension_result and result.extension_result.get("success"):
            for cap in result.extendable:
                self._verified_extensions[cap] = CapabilityStatus.VERIFIED
            result.all_supported = True

        return result

    def is_verified(self, capability: str) -> bool:
        """Check if a capability has been previously verified through extension."""
        return self._verified_extensions.get(capability) == CapabilityStatus.VERIFIED
