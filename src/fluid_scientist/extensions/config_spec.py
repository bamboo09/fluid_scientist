"""ConfigExtensionSpec.

For new dictionary mappings, boundary combinations, function objects,
parameter schemas, and template components.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConfigExtensionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_id: str
    description: str
    extension_type: Literal["config"] = "config"
    target_capability_type: str  # boundary_writer, function_object_generator, etc.
    semantic_role: str  # e.g. "helical_pulsating_inlet"
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    foundation13_mapping: dict[str, str] = Field(default_factory=dict)  # OpenFOAM dict entries
    dependencies: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    validation_method: str = "static"  # static, smoke_test, benchmark
    fallback_behavior: str = "reject"  # reject, use_default, skip


__all__ = ["ConfigExtensionSpec"]
