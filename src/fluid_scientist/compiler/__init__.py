"""OpenFOAM 13 component compiler.

This package provides the deterministic compiler that transforms a
:class:`~fluid_scientist.case_ir.models.ResolvedCaseIR` into a complete
set of OpenFOAM 13 dictionary files with full provenance tracking.

Typical usage::

    from fluid_scientist.components import ComponentRegistry
    from fluid_scientist.compiler import OpenFOAM13ComponentCompiler
    from fluid_scientist.platform.profile import PlatformProfile

    registry = ComponentRegistry()
    compiler = OpenFOAM13ComponentCompiler(registry, PlatformProfile())
    case, manifest, source_map, plan = compiler.compile(resolved_ir)
"""

from fluid_scientist.compiler.compiler import (
    CompiledCase,
    CompiledCaseManifest,
    OpenFOAM13ComponentCompiler,
    ValidationPlan,
)
from fluid_scientist.compiler.source_map import SourceMap, SourceMapEntry

__all__ = [
    "CompiledCase",
    "CompiledCaseManifest",
    "OpenFOAM13ComponentCompiler",
    "SourceMap",
    "SourceMapEntry",
    "ValidationPlan",
]
