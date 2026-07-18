"""OpenFOAM compiler package.

This package implements the deterministic OpenFOAM Foundation 13
compiler that converts a
:class:`~fluid_scientist.study_spec.models.SimulationStudySpec` into
OpenFOAM case files.

Public API
----------
:class:`OpenFOAMCompiler`
    The main compiler class.
:class:`CompiledCase`
    Pydantic model holding the compiled case (file dict + metadata).
:class:`CompiledCaseValidator`
    Post-compilation consistency checker.

Example
-------
::

    from fluid_scientist.openfoam_compiler import OpenFOAMCompiler

    compiler = OpenFOAMCompiler()
    case = compiler.compile(spec)
    print(case.files["system/controlDict"])
"""

from __future__ import annotations

from .compiler import CompiledCase, OpenFOAMCompiler
from .validators import CompiledCaseValidator

__all__ = [
    "CompiledCase",
    "OpenFOAMCompiler",
    "CompiledCaseValidator",
]
