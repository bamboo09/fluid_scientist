"""Experiment version state machine.

Implements the state transitions defined in the reform specification:
draft → ready → confirmed → compiling → running → completed/failed/rejected

States:
- draft: Free editing, parameters may be missing, no formal runs allowed.
- ready: Physics closed, high-risk params resolved, can be confirmed.
- confirmed: Immutable snapshot, can compile Case.
- compiling: Case generation in progress, params locked.
- awaiting_code_approval: Waiting for code extension tests/approval.
- running: Currently executing, modifications create derived versions.
- completed: Results immutable, can clone/derive.
- failed: Failure reason saved, can create fix version.
- rejected: Physics/code review failed, must revise.
"""

from __future__ import annotations


class TransitionError(ValueError):
    """Raised when a state transition is not allowed."""


_ALLOWED: dict[str, frozenset[str]] = {
    "draft": frozenset({"ready", "rejected"}),
    "ready": frozenset({"confirmed", "draft", "rejected"}),
    "confirmed": frozenset({"compiling", "draft"}),
    "compiling": frozenset({"running", "failed", "awaiting_code_approval"}),
    "awaiting_code_approval": frozenset({"compiling", "rejected"}),
    "running": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset({"draft"}),
    "rejected": frozenset({"draft"}),
}


def assert_transition(current: str, target: str) -> None:
    if target not in _ALLOWED.get(current, frozenset()):
        raise TransitionError(
            f"experiment cannot transition from '{current}' to '{target}'"
        )


def can_transition(current: str, target: str) -> bool:
    return target in _ALLOWED.get(current, frozenset())


def allowed_transitions(current: str) -> frozenset[str]:
    return _ALLOWED.get(current, frozenset())


def is_terminal(state: str) -> bool:
    return len(_ALLOWED.get(state, frozenset())) == 0


def is_editable(state: str) -> bool:
    return state in ("draft", "ready")


def is_immutable(state: str) -> bool:
    return state in ("confirmed", "compiling", "running", "completed")
