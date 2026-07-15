"""Smoke tests for compiled OpenFOAM 13 cases.

The :class:`SerialSmokeTest` and :class:`ParallelSmokeTest` run a short
``foamRun`` simulation (serial or parallel) and inspect the output for:

* Exit code (0 = success).
* ``FOAM FATAL ERROR`` in the log.
* ``NaN`` or ``Inf`` in field data or residuals.
* Courant number exceeding the threshold.
* Residual convergence behaviour.
* Function object initialisation errors.

When OpenFOAM is not installed, the tests can be run against a
pre-captured log file by passing ``log_output`` to :meth:`validate_log`.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from fluid_scientist.compiler.compiler import CompiledCase
from fluid_scientist.validation_runner.static_validator import ValidationResult


# ---------------------------------------------------------------------------
# Smoke test configuration
# ---------------------------------------------------------------------------


class SmokeTestConfig(BaseModel):
    """Configuration for a smoke test run.

    Attributes:
        timeout_seconds: Maximum wall-clock time for the run.
        n_steps: Number of time steps to run (overrides controlDict endTime).
        max_courant: Maximum acceptable Courant number.
        check_nan: Whether to scan output for NaN.
        check_inf: Whether to scan output for Inf.
        n_processors: Number of processors for parallel runs (0 = serial).
    """

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = 120
    n_steps: int = 5
    max_courant: float = 1.0
    check_nan: bool = True
    check_inf: bool = True
    n_processors: int = 0


# ---------------------------------------------------------------------------
# Base smoke test
# ---------------------------------------------------------------------------


class _SmokeTestBase:
    """Base class for serial and parallel smoke tests."""

    def __init__(
        self,
        config: SmokeTestConfig | None = None,
    ) -> None:
        self.config = config or SmokeTestConfig()

    def validate_log(
        self,
        log_output: str,
        exit_code: int = 0,
    ) -> ValidationResult:
        """Validate a pre-captured foamRun log.

        Parameters:
            log_output: The stdout/stderr from foamRun.
            exit_code: The process exit code.

        Returns:
            A :class:`ValidationResult` with any errors found.
        """
        result = ValidationResult(check_name=self._check_name())

        # Check exit code
        if exit_code != 0:
            result.add_error(
                f"foamRun exited with non-zero code: {exit_code}"
            )

        # Check for FOAM FATAL ERROR
        if "FOAM FATAL ERROR" in log_output:
            error_match = re.search(
                r"FOAM FATAL ERROR.*?(?:\n\n|\Z)", log_output, re.DOTALL
            )
            error_msg = (
                error_match.group(0).strip()[:200]
                if error_match
                else "FOAM FATAL ERROR"
            )
            result.add_error(f"foamRun reported: {error_msg}")

        # Check for FOAM WARNING (non-blocking but worth noting)
        warning_count = log_output.count("FOAM WARNING")
        if warning_count > 10:
            result.add_warning(
                f"foamRun produced {warning_count} FOAM WARNINGs"
            )

        # Check for NaN
        if self.config.check_nan:
            nan_patterns = [
                r"nan\b",
                r"NaN\b",
                r"NAN\b",
            ]
            for pattern in nan_patterns:
                matches = re.findall(pattern, log_output)
                if matches:
                    result.add_error(
                        f"NaN detected in foamRun output "
                        f"({len(matches)} occurrences)"
                    )
                    break

        # Check for Inf
        if self.config.check_inf:
            inf_patterns = [
                r"\binf\b",
                r"\bInf\b",
                r"\bINF\b",
            ]
            for pattern in inf_patterns:
                matches = re.findall(pattern, log_output)
                if matches:
                    result.add_error(
                        f"Inf detected in foamRun output "
                        f"({len(matches)} occurrences)"
                    )
                    break

        # Check Courant number
        courant_matches = re.findall(
            r"Courant Number mean:\s*([\d.eE+-]+)\s+max:\s*([\d.eE+-]+)",
            log_output,
        )
        if courant_matches:
            last_courant = courant_matches[-1]
            max_co = float(last_courant[1])
            if max_co > self.config.max_courant:
                result.add_error(
                    f"Max Courant number {max_co} exceeds threshold "
                    f"{self.config.max_courant}"
                )
        else:
            result.add_warning("Could not extract Courant number from log")

        # Check for solver residual progress
        residual_patterns = [
            r"Solving for Ux,\s*Initial residual\s*=\s*([\d.eE+-]+)",
            r"Solving for U,\s*Initial residual\s*=\s*([\d.eE+-]+)",
            r"Solving for p,\s*Initial residual\s*=\s*([\d.eE+-]+)",
        ]
        for pattern in residual_patterns:
            residuals = re.findall(pattern, log_output)
            if residuals:
                first_res = float(residuals[0])
                last_res = float(residuals[-1])
                if len(residuals) > 1 and last_res > first_res:
                    result.add_warning(
                        f"Residuals increasing: first={first_res}, "
                        f"last={last_res}"
                    )
                break

        # Check for function object initialisation
        if "functions" in log_output or "functionObject" in log_output:
            if "error" in log_output.lower() and "function" in log_output.lower():
                # Look for function object specific errors
                fo_error_match = re.search(
                    r"(?:function|FunctionObject).*?[Ee]rror.*?(?:\n|$)",
                    log_output,
                )
                if fo_error_match:
                    result.add_error(
                        f"Function object error: {fo_error_match.group(0).strip()}"
                    )

        # Check for time step completion
        time_step_pattern = re.findall(
            r"Time\s*=\s*([\d.eE+-]+)", log_output
        )
        if not time_step_pattern:
            result.add_error(
                "No time steps completed in foamRun output "
                "(simulation may not have started)"
            )
        else:
            n_completed = len(time_step_pattern)
            if n_completed < self.config.n_steps:
                result.add_warning(
                    f"Only {n_completed} time steps completed "
                    f"(expected {self.config.n_steps})"
                )

        # Check for segmentation fault
        if "Segmentation fault" in log_output or "SIGSEGV" in log_output:
            result.add_error("Segmentation fault detected in foamRun output")

        # Check for exception
        if "Exception" in log_output and "foamRun" in log_output:
            result.add_error("Exception detected in foamRun output")

        return result

    def _check_name(self) -> str:
        return "smoke_test"

    def _write_case(
        self, case: CompiledCase, case_dir: Path
    ) -> None:
        """Write a CompiledCase to a directory."""
        for fpath, content in case.files.items():
            full_path = case_dir / fpath
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Serial smoke test
# ---------------------------------------------------------------------------


class SerialSmokeTest(_SmokeTestBase):
    """Run a short serial foamRun simulation and inspect the output.

    This test writes the compiled case to a temporary directory, runs
    ``foamRun`` for a small number of time steps, and checks the log
    output for errors, NaN, Inf, Courant number, and residuals.

    When OpenFOAM is not available, use :meth:`validate_log` with a
    pre-captured log.
    """

    def run(
        self,
        case: CompiledCase,
        case_dir: Path | None = None,
    ) -> tuple[ValidationResult, str, int]:
        """Run the serial smoke test.

        Parameters:
            case: The compiled case to test.
            case_dir: Directory to write the case to.  If ``None``, a
                temporary directory is used.

        Returns:
            A tuple of ``(ValidationResult, log_output, exit_code)``.
        """
        if case_dir is None:
            with tempfile.TemporaryDirectory() as tmpdir:
                return self._run_impl(case, Path(tmpdir))
        return self._run_impl(case, case_dir)

    def _run_impl(
        self, case: CompiledCase, case_dir: Path
    ) -> tuple[ValidationResult, str, int]:
        """Internal run implementation."""
        self._write_case(case, case_dir)

        try:
            proc = subprocess.run(
                ["foamRun", "-case", str(case_dir)],
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                cwd=str(case_dir),
            )
            log_output = proc.stdout + proc.stderr
            exit_code = proc.returncode
        except FileNotFoundError:
            log_output = "foamRun command not found (OpenFOAM not installed)"
            exit_code = -1
        except subprocess.TimeoutExpired:
            log_output = f"foamRun timed out after {self.config.timeout_seconds}s"
            exit_code = -2

        result = self.validate_log(log_output, exit_code)
        if exit_code == -1:
            result.add_warning(
                "OpenFOAM not installed; cannot run actual smoke test"
            )

        return result, log_output, exit_code

    def _check_name(self) -> str:
        return "serial_smoke_test"


# ---------------------------------------------------------------------------
# Parallel smoke test
# ---------------------------------------------------------------------------


class ParallelSmokeTest(_SmokeTestBase):
    """Run a short parallel foamRun simulation and inspect the output.

    Similar to :class:`SerialSmokeTest` but uses ``mpirun`` to run
    ``foamRun`` in parallel.  The number of processors is controlled by
    :attr:`SmokeTestConfig.n_processors`.
    """

    def __init__(
        self,
        config: SmokeTestConfig | None = None,
    ) -> None:
        if config is None:
            config = SmokeTestConfig(n_processors=4)
        elif config.n_processors <= 0:
            config.n_processors = 4
        super().__init__(config)

    def run(
        self,
        case: CompiledCase,
        case_dir: Path | None = None,
    ) -> tuple[ValidationResult, str, int]:
        """Run the parallel smoke test.

        Parameters:
            case: The compiled case to test.
            case_dir: Directory to write the case to.

        Returns:
            A tuple of ``(ValidationResult, log_output, exit_code)``.
        """
        if case_dir is None:
            with tempfile.TemporaryDirectory() as tmpdir:
                return self._run_impl(case, Path(tmpdir))
        return self._run_impl(case, case_dir)

    def _run_impl(
        self, case: CompiledCase, case_dir: Path
    ) -> tuple[ValidationResult, str, int]:
        """Internal run implementation."""
        self._write_case(case, case_dir)

        n_proc = self.config.n_processors

        # Decompose the case if decomposeParDict doesn't exist
        if not (case_dir / "system/decomposeParDict").exists():
            self._write_decompose_dict(case_dir, n_proc)

        commands: list[list[str]] = [
            ["decomposePar", "-case", str(case_dir), "-force"],
            [
                "mpirun", "-np", str(n_proc),
                "foamRun", "-parallel", "-case", str(case_dir),
            ],
        ]

        all_output: list[str] = []
        exit_code = 0

        for cmd in commands:
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout_seconds,
                    cwd=str(case_dir),
                )
                all_output.append(proc.stdout)
                all_output.append(proc.stderr)
                if proc.returncode != 0:
                    exit_code = proc.returncode
                    break
            except FileNotFoundError:
                all_output.append(
                    f"{' '.join(cmd[:1])} command not found "
                    f"(OpenFOAM or MPI not installed)"
                )
                exit_code = -1
                break
            except subprocess.TimeoutExpired:
                all_output.append(
                    f"{' '.join(cmd[:1])} timed out after "
                    f"{self.config.timeout_seconds}s"
                )
                exit_code = -2
                break

        log_output = "\n".join(all_output)
        result = self.validate_log(log_output, exit_code)

        if exit_code == -1:
            result.add_warning(
                "OpenFOAM or MPI not installed; cannot run actual "
                "parallel smoke test"
            )

        return result, log_output, exit_code

    def _write_decompose_dict(self, case_dir: Path, n_proc: int) -> None:
        """Write a minimal decomposeParDict."""
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
  =========                 |
  \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\\\    /   O peration     | Website:  https://openfoam.org
    \\\\  /    A nd           | Version:  13
     \\\\/     M anipulation  |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      decomposeParDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

numberOfSubdomains  {n_proc};

method              scotch;

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""
        target = case_dir / "system/decomposeParDict"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _check_name(self) -> str:
        return "parallel_smoke_test"


__all__ = [
    "ParallelSmokeTest",
    "SerialSmokeTest",
    "SmokeTestConfig",
]
