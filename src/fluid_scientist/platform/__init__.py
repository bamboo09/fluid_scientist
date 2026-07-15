"""OpenFOAM Platform Profile — version-locked configuration for Foundation 13.

This module provides the single source of truth for the OpenFOAM platform
version, file conventions, solver module mapping, and security policy.
All modules that generate or validate OpenFOAM cases MUST read from the
same PlatformProfile instance.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.platform.profile import (
    LEGACY_SOLVER_MAP,
    PlatformProfile,
    SolverModuleInfo,
    TurbulenceFieldDependency,
    get_platform_profile,
    migrate_legacy_solver,
)

__all__ = [
    "LEGACY_SOLVER_MAP",
    "PlatformProfile",
    "SolverModuleInfo",
    "TurbulenceFieldDependency",
    "get_platform_profile",
    "migrate_legacy_solver",
]
