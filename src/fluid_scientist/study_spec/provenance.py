"""Spec provenance for the SimulationStudySpec.

This module tracks *who* created a spec, *when*, from *which* parent
version, and the full modification history.  Provenance is attached to
every :class:`~fluid_scientist.study_spec.models.SimulationStudySpec`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["SpecProvenance"]


class SpecProvenance(BaseModel):
    """Provenance metadata for a SimulationStudySpec.

    Parameters
    ----------
    created_at:
        ISO-8601 timestamp of spec creation.
    created_by:
        Identifier of the creator (user id or ``"system"``).
    parent_version:
        The version number this spec was derived from, or ``None`` for
        the initial version.
    creation_turn_id:
        The conversation turn that produced this spec, or ``None``.
    modification_history:
        Append-only list of modification records (dicts).  Each record
        typically contains ``patch_id``, ``turn_id``, ``timestamp``, and
        a ``summary`` of what changed.
    """

    model_config = ConfigDict(extra="forbid")

    created_at: str
    created_by: str
    parent_version: int | None = None
    creation_turn_id: str | None = None
    modification_history: list[dict[str, Any]] = Field(default_factory=list)
