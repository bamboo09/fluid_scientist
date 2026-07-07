"""Core data models for the CodeExtensionSpec system."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class CodeExtensionType(str, Enum):
    """Type of code extension."""

    FUNCTION_OBJECT = "function_object"
    BOUNDARY_CONDITION = "boundary_condition"
    POST_PROCESSING = "post_processing"
    SOLVER_PLUGIN = "solver_plugin"
    UTILITY = "utility"


class ExtensionStatus(str, Enum):
    """Lifecycle status of a code extension."""

    DRAFT = "draft"
    SANDBOX_TESTED = "sandbox_tested"
    AUTO_TESTED = "auto_tested"
    APPROVED = "approved"
    REGISTERED = "registered"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class TestSpec(StrictModel):
    """Specification for a single test case.

    Attributes:
        test_id: Unique identifier.
        test_name: Human-readable name.
        test_code: Python test code to execute.
        expected_behavior: Description of expected behavior.
        timeout_seconds: Maximum execution time.
    """

    test_id: str = Field(min_length=1, max_length=128)
    test_name: str = Field(min_length=1, max_length=200)
    test_code: str = Field(min_length=1, max_length=10000)
    expected_behavior: str = Field(default="", max_length=1000)
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)


class TestResult(StrictModel):
    """Result of running a single test.

    Attributes:
        test_id: The test identifier.
        passed: Whether the test passed.
        error_message: Error message if failed.
        execution_time_s: Execution time in seconds.
        stdout: Captured stdout.
        stderr: Captured stderr.
    """

    test_id: str = Field(min_length=1, max_length=128)
    passed: bool
    error_message: str = Field(default="", max_length=2000)
    execution_time_s: float = Field(default=0.0, ge=0)
    stdout: str = Field(default="", max_length=5000)
    stderr: str = Field(default="", max_length=5000)


class CodeExtensionSpec(StrictModel):
    """Complete specification for a code extension.

    Attributes:
        extension_id: Unique identifier.
        name: Human-readable name.
        extension_type: Type of extension.
        description: Detailed description.
        code: The code to be executed (Python or OpenFOAM C++).
        language: Programming language ("python" or "cpp").
        dependencies: List of required dependencies.
        openfoam_files: OpenFOAM dict files this extension modifies.
        tests: Test specifications for this extension.
        status: Current lifecycle status.
        version: Semantic version string.
        author: Author identifier.
        review_notes: Notes from the approval process.
        created_at: ISO timestamp.
        updated_at: ISO timestamp.
    """

    extension_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    extension_type: CodeExtensionType
    description: str = Field(default="", max_length=2000)
    code: str = Field(min_length=1, max_length=50000)
    language: str = Field(default="python", max_length=20)
    dependencies: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    openfoam_files: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    tests: tuple[TestSpec, ...] = Field(default_factory=tuple, max_length=50)
    status: ExtensionStatus = ExtensionStatus.DRAFT
    version: str = Field(default="1.0.0", max_length=20)
    author: str = Field(default="system", max_length=128)
    review_notes: str = Field(default="", max_length=5000)
    created_at: str = Field(default="", max_length=100)
    updated_at: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def validate_extension(self) -> CodeExtensionSpec:
        if self.language not in ("python", "cpp"):
            raise ValueError(
                f"language must be 'python' or 'cpp', got '{self.language}'"
            )
        # Check for dangerous patterns in code
        dangerous_patterns = [
            "import subprocess",
            "import os.system",
            "import shutil",
            "__import__",
            "eval(",
            "exec(",
            "open('/",
            "open(\"/",
        ]
        code_lower = self.code.lower()
        for pattern in dangerous_patterns:
            if pattern.lower() in code_lower:
                raise ValueError(
                    f"code contains dangerous pattern: '{pattern}'"
                )
        return self

    def can_transition_to(self, new_status: ExtensionStatus) -> bool:
        """Check if a status transition is valid."""
        allowed: dict[ExtensionStatus, frozenset[ExtensionStatus]] = {
            ExtensionStatus.DRAFT: frozenset({
                ExtensionStatus.SANDBOX_TESTED,
                ExtensionStatus.REJECTED,
            }),
            ExtensionStatus.SANDBOX_TESTED: frozenset({
                ExtensionStatus.AUTO_TESTED,
                ExtensionStatus.REJECTED,
            }),
            ExtensionStatus.AUTO_TESTED: frozenset({
                ExtensionStatus.APPROVED,
                ExtensionStatus.REJECTED,
            }),
            ExtensionStatus.APPROVED: frozenset({
                ExtensionStatus.REGISTERED,
                ExtensionStatus.REJECTED,
            }),
            ExtensionStatus.REGISTERED: frozenset({
                ExtensionStatus.DEPRECATED,
                ExtensionStatus.ROLLED_BACK,
            }),
            ExtensionStatus.DEPRECATED: frozenset({
                ExtensionStatus.ROLLED_BACK,
            }),
            ExtensionStatus.REJECTED: frozenset({
                ExtensionStatus.DRAFT,  # Can revise and resubmit
            }),
            ExtensionStatus.ROLLED_BACK: frozenset({
                ExtensionStatus.DRAFT,  # Can revise and resubmit
            }),
        }
        return new_status in allowed.get(self.status, frozenset())


__all__ = [
    "CodeExtensionSpec",
    "CodeExtensionType",
    "ExtensionStatus",
    "StrictModel",
    "TestResult",
    "TestSpec",
]
