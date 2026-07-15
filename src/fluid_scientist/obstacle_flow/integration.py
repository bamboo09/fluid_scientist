"""Integration bridge between obstacle_flow and the existing compilation framework.

This module provides adapter functions that allow ObstacleFlowExperimentSpecV1
to be compiled and validated through the existing pipeline.

The bridge uses ObstacleFlowCompiledCase directly (rather than forcing it
into CompiledCase), and optionally validates the archive using the existing
validate_custom_case_archive security validator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fluid_scientist.obstacle_flow.compiler import (
    CompilationError,
    CompilationManifest as ObstacleFlowManifest,
    ObstacleFlowCompiledCase,
    ObstacleFlowCompiler,
)
from fluid_scientist.obstacle_flow.models import ObstacleFlowExperimentSpecV1
from fluid_scientist.obstacle_flow.static_validator import (
    ObstacleFlowStaticValidator,
    StaticValidationResult,
)


@dataclass(frozen=True)
class ObstacleFlowCompilationResult:
    """Complete result of obstacle flow compilation.

    Contains the compiled case, manifest, and static validation result.
    This is the primary output of the compilation pipeline.
    """

    compiled: ObstacleFlowCompiledCase
    manifest: ObstacleFlowManifest
    static_validation: StaticValidationResult

    @property
    def archive(self) -> bytes:
        """The tar.gz archive bytes."""
        return self.compiled.archive

    @property
    def archive_sha256(self) -> str:
        """The SHA256 hash of the archive."""
        return self.compiled.archive_sha256

    @property
    def files(self) -> dict[str, str]:
        """The generated OpenFOAM case files."""
        return self.compiled.files

    @property
    def passed_static_validation(self) -> bool:
        """Whether the case passed static validation."""
        return self.static_validation.passed

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for API transport."""
        return {
            "compiled": {
                "experiment_type": self.compiled.experiment_type,
                "spec_version": self.compiled.spec_version,
                "preprocessing": list(self.compiled.preprocessing),
                "required_outputs": list(self.compiled.required_outputs),
                "archive_size": len(self.compiled.archive),
                "archive_sha256": self.compiled.archive_sha256,
                "files": sorted(self.compiled.files.keys()),
            },
            "manifest": {
                "compilation_id": self.manifest.compilation_id,
                "spec_version": self.manifest.spec_version,
                "spec_hash": self.manifest.spec_hash,
                "case_hash": self.manifest.case_hash,
                "generated_files": self.manifest.generated_files,
                "compiler_id": self.manifest.compiler_id,
                "compiler_version": self.manifest.compiler_version,
                "flow_mode": self.manifest.flow_mode,
                "has_cylinder": self.manifest.has_cylinder,
                "has_bump": self.manifest.has_bump,
            },
            "static_validation": {
                "passed": self.static_validation.passed,
                "errors": self.static_validation.errors,
                "warnings": self.static_validation.warnings,
            },
        }


def compile_obstacle_flow_spec(
    spec: ObstacleFlowExperimentSpecV1,
) -> ObstacleFlowCompilationResult:
    """Compile an ObstacleFlowExperimentSpecV1 into a complete case.

    This is the main integration entry point.  It:
    1. Compiles the spec using ObstacleFlowCompiler
    2. Runs the ObstacleFlowStaticValidator
    3. Returns the complete compilation result

    The archive bytes in the result can be submitted to:
    - The existing validate_custom_case_archive() for security validation
    - The existing ValidationRunner for mesh and solver validation
    - The workstation for formal computation

    Args:
        spec: The obstacle flow experiment specification.

    Returns:
        ObstacleFlowCompilationResult containing all compilation artifacts.

    Raises:
        CompilationError: If the spec cannot be compiled.
    """
    compiler = ObstacleFlowCompiler()
    compiled, manifest = compiler.compile(spec)

    # Run static validation
    static_validator = ObstacleFlowStaticValidator()
    static_result = static_validator.validate(spec, compiled.files)

    return ObstacleFlowCompilationResult(
        compiled=compiled,
        manifest=manifest,
        static_validation=static_result,
    )


def validate_archive_security(archive: bytes) -> dict[str, Any]:
    """Validate the archive using the existing security validator.

    This is an optional step that uses the existing
    validate_custom_case_archive() function to perform security
    checks on the generated archive.

    Args:
        archive: The tar.gz archive bytes.

    Returns:
        Dictionary with validation results, or error information.
    """
    try:
        from fluid_scientist.adapters.custom_openfoam import (
            validate_custom_case_archive,
        )
        manifest = validate_custom_case_archive(archive)
        return {
            "passed": True,
            "archive_sha256": manifest.archive_sha256,
            "solver": manifest.solver,
            "members": list(manifest.members),
            "has_mesh": manifest.has_mesh,
            "needs_block_mesh": manifest.needs_block_mesh,
            "uncompressed_bytes": manifest.uncompressed_bytes,
        }
    except Exception as e:
        return {
            "passed": False,
            "error": str(e),
        }


__all__ = [
    "ObstacleFlowCompilationResult",
    "compile_obstacle_flow_spec",
    "validate_archive_security",
]
