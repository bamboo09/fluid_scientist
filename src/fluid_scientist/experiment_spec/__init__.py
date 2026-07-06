"""Structured experiment specification package.

Re-exports the public compilation surface so callers can import the formal
``compile_spec`` interface directly from the package.
"""

from fluid_scientist.experiment_spec.compilation import (
    COMPILER_ID,
    COMPILER_VERSION,
    TEMPLATE_VERSIONS,
    CompilationManifest,
    SpecNotConfirmedError,
    compile_confirmed_spec,
    compile_spec,
    compute_case_hash,
    compute_spec_hash,
)

__all__ = [
    "COMPILER_ID",
    "COMPILER_VERSION",
    "TEMPLATE_VERSIONS",
    "CompilationManifest",
    "SpecNotConfirmedError",
    "compile_confirmed_spec",
    "compile_spec",
    "compute_case_hash",
    "compute_spec_hash",
]
