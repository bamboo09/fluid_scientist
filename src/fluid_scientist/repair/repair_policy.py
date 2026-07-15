"""Repair policy — controls retry limits, phase freezing, and escalation.

Levels:
1. CONFIG_ONLY — adjust controlDict, fvSolution parameters (deltaT, maxCo, etc.)
2. DICTIONARY_SYNTAX — fix syntax errors in OpenFOAM dictionaries
3. PARTIAL_REGENERATION — regenerate specific files from CaseSpec

Rules:
- Each phase freezes after 3 failed repair attempts
- Global retry limit: 10 total attempts
- Each repair must pass validation before retry
- No RETRY_WITHOUT_REPAIR — every retry must include a change
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RepairLevel(str, Enum):
    CONFIG_ONLY = "config_only"
    DICTIONARY_SYNTAX = "dictionary_syntax"
    PARTIAL_REGENERATION = "partial_regeneration"


class RepairPhase(str, Enum):
    MESH = "mesh"
    SMOKE = "smoke"
    FULL_RUN = "full_run"


class RepairStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    PHASE_FROZEN = "phase_frozen"
    GLOBAL_LIMIT_REACHED = "global_limit_reached"


@dataclass
class RepairAttempt:
    """Record of a single repair attempt."""
    attempt_number: int
    phase: RepairPhase
    level: RepairLevel
    error_summary: str
    diagnosis: str | None = None
    fix_applied: str | None = None
    files_modified: list[str] = field(default_factory=list)
    validation_passed: bool = False
    retry_passed: bool = False
    error_log: str | None = None


@dataclass
class RepairPolicy:
    """Controls the repair loop behavior."""
    max_attempts_per_phase: int = 3
    max_global_attempts: int = 10
    current_global_attempts: int = 0
    phase_attempts: dict[str, int] = field(default_factory=lambda: {"mesh": 0, "smoke": 0, "full_run": 0})
    phase_frozen: dict[str, bool] = field(default_factory=lambda: {"mesh": False, "smoke": False, "full_run": False})
    attempt_history: list[RepairAttempt] = field(default_factory=list)

    def can_attempt(self, phase: RepairPhase) -> bool:
        """Check if another repair attempt is allowed."""
        if self.current_global_attempts >= self.max_global_attempts:
            return False
        if self.phase_frozen.get(phase.value, False):
            return False
        if self.phase_attempts.get(phase.value, 0) >= self.max_attempts_per_phase:
            return False
        return True

    def record_attempt(self, attempt: RepairAttempt) -> RepairStatus:
        """Record an attempt and return the resulting status."""
        self.attempt_history.append(attempt)
        self.current_global_attempts += 1
        self.phase_attempts[attempt.phase.value] = self.phase_attempts.get(attempt.phase.value, 0) + 1

        if attempt.retry_passed:
            return RepairStatus.SUCCESS

        # Check if phase should be frozen
        if self.phase_attempts.get(attempt.phase.value, 0) >= self.max_attempts_per_phase:
            self.phase_frozen[attempt.phase.value] = True
            return RepairStatus.PHASE_FROZEN

        # Check global limit
        if self.current_global_attempts >= self.max_global_attempts:
            return RepairStatus.GLOBAL_LIMIT_REACHED

        return RepairStatus.FAILED

    def get_repair_level(self, phase: RepairPhase, attempt_number: int) -> RepairLevel:
        """Determine repair level based on phase and attempt number."""
        if attempt_number <= 1:
            return RepairLevel.CONFIG_ONLY
        elif attempt_number <= 2:
            return RepairLevel.DICTIONARY_SYNTAX
        else:
            return RepairLevel.PARTIAL_REGENERATION

    @property
    def has_repair_been_attempted(self) -> bool:
        """Check if any repair has been attempted (vs RETRY_WITHOUT_REPAIR)."""
        return len(self.attempt_history) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts_per_phase": self.max_attempts_per_phase,
            "max_global_attempts": self.max_global_attempts,
            "current_global_attempts": self.current_global_attempts,
            "phase_attempts": dict(self.phase_attempts),
            "phase_frozen": dict(self.phase_frozen),
            "attempt_count": len(self.attempt_history),
            "attempts": [
                {
                    "attempt_number": a.attempt_number,
                    "phase": a.phase.value,
                    "level": a.level.value,
                    "error_summary": a.error_summary,
                    "fix_applied": a.fix_applied,
                    "validation_passed": a.validation_passed,
                    "retry_passed": a.retry_passed,
                }
                for a in self.attempt_history
            ],
        }
