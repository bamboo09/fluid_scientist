"""Tests for the native PipeFlowCompiler — direct OpenFOAM case generation.

These tests verify that PipeFlowCompiler.compile() generates OpenFOAM case
files directly from ExperimentSpec parameters WITHOUT constructing
PipeExperimentPlan or calling compile_pipe_plan.
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
from fluid_scientist.experiment_spec.native_compiler import PipeFlowCompiler

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


def _make_pipe_spec(
    *,
    diameter=0.05,
    length=1.0,
    mean_velocity=0.02,
    kinematic_viscosity=1e-6,
    density=998.2,
    axial_cells=80,
    radial_cells=10,
    experiment_id: str = "pipe-native-test-001",
) -> ExperimentSpec:
    """Build a confirmed pipe experiment spec with the given parameters."""
    return ExperimentSpec(
        experiment_id=experiment_id,
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(title="Pipe Test", objective="Test pipe flow"),
        parameters=[
            _param(
                "diameter",
                diameter,
                criticality="critical" if diameter is not None else "medium",
            ),
            _param("length", length),
            _param("mean_velocity", mean_velocity, criticality="critical"),
            _param("kinematic_viscosity", kinematic_viscosity),
            _param("density", density),
            _param("axial_cells", axial_cells, data_type="integer"),
            _param("radial_cells", radial_cells, data_type="integer"),
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
# 1. PipeFlowCompiler.compile() does NOT call compile_pipe_plan
# ---------------------------------------------------------------------------


class TestPipeCompilerNoCompilePipePlan:
    def test_pipe_compiler_does_not_call_compile_pipe_plan(self):
        """PipeFlowCompiler.compile() must NOT call compile_pipe_plan."""
        spec = _make_pipe_spec()
        compiler = PipeFlowCompiler()

        with patch(
            "fluid_scientist.experiment_planning.compilers.compile_pipe_plan"
        ) as spy:
            compiled = compiler.compile(spec)
            assert spy.call_count == 0, (
                "compile_pipe_plan must not be called by "
                "PipeFlowCompiler.compile()"
            )

        assert isinstance(compiled, CompiledCase)


# ---------------------------------------------------------------------------
# 2. PipeFlowCompiler.compile() does NOT construct PipeExperimentPlan
# ---------------------------------------------------------------------------


class TestPipeCompilerNoPipeExperimentPlan:
    def test_pipe_compiler_does_not_construct_pipe_experiment_plan(self):
        """PipeFlowCompiler.compile() must NOT construct PipeExperimentPlan."""
        from fluid_scientist.experiment_planning.models import PipeExperimentPlan

        spec = _make_pipe_spec()
        compiler = PipeFlowCompiler()

        with patch.object(PipeExperimentPlan, "__init__") as spy:
            spy.return_value = None
            compiled = compiler.compile(spec)
            assert spy.call_count == 0, (
                "PipeExperimentPlan must not be constructed by "
                "PipeFlowCompiler.compile()"
            )

        assert isinstance(compiled, CompiledCase)


# ---------------------------------------------------------------------------
# 3. PipeFlowCompiler.compile() generates a valid case with all expected files
# ---------------------------------------------------------------------------


class TestPipeCompilerGeneratesValidCase:
    def test_pipe_compiler_generates_valid_case(self):
        """The compiled archive must contain all expected OpenFOAM case files."""
        spec = _make_pipe_spec()
        compiler = PipeFlowCompiler()
        compiled = compiler.compile(spec)

        assert isinstance(compiled, CompiledCase)
        assert compiled.experiment_type == "laminar_pipe"
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
# 4. PipeFlowCompiler.compile() uses spec parameters in generated files
# ---------------------------------------------------------------------------


class TestPipeCompilerUsesSpecParameters:
    def test_pipe_compiler_uses_spec_parameters(self):
        """The blockMeshDict must contain the diameter and length from the spec."""
        spec = _make_pipe_spec(diameter=0.1, length=2.5)
        compiler = PipeFlowCompiler()
        compiled = compiler.compile(spec)

        files = _extract_archive_files(compiled.archive)
        block_mesh = files["system/blockMeshDict"]

        # diameter=0.1 -> radius=0.05
        assert "radius 0.05" in block_mesh
        # length=2.5
        assert "length 2.5" in block_mesh


# ---------------------------------------------------------------------------
# 5. PipeFlowCompiler.compile() raises on missing required parameter
# ---------------------------------------------------------------------------


class TestPipeCompilerRaisesOnMissingParameter:
    def test_pipe_compiler_raises_on_missing_parameter(self):
        """Compiling a spec with diameter=None must raise
        MissingRequiredParameterError."""
        spec = _make_pipe_spec(diameter=None)
        compiler = PipeFlowCompiler()

        with pytest.raises(MissingRequiredParameterError, match="diameter"):
            compiler.compile(spec)


# ---------------------------------------------------------------------------
# 6. fluidScientist/spec.json is spec-based metadata (not a plan dump)
# ---------------------------------------------------------------------------


class TestPipeCompilerMetadataIsSpecBased:
    def test_pipe_compiler_metadata_is_spec_based(self):
        """The fluidScientist/spec.json must contain spec-based metadata,
        not a plan dump."""
        spec = _make_pipe_spec(
            diameter=0.08,
            length=3.0,
            experiment_id="pipe-meta-001",
        )
        compiler = PipeFlowCompiler()
        compiled = compiler.compile(spec)

        files = _extract_archive_files(compiled.archive)
        metadata = json.loads(files["fluidScientist/spec.json"])

        # spec-based metadata (schema_version 2, not 1)
        assert metadata["schema_version"] == 2
        assert metadata["source"] == "native_compiler"
        assert metadata["experiment_type"] == "laminar_pipe"

        # experiment_id from the spec
        assert metadata["experiment_id"] == "pipe-meta-001"

        # parameters from the spec
        assert metadata["parameters"]["diameter"] == 0.08
        assert metadata["parameters"]["length"] == 3.0
        assert metadata["parameters"]["mean_velocity"] == 0.02

        # requested_outputs
        assert metadata["requested_outputs"] == ["pressure_drop", "residuals"]

        # compilation metadata
        assert metadata["compilation"]["mode"] == "native"
        assert metadata["compilation"]["compiler_id"] == compiler.compiler_id

        # Must NOT contain plan-dump keys
        assert "base_case" not in metadata
        assert "convergence_targets" not in metadata
        assert "parameter_sweeps" not in metadata
