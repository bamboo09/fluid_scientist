"""Unknown-capability extension system for the Fluid Scientist project.

This package defines the three extension spec types used to describe how an
unknown capability should be generated, the factory that turns a
:class:`~fluid_scientist.capabilities.gap_analyzer.CapabilityResolutionPlan`
into concrete specs, and the orchestrator that validates and (on success)
registers the resulting capabilities.

Public exports
--------------
* :class:`ConfigExtensionSpec`   — dictionary mappings, boundary combos,
  function objects, parameter schemas.
* :class:`CodeExtensionSpec`     — Python preprocessors, geometry/mesh
  generators, post-processors, OpenFOAM C++ extensions.
* :class:`PhysicsExtensionSpec`  — solver modules, equations, phase states,
  material models, multi-region coupling.
* :class:`ExtensionSpecFactory`  — translates a resolution plan into specs.
* :class:`ExtensionOrchestrator` — runs the full validate-and-register
  pipeline and never fakes success.
"""

from __future__ import annotations

from fluid_scientist.extensions.code_spec import CodeExtensionSpec, TestSpec
from fluid_scientist.extensions.config_spec import ConfigExtensionSpec
from fluid_scientist.extensions.factory import (
    DEFAULT_SECURITY_CONSTRAINTS,
    ExtensionSpecFactory,
    ExtensionSpecUnion,
)
from fluid_scientist.extensions.orchestrator import (
    FAILURE_STATES,
    ExtensionExecutionRecord,
    ExtensionOrchestrationResult,
    ExtensionOrchestrator,
    ExtensionStatus,
    ExtensionStepRecord,
)
from fluid_scientist.extensions.physics_spec import (
    ConservationCheck,
    PhysicsExtensionSpec,
)

__all__ = [
    "CodeExtensionSpec",
    "ConfigExtensionSpec",
    "ConservationCheck",
    "DEFAULT_SECURITY_CONSTRAINTS",
    "ExtensionExecutionRecord",
    "ExtensionOrchestrationResult",
    "ExtensionOrchestrator",
    "ExtensionSpecFactory",
    "ExtensionSpecUnion",
    "ExtensionStatus",
    "ExtensionStepRecord",
    "FAILURE_STATES",
    "PhysicsExtensionSpec",
    "TestSpec",
]
