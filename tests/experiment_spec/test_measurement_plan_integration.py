"""Tests for MeasurementPlan integration into native compilers (Commit 5).

Verifies that when an ExperimentSpec carries a MeasurementPlan in
``spec.metrics``, the native compilers (PipeFlowCompiler,
CylinderFlowCompiler, CavityFlowCompiler) merge the plan's
functionObjects into the real ``system/controlDict`` file inside the
generated case archive.
"""

from __future__ import annotations

import io
import tarfile

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
    PipeFlowCompiler,
)
from fluid_scientist.measurement.models import (
    FieldOutputSpec,
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
    TimeSamplingSpec,
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


def _make_pipe_spec(metrics=None) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="pipe-mp-001",
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(title="Pipe MP Test", objective="Test"),
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
        metrics=metrics or [],
    )


def _make_cylinder_spec(metrics=None) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="cyl-mp-001",
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(title="Cylinder MP Test", objective="Test"),
        parameters=[
            _param("reynolds_number", 100.0, criticality="critical"),
            _param("diameter", 0.1, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("cells_radial", 40, data_type="integer"),
            _param("cells_wake", 120, data_type="integer"),
            _param("end_time", 10.0),
            _param("max_courant", 0.5),
        ],
        metrics=metrics or [],
    )


def _make_cavity_spec(metrics=None) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="cav-mp-001",
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(title="Cavity MP Test", objective="Test"),
        parameters=[
            _param("side_length", 0.1, criticality="critical"),
            _param("lid_velocity", 1.0, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("cells_per_side", 64, data_type="integer"),
            _param("end_time", 10.0),
        ],
        metrics=metrics or [],
    )


def _extract_file(archive: bytes, name: str) -> str:
    """Extract a single file from a tar.gz archive."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name == name:
                f = tar.extractfile(member)
                assert f is not None
                return f.read().decode("utf-8")
    raise AssertionError(f"{name} not found in archive")


def _extract_control_dict(archive: bytes) -> str:
    return _extract_file(archive, "system/controlDict")


def _make_surface_field_value_plan() -> MeasurementPlan:
    """A plan with a surfaceFieldValue functionObject (no patch needed)."""
    return MeasurementPlan(
        required_fields=[
            FieldOutputSpec(field_name="U"),
            FieldOutputSpec(field_name="p"),
        ],
        function_objects=[
            FunctionObjectSpec(
                type=FunctionObjectType.SURFACE_FIELD_VALUE,
                name="mp_surface_avg",
                field="p",
                operation="areaAverage",
                surface="mid_plane",
            ),
        ],
        time_sampling=TimeSamplingSpec(
            start_time=0.0, end_time=10.0, interval=0.01
        ),
    )


def _make_force_coeffs_plan() -> MeasurementPlan:
    """A plan with a forceCoeffs functionObject targeting the cylinder patch."""
    return MeasurementPlan(
        required_fields=[
            FieldOutputSpec(field_name="U"),
            FieldOutputSpec(field_name="p"),
        ],
        function_objects=[
            FunctionObjectSpec(
                type=FunctionObjectType.FORCE_COEFFS,
                name="mp_force_coeffs",
                target_patch="cylinder",
            ),
        ],
        time_sampling=TimeSamplingSpec(
            start_time=0.0, end_time=10.0, interval=0.01
        ),
    )


def _make_probes_plan() -> MeasurementPlan:
    """A plan with a probes functionObject (no patch needed)."""
    return MeasurementPlan(
        required_fields=[
            FieldOutputSpec(field_name="U"),
        ],
        function_objects=[
            FunctionObjectSpec(
                type=FunctionObjectType.PROBES,
                name="mp_velocity_probes",
                field="U",
            ),
        ],
        time_sampling=TimeSamplingSpec(
            start_time=0.0, end_time=10.0, interval=0.01
        ),
    )


def _make_residuals_override_plan() -> MeasurementPlan:
    """A plan whose functionObject name 'residuals' collides with the
    built-in residuals block -- MeasurementPlan version should win."""
    return MeasurementPlan(
        required_fields=[
            FieldOutputSpec(field_name="U"),
            FieldOutputSpec(field_name="p"),
        ],
        function_objects=[
            FunctionObjectSpec(
                type=FunctionObjectType.RESIDUALS,
                name="residuals",
                write_interval=50,
            ),
        ],
        time_sampling=TimeSamplingSpec(
            start_time=0.0, end_time=10.0, interval=0.01
        ),
    )


# ---------------------------------------------------------------------------
# 1. Pipe compiler includes MeasurementPlan functionObjects
# ---------------------------------------------------------------------------


class TestPipeCompilerMeasurementPlan:
    def test_pipe_compiler_includes_measurement_plan_fos(self):
        """PipeFlowCompiler writes MeasurementPlan functionObjects into the
        real system/controlDict inside the case archive."""
        plan = _make_surface_field_value_plan()
        spec = _make_pipe_spec(metrics=[plan.model_dump()])
        compiled = PipeFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)
        assert "mp_surface_avg" in cd
        assert "surfaceFieldValue" in cd

    def test_pipe_compiler_without_measurement_plan_works(self):
        """PipeFlowCompiler still works when spec.metrics is empty -- only
        base functionObjects should be present."""
        spec = _make_pipe_spec(metrics=[])
        compiled = PipeFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)
        assert "pressureDrop" in cd
        assert "residuals" in cd
        assert "mp_surface_avg" not in cd


# ---------------------------------------------------------------------------
# 2. Cylinder compiler includes MeasurementPlan functionObjects
# ---------------------------------------------------------------------------


class TestCylinderCompilerMeasurementPlan:
    def test_cylinder_compiler_includes_measurement_plan_fos(self):
        """CylinderFlowCompiler writes MeasurementPlan functionObjects into
        the real system/controlDict."""
        plan = _make_force_coeffs_plan()
        spec = _make_cylinder_spec(metrics=[plan.model_dump()])
        compiled = CylinderFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)
        assert "mp_force_coeffs" in cd
        assert "forceCoeffs" in cd
        assert "libforces.so" in cd

    def test_cylinder_compiler_without_measurement_plan_works(self):
        """CylinderFlowCompiler still works when spec.metrics is empty."""
        spec = _make_cylinder_spec(metrics=[])
        compiled = CylinderFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)
        assert "forceCoeffs" in cd
        assert "residuals" in cd
        assert "mp_force_coeffs" not in cd


# ---------------------------------------------------------------------------
# 3. Cavity compiler includes MeasurementPlan functionObjects
# ---------------------------------------------------------------------------


class TestCavityCompilerMeasurementPlan:
    def test_cavity_compiler_includes_measurement_plan_fos(self):
        """CavityFlowCompiler writes MeasurementPlan functionObjects into
        the real system/controlDict."""
        plan = _make_probes_plan()
        spec = _make_cavity_spec(metrics=[plan.model_dump()])
        compiled = CavityFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)
        assert "mp_velocity_probes" in cd
        assert "probes" in cd
        assert "libsampling.so" in cd

    def test_cavity_compiler_without_measurement_plan_works(self):
        """CavityFlowCompiler still works when spec.metrics is empty."""
        spec = _make_cavity_spec(metrics=[])
        compiled = CavityFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)
        assert "velocityProbes" in cd
        assert "residuals" in cd
        assert "mp_velocity_probes" not in cd


# ---------------------------------------------------------------------------
# 4. MeasurementPlan FOs appear as actual OpenFOAM dict entries
# ---------------------------------------------------------------------------


class TestMeasurementPlanFOsInControlDict:
    def test_measurement_plan_fos_appear_in_actual_controlDict(self):
        """Parse the generated controlDict text and verify the
        MeasurementPlan functionObject names appear as block headers
        (i.e. ``<name>`` followed by ``{``)."""
        plan = _make_force_coeffs_plan()
        spec = _make_cylinder_spec(metrics=[plan.model_dump()])
        compiled = CylinderFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)

        lines = cd.split("\n")
        fo_names_found = set()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "mp_force_coeffs":
                # Next non-empty line should be "{"
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    j += 1
                if j < len(lines) and lines[j].strip() == "{":
                    fo_names_found.add("mp_force_coeffs")

        assert "mp_force_coeffs" in fo_names_found, (
            "MeasurementPlan functionObject 'mp_force_coeffs' must appear "
            "as a block header in the controlDict"
        )

    def test_measurement_plan_fo_has_correct_type(self):
        """The MeasurementPlan functionObject in the controlDict has the
        correct 'type' entry."""
        plan = _make_force_coeffs_plan()
        spec = _make_cylinder_spec(metrics=[plan.model_dump()])
        compiled = CylinderFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)

        # Find the mp_force_coeffs block and check for type forceCoeffs
        lines = cd.split("\n")
        in_block = False
        type_found = None
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "mp_force_coeffs":
                in_block = True
                continue
            if in_block:
                if stripped == "}":
                    break
                if stripped.startswith("type ") and stripped.endswith(";"):
                    type_found = stripped.split()[1].rstrip(";")
                    break

        assert type_found == "forceCoeffs"


# ---------------------------------------------------------------------------
# 5. No duplicate functionObjects (MeasurementPlan wins on name collision)
# ---------------------------------------------------------------------------


class TestNoDuplicateFunctionObjects:
    def test_no_duplicate_function_objects_pipe(self):
        """When MeasurementPlan has a functionObject named 'residuals'
        (same as the built-in), the old block is removed and the
        MeasurementPlan version replaces it -- only one 'residuals' block
        in the controlDict."""
        plan = _make_residuals_override_plan()
        spec = _make_pipe_spec(metrics=[plan.model_dump()])
        compiled = PipeFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)

        # Count occurrences of "residuals" as a block header (line == "residuals")
        lines = cd.split("\n")
        residuals_count = sum(
            1 for line in lines
            if line.strip() == "residuals"
        )
        assert residuals_count == 1, (
            f"Expected exactly 1 'residuals' block, found {residuals_count}"
        )

    def test_no_duplicate_function_objects_cylinder(self):
        """Same test for cylinder compiler."""
        plan = _make_residuals_override_plan()
        spec = _make_cylinder_spec(metrics=[plan.model_dump()])
        compiled = CylinderFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)

        lines = cd.split("\n")
        residuals_count = sum(
            1 for line in lines
            if line.strip() == "residuals"
        )
        assert residuals_count == 1

    def test_measurement_plan_residuals_has_write_interval_50(self):
        """The MeasurementPlan residuals block (writeInterval 50) replaces
        the built-in one (writeInterval 1)."""
        plan = _make_residuals_override_plan()
        spec = _make_cylinder_spec(metrics=[plan.model_dump()])
        compiled = CylinderFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)

        lines = cd.split("\n")
        in_block = False
        write_interval_found = None
        for line in lines:
            stripped = line.strip()
            if stripped == "residuals":
                in_block = True
                continue
            if in_block:
                if stripped == "}":
                    break
                if stripped.startswith("writeInterval"):
                    write_interval_found = stripped.split()[1].rstrip(";")
                    break

        assert write_interval_found == "50", (
            f"Expected writeInterval 50 (MeasurementPlan version), "
            f"got {write_interval_found}"
        )

    def test_base_fos_preserved_when_no_name_collision(self):
        """Base functionObjects that don't collide with MeasurementPlan
        names are preserved."""
        plan = _make_force_coeffs_plan()
        spec = _make_cylinder_spec(metrics=[plan.model_dump()])
        compiled = CylinderFlowCompiler().compile(spec)
        cd = _extract_control_dict(compiled.archive)

        # 'forces' and 'residuals' should still be there
        assert "forces" in cd
        assert "residuals" in cd
        # 'mp_force_coeffs' should also be there
        assert "mp_force_coeffs" in cd
