"""Base packs for the OpenFOAM 13 component system.

A :class:`BasePack` bundles the solver-module choice and the default
template content for the five core OpenFOAM dictionary files:

* ``constant/physicalProperties``
* ``constant/momentumTransport``
* ``system/fvSchemes``
* ``system/fvSolution``
* ``system/controlDict``

Each base pack is frozen (immutable) so that it can be safely shared
across multiple compiler instances without risk of mutation.

All template strings contain real Foundation 13 OpenFOAM dictionary
content using the ``foamRun -solver <module>`` syntax.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------


class _ComponentBase(BaseModel):
    """Shared configuration for all component models.

    ``extra="forbid"`` rejects unexpected fields so schema drift is
    caught early.  ``frozen=True`` makes every instance immutable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class BasePack(_ComponentBase):
    """A bundle of solver-module choice and default dictionary templates.

    Attributes:
        pack_id: Unique identifier for the pack.
        description: Human-readable description.
        solver_module: The foamRun solver module name, e.g.
            ``"incompressibleFluid"``.
        application: The top-level application, always ``"foamRun"`` for
            Foundation 13.
        time_mode: ``"steady"`` or ``"transient"``.
        turbulence_support: List of turbulence model names this pack
            supports (e.g. ``["laminar"]``).
        required_fields: Field files that must exist in the ``0/``
            directory.
        physical_properties_template: Template lines for
            ``constant/physicalProperties``.
        momentum_transport_template: Template lines for
            ``constant/momentumTransport``.
        fv_schemes_template: Template lines for ``system/fvSchemes``.
        fv_solution_template: Template lines for ``system/fvSolution``.
        control_dict_template: Template lines for ``system/controlDict``.
    """

    pack_id: str
    description: str
    solver_module: str = "incompressibleFluid"
    application: str = "foamRun"
    time_mode: Literal["steady", "transient"] = "transient"
    turbulence_support: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    physical_properties_template: dict[str, str] = Field(default_factory=dict)
    momentum_transport_template: dict[str, str] = Field(default_factory=dict)
    fv_schemes_template: dict[str, str] = Field(default_factory=dict)
    fv_solution_template: dict[str, str] = Field(default_factory=dict)
    control_dict_template: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Base Pack 1: incompressible laminar transient
# ---------------------------------------------------------------------------

FOUNDATION13_INCOMPRESSIBLE_LAMINAR_TRANSIENT = BasePack(
    pack_id="foundation13-incompressible-laminar-transient",
    description="Incompressible laminar transient flow (foamRun -solver incompressibleFluid)",
    solver_module="incompressibleFluid",
    application="foamRun",
    time_mode="transient",
    turbulence_support=["laminar"],
    required_fields=["U", "p"],
    physical_properties_template={
        "viscosityModel": "constant;",
        "nu": "nu [0 2 -1 0 0 0 0] 1e-06;",
    },
    momentum_transport_template={
        "simulationType": "laminar;",
    },
    fv_schemes_template={
        "ddtSchemes": "default Euler;",
        "gradSchemes": "default Gauss linear;",
        "divSchemes": "default none; div(phi,U) bounded Gauss linearUpwind grad(U); div((nuEff*dev2(T(grad(U))))) Gauss linear;",
        "laplacianSchemes": "default Gauss linear corrected;",
        "interpolationSchemes": "default linear;",
        "snGradSchemes": "default corrected;",
    },
    fv_solution_template={
        "solvers": "p { solver GAMG; smoother GaussSeidel; tolerance 1e-06; relTol 0.01; } pFinal { $p; relTol 0; } U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0; } UFinal { $U; relTol 0; }",
        "PIMPLE": "nNonOrthogonalCorrectors 0; nCorrectors 2; residualControl { U 1e-06; p 1e-06; }",
    },
    control_dict_template={
        "application": "foamRun;",
        "solver": "incompressibleFluid;",
        "startFrom": "startTime;",
        "startTime": "0;",
        "stopAt": "endTime;",
        "endTime": "1000;",
        "deltaT": "0.01;",
        "writeControl": "timeStep;",
        "writeInterval": "100;",
        "purgeWrite": "2;",
        "writeFormat": "ascii;",
        "writePrecision": "10;",
        "writeCompression": "off;",
        "timeFormat": "general;",
        "timePrecision": "6;",
        "runTimeModifiable": "true;",
    },
)


# ---------------------------------------------------------------------------
# Base Pack 2: incompressible RANS steady
# ---------------------------------------------------------------------------

FOUNDATION13_INCOMPRESSIBLE_RANS_STEADY = BasePack(
    pack_id="foundation13-incompressible-rans-steady",
    description="Incompressible RANS steady flow (foamRun -solver incompressibleFluid)",
    solver_module="incompressibleFluid",
    application="foamRun",
    time_mode="steady",
    turbulence_support=["kOmegaSST", "kEpsilon", "SpalartAllmaras"],
    required_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    physical_properties_template={
        "viscosityModel": "constant;",
        "nu": "nu [0 2 -1 0 0 0 0] 1e-06;",
    },
    momentum_transport_template={
        "simulationType": "RANS;",
        "RAS": "model kOmegaSST; turbulence on; printCoeffs on;",
    },
    fv_schemes_template={
        "ddtSchemes": "default steadyState;",
        "gradSchemes": "default Gauss linear;",
        "divSchemes": "default none; div(phi,U) bounded Gauss linearUpwind grad(U); div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind; div(phi,epsilon) bounded Gauss upwind; div(phi,nuTilda) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear;",
        "laplacianSchemes": "default Gauss linear corrected;",
        "interpolationSchemes": "default linear;",
        "snGradSchemes": "default corrected;",
    },
    fv_solution_template={
        "solvers": "p { solver GAMG; smoother GaussSeidel; tolerance 1e-07; relTol 0.01; } U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0.1; } k { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0.1; } omega { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0.1; } epsilon { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0.1; } nuTilda { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0.1; } nut { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0.1; }",
        "SIMPLE": "nNonOrthogonalCorrectors 0; consistent yes; residualControl { U 1e-05; p 1e-05; k 1e-05; omega 1e-05; }",
        "relaxationFactors": "fields { p 0.3; } equations { U 0.7; k 0.7; omega 0.7; epsilon 0.7; nuTilda 0.7; }",
    },
    control_dict_template={
        "application": "foamRun;",
        "solver": "incompressibleFluid;",
        "startFrom": "startTime;",
        "startTime": "0;",
        "stopAt": "endTime;",
        "endTime": "5000;",
        "deltaT": "1;",
        "writeControl": "timeStep;",
        "writeInterval": "500;",
        "purgeWrite": "2;",
        "writeFormat": "ascii;",
        "writePrecision": "10;",
        "writeCompression": "off;",
        "timeFormat": "general;",
        "timePrecision": "6;",
        "runTimeModifiable": "true;",
    },
)


# ---------------------------------------------------------------------------
# Base Pack 3: incompressible RANS transient
# ---------------------------------------------------------------------------

FOUNDATION13_INCOMPRESSIBLE_RANS_TRANSIENT = BasePack(
    pack_id="foundation13-incompressible-rans-transient",
    description="Incompressible RANS transient flow (foamRun -solver incompressibleFluid)",
    solver_module="incompressibleFluid",
    application="foamRun",
    time_mode="transient",
    turbulence_support=["kOmegaSST", "kEpsilon", "SpalartAllmaras"],
    required_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    physical_properties_template={
        "viscosityModel": "constant;",
        "nu": "nu [0 2 -1 0 0 0 0] 1e-06;",
    },
    momentum_transport_template={
        "simulationType": "RANS;",
        "RAS": "model kOmegaSST; turbulence on; printCoeffs on;",
    },
    fv_schemes_template={
        "ddtSchemes": "default backward;",
        "gradSchemes": "default Gauss linear;",
        "divSchemes": "default none; div(phi,U) bounded Gauss linearUpwind grad(U); div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind; div(phi,epsilon) bounded Gauss upwind; div(phi,nuTilda) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear;",
        "laplacianSchemes": "default Gauss linear corrected;",
        "interpolationSchemes": "default linear;",
        "snGradSchemes": "default corrected;",
    },
    fv_solution_template={
        "solvers": "p { solver GAMG; smoother GaussSeidel; tolerance 1e-06; relTol 0.01; } pFinal { $p; relTol 0; } U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0; } UFinal { $U; relTol 0; } k { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0; } kFinal { $k; relTol 0; } omega { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0; } omegaFinal { $omega; relTol 0; } epsilon { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0; } epsilonFinal { $epsilon; relTol 0; } nuTilda { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0; } nuTildaFinal { $nuTilda; relTol 0; }",
        "PIMPLE": "nNonOrthogonalCorrectors 0; nCorrectors 2; nOuterCorrectors 1; residualControl { U 1e-06; p 1e-06; k 1e-06; omega 1e-06; }",
    },
    control_dict_template={
        "application": "foamRun;",
        "solver": "incompressibleFluid;",
        "startFrom": "startTime;",
        "startTime": "0;",
        "stopAt": "endTime;",
        "endTime": "10;",
        "deltaT": "0.001;",
        "writeControl": "adjustableRunTime;",
        "writeInterval": "0.1;",
        "purgeWrite": "2;",
        "writeFormat": "ascii;",
        "writePrecision": "10;",
        "writeCompression": "off;",
        "timeFormat": "general;",
        "timePrecision": "6;",
        "runTimeModifiable": "true;",
        "maxCo": "0.5;",
        "adjustTimeStep": "yes;",
    },
)


# ---------------------------------------------------------------------------
# Base Pack 4: incompressible LES transient
# ---------------------------------------------------------------------------

FOUNDATION13_INCOMPRESSIBLE_LES_TRANSIENT = BasePack(
    pack_id="foundation13-incompressible-les-transient",
    description="Incompressible LES transient flow (foamRun -solver incompressibleFluid)",
    solver_module="incompressibleFluid",
    application="foamRun",
    time_mode="transient",
    turbulence_support=["LESWALE", "LESSmagorinsky"],
    required_fields=["U", "p", "nut"],
    physical_properties_template={
        "viscosityModel": "constant;",
        "nu": "nu [0 2 -1 0 0 0 0] 1e-06;",
    },
    momentum_transport_template={
        "simulationType": "LES;",
        "LES": "model WALE; turbulence on; printCoeffs on; delta cubeRootVol;",
    },
    fv_schemes_template={
        "ddtSchemes": "default backward;",
        "gradSchemes": "default Gauss linear;",
        "divSchemes": "default none; div(phi,U) bounded Gauss linearUpwind grad(U); div((nuEff*dev2(T(grad(U))))) Gauss linear;",
        "laplacianSchemes": "default Gauss linear corrected;",
        "interpolationSchemes": "default linear;",
        "snGradSchemes": "default corrected;",
    },
    fv_solution_template={
        "solvers": "p { solver GAMG; smoother GaussSeidel; tolerance 1e-06; relTol 0.01; } pFinal { $p; relTol 0; } U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0; } UFinal { $U; relTol 0; }",
        "PIMPLE": "nNonOrthogonalCorrectors 0; nCorrectors 2; nOuterCorrectors 1; residualControl { U 1e-06; p 1e-06; }",
    },
    control_dict_template={
        "application": "foamRun;",
        "solver": "incompressibleFluid;",
        "startFrom": "startTime;",
        "startTime": "0;",
        "stopAt": "endTime;",
        "endTime": "10;",
        "deltaT": "0.0005;",
        "writeControl": "adjustableRunTime;",
        "writeInterval": "0.05;",
        "purgeWrite": "2;",
        "writeFormat": "ascii;",
        "writePrecision": "10;",
        "writeCompression": "off;",
        "timeFormat": "general;",
        "timePrecision": "6;",
        "runTimeModifiable": "true;",
        "maxCo": "0.5;",
        "adjustTimeStep": "yes;",
    },
)


# ---------------------------------------------------------------------------
# Registry of all base packs
# ---------------------------------------------------------------------------

BASE_PACKS: dict[str, BasePack] = {
    FOUNDATION13_INCOMPRESSIBLE_LAMINAR_TRANSIENT.pack_id: FOUNDATION13_INCOMPRESSIBLE_LAMINAR_TRANSIENT,
    FOUNDATION13_INCOMPRESSIBLE_RANS_STEADY.pack_id: FOUNDATION13_INCOMPRESSIBLE_RANS_STEADY,
    FOUNDATION13_INCOMPRESSIBLE_RANS_TRANSIENT.pack_id: FOUNDATION13_INCOMPRESSIBLE_RANS_TRANSIENT,
    FOUNDATION13_INCOMPRESSIBLE_LES_TRANSIENT.pack_id: FOUNDATION13_INCOMPRESSIBLE_LES_TRANSIENT,
}


def get_base_pack(pack_id: str) -> BasePack | None:
    """Look up a base pack by its identifier.

    Returns ``None`` if the pack id is not registered.
    """
    return BASE_PACKS.get(pack_id)


__all__ = [
    "BASE_PACKS",
    "FOUNDATION13_INCOMPRESSIBLE_LAMINAR_TRANSIENT",
    "FOUNDATION13_INCOMPRESSIBLE_LES_TRANSIENT",
    "FOUNDATION13_INCOMPRESSIBLE_RANS_STEADY",
    "FOUNDATION13_INCOMPRESSIBLE_RANS_TRANSIENT",
    "BasePack",
    "get_base_pack",
]
