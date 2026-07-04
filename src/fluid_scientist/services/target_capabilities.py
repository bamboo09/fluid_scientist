"""Short-lived, credential-safe execution-target capability cache."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from time import monotonic

from pydantic import ConfigDict, Field

from fluid_scientist.compat import UTC
from fluid_scientist.execution_targets.base import (
    ExecutionTargetAdapter,
    ExecutionTargetCapability,
)


class TargetCapabilityStatus(ExecutionTargetCapability):
    """A doctor result with cache provenance suitable for the UI."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_at: datetime
    age_seconds: float = Field(ge=0)
    cached: bool
    stale: bool


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    capability: ExecutionTargetCapability
    checked_at: datetime
    checked_monotonic: float


class TargetCapabilityCache:
    """Cache both healthy and unhealthy doctor results for a bounded interval."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = monotonic,
        ttl_seconds: float = 30.0,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._monotonic = monotonic
        self._ttl_seconds = float(ttl_seconds)
        self._entries: dict[str, _CacheEntry] = {}
        self._lock = RLock()

    def get(
        self,
        target: ExecutionTargetAdapter,
        *,
        force_refresh: bool = False,
    ) -> TargetCapabilityStatus:
        """Return a fresh-enough result; serialize refreshes per cache instance."""

        with self._lock:
            now = self._monotonic()
            entry = self._entries.get(target.target_id)
            if not force_refresh and entry is not None:
                age = max(0.0, now - entry.checked_monotonic)
                if age <= self._ttl_seconds:
                    return self._view(entry, age=age, cached=True, stale=False)

            checked_at = datetime.now(UTC)
            try:
                capability = target.doctor()
            except Exception:
                # Never reflect transport details: they can contain hosts, paths, or commands.
                kind = getattr(target, "kind", "workstation_openfoam")
                capability = ExecutionTargetCapability(
                    target_id=target.target_id,
                    kind=kind,
                    available=False,
                    reason="execution target capability check failed",
                )
            entry = _CacheEntry(
                capability=capability,
                checked_at=checked_at,
                checked_monotonic=self._monotonic(),
            )
            self._entries[target.target_id] = entry
            return self._view(entry, age=0.0, cached=False, stale=False)

    @staticmethod
    def _view(
        entry: _CacheEntry,
        *,
        age: float,
        cached: bool,
        stale: bool,
    ) -> TargetCapabilityStatus:
        return TargetCapabilityStatus(
            **entry.capability.model_dump(),
            checked_at=entry.checked_at,
            age_seconds=age,
            cached=cached,
            stale=stale,
        )


__all__ = ["TargetCapabilityCache", "TargetCapabilityStatus"]
