"""Quantity and sourced-value types for the SimulationStudySpec.

This module provides the foundational value-wrapper types used across the
entire study spec.  Every numeric or symbolic quantity in the spec is
expressed either as a :class:`Quantity` (a raw physical value with optional
unit and relative-modification expression) or as a :class:`SourcedValue`
(a value with full provenance tracking).

Design notes
------------
* ``Quantity`` supports *relative modifications* through the ``expression``
  field.  An expression dict describes an operation to apply to a value
  located elsewhere in the spec, e.g.::

      {"operator": "multiply", "path": "/numerics/time/delta_t", "factor": 0.5}

* ``SourcedValue`` carries a strict :data:`SourcedValueStatus` hierarchy so
  that the system can decide which source wins when two values collide.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Quantity",
    "SourcedValue",
    "SourcedValueStatus",
    "TimeControl",
    "TimeWindow",
]


# ---------------------------------------------------------------------------
# Status literal — the canonical source hierarchy
# ---------------------------------------------------------------------------

#: The six-level status hierarchy.  Higher in the tuple = higher priority.
SourcedValueStatus = Literal[
    "user_explicit",
    "user_confirmed",
    "model_recommended",
    "derived",
    "default_pending",
    "unknown",
]

#: Priority mapping (higher number = higher priority / wins over lower).
_STATUS_PRIORITY: dict[str, int] = {
    "user_explicit": 100,
    "user_confirmed": 90,
    "derived": 70,
    "model_recommended": 50,
    "default_pending": 30,
    "unknown": 10,
}


def status_priority(status: str) -> int:
    """Return the numeric priority for a sourced-value status.

    Higher number wins over lower.  Unknown statuses default to 0.
    """
    return _STATUS_PRIORITY.get(status, 0)


def should_override(existing: str, new: str) -> bool:
    """Return True if *new* status should override *existing* status."""
    return status_priority(new) > status_priority(existing)


# ---------------------------------------------------------------------------
# Quantity — a raw physical value, optionally symbolic
# ---------------------------------------------------------------------------


class Quantity(BaseModel):
    """A physical quantity with optional unit and relative expression.

    Parameters
    ----------
    value:
        The numeric value, or a dict describing a symbolic expression.
        Supported scalar types are ``float`` and ``int``.  A ``dict`` value
        is interpreted as an inline expression (e.g. for parameterised
        sweeps).
    unit:
        Physical unit string (e.g. ``"m/s"``, ``"Pa"``).  ``None`` means
        dimensionless or unitless.
    expression:
        Optional dict describing a *relative modification* to apply to a
        value located elsewhere in the spec.  Example::

            {
                "operator": "multiply",
                "path": "/numerics/time/delta_t",
                "factor": 0.5,
            }

        When ``expression`` is set the ``value`` may be omitted; the
        effective value is computed at compile time.
    """

    model_config = ConfigDict(extra="forbid")

    value: float | int | dict[str, Any] | None = None
    unit: str | None = None
    expression: dict[str, Any] | None = None

    def is_resolved(self) -> bool:
        """Return True when a concrete value is available."""
        return self.value is not None

    def is_symbolic(self) -> bool:
        """Return True when this quantity relies on an expression."""
        return self.expression is not None


# ---------------------------------------------------------------------------
# SourcedValue — a value with full provenance
# ---------------------------------------------------------------------------


class SourcedValue(BaseModel):
    """A value carrying full provenance metadata.

    This is the canonical wrapper used throughout the spec for any field
    whose origin matters.  The :attr:`status` field encodes the source
    hierarchy; :func:`status_priority` / :func:`should_override` can be used
    to resolve conflicts.
    """

    model_config = ConfigDict(extra="forbid")

    value: Any = None
    unit: str | None = None
    status: SourcedValueStatus = "unknown"
    source_turn_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    derivation_id: str | None = None
    last_modified_by_patch: str | None = None

    def is_user_provided(self) -> bool:
        """Return True if the value came from the user."""
        return self.status in ("user_explicit", "user_confirmed")

    def is_resolved(self) -> bool:
        """Return True when a concrete value is available."""
        return self.value is not None


# ---------------------------------------------------------------------------
# TimeWindow — a closed interval used for statistics collection
# ---------------------------------------------------------------------------


class TimeWindow(BaseModel):
    """A closed time interval ``[start, end]`` with an optional label."""

    model_config = ConfigDict(extra="forbid")

    start: Quantity
    end: Quantity
    label: str | None = None

    @model_validator(mode="after")
    def _validate_bounds(self) -> TimeWindow:
        # Only validate when both ends are concrete numeric values.
        if (
            isinstance(self.start.value, int | float)
            and isinstance(self.end.value, int | float)
            and self.start.value > self.end.value
        ):
            raise ValueError(
                f"TimeWindow start ({self.start.value}) must be <= "
                f"end ({self.end.value})"
            )
        return self


# ---------------------------------------------------------------------------
# TimeControl — simulation temporal control
# ---------------------------------------------------------------------------


class TimeControl(BaseModel):
    """Simulation temporal control block.

    When both ``start_time`` and ``end_time`` are present and ``duration``
    is not explicitly set, ``duration`` is derived as
    ``end_time - start_time``.

    Statistics windows must fall within the simulation time range when it
    can be determined from concrete numeric values.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["steady", "transient"]
    start_time: Quantity | None = None
    end_time: Quantity | None = None
    duration: Quantity | None = None
    delta_t: Quantity | None = None
    adaptive: bool = False
    max_courant: float | None = None
    max_delta_t: Quantity | None = None
    write_control: (
        Literal[
            "timeStep",
            "runTime",
            "adjustableRunTime",
            "clockTime",
            "cpuTime",
        ]
        | None
    ) = None
    write_interval: Quantity | int | None = None
    purge_write: int | None = None
    statistics_windows: list[TimeWindow] = Field(default_factory=list)

    @staticmethod
    def _numeric(quantity: Quantity | None) -> float | None:
        """Extract a concrete float from a Quantity, or None."""
        if quantity is None:
            return None
        v = quantity.value
        if isinstance(v, int | float):
            return float(v)
        return None

    @model_validator(mode="after")
    def _derive_duration_and_validate_windows(self) -> TimeControl:
        # --- Derive duration = end - start when possible ---
        start_v = self._numeric(self.start_time)
        end_v = self._numeric(self.end_time)
        if (
            start_v is not None
            and end_v is not None
            and self.duration is None
        ):
            # Only derive when units are compatible (both None or equal).
            u_start = self.start_time.unit if self.start_time else None
            u_end = self.end_time.unit if self.end_time else None
            if u_start == u_end:
                self.duration = Quantity(
                    value=end_v - start_v,
                    unit=u_start,
                )

        # --- Validate statistics windows fall within sim range ---
        if self.statistics_windows and start_v is not None and end_v is not None:
            for win in self.statistics_windows:
                w_start = self._numeric(win.start)
                w_end = self._numeric(win.end)
                if w_start is not None and w_start < start_v:
                    raise ValueError(
                        f"Statistics window '{win.label or 'unnamed'}' start "
                        f"({w_start}) is before simulation start ({start_v})"
                    )
                if w_end is not None and w_end > end_v:
                    raise ValueError(
                        f"Statistics window '{win.label or 'unnamed'}' end "
                        f"({w_end}) is after simulation end ({end_v})"
                    )
        return self
