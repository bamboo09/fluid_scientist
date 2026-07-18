"""Compiler for the OpenFOAM ``constant/turbulenceProperties`` file.

Maps the :class:`NumericsDefinition` turbulence model to the
``turbulenceProperties`` dictionary.
"""

from __future__ import annotations

from fluid_scientist.study_spec.numerics import NumericsDefinition

from ._common import foam_file_header

__all__ = ["compile_turbulence_properties"]


def compile_turbulence_properties(numerics: NumericsDefinition) -> str:
    """Produce the ``turbulenceProperties`` dictionary string.

    Mapping
    -------
    * ``laminar`` (or ``None``) → ``simulationType laminar;``
    * ``RANS_kEpsilon``         → ``simulationType RAS;`` with ``RASModel kEpsilon``
    * ``RANS_kOmegaSST``        → ``simulationType RAS;`` with ``RASModel kOmegaSST``
    * ``LES``                   → ``simulationType LES;`` with ``LESModel WALE``
    """
    model = numerics.turbulence_model

    lines: list[str] = []
    lines.append(foam_file_header("dictionary", "turbulenceProperties"))
    lines.append("")

    if model is None or model == "laminar":
        lines.append("simulationType  laminar;")
        lines.append("")
        return "\n".join(lines)

    if model in ("RANS_kEpsilon", "RANS_kOmegaSST"):
        ras_model = "kEpsilon" if model == "RANS_kEpsilon" else "kOmegaSST"
        lines.append("simulationType  RAS;")
        lines.append("")
        lines.append("RAS")
        lines.append("{")
        lines.append(f"    RASModel        {ras_model};")
        lines.append("    turbulence      on;")
        lines.append("    printCoeffs     on;")
        lines.append("}")
        lines.append("")
        return "\n".join(lines)

    if model == "LES":
        lines.append("simulationType  LES;")
        lines.append("")
        lines.append("LES")
        lines.append("{")
        lines.append("    LESModel        WALE;")
        lines.append("    turbulence      on;")
        lines.append("    printCoeffs     on;")
        lines.append("}")
        lines.append("")
        return "\n".join(lines)

    # Fallback for DES / DNS or any future model.
    lines.append("simulationType  laminar;")
    lines.append("")
    return "\n".join(lines)
