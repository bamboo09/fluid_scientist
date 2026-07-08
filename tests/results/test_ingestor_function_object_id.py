"""Tests for OpenFOAMResultIngestor — functionObject ID-based reading (Commit 7).

These tests verify that the ingestor:
1. Reads postProcessing directories by functionObject name (from MeasurementPlan)
2. Performs identity verification (directory content matches declared type)
3. Stores time column data in ``data.time_values[fo_name]``
4. Falls back to scanning all directories when no plan is provided
5. Records missing functionObjects by name
6. Validates expected objects by functionObject ID
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

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmp_dir() -> Iterator[Path]:
    """Provide a writable temporary directory."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# --------------------------------------------------------------------------- #
# Test 1: Read by functionObject name
# --------------------------------------------------------------------------- #


class TestIngestorReadsByFoName:
    """Verify that the ingestor reads directories by functionObject name."""

    def test_ingestor_reads_by_fo_name(self, tmp_dir):
        """A directory named 'myForceCoeffs' (not 'forceCoeffs') should be
        read when the MeasurementPlan declares that name."""
        # Create postProcessing/myForceCoeffs/0/coefficient.dat
        fc_dir = tmp_dir / "postProcessing" / "myForceCoeffs" / "0"
        fc_dir.mkdir(parents=True)
        (fc_dir / "coefficient.dat").write_text(
            "# Time  Cd  Cl  Cm\n"
            "0.1  1.23  0.45  0.12\n"
            "0.2  1.25  0.46  0.13\n",
        )

        measurement_plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.FORCE_COEFFS,
                    name="myForceCoeffs",
                ),
            ],
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            measurement_plan=measurement_plan,
        )

        # Data should be parsed from myForceCoeffs directory
        assert "Cd" in data.force_coefficients
        assert data.force_coefficients["Cd"] == [1.23, 1.25]
        assert "Cl" in data.force_coefficients
        assert data.force_coefficients["Cl"] == [0.45, 0.46]
        # Should NOT be in missing_data
        assert not any("myForceCoeffs" in entry for entry in data.missing_data)


# --------------------------------------------------------------------------- #
# Test 2: Identity verification
# --------------------------------------------------------------------------- #


class TestIngestorIdentityVerification:
    """Verify that identity mismatch generates a warning."""

    def test_ingestor_identity_verification(self, tmp_dir):
        """A directory named 'myForceCoeffs' containing surfaceFieldValue
        data (not coefficient.dat) should generate an identity warning."""
        # Create postProcessing/myForceCoeffs/0/surfaceFieldValue.dat
        # (wrong file type for forceCoeffs)
        fc_dir = tmp_dir / "postProcessing" / "myForceCoeffs" / "0"
        fc_dir.mkdir(parents=True)
        (fc_dir / "surfaceFieldValue.dat").write_text(
            "# Time  value\n"
            "0.1  100.5\n",
        )

        measurement_plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.FORCE_COEFFS,
                    name="myForceCoeffs",
                ),
            ],
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            measurement_plan=measurement_plan,
        )

        # A warning about identity mismatch should be generated
        identity_warnings = [
            w for w in data.warnings if "Identity mismatch" in w
        ]
        assert len(identity_warnings) > 0
        assert "myForceCoeffs" in identity_warnings[0]
        assert "forceCoeffs" in identity_warnings[0]


# --------------------------------------------------------------------------- #
# Test 3: Store time values
# --------------------------------------------------------------------------- #


class TestIngestorStoresTimeValues:
    """Verify that time column data is stored in data.time_values."""

    def test_ingestor_stores_time_values(self, tmp_dir):
        """Parse forceCoeffs data and verify data.time_values contains
        the time directory names as floats."""
        # Create postProcessing/myForceCoeffs/0.1/ and 0.2/
        for time_val in ["0.1", "0.2", "0.3"]:
            td = tmp_dir / "postProcessing" / "myForceCoeffs" / time_val
            td.mkdir(parents=True)
            (td / "coefficient.dat").write_text(
                f"# Time  Cd  Cl\n"
                f"{time_val}  1.0  0.5\n",
            )

        measurement_plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.FORCE_COEFFS,
                    name="myForceCoeffs",
                ),
            ],
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            measurement_plan=measurement_plan,
        )

        # time_values should have an entry for "myForceCoeffs"
        assert "myForceCoeffs" in data.time_values
        assert data.time_values["myForceCoeffs"] == [0.1, 0.2, 0.3]


# --------------------------------------------------------------------------- #
# Test 4: Fallback without plan
# --------------------------------------------------------------------------- #


class TestIngestorFallbackWithoutPlan:
    """Verify backward-compatible scanning when no plan is provided."""

    def test_ingestor_fallback_without_plan(self, tmp_dir):
        """Without measurement_plan, the ingestor should scan all
        directories and detect type from directory name."""
        # Create postProcessing/forceCoeffs/0/coefficient.dat
        fc_dir = tmp_dir / "postProcessing" / "forceCoeffs" / "0"
        fc_dir.mkdir(parents=True)
        (fc_dir / "coefficient.dat").write_text(
            "# Time  Cd  Cl\n"
            "0.1  1.0  0.5\n",
        )

        # Create postProcessing/probes/0/U.dat
        probes_dir = tmp_dir / "postProcessing" / "probes" / "0"
        probes_dir.mkdir(parents=True)
        (probes_dir / "U.dat").write_text(
            "0.1  1.0  0.0  0.0\n",
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(case_path=tmp_dir)

        # Both should be parsed via fallback scanning
        assert "Cd" in data.force_coefficients
        assert data.force_coefficients["Cd"] == [1.0]
        assert "U_probe" in data.probe_data
        # time_values should be stored under directory names
        assert "forceCoeffs" in data.time_values
        assert data.time_values["forceCoeffs"] == [0.0]
        assert "probes" in data.time_values
        assert data.time_values["probes"] == [0.0]


# --------------------------------------------------------------------------- #
# Test 5: Missing functionObject recorded
# --------------------------------------------------------------------------- #


class TestIngestorMissingFoRecorded:
    """Verify that missing functionObjects are recorded in missing_data."""

    def test_ingestor_missing_fo_recorded(self, tmp_dir):
        """MeasurementPlan expects 'myProbes' but the directory doesn't
        exist — should be recorded in missing_data."""
        # Create an empty postProcessing directory (exists but no myProbes)
        (tmp_dir / "postProcessing").mkdir(parents=True)

        measurement_plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.PROBES,
                    name="myProbes",
                ),
            ],
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            measurement_plan=measurement_plan,
        )

        # myProbes should be in missing_data
        my_probes_missing = any(
            "myProbes" in entry for entry in data.missing_data
        )
        assert my_probes_missing
        # The format should include both name and type
        assert any(
            "myProbes" in entry and "probes" in entry
            for entry in data.missing_data
        )


# --------------------------------------------------------------------------- #
# Test 6: Validate expected objects by ID
# --------------------------------------------------------------------------- #


class TestValidateExpectedObjectsById:
    """Verify _validate_expected_objects checks by functionObject name+type."""

    def test_validate_expected_objects_by_id(self, tmp_dir):
        """Validate with specific functionObject names: present ones should
        not be in missing_data, missing ones should be recorded by name."""
        # Create solver log so solver_log is not missing
        (tmp_dir / "log.simpleFoam").write_text(
            "Time = 0.1\nsolution converged\n",
        )

        # Create postProcessing/myForceCoeffs with data
        fc_dir = tmp_dir / "postProcessing" / "myForceCoeffs" / "0"
        fc_dir.mkdir(parents=True)
        (fc_dir / "coefficient.dat").write_text(
            "# Time  Cd  Cl\n"
            "0.1  1.0  0.5\n",
        )

        measurement_plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.FORCE_COEFFS,
                    name="myForceCoeffs",
                ),
                FunctionObjectSpec(
                    type=FunctionObjectType.PROBES,
                    name="myProbes",
                ),
                FunctionObjectSpec(
                    type=FunctionObjectType.SURFACE_FIELD_VALUE,
                    name="mySurfaceValue",
                ),
            ],
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            case_path=tmp_dir,
            measurement_plan=measurement_plan,
        )

        # myForceCoeffs has data — should NOT be in missing_data
        assert not any("myForceCoeffs" in e for e in data.missing_data)

        # myProbes directory doesn't exist — should be in missing_data
        assert any(
            "myProbes" in e and "probes" in e for e in data.missing_data
        )

        # mySurfaceValue directory doesn't exist — should be in missing_data
        assert any(
            "mySurfaceValue" in e and "surfaceFieldValue" in e
            for e in data.missing_data
        )
