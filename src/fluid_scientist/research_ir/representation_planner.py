"""Representation Planner -- assigns geometry representations to entities.

Takes a :class:`GeometryEntity` (with a ``semantic_shape`` such as
``"trapezoid"``) and assigns a proper :class:`GeometryRepresentation`.

Two planning strategies are supported:

* **Rule-based fallback** -- a deterministic ``SHAPE_TO_REPRESENTATION``
  mapping covers common shapes (circle, rectangle, triangle, trapezoid,
  cosine bell, half sine, gaussian, ellipse, ...).  No LLM is required.
* **LLM-based planning** -- when an ``llm_client`` is provided and the
  semantic shape is not in the rule table, the planner loads the
  ``geometry_representation_planner`` prompt from the
  :class:`PromptRegistry` and asks the LLM to choose the most faithful
  mathematical representation.

If neither strategy can resolve the shape, the entity is marked
``representation_status="needs_clarification"``.
"""

from __future__ import annotations

import logging
from typing import Any

from fluid_scientist.research_ir.models import (
    GeometryEntity,
    GeometryRepresentation,
    OpenWorldResearchIR,
)
from fluid_scientist.research_ir.prompt_registry import PromptRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Prompt name used to load the system prompt from the registry.
_PROMPT_NAME = "geometry_representation_planner"

# Valid representation ``type`` values -- must match the ``Literal`` declared
# on :class:`GeometryRepresentation`.
_VALID_REP_TYPES: frozenset[str] = frozenset(
    {
        "circle",
        "ellipse",
        "parametric_polygon",
        "explicit_polygon",
        "profile_function",
        "csg",
        "imported_mesh",
        "implicit_surface",
        "unknown",
    }
)

# Valid ``representation_status`` values -- must match the ``Literal``
# declared on :class:`GeometryEntity`.
_VALID_STATUSES: frozenset[str] = frozenset(
    {"resolved", "needs_clarification", "unsupported"}
)

# ---------------------------------------------------------------------------
# Rule-based shape -> representation mapping
# ---------------------------------------------------------------------------

#: Deterministic mapping from semantic shape name to a representation spec.
#: Each entry provides the representation ``type``, an optional ``subtype``,
#: and the list of ``definition_keys`` that constitute the representation's
#: ``definition`` template.
SHAPE_TO_REPRESENTATION: dict[str, dict[str, Any]] = {
    "circle": {
        "type": "circle",
        "subtype": None,
        "definition_keys": ["center_x", "center_y", "radius"],
    },
    "cylinder": {
        "type": "circle",
        "subtype": "2d_cross_section",
        "definition_keys": ["center_x", "center_y", "radius"],
    },
    "rectangle": {
        "type": "explicit_polygon",
        "subtype": "axis_aligned",
        "definition_keys": ["center_x", "center_y", "width", "height"],
    },
    "triangle": {
        "type": "explicit_polygon",
        "subtype": "three_vertex",
        "definition_keys": ["base_width", "height", "center_x"],
    },
    "trapezoid": {
        "type": "explicit_polygon",
        "subtype": "four_vertex",
        "definition_keys": ["top_width", "bottom_width", "height", "center_x"],
    },
    "cosine_bell": {
        "type": "profile_function",
        "subtype": "cosine",
        "definition_keys": ["center_x", "width", "height"],
    },
    "half_sine": {
        "type": "profile_function",
        "subtype": "half_sine",
        "definition_keys": ["center_x", "width", "height"],
    },
    "gaussian": {
        "type": "profile_function",
        "subtype": "gaussian",
        "definition_keys": ["center_x", "width", "height"],
    },
    "ellipse": {
        "type": "ellipse",
        "subtype": None,
        "definition_keys": [
            "center_x",
            "center_y",
            "semi_axis_a",
            "semi_axis_b",
        ],
    },
}


class RepresentationPlanner:
    """Plans geometry representations for open-world entities.

    Args:
        llm_client: An optional LLM client with a ``call`` method.  When
            provided, the planner uses the LLM for shapes not covered by
            :data:`SHAPE_TO_REPRESENTATION`.  When ``None`` (default),
            only the rule-based mapping is used.
        prompt_registry: An optional :class:`PromptRegistry` for loading
            the system prompt.  When ``None`` (default), a new registry
            instance is created.
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_registry: PromptRegistry = (
            prompt_registry if prompt_registry is not None else PromptRegistry()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        entity: GeometryEntity,
        ir: OpenWorldResearchIR,
    ) -> GeometryEntity:
        """Plan a geometry representation for a single entity.

        If the entity already has a resolved representation
        (``representation.type != "unknown"``) it is returned unchanged.

        Otherwise the planner tries, in order:

        1. **Rule-based lookup** -- if ``entity.semantic_shape`` is found
           in :data:`SHAPE_TO_REPRESENTATION`, a representation is built
           deterministically and ``representation_status`` is set to
           ``"resolved"``.
        2. **LLM-based planning** -- if an LLM client is available and the
           shape is not in the rule table, the LLM is asked to choose a
           representation.
        3. **Needs clarification** -- if no strategy resolves the shape,
           the representation is left as ``"unknown"`` and the status is
           set to ``"needs_clarification"``.

        Args:
            entity: The geometry entity to plan a representation for.
            ir: The full research IR, providing context (dimensionality,
                spatial relations, etc.) for LLM-based planning.

        Returns:
            The same *entity* (modified in place) with its
            ``representation`` and ``representation_status`` updated.
        """
        # Already resolved -- return as-is.
        if entity.representation.type != "unknown":
            return entity

        semantic_shape = (
            entity.semantic_shape.strip().lower()
            if entity.semantic_shape
            else ""
        )

        # 1. Rule-based lookup.
        if semantic_shape and semantic_shape in SHAPE_TO_REPRESENTATION:
            return self._apply_rule_based(entity, semantic_shape)

        # 2. LLM-based planning for unknown / unmapped shapes.
        if self._llm_client is not None:
            try:
                planned = self._plan_with_llm(entity, ir)
                if planned is not None:
                    return planned
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "LLM representation planning failed for entity '%s': %s",
                    entity.entity_id,
                    exc,
                )

        # 3. Fallback: needs clarification.
        entity.representation = GeometryRepresentation(type="unknown")
        entity.representation_status = "needs_clarification"
        logger.info(
            "Entity '%s' (semantic_shape='%s') could not be resolved; "
            "marked as needs_clarification.",
            entity.entity_id,
            entity.semantic_shape,
        )
        return entity

    def plan_all(self, ir: OpenWorldResearchIR) -> OpenWorldResearchIR:
        """Plan representations for all geometry entities in the IR.

        Iterates over ``ir.geometry_entities`` and calls :meth:`plan` on
        each one.  The IR is modified in place and also returned for
        chaining convenience.

        Args:
            ir: The research IR whose geometry entities should be planned.

        Returns:
            The same *ir* with all geometry entities planned.
        """
        for i, entity in enumerate(ir.geometry_entities):
            ir.geometry_entities[i] = self.plan(entity, ir)
        return ir

    # ------------------------------------------------------------------
    # Rule-based planning
    # ------------------------------------------------------------------

    def _apply_rule_based(
        self,
        entity: GeometryEntity,
        semantic_shape: str,
    ) -> GeometryEntity:
        """Apply the deterministic shape-to-representation mapping."""
        spec = SHAPE_TO_REPRESENTATION[semantic_shape]
        definition = self._build_definition(spec["definition_keys"], entity)

        entity.representation = GeometryRepresentation(
            type=spec["type"],
            subtype=spec["subtype"],
            definition=definition,
        )
        entity.representation_status = "resolved"
        logger.debug(
            "Rule-based representation for entity '%s' (%s): "
            "type=%s, subtype=%s",
            entity.entity_id,
            semantic_shape,
            spec["type"],
            spec["subtype"],
        )
        return entity

    @staticmethod
    def _build_definition(
        definition_keys: list[str],
        entity: GeometryEntity,
    ) -> dict[str, Any]:
        """Build a definition dict, pre-filling values from entity parameters.

        Each key in *definition_keys* is included in the returned dict.
        If the entity has a matching entry in ``entity.parameters``, its
        ``value`` is used; otherwise the key maps to ``None``.
        """
        definition: dict[str, Any] = {}
        for key in definition_keys:
            if key in entity.parameters:
                definition[key] = entity.parameters[key].value
            else:
                definition[key] = None
        return definition

    # ------------------------------------------------------------------
    # LLM-based planning
    # ------------------------------------------------------------------

    def _plan_with_llm(
        self,
        entity: GeometryEntity,
        ir: OpenWorldResearchIR,
    ) -> GeometryEntity | None:
        """Call the LLM to plan a representation.

        Returns the updated entity on success, or ``None`` if the LLM
        call did not succeed.
        """
        system_prompt = self._load_system_prompt()
        user_message = self._build_user_message(entity, ir)

        output_schema = {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "subtype": {"type": "string"},
                "definition": {"type": "object"},
                "representation_status": {"type": "string"},
            },
        }

        parsed, record = self._llm_client.call(
            purpose="geometry_representation_planning",
            prompt_name=_PROMPT_NAME,
            system_prompt=system_prompt,
            user_message=user_message,
            output_schema=output_schema,
        )

        if not getattr(record, "success", False):
            logger.warning(
                "LLM call unsuccessful for entity '%s': %s",
                entity.entity_id,
                getattr(record, "error", "unknown error"),
            )
            return None

        return self._apply_llm_result(entity, parsed)

    def _apply_llm_result(
        self,
        entity: GeometryEntity,
        parsed: dict[str, Any],
    ) -> GeometryEntity | None:
        """Apply the parsed LLM result to the entity.

        Validates the ``type`` and ``representation_status`` fields
        against the allowed ``Literal`` values, falling back to safe
        defaults when the LLM returns something unexpected.
        """
        if not isinstance(parsed, dict):
            logger.warning(
                "LLM returned non-dict result for entity '%s'; ignoring.",
                entity.entity_id,
            )
            return None

        rep_type = parsed.get("type", "unknown")
        if rep_type not in _VALID_REP_TYPES:
            logger.warning(
                "LLM returned invalid representation type '%s' for entity "
                "'%s'; falling back to 'unknown'.",
                rep_type,
                entity.entity_id,
            )
            rep_type = "unknown"

        subtype = parsed.get("subtype")
        # Coerce non-string subtypes to None.
        if subtype is not None and not isinstance(subtype, str):
            subtype = str(subtype) if subtype else None

        definition = parsed.get("definition", {})
        if not isinstance(definition, dict):
            definition = {}

        status = parsed.get("representation_status", "needs_clarification")
        if status not in _VALID_STATUSES:
            logger.warning(
                "LLM returned invalid representation_status '%s' for entity "
                "'%s'; falling back to 'needs_clarification'.",
                status,
                entity.entity_id,
            )
            status = "needs_clarification"

        entity.representation = GeometryRepresentation(
            type=rep_type,
            subtype=subtype,
            definition=definition,
        )
        entity.representation_status = status  # type: ignore[assignment]

        logger.debug(
            "LLM representation for entity '%s': type=%s, subtype=%s, "
            "status=%s",
            entity.entity_id,
            rep_type,
            subtype,
            status,
        )
        return entity

    # ------------------------------------------------------------------
    # Prompt / message helpers
    # ------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        """Load the system prompt from the registry.

        Falls back to a minimal inline prompt when the prompt file is
        not found in the registry.
        """
        try:
            return self._prompt_registry.load(_PROMPT_NAME)
        except FileNotFoundError:
            logger.warning(
                "Prompt '%s' not found in registry; using minimal fallback "
                "prompt.",
                _PROMPT_NAME,
            )
            return self._fallback_system_prompt()

    @staticmethod
    def _fallback_system_prompt() -> str:
        """Return a minimal system prompt when the registry has no file."""
        return (
            "You are a CFD geometry representation planner. "
            "Given a geometry entity with a semantic shape, choose the most "
            "faithful and general mathematical representation.\n\n"
            "Available representation types: circle, ellipse, "
            "parametric_polygon, explicit_polygon, profile_function, csg, "
            "imported_mesh, implicit_surface, unknown.\n\n"
            "Rules:\n"
            "1. Prefer existing general representations over specific "
            "shape templates.\n"
            "2. triangle, rectangle, trapezoid, parallelogram, "
            "regular_polygon should be represented as explicit_polygon.\n"
            "3. Sine, cosine, or piecewise wall profiles should be "
            "represented as profile_function.\n"
            "4. When vertices are provided, use explicit_polygon.\n"
            "5. When CAD/STL is provided, use imported_mesh.\n"
            "6. When the shape cannot be uniquely determined from "
            "parameters, set representation_status to "
            "'needs_clarification'.\n"
            "7. Never discard an entity or convert an unknown shape to "
            "the nearest known shape.\n\n"
            "Return a JSON object with: type, subtype, definition, "
            "representation_status."
        )

    @staticmethod
    def _build_user_message(
        entity: GeometryEntity,
        ir: OpenWorldResearchIR,
    ) -> str:
        """Build the user message describing the entity and IR context."""
        parts: list[str] = []

        parts.append("## 几何实体")
        parts.append(f"- entity_id: {entity.entity_id}")
        parts.append(f"- raw_name: {entity.raw_name}")
        parts.append(f"- role: {entity.role}")
        parts.append(f"- semantic_shape: {entity.semantic_shape}")
        parts.append(f"- confidence: {entity.confidence}")

        if entity.parameters:
            param_lines: list[str] = []
            for key, pv in entity.parameters.items():
                val_str = (
                    str(pv.value) if pv.value is not None else "null"
                )
                if pv.unit:
                    val_str += f" {pv.unit}"
                param_lines.append(f"  - {key}: {val_str}")
            parts.append("\n## 已知参数")
            parts.append("\n".join(param_lines))

        if entity.relations:
            parts.append("\n## 空间关系")
            for rel in entity.relations:
                parts.append(f"  - {rel}")

        parts.append(f"\n## 全局维度: {ir.dimensionality}")

        parts.append(
            "\n## 可用表示类型\n"
            "circle, ellipse, parametric_polygon, explicit_polygon, "
            "profile_function, csg, imported_mesh, implicit_surface, unknown"
        )

        parts.append(
            "\n请为该实体选择最忠实的数学表示，返回 JSON：\n"
            '{"type": "...", "subtype": "...", "definition": {}, '
            '"representation_status": '
            '"resolved|needs_clarification|unsupported"}'
        )

        return "\n".join(parts)


__all__ = ["RepresentationPlanner", "SHAPE_TO_REPRESENTATION"]
