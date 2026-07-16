"""JSON Schema export and path registry for the SimulationStudySpec.

This module provides a :class:`SchemaExporter` that can produce:

* The full JSON Schema for :class:`SimulationStudySpec` (via Pydantic's
  ``model_json_schema()``).
* A placeholder JSON Schema for the forward-referenced
  ``SimulationSpecPatch`` type.
* A **path registry** mapping JSON Pointer paths to path-level metadata
  (mutability, risk level, unit dimension, dependency tags, …).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .models import SimulationStudySpec

__all__ = ["SchemaExporter"]


class SchemaExporter:
    """Export JSON Schemas and a path registry for the study spec.

    The path registry is the key artifact consumed by the spec-patching
    layer: it tells the patcher which paths are mutable, which carry high
    risk, and which units/dimensions they belong to.
    """

    def __init__(self) -> None:
        self._model_cls = SimulationStudySpec

    # ------------------------------------------------------------------
    # Schema export
    # ------------------------------------------------------------------

    def export_schema(self) -> dict[str, Any]:
        """Return the JSON Schema for :class:`SimulationStudySpec`."""
        schema = self._model_cls.model_json_schema()
        # Ensure top-level $schema is present for discoverability.
        schema.setdefault("$schema", "http://json-schema.org/draft-07/schema#")
        return schema

    def export_patch_schema(self) -> dict[str, Any]:
        """Return a JSON Schema for the ``SimulationSpecPatch`` type.

        The patch type is a forward reference (it is defined in a separate
        patching layer).  This method returns a placeholder schema that
        documents the expected patch structure so that consumers can
        validate patches before the full patch model is available.
        """
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "SimulationSpecPatch",
            "description": (
                "Placeholder schema for the SimulationSpecPatch type. "
                "A patch describes a set of operations (set, multiply, "
                "append, remove) applied to specific JSON Pointer paths "
                "within a SimulationStudySpec."
            ),
            "type": "object",
            "required": ["patch_id", "operations"],
            "properties": {
                "patch_id": {
                    "type": "string",
                    "description": "Unique identifier for this patch.",
                },
                "turn_id": {
                    "type": ["string", "null"],
                    "description": "The conversation turn that produced this patch.",
                },
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["op", "path"],
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": [
                                    "set",
                                    "multiply",
                                    "divide",
                                    "add",
                                    "subtract",
                                    "append",
                                    "remove",
                                    "replace",
                                ],
                                "description": "The patch operation.",
                            },
                            "path": {
                                "type": "string",
                                "description": (
                                    "JSON Pointer path to the target field."
                                ),
                            },
                            "value": {
                                "description": (
                                    "The value to set / append (type depends "
                                    "on the target field)."
                                ),
                            },
                            "factor": {
                                "type": "number",
                                "description": "Multiplicative factor for "
                                "'multiply' / 'divide' operations.",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Human-readable reason for "
                                "the operation.",
                            },
                        },
                    },
                },
            },
        }

    # ------------------------------------------------------------------
    # Path registry
    # ------------------------------------------------------------------

    def get_path_registry(self) -> dict[str, dict[str, Any]]:
        """Return a dict mapping JSON Pointer paths to path metadata.

        Each entry has the following keys:

        ``json_pointer``
            The JSON Pointer string.
        ``value_schema``
            A compact dict describing the value type
            (``{"type": "number"}``, ``{"type": "string"}``, …).
        ``required``
            Whether the path is required (must exist in a valid spec).
        ``mutable``
            Whether the path can be changed by a patch at runtime.
        ``risk_level``
            One of ``"low"``, ``"medium"``, ``"high"``.
        ``unit_dimension``
            The physical dimension of the value
            (``"time"``, ``"length"``, ``"velocity"``, …) or ``None``.
        ``dependency_tags``
            List of tags identifying fields that depend on or are
            affected by this path.
        """
        return self._build_path_registry()

    # ------------------------------------------------------------------
    # Internal: build the static path registry
    # ------------------------------------------------------------------

    def _build_path_registry(self) -> dict[str, dict[str, Any]]:
        """Construct the static path registry.

        This is a hand-curated registry of the most important spec paths.
        It covers numerics, physics, geometry, boundaries, mesh, and
        execution.
        """
        paths: dict[str, dict[str, Any]] = {}

        def _add(
            pointer: str,
            *,
            value_type: str = "object",
            required: bool = False,
            mutable: bool = True,
            risk_level: str = "low",
            unit_dimension: str | None = None,
            dependency_tags: list[str] | None = None,
        ) -> None:
            paths[pointer] = {
                "json_pointer": pointer,
                "value_schema": {"type": value_type},
                "required": required,
                "mutable": mutable,
                "risk_level": risk_level,
                "unit_dimension": unit_dimension,
                "dependency_tags": dependency_tags or [],
            }

        # --- Top-level metadata ---
        _add("/schema_version", value_type="string", required=True, mutable=False)
        _add("/spec_id", value_type="string", required=True, mutable=False)
        _add("/session_id", value_type="string", required=True, mutable=False)
        _add("/version", value_type="integer", required=True, mutable=False)
        _add("/parent_version", value_type="integer", mutable=False)

        # --- Study ---
        _add("/study/title", value_type="string", required=True, risk_level="low")
        _add("/study/objective", value_type="string", required=True, risk_level="low")
        _add("/study/research_questions", value_type="array", risk_level="low")

        # --- Physics ---
        _add("/physics/material", value_type="object", required=True,
             risk_level="medium", dependency_tags=["density", "kinematic_viscosity"])
        _add("/physics/density", value_type="object", risk_level="medium",
             unit_dimension="density", dependency_tags=["reynolds_number"])
        _add("/physics/kinematic_viscosity", value_type="object", risk_level="high",
             unit_dimension="kinematic_viscosity",
             dependency_tags=["reynolds_number", "delta_t"])
        _add("/physics/reynolds_number", value_type="object", risk_level="high",
             unit_dimension="dimensionless",
             dependency_tags=["flow_regime", "turbulence_model"])
        _add("/physics/velocity", value_type="object", risk_level="high",
             unit_dimension="velocity",
             dependency_tags=["reynolds_number", "delta_t", "courant_number"])
        _add("/physics/characteristic_length", value_type="object", risk_level="medium",
             unit_dimension="length",
             dependency_tags=["reynolds_number", "mesh_resolution"])

        # --- Geometry ---
        _add("/geometry/domain/length", value_type="object", required=True,
             risk_level="high", unit_dimension="length",
             dependency_tags=["mesh_resolution", "domain_bounds"])
        _add("/geometry/domain/width", value_type="object",
             risk_level="medium", unit_dimension="length",
             dependency_tags=["mesh_resolution", "domain_bounds"])
        _add("/geometry/domain/height", value_type="object",
             risk_level="medium", unit_dimension="length",
             dependency_tags=["mesh_resolution", "domain_bounds"])
        _add("/geometry/domain/dimensions", value_type="string", required=True,
             risk_level="high", mutable=False,
             dependency_tags=["boundary_config", "solver"])

        # --- Boundaries ---
        _add("/boundaries/conditions", value_type="array", required=True,
             risk_level="high", dependency_tags=["inlet_velocity", "outlet_pressure"])

        # --- Numerics: time control ---
        _add("/numerics/time/mode", value_type="string", required=True,
             risk_level="high", mutable=False,
             dependency_tags=["solver", "discretization"])
        _add("/numerics/time/start_time", value_type="object", risk_level="medium",
             unit_dimension="time", dependency_tags=["duration"])
        _add("/numerics/time/end_time", value_type="object", risk_level="high",
             unit_dimension="time",
             dependency_tags=["duration", "write_interval", "statistics_windows"])
        _add("/numerics/time/duration", value_type="object", risk_level="medium",
             unit_dimension="time", mutable=False,
             dependency_tags=["end_time", "start_time"])
        _add("/numerics/time/delta_t", value_type="object", risk_level="high",
             unit_dimension="time",
             dependency_tags=["courant_number", "write_interval", "stability"])
        _add("/numerics/time/adaptive", value_type="boolean", risk_level="medium",
             dependency_tags=["delta_t", "max_courant"])
        _add("/numerics/time/max_courant", value_type="number", risk_level="high",
             unit_dimension="dimensionless",
             dependency_tags=["delta_t", "stability"])
        _add("/numerics/time/max_delta_t", value_type="object", risk_level="medium",
             unit_dimension="time", dependency_tags=["delta_t", "stability"])
        _add("/numerics/time/write_control", value_type="string", risk_level="medium",
             dependency_tags=["write_interval"])
        _add("/numerics/time/write_interval", value_type="object", risk_level="medium",
             unit_dimension="time",
             dependency_tags=["write_control", "storage"])
        _add("/numerics/time/purge_write", value_type="integer", risk_level="low",
             dependency_tags=["storage"])
        _add("/numerics/time/statistics_windows", value_type="array", risk_level="medium",
             dependency_tags=["end_time", "start_time"])

        # --- Numerics: solver & discretisation ---
        _add("/numerics/solver", value_type="string", required=True,
             risk_level="high", mutable=False,
             dependency_tags=["time_mode", "turbulence_model", "discretization"])
        _add("/numerics/discretization", value_type="object", risk_level="high",
             dependency_tags=["solver", "accuracy", "stability"])
        _add("/numerics/turbulence_model", value_type="string", risk_level="high",
             dependency_tags=["reynolds_number", "solver", "mesh_resolution"])

        # --- Mesh ---
        _add("/mesh/resolution", value_type="object", required=True,
             risk_level="high", dependency_tags=["accuracy", "compute_cost"])
        _add("/mesh/mesh_type", value_type="string", required=True,
             risk_level="medium", mutable=False,
             dependency_tags=["geometry", "solver"])
        _add("/mesh/refinement_regions", value_type="array", risk_level="medium",
             dependency_tags=["accuracy", "compute_cost"])

        # --- Observations ---
        _add("/observations/targets", value_type="array", risk_level="low",
             dependency_tags=["postprocessing", "analysis"])
        _add("/observations/probes", value_type="array", risk_level="low",
             dependency_tags=["postprocessing", "analysis"])

        # --- Execution ---
        _add("/execution/target_id", value_type="string", required=True,
             risk_level="low", dependency_tags=["parallel", "cores"])
        _add("/execution/parallel", value_type="boolean", required=True,
             risk_level="medium", dependency_tags=["cores", "decomposition"])
        _add("/execution/cores", value_type="integer", risk_level="medium",
             dependency_tags=["parallel", "decomposition", "compute_cost"])

        # --- Validation ---
        _add("/validation/checks", value_type="array", risk_level="low",
             dependency_tags=["postprocessing", "quality_control"])

        return paths
