"""OpenFOAM Foundation 13 compiler sub-package.

This sub-package contains the individual file compilers that together
produce a complete OpenFOAM case directory.  Each compiler function
takes one or more study-spec blocks and returns a valid OpenFOAM
dictionary string.
"""

from __future__ import annotations

from ._common import (
    foam_dict_block,
    foam_file_header,
    fmt_num,
    foam_value,
    quantity_value,
    sourced_numeric,
)
from .control_dict import compile_control_dict
from .fields import (
    compile_nu_tilda_field,
    compile_pressure_field,
    compile_velocity_field,
)
from .function_objects import compile_function_objects
from .fv_schemes import compile_fv_schemes
from .fv_solution import compile_fv_solution
from .transport_properties import compile_transport_properties
from .turbulence import compile_turbulence_properties

__all__ = [
    # Shared helpers
    "foam_file_header",
    "fmt_num",
    "foam_value",
    "foam_dict_block",
    "quantity_value",
    "sourced_numeric",
    # File compilers
    "compile_control_dict",
    "compile_fv_schemes",
    "compile_fv_solution",
    "compile_velocity_field",
    "compile_pressure_field",
    "compile_nu_tilda_field",
    "compile_transport_properties",
    "compile_turbulence_properties",
    "compile_function_objects",
]
