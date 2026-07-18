"""Compiler for OpenFOAM initial / boundary-condition field files (``0/``).

Generates ``0/U`` (velocity), ``0/p`` (pressure) and ``0/nuTilda``
(turbulent viscosity) files with boundary conditions mapped from
:class:`BoundaryDefinition`.
"""

from __future__ import annotations

from fluid_scientist.study_spec.boundaries import BoundaryCondition, BoundaryDefinition
from fluid_scientist.study_spec.geometry import DomainSpec

from ._common import foam_file_header, fmt_num, sourced_numeric

__all__ = [
    "compile_velocity_field",
    "compile_pressure_field",
    "compile_nu_tilda_field",
]


# ---------------------------------------------------------------------------
# Velocity field (0/U)
# ---------------------------------------------------------------------------


def compile_velocity_field(
    boundaries: BoundaryDefinition,
    domain: DomainSpec,
) -> str:
    """Produce the ``0/U`` (volVectorField) dictionary string.

    Boundary role mapping
    ---------------------
    * ``inlet``    → ``fixedValue`` with ``uniform (Ux 0 0)``
    * ``outlet``   → ``zeroGradient``
    * ``wall``     → ``noSlip`` (or ``slip`` for ``slipWall`` bc_type)
    * ``symmetry`` → ``symmetryPlane``
    * ``empty``    → ``empty`` (2D)
    """
    lines: list[str] = []
    lines.append(foam_file_header("volVectorField", "U"))
    lines.append("")

    # Domain metadata comment — makes the domain genuinely affect output.
    length_v = sourced_numeric(domain.length) or 0.0
    width_v = sourced_numeric(domain.width) or 0.0
    lines.append(f"// domain: {domain.dimensions} {fmt_num(length_v)}x{fmt_num(width_v)}")
    lines.append("")

    lines.append("dimensions      [0 1 -1 0 0 0 0];")
    lines.append("")
    lines.append("internalField   uniform (0 0 0);")
    lines.append("")
    lines.append("boundaryField")
    lines.append("{")

    for bc in boundaries.conditions:
        entries = _velocity_bc(bc)
        lines.append(f"    {bc.patch_name}")
        lines.append("    {")
        for key, val in entries.items():
            lines.append(f"        {key:<16} {val};")
        lines.append("    }")
        lines.append("")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _velocity_bc(bc: BoundaryCondition) -> dict[str, str]:
    """Return OpenFOAM BC entries for the velocity field."""
    role = bc.role
    bc_type = bc.bc_type
    params = bc.parameters

    if role == "inlet":
        vel = float(params.get("velocity", 0.0))
        vel_str = fmt_num(vel)
        return {"type": "fixedValue", "value": f"uniform ({vel_str} 0 0)"}
    if role == "outlet":
        return {"type": "zeroGradient"}
    if role == "wall":
        if bc_type == "slipWall":
            return {"type": "slip"}
        return {"type": "noSlip"}
    if role == "symmetry":
        return {"type": "symmetryPlane"}
    if role == "empty":
        return {"type": "empty"}
    if role == "freestream":
        return {"type": "slip"}
    if role == "cyclic":
        return {"type": "cyclic"}
    return {"type": "zeroGradient"}


# ---------------------------------------------------------------------------
# Pressure field (0/p)
# ---------------------------------------------------------------------------


def compile_pressure_field(boundaries: BoundaryDefinition) -> str:
    """Produce the ``0/p`` (volScalarField) dictionary string.

    Boundary role mapping
    ---------------------
    * ``inlet``    → ``zeroGradient``
    * ``outlet``   → ``fixedValue`` with ``uniform 0``
    * ``wall``     → ``zeroGradient``
    * ``symmetry`` → ``symmetryPlane``
    * ``empty``    → ``empty``
    """
    lines: list[str] = []
    lines.append(foam_file_header("volScalarField", "p"))
    lines.append("")
    lines.append("dimensions      [0 2 -2 0 0 0 0];")
    lines.append("")
    lines.append("internalField   uniform 0;")
    lines.append("")
    lines.append("boundaryField")
    lines.append("{")

    for bc in boundaries.conditions:
        entries = _pressure_bc(bc)
        lines.append(f"    {bc.patch_name}")
        lines.append("    {")
        for key, val in entries.items():
            lines.append(f"        {key:<16} {val};")
        lines.append("    }")
        lines.append("")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _pressure_bc(bc: BoundaryCondition) -> dict[str, str]:
    """Return OpenFOAM BC entries for the pressure field."""
    role = bc.role
    params = bc.parameters

    if role == "inlet":
        return {"type": "zeroGradient"}
    if role == "outlet":
        pressure = float(params.get("pressure", 0.0))
        return {"type": "fixedValue", "value": f"uniform {fmt_num(pressure)}"}
    if role == "wall":
        return {"type": "zeroGradient"}
    if role == "symmetry":
        return {"type": "symmetryPlane"}
    if role == "empty":
        return {"type": "empty"}
    if role == "freestream":
        return {"type": "zeroGradient"}
    return {"type": "zeroGradient"}


# ---------------------------------------------------------------------------
# nuTilda field (0/nuTilda) — only for turbulent cases
# ---------------------------------------------------------------------------


def compile_nu_tilda_field(boundaries: BoundaryDefinition) -> str:
    """Produce the ``0/nuTilda`` (volScalarField) dictionary string."""
    lines: list[str] = []
    lines.append(foam_file_header("volScalarField", "nuTilda"))
    lines.append("")
    lines.append("dimensions      [0 2 -1 0 0 0 0];")
    lines.append("")
    lines.append("internalField   uniform 0;")
    lines.append("")
    lines.append("boundaryField")
    lines.append("{")

    for bc in boundaries.conditions:
        entries = _nu_tilda_bc(bc)
        lines.append(f"    {bc.patch_name}")
        lines.append("    {")
        for key, val in entries.items():
            lines.append(f"        {key:<16} {val};")
        lines.append("    }")
        lines.append("")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _nu_tilda_bc(bc: BoundaryCondition) -> dict[str, str]:
    """Return OpenFOAM BC entries for the nuTilda field."""
    role = bc.role
    if role == "inlet":
        return {"type": "fixedValue", "value": "uniform 0"}
    if role == "outlet":
        return {"type": "zeroGradient"}
    if role == "wall":
        return {"type": "zeroGradient"}
    if role == "symmetry":
        return {"type": "symmetryPlane"}
    if role == "empty":
        return {"type": "empty"}
    return {"type": "zeroGradient"}
