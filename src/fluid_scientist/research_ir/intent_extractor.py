"""Open-world intent extractor.

Parses free-form user research text into a canonical
:class:`~fluid_scientist.research_ir.models.OpenWorldResearchIR` by
delegating the heavy lifting to an LLM driven by the
``open_world_intent_extractor`` prompt template.

The extractor is intentionally *open-world*: it never discards user
mentions, never maps unknown concepts onto known templates, and always
records the full mention inventory so that downstream coverage guards
can verify that every user mention has been accounted for.

Typical usage::

    from fluid_scientist.research_ir.intent_extractor import (
        OpenWorldIntentExtractor,
    )
    from fluid_scientist.llm.client import LLMClient

    extractor = OpenWorldIntentExtractor(llm_client=LLMClient())
    ir = extractor.extract("2D flow past a cylinder at Re=100")
    print(ir.source_coverage.coverage_ratio)
"""

from __future__ import annotations

import logging
from typing import Any

from fluid_scientist.research_ir.models import (
    Mention,
    MentionInventory,
    OpenWorldResearchIR,
    UnresolvedMention,
)
from fluid_scientist.research_ir.prompt_registry import PromptRegistry

logger = logging.getLogger(__name__)

# The prompt template that drives extraction.
_EXTRACTOR_PROMPT_NAME: str = "open_world_intent_extractor"

# Permissive JSON schema mirroring the OpenWorldResearchIR shape.  It is
# deliberately loose (``additionalProperties: True``) so the LLM is free to
# surface novel fields that later pipeline stages may consume.
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ir_version": {"type": "string"},
        "study_type": {"type": "string"},
        "dimensionality": {"type": "string"},
        "domain": {"type": "object"},
        "geometry_entities": {"type": "array"},
        "materials": {"type": "array"},
        "boundaries": {"type": "array"},
        "initial_conditions": {"type": "array"},
        "physics_models": {"type": "array"},
        "observables": {"type": "array"},
        "spatial_relations": {"type": "array"},
        "unresolved_mentions": {"type": "array"},
        "ambiguities": {"type": "array"},
        "assumptions": {"type": "array"},
        "mention_inventory": {"type": "object"},
    },
    "additionalProperties": True,
}

# Literal value sets copied from :class:`Mention` -- used to coerce raw LLM
# values into valid enumerations so a single malformed mention never breaks
# deserialization of the whole inventory.
_VALID_MENTION_CATEGORIES: frozenset[str] = frozenset({
    "domain", "geometry", "material", "boundary",
    "initial_condition", "physics", "observable",
    "spatial_relation", "numerics", "unknown",
})
_VALID_MENTION_STATUSES: frozenset[str] = frozenset({
    "mapped", "derived", "ambiguous",
    "unsupported", "needs_clarification", "ignored",
})


def build_mention_inventory(
    user_text: str,
    llm_mentions: list[dict] | None = None,
) -> MentionInventory:
    """Build a :class:`MentionInventory` from raw data.

    Args:
        user_text: The original user text.  Used as a fallback when the
            LLM did not return any usable mentions.
        llm_mentions: Optional list of mention dicts (as produced by the
            LLM).  Each dict may carry ``mention_id``, ``text``,
            ``category``, ``status`` and ``mapped_to``.

    Returns:
        A :class:`MentionInventory`.  When *llm_mentions* is provided and
        yields at least one valid mention, those mentions are returned.
        Otherwise a single :class:`Mention` covering the full *user_text*
        is returned with ``category="unknown"`` and ``status="ignored"``
        so that no user input is ever lost.
    """
    if llm_mentions:
        mentions: list[Mention] = []
        for index, item in enumerate(llm_mentions):
            if not isinstance(item, dict):
                continue
            category = item.get("category", "unknown")
            if category not in _VALID_MENTION_CATEGORIES:
                category = "unknown"
            status = item.get("status", "ignored")
            if status not in _VALID_MENTION_STATUSES:
                status = "ignored"
            mentions.append(
                Mention(
                    mention_id=item.get("mention_id") or f"m{index + 1}",
                    text=item.get("text", ""),
                    category=category,  # type: ignore[arg-type]
                    status=status,  # type: ignore[arg-type]
                    mapped_to=item.get("mapped_to"),
                )
            )
        if mentions:
            return MentionInventory(mentions=mentions)

    # No usable LLM mentions -- record the full user text as a single
    # ignored mention so the coverage guard can still see it.
    return MentionInventory(
        mentions=[
            Mention(
                mention_id="m1",
                text=user_text,
                category="unknown",
                status="ignored",
            )
        ]
    )


def _extract_mention_list(
    parsed: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Normalise the ``mention_inventory`` field of *parsed* into a list.

    The LLM payload may encode mentions either as a bare list or as an
    object wrapping a ``mentions`` list; this helper accepts both and
    returns ``None`` when nothing usable is present.
    """
    raw = parsed.get("mention_inventory")
    if isinstance(raw, list):
        return raw  # type: ignore[return-value]
    if isinstance(raw, dict):
        inner = raw.get("mentions")
        if isinstance(inner, list):
            return inner  # type: ignore[return-value]
    return None


def _collect_ir_targets(
    ir: OpenWorldResearchIR,
) -> tuple[set[str], set[str]]:
    """Collect (source_spans, mapped_ids) from an IR for mention matching.

    Returns a tuple of two sets: the first holds every ``source_span``
    string attached to an IR element; the second holds every identifier
    (entity/boundary/material/...) that a mention could be ``mapped_to``.
    """
    spans: set[str] = set()
    ids: set[str] = set()

    spans.update(ir.domain.source_spans)

    for entity in ir.geometry_entities:
        ids.add(entity.entity_id)
        spans.update(entity.source_spans)
    for boundary in ir.boundaries:
        ids.add(boundary.boundary_id)
        if boundary.source_span:
            spans.add(boundary.source_span)
    for material in ir.materials:
        ids.add(material.material_id)
        spans.update(material.source_spans)
    for ic in ir.initial_conditions:
        ids.add(ic.ic_id)
        if ic.source_span:
            spans.add(ic.source_span)
    for model in ir.physics_models:
        ids.add(model.model_id)
        spans.update(model.source_spans)
    for observable in ir.observables:
        ids.add(observable.observable_id)
        if observable.source_span:
            spans.add(observable.source_span)
    for relation in ir.spatial_relations:
        ids.add(relation.relation_id)
        if relation.source_span:
            spans.add(relation.source_span)

    return spans, ids


def _mention_is_mapped(
    mention: Mention,
    spans: set[str],
    ids: set[str],
) -> bool:
    """Return ``True`` if *mention* is accounted for by the IR.

    A mention counts as mapped when its ``mapped_to`` references a known
    IR identifier, or when its text overlaps any source span recorded on
    an IR element.
    """
    if mention.mapped_to and mention.mapped_to in ids:
        return True
    text = (mention.text or "").strip()
    if not text:
        return False
    for span in spans:
        if not span:
            continue
        if text in span or span in text:
            return True
    return False


class OpenWorldIntentExtractor:
    """Extracts an :class:`OpenWorldResearchIR` from free-form user text.

    The extractor calls an LLM using the ``open_world_intent_extractor``
    prompt and parses the structured JSON response into the canonical IR.
    It always attaches a complete :class:`MentionInventory` so downstream
    coverage guards can verify that every user mention has been resolved.

    Args:
        llm_client: Optional LLM client exposing a ``call`` method with
            the signature ``call(purpose, prompt_name, system_prompt,
            user_message, output_schema=..., session_id=...) ->
            (dict, record)``.  When ``None`` the extractor degrades
            gracefully and returns an empty IR recording the raw user
            text.
        prompt_registry: Optional :class:`PromptRegistry` used to load
            the system prompt.  A default registry is created when
            omitted.
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_registry = prompt_registry or PromptRegistry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        user_text: str,
        session_id: str = "",
    ) -> OpenWorldResearchIR:
        """Parse *user_text* into an :class:`OpenWorldResearchIR`.

        Args:
            user_text: The free-form research description supplied by the
                user.
            session_id: Optional session identifier forwarded to the LLM
                client for audit tracing.

        Returns:
            A populated :class:`OpenWorldResearchIR`.  When no LLM client
            is configured or the LLM call fails, an empty IR recording
            the raw *user_text* is returned instead.
        """
        # No LLM client -> graceful degradation.
        if self._llm_client is None:
            logger.info(
                "OpenWorld intent extraction skipped (no LLM client); "
                "recording raw user text as unresolved."
            )
            return self._fallback_ir(
                user_text, reason="LLM client not configured"
            )

        # Load the system prompt.
        try:
            system_prompt = self._prompt_registry.load(_EXTRACTOR_PROMPT_NAME)
        except FileNotFoundError as exc:
            logger.error("Intent extractor prompt not found: %s", exc)
            return self._fallback_ir(
                user_text, reason=f"prompt not found: {exc}"
            )

        # Call the LLM.
        try:
            parsed, _record = self._llm_client.call(
                purpose="extraction",
                prompt_name=_EXTRACTOR_PROMPT_NAME,
                system_prompt=system_prompt,
                user_message=user_text,
                output_schema=_OUTPUT_SCHEMA,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001 - LLM failures are expected
            logger.error(
                "LLM call failed during open-world intent extraction: %s",
                exc,
            )
            return self._fallback_ir(
                user_text, reason=f"LLM call failed: {exc}"
            )

        # The LLM client contract guarantees a dict, but defend anyway.
        if not isinstance(parsed, dict):
            logger.error(
                "LLM returned a non-dict response (%s); falling back.",
                type(parsed).__name__,
            )
            return self._fallback_ir(
                user_text, reason="LLM returned non-dict response"
            )

        # Parse the structured response into the canonical IR.
        try:
            ir = OpenWorldResearchIR.model_validate(parsed)
        except Exception as exc:  # noqa: BLE001 - validation failures are expected
            logger.error(
                "Failed to validate LLM response into "
                "OpenWorldResearchIR: %s",
                exc,
            )
            return self._fallback_ir(
                user_text, reason=f"IR validation failed: {exc}"
            )

        # Build the mention inventory from the raw LLM payload (the prompt
        # asks the model to enumerate every mention it observed) and attach
        # it to the IR's source coverage.
        inventory = build_mention_inventory(
            user_text, _extract_mention_list(parsed)
        )
        ir.source_coverage.mention_inventory = inventory

        # Reconcile each mention against the parsed IR: anything that
        # cannot be traced to an IR element is marked "ignored" so the
        # coverage guard can flag it; anything that maps is upgraded from
        # the default "ignored" to "mapped" (preserving more specific
        # statuses such as "ambiguous" or "derived" set by the LLM).
        spans, ids = _collect_ir_targets(ir)
        for mention in inventory.mentions:
            if _mention_is_mapped(mention, spans, ids):
                if mention.status == "ignored":
                    mention.status = "mapped"
            else:
                mention.status = "ignored"

        logger.info(
            "OpenWorld intent extracted: entities=%d, boundaries=%d, "
            "coverage_ratio=%.2f",
            len(ir.geometry_entities),
            len(ir.boundaries),
            ir.source_coverage.coverage_ratio,
        )
        return ir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_ir(
        user_text: str,
        reason: str,
    ) -> OpenWorldResearchIR:
        """Build an empty IR recording *user_text* as unresolved.

        The full user text is captured both as an ignored mention (so the
        coverage guard sees it) and as an :class:`UnresolvedMention`
        explaining why structured extraction did not occur.
        """
        ir = OpenWorldResearchIR()
        ir.source_coverage.mention_inventory = build_mention_inventory(
            user_text
        )
        ir.unresolved_mentions.append(
            UnresolvedMention(
                text=user_text,
                category="unknown",
                reason=reason,
            )
        )
        return ir


__all__ = [
    "OpenWorldIntentExtractor",
    "build_mention_inventory",
]
