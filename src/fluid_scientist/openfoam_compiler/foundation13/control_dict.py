"""Compiler for the OpenFOAM ``system/controlDict`` file.

Maps :class:`NumericsDefinition` temporal-control fields and
:class:`ObservationDefinition` targets to a valid OpenFOAM dictionary.
"""

from __future__ import annotations

from fluid_scientist.study_spec.numerics import NumericsDefinition
from fluid_scientist.study_spec.observations import ObservationDefinition

from ._common import foam_dict_block, foam_file_header, fmt_num, quantity_value
from .function_objects import compile_function_objects

__all__ = ["compile_control_dict"]


def compile_control_dict(
    numerics: NumericsDefinition,
    observations: ObservationDefinition,
    spec_id: str,
) -> str:
    """Produce the ``controlDict`` dictionary string.

    Mapping summary
    ---------------
    ============================ ========================
    Spec field                   OpenFOAM key
    ============================ ========================
    ``time.start_time``          ``startTime``
    ``time.end_time``            ``endTime``
    ``time.delta_t``             ``deltaT``
    ``time.adaptive``            ``adjustTimeStep``
    ``time.max_courant``         ``maxCo``
    ``time.max_delta_t``         ``maxDeltaT``
    ``time.write_control``       ``writeControl``
    ``time.write_interval``      ``writeInterval``
    ``time.purge_write``         ``purgeWrite``
    ============================ ========================
    """
    time = numerics.time
    lines: list[str] = []
    lines.append(foam_file_header("dictionary", "controlDict"))
    lines.append("")

    # --- application ---
    lines.append(f"application     {numerics.solver};")
    lines.append("")
    lines.append("startFrom       startTime;")

    # --- startTime ---
    start_v = quantity_value(time.start_time)
    lines.append(f"startTime       {fmt_num(start_v) if start_v is not None else '0'};")

    lines.append("stopAt          endTime;")

    # --- endTime (key acceptance criterion) ---
    end_v = quantity_value(time.end_time)
    lines.append(f"endTime         {fmt_num(end_v) if end_v is not None else '0'};")

    # --- deltaT ---
    dt_v = quantity_value(time.delta_t)
    lines.append(f"deltaT          {fmt_num(dt_v) if dt_v is not None else '1'};")

    # --- adjustTimeStep / maxCo / maxDeltaT ---
    lines.append(f"adjustTimeStep  {'yes' if time.adaptive else 'no'};")

    if time.max_courant is not None:
        lines.append(f"maxCo           {fmt_num(time.max_courant)};")

    max_dt_v = quantity_value(time.max_delta_t)
    if max_dt_v is not None:
        lines.append(f"maxDeltaT       {fmt_num(max_dt_v)};")

    # --- writeControl / writeInterval / purgeWrite ---
    if time.write_control is not None:
        wc = time.write_control
    elif time.adaptive:
        wc = "adjustableRunTime"
    else:
        wc = "runTime"
    lines.append(f"writeControl    {wc};")

    wi = time.write_interval
    if isinstance(wi, bool):
        wi_v = None
    elif isinstance(wi, int):
        wi_v = float(wi)
    else:
        wi_v = quantity_value(wi)
    lines.append(f"writeInterval   {fmt_num(wi_v) if wi_v is not None else '1'};")

    pw = time.purge_write if time.purge_write is not None else 0
    lines.append(f"purgeWrite      {pw};")

    # --- standard output settings ---
    lines.append("writeFormat     ascii;")
    lines.append("writePrecision  6;")
    lines.append("writeCompression off;")
    lines.append("timeFormat      general;")
    lines.append("timePrecision   6;")
    lines.append("runTimeModifiable yes;")
    lines.append("")

    # --- function objects ---
    fos = compile_function_objects(observations)
    if fos:
        lines.append("functions")
        lines.append("{")
        for fo in fos:
            name = fo.get("name", "funcObj")
            entries = {k: v for k, v in fo.items() if k != "name"}
            lines.append(foam_dict_block(name, entries, indent=1))
            lines.append("")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)
