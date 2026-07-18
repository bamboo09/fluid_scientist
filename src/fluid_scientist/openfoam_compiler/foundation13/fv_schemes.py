"""Compiler for the OpenFOAM ``system/fvSchemes`` file.

Maps :class:`NumericsDefinition` discretisation settings (with sensible
Foundation 13 defaults) to a valid OpenFOAM fvSchemes dictionary.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.study_spec.numerics import NumericsDefinition

from ._common import foam_file_header

__all__ = ["compile_fv_schemes"]


def compile_fv_schemes(numerics: NumericsDefinition) -> str:
    """Produce the ``fvSchemes`` dictionary string.

    Default schemes
    ---------------
    * **ddtSchemes** — ``steadyState`` for steady, ``Euler`` for
      transient (unless overridden by the spec ``discretization`` dict).
    * **gradSchemes** — ``Gauss linear``.
    * **divSchemes** — based on solver / turbulence model.
    * **laplacianSchemes** — ``Gauss linear corrected``.
    * **interpolationSchemes** — ``linear``.
    """
    disc: dict[str, Any] = numerics.discretization
    mode = numerics.time.mode
    turb = numerics.turbulence_model

    lines: list[str] = []
    lines.append(foam_file_header("dictionary", "fvSchemes"))
    lines.append("")

    # --- ddtSchemes ---
    lines.append("ddtSchemes")
    lines.append("{")
    ddt = disc.get("ddtSchemes", {}).get("ddtScheme") if disc else None
    if ddt:
        lines.append(f"    default         {ddt};")
    elif mode == "steady":
        lines.append("    default         steadyState;")
    else:
        lines.append("    default         Euler;")
    lines.append("}")
    lines.append("")

    # --- gradSchemes ---
    lines.append("gradSchemes")
    lines.append("{")
    grad = disc.get("gradSchemes", {}).get("gradScheme") if disc else None
    if grad:
        lines.append(f"    default         {grad};")
    else:
        lines.append("    default         Gauss linear;")
    lines.append("}")
    lines.append("")

    # --- divSchemes ---
    lines.append("divSchemes")
    lines.append("{")
    div_schemes = disc.get("divSchemes") if disc else None
    if div_schemes and isinstance(div_schemes, dict):
        for key, val in div_schemes.items():
            lines.append(f"    {key:<20} {val};")
    else:
        lines.append(_default_div_schemes(turb))
    lines.append("}")
    lines.append("")

    # --- laplacianSchemes ---
    lines.append("laplacianSchemes")
    lines.append("{")
    lapl = disc.get("laplacianSchemes", {}).get("laplacianScheme") if disc else None
    if lapl:
        lines.append(f"    default         {lapl};")
    else:
        lines.append("    default         Gauss linear corrected;")
    lines.append("}")
    lines.append("")

    # --- interpolationSchemes ---
    lines.append("interpolationSchemes")
    lines.append("{")
    interp = disc.get("interpolationSchemes", {}).get("interpolationScheme") if disc else None
    if interp:
        lines.append(f"    default         {interp};")
    else:
        lines.append("    default         linear;")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def _default_div_schemes(turb: str | None) -> str:
    """Return default ``divSchemes`` entries for the given turbulence model."""
    parts: list[str] = []
    parts.append("    default             none;")
    parts.append("    div(phi,U)          Gauss linear;")
    if turb and turb != "laminar":
        if turb == "RANS_kEpsilon":
            parts.append("    div(phi,k)          Gauss limitedLinear 1;")
            parts.append("    div(phi,epsilon)    Gauss limitedLinear 1;")
            parts.append("    div(nuEffPhi)       Gauss linear;")
        elif turb == "RANS_kOmegaSST":
            parts.append("    div(phi,k)          Gauss limitedLinear 1;")
            parts.append("    div(phi,omega)      Gauss limitedLinear 1;")
        elif turb == "LES":
            parts.append("    div(phi,k)          Gauss limitedLinear 1;")
    return "\n".join(parts)
