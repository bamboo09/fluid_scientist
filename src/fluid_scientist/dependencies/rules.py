"""Dependency rules for the CFD simulation spec.

This module defines :class:`DependencyRule` — a declarative description of
how one spec path (or a set of paths) gives rise to another — and a
:class:`RuleRegistry` that indexes the pre-built CFD dependency rules so
they can be looked up by source path, target path, or rule id.

Each rule captures one *causal* relationship in the simulation pipeline,
for example::

    Reynolds number = velocity * characteristic_length / kinematic_viscosity

The rules are intentionally pure data (no computation); the actual numeric
evaluation lives in :mod:`fluid_scientist.dependencies.derived_values`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DependencyRule", "RuleRegistry", "RuleType"]

#: The four causal relationships the engine understands.
RuleType = Literal["derive", "constrain", "invalidate", "recompile"]


class DependencyRule(BaseModel):
    """A single dependency rule describing how paths relate.

    Parameters
    ----------
    rule_id:
        Unique identifier for the rule (e.g. ``"reynolds_from_UDnu"``).
    source_paths:
        List of JSON-pointer paths that the rule reads from.
    target_path:
        The JSON-pointer path that the rule produces or constrains.
    formula:
        Optional human-readable formula string, e.g. ``"Re = U * D / nu"``.
        ``None`` for purely structural rules (e.g. geometry -> mesh).
    description:
        Human-readable explanation of what the rule does.
    rule_type:
        One of ``"derive"``, ``"constrain"``, ``"invalidate"``,
        ``"recompile"``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    source_paths: list[str] = Field(default_factory=list)
    target_path: str
    formula: str | None = None
    description: str
    rule_type: RuleType


# ---------------------------------------------------------------------------
# Pre-built CFD dependency rules
# ---------------------------------------------------------------------------

#: Canonical list of CFD dependency rules.
_DEFAULT_RULES: list[DependencyRule] = [
    # --- Physics derivations ---
    DependencyRule(
        rule_id="reynolds_from_UDnu",
        source_paths=[
            "/physics/velocity",
            "/physics/characteristic_length",
            "/physics/kinematic_viscosity",
        ],
        target_path="/physics/reynolds_number",
        formula="Re = U * D / nu",
        description=(
            "Reynolds number derived from velocity, characteristic length, "
            "and kinematic viscosity."
        ),
        rule_type="derive",
    ),
    DependencyRule(
        rule_id="viscosity_from_rho_nu",
        source_paths=["/physics/density", "/physics/kinematic_viscosity"],
        target_path="/physics/dynamic_viscosity",
        formula="mu = rho * nu",
        description=(
            "Dynamic viscosity derived from density and kinematic "
            "viscosity."
        ),
        rule_type="derive",
    ),
    DependencyRule(
        rule_id="material_to_density",
        source_paths=["/physics/material"],
        target_path="/physics/density",
        formula="rho = material_property(material, 'density')",
        description="Density derived from the material identifier.",
        rule_type="derive",
    ),
    DependencyRule(
        rule_id="material_to_viscosity",
        source_paths=["/physics/material"],
        target_path="/physics/kinematic_viscosity",
        formula="nu = material_property(material, 'kinematic_viscosity')",
        description="Kinematic viscosity derived from the material identifier.",
        rule_type="derive",
    ),
    DependencyRule(
        rule_id="material_to_dynamic_viscosity",
        source_paths=["/physics/material"],
        target_path="/physics/dynamic_viscosity",
        formula="mu = material_property(material, 'dynamic_viscosity')",
        description="Dynamic viscosity derived from the material identifier.",
        rule_type="derive",
    ),
    # --- Time / numerics derivations ---
    DependencyRule(
        rule_id="duration_from_start_end",
        source_paths=[
            "/numerics/time/start_time",
            "/numerics/time/end_time",
        ],
        target_path="/numerics/time/duration",
        formula="duration = end_time - start_time",
        description="Simulation duration derived from start and end time.",
        rule_type="derive",
    ),
    DependencyRule(
        rule_id="output_count_from_end_write",
        source_paths=[
            "/numerics/time/end_time",
            "/numerics/time/write_interval",
        ],
        target_path="/numerics/time/expected_output_count",
        formula="count = floor(end_time / write_interval)",
        description=(
            "Expected output count derived from end time and write "
            "interval."
        ),
        rule_type="derive",
    ),
    DependencyRule(
        rule_id="courant_from_dt_U_dx",
        source_paths=[
            "/numerics/time/delta_t",
            "/physics/velocity",
            "/mesh/resolution",
        ],
        target_path="/numerics/time/courant_number",
        formula="Co = U * delta_t / dx",
        description=(
            "Courant number estimate from time step, velocity, and cell "
            "size."
        ),
        rule_type="derive",
    ),
    # --- Structural constraints ---
    DependencyRule(
        rule_id="geometry_to_mesh",
        source_paths=["/geometry"],
        target_path="/mesh",
        formula=None,
        description="Geometry constrains the mesh topology and resolution.",
        rule_type="constrain",
    ),
    DependencyRule(
        rule_id="mesh_to_boundaries",
        source_paths=["/mesh"],
        target_path="/boundaries",
        formula=None,
        description="Mesh patches determine the boundary field mapping.",
        rule_type="constrain",
    ),
    # --- Objective -> function object ---
    DependencyRule(
        rule_id="objective_cd_cl_to_forceCoeffs",
        source_paths=["/observations/targets"],
        target_path="/observations/function_objects/forceCoeffs",
        formula=None,
        description="Cd/Cl objectives require the forceCoeffs function object.",
        rule_type="constrain",
    ),
    DependencyRule(
        rule_id="objective_point_velocity_to_probes",
        source_paths=["/observations/targets"],
        target_path="/observations/function_objects/probes",
        formula=None,
        description="Point-velocity objective requires the probes function object.",
        rule_type="constrain",
    ),
    DependencyRule(
        rule_id="objective_section_mean_to_surfaceFieldValue",
        source_paths=["/observations/targets"],
        target_path="/observations/function_objects/surfaceFieldValue",
        formula=None,
        description=(
            "Section-mean objective requires the surfaceFieldValue function "
            "object."
        ),
        rule_type="constrain",
    ),
    DependencyRule(
        rule_id="statistics_to_fieldAverage",
        source_paths=["/numerics/time/statistics_windows"],
        target_path="/observations/function_objects/fieldAverage",
        formula=None,
        description=(
            "Last-5s statistics window sets the fieldAverage start time."
        ),
        rule_type="constrain",
    ),
]


class RuleRegistry:
    """Registry of all known CFD dependency rules.

    The registry is populated with :data:`_DEFAULT_RULES` at construction
    time and indexes them by source path, target path, and rule id for
    O(1) lookup.
    """

    def __init__(self) -> None:
        self._rules: list[DependencyRule] = list(_DEFAULT_RULES)
        self._by_source: dict[str, list[DependencyRule]] = {}
        self._by_target: dict[str, list[DependencyRule]] = {}
        self._by_id: dict[str, DependencyRule] = {}
        self._reindex()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reindex(self) -> None:
        """Rebuild the lookup indexes from ``self._rules``."""
        self._by_source.clear()
        self._by_target.clear()
        self._by_id.clear()
        for rule in self._rules:
            self._by_id[rule.rule_id] = rule
            for src in rule.source_paths:
                self._by_source.setdefault(src, []).append(rule)
            self._by_target.setdefault(rule.target_path, []).append(rule)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, rule: DependencyRule) -> None:
        """Register an additional rule at runtime."""
        self._rules.append(rule)
        self._reindex()

    def get_rules_for_source(self, path: str) -> list[DependencyRule]:
        """Return all rules where *path* appears in ``source_paths``."""
        return list(self._by_source.get(path, []))

    def get_rules_for_target(self, path: str) -> list[DependencyRule]:
        """Return all rules where *path* is the ``target_path``."""
        return list(self._by_target.get(path, []))

    def list_all_rules(self) -> list[DependencyRule]:
        """Return a copy of every registered rule."""
        return list(self._rules)

    def get_rule(self, rule_id: str) -> DependencyRule | None:
        """Return the rule with *rule_id*, or ``None`` if not found."""
        return self._by_id.get(rule_id)
