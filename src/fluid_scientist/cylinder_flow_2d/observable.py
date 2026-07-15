"""Observable extraction, recommendation, and validation for CylinderFlow2D.

This module provides three components that work together to manage the
observables list on a :class:`CylinderFlow2DExperimentSpecV1`:

* :class:`CylinderFlow2DObservableExtractor` -- parses free-text user input
  and produces *user-explicit* observable candidates
  (``source = USER_EXPLICIT``).
* :class:`CylinderFlow2DObservableRecommender` -- when the user has not
  specified any observables, proposes a domain-appropriate set
  (``source = MODEL_RECOMMENDED``).
* :class:`CylinderFlow2DObservableValidator` -- inspects every observable for
  missing required fields and updates its ``status`` / ``missing_fields``.

Design rules (from the spec provenance hierarchy):

- User-explicit observables are **never** cleared by the recommender.
- Model recommendations never override user-explicit values.
- The observable list is **never** empty after recommendation.
"""

from __future__ import annotations

import re

from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    ObservableSpec,
    ObservableType,
    FieldSource,
    FieldStatus,
)


# ---------------------------------------------------------------------------
# ObservableExtractor
# ---------------------------------------------------------------------------


class CylinderFlow2DObservableExtractor:
    """Extract user-explicit observables from free-text descriptions.

    Recognised patterns (English keywords are matched case-insensitively):

    ================= =============================================== ============================ ========================
    Text pattern       Observable type                                 Status                       Missing fields
    ================= =============================================== ============================ ========================
    "某点平均流速"       POINT_VELOCITY                                  PARTIALLY_RESOLVED           ["point"]
    "某截面平均流速"     SECTION_MEAN_VELOCITY                           PARTIALLY_RESOLVED           ["section_x"]
    "点或截面"           BOTH POINT_VELOCITY and SECTION_MEAN_VELOCITY   PARTIALLY_RESOLVED (each)    ["point"] / ["section_x"]
    "圆柱阻力"           CYLINDER_DRAG                                   RESOLVED                     []
    "圆柱升力"           CYLINDER_LIFT                                   RESOLVED                     []
    ================= =============================================== ============================ ========================

    All extracted observables carry ``source = USER_EXPLICIT`` and are
    therefore never cleared by the recommender.
    """

    # Cylinder-context keywords (Chinese + English)
    _CYLINDER_KEYWORDS: tuple[str, ...] = ("圆柱", "柱体", "cylinder")
    # Force keywords
    _DRAG_KEYWORDS: tuple[str, ...] = ("阻力", "drag", "cd")
    _LIFT_KEYWORDS: tuple[str, ...] = ("升力", "lift", "cl")

    def extract(self, text: str | None) -> list[ObservableSpec]:
        """Extract user-explicit observables from *text*.

        Returns a de-duplicated list of :class:`ObservableSpec` instances
        with ``source = USER_EXPLICIT``.  Returns an empty list when *text*
        is empty or contains no recognisable patterns.
        """
        if not text:
            return []

        text_lower = text.lower()
        observables: list[ObservableSpec] = []
        seen: set[ObservableType] = set()

        # --- Point / Section velocity ------------------------------------
        # Skill pack patterns: 点.*平均.*流速, 某点.*流速, 点速度,
        #   截面.*平均.*流速, 断面.*平均.*流速, 截面流速
        has_point = bool(
            re.search(r"某点.*流速|点速度|点.*平均.*流速|point.*velocity", text_lower)
        )
        has_section = bool(
            re.search(r"截面.*流速|断面.*流速|截面平均|断面平均|section.*velocity", text_lower)
        )
        # "点或截面" — the user mentions both point and section connected by
        # "或" (or), signalling ambiguity.  In this case we keep BOTH
        # candidates rather than clearing either one.
        has_or_ambiguous = (
            "或" in text and "点" in text and ("截面" in text or "断面" in text)
        )

        if has_or_ambiguous:
            self._add_if_new(observables, seen, self._make_point_velocity())
            self._add_if_new(observables, seen, self._make_section_velocity())
        else:
            if has_point:
                self._add_if_new(observables, seen, self._make_point_velocity())
            if has_section:
                self._add_if_new(observables, seen, self._make_section_velocity())

        # --- Cylinder forces ---------------------------------------------
        # Skill pack: "阻力"/"升力" without requiring cylinder keyword
        has_cylinder = any(kw in text_lower for kw in self._CYLINDER_KEYWORDS)
        has_drag = any(kw in text_lower for kw in self._DRAG_KEYWORDS)
        has_lift = any(kw in text_lower for kw in self._LIFT_KEYWORDS)

        # Match skill pack: detect drag/lift even without explicit cylinder keyword
        # (since this pipeline is already cylinder-flow specific)
        if has_drag:
            self._add_if_new(observables, seen, self._make_cylinder_drag())
        if has_lift:
            self._add_if_new(observables, seen, self._make_cylinder_lift())

        # --- Wake shedding frequency -------------------------------------
        # Skill pack: "频率" or "涡脱落" → wake_shedding_frequency
        if "频率" in text or "涡脱落" in text or "vortex shedding" in text_lower or "shedding frequency" in text_lower:
            self._add_if_new(observables, seen, self._make_shedding_frequency())

        return observables

    # -- internals --------------------------------------------------------

    @staticmethod
    def _add_if_new(
        observables: list[ObservableSpec],
        seen: set[ObservableType],
        obs: ObservableSpec,
    ) -> None:
        """Append *obs* unless its type was already added."""
        if obs.type not in seen:
            observables.append(obs)
            seen.add(obs.type)

    # -- factory helpers --------------------------------------------------

    @staticmethod
    def _make_point_velocity() -> ObservableSpec:
        """Create a PARTIALLY_RESOLVED point-velocity observable."""
        return ObservableSpec(
            type=ObservableType.POINT_VELOCITY,
            label="某点平均流速",
            component="Ux",
            spatial_operation="point",
            temporal_operation="mean",
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.PARTIALLY_RESOLVED,
            missing_fields=["point"],
            confidence=0.7,
        )

    @staticmethod
    def _make_section_velocity() -> ObservableSpec:
        """Create a PARTIALLY_RESOLVED section-mean-velocity observable."""
        return ObservableSpec(
            type=ObservableType.SECTION_MEAN_VELOCITY,
            label="某截面平均流速",
            component="Ux",
            spatial_operation="section_mean",
            temporal_operation="mean",
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.PARTIALLY_RESOLVED,
            missing_fields=["section_x"],
            confidence=0.7,
        )

    @staticmethod
    def _make_cylinder_drag() -> ObservableSpec:
        """Create a RESOLVED cylinder-drag observable."""
        return ObservableSpec(
            type=ObservableType.CYLINDER_DRAG,
            label="圆柱阻力",
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            missing_fields=[],
            confidence=0.9,
        )

    @staticmethod
    def _make_cylinder_lift() -> ObservableSpec:
        """Create a RESOLVED cylinder-lift observable."""
        return ObservableSpec(
            type=ObservableType.CYLINDER_LIFT,
            label="圆柱升力",
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            missing_fields=[],
            confidence=0.9,
        )

    @staticmethod
    def _make_shedding_frequency() -> ObservableSpec:
        """Create a RESOLVED wake-shedding-frequency observable."""
        return ObservableSpec(
            type=ObservableType.WAKE_SHEDDING_FREQUENCY,
            label="涡脱落频率",
            source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED,
            missing_fields=[],
            confidence=0.9,
        )


# ---------------------------------------------------------------------------
# ObservableRecommender
# ---------------------------------------------------------------------------


class CylinderFlow2DObservableRecommender:
    """Recommend observables when the user has not specified any.

    Recommendations are context-aware:

    * **With a cylinder** -- drag, lift, downstream point velocity, section
      mean velocity, velocity-magnitude field, pressure field, vorticity
      field, and streamlines.
    * **Transient simulations** -- additionally drag/lift time series and
      wake-shedding frequency.

    All recommended observables carry ``source = MODEL_RECOMMENDED`` and
    ``status = AWAITING_CONFIRMATION``.

    User-explicit observables are **never** cleared.  When the user has
    already specified observables, the recommender returns them unchanged.
    The result is **never** an empty list.
    """

    def recommend(self, spec: CylinderFlow2DExperimentSpecV1) -> list[ObservableSpec]:
        """Return observables for *spec*, respecting user-explicit values.

        If the user has already supplied observables (``USER_EXPLICIT`` or
        ``USER_CONFIRMED``), they are returned as-is -- the recommender
        never clears user-explicit entries.

        Otherwise a context-appropriate recommendation set is built from
        the spec configuration.  The returned list is guaranteed to be
        non-empty.
        """
        # Preserve user-explicit observables -- never clear them.
        user_observables = [
            obs for obs in spec.observables
            if obs.source in (FieldSource.USER_EXPLICIT, FieldSource.USER_CONFIRMED)
        ]
        if user_observables:
            return list(user_observables)

        # User hasn't specified -- build recommendations.
        recommendations: list[ObservableSpec] = []

        if spec.has_cylinder:
            recommendations.extend(self._cylinder_recommendations())

        if spec.is_transient:
            recommendations.extend(self._transient_recommendations())

        # NEVER return an empty list -- fall back to basic field observables
        # when neither cylinder nor transient recommendations apply.
        if not recommendations:
            recommendations.extend(self._basic_field_recommendations())

        return recommendations

    # -- recommendation sets ---------------------------------------------

    @staticmethod
    def _cylinder_recommendations() -> list[ObservableSpec]:
        """Observables recommended when a cylinder is present."""
        return [
            ObservableSpec(
                type=ObservableType.CYLINDER_DRAG,
                label="圆柱阻力",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.6,
            ),
            ObservableSpec(
                type=ObservableType.CYLINDER_LIFT,
                label="圆柱升力",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.6,
            ),
            ObservableSpec(
                type=ObservableType.POINT_VELOCITY,
                label="下游监测点流速",
                component="Ux",
                spatial_operation="point",
                temporal_operation="mean",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.5,
            ),
            ObservableSpec(
                type=ObservableType.SECTION_MEAN_VELOCITY,
                label="截面平均流速",
                component="Ux",
                spatial_operation="section_mean",
                temporal_operation="mean",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.5,
            ),
            ObservableSpec(
                type=ObservableType.VELOCITY_MAGNITUDE_FIELD,
                label="速度幅值场",
                component="magnitude",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.6,
            ),
            ObservableSpec(
                type=ObservableType.PRESSURE_FIELD,
                label="压力场",
                component="magnitude",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.6,
            ),
            ObservableSpec(
                type=ObservableType.VORTICITY_FIELD,
                label="涡量场",
                component="magnitude",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.6,
            ),
            ObservableSpec(
                type=ObservableType.STREAMLINES,
                label="流线",
                component="magnitude",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.6,
            ),
        ]

    @staticmethod
    def _transient_recommendations() -> list[ObservableSpec]:
        """Additional observables recommended for transient simulations."""
        return [
            ObservableSpec(
                type=ObservableType.DRAG_LIFT_TIME_SERIES,
                label="阻力升力时间序列",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.6,
            ),
            ObservableSpec(
                type=ObservableType.WAKE_SHEDDING_FREQUENCY,
                label="尾迹涡脱落频率",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.6,
            ),
        ]

    @staticmethod
    def _basic_field_recommendations() -> list[ObservableSpec]:
        """Minimal observable set guaranteed to be non-empty.

        Used as a fallback when no cylinder or transient recommendations
        apply, ensuring the observable list is never empty.
        """
        return [
            ObservableSpec(
                type=ObservableType.VELOCITY_MAGNITUDE_FIELD,
                label="速度幅值场",
                component="magnitude",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.5,
            ),
            ObservableSpec(
                type=ObservableType.PRESSURE_FIELD,
                label="压力场",
                component="magnitude",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.5,
            ),
            ObservableSpec(
                type=ObservableType.VORTICITY_FIELD,
                label="涡量场",
                component="magnitude",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.5,
            ),
            ObservableSpec(
                type=ObservableType.STREAMLINES,
                label="流线",
                component="magnitude",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.5,
            ),
        ]


# ---------------------------------------------------------------------------
# ObservableValidator
# ---------------------------------------------------------------------------


class CylinderFlow2DObservableValidator:
    """Validate observables by checking for missing required fields.

    For each observable the validator:

    1. Looks up the required fields for the observable type.
    2. Checks whether those fields are populated (non-``None``).
    3. Updates ``status`` and ``missing_fields`` accordingly:

       * Missing fields          -> ``PARTIALLY_RESOLVED``
       * No missing + user source -> ``RESOLVED``
       * No missing + model source -> ``AWAITING_CONFIRMATION``
    """

    #: Required fields per observable type.  Types not listed here have no
    #: required fields and are considered complete.
    _REQUIRED_FIELDS: dict[ObservableType, list[str]] = {
        ObservableType.POINT_VELOCITY: ["point"],
        ObservableType.SECTION_MEAN_VELOCITY: ["section_x"],
        ObservableType.SECTION_FLOW_RATE: ["section_x"],
        ObservableType.WALL_SHEAR_STRESS: ["wall_name"],
    }

    _USER_SOURCES: frozenset[FieldSource] = frozenset({
        FieldSource.USER_EXPLICIT,
        FieldSource.USER_CONFIRMED,
    })

    def validate(self, observables: list[ObservableSpec]) -> list[ObservableSpec]:
        """Return a new list of observables with updated status and missing_fields.

        The original observables are not mutated; each returned entry is a
        shallow copy (via ``model_copy``) with potentially updated
        ``status`` and ``missing_fields``.
        """
        validated: list[ObservableSpec] = []
        for obs in observables:
            required = self._REQUIRED_FIELDS.get(obs.type, [])
            missing = [f for f in required if getattr(obs, f, None) is None]

            if missing:
                new_status = FieldStatus.PARTIALLY_RESOLVED
            elif obs.source in self._USER_SOURCES:
                new_status = FieldStatus.RESOLVED
            else:
                new_status = FieldStatus.AWAITING_CONFIRMATION

            validated.append(
                obs.model_copy(update={
                    "status": new_status,
                    "missing_fields": missing,
                })
            )
        return validated


__all__ = [
    "CylinderFlow2DObservableExtractor",
    "CylinderFlow2DObservableRecommender",
    "CylinderFlow2DObservableValidator",
]
