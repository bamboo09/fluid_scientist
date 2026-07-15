"""PhysicsExtensionSpec.

For new solver modules, equations, phase states, material models,
multi-region coupling, energy equations, porous media, and conjugate
heat transfer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConservationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: str
    quantity: str  # mass, momentum, energy
    method: str  # integral, flux_balance, etc.
    tolerance: float = 0.01


class PhysicsExtensionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_id: str
    description: str
    extension_type: Literal["physics"] = "physics"
    physical_scope: str  # e.g. "thermal_fluid", "multiphase", "porous_media"
    governing_equations: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    solver_module: str = ""  # e.g. "isothermalFluid" for thermal
    boundary_requirements: list[str] = Field(default_factory=list)
    validation_benchmark: str = ""  # benchmark case name
    conservation_checks: list[ConservationCheck] = Field(default_factory=list)
    applicability_limits: dict[str, Any] = Field(default_factory=dict)
    required_base_pack: str = ""  # which BasePack to extend
    new_constant_files: list[str] = Field(default_factory=list)  # e.g. thermophysicalProperties
    new_field_files: list[str] = Field(default_factory=list)  # e.g. "0/T" for temperature


__all__ = ["ConservationCheck", "PhysicsExtensionSpec"]
