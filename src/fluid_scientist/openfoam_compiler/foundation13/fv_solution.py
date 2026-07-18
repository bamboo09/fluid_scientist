"""Compiler for the OpenFOAM ``system/fvSolution`` file.

Maps solver settings and the algorithm choice (derived from the solver
name) to a valid OpenFOAM fvSolution dictionary.
"""

from __future__ import annotations

from fluid_scientist.study_spec.numerics import NumericsDefinition

from ._common import foam_file_header

__all__ = ["compile_fv_solution"]


def compile_fv_solution(numerics: NumericsDefinition) -> str:
    """Produce the ``fvSolution`` dictionary string.

    Solvers
    -------
    * **p / pFinal** — PCG with DIC preconditioner.
    * **U** — PBiCGStab with DILU preconditioner.
    * **k** (turbulent) — PBiCGStab with DILU preconditioner.

    The algorithm block (``SIMPLE`` / ``PIMPLE`` / ``PISO``) is derived
    from the solver name.
    """
    lines: list[str] = []
    lines.append(foam_file_header("dictionary", "fvSolution"))
    lines.append("")

    is_turbulent = (
        numerics.turbulence_model is not None
        and numerics.turbulence_model != "laminar"
    )

    # --- solvers ---
    lines.append("solvers")
    lines.append("{")
    lines.append("    p")
    lines.append("    {")
    lines.append("        solver          PCG;")
    lines.append("        preconditioner  DIC;")
    lines.append("        tolerance       1e-06;")
    lines.append("        relTol          0.01;")
    lines.append("    }")
    lines.append("")
    lines.append("    pFinal")
    lines.append("    {")
    lines.append("        $p;")
    lines.append("        relTol          0;")
    lines.append("    }")
    lines.append("")
    lines.append("    U")
    lines.append("    {")
    lines.append("        solver          PBiCGStab;")
    lines.append("        preconditioner  DILU;")
    lines.append("        tolerance       1e-05;")
    lines.append("        relTol          0.1;")
    lines.append("    }")

    if is_turbulent:
        lines.append("")
        lines.append("    k")
        lines.append("    {")
        lines.append("        solver          PBiCGStab;")
        lines.append("        preconditioner  DILU;")
        lines.append("        tolerance       1e-05;")
        lines.append("        relTol          0.1;")
        lines.append("    }")

    lines.append("}")
    lines.append("")

    # --- algorithm block ---
    algo = _solver_to_algorithm(numerics.solver)
    lines.append(algo)
    lines.append("{")
    if algo == "SIMPLE":
        lines.append("    nNonOrthogonalCorrectors 0;")
        lines.append("    pRefCell       0;")
        lines.append("    pRefValue      0;")
        lines.append("    consistent     yes;")
    elif algo == "PIMPLE":
        lines.append("    momentumPredictor yes;")
        lines.append("    nOuterCorrectors 1;")
        lines.append("    nCorrectors     2;")
        lines.append("    nNonOrthogonalCorrectors 0;")
    else:  # PISO
        lines.append("    nOuterCorrectors 1;")
        lines.append("    nCorrectors     2;")
        lines.append("    nNonOrthogonalCorrectors 0;")
    lines.append("}")
    lines.append("")

    # --- relaxation factors for SIMPLE ---
    if algo == "SIMPLE":
        lines.append("relaxationFactors")
        lines.append("{")
        lines.append("    equations")
        lines.append("    {")
        lines.append("        U               0.7;")
        if is_turbulent:
            lines.append("        k               0.7;")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def _solver_to_algorithm(solver: str) -> str:
    """Derive the OpenFOAM algorithm name from the solver identifier."""
    s = solver.lower()
    if "simple" in s:
        return "SIMPLE"
    if "pimple" in s:
        return "PIMPLE"
    if "piso" in s:
        return "PISO"
    if "ico" in s:
        return "PISO"
    return "PIMPLE"
