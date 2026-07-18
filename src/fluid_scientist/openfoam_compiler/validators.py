"""Validators for :class:`CompiledCase` against its source spec.

The :class:`CompiledCaseValidator` performs post-compilation consistency
checks to ensure that the generated OpenFOAM case files faithfully
represent the originating :class:`SimulationStudySpec`.
"""

from __future__ import annotations

import re

from fluid_scientist.study_spec.models import SimulationStudySpec

from .compiler import CompiledCase
from .foundation13 import compile_function_objects
from .foundation13._common import fmt_num, quantity_value

__all__ = ["CompiledCaseValidator"]


class CompiledCaseValidator:
    """Validate a :class:`CompiledCase` against its source spec.

    The :meth:`validate` method returns a list of error strings.  An
    empty list means the compiled case is consistent with the spec.
    """

    def validate(
        self,
        compiled: CompiledCase,
        spec: SimulationStudySpec,
    ) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []

        control_dict = compiled.files.get("system/controlDict", "")
        if not control_dict:
            errors.append("Missing file: system/controlDict")
            return errors

        # --- endTime ---
        end_v = quantity_value(spec.numerics.time.end_time)
        if end_v is not None:
            actual = _extract_value(control_dict, "endTime")
            expected = fmt_num(end_v)
            if actual is None:
                errors.append("controlDict missing 'endTime'")
            elif actual != expected:
                errors.append(
                    f"endTime mismatch: expected {expected}, got {actual}"
                )

        # --- deltaT ---
        dt_v = quantity_value(spec.numerics.time.delta_t)
        if dt_v is not None:
            actual = _extract_value(control_dict, "deltaT")
            expected = fmt_num(dt_v)
            if actual is None:
                errors.append("controlDict missing 'deltaT'")
            elif actual != expected:
                errors.append(
                    f"deltaT mismatch: expected {expected}, got {actual}"
                )

        # --- writeInterval ---
        wi = spec.numerics.time.write_interval
        if isinstance(wi, bool):
            wi_v = None
        elif isinstance(wi, int):
            wi_v = float(wi)
        else:
            wi_v = quantity_value(wi)
        if wi_v is not None:
            actual = _extract_value(control_dict, "writeInterval")
            expected = fmt_num(wi_v)
            if actual is None:
                errors.append("controlDict missing 'writeInterval'")
            elif actual != expected:
                errors.append(
                    f"writeInterval mismatch: expected {expected}, got {actual}"
                )

        # --- boundary patches present in 0/U and 0/p ---
        u_file = compiled.files.get("0/U", "")
        p_file = compiled.files.get("0/p", "")
        for bc in spec.boundaries.conditions:
            if not u_file:
                errors.append("Missing file: 0/U")
                break
            if bc.patch_name not in u_file:
                errors.append(f"Patch '{bc.patch_name}' missing from 0/U")
        for bc in spec.boundaries.conditions:
            if not p_file:
                errors.append("Missing file: 0/p")
                break
            if bc.patch_name not in p_file:
                errors.append(f"Patch '{bc.patch_name}' missing from 0/p")

        # --- function objects match observations ---
        fos = compile_function_objects(spec.observations)
        for fo in fos:
            fo_type = fo.get("type", "")
            if fo_type and fo_type not in control_dict:
                errors.append(
                    f"Function object '{fo_type}' missing from controlDict"
                )

        return errors


def _extract_value(content: str, key: str) -> str | None:
    """Extract the value of *key* from an OpenFOAM dictionary string.

    Looks for a line starting with *key* followed by whitespace and a
    value terminated by ``;``.
    """
    pattern = rf"(?m)^{key}\s+(\S+);"
    match = re.search(pattern, content)
    if match:
        return match.group(1)
    return None
