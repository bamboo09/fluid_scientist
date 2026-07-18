"""Context building for model invocations.

The :class:`ContextBuilder` assembles the full context that is passed
to the model on each turn.  The context is built in a fixed 11-section
order that matches the model-driven spec editing workflow, ensuring the
model always has:

1. Its system role and prohibitions.
2. The current workflow phase.
3. The OpenFOAM environment and capabilities.
4. The currently enabled professional Skills.
5. The SimulationSpecPatch JSON Schema (so the model knows how to emit
   valid patches).
6. The current complete SimulationStudySpec.
7. Confirmed facts extracted from the conversation.
8. Unresolved conflicts.
9. A compressed summary of earlier conversation.
10. The recent original conversation (last few turns).
11. The user's current message.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.spec_editing.models import SimulationSpecPatch

from .models import ConversationTurn, ResearchSessionState

__all__ = ["ModelContext", "ContextBuilder"]


class ModelContext(BaseModel):
    """The complete context passed to the model on a single turn.

    The fields are ordered to match the 11-section context-building
    plan.  Each field corresponds to one section of the context.

    Parameters
    ----------
    system_role:
        The system prompt describing the model's role and prohibitions.
    workflow_phase:
        The current workflow phase (e.g. ``"understanding"``).
    openfoam_environment:
        OpenFOAM version, available solvers, and capabilities.
    enabled_skills:
        List of currently enabled skill identifiers.
    patch_schema:
        JSON Schema for :class:`SimulationSpecPatch`, so the model
        knows the structure of valid patches.
    current_spec:
        The current spec serialized as a dict, or ``None`` if no spec
        exists yet.
    confirmed_facts:
        List of confirmed fact dicts (serialized
        :class:`FactRecord` objects).
    unresolved_conflicts:
        List of unresolved conflict dicts (serialized
        :class:`ConflictRecord` objects).
    session_summary:
        Compressed summary of earlier conversation.
    recent_conversation:
        List of recent turn dicts (serialized :class:`ConversationTurn`
        objects).
    user_message:
        The user's current message.
    """

    model_config = ConfigDict(extra="forbid")

    system_role: str
    workflow_phase: str
    openfoam_environment: dict[str, Any] = Field(default_factory=dict)
    enabled_skills: list[str] = Field(default_factory=list)
    patch_schema: dict[str, Any] = Field(default_factory=dict)
    current_spec: dict[str, Any] | None = None
    confirmed_facts: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    session_summary: str = ""
    recent_conversation: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, str]] = Field(default_factory=list)
    user_message: str = ""


class ContextBuilder:
    """Builds the model context from session state and user input.

    The builder assembles context in a fixed 11-section order, as
    specified by the model-driven spec editing plan.  It also provides
    summary compression and recent-turn retrieval helpers.
    """

    #: The system role text with explicit prohibitions.
    _SYSTEM_ROLE: str = (
        "You are a CFD simulation research assistant acting as a "
        "semantic editor of the SimulationStudySpec. "
        "You produce SimulationSpecPatch operations to modify the spec. "
        "PROHIBITIONS: "
        "(1) Never silently fall back to templates, defaults, or regex. "
        "(2) Never invent physical values or units. "
        "(3) Always include source_quote in every PatchOperation for "
        "full traceability from spec field to user utterance. "
        "(4) Never modify fields the user did not mention "
        "(respect untouched_guarantee). "
        "(5) If a capability is unknown, declare it via "
        "declare_unknown_capability -- never fake it."
    )

    def build_context(
        self,
        session: ResearchSessionState,
        spec: dict[str, Any] | None,
        user_message: str,
        skills: list[str],
        openfoam_env: dict[str, Any],
        references: list[dict[str, str]] | None = None,
    ) -> ModelContext:
        """Build the full model context for the current turn.

        The context is assembled in the fixed 11-section order:
        system role, workflow phase, OpenFOAM environment, enabled
        skills, patch schema, current spec, confirmed facts,
        unresolved conflicts, session summary, recent conversation,
        and the user's message.

        Parameters
        ----------
        session:
            The current research session state.
        spec:
            The current spec serialized as a dict, or ``None`` if no
            spec exists yet.
        user_message:
            The user's current message.
        skills:
            List of currently enabled skill identifiers.
        openfoam_env:
            OpenFOAM environment and capabilities dict.

        Returns
        -------
        ModelContext
            The assembled context with all 11 sections populated.
        """
        # 1. System role and prohibitions.
        system_role = self._SYSTEM_ROLE

        # 2. Current workflow phase.
        workflow_phase = str(session.current_phase)

        # 3. OpenFOAM environment and capabilities.
        openfoam_environment = dict(openfoam_env)

        # 4. Currently enabled professional Skills.
        enabled_skills = list(skills)

        # 5. SimulationSpecPatch JSON Schema.
        patch_schema = SimulationSpecPatch.model_json_schema()

        # 6. Current complete SimulationStudySpec.
        current_spec = spec

        # 7. Confirmed facts.
        confirmed_facts = [
            fact.model_dump() for fact in session.confirmed_facts
        ]

        # 8. Unresolved conflicts.
        unresolved_conflicts = [
            conflict.model_dump()
            for conflict in session.unresolved_conflicts
        ]

        # 9. Earlier session summary -- use stored summary, or generate
        #    a fresh one if none is stored yet.
        session_summary = (
            session.compact_summary
            if session.compact_summary
            else self.compress_summary(session)
        )

        # 10. Recent original conversation.
        recent_conversation = [
            turn.model_dump()
            for turn in self.get_recent_turns(session)
        ]

        # 11. User's current message.
        user_msg = user_message

        return ModelContext(
            system_role=system_role,
            workflow_phase=workflow_phase,
            openfoam_environment=openfoam_environment,
            enabled_skills=enabled_skills,
            patch_schema=patch_schema,
            current_spec=current_spec,
            confirmed_facts=confirmed_facts,
            unresolved_conflicts=unresolved_conflicts,
            session_summary=session_summary,
            recent_conversation=recent_conversation,
            references=list(references or []),
            user_message=user_msg,
        )

    def compress_summary(self, session: ResearchSessionState) -> str:
        """Generate a compressed summary of the session.

        The summary is designed to preserve the information that is
        critical for continuity across turns:

        * **Numerical values and units** -- so physical parameters are
          never lost.
        * **Geometry relationships** -- so spatial configuration is
          retained.
        * **User confirmation status** -- so the system knows which
          facts are confirmed versus tentative.
        * **Unresolved conflicts** -- so open issues are not forgotten.
        * **Recent patches** -- so the edit history is visible.
        * **Research objectives** -- so the scientific goal is clear.

        Parameters
        ----------
        session:
            The session to summarize.

        Returns
        -------
        str
            A compressed multi-line text summary.  Returns an empty
            string if the session has no turns, facts, conflicts, or
            patches.
        """
        lines: list[str] = []

        # --- Research objectives ---
        # Take the first user message as the primary objective.
        for turn in session.turns:
            if turn.user_message:
                lines.append("[Objectives]")
                lines.append(turn.user_message)
                break

        # --- Confirmed facts (numerical values, units, confirmation) ---
        fact_lines: list[str] = []
        for fact in session.confirmed_facts:
            value = fact.value
            if isinstance(value, dict):
                v = value.get("value")
                u = value.get("unit")
                if v is not None and u is not None:
                    fact_lines.append(
                        f"{fact.key}={v} {u} "
                        f"(confirmed={fact.confirmed})"
                    )
                elif v is not None:
                    fact_lines.append(
                        f"{fact.key}={v} "
                        f"(confirmed={fact.confirmed})"
                    )
                else:
                    fact_lines.append(
                        f"{fact.key}={value} "
                        f"(confirmed={fact.confirmed})"
                    )
            else:
                fact_lines.append(
                    f"{fact.key}={value} "
                    f"(confirmed={fact.confirmed})"
                )
        if fact_lines:
            lines.append("[Confirmed Facts]")
            lines.extend(fact_lines)

        # --- Geometry relationships ---
        geo_lines: list[str] = []
        for fact in session.confirmed_facts:
            key_lower = fact.key.lower()
            if "geometry" in key_lower or "relation" in key_lower:
                geo_lines.append(f"{fact.key}={fact.value}")
        if geo_lines:
            lines.append("[Geometry]")
            lines.extend(geo_lines)

        # --- Unresolved conflicts ---
        if session.unresolved_conflicts:
            lines.append("[Conflicts]")
            for conflict in session.unresolved_conflicts:
                lines.append(
                    f"- {conflict.description} "
                    f"(paths={conflict.paths}, "
                    f"status={conflict.status})"
                )

        # --- Recent patches ---
        if session.patch_history:
            lines.append("[Recent Patches]")
            for pid in session.patch_history[-5:]:
                lines.append(f"- {pid}")

        return "\n".join(lines)

    def get_recent_turns(
        self,
        session: ResearchSessionState,
        count: int = 5,
    ) -> list[ConversationTurn]:
        """Return the most recent *count* turns from the session.

        Parameters
        ----------
        session:
            The session to extract turns from.
        count:
            Maximum number of recent turns to return (default ``5``).

        Returns
        -------
        list[ConversationTurn]
            The most recent turns, in chronological order (oldest of
            the selected window first).  Returns an empty list if the
            session has no turns or *count* is non-positive.
        """
        if count <= 0 or not session.turns:
            return []
        return list(session.turns[-count:])
