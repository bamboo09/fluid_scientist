"""Patch provenance and history for the spec-editing module.

The :class:`PatchHistory` maintains an append-only ledger of
:class:`PatchRecord` objects — one per applied patch — keyed by spec id
and patch id.  This enables:

* **Undo** — look up the last patch and generate a reverse patch.
* **Audit** — list all patches that have been applied to a spec.
* **Provenance** — trace any spec version back to the patch that
  produced it and the user words that motivated it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import UTC

from .diff_builder import SpecDiff
from .impact_analyzer import ImpactReport
from .models import SimulationSpecPatch

__all__ = ["PatchRecord", "PatchHistory"]

#: Status of a patch record.
PatchRecordStatus = Literal["pending", "confirmed", "rejected", "superseded"]


class PatchRecord(BaseModel):
    """A single entry in the :class:`PatchHistory` ledger.

    Parameters
    ----------
    patch_id:
        Unique identifier for the patch.
    session_id:
        The research session this patch belongs to.
    base_spec_id:
        The ``spec_id`` of the spec this patch targeted.
    base_version:
        The version before the patch was applied.
    new_version:
        The version after the patch was applied (``None`` if the patch
        was rejected or is pending).
    patch:
        The :class:`SimulationSpecPatch` itself.
    diff:
        The :class:`SpecDiff` produced by applying the patch, or
        ``None`` if the patch was not applied.
    impact:
        The :class:`ImpactReport` for the patch, or ``None`` if impact
        analysis was not run.
    applied_at:
        ISO-8601 timestamp of when the patch was applied.
    applied_by:
        Identifier of the agent/user that applied the patch.
    status:
        The current status of the patch record.
    """

    model_config = ConfigDict(extra="forbid")

    patch_id: str
    session_id: str
    base_spec_id: str
    base_version: int
    new_version: int | None = None
    patch: SimulationSpecPatch
    diff: SpecDiff | None = None
    impact: ImpactReport | None = None
    applied_at: str = ""
    applied_by: str = ""
    status: PatchRecordStatus = "pending"


class PatchHistory:
    """Append-only ledger of applied patches.

    The history is keyed by ``spec_id`` and maintains insertion order
    within each spec.

    Usage::

        history = PatchHistory()
        history.record(PatchRecord(...))
        latest = history.get_latest("my_spec_id")
    """

    def __init__(self) -> None:
        self._records: dict[str, list[PatchRecord]] = {}
        self._by_id: dict[str, PatchRecord] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, patch_record: PatchRecord) -> None:
        """Add *patch_record* to the history.

        If a record with the same ``patch_id`` already exists, it is
        replaced (the new record supersedes the old one).
        """
        spec_id = patch_record.base_spec_id
        if spec_id not in self._records:
            self._records[spec_id] = []

        # If the same patch_id exists, mark the old one as superseded.
        existing = self._by_id.get(patch_record.patch_id)
        if existing is not None:
            existing.status = "superseded"

        self._records[spec_id].append(patch_record)
        self._by_id[patch_record.patch_id] = patch_record

    def get(self, patch_id: str) -> PatchRecord | None:
        """Return the :class:`PatchRecord` for *patch_id*, or ``None``."""
        return self._by_id.get(patch_id)

    def list_for_spec(self, spec_id: str) -> list[PatchRecord]:
        """Return all patch records for *spec_id* in insertion order."""
        return list(self._records.get(spec_id, []))

    def get_latest(self, spec_id: str) -> PatchRecord | None:
        """Return the most recently recorded patch for *spec_id*, or
        ``None`` if no patches have been recorded."""
        records = self._records.get(spec_id)
        if not records:
            return None
        return records[-1]

    @staticmethod
    def _now() -> str:
        """Return the current UTC timestamp as an ISO-8601 string."""
        return datetime.now(UTC).isoformat()
