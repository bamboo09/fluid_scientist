"""Compiler for the OpenFOAM ``constant/transportProperties`` file.

Maps :class:`PhysicsDefinition` material / viscosity to the
``transportProperties`` dictionary.
"""

from __future__ import annotations

from fluid_scientist.study_spec.models import PhysicsDefinition

from ._common import foam_file_header, fmt_num, sourced_numeric, sourced_raw

__all__ = ["compile_transport_properties"]


# Default kinematic viscosities [m^2/s] at 20 C.
_DEFAULT_NU: dict[str, float] = {
    "air": 1.5e-5,
    "water": 1.0e-6,
}


def compile_transport_properties(physics: PhysicsDefinition) -> str:
    """Produce the ``transportProperties`` dictionary string.

    For a Newtonian fluid the key entry is ``nu`` (kinematic viscosity).
    If the spec provides ``kinematic_viscosity`` it is used directly;
    otherwise a default based on the material name is chosen:

    * air at 20 C   → ``nu = 1.5e-5``
    * water at 20 C → ``nu = 1.0e-6``
    """
    lines: list[str] = []
    lines.append(foam_file_header("dictionary", "transportProperties"))
    lines.append("")

    material = str(sourced_raw(physics.material) or "").lower()

    nu = sourced_numeric(physics.kinematic_viscosity)
    if nu is None:
        nu = _default_nu(material)

    lines.append("transportModel  Newtonian;")
    lines.append("")
    lines.append(f"nu              {fmt_num(nu)};")
    lines.append("")

    # Include density if available.
    rho = sourced_numeric(physics.density)
    if rho is not None:
        lines.append(f"rho             {fmt_num(rho)};")
        lines.append("")

    return "\n".join(lines)


def _default_nu(material: str) -> float:
    """Return a default kinematic viscosity for the given material name."""
    for key, nu in _DEFAULT_NU.items():
        if key in material:
            return nu
    return _DEFAULT_NU["water"]
