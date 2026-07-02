"""Immutable registry of supported CFD experiment capabilities."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias

ExperimentType: TypeAlias = Literal[
    "laminar_pipe", "cylinder_flow", "lid_driven_cavity", "custom_openfoam"
]
Preprocessor: TypeAlias = Literal["blockMesh", "mirrorMesh", "checkMesh"]


class UnknownExperimentType(LookupError):
    """Raised when a requested experiment type is not registered."""


@dataclass(frozen=True)
class CustomUploadMarker:
    """Explicitly identifies plans that require a reviewed user archive."""

    route: Literal["custom_upload"] = "custom_upload"


CUSTOM_UPLOAD = CustomUploadMarker()
Compiler: TypeAlias = Callable[[object], object]


def _pipe_compiler(plan: object) -> object:
    from fluid_scientist.experiment_planning.compilers import compile_pipe_plan

    return compile_pipe_plan(plan)


def _cylinder_compiler(plan: object) -> object:
    from fluid_scientist.experiment_planning.compilers import compile_cylinder_plan

    return compile_cylinder_plan(plan)


def _cavity_compiler(plan: object) -> object:
    from fluid_scientist.experiment_planning.compilers import compile_cavity_plan

    return compile_cavity_plan(plan)


@dataclass(frozen=True)
class ExperimentCapability:
    """Execution metadata for one exact planning-contract variant."""

    experiment_type: ExperimentType
    label: str
    solver: Literal["incompressibleFluid"]
    preprocessing: tuple[Preprocessor, ...]
    required_outputs: tuple[str, ...]
    compiler: Compiler | CustomUploadMarker


CAPABILITIES: Mapping[str, ExperimentCapability] = MappingProxyType(
    {
        "laminar_pipe": ExperimentCapability(
            experiment_type="laminar_pipe",
            label="Laminar pipe / 层流圆管",
            solver="incompressibleFluid",
            preprocessing=("blockMesh", "checkMesh"),
            required_outputs=(
                "pressure_drop",
                "mass_imbalance",
                "residuals",
                "time_directories",
            ),
            compiler=_pipe_compiler,
        ),
        "cylinder_flow": ExperimentCapability(
            experiment_type="cylinder_flow",
            label="Cylinder flow / 圆柱绕流",
            solver="incompressibleFluid",
            preprocessing=("blockMesh", "mirrorMesh", "checkMesh"),
            required_outputs=(
                "drag_coefficient",
                "lift_coefficient",
                "strouhal_number",
                "residuals",
                "time_directories",
            ),
            compiler=_cylinder_compiler,
        ),
        "lid_driven_cavity": ExperimentCapability(
            experiment_type="lid_driven_cavity",
            label="Lid-driven cavity / 顶盖驱动方腔",
            solver="incompressibleFluid",
            preprocessing=("blockMesh", "checkMesh"),
            required_outputs=("velocity_probes", "residuals", "time_directories"),
            compiler=_cavity_compiler,
        ),
        "custom_openfoam": ExperimentCapability(
            experiment_type="custom_openfoam",
            label="Custom OpenFOAM upload / 自定义 OpenFOAM 上传",
            solver="incompressibleFluid",
            preprocessing=(),
            required_outputs=(),
            compiler=CUSTOM_UPLOAD,
        ),
    }
)


def get_experiment_capability(experiment_type: str) -> ExperimentCapability:
    """Return exact capability metadata or raise a stable typed error."""

    try:
        return CAPABILITIES[experiment_type]
    except KeyError as error:
        raise UnknownExperimentType(f"unknown experiment type: {experiment_type}") from error
