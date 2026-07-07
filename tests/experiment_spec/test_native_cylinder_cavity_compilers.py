"""Tests for the native CylinderFlowCompiler and CavityFlowCompiler.

These tests verify that both compilers generate OpenFOAM case files directly
from ExperimentSpec parameters WITHOUT constructing old plan models
(CylinderExperimentPlan / CavityExperimentPlan) or calling old compile
functions (compile_cylinder_plan / compile_cavity_plan).
"""

from __future__ import annotations

import io
import json
import tarfile
from unittest.mock import patch

import pytest

from fluid_scientist.experiment_planning.compilers import CompiledCase
from fluid_scientist.experiment_spec.compilation import (
    MissingRequiredParameterError,
)
from fluid_scientist.experiment_spec.models import (
    Criticality,
    ExperimentSpec,
    ExperimentStatus,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)
from fluid_scientist.experiment_spec.native_compiler import (
    CavityFlowCompiler,
    CylinderFlowCompiler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _param(
    pid: str,
    value,
    *,
    data_type: str = "float",
    criticality: str = "medium",
) -> ParameterSpec:
    return ParameterSpec(
        parameter_id=pid,
        display_name=pid,
        category="test",
        value=value,
        data_type=data_type,
        source=ParameterSourceInfo(type=ParameterSource.TEMPLATE_DEFAULT),
        criticality=Criticality(criticality),
    )


def _make_cylinder_spec(
    *,
    diameter=0.1,
    reynolds_number=100.0,
    kinematic_viscosity=1e-6,
    density=998.2,
    end_time=10.0,
    cells_radial=40,
    cells_wake=120,
    max_courant=0.5,
    experiment_id: str = "cyl-native-test-001",
) -> ExperimentSpec:
    """Build a confirmed cylinder experiment spec with the given parameters."""
    return ExperimentSpec(
        experiment_id=experiment_id,
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(
            title="Cylinder Test", objective="Test cylinder flow"
        ),
        parameters=[
            _param(
                "diameter",
                diameter,
                criticality="critical" if diameter is not None else "medium",
            ),
            _param(
                "reynolds_number",
                reynolds_number,
                criticality="critical" if reynolds_number is not None else "medium",
            ),
            _param("kinematic_viscosity", kinematic_viscosity),
            _param("density", density),
            _param("end_time", end_time),
            _param("cells_radial", cells_radial, data_type="integer"),
            _param("cells_wake", cells_wake, data_type="integer"),
            _param("max_courant", max_courant),
        ],
    )


def _make_cavity_spec(
    *,
    side_length=0.1,
    lid_velocity=1.0,
    kinematic_viscosity=1e-6,
    density=998.2,
    cells_per_side=64,
    end_time=10.0,
    experiment_id: str = "cav-native-test-001",
) -> ExperimentSpec:
    """Build a confirmed cavity experiment spec with the given parameters."""
    return ExperimentSpec(
        experiment_id=experiment_id,
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(
            title="Cavity Test", objective="Test cavity flow"
        ),
        parameters=[
            _param(
                "side_length",
                side_length,
                criticality="critical" if side_length is not None else "medium",
            ),
            _param(
                "lid_velocity",
                lid_velocity,
                criticality="critical" if lid_velocity is not None else "medium",
            ),
            _param("kinematic_viscosity", kinematic_viscosity),
            _param("density", density),
            _param("cells_per_side", cells_per_side, data_type="integer"),
            _param("end_time", end_time),
        ],
    )


def _extract_archive_files(archive: bytes) -> dict[str, str]:
    """Extract all text files from a tar.gz archive into a name->content dict."""
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as bundle:
        for member in bundle.getmembers():
            if member.isfile():
                handle = bundle.extractfile(member)
                if handle is not None:
                    files[member.name] = handle.read().decode("utf-8")
    return files


# ---------------------------------------------------------------------------
# 1. CylinderFlowCompiler does NOT call compile_cylinder_plan
# ---------------------------------------------------------------------------


class TestCylinderCompilerNoCompileCylinderPlan:
    def test_cylinder_compiler_does_not_call_compile_cylinder_plan(self):
        """CylinderFlowCompiler.compile() must NOT call compile_cylinder_plan."""
        spec = _make_cylinder_spec()
        compiler = CylinderFlowCompiler()

        with patch(
            "fluid_scientist.experiment_planning.compilers.compile_cylinder_plan"
        ) as spy:
            compiled = compiler.compile(spec)
            assert spy.call_count == 0, (
                "compile_cylinder_plan must not be called by "
                "CylinderFlowCompiler.compile()"
            )

        assert isinstance(compiled, CompiledCase)


# ---------------------------------------------------------------------------
# 2. CylinderFlowCompiler does NOT construct CylinderExperimentPlan
# ---------------------------------------------------------------------------


class TestCylinderCompilerNoCylinderExperimentPlan:
    def test_cylinder_compiler_does_not_construct_cylinder_experiment_plan(self):
        """CylinderFlowCompiler.compile() must NOT construct
        CylinderExperimentPlan."""
        from fluid_scientist.experiment_planning.models import (
            CylinderExperimentPlan,
        )

        spec = _make_cylinder_spec()
        compiler = CylinderFlowCompiler()

        with patch.object(CylinderExperimentPlan, "__init__") as spy:
            spy.return_value = None
            compiled = compiler.compile(spec)
            assert spy.call_count == 0, (
                "CylinderExperimentPlan must not be constructed by "
                "CylinderFlowCompiler.compile()"
            )

        assert isinstance(compiled, CompiledCase)


# ---------------------------------------------------------------------------
# 3. CylinderFlowCompiler generates a valid case with all expected files
# ---------------------------------------------------------------------------


class TestCylinderCompilerGeneratesValidCase:
    def test_cylinder_compiler_generates_valid_case(self):
        """The compiled archive must contain all expected OpenFOAM case
        files for a cylinder flow experiment."""
        spec = _make_cylinder_spec()
        compiler = CylinderFlowCompiler()
        compiled = compiler.compile(spec)

        assert isinstance(compiled, CompiledCase)
        assert compiled.experiment_type == "cylinder_flow"
        assert compiled.archive_sha256.startswith("sha256:")

        files = _extract_archive_files(compiled.archive)

        expected_files = {
            "0/U",
            "0/p",
            "constant/momentumTransport",
            "constant/physicalProperties",
            "system/blockMeshDict",
            "system/mirrorMeshDict",
            "system/controlDict",
            "system/fvSchemes",
            "system/fvSolution",
            "fluidScientist/spec.json",
        }
        missing = expected_files - set(files.keys())
        assert not missing, f"missing expected files: {missing}"


# ---------------------------------------------------------------------------
# 4. CylinderFlowCompiler uses spec parameters in generated files
# ---------------------------------------------------------------------------


class TestCylinderCompilerUsesSpecParameters:
    def test_cylinder_compiler_uses_spec_parameters(self):
        """The blockMeshDict must contain values derived from the diameter
        specified in the spec."""
        # diameter=0.2 -> radius=0.1, extrusion_span=0.02
        spec = _make_cylinder_spec(diameter=0.2)
        compiler = CylinderFlowCompiler()
        compiled = compiler.compile(spec)

        files = _extract_archive_files(compiled.archive)
        block_mesh = files["system/blockMeshDict"]

        # extrusion_span = diameter * 0.1 = 0.02
        assert "extrusionSpan 0.02" in block_mesh


# ---------------------------------------------------------------------------
# 5. CavityFlowCompiler does NOT call compile_cavity_plan
# ---------------------------------------------------------------------------


class TestCavityCompilerNoCompileCavityPlan:
    def test_cavity_compiler_does_not_call_compile_cavity_plan(self):
        """CavityFlowCompiler.compile() must NOT call compile_cavity_plan."""
        spec = _make_cavity_spec()
        compiler = CavityFlowCompiler()

        with patch(
            "fluid_scientist.experiment_planning.compilers.compile_cavity_plan"
        ) as spy:
            compiled = compiler.compile(spec)
            assert spy.call_count == 0, (
                "compile_cavity_plan must not be called by "
                "CavityFlowCompiler.compile()"
            )

        assert isinstance(compiled, CompiledCase)


# ---------------------------------------------------------------------------
# 6. CavityFlowCompiler does NOT construct CavityExperimentPlan
# ---------------------------------------------------------------------------


class TestCavityCompilerNoCavityExperimentPlan:
    def test_cavity_compiler_does_not_construct_cavity_experiment_plan(self):
        """CavityFlowCompiler.compile() must NOT construct
        CavityExperimentPlan."""
        from fluid_scientist.experiment_planning.models import (
            CavityExperimentPlan,
        )

        spec = _make_cavity_spec()
        compiler = CavityFlowCompiler()

        with patch.object(CavityExperimentPlan, "__init__") as spy:
            spy.return_value = None
            compiled = compiler.compile(spec)
            assert spy.call_count == 0, (
                "CavityExperimentPlan must not be constructed by "
                "CavityFlowCompiler.compile()"
            )

        assert isinstance(compiled, CompiledCase)


# ---------------------------------------------------------------------------
# 7. CavityFlowCompiler generates a valid case with all expected files
# ---------------------------------------------------------------------------


class TestCavityCompilerGeneratesValidCase:
    def test_cavity_compiler_generates_valid_case(self):
        """The compiled archive must contain all expected OpenFOAM case
        files for a lid-driven cavity experiment."""
        spec = _make_cavity_spec()
        compiler = CavityFlowCompiler()
        compiled = compiler.compile(spec)

        assert isinstance(compiled, CompiledCase)
        assert compiled.experiment_type == "lid_driven_cavity"
        assert compiled.archive_sha256.startswith("sha256:")

        files = _extract_archive_files(compiled.archive)

        expected_files = {
            "0/U",
            "0/p",
            "constant/momentumTransport",
            "constant/physicalProperties",
            "system/blockMeshDict",
            "system/controlDict",
            "system/fvSchemes",
            "system/fvSolution",
            "fluidScientist/spec.json",
        }
        missing = expected_files - set(files.keys())
        assert not missing, f"missing expected files: {missing}"


# ---------------------------------------------------------------------------
# 8. CavityFlowCompiler uses spec parameters in generated files
# ---------------------------------------------------------------------------


class TestCavityCompilerUsesSpecParameters:
    def test_cavity_compiler_uses_spec_parameters(self):
        """The blockMeshDict must contain the side_length from the spec."""
        # side_length=0.2 -> vertices contain (0.2 0 0)
        spec = _make_cavity_spec(side_length=0.2)
        compiler = CavityFlowCompiler()
        compiled = compiler.compile(spec)

        files = _extract_archive_files(compiled.archive)
        block_mesh = files["system/blockMeshDict"]

        # side_length=0.2 appears in vertices
        assert "(0.2 0 0)" in block_mesh


# ---------------------------------------------------------------------------
# 9. Both compilers raise MissingRequiredParameterError on missing parameter
# ---------------------------------------------------------------------------


class TestBothCompilersRaiseOnMissingParameter:
    def test_cylinder_compiler_raises_on_missing_parameter(self):
        """Compiling a cylinder spec with diameter=None must raise
        MissingRequiredParameterError."""
        spec = _make_cylinder_spec(diameter=None)
        compiler = CylinderFlowCompiler()

        with pytest.raises(MissingRequiredParameterError, match="diameter"):
            compiler.compile(spec)

    def test_cavity_compiler_raises_on_missing_parameter(self):
        """Compiling a cavity spec with side_length=None must raise
        MissingRequiredParameterError."""
        spec = _make_cavity_spec(side_length=None)
        compiler = CavityFlowCompiler()

        with pytest.raises(MissingRequiredParameterError, match="side_length"):
            compiler.compile(spec)
