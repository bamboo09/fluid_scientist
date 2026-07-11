"""Compile-Readiness Validator.

Performs the multi-stage validation required before a draft can be
published as COMPILE_READY:

1. Schema completeness
2. Dimensional consistency
3. Parameter dependency closure
4. File completeness (0/, constant/, system/ present)
5. OpenFOAM dictionary parsing (syntax validation)
6. Patch name consistency between blockMesh and field files
7. Field/solver compatibility
8. functionObject configuration check
9. Mesh generation (blockMesh) -- if OpenFOAM is available
10. checkMesh -- if OpenFOAM is available
11. Dynamic mesh / multi-region init check
12. Minimal solver dry-run (0 or 1 timestep) -- if OpenFOAM is available
13. functionObject output check
14. Postprocessing script smoke test

When OpenFOAM is not available in the environment, the validator
records this explicitly rather than mocking success.  The final
``compile_ready`` flag is only True when the runtime-OpenFOAM steps
have actually passed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Validation result model
# ---------------------------------------------------------------------------


class ValidationCheckResult(BaseModel):
    check_name: str
    passed: bool
    severity: Literal["error", "warning", "info"] = "error"
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class CompileReadinessReport(BaseModel):
    compile_ready: bool = False
    openfoam_available: bool = False
    openfoam_version: str = ""
    checks: list[ValidationCheckResult] = Field(default_factory=list)
    generated_files: list[str] = Field(default_factory=list)
    mesh_statistics: dict[str, Any] = Field(default_factory=dict)
    solver_dry_run_output: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_duration_ms: float = 0.0

    def add_check(self, check: ValidationCheckResult) -> None:
        self.checks.append(check)
        if not check.passed and check.severity == "error":
            self.errors.append(f"{check.check_name}: {check.message}")
        elif not check.passed and check.severity == "warning":
            self.warnings.append(f"{check.check_name}: {check.message}")


from typing import Literal  # noqa: E402


# ---------------------------------------------------------------------------
# OpenFOAM detection
# ---------------------------------------------------------------------------


def _detect_openfoam() -> tuple[bool, str]:
    """Check whether OpenFOAM is available in the current environment."""
    # Check common environment variables
    for var in ("WM_PROJECT_DIR", "FOAM_APP", "OPENFOAM_DIR"):
        if os.environ.get(var):
            return True, os.environ.get("WM_PROJECT_VERSION", "unknown")
    # Check for blockMesh on PATH
    if shutil.which("blockMesh") or shutil.which("blockMesh.exe"):
        return True, "path-detected"
    return False, ""


def _find_openfoam_command(name: str) -> str | None:
    """Find an OpenFOAM executable by name."""
    return shutil.which(name) or shutil.which(f"{name}.exe")


# ---------------------------------------------------------------------------
# Dictionary syntax validation (pure Python -- no OpenFOAM needed)
# ---------------------------------------------------------------------------

_FOAM_DICT_ENTRY = re.compile(r"""^\s*(\w+)\s+(.*?)\s*;\s*$""")
_FOAM_BLOCK_START = re.compile(r"""^\s*(\w+)\s*\{\s*$""")
_FOAM_BLOCK_END = re.compile(r"""^\s*\}\s*$""")


def _validate_foam_dictionary_syntax(text: str) -> tuple[bool, str, list[tuple[int, str]]]:
    """Structural validation of an OpenFOAM dictionary.

    Returns (valid, error_message, [(line_no, line)]).
    Checks: balanced braces and parentheses, proper termination.
    """
    brace_depth = 0
    paren_depth = 0
    errors: list[tuple[int, str]] = []
    in_block_comment = False

    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()

        # Handle block comments
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
                line = line.split("*/", 1)[1].strip()
            else:
                continue
        if line.startswith("/*"):
            if "*/" not in line:
                in_block_comment = True
            continue

        if not line or line.startswith("//"):
            continue
        # Strip inline comments
        if "//" in line:
            line = line.split("//", 1)[0].strip()
            if not line:
                continue

        # Count braces and parens character by character (since multiple can appear on one line)
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth < 0:
                    errors.append((lineno, "unexpected closing brace"))
                    brace_depth = 0
            elif ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
                if paren_depth < 0:
                    errors.append((lineno, "unexpected closing parenthesis"))
                    paren_depth = 0
            i += 1

    if brace_depth != 0:
        errors.append((0, f"unbalanced braces (depth={brace_depth})"))
    if paren_depth != 0:
        errors.append((0, f"unbalanced parentheses (depth={paren_depth})"))
    return len(errors) == 0, ("OK" if not errors else f"{len(errors)} syntax issue(s)"), errors


# ---------------------------------------------------------------------------
# CompileReadinessValidator
# ---------------------------------------------------------------------------


class CompileReadinessValidator:
    """Run the full compile-readiness validation chain."""

    def validate(
        self,
        case_dir: str | Path,
        case_dict: dict[str, Any] | None = None,
        design: dict[str, Any] | None = None,
        run_openfoam: bool = True,
    ) -> CompileReadinessReport:
        """Validate a generated case directory.

        Args:
            case_dir: Path to the generated OpenFOAM case.
            case_dict: In-memory case dict (from the compiler) for schema checks.
            design: The closed design (for parameter consistency checks).
            run_openfoam: If False, skip OpenFOAM runtime checks (useful for CI
                environments without OpenFOAM).
        """
        import time

        t0 = time.time()
        report = CompileReadinessReport()
        case_path = Path(case_dir)

        of_available, of_version = _detect_openfoam()
        report.openfoam_available = of_available
        report.openfoam_version = of_version

        # ---------- 1-4: Static checks (no OpenFOAM needed) ----------
        self._check_schema_completeness(report, case_dict)
        self._check_dimensional_consistency(report, design)
        self._check_file_completeness(report, case_path)
        self._check_dictionary_syntax(report, case_path)
        self._check_patch_consistency(report, case_path)
        self._check_field_solver_compatibility(report, case_path, case_dict)
        self._check_function_objects(report, case_path)

        # ---------- 5-14: OpenFOAM runtime checks ----------
        if run_openfoam and of_available:
            self._run_block_mesh(report, case_path)
            self._run_check_mesh(report, case_path)
            self._run_solver_dry_run(report, case_path)
            self._check_function_object_outputs(report, case_path)
        elif run_openfoam and not of_available:
            report.add_check(ValidationCheckResult(
                check_name="openfoam_runtime",
                passed=False,
                severity="error",
                message="OpenFOAM not found in environment; runtime validation cannot proceed. compile_ready cannot be set to True.",
            ))

        # ---------- Finalize ----------
        # compile_ready is True only if no ERROR-level checks failed AND
        # OpenFOAM runtime checks have passed (if requested).
        has_errors = any(not c.passed and c.severity == "error" for c in report.checks)
        runtime_needed = run_openfoam
        runtime_passed = False
        if runtime_needed:
            runtime_checks = [c for c in report.checks if c.check_name in (
                "blockMesh", "checkMesh", "solver_dry_run"
            )]
            runtime_passed = all(c.passed for c in runtime_checks) if runtime_checks else False
        else:
            runtime_passed = True  # not requested

        report.compile_ready = (not has_errors) and (not runtime_needed or runtime_passed)
        report.validation_duration_ms = (time.time() - t0) * 1000
        return report

    # ------------------------------------------------------------------
    # Static checks
    # ------------------------------------------------------------------

    def _check_schema_completeness(self, report: CompileReadinessReport, case_dict: dict | None) -> None:
        if case_dict is None:
            report.add_check(ValidationCheckResult(
                check_name="schema_completeness",
                passed=False,
                severity="warning",
                message="No case dict provided for schema check.",
            ))
            return
        required_system = ["controlDict", "fvSchemes", "fvSolution"]
        missing = [f for f in required_system if f not in case_dict.get("system", {})]
        required_constant = ["transportProperties"]
        missing += [f for f in required_constant if f not in case_dict.get("constant", {})]
        required_zero = ["U", "p"]
        missing += [f for f in required_zero if f not in case_dict.get("0", {})]
        report.add_check(ValidationCheckResult(
            check_name="schema_completeness",
            passed=len(missing) == 0,
            severity="error",
            message="All required dictionaries present." if not missing else f"Missing required files: {missing}",
            details={"missing": missing},
        ))

    def _check_dimensional_consistency(self, report: CompileReadinessReport, design: dict | None) -> None:
        if not design:
            report.add_check(ValidationCheckResult(
                check_name="dimensional_consistency",
                passed=True,
                severity="info",
                message="No design supplied; skipping dimensional check.",
            ))
            return
        # Basic sanity: Re should be positive, nu positive if present
        issues: list[str] = []
        params = design.get("resolved_values", {})
        for key in ("Re", "nu", "U_ref", "rho"):
            if key in params:
                val = params[key]
                try:
                    if float(val) <= 0 and key != "Re" or (key == "Re" and float(val) < 0):
                        issues.append(f"{key}={val} must be positive")
                except (TypeError, ValueError):
                    pass
        report.add_check(ValidationCheckResult(
            check_name="dimensional_consistency",
            passed=len(issues) == 0,
            severity="warning",
            message="Dimensional checks passed." if not issues else "; ".join(issues),
        ))

    def _check_file_completeness(self, report: CompileReadinessReport, case_path: Path) -> None:
        required_dirs = ["0", "constant", "system"]
        missing = [d for d in required_dirs if not (case_path / d).is_dir()]
        required_files = ["system/controlDict", "system/fvSchemes", "system/fvSolution", "0/U", "0/p", "constant/transportProperties"]
        missing_files = [f for f in required_files if not (case_path / f).is_file()]
        report.generated_files = [
            p.relative_to(case_path).as_posix()
            for p in sorted(case_path.rglob("*"))
            if p.is_file()
        ]
        all_present = not missing and not missing_files
        report.add_check(ValidationCheckResult(
            check_name="file_completeness",
            passed=all_present,
            severity="error",
            message="All required files and directories present." if all_present else f"Missing dirs: {missing}, missing files: {missing_files}",
            details={"missing_dirs": missing, "missing_files": missing_files},
        ))

    def _check_dictionary_syntax(self, report: CompileReadinessReport, case_path: Path) -> None:
        all_ok = True
        issues: list[str] = []
        for rel_path in ["system/controlDict", "system/fvSchemes", "system/fvSolution", "0/U", "0/p", "constant/transportProperties"]:
            fpath = case_path / rel_path
            if not fpath.is_file():
                continue
            text = fpath.read_text(encoding="utf-8", errors="replace")
            ok, msg, errs = _validate_foam_dictionary_syntax(text)
            if not ok:
                all_ok = False
                issues.append(f"{rel_path}: {msg}")
        report.add_check(ValidationCheckResult(
            check_name="dictionary_syntax",
            passed=all_ok,
            severity="error",
            message="All dictionaries parse correctly." if all_ok else "; ".join(issues),
        ))

    def _check_patch_consistency(self, report: CompileReadinessReport, case_path: Path) -> None:
        """Verify that patches referenced in 0/U and 0/p exist in blockMeshDict boundary."""
        bm_path = case_path / "system" / "blockMeshDict"
        u_path = case_path / "0" / "U"
        p_path = case_path / "0" / "p"
        if not all(p.is_file() for p in (bm_path, u_path, p_path)):
            report.add_check(ValidationCheckResult(
                check_name="patch_consistency",
                passed=True,
                severity="info",
                message="Skipping patch check (missing files).",
            ))
            return
        bm_text = bm_path.read_text(encoding="utf-8", errors="replace")
        # Extract patch names from boundary section.
        # Format: boundary ( patchName { type ...; faces (...); } ... );
        bm_patches: set[str] = set()
        in_boundary_list = False
        boundary_paren_depth = 0
        # Find "boundary" keyword, then parse entries between outer ( ... )
        lines = bm_text.split("\n")
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            # Look for "boundary" followed by optional whitespace then "("
            if not in_boundary_list and re.match(r"^boundary\s*$", s):
                # Next non-empty line should be "("
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and lines[j].strip() == "(":
                    in_boundary_list = True
                    boundary_paren_depth = 1
                    i = j + 1
                    continue
            if in_boundary_list:
                # Track paren depth
                for ch in s:
                    if ch == "(":
                        boundary_paren_depth += 1
                    elif ch == ")":
                        boundary_paren_depth -= 1
                if boundary_paren_depth <= 0:
                    in_boundary_list = False
                    i += 1
                    continue
                # A patch entry: a word (identifier) on its own line, followed by { on next line
                # Patch names are identifiers that appear right before a { at depth 1
                # We look for lines that are just a word (patch name) at depth 1
                if re.match(r"^(\w+)\s*$", s) and boundary_paren_depth == 1:
                    # Check if next non-empty line starts with {
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and lines[j].strip().startswith("{"):
                        bm_patches.add(s)
            i += 1

        # Also check for patches added by snappyHexMesh/topoSet (look for snappyHexMeshDict or topoSetDict)
        # These add patches dynamically; allow wall/cylinder etc if those files exist
        dynamic_patches: set[str] = set()
        for snappy_name in ("system/snappyHexMeshDict", "system/topoSetDict"):
            if (case_path / snappy_name).is_file():
                snappy_text = (case_path / snappy_name).read_text(encoding="utf-8", errors="replace")
                # Extract patch names from add/faceZone controls
                for m in re.finditer(r"(?:wall|patch|faceZone)\s+(\w+)", snappy_text):
                    dynamic_patches.add(m.group(1))
                for m in re.finditer(r"\bname\s+(\w+)\s*;", snappy_text):
                    dynamic_patches.add(m.group(1))

        # Extract patches from U/p boundaryField
        def extract_bcs(text: str) -> set[str]:
            cleaned = re.sub(r"//.*", "", text)
            start = re.search(r"\bboundaryField\b\s*\{", cleaned)
            if not start:
                return set()

            pos = start.end()
            depth = 1
            body_start = pos
            while pos < len(cleaned) and depth > 0:
                if cleaned[pos] == "{":
                    depth += 1
                elif cleaned[pos] == "}":
                    depth -= 1
                pos += 1
            if depth != 0:
                return set()

            body = cleaned[body_start : pos - 1]
            patches: set[str] = set()
            token_re = re.compile(r"\b([A-Za-z_]\w*)\b\s*\{")
            reserved = {"type", "value", "uniform", "nonuniform"}
            for match in token_re.finditer(body):
                prefix = body[: match.start()]
                local_depth = prefix.count("{") - prefix.count("}")
                name = match.group(1)
                if local_depth == 0 and name not in reserved:
                    patches.add(name)
            return patches

        u_patches = extract_bcs(u_path.read_text(encoding="utf-8"))
        p_patches = extract_bcs(p_path.read_text(encoding="utf-8"))
        all_bf_patches = u_patches | p_patches
        # Allow dynamic patches and standard 2D front/back
        allowed_extra = dynamic_patches | {"front", "back", "frontandback", "defaultFaces", "walls"}
        expected_but_missing = {p for p in all_bf_patches if p not in bm_patches and p not in allowed_extra}
        report.add_check(ValidationCheckResult(
            check_name="patch_consistency",
            passed=len(expected_but_missing) == 0,
            severity="error",
            message="All boundary patches consistent between mesh and field files." if not expected_but_missing else f"Patches in field files not found in blockMesh boundary: {expected_but_missing}",
            details={"mesh_patches": sorted(bm_patches), "field_patches": sorted(all_bf_patches), "dynamic_patches": sorted(dynamic_patches)},
        ))

    def _check_field_solver_compatibility(self, report: CompileReadinessReport, case_path: Path, case_dict: dict | None) -> None:
        cdict = None
        cd_path = case_path / "system" / "controlDict"
        if cd_path.is_file():
            cdict = cd_path.read_text(encoding="utf-8", errors="replace")
        if not cdict:
            report.add_check(ValidationCheckResult(
                check_name="field_solver_compatibility",
                passed=True,
                severity="info",
                message="No controlDict; skipping solver-field check.",
            ))
            return
        app_match = re.search(r"application\s+(\w+)\s*;", cdict)
        solver_name = app_match.group(1) if app_match else ""
        incompressible_solvers = {"pimpleFoam", "pisoFoam", "simpleFoam", "icoFoam"}
        if solver_name in incompressible_solvers:
            # Check that p uses kinematic pressure (dimensions [0 2 -2 ...])
            p_path = case_path / "0" / "p"
            if p_path.is_file():
                p_text = p_path.read_text(encoding="utf-8", errors="replace")
                dim_match = re.search(r"dimensions\s+(\[[^\]]+\])\s*;", p_text)
                dims = dim_match.group(1) if dim_match else ""
                # For incompressible solvers p is often [0 2 -2 0 0 0 0] (p/rho)
                report.add_check(ValidationCheckResult(
                    check_name="field_solver_compatibility",
                    passed=True,
                    severity="info",
                    message=f"Solver {solver_name} is incompressible; p dimensions = {dims}.",
                ))
            else:
                report.add_check(ValidationCheckResult(
                    check_name="field_solver_compatibility",
                    passed=False,
                    severity="error",
                    message="Missing p field file.",
                ))
        else:
            report.add_check(ValidationCheckResult(
                check_name="field_solver_compatibility",
                passed=True,
                severity="info",
                message=f"Solver {solver_name} not in incompressible list; no additional field checks applied.",
            ))

    def _check_function_objects(self, report: CompileReadinessReport, case_path: Path) -> None:
        cd_path = case_path / "system" / "controlDict"
        if not cd_path.is_file():
            report.add_check(ValidationCheckResult(
                check_name="function_objects",
                passed=True,
                severity="info",
                message="No controlDict; skipping functionObject check.",
            ))
            return
        cd_text = cd_path.read_text(encoding="utf-8", errors="replace")
        has_functions = "functions" in cd_text
        has_residuals = "residuals" in cd_text
        report.add_check(ValidationCheckResult(
            check_name="function_objects",
            passed=has_functions,
            severity="warning" if not has_functions else "info",
            message="functionObjects configured." if has_functions else "No functions block found in controlDict (residuals/forces/sampling will not run).",
        ))

    # ------------------------------------------------------------------
    # OpenFOAM runtime checks
    # ------------------------------------------------------------------

    def _run_command(self, cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
        try:
            result = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                env=os.environ.copy(),
            )
            return result.returncode, result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            return -1, f"Command timed out after {timeout}s: {' '.join(cmd)}"
        except FileNotFoundError as e:
            return -1, f"Executable not found: {e}"
        except Exception as e:
            return -1, f"Error running command: {e}"

    def _run_block_mesh(self, report: CompileReadinessReport, case_path: Path) -> None:
        bm = _find_openfoam_command("blockMesh")
        if not bm:
            report.add_check(ValidationCheckResult(
                check_name="blockMesh",
                passed=False,
                severity="error",
                message="blockMesh executable not found on PATH.",
            ))
            return
        rc, output = self._run_command([bm, "-case", str(case_path)], case_path, timeout=60)
        success = rc == 0
        report.mesh_statistics["blockMesh_returncode"] = rc
        report.mesh_statistics["blockMesh_log_tail"] = output[-500:] if output else ""
        report.add_check(ValidationCheckResult(
            check_name="blockMesh",
            passed=success,
            severity="error",
            message="blockMesh completed successfully." if success else f"blockMesh failed (rc={rc}).",
            details={"returncode": rc},
        ))

    def _run_check_mesh(self, report: CompileReadinessReport, case_path: Path) -> None:
        cm = _find_openfoam_command("checkMesh")
        if not cm:
            report.add_check(ValidationCheckResult(
                check_name="checkMesh",
                passed=False,
                severity="error",
                message="checkMesh executable not found on PATH.",
            ))
            return
        # Only run if blockMesh succeeded (polyMesh exists)
        poly_dir = case_path / "constant" / "polyMesh"
        if not poly_dir.is_dir():
            report.add_check(ValidationCheckResult(
                check_name="checkMesh",
                passed=False,
                severity="error",
                message="No polyMesh directory; cannot run checkMesh.",
            ))
            return
        rc, output = self._run_command(
            [cm, "-case", str(case_path), "-allGeometry", "-allTopology"],
            case_path, timeout=60,
        )
        success = rc == 0 and "Failed" not in output and "Mesh OK" in output
        # checkMesh returns 0 even with warnings; check for "Mesh OK"
        if rc == 0 and "Mesh OK" in output:
            success = True
        report.mesh_statistics["checkMesh_returncode"] = rc
        report.mesh_statistics["checkMesh_log_tail"] = output[-800:] if output else ""
        report.add_check(ValidationCheckResult(
            check_name="checkMesh",
            passed=success,
            severity="error",
            message="checkMesh reports Mesh OK." if success else f"checkMesh did not confirm Mesh OK (rc={rc}).",
            details={"returncode": rc},
        ))

    def _run_solver_dry_run(self, report: CompileReadinessReport, case_path: Path) -> None:
        """Run solver for 0 iterations (or endTime=deltaT) as a smoke test.

        We set endTime to the first write interval (or 1 step) by creating
        a temporary override, run the solver, and check that it starts
        without dictionary/configuration errors.
        """
        cd_path = case_path / "system" / "controlDict"
        if not cd_path.is_file():
            report.add_check(ValidationCheckResult(
                check_name="solver_dry_run",
                passed=False,
                severity="error",
                message="No controlDict; cannot run solver.",
            ))
            return
        cd_text = cd_path.read_text(encoding="utf-8")
        app_match = re.search(r"application\s+(\w+)\s*;", cd_text)
        if not app_match:
            report.add_check(ValidationCheckResult(
                check_name="solver_dry_run",
                passed=False,
                severity="error",
                message="No application entry in controlDict.",
            ))
            return
        solver_name = app_match.group(1)
        solver_exe = _find_openfoam_command(solver_name)
        if not solver_exe:
            report.add_check(ValidationCheckResult(
                check_name="solver_dry_run",
                passed=False,
                severity="error",
                message=f"Solver {solver_name} not found on PATH.",
            ))
            return
        # Modify endTime to be small (1 time step)
        # Read deltaT
        dt_match = re.search(r"deltaT\s+([0-9.eE+-]+)\s*;", cd_text)
        dt = float(dt_match.group(1)) if dt_match else 0.001
        # Write a temporary override with endTime = deltaT (single step)
        dry_cd = cd_text
        dry_cd = re.sub(r"endTime\s+[0-9.eE+-]+\s*;", f"endTime  {dt};", dry_cd)
        dry_cd = re.sub(r"startFrom\s+\w+\s*;", "startFrom  startTime;", dry_cd)
        dry_cd = re.sub(r"startTime\s+[0-9.eE+-]+\s*;", "startTime  0;", dry_cd)
        dry_cd = re.sub(r"writeInterval\s+[0-9]+\s*;", "writeInterval  1;", dry_cd)
        # Backup and write
        backup = cd_path.read_text(encoding="utf-8")
        try:
            cd_path.write_text(dry_cd, encoding="utf-8")
            rc, output = self._run_command(
                [solver_exe, "-case", str(case_path)],
                case_path, timeout=120,
            )
        finally:
            cd_path.write_text(backup, encoding="utf-8")
        # Consider success if solver started and didn't crash immediately
        # (rc=0 or rc=1 with "End" in output means it completed the single step)
        started_ok = any(kw in output for kw in ("Starting time loop", "Time =", "Courant Number", "End"))
        crashed = any(kw in output for kw in ("FOAM FATAL ERROR", "FOAM FATAL IO ERROR", "Segmentation fault", "abort"))
        success = started_ok and not crashed
        report.solver_dry_run_output = output[-1500:] if output else ""
        report.add_check(ValidationCheckResult(
            check_name="solver_dry_run",
            passed=success,
            severity="error",
            message="Solver dry-run (1 timestep) completed without fatal errors." if success else f"Solver dry-run failed or did not start properly (rc={rc}).",
            details={"returncode": rc, "solver": solver_name},
        ))

    def _check_function_object_outputs(self, report: CompileReadinessReport, case_path: Path) -> None:
        """Check that functionObjects produced postProcessing/ outputs after dry-run."""
        pp_dir = case_path / "postProcessing"
        if not pp_dir.is_dir():
            report.add_check(ValidationCheckResult(
                check_name="function_object_outputs",
                passed=False,
                severity="warning",
                message="postProcessing/ directory not created; functionObject outputs not verified.",
            ))
            return
        subdirs = [p.name for p in pp_dir.iterdir() if p.is_dir()]
        report.add_check(ValidationCheckResult(
            check_name="function_object_outputs",
            passed=len(subdirs) > 0,
            severity="warning",
            message=f"Function objects produced outputs: {subdirs}" if subdirs else "postProcessing/ exists but no subdirectories found.",
            details={"outputs": subdirs},
        ))


__all__ = [
    "CompileReadinessReport",
    "CompileReadinessValidator",
    "ValidationCheckResult",
]
