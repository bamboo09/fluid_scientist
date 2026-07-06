"""Tests for the Simulation Compiler integration with ExperimentSpec."""

import pytest

from fluid_scientist.experiment_planning.compilers import CompiledCase
from fluid_scientist.experiment_spec.compilation import (
    SpecNotConfirmedError,
    _detect_experiment_type,
    compile_confirmed_spec,
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


def _pipe_spec(status: ExperimentStatus = ExperimentStatus.CONFIRMED) -> ExperimentSpec:
    """A confirmed pipe experiment spec with valid laminar parameters."""
    return ExperimentSpec(
        experiment_id="pipe-001",
        status=status,
        research=ResearchSpec(title="Pipe Test", objective="Test pipe flow"),
        parameters=[
            _param("diameter", 0.05, criticality="critical"),
            _param("length", 1.0),
            _param("mean_velocity", 0.02, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("reynolds_number", 1000.0, criticality="critical"),
            _param("axial_cells", 80, data_type="integer"),
            _param("radial_cells", 10, data_type="integer"),
        ],
    )


def _cylinder_spec(status: ExperimentStatus = ExperimentStatus.CONFIRMED) -> ExperimentSpec:
    """A confirmed cylinder experiment spec with valid low-Re parameters."""
    return ExperimentSpec(
        experiment_id="cyl-001",
        status=status,
        research=ResearchSpec(title="Cylinder Test", objective="Test cylinder flow"),
        parameters=[
            _param("reynolds_number", 100.0, criticality="critical"),
            _param("diameter", 0.1, criticality="critical"),
            _param("inlet_velocity", 0.1, criticality="high"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("cells_radial", 40, data_type="integer"),
            _param("cells_wake", 120, data_type="integer"),
            _param("end_time", 10.0),
            _param("max_courant", 0.5),
        ],
    )


def _cavity_spec(status: ExperimentStatus = ExperimentStatus.CONFIRMED) -> ExperimentSpec:
    """A confirmed cavity experiment spec."""
    return ExperimentSpec(
        experiment_id="cav-001",
        status=status,
        research=ResearchSpec(title="Cavity Test", objective="Test cavity flow"),
        parameters=[
            _param("side_length", 0.1, criticality="critical"),
            _param("lid_velocity", 1.0, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("cells_per_side", 64, data_type="integer"),
            _param("end_time", 10.0),
        ],
    )


# --- Type detection tests ---


class TestDetectExperimentType:
    def test_detect_pipe(self):
        spec = _pipe_spec()
        assert _detect_experiment_type(spec) == "laminar_pipe"

    def test_detect_cylinder(self):
        spec = _cylinder_spec()
        assert _detect_experiment_type(spec) == "cylinder_flow"

    def test_detect_cavity(self):
        spec = _cavity_spec()
        assert _detect_experiment_type(spec) == "lid_driven_cavity"

    def test_detect_unknown_raises(self):
        spec = ExperimentSpec(
            experiment_id="unknown-001",
            status=ExperimentStatus.CONFIRMED,
            research=ResearchSpec(title="Unknown", objective="Test"),
            parameters=[_param("foo", 1.0), _param("bar", 2.0)],
        )
        with pytest.raises(ValueError, match="cannot detect"):
            _detect_experiment_type(spec)


# --- Compile confirmed spec tests ---


class TestCompileConfirmedSpec:
    def test_compile_pipe(self):
        spec = _pipe_spec()
        compiled = compile_confirmed_spec(spec)
        assert isinstance(compiled, CompiledCase)
        assert compiled.experiment_type == "laminar_pipe"
        assert compiled.archive_sha256.startswith("sha256:")
        assert len(compiled.archive) > 0

    def test_compile_cylinder(self):
        spec = _cylinder_spec()
        compiled = compile_confirmed_spec(spec)
        assert isinstance(compiled, CompiledCase)
        assert compiled.experiment_type == "cylinder_flow"
        assert compiled.archive_sha256.startswith("sha256:")

    def test_compile_cavity(self):
        spec = _cavity_spec()
        compiled = compile_confirmed_spec(spec)
        assert isinstance(compiled, CompiledCase)
        assert compiled.experiment_type == "lid_driven_cavity"
        assert compiled.archive_sha256.startswith("sha256:")

    def test_draft_spec_rejected(self):
        spec = _pipe_spec(status=ExperimentStatus.DRAFT)
        with pytest.raises(SpecNotConfirmedError, match="confirmed"):
            compile_confirmed_spec(spec)

    def test_ready_spec_rejected(self):
        spec = _cylinder_spec(status=ExperimentStatus.READY)
        with pytest.raises(SpecNotConfirmedError, match="confirmed"):
            compile_confirmed_spec(spec)

    def test_compiling_spec_rejected(self):
        spec = _cavity_spec(status=ExperimentStatus.COMPILING)
        with pytest.raises(SpecNotConfirmedError, match="confirmed"):
            compile_confirmed_spec(spec)

    def test_cylinder_with_time_step(self):
        """Cylinder spec using time_step instead of max_courant."""
        spec = ExperimentSpec(
            experiment_id="cyl-ts-001",
            status=ExperimentStatus.CONFIRMED,
            research=ResearchSpec(title="Cylinder TS", objective="Test cylinder flow"),
            parameters=[
                _param("reynolds_number", 100.0, criticality="critical"),
                _param("diameter", 0.1, criticality="critical"),
                _param("inlet_velocity", 0.1),
                _param("kinematic_viscosity", 1e-6),
                _param("density", 998.2),
                _param("cells_radial", 40, data_type="integer"),
                _param("cells_wake", 120, data_type="integer"),
                _param("end_time", 10.0),
                _param("time_step", 0.01),
            ],
        )
        compiled = compile_confirmed_spec(spec)
        assert compiled.experiment_type == "cylinder_flow"

    def test_cylinder_neither_ts_nor_courant_uses_default(self):
        """Cylinder spec without time_step or max_courant defaults to Co=0.5."""
        spec = ExperimentSpec(
            experiment_id="cyl-def-001",
            status=ExperimentStatus.CONFIRMED,
            research=ResearchSpec(title="Cylinder Default", objective="Test cylinder flow"),
            parameters=[
                _param("reynolds_number", 100.0, criticality="critical"),
                _param("diameter", 0.1, criticality="critical"),
                _param("inlet_velocity", 0.1),
                _param("kinematic_viscosity", 1e-6),
                _param("density", 998.2),
                _param("cells_radial", 40, data_type="integer"),
                _param("cells_wake", 120, data_type="integer"),
                _param("end_time", 10.0),
            ],
        )
        compiled = compile_confirmed_spec(spec)
        assert compiled.experiment_type == "cylinder_flow"

    def test_compiled_archive_contains_files(self):
        """The compiled archive should contain OpenFOAM dictionary files."""
        import gzip
        import io
        import tarfile

        spec = _cavity_spec()
        compiled = compile_confirmed_spec(spec)
        with (
            gzip.GzipFile(fileobj=io.BytesIO(compiled.archive)) as gz,
            tarfile.open(fileobj=gz, mode="r") as tar,
        ):
            names = tar.getnames()
        assert "system/controlDict" in names
        assert "0/U" in names
        assert "0/p" in names
