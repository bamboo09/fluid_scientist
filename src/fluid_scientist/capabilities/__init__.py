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
from fluid_scientist.capabilities.config_extension import (
    ConfigExtensionExecution,
    ConfigExtensionExecutor,
)
from fluid_scientist.capabilities.orchestrator import (
    ExtensionLifecycleStatus,
    ExtensionRunRecord,
    ExtensionSpec,
    PipelineCheckpoint,
    UnknownCapabilityOrchestrator,
    UnknownCapabilityResult,
)
from fluid_scientist.capabilities.resolution import (
    CapabilityRequirementGraph,
    CapabilityResolution,
    RequirementGraphResolver,
    ResolutionStatus,
    Resolu