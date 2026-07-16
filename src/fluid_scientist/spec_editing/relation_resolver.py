"""Spatial relation resolution for the spec-editing module.

The :class:`RelationResolver` converts high-level spatial relations
(``attached_to``, ``centered_in``, ``aligned_below``, ``distance_to``)
into concrete coordinate values for geometry entities.

When a user says "the triangle is attached to the bottom wall", the
model emits a :class:`GeometryRelation` with ``type="attached_to"`` and
``object_id="bottom_wall"``.  The relation resolver then computes the
``y=0`` coordinate for the triangle's placement, grounding the semantic
relation in numeric values.

This module operates on plain dicts (``model_dump()`` output) so it can
be used both during patch application and during spec compilation.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["RelationResolver"]


class RelationResolver:
    """Resolve spatial relations in a geometry dict to concrete
    coordinates.

    The resolver scans the ``geometry["relations"]`` list and, for each
    relation, updates the placement coordinates of the subject entity
    based on the relation type and the object entity / domain.

    Supported relation types:

    * ``attached_to`` — places the subject at the boundary indicated by
      ``object_id`` (``bottom_wall`` -> y=0, ``top_wall`` -> y=max,
      ``left_wall`` -> x=0, ``right_wall`` -> x=max).
    * ``centered_in`` — places the subject at the centre of the domain.
    * ``aligned_below`` — same x as the object entity, y is below it
      (offset by the subject's height or a specified distance).
    * ``aligned_above`` — same x as the object entity, y is above it.
    * ``distance_to`` — places the subject at a specified distance from
      the object entity along a given axis.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_relations(self, geometry: dict[str, Any]) -> dict[str, Any]:
        """Resolve all relations in *geometry* and return an updated copy.

        The input *geometry* dict must have ``"domain"`` and
        ``"entities"`` keys (matching the
        :class:`~fluid_scientist.study_spec.geometry.GeometryDefinition`
        structure).

        Parameters
        ----------
        geometry:
            The geometry block as a plain dict.

        Returns
        -------
        A new dict with the same structure as *geometry* but with
        resolved placement coordinates for entities that participate in
        relations.
        """
        result = copy.deepcopy(geometry)
        entities: dict[str, Any] = result.get("entities", {})
        relations: list[dict[str, Any]] = result.get("relations", [])
        domain: dict[str, Any] = result.get("domain", {})

        for relation in relations:
            self._resolve_single(relation, entities, domain)

        return result

    # ------------------------------------------------------------------
    # Internal: single-relation resolution
    # ------------------------------------------------------------------

    def _resolve_single(
        self,
        relation: dict[str, Any],
        entities: dict[str, Any],
        domain: dict[str, Any],
    ) -> None:
        """Resolve a single relation, mutating *entities* in place."""
        rel_type = relation.get("type")
        subject_id = relation.get("subject_id")
        object_id = relation.get("object_id")
        params = relation.get("parameters", {})

        if subject_id not in entities:
            return

        subject = entities[subject_id]
        subject_placement = self._ensure_placement(subject)

        if rel_type == "attached_to":
            self._resolve_attached_to(subject_placement, object_id, domain, params)
        elif rel_type == "centered_in":
            self._resolve_centered_in(subject_placement, domain, params)
        elif rel_type == "aligned_below":
            self._resolve_aligned_below(subject_placement, object_id, entities, params)
        elif rel_type == "aligned_above":
            self._resolve_aligned_above(subject_placement, object_id, entities, params)
        elif rel_type == "distance_to":
            self._resolve_distance_to(subject_placement, object_id, entities, params)
        # Other relation types (tangent_to, inside, outside, intersects,
        # custom) are left for the compiler to handle.

    # ------------------------------------------------------------------
    # Internal: relation-type resolvers
    # ------------------------------------------------------------------

    def _resolve_attached_to(
        self,
        placement: dict[str, Any],
        object_id: str | None,
        domain: dict[str, Any],
        params: dict[str, Any],
    ) -> None:
        """Resolve ``attached_to`` relation.

        ``object_id`` can be a wall name (``bottom_wall``, ``top_wall``,
        ``left_wall``, ``right_wall``) or an entity id.  For wall names,
        the corresponding coordinate is set to the boundary value.
        """
        if object_id is None:
            return

        wall_map = {
            "bottom_wall": ("y", 0.0),
            "top_wall": ("y", self._domain_max(domain, "width", "height")),
            "left_wall": ("x", 0.0),
            "right_wall": ("x", self._domain_max(domain, "length", "length")),
        }

        if object_id in wall_map:
            axis, value = wall_map[object_id]
            self._set_placement_coord(placement, axis, value)
            placement["attachment"] = object_id

    def _resolve_centered_in(
        self,
        placement: dict[str, Any],
        domain: dict[str, Any],
        params: dict[str, Any],
    ) -> None:
        """Resolve ``centered_in`` relation — place at domain centre."""
        length = self._domain_max(domain, "length", "length")
        width = self._domain_max(domain, "width", "height")
        self._set_placement_coord(placement, "x", length / 2.0)
        self._set_placement_coord(placement, "y", width / 2.0)
        placement["attachment"] = "centered"

    def _resolve_aligned_below(
        self,
        placement: dict[str, Any],
        object_id: str | None,
        entities: dict[str, Any],
        params: dict[str, Any],
    ) -> None:
        """Resolve ``aligned_below`` — same x as object, y below it."""
        if object_id is None or object_id not in entities:
            return
        obj_placement = entities[object_id].get("placement") or {}
        obj_x = self._get_placement_coord(obj_placement, "x")
        obj_y = self._get_placement_coord(obj_placement, "y")
        if obj_x is not None:
            self._set_placement_coord(placement, "x", obj_x)
        offset = params.get("offset", 0.0)
        if obj_y is not None:
            self._set_placement_coord(placement, "y", obj_y - offset)

    def _resolve_aligned_above(
        self,
        placement: dict[str, Any],
        object_id: str | None,
        entities: dict[str, Any],
        params: dict[str, Any],
    ) -> None:
        """Resolve ``aligned_above`` — same x as object, y above it."""
        if object_id is None or object_id not in entities:
            return
        obj_placement = entities[object_id].get("placement") or {}
        obj_x = self._get_placement_coord(obj_placement, "x")
        obj_y = self._get_placement_coord(obj_placement, "y")
        if obj_x is not None:
            self._set_placement_coord(placement, "x", obj_x)
        offset = params.get("offset", 0.0)
        if obj_y is not None:
            self._set_placement_coord(placement, "y", obj_y + offset)

    def _resolve_distance_to(
        self,
        placement: dict[str, Any],
        object_id: str | None,
        entities: dict[str, Any],
        params: dict[str, Any],
    ) -> None:
        """Resolve ``distance_to`` — place at a distance from object
        along a specified axis."""
        if object_id is None or object_id not in entities:
            return
        obj_placement = entities[object_id].get("placement") or {}
        distance = params.get("distance", 0.0)
        axis = params.get("axis", "x")

        obj_coord = self._get_placement_coord(obj_placement, axis)
        if obj_coord is not None:
            self._set_placement_coord(placement, axis, obj_coord + distance)

    # ------------------------------------------------------------------
    # Internal: placement helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_placement(entity: dict[str, Any]) -> dict[str, Any]:
        """Ensure the entity has a ``placement`` dict and return it."""
        if "placement" not in entity or entity["placement"] is None:
            entity["placement"] = {}
        return entity["placement"]

    @staticmethod
    def _set_placement_coord(
        placement: dict[str, Any],
        axis: str,
        value: float,
    ) -> None:
        """Set the ``x`` or ``y`` coordinate in a placement dict.

        Coordinates are stored as :class:`SourcedValue`-compatible
        dicts: ``{"value": <float>, "status": "derived", "unit": "m"}``.
        """
        existing = placement.get(axis)
        unit = "m"
        status = "derived"
        if isinstance(existing, dict):
            unit = existing.get("unit", unit)
            status = existing.get("status", status)
        placement[axis] = {"value": value, "unit": unit, "status": status}

    @staticmethod
    def _get_placement_coord(
        placement: dict[str, Any],
        axis: str,
    ) -> float | None:
        """Extract the numeric value of an axis coordinate."""
        coord = placement.get(axis)
        if coord is None:
            return None
        if isinstance(coord, dict):
            val = coord.get("value")
            if isinstance(val, int | float):
                return float(val)
            return None
        if isinstance(coord, int | float):
            return float(coord)
        return None

    @staticmethod
    def _domain_max(
        domain: dict[str, Any],
        primary_key: str,
        fallback_key: str,
    ) -> float:
        """Extract a numeric domain dimension, trying *primary_key*
        then *fallback_key*, defaulting to ``0.0``."""
        for key in (primary_key, fallback_key):
            dim = domain.get(key)
            if dim is not None:
                val = dim.get("value") if isinstance(dim, dict) else dim
                if isinstance(val, int | float):
                    return float(val)
        return 0.0
