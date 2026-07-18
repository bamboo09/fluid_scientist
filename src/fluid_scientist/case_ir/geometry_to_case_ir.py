"""Bridge from :class:`SimulationStudySpec` to :class:`RequestedCaseIR`.

The :class:`StudySpecToCaseIRConverter` transforms a serialised
:class:`~fluid_scientist.study_spec.models.SimulationStudySpec` dict
into a :class:`~fluid_scientist.case_ir.models.RequestedCaseIR`-
compatible dict, preserving all SourcedValue provenance by mapping
each :class:`~fluid_scientist.study_spec.quantities.SourcedValue` to a
:class:`~fluid_scientist.case_ir.models.ParameterValue`.

Mapping summary
---------------
* **Geometry entities** -> :class:`~fluid_scientist.case_ir.models.Entity`
  objects with :class:`ParameterValue` parameters.
* **Boundary conditions** ->
  :class:`~fluid_scientist.case_ir.models.BoundaryIntent` objects.
* **Observations** -> :class:`~fluid_scientist.case_ir.models.Observable`
  objects.
* **Physics + numerics** ->
  :class:`~fluid_scientist.case_ir.models.PhysicsIntent`.
* **Geometry relations** ->
  :class:`~fluid_scientist.case_ir.models.Relation` objects.
* All :class:`SourcedValue` provenance is preserved through the
  source-status -> ParameterValue-source mapping.
"""

from __future__ import annotations

from typing import Any

__all__ = ["StudySpecToCaseIRConverter"]


# ---------------------------------------------------------------------------
# SourcedValue status -> ParameterValue source/status mapping
# ---------------------------------------------------------------------------

_SOURCED_TO_PV_SOURCE: dict[str, str] = {
    "user_explicit": "USER_EXPLICIT",
    "user_confirmed": "USER_CONFIRMED",
    "model_recommended": "MODEL_RECOMMENDED",
    "derived": "FORMULA_DERIVED",
    "default_pending": "SYSTEM_DEFAULT",
    "unknown": "SYSTEM_DEFAULT",
}

_SOURCED_TO_PV_STATUS: dict[str, str] = {
    "user_explicit": "CONFIRMED",
    "user_confirmed": "CONFIRMED",
    "model_recommended": "RECOMMENDED",
    "derived": "CONFIRMED",
    "default_pending": "ASSUMED",
    "unknown": "UNRESOLVED",
}

#: semantic_type -> Entity.kind mapping.
_KIND_MAP: dict[str, str] = {
    "cylinder_2d": "cylinder",
    "cylinder_3d": "cylinder",
    "sphere_2d": "sphere",
    "sphere_3d": "sphere",
    "rectangle_2d": "box",
    "box_3d": "box",
    "pipe_2d": "pipe",
    "pipe_3d": "pipe",
    "plane_wall_2d": "plane_wall",
    "plane_wall_3d": "plane_wall",
    "nozzle_2d": "nozzle",
    "nozzle_3d": "nozzle",
    "imported": "imported_stl",
    "imported_stl": "imported_stl",
}

#: GeometryRelation.type -> Relation.type mapping.
_RELATION_TYPE_MAP: dict[str, str] = {
    "attached_to": "attached_to",
    "aligned_below": "aligned_with",
    "aligned_above": "aligned_with",
    "centered_in": "inside",
    "distance_to": "near",
    "tangent_to": "near",
    "inside": "inside",
    "outside": "near",
    "intersects": "intersects",
    "custom": "near",
}

#: Turbulence model -> PhysicsIntent.turbulence mapping.
_TURBULENCE_CATEGORY: dict[str, str] = {
    "laminar": "laminar",
    "RANS_kEpsilon": "RANS",
    "RANS_kOmegaSST": "RANS",
    "LES": "LES",
    "DES": "DES",
    "DNS": "DNS",
}


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


class StudySpecToCaseIRConverter:
    """Converts a SimulationStudySpec dict to a RequestedCaseIR dict.

    The converter preserves all SourcedValue provenance by mapping each
    SourcedValue to a ParameterValue with the appropriate ``source``
    and ``status`` fields.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def convert(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Convert a SimulationStudySpec dict to a RequestedCaseIR dict.

        Args:
            spec: A serialised ``SimulationStudySpec`` dict.

        Returns:
            A dict compatible with
            :class:`~fluid_scientist.case_ir.models.RequestedCaseIR`,
            plus a ``domain`` key for downstream fidelity checking.
        """
        spec_id = spec.get("spec_id", "unknown_spec")
        version = spec.get("version", 1)

        entities = self._convert_entities(spec)
        boundary_intents = self._convert_boundaries(spec)
        observables = self._convert_observations(spec)
        physics = self._convert_physics(spec)
        relations = self._convert_relations(spec)
        domain = self._extract_domain(spec)
        materials = self._convert_materials(spec)

        return {
            "schema_version": "2.0",
            "case_ir_version": 1,
            "study_id": spec_id,
            "case_id": f"{spec_id}_case_v{version}",
            "physics": physics,
            "entities": entities,
            "regions": [],
            "relations": relations,
            "interfaces": [],
            "materials": materials,
            "fields": [],
            "boundary_intents": boundary_intents,
            "initial_conditions": [],
            "operating_stages": [],
            "mesh_intent": {},
            "numerical_intent": {},
            "observables": observables,
            "derived_constraints": [],
            "assumptions": [],
            "ambiguities": [],
            "unresolved_requirements": [],
            "extensions": [],
            # Extra metadata for fidelity checking (not part of the
            # strict RequestedCaseIR schema, but kept for downstream
            # consumers that work with dicts).
            "domain": domain,
        }

    # ------------------------------------------------------------------
    # SourcedValue -> ParameterValue
    # ------------------------------------------------------------------

    @staticmethod
    def _sourced_to_parameter_value(
        sourced: dict[str, Any] | Any,
        default_unit: str = "dimensionless",
    ) -> dict[str, Any]:
        """Convert a SourcedValue dict to a ParameterValue-compatible dict.

        If *sourced* is a plain scalar, it is wrapped with default
        provenance.
        """
        if sourced is None:
            return {
                "value": None,
                "unit": default_unit,
                "source": "SYSTEM_DEFAULT",
                "confidence": 1.0,
                "status": "UNRESOLVED",
            }

        if isinstance(sourced, int | float | str):
            return {
                "value": sourced,
                "unit": default_unit,
                "source": "USER_EXPLICIT",
                "confidence": 1.0,
                "status": "CONFIRMED",
            }

        if isinstance(sourced, dict):
            status = sourced.get("status", "unknown")
            confidence = sourced.get("confidence")
            if confidence is None:
                confidence = 1.0
            return {
                "value": sourced.get("value"),
                "unit": sourced.get("unit") or default_unit,
                "source": _SOURCED_TO_PV_SOURCE.get(status, "SYSTEM_DEFAULT"),
                "confidence": float(confidence),
                "status": _SOURCED_TO_PV_STATUS.get(status, "UNRESOLVED"),
            }

        # Fallback for unexpected types.
        return {
            "value": str(sourced),
            "unit": default_unit,
            "source": "SYSTEM_DEFAULT",
            "confidence": 1.0,
            "status": "UNRESOLVED",
        }

    # ------------------------------------------------------------------
    # Geometry entities -> Entity
    # ------------------------------------------------------------------

    def _convert_entities(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert geometry entities to Case IR Entity dicts."""
        geometry = spec.get("geometry", {})
        entities = geometry.get("entities", {})
        result: list[dict[str, Any]] = []

        if not isinstance(entities, dict):
            return result

        for entity_id, entity in entities.items():
            eid = entity.get("entity_id", entity_id)
            semantic_type = entity.get("semantic_type", "custom")
            kind = _KIND_MAP.get(semantic_type, "custom")
            primitive = entity.get("primitive") or {}
            placement = entity.get("placement") or {}

            parameters: dict[str, Any] = {}

            # Store semantic_type for fidelity checking.
            parameters["semantic_type"] = self._sourced_to_parameter_value(
                {"value": semantic_type, "status": "user_explicit", "confidence": 1.0}
            )

            # Placement coordinates.
            if placement.get("x") is not None:
                parameters["center_x"] = self._sourced_to_parameter_value(
                    placement["x"], default_unit="m"
                )
            if placement.get("y") is not None:
                parameters["center_y"] = self._sourced_to_parameter_value(
                    placement["y"], default_unit="m"
                )

            # Primitive-specific parameters.
            for key, val in primitive.items():
                if key == "type":
                    continue
                if isinstance(val, dict):
                    parameters[key] = self._sourced_to_parameter_value(val)
                elif isinstance(val, int | float):
                    parameters[key] = self._sourced_to_parameter_value(
                        {"value": val, "status": "user_explicit", "confidence": 1.0}
                    )

            # Polygon vertices.
            polygon_vertices = entity.get("polygon_vertices")
            if polygon_vertices:
                parameters["vertices"] = self._sourced_to_parameter_value(
                    {"value": polygon_vertices, "status": "user_explicit", "confidence": 1.0}
                )

            result.append({
                "id": eid,
                "kind": kind,
                "parameters": parameters,
                "motion": None,
            })

        return result

    # ------------------------------------------------------------------
    # Boundary conditions -> BoundaryIntent
    # ------------------------------------------------------------------

    def _convert_boundaries(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert boundary conditions to BoundaryIntent dicts."""
        boundaries = spec.get("boundaries", {})
        conditions = boundaries.get("conditions", [])
        result: list[dict[str, Any]] = []

        if not isinstance(conditions, list):
            return result

        for i, condition in enumerate(conditions):
            patch_name = condition.get("patch_name", f"patch_{i}")
            role = condition.get("role", "custom")
            bc_type = condition.get("bc_type", "unknown")
            params = condition.get("parameters", {})
            source_status = condition.get("source_status", "unknown")

            parameters: dict[str, Any] = {}
            parameters["bc_type"] = self._sourced_to_parameter_value(
                {"value": bc_type, "status": source_status, "confidence": 1.0}
            )
            for key, val in params.items():
                if isinstance(val, dict):
                    parameters[key] = self._sourced_to_parameter_value(val)
                elif isinstance(val, int | float | str):
                    parameters[key] = self._sourced_to_parameter_value(
                        {"value": val, "status": source_status, "confidence": 1.0}
                    )

            result.append({
                "id": f"bc_{patch_name}",
                "target_patch": patch_name,
                "semantic_role": role,
                "capability_ref": None,
                "parameters": parameters,
                "fields": [],
            })

        return result

    # ------------------------------------------------------------------
    # Observations -> Observable
    # ------------------------------------------------------------------

    def _convert_observations(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert observation targets to Observable dicts."""
        observations = spec.get("observations", {})
        targets = observations.get("targets", [])
        result: list[dict[str, Any]] = []

        if not isinstance(targets, list):
            return result

        for target in targets:
            target_id = target.get("target_id", "obs_0")
            metric = target.get("metric", "custom")
            func_obj = target.get("function_object_type")
            params = target.get("parameters", {})

            result.append({
                "id": target_id,
                "semantic_type": metric,
                "target_region": "",
                "required_fields": [],
                "sampling": params,
                "analysis": {},
                "capability_status": "UNRESOLVED",
                "capability_ref": None,
                "openfoam_sampling_capability": func_obj,
                "external_analysis_capability": None,
            })

        return result

    # ------------------------------------------------------------------
    # Physics + numerics -> PhysicsIntent
    # ------------------------------------------------------------------

    def _convert_physics(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Convert physics and numerics to a PhysicsIntent dict."""
        numerics = spec.get("numerics", {})
        time = numerics.get("time", {})
        time_mode = time.get("mode", "transient")
        turbulence_model = numerics.get("turbulence_model")

        if turbulence_model is None:
            turbulence = "laminar"
            turbulence_model_str = ""
        else:
            turbulence = _TURBULENCE_CATEGORY.get(turbulence_model, "laminar")
            turbulence_model_str = turbulence_model

        return {
            "flow_regime": "incompressible",
            "time_mode": time_mode if time_mode in ("steady", "transient") else "transient",
            "turbulence": turbulence,
            "turbulence_model": turbulence_model_str,
            "heat_transfer": False,
            "multiphase": False,
            "porous_media": False,
            "moving_mesh": False,
            "additional_physics": [],
        }

    # ------------------------------------------------------------------
    # Geometry relations -> Relation
    # ------------------------------------------------------------------

    def _convert_relations(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert geometry relations to Relation dicts."""
        geometry = spec.get("geometry", {})
        relations = geometry.get("relations", [])
        result: list[dict[str, Any]] = []

        if not isinstance(relations, list):
            return result

        for rel in relations:
            rel_type = rel.get("type", "custom")
            mapped_type = _RELATION_TYPE_MAP.get(rel_type, "near")
            params = rel.get("parameters", {})

            parameters: dict[str, Any] = {}
            for key, val in params.items():
                if isinstance(val, dict):
                    parameters[key] = self._sourced_to_parameter_value(val)
                elif isinstance(val, int | float | str):
                    parameters[key] = self._sourced_to_parameter_value(
                        {"value": val, "status": "user_explicit", "confidence": 1.0}
                    )

            result.append({
                "id": rel.get("relation_id", f"rel_{len(result)}"),
                "type": mapped_type,
                "source": rel.get("subject_id", ""),
                "target": rel.get("object_id", ""),
                "parameters": parameters,
            })

        return result

    # ------------------------------------------------------------------
    # Materials
    # ------------------------------------------------------------------

    def _convert_materials(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert physics material to Material dicts."""
        physics = spec.get("physics", {})
        material = physics.get("material", {})
        material_name = ""
        if isinstance(material, dict):
            material_name = str(material.get("value", "fluid"))
        elif isinstance(material, str):
            material_name = material

        properties: dict[str, Any] = {}
        for field_name in ("density", "kinematic_viscosity", "velocity"):
            val = physics.get(field_name)
            if val is not None:
                properties[field_name] = self._sourced_to_parameter_value(val)

        return [{
            "id": "material_0",
            "kind": "newtonian_fluid",
            "properties": properties,
        }]

    # ------------------------------------------------------------------
    # Domain extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_domain(spec: dict[str, Any]) -> dict[str, Any]:
        """Extract domain dimensions from the spec for fidelity checking."""
        geometry = spec.get("geometry", {})
        domain = geometry.get("domain", {})
        return {
            "length": domain.get("length"),
            "width": domain.get("width"),
            "height": domain.get("height"),
            "dimensions": domain.get("dimensions", "2d"),
        }
