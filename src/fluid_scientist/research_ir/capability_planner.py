"""Capability Planner -- checks the research IR against the capability manifest.

Takes an :class:`~fluid_scientist.research_ir.models.OpenWorldResearchIR` and
a :class:`~fluid_scientist.research_ir.capability_manifest.CapabilityManifest`
and produces a :class:`CapabilityPlan` that identifies:

* **Supported** capabilities -- IR entities that the native compiler can
  handle out of the box.
* **Missing** capabilities -- IR entities that require a compiler extension
  before the case can be built.  Each missing capability carries a severity
  (``"blocking"`` or ``"warning"``) and, when available, an extension plan.
* **Needs clarification** -- IR entities whose specification is too vague
  (e.g. ``model="unknown"``) for the planner to make a determination.

When an ``llm_client`` is supplied, the planner can additionally call the
LLM with the ``capability_planner`` prompt to obtain extension suggestions
for missing capabilities, enriching the :class:`MissingCapability` entries
with concrete ``extension_plan`` text.

Typical usage::

    from fluid_scientist.research_ir.capability_planner import CapabilityPlanner
    from fluid_scientist.research_ir.capability_manifest import get_default_manifest

    planner = CapabilityPlanner(manifest=get_default_manifest())
    plan = planner.plan(ir)
    if plan.is_blocked:
        for mc in plan.missing:
            if mc.severity == "blocking":
                print(mc.capability_id, mc.extension_plan)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.research_ir.models import OpenWorldResearchIR
from fluid_scientist.research_ir.capability_manifest import (
    Capability,
    CapabilityManifest,
    get_default_manifest,
)
from fluid_scientist.research_ir.prompt_registry import PromptRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Prompt name used to load the system prompt from the registry.
_PROMPT_NAME = "capability_planner"

# Mapping from physical-quantity strings (Chinese or English) to observable
# capability IDs.  Keys are matched case-insensitively after stripping.
_OBSERVABLE_CAPABILITY_MAP: dict[str, str] = {
    # drag coefficient
    "阻力系数": "observable.drag_coefficient",
    "drag": "observable.drag_coefficient",
    "drag_coefficient": "observable.drag_coefficient",
    "cd": "observable.drag_coefficient",
    # lift coefficient
    "升力系数": "observable.lift_coefficient",
    "lift": "observable.lift_coefficient",
    "lift_coefficient": "observable.lift_coefficient",
    "cl": "observable.lift_coefficient",
    # Strouhal number / vortex shedding frequency
    "涡脱落频率": "observable.strouhal_number",
    "strouhal": "observable.strouhal_number",
    "strouhal_number": "observable.strouhal_number",
    "vortex_shedding_frequency": "observable.strouhal_number",
    # velocity field
    "速度场": "observable.velocity_field",
    "velocity field": "observable.velocity_field",
    "velocity_field": "observable.velocity_field",
    # pressure field
    "压力场": "observable.pressure_field",
    "pressure field": "observable.pressure_field",
    "pressure_field": "observable.pressure_field",
    # vorticity
    "涡量": "observable.vorticity",
    "vorticity": "observable.vorticity",
    # section mean velocity
    "截面平均流速": "observable.section_mean_velocity",
    "section mean velocity": "observable.section_mean_velocity",
    "section_mean_velocity": "observable.section_mean_velocity",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MissingCapability:
    """A capability that the IR requires but the manifest does not provide.

    Attributes:
        capability_id: The capability identifier that would be needed,
            following the ``{category}.{value}`` convention.
        category: One of ``"geometry"``, ``"material"``, ``"boundary"``,
            ``"physics"``, ``"observable"``.
        description: Human-readable explanation of what is missing.
        ir_reference: Which IR entity or field needs this capability,
            e.g. ``"GeometryEntity(entity_id='geo_1', semantic_shape='star')"``.
        severity: ``"blocking"`` if the case cannot proceed without this
            capability, or ``"warning"`` if it is non-critical.
        extension_plan: A suggested plan for adding the capability, or
            ``None`` when no plan has been generated yet.
    """

    capability_id: str
    category: str
    description: str
    ir_reference: str
    severity: str
    extension_plan: str | None = None


@dataclass
class CapabilityPlan:
    """The result of :meth:`CapabilityPlanner.plan`.

    Attributes:
        supported: Capability IDs that are natively supported by the
            manifest.
        missing: Capabilities needed by the IR but not available in the
            manifest.
        needs_clarification: Capability or entity identifiers that require
            user input before a determination can be made.
        is_blocked: ``True`` if any missing capability has
            ``severity == "blocking"``.
    """

    supported: list[str] = field(default_factory=list)
    missing: list[MissingCapability] = field(default_factory=list)
    needs_clarification: list[str] = field(default_factory=list)
    is_blocked: bool = False

    def to_dict(self) -> dict:
        """Serialise the plan to a plain dictionary."""
        return {
            "supported": list(self.supported),
            "missing": [mc.__dict__ if hasattr(mc, "__dict__") else dict(mc) for mc in self.missing]
            if self.missing
            else [],
            "needs_clarification": list(self.needs_clarification),
            "is_blocked": self.is_blocked,
        }


# ---------------------------------------------------------------------------
# CapabilityPlanner
# ---------------------------------------------------------------------------


class CapabilityPlanner:
    """Checks a research IR against the capability manifest.

    Args:
        manifest: The :class:`CapabilityManifest` to check against.  When
            ``None`` (default) the result of :func:`get_default_manifest`
            is used.
        llm_client: An optional LLM client with a ``call`` method.  When
            provided, the planner asks the LLM for extension suggestions
            for missing capabilities.
        prompt_registry: An optional :class:`PromptRegistry` for loading
            the system prompt.  When ``None`` (default), a new registry
            instance is created.
    """

    def __init__(
        self,
        manifest: CapabilityManifest | None = None,
        llm_client: Any | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self._manifest: CapabilityManifest = (
            manifest if manifest is not None else get_default_manifest()
        )
        self._llm_client = llm_client
        self._prompt_registry: PromptRegistry = (
            prompt_registry if prompt_registry is not None else PromptRegistry()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, ir: OpenWorldResearchIR) -> CapabilityPlan:
        """Produce a :class:`CapabilityPlan` for the given research IR.

        The method iterates over every geometry entity, material, boundary,
        physics model, and observable in the IR, checking each against the
        manifest.  When an LLM client is available and missing capabilities
        are found, the LLM is asked for extension suggestions which are
        attached to the :class:`MissingCapability` entries.

        Args:
            ir: The research intermediate representation to evaluate.

        Returns:
            A :class:`CapabilityPlan` summarising supported, missing, and
            needs-clarification items.
        """
        supported: list[str] = []
        missing: list[MissingCapability] = []
        needs_clarification: list[str] = []

        # a. Geometry entities
        for entity in ir.geometry_entities:
            self._check_geometry_entity(
                entity, supported, missing, needs_clarification
            )

        # b. Materials
        for material in ir.materials:
            self._check_material(material, supported, missing, needs_clarification)

        # c. Boundaries
        for boundary in ir.boundaries:
            self._check_boundary(boundary, supported, missing, needs_clarification)

        # d. Physics models
        for physics in ir.physics_models:
            self._check_physics_model(physics, supported, missing, needs_clarification)

        # e. Observables
        for observable in ir.observables:
            self._check_observable(observable, supported, missing, needs_clarification)

        # De-duplicate the supported list while preserving order.
        seen: set[str] = set()
        deduped_supported: list[str] = []
        for cap_id in supported:
            if cap_id not in seen:
                seen.add(cap_id)
                deduped_supported.append(cap_id)
        supported = deduped_supported

        # Determine whether the plan is blocked.
        is_blocked = any(mc.severity == "blocking" for mc in missing)

        plan = CapabilityPlan(
            supported=supported,
            missing=missing,
            needs_clarification=needs_clarification,
            is_blocked=is_blocked,
        )

        # LLM enhancement: ask for extension suggestions when there are
        # missing capabilities and an LLM client is available.
        if self._llm_client is not None and missing:
            self._enhance_with_llm(plan)

        return plan

    # ------------------------------------------------------------------
    # Per-category checks
    # ------------------------------------------------------------------

    def _check_geometry_entity(
        self,
        entity: Any,
        supported: list[str],
        missing: list[MissingCapability],
        needs_clarification: list[str],
    ) -> None:
        """Check a geometry entity's semantic_shape against the manifest."""
        semantic_shape = (
            entity.semantic_shape.strip().lower()
            if entity.semantic_shape
            else ""
        )

        # Unknown or empty shape -> needs clarification.
        if not semantic_shape or semantic_shape == "unknown":
            needs_clarification.append(
                f"geometry.{entity.entity_id}: semantic_shape is unknown"
            )
            return

        cap_id = f"geometry.{semantic_shape}"
        if self._manifest.has(cap_id):
            supported.append(cap_id)
            logger.debug(
                "Geometry entity '%s' supported (shape=%s)",
                entity.entity_id,
                semantic_shape,
            )
        else:
            missing.append(
                MissingCapability(
                    capability_id=cap_id,
                    category="geometry",
                    description=(
                        f"Geometry shape '{semantic_shape}' is not natively "
                        f"supported by the compiler."
                    ),
                    ir_reference=(
                        f"GeometryEntity(entity_id='{entity.entity_id}', "
                        f"semantic_shape='{semantic_shape}')"
                    ),
                    severity="blocking",
                    extension_plan=f"Add compiler hook for {semantic_shape}",
                )
            )
            logger.info(
                "Geometry entity '%s' missing capability: %s",
                entity.entity_id,
                cap_id,
            )

    def _check_material(
        self,
        material: Any,
        supported: list[str],
        missing: list[MissingCapability],
        needs_clarification: list[str],
    ) -> None:
        """Check a material's model and required properties against the manifest."""
        model = (
            material.model.strip().lower() if material.model else ""
        )

        # Unknown model -> needs clarification.
        if not model or model == "unknown":
            needs_clarification.append(
                f"material.{material.material_id}: model is unknown"
            )
            return

        cap_id = f"material.{model}"
        if self._manifest.has(cap_id):
            supported.append(cap_id)

            # Check required properties.
            cap = self._manifest.get(cap_id)
            required_props = cap.required_properties if cap else []
            provided_props = set(material.properties.keys()) if material.properties else set()
            missing_props = [
                p for p in required_props if p not in provided_props
            ]

            # Also honour any pre-tracked missing_required_properties.
            if hasattr(material, "missing_required_properties") and material.missing_required_properties:
                for p in material.missing_required_properties:
                    if p not in missing_props:
                        missing_props.append(p)

            if missing_props:
                needs_clarification.append(
                    f"material.{material.material_id}: "
                    f"missing required properties {missing_props}"
                )

            logger.debug(
                "Material '%s' supported (model=%s, missing_props=%s)",
                material.material_id,
                model,
                missing_props,
            )
        else:
            missing.append(
                MissingCapability(
                    capability_id=cap_id,
                    category="material",
                    description=(
                        f"Material model '{model}' is not natively supported "
                        f"by the compiler."
                    ),
                    ir_reference=(
                        f"MaterialIntent(material_id='{material.material_id}', "
                        f"model='{model}')"
                    ),
                    severity="blocking",
                    extension_plan=f"Add material model writer for {model}",
                )
            )
            logger.info(
                "Material '%s' missing capability: %s",
                material.material_id,
                cap_id,
            )

    def _check_boundary(
        self,
        boundary: Any,
        supported: list[str],
        missing: list[MissingCapability],
        needs_clarification: list[str],
    ) -> None:
        """Check a boundary's physical_role against the manifest."""
        role = (
            boundary.physical_role.strip().lower()
            if boundary.physical_role
            else ""
        )

        # Unknown role -> needs clarification.
        if not role or role == "unknown":
            needs_clarification.append(
                f"boundary.{boundary.boundary_id}: physical_role is unknown"
            )
            return

        cap_id = f"boundary.{role}"
        if self._manifest.has(cap_id):
            supported.append(cap_id)
            logger.debug(
                "Boundary '%s' supported (role=%s)",
                boundary.boundary_id,
                role,
            )
        else:
            missing.append(
                MissingCapability(
                    capability_id=cap_id,
                    category="boundary",
                    description=(
                        f"Boundary role '{role}' is not natively supported "
                        f"by the compiler."
                    ),
                    ir_reference=(
                        f"BoundaryIntent(boundary_id='{boundary.boundary_id}', "
                        f"physical_role='{role}')"
                    ),
                    severity="blocking",
                    extension_plan=f"Add boundary condition writer for {role}",
                )
            )
            logger.info(
                "Boundary '%s' missing capability: %s",
                boundary.boundary_id,
                cap_id,
            )

    def _check_physics_model(
        self,
        physics: Any,
        supported: list[str],
        missing: list[MissingCapability],
        needs_clarification: list[str],
    ) -> None:
        """Check a physics model's model_type against the manifest."""
        model_type = (
            physics.model_type.strip().lower()
            if physics.model_type
            else ""
        )

        # Unknown model_type -> needs clarification.
        if not model_type or model_type == "unknown":
            needs_clarification.append(
                f"physics.{physics.model_id}: model_type is unknown"
            )
            return

        cap_id = f"physics.{model_type}"
        if self._manifest.has(cap_id):
            supported.append(cap_id)
            logger.debug(
                "Physics model '%s' supported (type=%s)",
                physics.model_id,
                model_type,
            )
        else:
            # Known model type but not in manifest -> missing (blocking).
            missing.append(
                MissingCapability(
                    capability_id=cap_id,
                    category="physics",
                    description=(
                        f"Physics model type '{model_type}' is not natively "
                        f"supported by the compiler."
                    ),
                    ir_reference=(
                        f"PhysicsModelIntent(model_id='{physics.model_id}', "
                        f"model_type='{model_type}')"
                    ),
                    severity="blocking",
                    extension_plan=f"Add solver extension for {model_type}",
                )
            )
            logger.info(
                "Physics model '%s' missing capability: %s",
                physics.model_id,
                cap_id,
            )

    def _check_observable(
        self,
        observable: Any,
        supported: list[str],
        missing: list[MissingCapability],
        needs_clarification: list[str],
    ) -> None:
        """Check an observable's physical_quantity against the manifest."""
        physical_quantity = observable.physical_quantity or ""
        cap_id = self._resolve_observable_capability(physical_quantity)

        if cap_id is not None:
            if self._manifest.has(cap_id):
                supported.append(cap_id)
                logger.debug(
                    "Observable '%s' supported (quantity='%s' -> %s)",
                    observable.observable_id,
                    physical_quantity,
                    cap_id,
                )
            else:
                missing.append(
                    MissingCapability(
                        capability_id=cap_id,
                        category="observable",
                        description=(
                            f"Observable for physical quantity "
                            f"'{physical_quantity}' (capability '{cap_id}') "
                            f"is not natively supported."
                        ),
                        ir_reference=(
                            f"ObservableIntent(observable_id="
                            f"'{observable.observable_id}', "
                            f"physical_quantity='{physical_quantity}')"
                        ),
                        severity="warning",
                        extension_plan=(
                            f"Add post-processor for {cap_id}"
                        ),
                    )
                )
                logger.info(
                    "Observable '%s' missing capability: %s",
                    observable.observable_id,
                    cap_id,
                )
        else:
            # Unrecognised physical quantity -> warning (non-blocking).
            missing.append(
                MissingCapability(
                    capability_id=(
                        f"observable.{physical_quantity.lower().replace(' ', '_')}"
                        if physical_quantity
                        else "observable.unknown"
                    ),
                    category="observable",
                    description=(
                        f"Physical quantity '{physical_quantity}' does not "
                        f"map to a known observable capability."
                    ),
                    ir_reference=(
                        f"ObservableIntent(observable_id="
                        f"'{observable.observable_id}', "
                        f"physical_quantity='{physical_quantity}')"
                    ),
                    severity="warning",
                    extension_plan=None,
                )
            )
            logger.info(
                "Observable '%s': unmapped physical quantity '%s'",
                observable.observable_id,
                physical_quantity,
            )

    # ------------------------------------------------------------------
    # Observable resolution
    # ------------------------------------------------------------------

    def _resolve_observable_capability(
        self, physical_quantity: str
    ) -> str | None:
        """Map a physical-quantity string to an observable capability ID.

        Supports both Chinese and English terms.  Matching is
        case-insensitive and ignores leading/trailing whitespace.

        Args:
            physical_quantity: The raw physical-quantity string from an
                :class:`~fluid_scientist.research_ir.models.ObservableIntent`.

        Returns:
            The capability ID (e.g. ``"observable.drag_coefficient"``) or
            ``None`` when no mapping is found.
        """
        if not physical_quantity:
            return None

        key = physical_quantity.strip().lower()
        if key in _OBSERVABLE_CAPABILITY_MAP:
            return _OBSERVABLE_CAPABILITY_MAP[key]

        # Also try matching with underscores instead of spaces.
        key_underscored = key.replace(" ", "_")
        if key_underscored in _OBSERVABLE_CAPABILITY_MAP:
            return _OBSERVABLE_CAPABILITY_MAP[key_underscored]

        return None

    # ------------------------------------------------------------------
    # LLM enhancement
    # ------------------------------------------------------------------

    def _enhance_with_llm(self, plan: CapabilityPlan) -> None:
        """Ask the LLM for extension suggestions and attach them to missing entries.

        The LLM is called with the ``capability_planner`` prompt.  Its
        response is expected to contain an ``extension_suggestions`` list
        whose entries have ``capability_id`` and ``extension_plan`` fields.
        Each suggestion is matched to an existing :class:`MissingCapability`
        by ``capability_id`` and, when matched, updates its
        ``extension_plan``.
        """
        if not plan.missing:
            return

        system_prompt = self._load_system_prompt()
        user_message = self._build_user_message(plan)

        output_schema = {
            "type": "object",
            "properties": {
                "extension_suggestions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "capability_id": {"type": "string"},
                            "extension_plan": {"type": "string"},
                        },
                        "required": ["capability_id", "extension_plan"],
                    },
                },
            },
            "required": ["extension_suggestions"],
        }

        try:
            parsed, record = self._llm_client.call(
                purpose="capability_planning",
                prompt_name=_PROMPT_NAME,
                system_prompt=system_prompt,
                user_message=user_message,
                output_schema=output_schema,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM capability planning call failed: %s", exc)
            return

        if not getattr(record, "success", False):
            logger.warning(
                "LLM capability planning call unsuccessful: %s",
                getattr(record, "error", "unknown error"),
            )
            return

        if not isinstance(parsed, dict):
            logger.warning("LLM returned non-dict result; ignoring.")
            return

        suggestions = parsed.get("extension_suggestions", [])
        if not isinstance(suggestions, list):
            logger.warning("LLM 'extension_suggestions' is not a list; ignoring.")
            return

        # Build a lookup from capability_id -> extension_plan.
        suggestion_map: dict[str, str] = {}
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            cap_id = suggestion.get("capability_id")
            ext_plan = suggestion.get("extension_plan")
            if cap_id and ext_plan:
                suggestion_map[str(cap_id)] = str(ext_plan)

        if not suggestion_map:
            logger.debug("LLM returned no usable extension suggestions.")
            return

        # Apply suggestions to existing missing capabilities.
        updated_count = 0
        for mc in plan.missing:
            if mc.capability_id in suggestion_map:
                mc.extension_plan = suggestion_map[mc.capability_id]
                updated_count += 1

        logger.info(
            "LLM extension suggestions applied to %d/%d missing capabilities.",
            updated_count,
            len(plan.missing),
        )

    # ------------------------------------------------------------------
    # Prompt / message helpers
    # ------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        """Load the system prompt from the registry.

        Falls back to a minimal inline prompt when the prompt file is not
        found in the registry.
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
            "You are a CFD capability planner.  Given a list of missing "
            "capabilities from a research IR, suggest concrete extension "
            "plans for each one.\n\n"
            "For each missing capability, describe how to add it to the "
            "compiler (e.g. which hook to implement, which writer to add, "
            "or which configuration to extend).\n\n"
            "Return a JSON object with an 'extension_suggestions' array.  "
            "Each element must have 'capability_id' and 'extension_plan' "
            "string fields."
        )

    @staticmethod
    def _build_user_message(plan: CapabilityPlan) -> str:
        """Build the user message describing the missing capabilities."""
        parts: list[str] = []
        parts.append("## Missing Capabilities")
        parts.append("")

        for i, mc in enumerate(plan.missing, start=1):
            parts.append(f"{i}. capability_id: {mc.capability_id}")
            parts.append(f"   category: {mc.category}")
            parts.append(f"   description: {mc.description}")
            parts.append(f"   ir_reference: {mc.ir_reference}")
            parts.append(f"   severity: {mc.severity}")
            if mc.extension_plan:
                parts.append(f"   current_extension_plan: {mc.extension_plan}")
            else:
                parts.append("   current_extension_plan: (none)")
            parts.append("")

        parts.append(
            "请为每个缺失的能力提供一个具体的扩展计划。返回 JSON：\n"
            '{"extension_suggestions": ['
            '{"capability_id": "...", "extension_plan": "..."}, ...]}'
        )

        return "\n".join(parts)


__all__ = [
    "CapabilityPlan",
    "CapabilityPlanner",
    "MissingCapability",
]
