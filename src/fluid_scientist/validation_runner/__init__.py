"""OpenFOAM 13 validation runner.

This package provides the full validation pipeline for compiled OpenFOAM
13 cases, from static structure checks through to serial and parallel
smoke tests.

The pipeline is orchestrated by :class:`ValidationRunner`::

    COMPILED
      -> STATIC_VALIDATED
      -> DICTIONARY_VALIDATED
      -> MESH_BUILT
      -> MESH_VALIDATED
      -> SERIAL_SMOKE_TEST_PASSED
      -> PARALLEL_SMOKE_TEST_PASSED
      -> READY_TO_SUBMIT

Typical usage::

    from fluid_scientist.validation_runner import ValidationRunner

    runner = ValidationRunner()
    manifest = runner.run(compiled_case)
    if manifest.ready_to_submit:
        print("Case is ready for submission")
    else:
        for err in manifest.blocking_errors:
            print(f"  ERROR: {err}")
"""

from fluid_scientist.validation_runner.dictionary_validator import DictionaryValidator
from fluid_scientist.validation_runner.mesh_validator import MeshValidator
from fluid_scientist.validation_runner.runner import (
    StageResult,
    ValidationManifest,
    ValidationRunner,
    ValidationStage,
)
from fluid_scientist.validation_runner.smoke_test import (
    ParallelSmokeTest,
    SerialSmokeTest,
    SmokeTestConfig,
)
from fluid_scientist.validation_runner.static_validator import (
    OpenFOAMCaseStaticValidator,
    StaticValidator,
    ValidationResult,
)

__all__ = [
    "DictionaryValidator",
    "MeshValidator",
    "OpenFOAMCaseStaticValidator",
    "ParallelSmokeTest",
    "SerialSmokeTest",
    "SmokeTestConfig",
    "StageResult",
    "StaticValidator",
    "ValidationManifest",
    "ValidationResult",
    "ValidationRunner",
    "ValidationStage",
]
