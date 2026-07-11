"""Unified Capability system.

Exports the new CapabilityRegistry (with native capabilities) alongside
legacy re-exports for backward compatibility.
"""

from fluid_scientist.capabilities.models import (
    CapabilityType,
    CodeExtensionSpec,
    CompilerCapability,
    MissingCapability,
    StrictModel,
)
from fluid_scientist.capabilities.resolution import (
    CapabilityRequirementGraph,
    CapabilityResolution,
    RequirementGraphResolver,
    ResolutionStatus,
    ResolutionStrategy,
)
from fluid_scientist.capabilities.registry import (
    CAPABILITY_TYPES,
    Capability,
    CapabilityHealthIssue,
    CapabilityHealthRecord,
    CapabilityHealthReport,
    CapabilityRegistry,
    CapabilityRequirement,
    CapabilityStatus,
    get_capability_registry,
    reset_registry,
)

__all__ = [
    "CAPABILITY_TYPES",
    "Capability",
    "CapabilityHealthIssue",
    "CapabilityHealthRecord",
    "CapabilityHealthReport",
    "CapabilityRegistry",
    "CapabilityRequirement",
    "CapabilityRequirementGraph",
    "CapabilityResolution",
    "CapabilityStatus",
    "CapabilityType",
    "CodeExtensionSpec",
    "CompilerCapability",
    "MissingCapability",
    "RequirementGraphResolver",
    "ResolutionStatus",
    "ResolutionStrategy",
    "StrictModel",
    "get_capability_registry",
    "reset_registry",
]
