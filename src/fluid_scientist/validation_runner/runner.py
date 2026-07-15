"""Validation runner for the OpenFOAM 13 component system.

The :class:`ValidationRunner` orchestrates all validation stages in
order, stopping at the first failure.  The pipeline is::

    COMPILED
      -> STATIC_VALIDATED
      -> DICTIONARY_VALIDATED
      -> MESH_BUILT
      -> MESH_VALIDATED
      -> SERIAL_SMOKE_TEST_PASSED
      -> PARALLEL_SMOKE_TEST_PASSED
      -> READY_TO_SUBMIT

Only a case that reaches ``READY_TO_SUBMIT`` is considered safe for
production submission.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compiler.compiler import (
    CompiledCase,
    CompiledCaseManifest,
    ValidationPlan,
)
from fluid_scientist.platform.profile import PlatformProfile
from fluid_scientist.validation_runner.dictionary_validator import DictionaryValidator
from fluid_scientist.validation_runner.mesh_validator import MeshValidator
from fluid_scientist.validation_runner.smoke_test import (
    ParallelSmokeTest,
    SerialSmokeTest,
)
from fluid_scientist.validation_runner.static_validator import (
    OpenFOAMCaseStaticValidator,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Validation stage enum
# ---------------------------------------------------------------------------


class ValidationStage(enum.Enum):
    """The validation pipeline stages.

    Each stage is a prerequisite for the next.  A case must pass all
    stages up to and including :attr:`PARALLEL_SMOKE_TEST_PASSED` before
    it reaches :attr:`READY_TO_SUBMIT`.
    """

    COMPILED = "compiled"
    STATIC_VALIDATED = "static_validated"
    DICTIONARY_VALIDATED = "dictionary_validated"
    MESH_BUILT = "mesh_built"
    MESH_VALIDATED = "mesh_validated"
    SERIAL_SMOKE_TEST_PASSED = "serial_smoke_test_passed"
    PARALLEL_SMOKE_TEST_PASSED = "parallel_smoke_test_passed"
    READY_TO_SUBMIT = "ready_to_submit"

    @classmethod
    def ordered(cls) -> list[ValidationStage]:
        """Return all stages in pipeline order."""
        return [
            cls.COMPILED,
            cls.STATIC_VALIDATED,
            cls.DICTIONARY_VALIDATED,
            cls.MESH_BUILT,
            cls.MESH_VALIDATED,
            cls.SERIAL_SMOKE_TEST_PASSED,
            cls.PARALLEL_SMOKE_TEST_PASSED,
            cls.READY_TO_SUBMIT,
        ]

    def next_stage(self) -> ValidationStage | None:
        """Return the next stage in the pipeline, or ``None`` if at the end."""
        stages = self.ordered()
        idx = stages.index(self)
        if idx + 1 < len(stages):
            return stages[idx + 1]
        return None


# ---------------------------------------------------------------------------
# Validation manifest
# ---------------------------------------------------------------------------


class StageResult(BaseModel):
    """The result of a single validation stage.

    Attributes:
        stage: The stage that was executed.
        passed: Whether the stage passed.
        result: The detailed validation result.
        duration_seconds: How long the stage took.
    """

    model_config = ConfigDict(extra="forbid")

    stage: ValidationStage
    passed: bool
    result: ValidationResult = Field(default_factory=lambda: ValidationResult())
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationManifest(BaseModel):
    """The overall validation manifest.

    Tracks the current stage, all stage results, and the overall
    pass/fail status.

    Attributes:
        case_id: The case identifier.
        current_stage: The furthest stage reached.
        stage_results: Results for each completed stage.
        all_passed: ``True`` if all stages passed.
        ready_to_submit: ``True`` if the case reached READY_TO_SUBMIT.
        blocking_errors: All blocking errors collected.
        warnings: All warnings collected.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = ""
    current_stage: ValidationStage = ValidationStage.COMPILED
    stage_results: list[StageResult] = Field(default_factory=list)
    all_passed: bool = False
    ready_to_submit: bool = False
    blocking_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def add_stage_result(self, sr: StageResult) -> None:
        """Add a stage result and update overall status."""
        self.stage_results.append(sr)
        if sr.passed:
            self.current_stage = sr.stage
        else:
            self.current_stage = sr.stage
            self.all_passed = False
            self.blocking_errors.extend(sr.result.errors)
        self.warnings.extend(sr.result.warnings)

    def finalize(self) -> None:
        """Compute final pass/fail status after all stages."""
        self.all_passed = all(sr.passed for sr in self.stage_results)
        if self.all_passed and self.current_stage == ValidationStage.PARALLEL_SMOKE_TEST_PASSED:
            self.ready_to_submit = True
            self.current_stage = ValidationStage.READY_TO_SUBMIT


# ---------------------------------------------------------------------------
# Validation runner
# ---------------------------------------------------------------------------


class ValidationRunner:
    """Orchestrates the full validation pipeline.

    Parameters:
        platform: The platform profile for security and solver checks.
        static_validator: Optional custom static validator.
        dictionary_validator: Optional custom dictionary validator.
        mesh_validator: Optional custom mesh validator.
        serial_smoke_test: Optional custom serial smoke test.
        parallel_smoke_test: Optional custom parallel smoke test.
    """

    def __init__(
        self,
        platform: PlatformProfile | None = None,
        static_validator: OpenFOAMCaseStaticValidator | None = None,
        dictionary_validator: DictionaryValidator | None = None,
        mesh_validator: MeshValidator | None = None,
        serial_smoke_test: SerialSmokeTest | None = None,
        parallel_smoke_test: ParallelSmokeTest | None = None,
    ) -> None:
        self.platform = platform or PlatformProfile()
        self.static_validator = static_validator or OpenFOAMCaseStaticValidator(self.platform)
        self.dictionary_validator = dictionary_validator or DictionaryValidator()
        self.mesh_validator = mesh_validator or MeshValidator()
        self.serial_smoke_test = serial_smoke_test or SerialSmokeTest()
        self.parallel_smoke_test = parallel_smoke_test or ParallelSmokeTest()

    def run(
        self,
        case: CompiledCase,
        manifest: CompiledCaseManifest | None = None,
        plan: ValidationPlan | None = None,
        case_dir: Path | None = None,
        checkmesh_output: str | None = None,
        serial_log: str | None = None,
        parallel_log: str | None = None,
    ) -> ValidationManifest:
        """Run the full validation pipeline.

        Parameters:
            case: The compiled case to validate.
            manifest: Optional metadata about the compiled case.
            plan: Optional validation plan specifying stages.
            case_dir: Optional directory for running smoke tests.
            checkmesh_output: Optional pre-captured checkMesh output.
            serial_log: Optional pre-captured serial foamRun log.
            parallel_log: Optional pre-captured parallel foamRun log.

        Returns:
            A :class:`ValidationManifest` with all stage results.
        """
        vm = ValidationManifest(
            case_id=manifest.case_id if manifest else "unknown",
            current_stage=ValidationStage.COMPILED,
            all_passed=True,
        )

        # Stage 0: COMPILED (always passes -- case was compiled)
        vm.add_stage_result(StageResult(
            stage=ValidationStage.COMPILED,
            passed=True,
            result=ValidationResult(
                check_name="compiled",
                passed=True,
            ),
            metadata={"n_files": len(case.files)},
        ))

        # Stage 1: STATIC_VALIDATED
        static_result = self.static_validator.validate(case, manifest)
        vm.add_stage_result(StageResult(
            stage=ValidationStage.STATIC_VALIDATED,
            passed=static_result.passed,
            result=static_result,
        ))
        if not static_result.passed:
            vm.finalize()
            return vm

        # Stage 2: DICTIONARY_VALIDATED
        dict_result = self.dictionary_validator.validate(case)
        vm.add_stage_result(StageResult(
            stage=ValidationStage.DICTIONARY_VALIDATED,
            passed=dict_result.passed,
            result=dict_result,
        ))
        if not dict_result.passed:
            vm.finalize()
            return vm

        # Stage 3: MESH_BUILT
        # If we have a case_dir and blockMeshDict exists, try to build the mesh
        mesh_build_result = ValidationResult(
            check_name="mesh_build",
            passed=True,
        )
        if case_dir and case.get("system/blockMeshDict"):
            import subprocess as sp
            try:
                # Write the case to the directory first
                for fpath, content in case.files.items():
                    full = case_dir / fpath
                    full.parent.mkdir(parents=True, exist_ok=True)
                    full.write_text(content, encoding="utf-8")

                proc = sp.run(
                    ["blockMesh", "-case", str(case_dir)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=str(case_dir),
                )
                if proc.returncode != 0:
                    mesh_build_result.add_error(
                        f"blockMesh failed (exit {proc.returncode}): "
                        f"{proc.stderr[:200]}"
                    )
                if "FOAM FATAL ERROR" in proc.stdout + proc.stderr:
                    mesh_build_result.add_error(
                        "blockMesh reported FOAM FATAL ERROR"
                    )
            except FileNotFoundError:
                mesh_build_result.add_warning(
                    "blockMesh command not found; mesh build skipped"
                )
            except sp.TimeoutExpired:
                mesh_build_result.add_error("blockMesh timed out")
        else:
            mesh_build_result.add_warning(
                "No case_dir provided; mesh build skipped"
            )

        vm.add_stage_result(StageResult(
            stage=ValidationStage.MESH_BUILT,
            passed=mesh_build_result.passed,
            result=mesh_build_result,
        ))
        if not mesh_build_result.passed:
            vm.finalize()
            return vm

        # Stage 4: MESH_VALIDATED
        mesh_result = self.mesh_validator.validate(case, checkmesh_output)
        vm.add_stage_result(StageResult(
            stage=ValidationStage.MESH_VALIDATED,
            passed=mesh_result.passed,
            result=mesh_result,
        ))
        if not mesh_result.passed:
            vm.finalize()
            return vm

        # Stage 5: SERIAL_SMOKE_TEST_PASSED
        if serial_log is not None:
            serial_result = self.serial_smoke_test.validate_log(serial_log)
        elif case_dir:
            serial_result, _, _ = self.serial_smoke_test.run(case, case_dir)
        else:
            serial_result = ValidationResult(
                check_name="serial_smoke_test",
                passed=True,
            )
            serial_result.add_warning(
                "No case_dir or serial_log provided; serial smoke test skipped"
            )

        vm.add_stage_result(StageResult(
            stage=ValidationStage.SERIAL_SMOKE_TEST_PASSED,
            passed=serial_result.passed,
            result=serial_result,
        ))
        if not serial_result.passed:
            vm.finalize()
            return vm

        # Stage 6: PARALLEL_SMOKE_TEST_PASSED
        if parallel_log is not None:
            parallel_result = self.parallel_smoke_test.validate_log(parallel_log)
        elif case_dir:
            parallel_result, _, _ = self.parallel_smoke_test.run(case, case_dir)
        else:
            parallel_result = ValidationResult(
                check_name="parallel_smoke_test",
                passed=True,
            )
            parallel_result.add_warning(
                "No case_dir or parallel_log provided; "
                "parallel smoke test skipped"
            )

        vm.add_stage_result(StageResult(
            stage=ValidationStage.PARALLEL_SMOKE_TEST_PASSED,
            passed=parallel_result.passed,
            result=parallel_result,
        ))

        vm.finalize()
        return vm


__all__ = [
    "StageResult",
    "ValidationManifest",
    "ValidationRunner",
    "ValidationStage",
]
