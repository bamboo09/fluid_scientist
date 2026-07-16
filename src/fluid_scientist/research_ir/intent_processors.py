"""Intent processors for materials and boundaries in the open-world IR.

Provides two rule-based processors that enrich
:class:`~fluid_scientist.research_ir.models.MaterialIntent` and
:class:`~fluid_scientist.research_ir.models.BoundaryIntent` objects
*without* any LLM dependency.  Both processors infer missing semantic
fields from user-supplied raw text/names and validate that all required
physical quantities are present, setting ``capability_status`` and
``semantic_status`` fields so downstream capability mapping and
clarification loops can consume the results.

Typical usage::

    from fluid_scientist.research_ir.intent_processors import (
        MaterialProcessor,
        BoundaryProcessor,
    )

    mat_proc = MaterialProcessor()
    materials = mat_proc.process(ir.materials)

    bnd_proc = BoundaryProcessor()
    boundaries = bnd_proc.process(ir.boundaries, domain_dim="2D")
"""

from __future__ import annotations

import logging

from fluid_scientist.research_ir.models import (
    BoundaryIntent,
    MaterialIntent,
    ParameterValue,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Material inference rules
# ---------------------------------------------------------------------------

#: Inference rules evaluated in declaration order.  Each entry is a tuple of
#: ``(keywords, phase, model)``.  The first keyword (Chinese or English) that
#: appears as a substring of the lower-cased ``raw_name`` wins.
_MATERIAL_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("水", "water"), "liquid", "incompressible_newtonian"),
    (("空气", "air"), "gas", "compressible_newtonian"),
    (("油", "oil"), "liquid", "incompressible_newtonian"),
]

#: Required physical properties per supported physics model.  Models absent
#: from this mapping are treated as having no required properties.
_REQUIRED_PROPERTIES: dict[str, list[str]] = {
    "incompressible_newtonian": ["density", "viscosity"],
    "compressible_newtonian": [
        "density",
        "viscosity",
        "gas_constant",
        "specific_heat_ratio",
    ],
}

#: The set of model names for which rule-based inference and property
#: validation are defined.  Used as the default ``supported_models`` set
#: when enriching materials inside :meth:`MaterialProcessor.process`.
_KNOWN_MATERIAL_MODELS: frozenset[str] = frozenset(_REQUIRED_PROPERTIES.keys())

# ---------------------------------------------------------------------------
# Boundary inference rules
# ---------------------------------------------------------------------------

#: Inference rules evaluated in declaration order.  Each entry is a tuple of
#: ``(keywords, physical_role, required_quantities)``.  The first keyword
#: (Chinese or English) that appears as a substring of the lower-cased
#: ``raw_text`` wins.
#:
#: .. note::
#:    ``"no-slip"`` **must** be listed before ``"slip"`` so that text such
#:    as ``"no-slip wall"`` is not misread as a slip wall (``"slip"`` is a
#:    substring of ``"no-slip"``).
_BOUNDARY_RULES: list[tuple[tuple[str, ...], str, list[str]]] = [
    (("速度入口", "velocity inlet"), "velocity_inlet", ["velocity"]),
    (("压力出口", "pressure outlet"), "pressure_outlet", ["pressure"]),
    (("无滑移", "no-slip"), "no_slip_wall", []),
    (("自由滑移", "slip"), "slip_wall", []),
    (("周期", "periodic"), "periodic", []),
    (("对称", "symmetry"), "symmetry", []),
    (("切向应力", "shear stress"), "shear_stress", ["shear_stress"]),
    (("质量流量入口", "mass flow inlet"), "mass_flow_inlet", ["mass_flow_rate"]),
]

#: Required quantities per physical role, derived from :data:`_BOUNDARY_RULES`.
#: Roles absent from this mapping (e.g. ``"unknown"``) are treated as having
#: no required quantities.
_ROLE_REQUIRED: dict[str, list[str]] = {
    role: list(required) for _, role, required in _BOUNDARY_RULES
}

#: The set of physical roles for which rule-based inference is defined.
_KNOWN_BOUNDARY_ROLES: frozenset[str] = frozenset(_ROLE_REQUIRED.keys())


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _param_has_value(pv: ParameterValue | None) -> bool:
    """Return ``True`` when *pv* is not ``None`` and carries a value.

    A :class:`ParameterValue` whose ``value`` is ``None`` is treated as
    *absent* because it carries no usable numeric or string data for
    simulation.  A value of ``0`` (falsy but valid) is considered present.
    """
    return pv is not None and pv.value is not None


# ---------------------------------------------------------------------------
# MaterialProcessor
# ---------------------------------------------------------------------------


class MaterialProcessor:
    """Rule-based enrichment of :class:`MaterialIntent` objects.

    Infers the fluid ``phase`` and physics ``model`` from the material
    ``raw_name`` and validates that all required physical properties are
    present.  The processor is entirely deterministic and requires **no**
    LLM client -- it can run as a standalone, offline enrichment stage.

    Enrichment steps performed by :meth:`process`:

    1. **Phase inference** -- when ``phase`` is ``"unknown"``, it is
       inferred from ``raw_name`` via :meth:`_infer_phase`.
    2. **Model inference** -- when ``model`` is ``"unknown"``, it is
       inferred from ``raw_name`` (and available ``properties``) via
       :meth:`_infer_model`.
    3. **Property validation** -- :meth:`_check_required_properties`
       computes the list of missing required properties and stores it in
       ``material.missing_required_properties``.
    4. **Capability assessment** -- :meth:`check_capabilities` sets
       ``material.capability_status`` to ``"supported"``,
       ``"needs_properties"``, or ``"unsupported"``.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, materials: list[MaterialIntent]) -> list[MaterialIntent]:
        """Enrich a list of material intents in place and return them.

        Each material is enriched independently.  Fields that were already
        set by an upstream stage (e.g. ``phase`` or ``model`` set by the
        LLM extractor) are preserved -- inference only fills in
        ``"unknown"`` values.

        Args:
            materials: The material intents to enrich.

        Returns:
            The same list object (modified in place) for convenience.
        """
        for material in materials:
            self._enrich(material)
        logger.info(
            "MaterialProcessor enriched %d material(s).",
            len(materials),
        )
        return materials

    def check_capabilities(
        self,
        material: MaterialIntent,
        supported_models: set[str],
    ) -> str:
        """Assess the capability status of *material*.

        Args:
            material: The material intent to assess.
            supported_models: The set of physics model names that the
                target solver/backend supports.

        Returns:
            ``"supported"`` -- the model is in *supported_models* and all
            required properties are present.

            ``"needs_properties"`` -- the model is in *supported_models*
            but one or more required properties are missing.

            ``"unsupported"`` -- the model is not in *supported_models*
            (this includes ``"unknown"`` and ``"custom"``).
        """
        if material.model not in supported_models:
            return "unsupported"
        missing = self._check_required_properties(material)
        if missing:
            return "needs_properties"
        return "supported"

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _infer_phase(self, raw_name: str) -> str:
        """Infer the fluid phase from *raw_name*.

        Matching is case-insensitive for English keywords.  The first
        matching rule in :data:`_MATERIAL_RULES` wins.

        Args:
            raw_name: The raw material name supplied by the user or LLM.

        Returns:
            One of ``"liquid"``, ``"gas"``, or ``"unknown"``.
        """
        name = (raw_name or "").lower()
        for keywords, phase, _ in _MATERIAL_RULES:
            for kw in keywords:
                if kw in name:
                    return phase
        return "unknown"

    def _infer_model(
        self,
        raw_name: str,
        properties: dict,
    ) -> str:
        """Infer the physics model from *raw_name* and *properties*.

        Primary inference is keyword-based on *raw_name* (case-insensitive
        for English).  When no keyword matches, a secondary heuristic
        inspects *properties*: if both ``gas_constant`` and
        ``specific_heat_ratio`` are present the model is inferred as
        ``"compressible_newtonian"``; if both ``density`` and
        ``viscosity`` are present it is inferred as
        ``"incompressible_newtonian"``.

        Args:
            raw_name: The raw material name.
            properties: The material's current property mapping
                (``dict[str, ParameterValue]``).

        Returns:
            One of the model literals or ``"unknown"`` when no rule
            matches.
        """
        name = (raw_name or "").lower()
        for keywords, _, model in _MATERIAL_RULES:
            for kw in keywords:
                if kw in name:
                    return model

        # Secondary signal: infer from available properties when the name
        # gave no clue.
        if properties:
            prop_keys = set(properties.keys())
            if {"gas_constant", "specific_heat_ratio"}.issubset(prop_keys):
                return "compressible_newtonian"
            if {"density", "viscosity"}.issubset(prop_keys):
                return "incompressible_newtonian"

        return "unknown"

    def _check_required_properties(
        self,
        material: MaterialIntent,
    ) -> list[str]:
        """Return the list of required properties missing from *material*.

        A property counts as *missing* when it is absent from
        ``material.properties`` **or** when its :class:`ParameterValue`
        carries a ``None`` value (i.e. present but empty).

        Args:
            material: The material intent to check.

        Returns:
            A list of missing required property names.  When the model has
            no entry in :data:`_REQUIRED_PROPERTIES` an empty list is
            returned (no required properties defined).
        """
        required = _REQUIRED_PROPERTIES.get(material.model, [])
        missing: list[str] = []
        for prop in required:
            pv = material.properties.get(prop)
            if not _param_has_value(pv):
                missing.append(prop)
        return missing

    # ------------------------------------------------------------------
    # Internal enrichment
    # ------------------------------------------------------------------

    def _enrich(self, material: MaterialIntent) -> None:
        """Enrich a single material intent in place."""
        raw_name = material.raw_name or ""

        # Step 1: infer phase when unknown.
        if material.phase == "unknown":
            material.phase = self._infer_phase(raw_name)  # type: ignore[assignment]

        # Step 2: infer model when unknown.
        if material.model == "unknown":
            material.model = self._infer_model(  # type: ignore[assignment]
                raw_name, material.properties
            )

        # Step 3: compute missing required properties.
        material.missing_required_properties = self._check_required_properties(
            material
        )

        # Step 4: assess capability status.
        material.capability_status = self.check_capabilities(
            material, set(_KNOWN_MATERIAL_MODELS)
        )

        logger.debug(
            "Material '%s' enriched: phase=%s, model=%s, "
            "missing=%s, capability=%s",
            material.material_id,
            material.phase,
            material.model,
            material.missing_required_properties,
            material.capability_status,
        )


# ---------------------------------------------------------------------------
# BoundaryProcessor
# ---------------------------------------------------------------------------


class BoundaryProcessor:
    """Rule-based enrichment of :class:`BoundaryIntent` objects.

    Infers the ``physical_role`` from boundary ``raw_text`` and validates
    that all required physical quantities are present.  The processor is
    entirely deterministic and requires **no** LLM client.

    Enrichment steps performed by :meth:`process`:

    1. **Role inference** -- when ``physical_role`` is ``"unknown"``, it
       is inferred from ``raw_text`` via :meth:`_infer_physical_role`.
    2. **Quantity validation** -- :meth:`_validate_quantities` computes
       the list of missing required quantities for the (possibly inferred)
       role.
    3. **Semantic status** -- ``semantic_status`` is set to
       ``"resolved"`` (role known, all quantities present),
       ``"incomplete"`` (role known but quantities missing), or
       ``"needs_clarification"`` (role unknown).
    4. **Capability status** -- ``capability_status`` is set to
       ``"supported"`` for known roles or ``"unsupported"`` for unknown
       roles.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        boundaries: list[BoundaryIntent],
        domain_dim: str,
    ) -> list[BoundaryIntent]:
        """Enrich a list of boundary intents in place and return them.

        Each boundary is enriched independently.  Fields that were already
        set by an upstream stage are preserved -- inference only fills in
        ``"unknown"`` values.

        Args:
            boundaries: The boundary intents to enrich.
            domain_dim: The domain dimensionality (``"2D"``, ``"3D"``,
                ``"axisymmetric"``, or ``"unknown"``).  Reserved for
                future dimension-aware enrichment; the current
                rule-based implementation does not alter behaviour based
                on this value but accepts it for API completeness.

        Returns:
            The same list object (modified in place) for convenience.
        """
        for boundary in boundaries:
            self._enrich(boundary, domain_dim)
        logger.info(
            "BoundaryProcessor enriched %d boundary/boundaries (dim=%s).",
            len(boundaries),
            domain_dim,
        )
        return boundaries

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _infer_physical_role(self, raw_text: str) -> str:
        """Infer the physical boundary role from *raw_text*.

        Matching is case-insensitive for English keywords.  The first
        matching rule in :data:`_BOUNDARY_RULES` wins.  Order matters:
        ``"no-slip"`` is checked before ``"slip"`` to prevent
        ``"no-slip wall"`` from being misclassified as a slip wall.

        Args:
            raw_text: The raw boundary description supplied by the user
                or LLM.

        Returns:
            One of the ``physical_role`` literals, or ``"unknown"`` when
            no rule matches.
        """
        text = (raw_text or "").lower()
        for keywords, role, _ in _BOUNDARY_RULES:
            for kw in keywords:
                if kw in text:
                    return role
        return "unknown"

    def _validate_quantities(
        self,
        boundary: BoundaryIntent,
        role: str,
    ) -> list[str]:
        """Return the list of required quantities missing from *boundary*.

        A quantity counts as *missing* when it is absent from
        ``boundary.quantities`` **or** when its :class:`ParameterValue`
        carries a ``None`` value.

        Args:
            boundary: The boundary intent to check.
            role: The physical role whose required quantities should be
                validated.

        Returns:
            A list of missing required quantity names.  When the role has
            no entry in :data:`_ROLE_REQUIRED` an empty list is returned.
        """
        required = _ROLE_REQUIRED.get(role, [])
        missing: list[str] = []
        for qty in required:
            pv = boundary.quantities.get(qty)
            if not _param_has_value(pv):
                missing.append(qty)
        return missing

    # ------------------------------------------------------------------
    # Internal enrichment
    # ------------------------------------------------------------------

    def _enrich(
        self,
        boundary: BoundaryIntent,
        domain_dim: str,
    ) -> None:
        """Enrich a single boundary intent in place."""
        raw_text = boundary.raw_text or ""

        # Step 1: infer physical role when unknown.
        if boundary.physical_role == "unknown":
            boundary.physical_role = self._infer_physical_role(  # type: ignore[assignment]
                raw_text
            )

        role = boundary.physical_role

        # Step 2: validate required quantities.
        missing = self._validate_quantities(boundary, role)

        # Step 3: set semantic status.
        if role == "unknown":
            boundary.semantic_status = "needs_clarification"
        elif missing:
            boundary.semantic_status = "incomplete"
        else:
            boundary.semantic_status = "resolved"

        # Step 4: set capability status.
        boundary.capability_status = (
            "supported" if role in _KNOWN_BOUNDARY_ROLES else "unsupported"
        )

        logger.debug(
            "Boundary '%s' enriched: role=%s, missing=%s, "
            "semantic=%s, capability=%s",
            boundary.boundary_id,
            role,
            missing,
            boundary.semantic_status,
            boundary.capability_status,
        )


__all__ = [
    "MaterialProcessor",
    "BoundaryProcessor",
]
