"""CodeExtensionSpec system — code extension management, sandbox execution,
auto-testing, approval workflow, plugin registration, and rollback.

Implements P3 requirements: safe generation, validation, and lifecycle
management of code extensions (custom functionObjects, boundary conditions,
post-processing scripts) for OpenFOAM simulations.
"""

from fluid_scientist.code_extension.models import (
    CodeExtensionSpec,
    CodeExtensionType,
    ExtensionStatus,
    TestResult,
    TestSpec,
)
from fluid_scientist.code_extension.registry import (
    ExtensionRegistry,
    approve_extension,
    register_plugin,
    rollback_extension,
)
from fluid_scientist.code_extension.sandbox import (
    SandboxResult,
    execute_in_sandbox,
)
from fluid_scientist.code_extension.testing import (
    generate_tests,
    run_tests,
)

__all__ = [
    "CodeExtensionSpec",
    "CodeExtensionType",
    "ExtensionRegistry",
    "ExtensionStatus",
    "SandboxResult",
    "TestResult",
    "TestSpec",
    "approve_extension",
    "execute_in_sandbox",
    "generate_tests",
    "register_plugin",
    "rollback_extension",
    "run_tests",
]
