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
            self._parse_post_processing(post_dir, data, source_files)
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

            # Courant number \u2014 prefer the "max" value
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
    ) -> None:
        """Parse postProcessing directory for functionObject outputs."""
        # forceCoeffs
        fc_dir = post_dir / "forceCoeffs"
        if fc_dir.exists():
            source_files.append(str(fc_dir))
            self._parse_force_coeffs(fc_dir, data)

        # forces
        forces_dir = post_dir / "forces"
        if forces_dir.exists():
            source_files.append(str(forces_dir))
            self._parse_forces(forces_dir, data)

        # probes
        probes_dir = post_dir / "probes"
        if probes_dir.exists():
            source_files.append(str(probes_dir))
            self._parse_probes(probes_dir, data)

        # surfaceFieldValue
        sfv_dir = post_dir / "surfaceFieldValue"
        if sfv_dir.exists():
            source_files.append(str(sfv_dir))
            self._parse_surface_field_value(sfv_dir, data)

        # fieldAverage
        fa_dir = post_dir / "fieldAverage"
        if fa_dir.exists():
            source_files.append(str(fa_dir))
            self._parse_field_average(fa_dir, data)

    def _parse_force_coeffs(self, fc_dir: Path, data: SimulationData) -> None:
        """Parse forceCoeffs output."""
        # Look for time directories
        for time_dir in sorted(fc_dir.iterdir()):
            if not time_dir.is_dir():
                continue
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
                        for name, val in zip(header, values):
                            if name not in data.force_coefficients:
                                data.force_coefficients[name] = []
                            try:
                                data.force_coefficients[name].append(float(val))
                            except ValueError:
                                pass
            except Exception:
                pass

    def _parse_forces(self, forces_dir: Path, data: SimulationData) -> None:
        """Parse forces output."""
        for time_dir in sorted(forces_dir.iterdir()):
            if not time_dir.is_dir():
                continue
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
                            try:
                                data.forces[key].append(float(v))
                            except ValueError:
                                pass
                except Exception:
                    pass

    def _parse_probes(self, probes_dir: Path, data: SimulationData) -> None:
        """Parse probes output."""
        for time_dir in sorted(probes_dir.iterdir()):
            if not time_dir.is_dir():
                continue
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
                            try:
                                data.probe_data[key].append(float(values[-1]))
                            except ValueError:
                                pass
                except Exception:
                    pass

    def _parse_surface_field_value(
        self, sfv_dir: Path, data: SimulationData,
    ) -> None:
        """Parse surfaceFieldValue output."""
        for time_dir in sorted(sfv_dir.iterdir()):
            if not time_dir.is_dir():
                continue
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
                            try:
                                data.surface_field_values[key].append(
                                    float(values[-1]),
                                )
                            except ValueError:
                                pass
                except Exception:
                    pass

    def _parse_field_average(self, fa_dir: Path, data: SimulationData) -> None:
        """Parse fieldAverage output."""
        for time_dir in sorted(fa_dir.iterdir()):
            if not time_dir.is_dir():
                continue
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
                            try:
                                data.field_averages[name] = float(values[-1])
                            except ValueError:
                                pass
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
        """Validate that expected functionObjects have corresponding data."""
        expected_types = set()
        for fo in getattr(measurement_plan, "function_objects", []):
            fo_type = fo.type.value if hasattr(fo.type, "value") else str(fo.type)
            expected_types.add(fo_type)

        # Check which ones have data
        if "forceCoeffs" in expected_types and not data.force_coefficients:
            data.missing_data.append("forceCoeffs")
        if "forces" in expected_types and not data.forces:
            data.missing_data.append("forces")
        if "probes" in expected_types and not data.probe_data:
            data.missing_data.append("probes")
        if "surfaceFieldValue" in expected_types and not data.surface_field_values:
            data.missing_data.append("surfaceFieldValue")
        if "fieldAverage" in expected_types and not data.field_averages:
            data.missing_data.append("fieldAverage")


__all__ = ["OpenFOAMResultIngestor"]
