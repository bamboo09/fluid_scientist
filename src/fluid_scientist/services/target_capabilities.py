"""Short-lived, credential-safe execution-target capability cache."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from time import monotonic

from pydantic import AwareDatetime, ConfigDict, Field

from fluid_scientist.compat import UTC
from fluid_scientist.execution_targets.base import (
    ExecutionTargetAdapter,
    ExecutionTargetCapability,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _public_version(value: str | None) -> str | None:
    if value is None:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+ -]{0,63}", value) else None


class TargetCapabilityStatus(ExecutionTargetCapability):
    """A doctor result with cache provenance suitable for the UI."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_at: AwareDatetime
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
        wall_clock: Callable[[], datetime] = _utc_now,
        ttl_seconds: float = 30.0,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._ttl_seconds = float(ttl_seconds)
        self._entries: dict[str, _CacheEntry] = {}
        self._map_lock = RLock()
        self._refresh_locks: dict[str, RLock] = {}

    def get(
        self,
        target: ExecutionTargetAdapter,
        *,
        force_refresh: bool = False,
    ) -> TargetCapabilityStatus:
        """Return a fresh-enough result with single-flight refresh per target."""

        now = self._monotonic()
        with self._map_lock:
            observed = self._entries.get(target.target_id)
            refresh_lock = self._refresh_locks.setdefault(target.target_id, RLock())
        if not force_refresh and observed is not None:
            age = max(0.0, now - observed.checked_monotonic)
            if age <= self._ttl_seconds:
                return self._view(observed, age=age, cached=True, stale=False)

        with refresh_lock:
            now = self._monotonic()
            with self._map_lock:
                current = self._entries.get(target.target_id)
            if current is not None:
                age = max(0.0, now - current.checked_monotonic)
                refreshed_by_peer = force_refresh and current is not observed
                if refreshed_by_peer or (not force_refresh and age <= self._ttl_seconds):
                    return self._view(current, age=age, cached=True, stale=False)

            try:
                capability = self._sanitize(target, target.doctor())
            except Exception:
                # Never reflect transport details: they can contain hosts, paths, or commands.
                kind = getattr(target, "kind", "workstation_openfoam")
                capability = ExecutionTargetCapability(
                    target_id=target.target_id,
                    kind=kind,
                    available=False,
                    reason="execution target capability check failed",
                )
            completed_monotonic = self._monotonic()
            checked_at = self._wall_clock()
            entry = _CacheEntry(
                capability=capability,
                checked_at=checked_at,
                checked_monotonic=completed_monotonic,
            )
            with self._map_lock:
                self._entries[target.target_id] = entry
            return self._view(entry, age=0.0, cached=False, stale=False)

    @staticmethod
    def _sanitize(
        target: ExecutionTargetAdapter,
        capability: ExecutionTargetCapability,
    ) -> ExecutionTargetCapability:
        """Reduce a remote doctor payload to explicitly public fields."""

        return ExecutionTargetCapability(
            target_id=target.target_id,
            kind=getattr(target, "kind", capability.kind),
            available=capability.available,
            selected_candidate=None,
            foam_version=_public_version(capability.foam_version),
            cpu_count=capability.cpu_count,
            memory_gb=capability.memory_gb,
            disk_free_gb=capability.disk_free_gb,
            commands=(),
            worker_protocol=capability.worker_protocol,
            reason=None
            if capability.available
            else "execution target is unavailable",
        )

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
