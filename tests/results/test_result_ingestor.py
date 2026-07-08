"""Tests for the Result Ingestor with ResultManifest (Commit 9).

These tests verify that OpenFOAMResultIngestor reads real files from disk,
creates ResultManifest bindings, and handles missing results properly.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from fluid_scientist.measurement.models import (
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
)
from fluid_scientist.results.ingestor import OpenFOAMResultIngestor
from fluid_scientist.results.models import (
    MetricResult,
    ResultManifest,
    SimulationData,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmp_dir() -> Iterator[Path]:
    """Provide a writable temporary directory."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _make_manifest() -> ResultManifest:
    """Create a minimal ResultManifest for testing."""
    return ResultManifest(
        run_id="run-001",
        experiment_id="exp-001",
        experiment_version=1,
        spec_hash="abc123def456",
        case_hash="case789ghi012",
    )


# --------------------------------------------------------------------------- #
# Model tests
# --------------------------------------------------------------------------- #


class TestResultManifest:
    """Test 1: ResultManifest model with all required fields."""

    def test_result_manifest_required_fields(self):
        """ResultManifest requires run_id, experiment_id, experiment_version,
        spec_hash, case_hash."""
        manifest = ResultManifest(
            run_id="run-001",
            experiment_id="exp-001",
            experiment_version=1,
            spec_hash="abc123",
            case_hash="def456",
        )
        assert manifest.run_id == "run-001"
        assert manifest.experiment_id == "exp-001"
        assert manifest.experiment_version == 1
        assert manifest.spec_hash == "abc123"
        assert manifest.case_hash == "def456"

    def test_result_manifest_optional_fields(self):
        """Optional fields have sensible defaults."""
        manifest = ResultManifest(
            run_id="run-001",
            experiment_id="exp-001",
            experiment_version=1,
            spec_hash="abc123",
            case_hash="def456",
        )
        assert manifest.remote_job_id is None
        assert manifest.remote_host is None
        assert manifest.solver_exit_code == 0
        assert manifest.result_paths == []
        assert manifest.downloaded_paths == []
        assert manifest.started_at is not None
        assert manifest.completed_at is None

    def test_result_manifest_with_all_fields(self):
        """ResultManifest accepts all fields."""
        manifest = ResultManifest(
            run_id="run-002",
            experiment_id="exp-002",
            experiment_version=3,
            spec_hash="hash_a",
            case_hash="hash_b",
            remote_job_id="slurm-12345",
            remote_host="hpc.cluster.edu",
            solver_exit_code=0,
            result_paths=["/path/to/results", "/path/to/log"],
            downloaded_paths=["/local/results", "/local/log"],
            completed_at="2026-01-01T12:00:00+00:00",
        )
        assert manifest.remote_job_id == "slurm-12345"
        assert manifest.remote_host == "hpc.cluster.edu"
        assert len(manifest.result_paths) == 2
        assert len(manifest.downloaded_paths) == 2
        assert manifest.completed_at == "2026-01-01T12:00:00+00:00"


class TestSimulationData:
    """Test 2: SimulationData model with all data categories."""

    def test_simulation_data_defaults(self):
        """SimulationData has sensible defaults for all fields."""
        data = SimulationData()
        assert data.solver_name is None
        assert data.solver_version is None
        assert data.end_time is None
        assert data.converged is False
        assert data.residuals == {}
        assert data.final_residuals == {}
        assert data.continuity_errors == []
        assert data.final_continuity_error is None
        assert data.courant_numbers == []
        assert data.max_courant is None
        assert data.force_coefficients == {}
        assert data.forces == {}
        assert data.probe_data == {}
        assert data.surface_field_values == {}
        assert data.field_averages == {}
        assert data.sample_data == {}
        assert data.mesh_cells is None
        assert data.mesh_max_aspect_ratio is None
        assert data.mesh_max_non_orthogonality is None
        assert data.missing_data == []
        assert data.source_files == []
        assert data.warnings == []

    def test_simulation_data_all_categories(self):
        """SimulationData can hold all data categories."""
        data = SimulationData(
            solver_name="simpleFoam",
            solver_version="OpenFOAM-13",
            end_time=100.0,
            converged=True,
            residuals={"Ux": [0.1, 0.01], "p": [0.5, 0.05]},
            final_residuals={"Ux": 0.01, "p": 0.05},
            continuity_errors=[1e-5, 1e-6],
            final_continuity_error=1e-6,
            courant_numbers=[0.5, 0.3],
            max_courant=0.5,
            force_coefficients={"Cd": [1.2, 1.3], "Cl": [0.4, 0.5]},
            forces={"force_0": [10.0, 11.0]},
            probe_data={"U_probe": [1.0, 2.0]},
            surface_field_values={"p_surface": [100.0, 101.0]},
            field_averages={"U_avg": 0.5},
            mesh_cells=10000,
            mesh_max_aspect_ratio=5.5,
            mesh_max_non_orthogonality=45.0,
            missing_data=["forceCoeffs"],
            source_files=["/path/to/log", "/path/to/postProcessing"],
            warnings=["Some warning"],
        )
        assert data.solver_name == "simpleFoam"
        assert data.converged is True
        assert data.residuals["Ux"] == [0.1, 0.01]
        assert data.force_coefficients["Cd"] == [1.2, 1.3]
        assert data.mesh_cells == 10000
        assert "forceCoeffs" in data.missing_data


class TestMetricResult:
    """Test 3: MetricResult model with quality_checks, confidence, data_missing."""

    def test_metric_result_defaults(self):
        """MetricResult has sensible defaults."""
        result = MetricResult(metric_id="drag_coefficient")
        assert result.metric_id == "drag_coefficient"
        assert result.metric_version == "1.0.0"
        assert result.value is None
        assert result.unit == ""
        assert result.time_range is None
        assert result.spatial_scope is None
        assert result.quality_checks == []
        assert result.confidence == "high"
        assert result.warnings == []
        assert result.source_files == []
        assert result.algorithm_version == "1.0.0"
        assert result.data_missing is False
        assert result.missing_reason is None

    def test_metric_result_with_quality_checks(self):
        """MetricResult can hold quality_checks."""
        result = MetricResult(
            metric_id="drag_coefficient",
            value=1.23,
            unit="-",
            quality_checks=[
                {"check": "convergence", "status": "passed", "threshold": 1e-4},
                {"check": "courant", "status": "passed", "threshold": 1.0},
            ],
            confidence="high",
        )
        assert len(result.quality_checks) == 2
        assert result.quality_checks[0]["check"] == "convergence"
        assert result.confidence == "high"

    def test_metric_result_data_missing(self):
        """MetricResult can represent missing data."""
        result = MetricResult(
            metric_id="lift_coefficient",
            value=None,
            confidence="failed",
            data_missing=True,
            missing_reason="forceCoeffs output not found",
        )
        assert result.data_missing is True
        assert result.missing_reason == "forceCoeffs output not found"
        assert result.confidence == "failed"

    def test_metric_result_low_confidence(self):
        """MetricResult can have low confidence."""
        result = MetricResult(
            metric_id="pressure_drop",
            value=150.0,
            unit="Pa",
            confidence="low",
            warnings=["Insufficient data points", "High residual"],
        )
        assert result.confidence == "low"
        assert len(result.warnings) == 2


# --------------------------------------------------------------------------- #
# Ingestor tests
# --------------------------------------------------------------------------- #


class TestIngestorSolverLog:
    """Test 5 & 6: _parse_solver_log() extracts residuals, continuity,
    Courant number, and detects convergence."""

    def test_parse_solver_log(self, tmp_dir):
        """Test that solver log is parsed correctly."""
        log_path = tmp_dir / "log.simpleFoam"
        log_path.write_text(
            "OpenFOAM-13\n"
            "Time = 0.1\n"
            "Ux: initial residual = 0.001, final residual = 0.0001\n"
            "Uy: initial residual = 0.002, final residual = 0.0002\n"
            "continuity error = 1.5e-06\n"
            "Courant Number mean: 0.15 max: 0.85\n"
            "Time = 0.2\n"
            "Ux: initial residual = 0.0005, final residual = 0.00005\n"
            "continuity error = 8e-07\n"
            "Courant Number mean: 0.12 max: 0.75\n"
            "solution converged\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        assert "Ux" in data.residuals
        assert len(data.residuals["Ux"]) == 2
        assert data.residuals["Ux"][0] == 0.001
        assert data.final_residuals["Ux"] == 0.00005
        assert len(data.continuity_errors) == 2
        assert data.final_continuity_error == 8e-07
        assert data.max_courant == 0.85
        assert data.converged is True
        assert data.end_time == 0.2

    def test_parse_solver_log_residuals(self, tmp_dir):
        """Test residual extraction from solver log."""
        log_path = tmp_dir / "solver.log"
        log_path.write_text(
            "Ux: initial residual = 0.01, final residual = 0.001\n"
            "Uy: initial residual = 0.02, final residual = 0.002\n"
            "p: initial residual = 0.5, final residual = 0.05\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        assert "Ux" in data.residuals
        assert data.residuals["Ux"] == [0.01]
        assert "Uy" in data.residuals
        assert data.residuals["Uy"] == [0.02]
        assert "p" in data.residuals
        assert data.residuals["p"] == [0.5]
        assert data.final_residuals["Ux"] == 0.001
        assert data.final_residuals["Uy"] == 0.002
        assert data.final_residuals["p"] == 0.05

    def test_parse_solver_log_continuity(self, tmp_dir):
        """Test continuity error extraction."""
        log_path = tmp_dir / "log.run"
        log_path.write_text(
            "continuity error = 1.0e-05\n"
            "continuity error = 5.0e-06\n"
            "continuity error = 2.0e-07\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        assert len(data.continuity_errors) == 3
        assert data.continuity_errors[0] == 1.0e-05
        assert data.final_continuity_error == 2.0e-07

    def test_parse_solver_log_courant(self, tmp_dir):
        """Test Courant number extraction — should capture max values."""
        log_path = tmp_dir / "log.run"
        log_path.write_text(
            "Courant Number mean: 0.1 max: 0.5\n"
            "Courant Number mean: 0.08 max: 0.3\n"
            "Courant Number mean: 0.05 max: 0.2\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        assert len(data.courant_numbers) == 3
        assert data.courant_numbers[0] == 0.5
        assert data.courant_numbers[1] == 0.3
        assert data.courant_numbers[2] == 0.2
        assert data.max_courant == 0.5

    def test_parse_solver_log_convergence(self, tmp_dir):
        """Test 6: convergence detection."""
        log_path = tmp_dir / "log.run"
        log_path.write_text(
            "Time = 1.0\n"
            "Solving for Ux\n"
            "solution converged\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        assert data.converged is True

    def test_parse_solver_log_no_convergence(self, tmp_dir):
        """Test that convergence is False when not mentioned."""
        log_path = tmp_dir / "log.run"
        log_path.write_text(
            "Time = 1.0\n"
            "Solving for Ux\n"
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        assert data.converged is False

    def test_parse_solver_log_end_time(self, tmp_dir):
        """Test end time extraction."""
        log_path = tmp_dir / "log.run"
        log_path.write_text(
            "Time = 0.5\n"
            "Time = 1.0\n"
            "Time = 1.5\n"
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        assert data.end_time == 1.5

    def test_parse_solver_log_solver_version(self, tmp_dir):
        """Test solver version extraction."""
        log_path = tmp_dir / "log.run"
        log_path.write_text(
            "OpenFOAM-13\n"
            "Time = 0.1\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        assert data.solver_version is not None
        assert "OpenFOAM" in data.solver_version

    def test_parse_solver_log_missing_file(self, tmp_dir):
        """Test warning when log file cannot be read."""
        # Create a path that exists as a directory, not a file
        log_path = tmp_dir / "not_a_file"
        log_path.mkdir()

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_solver_log(log_path, data)

        # Should not crash; should add a warning
        assert len(data.warnings) > 0


class TestIngestorForceCoeffs:
    """Test 7: _parse_force_coeffs() reads coefficient.dat files."""

    def test_parse_force_coeffs(self, tmp_dir):
        """Test forceCoeffs output parsing."""
        fc_dir = tmp_dir / "forceCoeffs"
        time_dir = fc_dir / "0"
        time_dir.mkdir(parents=True)
        (time_dir / "coefficient.dat").write_text(
            "# Time  Cd  Cl  Cm\n"
            "0.1  1.23  0.45  0.12\n"
            "0.2  1.25  0.46  0.13\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_force_coeffs(fc_dir, data)

        assert "Cd" in data.force_coefficients
        assert data.force_coefficients["Cd"] == [1.23, 1.25]
        assert "Cl" in data.force_coefficients
        assert data.force_coefficients["Cl"] == [0.45, 0.46]
        assert "Cm" in data.force_coefficients
        assert data.force_coefficients["Cm"] == [0.12, 0.13]

    def test_parse_force_coeffs_alternative_filename(self, tmp_dir):
        """Test forceCoeffs with alternative .dat filename."""
        fc_dir = tmp_dir / "forceCoeffs"
        time_dir = fc_dir / "0"
        time_dir.mkdir(parents=True)
        (time_dir / "forceCoeffs.dat").write_text(
            "# Time  Cd  Cl\n"
            "0.1  1.0  0.5\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_force_coeffs(fc_dir, data)

        assert "Cd" in data.force_coefficients
        assert data.force_coefficients["Cd"] == [1.0]

    def test_parse_force_coeffs_empty_dir(self, tmp_dir):
        """Test forceCoeffs with empty directory does not crash."""
        fc_dir = tmp_dir / "forceCoeffs"
        fc_dir.mkdir(parents=True)

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_force_coeffs(fc_dir, data)

        assert data.force_coefficients == {}


class TestIngestorProbes:
    """Test 8: _parse_probes() reads probe data files."""

    def test_parse_probes(self, tmp_dir):
        """Test probes output parsing."""
        probes_dir = tmp_dir / "probes"
        time_dir = probes_dir / "0"
        time_dir.mkdir(parents=True)
        (time_dir / "U.dat").write_text(
            "# Probe 0 (0 0 0)\n"
            "# Time\n"
            "0.1  1.0  0.0  0.0\n"
            "0.2  1.1  0.0  0.0\n",
        )
        (time_dir / "p.dat").write_text(
            "# Probe 0 (0 0 0)\n"
            "# Time\n"
            "0.1  10.0\n"
            "0.2  11.0\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_probes(probes_dir, data)

        assert "U_probe" in data.probe_data
        assert data.probe_data["U_probe"] == [0.0, 0.0]  # last value
        assert "p_probe" in data.probe_data
        assert data.probe_data["p_probe"] == [10.0, 11.0]

    def test_parse_probes_empty_dir(self, tmp_dir):
        """Test probes with empty directory does not crash."""
        probes_dir = tmp_dir / "probes"
        probes_dir.mkdir(parents=True)

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_probes(probes_dir, data)

        assert data.probe_data == {}


class TestIngestorSurfaceFieldValue:
    """Test 9: _parse_surface_field_value() reads surface field value data."""

    def test_parse_surface_field_value(self, tmp_dir):
        """Test surfaceFieldValue output parsing."""
        sfv_dir = tmp_dir / "surfaceFieldValue"
        time_dir = sfv_dir / "0"
        time_dir.mkdir(parents=True)
        (time_dir / "surfaceFieldValue.dat").write_text(
            "# Time  value\n"
            "0.1  100.5\n"
            "0.2  101.0\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_surface_field_value(sfv_dir, data)

        assert "surfaceFieldValue_surface" in data.surface_field_values
        assert data.surface_field_values["surfaceFieldValue_surface"] == [100.5, 101.0]

    def test_parse_surface_field_value_empty_dir(self, tmp_dir):
        """Test surfaceFieldValue with empty directory does not crash."""
        sfv_dir = tmp_dir / "surfaceFieldValue"
        sfv_dir.mkdir(parents=True)

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_surface_field_value(sfv_dir, data)

        assert data.surface_field_values == {}


class TestIngestorFullFlow:
    """Test 4, 10-13: Full ingest() flow tests."""

    def test_ingest_reads_real_files(self, tmp_dir):
        """Test 4: ingest() reads from real files."""
        # Create solver log
        (tmp_dir / "log.simpleFoam").write_text(
            "OpenFOAM-13\n"
            "Time = 0.1\n"
            "Ux: initial residual = 0.001, final residual = 0.0001\n"
            "continuity error = 1e-06\n"
            "Courant Number mean: 0.1 max: 0.5\n"
            "Time = 0.2\n"
            "Ux: initial residual = 0.0005, final residual = 0.00005\n"
            "continuity error = 5e-07\n"
            "Courant Number mean: 0.08 max: 0.3\n"
            "solution converged\n",
        )

        # Create postProcessing
        fc_dir = tmp_dir / "postProcessing" / "forceCoeffs" / "0"
        fc_dir.mkdir(parents=True)
        (fc_dir / "coefficient.dat").write_text(
            "# Time  Cd  Cl  Cm\n"
            "0.1  1.23  0.45  0.12\n"
            "0.2  1.25  0.46  0.13\n",
        )

        manifest = _make_manifest()
        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            result_manifest=manifest,
        )

        assert isinstance(data, SimulationData)
        assert "Ux" in data.residuals
        assert data.converged is True
        assert data.max_courant == 0.5
        assert "Cd" in data.force_coefficients
        assert data.force_coefficients["Cd"] == [1.23, 1.25]

    def test_ingest_raises_file_not_found(self, tmp_dir):
        """Test 10: ingest() raises FileNotFoundError for non-existent case path."""
        nonexistent = tmp_dir / "does_not_exist"
        manifest = _make_manifest()
        ingestor = OpenFOAMResultIngestor()

        with pytest.raises(FileNotFoundError, match="Case directory not found"):
            ingestor.ingest(case_path=nonexistent, result_manifest=manifest)

    def test_ingest_populates_missing_data(self, tmp_dir):
        """Test 11: ingest() populates missing_data when expected objects absent."""
        manifest = _make_manifest()
        measurement_plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(type=FunctionObjectType.FORCE_COEFFS),
                FunctionObjectSpec(type=FunctionObjectType.PROBES),
            ],
        )

        # Empty case directory — no log, no postProcessing
        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            result_manifest=manifest,
            measurement_plan=measurement_plan,
        )

        assert "solver_log" in data.missing_data
        assert "postProcessing" in data.missing_data
        assert "forceCoeffs" in data.missing_data
        assert "probes" in data.missing_data

    def test_ingest_validates_expected_objects(self, tmp_dir):
        """Test 12: ingest() validates expected functionObjects from MeasurementPlan."""
        # Create solver log so solver_log is not missing
        (tmp_dir / "log.simpleFoam").write_text(
            "Time = 0.1\n"
            "solution converged\n",
        )

        manifest = _make_manifest()
        measurement_plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(type=FunctionObjectType.FORCE_COEFFS),
                FunctionObjectSpec(type=FunctionObjectType.FORCES),
                FunctionObjectSpec(type=FunctionObjectType.SURFACE_FIELD_VALUE),
            ],
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            result_manifest=manifest,
            measurement_plan=measurement_plan,
        )

        # solver_log should NOT be missing (log file exists)
        assert "solver_log" not in data.missing_data
        # postProcessing should be missing (no postProcessing dir)
        assert "postProcessing" in data.missing_data
        # Expected functionObjects should be in missing_data
        assert "forceCoeffs" in data.missing_data
        assert "forces" in data.missing_data
        assert "surfaceFieldValue" in data.missing_data

    def test_ingest_no_missing_when_data_present(self, tmp_dir):
        """Test that missing_data is empty when all expected data is present."""
        # Create solver log
        (tmp_dir / "log.simpleFoam").write_text(
            "Time = 0.1\n"
            "Ux: initial residual = 0.001, final residual = 0.0001\n"
            "solution converged\n",
        )

        # Create postProcessing with forceCoeffs
        fc_dir = tmp_dir / "postProcessing" / "forceCoeffs" / "0"
        fc_dir.mkdir(parents=True)
        (fc_dir / "coefficient.dat").write_text(
            "# Time  Cd  Cl\n"
            "0.1  1.0  0.5\n",
        )

        manifest = _make_manifest()
        measurement_plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(type=FunctionObjectType.FORCE_COEFFS),
            ],
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            result_manifest=manifest,
            measurement_plan=measurement_plan,
        )

        # forceCoeffs should NOT be in missing_data
        assert "forceCoeffs" not in data.missing_data

    def test_ingest_records_source_files(self, tmp_dir):
        """Test 13: ingest() records source_files for traceability."""
        # Create solver log
        log_path = tmp_dir / "log.simpleFoam"
        log_path.write_text("Time = 0.1\nsolution converged\n")

        # Create postProcessing
        fc_dir = tmp_dir / "postProcessing" / "forceCoeffs"
        fc_dir.mkdir(parents=True)

        manifest = _make_manifest()
        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            result_manifest=manifest,
        )

        assert len(data.source_files) > 0
        # The solver log path should be in source_files
        log_in_sources = any("log.simpleFoam" in sf for sf in data.source_files)
        assert log_in_sources
        # The forceCoeffs dir should be in source_files
        fc_in_sources = any("forceCoeffs" in sf for sf in data.source_files)
        assert fc_in_sources

    def test_ingest_without_measurement_plan(self, tmp_dir):
        """Test ingest() works without a measurement_plan."""
        (tmp_dir / "log.simpleFoam").write_text(
            "Time = 0.1\n"
            "Ux: initial residual = 0.001\n"
            "solution converged\n",
        )

        manifest = _make_manifest()
        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(case_path=tmp_dir, result_manifest=manifest)

        assert isinstance(data, SimulationData)
        assert "Ux" in data.residuals
        # No validation should occur, so missing_data should only have
        # entries for missing directories/files, not expected objects
        assert "forceCoeffs" not in data.missing_data


class TestIngestorCheckMesh:
    """Test 14: _parse_checkmesh_log() extracts mesh quality metrics."""

    def test_parse_checkmesh_log(self, tmp_dir):
        """Test checkMesh log parsing."""
        log_path = tmp_dir / "log.checkMesh"
        log_path.write_text(
            "Checking geometry...\n"
            "cells: 50000\n"
            "aspect ratio: max: 5.5\n"
            "non-orthogonality: max: 42.3\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_checkmesh_log(log_path, data)

        assert data.mesh_cells == 50000
        assert data.mesh_max_aspect_ratio == 5.5
        assert data.mesh_max_non_orthogonality == 42.3

    def test_parse_checkmesh_log_partial(self, tmp_dir):
        """Test checkMesh log with only some metrics."""
        log_path = tmp_dir / "checkMesh.log"
        log_path.write_text(
            "cells: 10000\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_checkmesh_log(log_path, data)

        assert data.mesh_cells == 10000
        assert data.mesh_max_aspect_ratio is None
        assert data.mesh_max_non_orthogonality is None

    def test_find_checkmesh_log(self, tmp_dir):
        """Test finding checkMesh log file."""
        log_path = tmp_dir / "log.checkMesh"
        log_path.write_text("cells: 100\n")

        ingestor = OpenFOAMResultIngestor()
        found = ingestor._find_checkmesh_log(tmp_dir)
        assert found is not None
        assert found.name == "log.checkMesh"

    def test_find_checkmesh_log_not_found(self, tmp_dir):
        """Test that None is returned when no checkMesh log exists."""
        ingestor = OpenFOAMResultIngestor()
        found = ingestor._find_checkmesh_log(tmp_dir)
        assert found is None


class TestIngestorFindSolverLog:
    """Test finding solver log files."""

    def test_find_solver_log_log_prefix(self, tmp_dir):
        """Test finding log.* pattern."""
        (tmp_dir / "log.simpleFoam").write_text("test\n")

        ingestor = OpenFOAMResultIngestor()
        found = ingestor._find_solver_log(tmp_dir)
        assert found is not None
        assert found.name == "log.simpleFoam"

    def test_find_solver_log_dot_log(self, tmp_dir):
        """Test finding *.log pattern."""
        (tmp_dir / "solver.log").write_text("test\n")

        ingestor = OpenFOAMResultIngestor()
        found = ingestor._find_solver_log(tmp_dir)
        assert found is not None
        assert found.name == "solver.log"

    def test_find_solver_log_not_found(self, tmp_dir):
        """Test that None is returned when no log file exists."""
        ingestor = OpenFOAMResultIngestor()
        found = ingestor._find_solver_log(tmp_dir)
        assert found is None


class TestIngestorPostProcessing:
    """Test postProcessing parsing in the full ingest flow."""

    def test_parse_post_processing_all_types(self, tmp_dir):
        """Test that all postProcessing types are parsed."""
        # forceCoeffs
        fc_dir = tmp_dir / "forceCoeffs" / "0"
        fc_dir.mkdir(parents=True)
        (fc_dir / "coefficient.dat").write_text(
            "# Time  Cd  Cl\n"
            "0.1  1.0  0.5\n",
        )

        # forces
        forces_dir = tmp_dir / "forces" / "0"
        forces_dir.mkdir(parents=True)
        (forces_dir / "forces.dat").write_text(
            "# Time  fx  fy\n"
            "0.1  10.0  5.0\n",
        )

        # probes
        probes_dir = tmp_dir / "probes" / "0"
        probes_dir.mkdir(parents=True)
        (probes_dir / "U.dat").write_text(
            "0.1  1.0  0.0  0.0\n",
        )

        # surfaceFieldValue
        sfv_dir = tmp_dir / "surfaceFieldValue" / "0"
        sfv_dir.mkdir(parents=True)
        (sfv_dir / "surfaceFieldValue.dat").write_text(
            "0.1  100.0\n",
        )

        # fieldAverage
        fa_dir = tmp_dir / "fieldAverage" / "0"
        fa_dir.mkdir(parents=True)
        (fa_dir / "fieldAverage.dat").write_text(
            "0.1  0.5\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        source_files: list[str] = []
        ingestor._parse_post_processing(tmp_dir, data, source_files)

        assert "Cd" in data.force_coefficients
        assert "force_0" in data.forces
        assert "U_probe" in data.probe_data
        assert "surfaceFieldValue_surface" in data.surface_field_values
        assert "fieldAverage" in data.field_averages
        # All 5 directories should be in source_files
        assert len(source_files) == 5

    def test_parse_field_average(self, tmp_dir):
        """Test fieldAverage output parsing."""
        fa_dir = tmp_dir / "fieldAverage"
        time_dir = fa_dir / "0"
        time_dir.mkdir(parents=True)
        (time_dir / "U_avg.dat").write_text(
            "# Time  value\n"
            "0.1  0.5\n"
            "0.2  0.55\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_field_average(fa_dir, data)

        assert "U_avg" in data.field_averages
        assert data.field_averages["U_avg"] == 0.55  # last value

    def test_parse_forces(self, tmp_dir):
        """Test forces output parsing."""
        forces_dir = tmp_dir / "forces"
        time_dir = forces_dir / "0"
        time_dir.mkdir(parents=True)
        (time_dir / "forces.dat").write_text(
            "# Time  fx  fy  fz\n"
            "0.1  10.0  5.0  0.0\n"
            "0.2  11.0  5.5  0.0\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = SimulationData()
        ingestor._parse_forces(forces_dir, data)

        # The parser captures all columns: force_0=Time, force_1=fx, force_2=fy
        assert "force_0" in data.forces
        assert data.forces["force_0"] == [0.1, 0.2]  # Time column
        assert "force_1" in data.forces
        assert data.forces["force_1"] == [10.0, 11.0]  # fx column
        assert "force_2" in data.forces
        assert data.forces["force_2"] == [5.0, 5.5]  # fy column
