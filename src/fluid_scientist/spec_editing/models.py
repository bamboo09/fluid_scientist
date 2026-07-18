"""Patch data structures for the spec-editing module.

This module defines the core pydantic models that describe a
:class:`SimulationSpecPatch` — a semantic, schema-grounded description of
how the model wants to change a :class:`SimulationStudySpec`.

The design philosophy is that the model acts as a **semantic editor**,
not a keyword classifier.  Instead of parsing ``"仿真时间设为15秒"``
with regexes, the model produces a :class:`PatchOperation` with
``op="replace"``, ``path="/numerics/time/end_time"``, ``value=15.0``,
and ``source_quote="仿真时间设为15秒"``.  The patch engine then
validates and applies this operation using the schema-driven path
registry — no hardcoded field-specific logic required.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PatchOperation",
    "PatchIntent",
    "ClarificationAlternative",
    "ClarificationRequest",
    "SimulationSpecPatch",
]

#: The set of operations a patch can perform on a spec.
PatchOpType = Literal[
    "add",
    "replace",
    "remove",
    "merge",
    "append_unique",
    "move",
    "copy",
    "test",
    "set_relation",
    "unset_relation",
    "declare_unknown_capability",
]

#: The high-level intent behind a patch.
PatchIntent = Literal[
    "create_spec",
    "modify_existing_spec",
    "confirm_pending_patch",
    "reject_pending_patch",
    "undo_last_patch",
    "request_explanation",
]


class PatchOperation(BaseModel):
    """A single operation within a :class:`SimulationSpecPatch`.

    Each operation targets a JSON Pointer ``path`` within the spec and
    performs one of the :data:`PatchOpType` actions.  The ``source_quote``
    field is **required** and must contain the user's original words that
    motivated this operation, enabling full traceability from spec field
    back to user utterance.

    Parameters
    ----------
    op:
        The operation type (see :data:`PatchOpType`).
    path:
        JSON Pointer path to the target field, e.g.
        ``"/numerics/time/end_time"``.
    value:
        The value to set, add, or merge.  ``None`` for ``remove``.
    from_path:
        Source path for ``move`` and ``copy`` operations.
    entity_id:
        Entity identifier for ``set_relation`` / ``unset_relation``
        operations (or for paths with ``{entity_id}`` placeholders).
    relation:
        Relation definition dict for ``set_relation`` operations.
    source_quote:
        **Required.** The user's original words that motivated this
        operation.  This enables traceability from spec field back to
        the user's utterance.
    confidence:
        Model's confidence in this operation, in ``[0.0, 1.0]``.
    rationale:
        Optional human-readable explanation of why this operation is
        being performed.
    """

    model_config = ConfigDict(extra="forbid")

    op: PatchOpType
    path: str
    value: Any | None = None
    from_path: str | None = None
    entity_id: str | None = None
    relation: dict[str, Any] | None = None
    source_quote: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rationale: str | None = None


class ClarificationAlternative(BaseModel):
    """One concrete interpretation of an ambiguous user request.

    When the model detects ambiguity, it produces multiple
    :class:`ClarificationAlternative` objects, each representing a
    possible reading of the user's intent.  The user selects one,
    and the corresponding ``operations`` are applied.

    Parameters
    ----------
    label:
        Short human-readable label for this alternative.
    operations:
        The list of :class:`PatchOperation` objects that would be
        applied if the user selects this alternative.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    operations: list[PatchOperation] = Field(default_factory=list)


class ClarificationRequest(BaseModel):
    """A request for the user to clarify an ambiguous instruction.

    Parameters
    ----------
    clarification_id:
        Unique identifier for this clarification.
    question:
        The question to present to the user.
    alternatives:
        One or more concrete alternatives the user can choose from.
    affected_paths:
        JSON Pointer paths that would be affected by any of the
        alternatives.
    blocking:
        If ``True``, the patch will not be applied until the user
        resolves this clarification.  If ``False``, the patch proceeds
        with the model's best guess and the clarification is advisory.
    """

    model_config = ConfigDict(extra="forbid")

    clarification_id: str
    question: str
    alternatives: list[ClarificationAlternative] = Field(default_factory=list)
    affected_paths: list[str] = Field(default_factory=list)
    blocking: bool = False


class SimulationSpecPatch(BaseModel):
    """A semantic, schema-grounded patch to a
    :class:`SimulationStudySpec`.

    A patch is the **unit of change** in the spec-editing system.  It
    carries:

    * A set of :class:`PatchOperation` objects describing what to change.
    * Optional :class:`ClarificationRequest` objects for ambiguous cases.
    * Provenance metadata (``base_spec_id``, ``base_version``) for
      optimistic concurrency.
    * An ``untouched_guarantee`` flag: when ``True``, the model asserts
      that only the fields touched by ``operations`` will change — all
      other fields remain identical.  The patch executor verifies this.

    Parameters
    ----------
    patch_id:
        Unique identifier for this patch.
    session_id:
        The research session this patch belongs to.
    base_spec_id:
        The ``spec_id`` of the spec this patch targets.
    base_version:
        The version of the spec this patch was created against.  Must
        match the current spec version for the patch to apply.
    intent:
        The high-level intent (see :data:`PatchIntent`).
    operations:
        Ordered list of operations to apply.
    clarifications:
        Clarification requests for ambiguous patches.
    impact_requests:
        Paths for which the model requests impact analysis feedback.
    untouched_guarantee:
        If ``True``, the executor verifies that only paths in
        ``operations`` changed.
    assistant_message:
        Human-readable message from the model explaining the patch.
    """

    model_config = ConfigDict(extra="forbid")

    patch_id: str
    session_id: str
    base_spec_id: str
    base_version: int
    intent: PatchIntent
    operations: list[PatchOperation] = Field(default_factory=list)
    clarifications: list[ClarificationRequest] = Field(default_factory=list)
    impact_requests: list[str] = Field(default_factory=list)
    untouched_guarantee: bool = True
    assistant_message: str = ""
