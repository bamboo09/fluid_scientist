"""Capability requirement graph for the Case IR.

This module extracts capability requirements from a
:class:`~fluid_scientist.study_spec.models.SimulationStudySpec` dict and
tracks their resolution status against a set of available capabilities.

A *capability requirement* is an atomic statement of the form
``"geometry.triangle_2d"`` or ``"physics.turbulence.LES"`` that the
simulation case needs in order to be fully realised.  The
:class:`CapabilityRequirementGraph` collects all such requirements from
the spec, checks them against a known capability set, and provides
query helpers for missing / unknown requirements.

Typical usage::

    from fluid_scientist.case_ir.capability_requirements import (
        CapabilityRequirementGraph,
    )

    graph = CapabilityRequirementGraph()
    requirements = graph.build_from_spec(spec_dict)
    available = {"geometry.cylinder_2d", "solver.pimpleFoam", ...}
    checked = graph.check_requirements(requirements, available)
    missing = graph.get_missing(checked)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

__all__ = [
    "CapabilityRequirement",
    "CapabilityRequirementGraph",
    "RequirementStatus",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

RequirementStatus = Literal[
    "satisfied",
    "missing",
    "unknown",
    "extension_requested",
]

#: Materials considered "standard" and therefore not requiring a capability.
_STANDARD_MATERIALS: set[str] = {"air", "water"}


# ---------------------------------------------------------------------------
# CapabilityRequirement model
# ---------------------------------------------------------------------------


class CapabilityRequirement(BaseModel):
    """A single capability requirement extracted from the spec.

    Attributes:
        req_id: Unique identifier (e.g. ``"REQ-001"``).
        capability_key: Dotted capability key
            (e.g. ``"geometry.triangle_2d"``, ``"physics.turbulence.LES"``).
        required_by: What part of the spec requires this capability
            (e.g. ``"user_input"``, ``"physics_model"``, ``"geometry"``).
        status: Current resolution status.
        resolver: Name of the resolver that satisfied this requirement,
            or ``None``.
        resolved_artifact: Path or identifier of the artifact that
            satisfies this requirement, or ``None``.
    """

    req_id: str
    capability_key: str
    required_by: str
    status: RequirementStatus = "unknown"
    resolver: str | None = None
    resolved_artifact: str | None = None


# ---------------------------------------------------------------------------
# CapabilityRequirementGraph
# ---------------------------------------------------------------------------


class CapabilityRequirementGraph:
    """Builds and queries the capability requirement graph.

    The graph is constructed by scanning a
    :class:`~fluid_scientist.study_spec.models.SimulationStudySpec`
    dict and extracting every atomic capability key implied by the
    spec's geometry, physics, numerics, boundaries, observations, and
    material definitions.
    """

    def __init__(self) -> None:
        """Initialise an empty graph.

        Use :meth:`build_from_spec` to populate requirements from a
        spec dict.
        """
        self._requirements: list[CapabilityRequirement] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def requirements(self) -> list[CapabilityRequirement]:
        """Return a copy of the current requirement list."""
        return list(self._requirements)

    # ------------------------------------------------------------------
    # Build from spec
    # ------------------------------------------------------------------

    def build_from_spec(self, spec_dict: dict[str, Any]) -> list[CapabilityRequirement]:
        """Extract all capability requirements from a spec dict.

        Args:
            spec_dict: A serialised
                :class:`~fluid_scientist.study_spec.models.SimulationStudySpec`
                dict.

        Returns:
            A list of :class:`CapabilityRequirement` objects, each with
            ``status="unknown"``.
        """
        requirements: list[CapabilityRequirement] = []
        counter = 0

        def _next_id() -> str:
            nonlocal counter
            counter += 1
            return f"REQ-{counter:03d}"

        # --- Geometry entities ---
        geometry = spec_dict.get("geometry", {})
        entities = geometry.get("entities", {})
        if isinstance(entities, dict):
            for _entity_id, entity in entities.items():
                semantic_type = entity.get("semantic_type", "")
                if semantic_type:
                    requirements.append(CapabilityRequirement(
                        req_id=_next_id(),
                        capability_key=f"geometry.{semantic_type}",
                        required_by="user_input",
                    ))

        # --- Turbulence model ---
        numerics = spec_dict.get("numerics", {})
        turbulence_model = numerics.get("turbulence_model")
        if turbulence_model and turbulence_model != "laminar":
            requirements.append(CapabilityRequirement(
                req_id=_next_id(),
                capability_key=f"physics.turbulence.{turbulence_model}",
                required_by="physics_model",
            ))

        # --- Solver ---
        solver = numerics.get("solver", "")
        if solver:
            requirements.append(CapabilityRequirement(
                req_id=_next_id(),
                capability_key=f"solver.{solver}",
                required_by="numerics",
            ))

        # --- Boundary condition types ---
        boundaries = spec_dict.get("boundaries", {})
        conditions = boundaries.get("conditions", [])
        if isinstance(conditions, list):
            for condition in conditions:
                bc_type = condition.get("bc_type", "")
                if bc_type:
                    requirements.append(CapabilityRequirement(
                        req_id=_next_id(),
                        capability_key=f"boundary.{bc_type}",
                        required_by="boundary",
                    ))

        # --- Observation metrics ---
        observations = spec_dict.get("observations", {})
        targets = observations.get("targets", [])
        if isinstance(targets, list):
            for target in targets:
                metric = target.get("metric", "")
                if metric:
                    requirements.append(CapabilityRequirement(
                        req_id=_next_id(),
                        capability_key=f"observation.{metric}",
                        required_by="observation",
                    ))

        # --- Material ---
        physics = spec_dict.get("physics", {})
        material = physics.get("material", {})
        if isinstance(material, dict):
            material_name = material.get("value", "")
        else:
            material_name = str(material)
        if material_name and material_name.lower() not in _STANDARD_MATERIALS:
            requirements.append(CapabilityRequirement(
                req_id=_next_id(),
                capability_key=f"material.{material_name}",
                required_by="physics_model",
            ))

        self._requirements = list(requirements)
        return requirements

    # ------------------------------------------------------------------
    # Check requirements
    # ------------------------------------------------------------------

    def check_requirements(
        self,
        requirements: list[CapabilityRequirement],
        available_capabilities: set[str],
    ) -> list[CapabilityRequirement]:
        """Mark each requirement as satisfied or missing.

        A requirement is ``satisfied`` if its ``capability_key`` is
        present in *available_capabilities*; otherwise it is ``missing``.
        Requirements already marked ``extension_requested`` are left
        unchanged.

        Args:
            requirements: The list of requirements to check.
            available_capabilities: A set of known capability keys.

        Returns:
            A new list of requirements with updated statuses.  The
            original list is not modified.
        """
        checked: list[CapabilityRequirement] = []
        for req in requirements:
            if req.status == "extension_requested":
                checked.append(req)
                continue
            if req.capability_key in available_capabilities:
                checked.append(req.model_copy(update={"status": "satisfied"}))
            else:
                checked.append(req.model_copy(update={"status": "missing"}))
        return checked

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_missing(
        self, requirements: list[CapabilityRequirement]
    ) -> list[CapabilityRequirement]:
        """Return only the requirements with ``status="missing"``."""
        return [r for r in requirements if r.status == "missing"]

    def get_unknown(
        self, requirements: list[CapabilityRequirement]
    ) -> list[CapabilityRequirement]:
        """Return only the requirements with ``status="unknown"``."""
        return [r for r in requirements if r.status == "unknown"]

    def get_satisfied(
        self, requirements: list[CapabilityRequirement]
    ) -> list[CapabilityRequirement]:
        """Return only the requirements with ``status="satisfied"``."""
        return [r for r in requirements if r.status == "satisfied"]
