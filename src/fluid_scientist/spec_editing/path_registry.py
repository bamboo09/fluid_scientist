"""Schema-driven path registry for the spec-editing module.

The :class:`PathRegistry` is the central metadata source that tells the
patch engine *everything* it needs to know about a given JSON Pointer
path in the spec:

* Does the path exist in the schema?  (:meth:`validate_path`)
* Is the field mutable?  (:meth:`is_mutable`)
* What is the risk level?  (:meth:`get_risk_level`)
* What unit dimension does it expect?  (:attr:`PathMetadata.unit_dimension`)
* What derived fields depend on it?  (:attr:`PathMetadata.dependency_tags`)

The registry is **auto-generated** from the
:class:`~fluid_scientist.study_spec.schema_export.SchemaExporter` at
construction time.  It augments the static path metadata from the
schema exporter with support for two dynamic path patterns:

1. **Entity paths** with ``{entity_id}`` placeholders, e.g.
   ``/geometry/entities/{entity_id}/primitive/type``.
2. **Array append paths** using the JSON Pointer ``-`` sentinel, e.g.
   ``/observations/probes/-``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.study_spec.schema_export import SchemaExporter

__all__ = ["PathMetadata", "PathRegistry"]

#: Risk level literal.
RiskLevel = Literal["low", "medium", "high", "critical"]


class PathMetadata(BaseModel):
    """Metadata for a single JSON Pointer path in the spec.

    Parameters
    ----------
    json_pointer:
        The canonical JSON Pointer string, e.g.
        ``"/numerics/time/end_time"``.
    value_schema:
        A compact dict describing the expected value type
        (e.g. ``{"type": "number"}``).
    required:
        Whether the path is required (must exist in a valid spec).
    mutable:
        Whether the path can be changed by a patch at runtime.
    risk_level:
        One of ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.
    unit_dimension:
        The physical dimension of the value
        (``"time"``, ``"length"``, ``"velocity"``, …) or ``None``.
    dependency_tags:
        Tags identifying fields that depend on or are affected by this
        path.  Used by the :class:`ImpactAnalyzer` to determine
        cascading recomputation needs.
    """

    model_config = ConfigDict(extra="forbid")

    json_pointer: str
    value_schema: dict[str, Any] = Field(default_factory=dict)
    required: bool = False
    mutable: bool = True
    risk_level: RiskLevel = "low"
    unit_dimension: str | None = None
    dependency_tags: set[str] = Field(default_factory=set)


# ---------------------------------------------------------------------------
# Path pattern matchers
# ---------------------------------------------------------------------------

#: Matches entity-id placeholder paths like
#: ``/geometry/entities/{entity_id}/primitive/type``.
_ENTITY_PATH_RE = re.compile(
    r"^/geometry/entities/[^/]+(/.*)?$"
)

#: Matches array-append paths like ``/observations/probes/-`` or
#: ``/observations/probes/-/field``.
_APPEND_PATH_RE = re.compile(
    r"^(.*)/-($|/.*)"
)

#: Known entity sub-paths (relative to ``/geometry/entities/{entity_id}``).
_ENTITY_SUB_PATHS: list[str] = [
    "/semantic_type",
    "/primitive",
    "/primitive/type",
    "/polygon_vertices",
    "/original_user_semantics",
    "/placement",
    "/placement/x",
    "/placement/y",
    "/placement/orientation",
    "/placement/attachment",
]

#: Metadata defaults for entity sub-paths.
_ENTITY_PATH_META: dict[str, dict[str, Any]] = {
    "/semantic_type": {"value_schema": {"type": "string"}, "mutable": True, "risk_level": "medium"},
    "/primitive": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "medium"},
    "/primitive/type": {"value_schema": {"type": "string"}, "mutable": True, "risk_level": "medium"},
    "/polygon_vertices": {"value_schema": {"type": "array"}, "mutable": True, "risk_level": "medium"},
    "/original_user_semantics": {"value_schema": {"type": "string"}, "mutable": False, "risk_level": "low"},
    "/placement": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "medium"},
    "/placement/x": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "medium", "unit_dimension": "length"},
    "/placement/y": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "medium", "unit_dimension": "length"},
    "/placement/orientation": {"value_schema": {"type": "string"}, "mutable": True, "risk_level": "low"},
    "/placement/attachment": {"value_schema": {"type": "string"}, "mutable": True, "risk_level": "low"},
}

#: Known append-eligible array paths and their element schemas.
_APPEND_ARRAY_PATHS: dict[str, dict[str, Any]] = {
    "/observations/probes": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "low"},
    "/observations/targets": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "low"},
    "/boundaries/conditions": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "high"},
    "/mesh/refinement_regions": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "medium"},
    "/study/research_questions": {"value_schema": {"type": "string"}, "mutable": True, "risk_level": "low"},
    "/validation/checks": {"value_schema": {"type": "string"}, "mutable": True, "risk_level": "low"},
    "/initial_conditions": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "medium"},
    "/numerics/time/statistics_windows": {"value_schema": {"type": "object"}, "mutable": True, "risk_level": "medium"},
}


class PathRegistry:
    """Auto-generated registry of spec path metadata.

    The registry is built once at construction time from the
    :class:`SchemaExporter`'s static path registry.  It augments that
    data with support for dynamic paths (entity placeholders and array
    append sentinels).

    Usage::

        registry = PathRegistry()
        meta = registry.get_path_metadata("/numerics/time/end_time")
        assert meta is not None
        assert meta.mutable is True
        assert meta.risk_level == "high"
    """

    def __init__(self) -> None:
        self._exporter = SchemaExporter()
        self._paths: dict[str, PathMetadata] = {}
        self._build_registry()

    # ------------------------------------------------------------------
    # Internal: build the registry
    # ------------------------------------------------------------------

    def _build_registry(self) -> None:
        """Populate ``self._paths`` from the SchemaExporter."""
        raw = self._exporter.get_path_registry()
        for pointer, meta_dict in raw.items():
            risk = meta_dict.get("risk_level", "low")
            # Map "critical" if present; the schema exporter uses up to "high".
            if risk not in ("low", "medium", "high", "critical"):
                risk = "low"
            self._paths[pointer] = PathMetadata(
                json_pointer=pointer,
                value_schema=meta_dict.get("value_schema", {}),
                required=meta_dict.get("required", False),
                mutable=meta_dict.get("mutable", True),
                risk_level=risk,  # type: ignore[arg-type]
                unit_dimension=meta_dict.get("unit_dimension"),
                dependency_tags=set(meta_dict.get("dependency_tags", [])),
            )

        # Add entity-relation paths.
        self._paths["/geometry/relations"] = PathMetadata(
            json_pointer="/geometry/relations",
            value_schema={"type": "array"},
            mutable=True,
            risk_level="medium",
            dependency_tags={"geometry", "placement"},
        )

    # ------------------------------------------------------------------
    # Path normalisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_entity_path(path: str) -> str | None:
        """If *path* matches an entity placeholder pattern, return the
        canonical template with ``{entity_id}``.  Otherwise return ``None``.
        """
        if not _ENTITY_PATH_RE.match(path):
            return None
        parts = path.split("/")
        # parts: ['', 'geometry', 'entities', '<entity_id>', ...rest]
        if len(parts) < 4:
            return None
        parts[3] = "{entity_id}"
        return "/".join(parts)

    @staticmethod
    def _normalise_append_path(path: str) -> tuple[str, str] | None:
        """If *path* contains an array-append ``-`` sentinel, return
        ``(base_array_path, remaining_sub_path)``.  Otherwise ``None``.
        """
        m = _APPEND_PATH_RE.match(path)
        if m is None:
            return None
        base = m.group(1)
        remaining = m.group(2) or ""
        return base, remaining

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_path_metadata(self, path: str) -> PathMetadata | None:
        """Return :class:`PathMetadata` for *path*, or ``None`` if the
        path is not recognised.

        Handles three categories of paths:

        1. **Static paths** — looked up directly in the registry.
        2. **Entity paths** — ``/geometry/entities/{id}/...`` patterns
           are normalised to ``{entity_id}`` templates and matched
           against known entity sub-paths.
        3. **Append paths** — ``/array/-`` patterns are matched against
           known append-eligible array paths.
        """
        # 1. Direct lookup.
        if path in self._paths:
            return self._paths[path]

        # 2. Entity placeholder path.
        entity_template = self._normalise_entity_path(path)
        if entity_template is not None:
            # Extract the sub-path after /geometry/entities/{entity_id}
            prefix = "/geometry/entities/{entity_id}"
            sub_path = entity_template[len(prefix):] if entity_template.startswith(prefix) else ""
            meta = _ENTITY_PATH_META.get(sub_path)
            if meta is not None:
                return PathMetadata(
                    json_pointer=entity_template,
                    value_schema=meta["value_schema"],
                    mutable=meta.get("mutable", True),
                    risk_level=meta.get("risk_level", "low"),  # type: ignore[arg-type]
                    unit_dimension=meta.get("unit_dimension"),
                    dependency_tags={"geometry"},
                )
            # Unknown entity sub-path but still a valid entity path.
            return PathMetadata(
                json_pointer=entity_template,
                value_schema={"type": "object"},
                mutable=True,
                risk_level="medium",
                dependency_tags={"geometry"},
            )

        # 3. Array-append path.
        append_info = self._normalise_append_path(path)
        if append_info is not None:
            base, _remaining = append_info
            arr_meta = _APPEND_ARRAY_PATHS.get(base)
            if arr_meta is not None:
                return PathMetadata(
                    json_pointer=base + "/-",
                    value_schema=arr_meta["value_schema"],
                    mutable=arr_meta.get("mutable", True),
                    risk_level=arr_meta.get("risk_level", "low"),  # type: ignore[arg-type]
                    dependency_tags={"array_append"},
                )

        return None

    def is_mutable(self, path: str) -> bool:
        """Return ``True`` if the field at *path* can be modified."""
        meta = self.get_path_metadata(path)
        if meta is None:
            return False
        return meta.mutable

    def get_risk_level(self, path: str) -> str:
        """Return the risk level for *path*, defaulting to ``"low"``."""
        meta = self.get_path_metadata(path)
        if meta is None:
            return "low"
        return meta.risk_level

    def validate_path(self, path: str) -> bool:
        """Return ``True`` if *path* exists in the schema."""
        return self.get_path_metadata(path) is not None

    def list_paths(self) -> list[str]:
        """Return a sorted list of all registered JSON Pointer paths."""
        return sorted(self._paths.keys())
