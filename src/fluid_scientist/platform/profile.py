"""OpenFOAM Platform Profile — Foundation 13 locked configuration.

This module defines the single, authoritative PlatformProfile that every
compiler, validator, and capability component MUST reference.  It locks
the OpenFOAM distribution to Foundation 13, defines the file naming
conventions (physicalProperties vs transportProperties), the solver
module mapping (foamRun -solver <module>), the turbulence field
dependencies, and the security policy.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Security policy
# ---------------------------------------------------------------------------

class SecurityPolicy(BaseModel):
    """Security constraints for generated OpenFOAM cases.

    Any case submitted to the workstation MUST comply with these rules.
    The static validator enforces them before any case is allowed to
    reach READY_TO_SUBMIT.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    allow_code_stream: bool = False
    allow_case_local_dynamic_libs: bool = False
    allow_shell_variables: bool = False
    allow_external_include: bool = False
    allow_arbitrary_scripts: bool = False
    allow_arbitrary_executables: bool = False

    def validate_dict_content(self, content: str) -> list[str]:
        """Return a list of security violations found in *content*."""
        violations: list[str] = []
        if not self.allow_code_stream and "codeStream" in content:
            violations.append("codeStream is forbidden by platform security policy")
        if not self.allow_case_local_dynamic_libs and "libs (" in content:
            violations.append("case-local dynamic libs are forbidden by platform security policy")
        if not self.allow_shell_variables and "$(" in content:
            violations.append("shell variable expansion '$(' is forbidden by platform security policy")
        if not self.allow_external_include and "#include" in content and "Etc/" not in content:
            violations.append("external #include is forbidden by platform security policy")
        if not self.allow_arbitrary_scripts and ("systemCall" in content or "Allrun" in content):
            violations.append("arbitrary scripts/systemCall are forbidden by platform security policy")
        return violations


# ---------------------------------------------------------------------------
# Turbulence field dependencies
# ---------------------------------------------------------------------------

class TurbulenceFieldDependency(BaseModel):
    """Defines which fields are required for a given turbulence model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str
    required_fields: tuple[str, ...]
    required_field_classes: dict[str, str] = Field(default_factory=dict)
    nut_required: bool = True

    def missing_fields(self, available_fields: set[str]) -> list[str]:
        """Return the list of required fields not present in *available_fields*."""
        return [f for f in self.required_fields if f not in available_fields]


# Pre-built turbulence dependencies for Foundation 13
TURBULENCE_DEPENDENCIES: dict[str, TurbulenceFieldDependency] = {
    "laminar": TurbulenceFieldDependency(
        model_name="laminar",
        required_fields=("U", "p"),
        nut_required=False,
    ),
    "kOmegaSST": TurbulenceFieldDependency(
        model_name="kOmegaSST",
        required_fields=("U", "p", "k", "omega", "nut"),
        required_field_classes={
            "U": "volVectorField",
            "p": "volScalarField",
            "k": "volScalarField",
            "omega": "volScalarField",
            "nut": "volScalarField",
        },
    ),
    "kEpsilon": TurbulenceFieldDependency(
        model_name="kEpsilon",
        required_fields=("U", "p", "k", "epsilon", "nut"),
        required_field_classes={
            "U": "volVectorField",
            "p": "volScalarField",
            "k": "volScalarField",
            "epsilon": "volScalarField",
            "nut": "volScalarField",
        },
    ),
    "SpalartAllmaras": TurbulenceFieldDependency(
        model_name="SpalartAllmaras",
        required_fields=("U", "p", "nuTilda", "nut"),
        required_field_classes={
            "U": "volVectorField",
            "p": "volScalarField",
            "nuTilda": "volScalarField",
            "nut": "volScalarField",
        },
    ),
    "LESWALE": TurbulenceFieldDependency(
        model_name="LESWALE",
        required_fields=("U", "p", "nut"),
        required_field_classes={
            "U": "volVectorField",
            "p": "volScalarField",
            "nut": "volScalarField",
        },
    ),
    "LESSmagorinsky": TurbulenceFieldDependency(
        model_name="LESSmagorinsky",
        required_fields=("U", "p", "nut"),
        required_field_classes={
            "U": "volVectorField",
            "p": "volScalarField",
            "nut": "volScalarField",
        },
    ),
}


# ---------------------------------------------------------------------------
# Solver module info
# ---------------------------------------------------------------------------

class SolverModuleInfo(BaseModel):
    """Information about a foamRun solver module."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    module_name: str
    application: str = "foamRun"
    time_mode: Literal["steady", "transient"]
    coupling: str
    compressibility: Literal["incompressible", "compressible"] = "incompressible"
    description: str = ""


# ---------------------------------------------------------------------------
# Legacy solver migration map
# ---------------------------------------------------------------------------

LEGACY_SOLVER_MAP: dict[str, SolverModuleInfo] = {
    "simpleFoam": SolverModuleInfo(
        module_name="incompressibleFluid",
        time_mode="steady",
        coupling="SIMPLE",
        description="Steady-state incompressible flow (legacy: simpleFoam)",
    ),
    "pimpleFoam": SolverModuleInfo(
        module_name="incompressibleFluid",
        time_mode="transient",
        coupling="PIMPLE",
        description="Transient incompressible flow (legacy: pimpleFoam)",
    ),
    "icoFoam": SolverModuleInfo(
        module_name="incompressibleFluid",
        time_mode="transient",
        coupling="laminar",
        description="Laminar transient incompressible flow (legacy: icoFoam)",
    ),
    "pisoFoam": SolverModuleInfo(
        module_name="incompressibleFluid",
        time_mode="transient",
        coupling="PIMPLE-compatible",
        description="Transient incompressible flow PISO (legacy: pisoFoam)",
    ),
    "rhoSimpleFoam": SolverModuleInfo(
        module_name="fluid",
        time_mode="steady",
        coupling="SIMPLE",
        compressibility="compressible",
        description="Steady-state compressible flow (legacy: rhoSimpleFoam)",
    ),
    "rhoPimpleFoam": SolverModuleInfo(
        module_name="fluid",
        time_mode="transient",
        coupling="PIMPLE",
        compressibility="compressible",
        description="Transient compressible flow (legacy: rhoPimpleFoam)",
    ),
}


def migrate_legacy_solver(legacy_name: str) -> SolverModuleInfo | None:
    """Migrate a legacy solver name to the Foundation 13 solver module.

    Returns None if the solver name is not a recognized legacy solver.
    """
    return LEGACY_SOLVER_MAP.get(legacy_name)


# ---------------------------------------------------------------------------
# Platform profile
# ---------------------------------------------------------------------------

class PlatformProfile(BaseModel):
    """The authoritative OpenFOAM platform configuration.

    All compilers, validators, and capability components MUST reference
    the same PlatformProfile instance to ensure version consistency.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = "openfoam-foundation-13"
    distribution: Literal["OpenFOAMFoundation", "OpenCFD"] = "OpenFOAMFoundation"
    version: str = "13"
    application: str = "foamRun"
    default_solver_module: str = "incompressibleFluid"
    physical_properties_file: str = "constant/physicalProperties"
    momentum_transport_file: str = "constant/momentumTransport"
    transport_properties_file: str = "constant/transportProperties"  # DEPRECATED — must NOT be generated
    supports_dry_run: bool = False
    security_policy: SecurityPolicy = Field(default_factory=SecurityPolicy)

    # File conventions
    system_files: tuple[str, ...] = (
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
    )
    constant_files: tuple[str, ...] = (
        "constant/physicalProperties",
        "constant/momentumTransport",
    )

    # Forbidden files (legacy v2406/v2312 conventions)
    forbidden_files: tuple[str, ...] = (
        "constant/transportProperties",
        "constant/turbulenceProperties",
    )

    # Workstation probe commands
    probe_commands: tuple[str, ...] = (
        "foamVersion",
        "foamRun -help",
        "foamToC -solvers",
        "foamToC -vectorBCs",
        "foamToC -scalarBCs",
        "foamToC -functionObjects",
        "foamPostProcess -list",
    )

    @property
    def is_foundation(self) -> bool:
        """True if this is an OpenFOAM Foundation distribution."""
        return self.distribution == "OpenFOAMFoundation"

    @property
    def run_command(self) -> str:
        """The default run command for this platform."""
        return f"{self.application} -solver {self.default_solver_module}"

    def get_turbulence_dependency(self, model_name: str) -> TurbulenceFieldDependency | None:
        """Get the field dependency for a turbulence model."""
        return TURBULENCE_DEPENDENCIES.get(model_name)

    def is_forbidden_file(self, file_path: str) -> bool:
        """Check if a file path is forbidden by this platform profile."""
        return file_path in self.forbidden_files

    def is_legacy_solver(self, solver_name: str) -> bool:
        """Check if a solver name is a legacy (pre-Foundation-13) solver."""
        return solver_name in LEGACY_SOLVER_MAP

    def validate_solver_module(self, module_name: str) -> bool:
        """Validate that a solver module name is acceptable."""
        # Foundation 13 known solver modules
        known_modules = {
            "incompressibleFluid",
            "fluid",
            "isothermalFluid",
            "multiphaseEulerFoam",
            "reactingFoam",
            "rhoFluid",
            "shockFluid",
            "porousSimpleFoam",
        }
        return module_name in known_modules

    def get_required_constant_files(self, solver_module: str) -> list[str]:
        """Return the required constant/ files for a given solver module."""
        files: list[str] = []
        if solver_module in ("incompressibleFluid", "fluid", "isothermalFluid"):
            files.append(self.physical_properties_file)
            files.append(self.momentum_transport_file)
        return files

    def get_required_system_files(self) -> list[str]:
        """Return the required system/ files."""
        return list(self.system_files)

    def get_required_initial_fields(
        self, solver_module: str, turbulence_model: str = "laminar"
    ) -> list[str]:
        """Return the required 0/ field files."""
        fields = ["U", "p"]
        dep = self.get_turbulence_dependency(turbulence_model)
        if dep:
            for f in dep.required_fields:
                if f not in fields:
                    fields.append(f)
        return fields


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_PROFILE: PlatformProfile | None = None


def get_platform_profile() -> PlatformProfile:
    """Return the singleton PlatformProfile for this runtime.

    All modules should call this function rather than constructing
    their own PlatformProfile, to ensure version consistency.
    """
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = PlatformProfile()
    return _PROFILE
