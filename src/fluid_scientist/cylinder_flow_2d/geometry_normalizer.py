"""CylinderFlow2D geometry normalizer and derived-field resolver.

This module provides two deterministic (code-only, no-LLM) components that
operate on :class:`CylinderFlow2DExperimentSpecV1`:

1. :class:`CylinderFlow2DGeometryNormalizer`
   Scans the user's natural-language description for cylinder-indicating
   keywords (Chinese and English) and stamps ``cylinder.type = "cylinder"``.

2. :class:`CylinderFlow2DDerivedFieldResolver`
   Derives ``diameter`` from ``radius`` (or vice-versa) and the
   ``characteristic_dimension`` using pure arithmetic.  Every derived value
   is tagged with the ``FORMULA_DERIVED`` provenance source so the audit
   trail remains intact and downstream validators can distinguish
   user-supplied values from computed ones.

Design contract
---------------
* The normalizer does **not** require ``bottom_profile`` to be present.
* The resolver **never** reports ``geometry_missing_type`` or
  ``geometry_missing_characteristic_dimension`` when the user has supplied
  a radius or diameter — those fields are always filled by derivation.
* Source priority is respected via
  :meth:`FieldSource.should_override`, so a user-explicit value is never
  clobbered by a formula-derived one.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    FieldSource,
    FieldStatus,
    ProvenanceField,
    SemanticBoundaryType,
)

__all__ = [
    "CylinderFlow2DGeometryNormalizer",
    "CylinderFlow2DDerivedFieldResolver",
]


# ---------------------------------------------------------------------------
# Keyword catalogue for cylinder identification
# ---------------------------------------------------------------------------

#: Phrases that indicate the user is describing a cylinder obstacle.
#: The list intentionally mixes Chinese and English to cover the common
#: phrasings observed in cylinder-flow experiment requests.  Matching is
#: case-insensitive (see :meth:`CylinderFlow2DGeometryNormalizer.normalize`).
_CYLINDER_CANDIDATE_WORDS: tuple[str, ...] = (
    "圆柱",
    "圆形障碍物",
    "圆形物体",
    "cylinder",
    "circular body",
    "circular obstacle",
)

#: Issue identifiers that must be cleared once radius/diameter is present.
_GEOMETRY_MISSING_TYPE = "geometry_missing_type"
_GEOMETRY_MISSING_CHAR_DIM = "geometry_missing_characteristic_dimension"


# ---------------------------------------------------------------------------
# CylinderFlow2DGeometryNormalizer
# ---------------------------------------------------------------------------


class CylinderFlow2DGeometryNormalizer:
    """Identify the cylinder obstacle from natural-language user text.

    This is a lightweight, keyword-based scanner — **no LLM** is involved.
    It looks for a curated set of Chinese and English phrases that users
    commonly employ when describing a cylinder-flow experiment.  When any
    candidate phrase is found, ``spec.cylinder.type`` is set to
    ``"cylinder"``.

    The normalizer deliberately does **not** require ``bottom_profile`` to
    be present; a flat bottom is a valid configuration for cylinder flow.
    """

    #: Read-only access to the candidate-word catalogue.
    candidate_words: tuple[str, ...] = _CYLINDER_CANDIDATE_WORDS

    def normalize(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
    ) -> CylinderFlow2DExperimentSpecV1:
        """Identify cylinder from ``user_text`` and set ``cylinder.type``.

        Parameters
        ----------
        spec:
            The current experiment specification (mutated in place and
            returned for convenience).
        user_text:
            The raw natural-language text supplied by the user.  May be
            empty or ``None``-equivalent, in which case the spec is
            returned unchanged.

        Returns
        -------
        CylinderFlow2DExperimentSpecV1
            The same *spec* instance, with ``cylinder.type`` set to
            ``"cylinder"`` if any candidate keyword was found.

        Notes
        -----
        * Matching is **case-insensitive** for the English phrases.
          Chinese phrases are unaffected by case folding.
        * Only the ``type`` attribute is touched — radius, diameter, and
          center coordinates are left for the LLM passes and the
          :class:`CylinderFlow2DDerivedFieldResolver`.
        * ``bottom_profile`` is never required; its absence does not block
          geometry identification.
        """
        if not user_text:
            return spec

        lowered_text = user_text.lower()
        for candidate in _CYLINDER_CANDIDATE_WORDS:
            if candidate.lower() in lowered_text:
                spec.cylinder.type = "cylinder"
                return spec

        return spec


# ---------------------------------------------------------------------------
# CylinderFlow2DDerivedFieldResolver
# ---------------------------------------------------------------------------


class CylinderFlow2DDerivedFieldResolver:
    """Deterministically derive cylinder geometry fields using code.

    All derivation is pure arithmetic — **no LLM calls**.  The resolver
    enforces three invariants:

    1. **Radius → diameter**: if the user supplied a radius,
       ``diameter = 2 * radius``.
    2. **Diameter → radius**: if the user supplied a diameter (and no
       radius), ``radius = diameter / 2``.
    3. **Characteristic dimension**: for a cylinder the characteristic
       dimension equals the diameter, so ``characteristic_dimension =
       diameter`` in all cases.

    Provenance rules
    ----------------
    * A user-supplied radius keeps its ``USER_EXPLICIT`` source
      (preserved, never downgraded).
    * Derived diameter, derived radius, and derived
      ``characteristic_dimension`` carry ``FORMULA_DERIVED``.
    * :meth:`FieldSource.should_override` is consulted before every write
      so a higher-priority user value is never clobbered.

    Safety guarantee
    ----------------
    When the user has provided a radius **or** a diameter, the resolver
    guarantees that neither ``geometry_missing_type`` nor
    ``geometry_missing_characteristic_dimension`` remains in
    ``spec.unresolved_fields`` or ``spec.blocking_issues``.
    """

    def resolve(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
    ) -> CylinderFlow2DExperimentSpecV1:
        """Derive diameter/radius and characteristic_dimension from user input.

        Parameters
        ----------
        spec:
            The current experiment specification (mutated in place and
            returned for convenience).

        Returns
        -------
        CylinderFlow2DExperimentSpecV1
            The same *spec* instance with derived fields populated.

        Derivation logic
        ----------------
        * **Radius provided** (``radius_m`` resolved):
            - ``diameter = radius * 2``  → ``FORMULA_DERIVED``
            - ``characteristic_dimension = diameter`` → ``FORMULA_DERIVED``
            - radius source stays ``USER_EXPLICIT`` (preserved).

        * **Diameter provided** (``diameter_m`` resolved, radius not):
            - ``radius = diameter / 2`` → ``FORMULA_DERIVED``
            - ``characteristic_dimension = diameter`` → ``FORMULA_DERIVED``
            - diameter source stays ``USER_EXPLICIT`` (preserved).

        * **Neither provided**: no derivation is performed; the fields
          remain in their current state.

        Side effects
        ------------
        * ``cylinder.type`` is set to ``"cylinder"`` (geometry type).
        * 2D ``front``/``back`` boundaries are verified to be ``empty``.
        * ``unresolved_fields`` and ``blocking_issues`` are scrubbed of
          ``geometry_missing_type`` and
          ``geometry_missing_characteristic_dimension`` when a radius or
          diameter is present.
        """
        cylinder = spec.cylinder

        # --- Invariant: cylinder type is always "cylinder" for this family.
        cylinder.type = "cylinder"

        radius_field: ProvenanceField = cylinder.radius_m
        diameter_field: ProvenanceField = cylinder.diameter_m
        char_dim_field: ProvenanceField = cylinder.characteristic_dimension_m

        radius_value = radius_field.value if radius_field.is_resolved() else None
        diameter_value = diameter_field.value if diameter_field.is_resolved() else None

        has_user_dimension = radius_value is not None or diameter_value is not None

        # ------------------------------------------------------------------
        # Case 1 — radius provided: derive diameter & characteristic dim
        # ------------------------------------------------------------------
        if radius_value is not None:
            r = float(radius_value)

            # Derive diameter = 2 * R  (respect source priority)
            if FieldSource.should_override(
                diameter_field.source, FieldSource.FORMULA_DERIVED
            ):
                cylinder.diameter_m = ProvenanceField(
                    value=r * 2.0,
                    source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason=f"Derived from radius ({r} m): D = 2R",
                )

            # characteristic_dimension = diameter (use the effective value)
            effective_diameter = cylinder.diameter_m.value
            if FieldSource.should_override(
                char_dim_field.source, FieldSource.FORMULA_DERIVED
            ):
                cylinder.characteristic_dimension_m = ProvenanceField(
                    value=effective_diameter,
                    source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason=(
                        f"Characteristic dimension = diameter "
                        f"({effective_diameter} m)"
                    ),
                )

        # ------------------------------------------------------------------
        # Case 2 — diameter provided (radius absent): derive radius
        # ------------------------------------------------------------------
        elif diameter_value is not None:
            d = float(diameter_value)

            # Derive radius = D / 2  (respect source priority)
            if FieldSource.should_override(
                radius_field.source, FieldSource.FORMULA_DERIVED
            ):
                cylinder.radius_m = ProvenanceField(
                    value=d / 2.0,
                    source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason=f"Derived from diameter ({d} m): R = D/2",
                )

            # characteristic_dimension = diameter
            if FieldSource.should_override(
                char_dim_field.source, FieldSource.FORMULA_DERIVED
            ):
                cylinder.characteristic_dimension_m = ProvenanceField(
                    value=d,
                    source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason=f"Characteristic dimension = diameter ({d} m)",
                )

        # ------------------------------------------------------------------
        # 2D boundary verification — front/back must be 'empty'
        # ------------------------------------------------------------------
        self._verify_2d_boundaries(spec)

        # ------------------------------------------------------------------
        # Scrub false "missing geometry" reports when a dimension exists
        # ------------------------------------------------------------------
        if has_user_dimension:
            self._scrub_missing_geometry_reports(spec)

        return spec

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_2d_boundaries(
        spec: CylinderFlow2DExperimentSpecV1,
    ) -> None:
        """Verify that ``front`` and ``back`` boundaries are ``empty``.

        The model-level ``enforce_2d_boundary`` validator already enforces
        this invariant, but the resolver performs a defensive re-check.
        If — for any reason — a downstream pass has altered the front or
        back boundary, it is restored to the 2D-mandated ``empty`` state
        with ``SYSTEM_DERIVED`` provenance.
        """
        for side in ("front", "back"):
            boundary = getattr(spec.boundaries, side)
            if boundary.semantic_type != SemanticBoundaryType.EMPTY:
                boundary.semantic_type = SemanticBoundaryType.EMPTY
                boundary.source = FieldSource.SYSTEM_DERIVED
                boundary.status = FieldStatus.RESOLVED
                boundary.confidence = 1.0

    @staticmethod
    def _scrub_missing_geometry_reports(
        spec: CylinderFlow2DExperimentSpecV1,
    ) -> None:
        """Remove stale ``geometry_missing_*`` entries from the spec.

        When the user has supplied a radius or diameter, the resolver
        guarantees that ``geometry_missing_type`` and
        ``geometry_missing_characteristic_dimension`` do not linger in
        ``unresolved_fields`` or ``blocking_issues``.

        Parameters
        ----------
        spec:
            The spec whose diagnostic lists will be cleaned in place.
        """
        stale_codes = {_GEOMETRY_MISSING_TYPE, _GEOMETRY_MISSING_CHAR_DIM}

        # --- unresolved_fields: list[str] ---------------------------------
        spec.unresolved_fields = [
            field
            for field in spec.unresolved_fields
            if field not in stale_codes
        ]

        # --- blocking_issues: list[dict[str, Any]] ------------------------
        # Each blocking issue is a dict; we check common key names for the
        # issue identifier so this stays robust across pipeline versions.
        id_keys = ("id", "code", "issue", "field", "type")
        filtered_issues: list[dict[str, Any]] = []
        for issue in spec.blocking_issues:
            issue_id: str | None = None
            for key in id_keys:
                val = issue.get(key)
                if isinstance(val, str):
                    issue_id = val
                    break
            if issue_id is not None and issue_id in stale_codes:
                continue  # drop stale report
            filtered_issues.append(issue)
        spec.blocking_issues = filtered_issues
