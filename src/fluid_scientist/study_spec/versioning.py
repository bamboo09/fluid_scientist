"""Version management for the SimulationStudySpec.

This module provides an in-memory :class:`VersionedSpecStore` that tracks
the full version history of one or more :class:`SimulationStudySpec`
instances.  Each call to :meth:`VersionedSpecStore.create_version` clones
the spec, increments its version number, records the parent version, and
stores the snapshot for later retrieval.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from .models import SimulationStudySpec

__all__ = [
    "SpecVersion",
    "VersionedSpecStore",
]


class SpecVersion(BaseModel):
    """Metadata record for a single spec version.

    Parameters
    ----------
    version:
        The version number.
    parent_version:
        The version this one was derived from, or ``None`` for v1.
    created_at:
        ISO-8601 timestamp of version creation.
    patch_id:
        Identifier of the patch that produced this version, or ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    version: int
    parent_version: int | None = None
    created_at: str
    patch_id: str | None = None


class VersionedSpecStore:
    """In-memory store for versioned SimulationStudySpec instances.

    The store keeps a dict-of-dicts: ``{spec_id: {version: spec}}``.
    It also maintains a parallel ``{spec_id: [SpecVersion, ...]}`` metadata
    ledger for cheap listing without deserialising full specs.
    """

    def __init__(self) -> None:
        self._specs: dict[str, dict[int, SimulationStudySpec]] = {}
        self._ledger: dict[str, list[SpecVersion]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_spec_exists(self, spec_id: str) -> None:
        if spec_id not in self._specs:
            self._specs[spec_id] = {}
            self._ledger[spec_id] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_version(
        self,
        spec: SimulationStudySpec,
    ) -> SimulationStudySpec:
        """Create a new version of *spec* and store it.

        If the spec has no prior versions in the store, it is inserted as
        version 1.  Otherwise the version number is incremented and the
        current spec's version becomes the parent.

        The returned spec is a *deep copy* with the updated ``version``,
        ``parent_version``, and provenance fields.  The original *spec*
        object is not mutated.
        """
        self._ensure_spec_exists(spec.spec_id)

        existing_versions = self._specs[spec.spec_id]
        if not existing_versions:
            # First version — insert as-is (version 1).
            new_version = spec.model_copy(deep=True)
            if new_version.version < 1:
                new_version.version = 1
            new_version.parent_version = None
        else:
            latest_version = max(existing_versions)
            new_version = spec.model_copy(deep=True)
            new_version.version = latest_version + 1
            new_version.parent_version = latest_version

        # Record metadata.
        record = SpecVersion(
            version=new_version.version,
            parent_version=new_version.parent_version,
            created_at=self._now(),
            patch_id=None,
        )
        self._ledger[spec.spec_id].append(record)
        self._specs[spec.spec_id][new_version.version] = new_version
        return new_version

    def get_version(
        self,
        spec_id: str,
        version: int,
    ) -> SimulationStudySpec | None:
        """Return the spec at *version*, or ``None`` if not found."""
        versions = self._specs.get(spec_id)
        if versions is None:
            return None
        return versions.get(version)

    def get_latest(self, spec_id: str) -> SimulationStudySpec | None:
        """Return the latest version of *spec_id*, or ``None``."""
        versions = self._specs.get(spec_id)
        if not versions:
            return None
        latest = max(versions)
        return versions[latest]

    def list_versions(self, spec_id: str) -> list[int]:
        """Return a sorted list of all version numbers for *spec_id*."""
        versions = self._specs.get(spec_id)
        if not versions:
            return []
        return sorted(versions)

    def get_ledger(self, spec_id: str) -> list[SpecVersion]:
        """Return the version metadata ledger for *spec_id*."""
        return list(self._ledger.get(spec_id, []))

    def snapshot(self, spec: SimulationStudySpec) -> SimulationStudySpec:
        """Return a deep copy of *spec* (utility for callers)."""
        return copy.deepcopy(spec)
