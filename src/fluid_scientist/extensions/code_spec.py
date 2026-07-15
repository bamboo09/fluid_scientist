"""CodeExtensionSpec.

For Python preprocessors, geometry generators, mesh generators,
post-processing algorithms, and OpenFOAM C++ extensions.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test_id: str
    test_type: Literal["unit", "integration", "benchmark"]
    description: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    tolerance: float = 0.01


class CodeExtensionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_id: str
    description: str
    extension_type: Literal["code"] = "code"
    target_capability_type: str
    language: Literal["python", "cpp"] = "python"
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    security_constraints: list[str] = Field(default_factory=list)
    fallback_behavior: str = "reject"
    unit_tests: list[TestSpec] = Field(default_factory=list)
    benchmark_tests: list[TestSpec] = Field(default_factory=list)
    target_case_tests: list[TestSpec] = Field(default_factory=list)
    implementation_code: str = ""  # actual code to execute
    implementation_entrypoint: str = ""  # function name to call


__all__ = ["CodeExtensionSpec", "TestSpec"]
