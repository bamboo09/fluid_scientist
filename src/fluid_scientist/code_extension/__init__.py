"""CodeExtensionSpec system — code extension management, sandbox execution,
auto-testing, approval workflow, plugin registration, and rollback.

Implements P3 requirements: safe generation, validation, and lifecycle
management of code extensions (custom functionObjects, boundary conditions,
post-processing scripts) for OpenFOAM simulations.

The primary :class:`CodeExtensionSpec` and :class:`CodeExtensionWorkflow`
are exported from :mod:`fluid_scientist.code_extension.spec`. The legacy
models from :mod:`fluid_scientist.code_extension.models` remain available
for the existing sandbox / registry / testing infrastructure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Eagerly import only lightweight model/utility modules that do not
# trigger large import chains.  The heavy workflow/spec modules are
# loaded lazily via __getattr__ to avoid circular imports (spec.py
# imports from case_plan.models which can transitively pull in
# study_decomposition.capability_checker → capabilities.models →
# code_extension).
from fluid_scientist.code_extension.models import (
    CodeExtensionType,
    ExtensionStatus,
    TestResult,
    TestSpec,
)

if TYPE_CHECKING:
    from fluid_scientist.code_extension.registry import ExtensionRegistry
    from fluid_scientist.code_extension.sandbox import SandboxResult
    from fluid_scientist.code_extension.spec import (
        DEFAULT_SAFETY_CONSTRAINTS,
        CodeExtensionSpec,
        CodeExtensionWorkflow,
    )


def __getattr__(name: str):
    if name in ("CodeExtensionSpec", "CodeExtensionWorkflow",
                "DEFAULT_SAFETY_CONSTRAINTS"):
        from fluid_scientist.code_extension.spec import (
            DEFAULT_SAFETY_CONSTRAINTS as _DSC,
        )
        from fluid_scientist.code_extension.spec import (
            CodeExtensionSpec as _CES,
        )
        from fluid_scientist.code_extension.spec import (
            CodeExtensionWorkflow as _CEW,
        )
        return {"DEFAULT_SAFETY_CONSTRAINTS": _DSC,
                "CodeExtensionSpec": _CES,
                "CodeExtensionWorkflow": _CEW}[name]
    if name == "ExtensionRegistry":
        from fluid_scientist.code_extension.registry import (
            ExtensionRegistry as _ER,
        )
        return _ER
    if name in ("approve_extension", "register_plugin", "rollback_extension"):
        from fluid_scientist.code_extension import registry as _reg
        return getattr(_reg, name)
    if name == "SandboxResult":
        from fluid_scientist.code_extension.sandbox import SandboxResult as _SR
        return _SR
    if name == "execute_in_sandbox":
        from fluid_scientist.code_extension.sandbox import (
            execute_in_sandbox as _eis,
        )
        return _eis
    if name in ("generate_tests", "run_tests"):
        from fluid_scientist.code_extension import testing as _tst
        return getattr(_tst, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Import functions lazily via __getattr__ (defined above)
# (approve_extension, register_plugin, rollback_extension,
#  execute_in_sandbox, generate_tests, run_tests)


__all__ = [
    "DEFAULT_SAFETY_CONSTRAINTS",
    "CodeExtensionSpec",
    "CodeExtensionType",
    "CodeExtensionWorkflow",
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
