"""Session management for multi-turn spec editing.

The :class:`SessionManager` provides in-memory persistence for
:class:`ResearchSessionState` instances across method calls.  It also
integrates with :class:`VersionedSpecStore` for spec versioning and
:class:`PatchHistory` for patch provenance.

Design rules
------------
* **In-memory persistence.** Sessions survive across method calls
  because they are stored in a dict on the manager instance.
* **No silent fallback.** Methods that require an existing session
  raise :class:`KeyError` if the session is not found -- they never
  silently do nothing.
* **Pending patch lifecycle.** A patch is set via
  :meth:`set_pending_patch`, confirmed via
  :meth:`confirm_pending_patch` (which adds it to ``patch_history``
  and returns its id), and finally cleared via
  :meth:`clear_pending_patch`.  Rejection uses
  :meth:`clear_pending_patch` without confirmation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fluid_scientist.compat import UTC
from fluid_scientist.spec_editing.models import SimulationSpecPatch
from fluid_scientist.spec_editing.provenance import PatchHistory
from fluid_scientist.study_spec.models import SimulationStudySpec
from fluid_scientist.study_spec.versioning import VersionedSpecStore

from .models import (
    ConflictRecord,
    ConversationTurn,
    FactRecord,
    ResearchSessionState,
    SessionPhase,
)

__all__ = ["SessionManager"]


class SessionManager:
    """Manages research sessions in memory.

    The manager keeps a dict of ``session_id`` -> :class:`ResearchSessionState`
    and provides methods for creating, retrieving, and mutating sessions.
    It also holds a :class:`VersionedSpecStore` for spec versioning and
    a :class:`PatchHistory` for patch provenance.

    Sessions survive across method calls (in-memory persistence).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ResearchSessionState] = {}
        self._spec_store: VersionedSpecStore = VersionedSpecStore()
        self._patch_history: PatchHistory = PatchHistory()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        """Return the current UTC timestamp as an ISO-8601 string."""
        return datetime.now(UTC).isoformat()

    def _require_session(self, session_id: str) -> ResearchSessionState:
        """Return the session, or raise ``KeyError`` if it does not exist.

        This enforces the "no silent fallback" rule: every mutating
        method must fail loudly when the session is missing.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        return session

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(self, project_id: str) -> ResearchSessionState:
        """Create a new research session.

        Parameters
        ----------
        project_id:
            The project this session belongs to.

        Returns
        -------
        ResearchSessionState
            The newly created session, with a unique ``session_id``,
            the ``UNDERSTANDING`` phase, and ISO-8601 timestamps.
        """
        now = self._now()
        session = ResearchSessionState(
            session_id=f"session_{uuid.uuid4().hex[:16]}",
            project_id=project_id,
            created_at=now,
            last_active_at=now,
        )
        self._sessions[session.session_id] = session
        return session

    def get_session(
        self,
        session_id: str,
    ) -> ResearchSessionState | None:
        """Return the session, or ``None`` if it does not exist."""
        return self._sessions.get(session_id)

    # ------------------------------------------------------------------
    # Conversation turns
    # ------------------------------------------------------------------

    def add_turn(
        self,
        session_id: str,
        turn: ConversationTurn,
    ) -> None:
        """Append a conversation turn to the session.

        Also updates ``last_active_at``.

        Parameters
        ----------
        session_id:
            The session to add the turn to.
        turn:
            The :class:`ConversationTurn` to append.
        """
        session = self._require_session(session_id)
        session.turns.append(turn)
        session.last_active_at = self._now()

    # ------------------------------------------------------------------
    # Pending patch lifecycle
    # ------------------------------------------------------------------

    def set_pending_patch(
        self,
        session_id: str,
        patch: SimulationSpecPatch,
    ) -> None:
        """Set the pending patch for the session.

        This is the first step in the pending-patch lifecycle.  The
        patch will await user confirmation before being applied.

        Parameters
        ----------
        session_id:
            The session to set the pending patch on.
        patch:
            The :class:`SimulationSpecPatch` to set as pending.
        """
        session = self._require_session(session_id)
        session.pending_patch = patch

    def clear_pending_patch(self, session_id: str) -> None:
        """Clear the pending patch.

        This is used both after confirmation (the patch has been
        confirmed and will be applied elsewhere) and after rejection
        (the user declined the patch).

        Parameters
        ----------
        session_id:
            The session to clear the pending patch on.
        """
        session = self._require_session(session_id)
        session.pending_patch = None

    def confirm_pending_patch(self, session_id: str) -> str | None:
        """Confirm the pending patch.

        If a pending patch exists, its ``patch_id`` is added to the
        session's ``patch_history`` and returned.  The pending patch is
        **not** cleared by this method -- call
        :meth:`clear_pending_patch` afterwards to reset the pending
        field.

        Parameters
        ----------
        session_id:
            The session whose pending patch should be confirmed.

        Returns
        -------
        str | None
            The confirmed ``patch_id``, or ``None`` if no patch is
            pending.
        """
        session = self._require_session(session_id)
        if session.pending_patch is None:
            return None
        patch_id = session.pending_patch.patch_id
        session.patch_history.append(patch_id)
        return patch_id

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------

    def update_phase(
        self,
        session_id: str,
        phase: SessionPhase,
    ) -> None:
        """Update the session's workflow phase.

        Parameters
        ----------
        session_id:
            The session to update.
        phase:
            The new :class:`SessionPhase`.
        """
        session = self._require_session(session_id)
        session.current_phase = phase

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    def add_fact(
        self,
        session_id: str,
        fact: FactRecord,
    ) -> None:
        """Add a confirmed fact to the session.

        Parameters
        ----------
        session_id:
            The session to add the fact to.
        fact:
            The :class:`FactRecord` to add.
        """
        session = self._require_session(session_id)
        session.confirmed_facts.append(fact)

    # ------------------------------------------------------------------
    # Conflicts
    # ------------------------------------------------------------------

    def add_conflict(
        self,
        session_id: str,
        conflict: ConflictRecord,
    ) -> None:
        """Add an unresolved conflict to the session.

        Parameters
        ----------
        session_id:
            The session to add the conflict to.
        conflict:
            The :class:`ConflictRecord` to add.
        """
        session = self._require_session(session_id)
        session.unresolved_conflicts.append(conflict)

    def resolve_conflict(
        self,
        session_id: str,
        conflict_id: str,
        turn_id: str,
    ) -> None:
        """Mark a conflict as resolved and remove it from the active list.

        The conflict's ``status`` is set to ``"resolved"`` and its
        ``resolution_turn_id`` is set to *turn_id*.  It is then removed
        from the session's ``unresolved_conflicts`` list.

        Parameters
        ----------
        session_id:
            The session containing the conflict.
        conflict_id:
            The identifier of the conflict to resolve.
        turn_id:
            The turn in which the conflict was resolved.
        """
        session = self._require_session(session_id)
        remaining: list[ConflictRecord] = []
        for conflict in session.unresolved_conflicts:
            if conflict.conflict_id == conflict_id:
                conflict.status = "resolved"
                conflict.resolution_turn_id = turn_id
                # Resolved conflicts are removed from the active list.
            else:
                remaining.append(conflict)
        session.unresolved_conflicts = remaining

    # ------------------------------------------------------------------
    # Model traces
    # ------------------------------------------------------------------

    def add_model_trace(
        self,
        session_id: str,
        trace_id: str,
    ) -> None:
        """Record a model trace identifier for the session.

        Parameters
        ----------
        session_id:
            The session to record the trace for.
        trace_id:
            The model trace identifier to record.
        """
        session = self._require_session(session_id)
        session.model_trace_ids.append(trace_id)

    # ------------------------------------------------------------------
    # Active spec
    # ------------------------------------------------------------------

    def get_active_spec(
        self,
        session_id: str,
    ) -> SimulationStudySpec | None:
        """Return the active spec for the session, or ``None``.

        The spec is retrieved from the :class:`VersionedSpecStore`
        using the session's ``active_spec_id`` and
        ``active_spec_version``.

        Parameters
        ----------
        session_id:
            The session whose active spec should be returned.

        Returns
        -------
        SimulationStudySpec | None
            The active spec, or ``None`` if no spec has been set.
        """
        session = self._require_session(session_id)
        if not session.active_spec_id or session.active_spec_version < 1:
            return None
        return self._spec_store.get_version(
            session.active_spec_id,
            session.active_spec_version,
        )

    def set_active_spec(
        self,
        session_id: str,
        spec: SimulationStudySpec,
    ) -> None:
        """Store the spec in the versioned store and update the session.

        The spec is registered as a new version in the
        :class:`VersionedSpecStore`, and the session's
        ``active_spec_id`` and ``active_spec_version`` are updated to
        point at it.  ``last_active_at`` is also refreshed.

        Parameters
        ----------
        session_id:
            The session to update.
        spec:
            The :class:`SimulationStudySpec` to set as active.
        """
        session = self._require_session(session_id)
        stored = self._spec_store.create_version(spec)
        session.active_spec_id = stored.spec_id
        session.active_spec_version = stored.version
        session.last_active_at = self._now()
