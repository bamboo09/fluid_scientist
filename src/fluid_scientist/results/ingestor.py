"""OpenFOAM Result Ingestor — reads real files from a case directory.

This module parses actual OpenFOAM output files (solver logs, postProcessing
directories, checkMesh logs) into structured SimulationData.

Unlike the old approach that only accepted text strings, this ingestor
reads real files from disk.

Backward compatibility: the ``ingest`` method also accepts the legacy
keyword arguments (``log_text``, ``log_path``, ``post_processing_dir``)
so that existing callers continue to work.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from fluid_scientist.results.models import ResultManifest, SimulationData


class OpenFOAMResultIngestor:
    """Ingests OpenFOAM results from a case directory.

    Reads real files from disk:
    - solver log (*.log or log.*)
    - postProcessing/ directory (forceCoeffs, forces, probes, surfaceFieldValue, fieldAverage)
    - checkMesh log

    Backward compatibility: when called with the legacy keyword arguments
    (``log_text``, ``log_path``, ``post_processing_dir``) instead of
    ``case_path``, the ingestor delegates to the legacy text-based parsing
    path and returns a ``simulation_data.SimulationData`` instance.
    """

    def __init__(self) -> None:
        """Initialise legacy parsers for backward-compatible text ingestion."""
        from fluid_scientist.results.log_parser import OpenFOAMLogParser
        from fluid_scientist.results.postprocessing_parser import (
            PostProcessingParser,
        )

        self._log_parser = OpenFOAMLogParser()
        self._post_parser = PostProcessingParser()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def ingest(
        self,
        case_path: Path | None = None,
        result_manifest: ResultManifest | None = None,
        measurement_plan: Any | None = None,
        *,
        log_text: str | None = None,
        log_path: str | Path | None = None,
        post_processing_dir: str | Path | None = None,
    ) -> Any:
        """Ingest simulation results.

        New API:
            ingest(case_path, result_manifest, measurement_plan)
            -> results.models.SimulationData

        Legacy API:
            ingest(log_text=..., log_path=..., post_processing_dir=...)
            -> simulation_data.SimulationData
        """
        if case_path is not None:
            return self._ingest_from_case(
                case_path,
                result_manifest,
                measurement_plan,
            )
        return self._ingest_legacy(log_text, log_path, post_processing_dir)

    # ------------------------------------------------------------------ #
    # New API — file-based ingestion
    # ------------------------------------------------------------------ #

    def _ingest_from_case(
        self,
        case_path: Path,
        result_manifest: ResultManifest | None,
        measurement_plan: Any | None,
    ) -> SimulationData:
        """Ingest from a real case directory on disk."""
        if not case_path.exists():
            raise FileNotFoundError(f"Case directory not found: {case_path}")

        data = SimulationData()
        source_files: list[str] = []

        # 1. Find and parse solver log
        log_path = self._find_solver_log(case_path)
        if log_path:
            source_files.append(str(log_path))
            self._parse_solver_log(log_path, data)
        else:
            data.missing_data.append("solver_log")

        # 2. Parse postProcessing directory
        post_dir = case_path / "postProcessing"
        if post_dir.exists():
            self._parse_post_processing(post_dir, data, source_files, measurement_plan)
        else:
            data.missing_data.append("postProcessing")

        # 3. Parse checkMesh log if available
        mesh_log = self._find_checkmesh_log(case_path)
        if mesh_log:
            source_files.append(str(mesh_log))
            self._parse_checkmesh_log(mesh_log, data)

        # 4. Check for expected functionObjects from measurement_plan
        if measurement_plan is not None:
            self._validate_expected_objects(measurement_plan, data)

        data.source_files = source_files

        return data

    # ------------------------------------------------------------------ #
    # Legacy API — text/path-based ingestion
    # ------------------------------------------------------------------ #

    def _ingest_legacy(
        self,
        log_text: str | None,
        log_path: str | Path | None,
        post_processing_dir: str | Path | None,
    ):
        """Legacy ingestion from text or file paths."""
        from fluid_scientist.results.simulation_data import (
            SimulationData as LegacySimulationData,
        )

        # 1. Parse log
        if log_text is None and log_path is not None:
            log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")

        data = LegacySimulationData()
        if log_text:
            data = self._log_parser.parse_log(log_text)

        # 2. Parse post-processing files
        if post_processing_dir is not None:
            pp_dir = Path(post_processing_dir)
            self._parse_post_processing_legacy(pp_dir, data)

        return data

    def _parse_post_processing_legacy(self, pp_dir: Path, data: Any) -> None:
        """Parse postProcessing directory for legacy SimulationData."""
        # forceCoeffs
        for fc_dir in pp_dir.rglob("forceCoeffs*"):
            if fc_dir.is_dir():
                files = sorted(fc_dir.glob("*/coefficient.dat"), reverse=True)
                if not files:
                    files = sorted(fc_dir.glob("*.dat"), reverse=True)
                if files:
                    with contextlib.suppress(Exception):
                        data.forces = self._post_parser.parse_force_coeffs(files[0])

        # surfaceFieldValue
        for sv_dir in pp_dir.rglob("surfaceFieldValue*"):
            if sv_dir.is_dir():
                files = sorted(sv_dir.glob("*/surfaceFieldValue.dat"), reverse=True)
                if not files:
                    files = sorted(sv_dir.glob("*.dat"), reverse=True)
                if files:
                    with contextlib.suppress(Exception):
                        sv_data = self._post_parser.parse_surface_field_value(
                            files[0],
                            name=sv_dir.name,
                        )
                        data.surface_values.append(sv_data)

    # ------------------------------------------------------------------ #
    # New API — solver log parsing
    # ------------------------------------------------------------------ #

    def _find_solver_log(self, case_path: Path) -> Path | None:
        """Find the solver log file in the case directory."""
        # Try common patterns: log.*, *.log
        for pattern in ["log.*", "*.log"]:
            logs = list(case_path.glob(pattern))
            if logs:
                return logs[0]
        # Also check for solver-specific names
        for name in ["solver.log", "simulation.log", "run.log"]:
            p = case_path / name
            if p.exists():
                return p
        return None

    def _parse_solver_log(self, log_path: Path, data: SimulationData) -> None:
        """Parse solver log for residuals, continuity, Courant number."""
        try:
            content = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            data.warnings.append(f"Failed to read solver log: {e}")
            return

        lines = content.splitlines()

        # Extract solver name and version
        for line in lines[:50]:
            if "OpenFOAM" in line:
                data.solver_version = line.strip()

        # Parse residuals, continuity, Courant, convergence
        for line in lines:
            # Residuals: "Ux: initial residual = 0.001, final residual = 0.0001"
            residual_match = re.findall(
                r"(\w+):\s+(?:initial|solving)\s+residual\s*=\s*([\d.eE+-]+)",
                line,
            )
            for field_name, residual_val in residual_match:
                if field_name not in data.residuals:
                    data.residuals[field_name] = []
                data.residuals[field_name].append(float(residual_val))

            # Final residuals
            final_match = re.findall(
                r"(\w+):\s+.*?final\s+residual\s*=\s*([\d.eE+-]+)",
                line,
            )
            for field_name, residual_val in final_match:
                data.final_residuals[field_name] = float(residual_val)

            # Continuity errors
            cont_match = re.search(
                r"continuity error[s]?\s*[:=]?\s*([\d.eE+-]+)",
                line,
                re.IGNORECASE,
            )
            if cont_match:
                data.continuity_errors.append(float(cont_match.group(1)))

            # Courant number — prefer the "max" value
            cour_match = re.search(
                r"Courant Number.*?max[:\s]*([\d.eE+-]+)",
                line,
                re.IGNORECASE,
            )
            if not cour_match:
                cour_match = re.search(
                    r"Courant Number\s*[:=]?\s*([\d.eE+-]+)",
                    line,
                    re.IGNORECASE,
                )
            if cour_match:
                data.courant_numbers.append(float(cour_match.group(1)))

            # Convergence
            if "converged" in line.lower() or "solution converged" in line.lower():
                data.converged = True

            # End time
            time_match = re.search(r"^Time\s*=\s*([\d.]+)", line)
            if time_match:
                data.end_time = float(time_match.group(1))

        # Summary values
        if data.courant_numbers:
            data.max_courant = max(data.courant_numbers)
        if data.continuity_errors:
            data.final_continuity_error = data.continuity_errors[-1]

    # ------------------------------------------------------------------ #
    # New API — postProcessing parsing
    # ------------------------------------------------------------------ #

    def _parse_post_processing(
        self,
        post_dir: Path,
        data: SimulationData,
        source_files: list[str],
        measurement_plan: Any | None = None,
    ) -> None:
        """Parse postProcessing directory for functionObject outputs.

        When *measurement_plan* is provided, directories are looked up by
        functionObject name (``post_dir / fo.name``) and the content is
        verified against the declared type (identity verification).

        Without a plan the method falls back to scanning all subdirectories
        and detecting the type from the directory name (backward compat).
        """
        if measurement_plan is not None:
            # Plan-driven: read by functionObject name
            for fo in measurement_plan.function_objects:
                fo_type = (
                    fo.type.value if hasattr(fo.type, "value") else str(fo.type)
                )
                effective_name = fo.name if fo.name else fo_type
                fo_dir = post_dir / effective_name
                if fo_dir.exists():
                    source_files.append(str(fo_dir))
                    self._verify_and_parse(fo_dir, fo_type, effective_name, data)
                else:
                    if fo.name:
                        data.missing_data.append(f"{fo.name} ({fo_type})")
                    else:
                        data.missing_data.append(fo_type)
        else:
            # Fallback: scan all directories (backward compatibility)
            for fo_dir in sorted(post_dir.iterdir()):
                if not fo_dir.is_dir():
                    continue
                source_files.append(str(fo_dir))
                fo_name = fo_dir.name
                fo_type = self._detect_fo_type(fo_name)
                if fo_type:
                    self._verify_and_parse(fo_dir, fo_type, fo_name, data)
                else:
                    data.warnings.append(
                        f"Unknown functionObject type for directory '{fo_name}'",
                    )

    def _verify_and_parse(
        self,
        fo_dir: Path,
        fo_type: str,
        fo_name: str,
        data: SimulationData,
    ) -> None:
        """Verify directory content matches expected type and parse data."""
        # Identity verification: check that expected files are present
        self._verify_identity(fo_dir, fo_type, fo_name, data)

        if fo_type == "forceCoeffs":
            self._parse_force_coeffs(fo_dir, data, fo_name)
        elif fo_type == "forces":
            self._parse_forces(fo_dir, data, fo_name)
        elif fo_type == "probes":
            self._parse_probes(fo_dir, data, fo_name)
        elif fo_type == "surfaceFieldValue":
            self._parse_surface_field_value(fo_dir, data, fo_name)
        elif fo_type == "fieldAverage":
            self._parse_field_average(fo_dir, data, fo_name)
        else:
            data.warnings.append(
                f"Unknown functionObject type '{fo_type}' for '{fo_name}'",
            )

    def _verify_identity(
        self,
        fo_dir: Path,
        fo_type: str,
        fo_name: str,
        data: SimulationData,
    ) -> None:
        """Verify that directory content matches the expected functionObject type.

        Checks for the presence of type-specific file names (e.g.
        ``coefficient.dat`` for *forceCoeffs*).  When the expected file is
        missing a warning is recorded so callers can detect mismatches
        between the declared type and the actual data on disk.
        """
        # Collect all .dat files inside time sub-directories
        dat_files = list(fo_dir.glob("*/" + "*.dat"))
        if not dat_files:
            return  # nothing to verify — let the type-specific parser handle it

        expected_filenames: dict[str, list[str]] = {
            "forceCoeffs": ["coefficient.dat"],
            "surfaceFieldValue": ["surfaceFieldValue.dat"],
            "fieldAverage": ["fieldAverage.dat"],
        }

        expected = expected_filenames.get(fo_type)
        if expected is None:
            return  # no specific filename requirement for this type

        found_names = {f.name for f in dat_files}
        if not any(ef in found_names for ef in expected):
            data.warnings.append(
                f"Identity mismatch: '{fo_name}' declared as '{fo_type}' "
                f"but expected file(s) {expected} not found",
            )

    def _detect_fo_type(self, dir_name: str) -> str | None:
        """Detect functionObject type from directory name (fallback mode)."""
        name_lower = dir_name.lower()
        if "forcecoeffs" in name_lower:
            return "forceCoeffs"
        if "forces" in name_lower:
            return "forces"
        if "probes" in name_lower:
            return "probes"
        if "surfacefieldvalue" in name_lower:
            return "surfaceFieldValue"
        if "fieldaverage" in name_lower:
            return "fieldAverage"
        return None

    def _parse_force_coeffs(
        self,
        fc_dir: Path,
        data: SimulationData,
        fo_name: str = "",
    ) -> None:
        """Parse forceCoeffs output."""
        # Look for time directories
        for time_dir in sorted(fc_dir.iterdir()):
            if not time_dir.is_dir():
                continue

            # Store time values for Strouhal / frequency calculations
            try:
                time_val = float(time_dir.name)
                if fo_name not in data.time_values:
                    data.time_values[fo_name] = []
                data.time_values[fo_name].append(time_val)
            except ValueError:
                pass

            data_file = time_dir / "coefficient.dat"
            if not data_file.exists():
                # Try alternative names
                dat_files = list(time_dir.glob("*.dat"))
                if dat_files:
                    data_file = dat_files[0]
                else:
                    continue

            try:
                lines = data_file.read_text(
                    encoding="utf-8", errors="ignore",
                ).splitlines()
                # Header line has column names
                header = None
                for line in lines:
                    if line.startswith("#"):
                        parts = line.strip().split()
                        if len(parts) > 1:
                            header = parts[1:]
                        continue
                    if header:
                        values = line.split()
                        for name, val in zip(header, values, strict=False):
                            if name not in data.force_coefficients:
                                data.force_coefficients[name] = []
                            with contextlib.suppress(ValueError):
                                data.force_coefficients[name].append(float(val))
            except Exception:
                pass

    def _parse_forces(
        self,
        forces_dir: Path,
        data: SimulationData,
        fo_name: str = "",
    ) -> None:
        """Parse forces output."""
        for time_dir in sorted(forces_dir.iterdir()):
            if not time_dir.is_dir():
                continue

            # Store time values
            try:
                time_val = float(time_dir.name)
                if fo_name not in data.time_values:
                    data.time_values[fo_name] = []
                data.time_values[fo_name].append(time_val)
            except ValueError:
                pass

            dat_files = list(time_dir.glob("*.dat"))
            if dat_files:
                try:
                    lines = dat_files[0].read_text(
                        encoding="utf-8", errors="ignore",
                    ).splitlines()
                    for line in lines:
                        if line.startswith("#"):
                            continue
                        values = line.split()
                        for i, v in enumerate(values):
                            key = f"force_{i}"
                            if key not in data.forces:
                                data.forces[key] = []
                            with contextlib.suppress(ValueError):
                                data.forces[key].append(float(v))
                except Exception:
                    pass

    def _parse_probes(
        self,
        probes_dir: Path,
        data: SimulationData,
        fo_name: str = "",
    ) -> None:
        """Parse probes output."""
        for time_dir in sorted(probes_dir.iterdir()):
            if not time_dir.is_dir():
                continue

            # Store time values
            try:
                time_val = float(time_dir.name)
                if fo_name not in data.time_values:
                    data.time_values[fo_name] = []
                data.time_values[fo_name].append(time_val)
            except ValueError:
                pass

            for probe_file in time_dir.glob("*.dat"):
                field_name = probe_file.stem  # e.g., "U" or "p"
                try:
                    lines = probe_file.read_text(
                        encoding="utf-8", errors="ignore",
                    ).splitlines()
                    key = f"{field_name}_probe"
                    if key not in data.probe_data:
                        data.probe_data[key] = []
                    for line in lines:
                        if line.startswith("#"):
                            continue
                        values = line.split()
                        # Take the last value or the magnitude
                        if values:
                            with contextlib.suppress(ValueError):
                                data.probe_data[key].append(float(values[-1]))
                except Exception:
                    pass

    def _parse_surface_field_value(
        self,
        sfv_dir: Path,
        data: SimulationData,
        fo_name: str = "",
    ) -> None:
        """Parse surfaceFieldValue output."""
        for time_dir in sorted(sfv_dir.iterdir()):
            if not time_dir.is_dir():
                continue

            # Store time values
            try:
                time_val = float(time_dir.name)
                if fo_name not in data.time_values:
                    data.time_values[fo_name] = []
                data.time_values[fo_name].append(time_val)
            except ValueError:
                pass

            for sv_file in time_dir.glob("*.dat"):
                name = sv_file.stem
                try:
                    lines = sv_file.read_text(
                        encoding="utf-8", errors="ignore",
                    ).splitlines()
                    key = f"{name}_surface"
                    if key not in data.surface_field_values:
                        data.surface_field_values[key] = []
                    for line in lines:
                        if line.startswith("#"):
                            continue
                        values = line.split()
                        if values:
                            with contextlib.suppress(ValueError):
                                data.surface_field_values[key].append(
                                    float(values[-1]),
                                )
                except Exception:
                    pass

    def _parse_field_average(
        self,
        fa_dir: Path,
        data: SimulationData,
        fo_name: str = "",
    ) -> None:
        """Parse fieldAverage output."""
        for time_dir in sorted(fa_dir.iterdir()):
            if not time_dir.is_dir():
                continue

            # Store time values
            try:
                time_val = float(time_dir.name)
                if fo_name not in data.time_values:
                    data.time_values[fo_name] = []
                data.time_values[fo_name].append(time_val)
            except ValueError:
                pass

            for fa_file in time_dir.glob("*.dat"):
                name = fa_file.stem
                try:
                    lines = fa_file.read_text(
                        encoding="utf-8", errors="ignore",
                    ).splitlines()
                    for line in lines:
                        if line.startswith("#"):
                            continue
                        values = line.split()
                        if values:
                            with contextlib.suppress(ValueError):
                                data.field_averages[name] = float(values[-1])
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # New API — checkMesh parsing
    # ------------------------------------------------------------------ #

    def _find_checkmesh_log(self, case_path: Path) -> Path | None:
        """Find checkMesh log file."""
        for name in ["log.checkMesh", "checkMesh.log", "log.checkMesh.*"]:
            p = case_path / name
            if p.exists():
                return p
            # Try glob
            for g in case_path.glob(name):
                return g
        return None

    def _parse_checkmesh_log(
        self, mesh_log: Path, data: SimulationData,
    ) -> None:
        """Parse checkMesh log for mesh quality metrics."""
        try:
            content = mesh_log.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        for line in content.splitlines():
            cells_match = re.search(r"cells:\s*(\d+)", line, re.IGNORECASE)
            if cells_match:
                data.mesh_cells = int(cells_match.group(1))

            ar_match = re.search(
                r"aspect ratio.*?max:\s*([\d.]+)",
                line,
                re.IGNORECASE,
            )
            if ar_match:
                data.mesh_max_aspect_ratio = float(ar_match.group(1))

            no_match = re.search(
                r"non-orthogonality.*?max:\s*([\d.]+)",
                line,
                re.IGNORECASE,
            )
            if no_match:
                data.mesh_max_non_orthogonality = float(no_match.group(1))

    # ------------------------------------------------------------------ #
    # New API — validation
    # ------------------------------------------------------------------ #

    def _validate_expected_objects(
        self,
        measurement_plan: Any,
        data: SimulationData,
    ) -> None:
        """Validate that each expected functionObject has corresponding data.

        Each functionObject in the plan is checked individually (by name and
        type).  Missing entries are recorded in ``data.missing_data`` using
        the format ``"<name> (<type>)"`` when a name is set, or just
        ``"<type>"`` as a fallback.
        """
        for fo in getattr(measurement_plan, "function_objects", []):
            fo_type = fo.type.value if hasattr(fo.type, "value") else str(fo.type)

            # Check if this functionObject has data
            has_data = False
            if (
                fo_type == "forceCoeffs" and data.force_coefficients
                or fo_type == "forces" and data.forces
                or fo_type == "probes" and data.probe_data
                or fo_type == "surfaceFieldValue" and data.surface_field_values
                or fo_type == "fieldAverage" and data.field_averages
            ):
                has_data = True

            if not has_data:
                missing_entry = f"{fo.name} ({fo_type})" if fo.name else fo_type
                # Avoid duplicate entries (may already be recorded by
                # _parse_post_processing when the directory was absent)
                if missing_entry not in data.missing_data:
                    data.missing_data.append(missing_entry)


__all__ = ["OpenFOAMResultIngestor"]
