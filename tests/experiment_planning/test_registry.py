from types import MappingProxyType

import pytest

import fluid_scientist.experiment_planning as planning
from fluid_scientist.experiment_planning.registry import (
    CAPABILITIES,
    CUSTOM_UPLOAD,
    ExperimentCapability,
    UnknownExperimentType,
    get_experiment_capability,
)


def test_registry_has_exactly_the_supported_experiment_types() -> None:
    assert isinstance(CAPABILITIES, MappingProxyType)
    assert tuple(CAPABILITIES) == (
        "laminar_pipe",
        "cylinder_flow",
        "lid_driven_cavity",
        "custom_openfoam",
    )
    assert all(isinstance(item, ExperimentCapability) for item in CAPABILITIES.values())
    assert all(item.solver == "incompressibleFluid" for item in CAPABILITIES.values())
    assert all(
        item.label.encode("utf-8").decode("utf-8") == item.label
        for item in CAPABILITIES.values()
    )


def test_registry_declares_fixed_preprocessing_and_outputs() -> None:
    cylinder = get_experiment_capability("cylinder_flow")
    cavity = get_experiment_capability("lid_driven_cavity")

    assert cylinder.preprocessing == ("blockMesh", "mirrorMesh", "checkMesh")
    assert cavity.preprocessing == ("blockMesh", "checkMesh")
    assert cylinder.required_outputs == (
        "drag_coefficient",
        "lift_coefficient",
        "strouhal_number",
        "residuals",
        "time_directories",
    )
    assert cavity.required_outputs == ("velocity_probes", "residuals", "time_directories")


def test_custom_openfoam_routes_only_to_explicit_upload_marker() -> None:
    custom = get_experiment_capability("custom_openfoam")

    assert custom.compiler is CUSTOM_UPLOAD
    assert custom.preprocessing == ()


def test_unknown_experiment_type_raises_typed_error() -> None:
    with pytest.raises(UnknownExperimentType, match="not_registered"):
        get_experiment_capability("not_registered")


def test_compilation_api_is_exported_from_package() -> None:
    assert planning.CAPABILITIES is CAPABILITIES
    assert planning.compile_plan.__name__ == "compile_plan"
    assert planning.CompiledCase.__name__ == "CompiledCase"
    assert planning.UnsupportedCompilation.__name__ == "UnsupportedCompilation"
