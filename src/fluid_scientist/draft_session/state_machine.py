"""Draft session state machine.

Implements the lifecycle transitions and workbench button matrix for a
:class:`~fluid_scientist.draft_session.models.DraftSession`.

The machine is deliberately conservative: every status declares the
*exact* set of statuses it may move to, and :meth:`transition` raises
:class:`TransitionError` for anything outside that set.  The
:meth:`get_buttons` method mirrors the workbench button matrix from the
plan's section 8.3 so the frontend can be driven from a single source
of truth.
"""

from __future__ import annotations

from datetime import datetime

from fluid_scientist.compat import UTC
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
)


class TransitionError(ValueError):
    """Raised when a requested status transition is not allowed."""


# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------

_TRANSITIONS: dict[DraftSessionStatus, frozenset[DraftSessionStatus]] = {
    # The user is describing their intent.  We may detect a batch, need
    # clarification, or jump straight to a draftable single study.
    DraftSessionStatus.COLLECTING_INTENT: frozenset(
        {
            DraftSessionStatus.BATCH_REVIEW,
            DraftSessionStatus.CLARIFYING,
            DraftSessionStatus.DRAFT_READY,
        }
    ),
    # A batch of studies is being reviewed; the user picks one (or
    # cancels back to intent collection).
    DraftSessionStatus.BATCH_REVIEW: frozenset(
        {
            DraftSessionStatus.CLARIFYING,
            DraftSessionStatus.DRAFT_READY,
            DraftSessionStatus.COLLECTING_INTENT,
        }
    ),
    # Clarification round; can stay for another round, advance to a
    # draft, or restart.
    DraftSessionStatus.CLARIFYING: frozenset(
        {
            DraftSessionStatus.CLARIFYING,
            DraftSessionStatus.DRAFT_READY,
            DraftSessionStatus.COLLECTING_INTENT,
        }
    ),
    # A draft exists; user may request a change (pending proposal),
    # validate it (ready) or step back to clarification.
    DraftSessionStatus.DRAFT_READY: frozenset(
        {
            DraftSessionStatus.PROPOSAL_PENDING,
            DraftSessionStatus.READY,
            DraftSessionStatus.CLARIFYING,
        }
    ),
    # A change proposal is awaiting confirmation; both confirm and
    # cancel return to draft_ready.
    DraftSessionStatus.PROPOSAL_PENDING: frozenset(
        {DraftSessionStatus.DRAFT_READY}
    ),
    # Draft validated; user confirms the experiment version or keeps
    # editing.
    DraftSessionStatus.READY: frozenset(
        {DraftSessionStatus.CONFIRMED, DraftSessionStatus.DRAFT_READY}
    ),
    # Immutable confirmed snapshot; may start case planning or step
    # back to the draft.
    DraftSessionStatus.CONFIRMED: frozenset(
        {DraftSessionStatus.CASE_PLANNING, DraftSessionStatus.DRAFT_READY}
    ),
    # Case planning; may discover missing capabilities, finish with a
    # compiled case, or return to confirmed.
    DraftSessionStatus.CASE_PLANNING: frozenset(
        {
            DraftSessionStatus.AWAITING_CODE_EXTENSION,
            DraftSessionStatus.COMPILED,
            DraftSessionStatus.CONFIRMED,
        }
    ),
    # Waiting for code extension; once created we resume planning or
    # fall back to confirmed.
    DraftSessionStatus.AWAITING_CODE_EXTENSION: frozenset(
        {
            DraftSessionStatus.CASE_PLANNING,
            DraftSessionStatus.CONFIRMED,
        }
    ),
    # Case compiled; may submit a run or recompile.
    DraftSessionStatus.COMPILED: frozenset(
        {DraftSessionStatus.RUNNING, DraftSessionStatus.CONFIRMED}
    ),
    # Simulation running; ends in completed or failed.
    DraftSessionStatus.RUNNING: frozenset(
        {DraftSessionStatus.COMPLETED, DraftSessionStatus.FAILED}
    ),
    # Run finished successfully; may clone a new experiment or derive
    # from the confirmed snapshot.
    DraftSessionStatus.COMPLETED: frozenset(
        {
            DraftSessionStatus.COLLECTING_INTENT,
            DraftSessionStatus.CONFIRMED,
        }
    ),
    # Run failed; may retry compilation, create a fix version from the
    # confirmed snapshot, or restart.
    DraftSessionStatus.FAILED: frozenset(
        {
            DraftSessionStatus.COMPILED,
            DraftSessionStatus.CONFIRMED,
            DraftSessionStatus.COLLECTING_INTENT,
        }
    ),
}

# ---------------------------------------------------------------------------
# Button matrix (plan section 8.3)
# ---------------------------------------------------------------------------

_BUTTONS: dict[DraftSessionStatus, dict[str, object]] = {
    DraftSessionStatus.COLLECTING_INTENT: {
        "main_button": "继续描述需求",
        "auxiliary_button": "取消",
        "forbidden_actions": ["生成 Case"],
    },
    DraftSessionStatus.BATCH_REVIEW: {
        "main_button": "选择任务进入草案",
        "auxiliary_button": "生成全部摘要",
        "forbidden_actions": ["直接编译"],
    },
    DraftSessionStatus.CLARIFYING: {
        "main_button": "提交回答",
        "auxiliary_button": "使用推荐假设",
        "forbidden_actions": ["生成 Case"],
    },
    DraftSessionStatus.DRAFT_READY: {
        "main_button": "校验草案",
        "auxiliary_button": "提出修改",
        "forbidden_actions": ["提交运行"],
    },
    DraftSessionStatus.PROPOSAL_PENDING: {
        "main_button": "确认应用修改",
        "auxiliary_button": "取消修改",
        "forbidden_actions": ["生成 Case"],
    },
    DraftSessionStatus.READY: {
        "main_button": "确认实验版本",
        "auxiliary_button": "继续修改",
        "forbidden_actions": ["提交运行"],
    },
    DraftSessionStatus.CONFIRMED: {
        "main_button": "生成 CasePlan",
        "auxiliary_button": "克隆并修改",
        "forbidden_actions": ["原地修改"],
    },
    DraftSessionStatus.CASE_PLANNING: {
        "main_button": "查看 CasePlan",
        "auxiliary_button": "返回草案",
        "forbidden_actions": ["提交运行"],
    },
    DraftSessionStatus.AWAITING_CODE_EXTENSION: {
        "main_button": "查看缺失能力",
        "auxiliary_button": "创建扩展任务",
        "forbidden_actions": ["直接生成假 Case"],
    },
    DraftSessionStatus.COMPILED: {
        "main_button": "提交运行",
        "auxiliary_button": "查看 Case 文件",
        "forbidden_actions": ["应用修改"],
    },
    DraftSessionStatus.RUNNING: {
        "main_button": "查看运行状态",
        "auxiliary_button": "停止任务",
        "forbidden_actions": ["修改当前版本"],
    },
    DraftSessionStatus.COMPLETED: {
        "main_button": "查看分析报告",
        "auxiliary_button": "克隆新实验",
        "forbidden_actions": ["修改当前版本"],
    },
    DraftSessionStatus.FAILED: {
        "main_button": "创建修复版本",
        "auxiliary_button": "查看错误",
        "forbidden_actions": ["提交运行"],
    },
}


# ---------------------------------------------------------------------------
# DraftSessionStateMachine
# ---------------------------------------------------------------------------


class DraftSessionStateMachine:
    """Validate status transitions and expose the workbench button matrix."""

    _TRANSITIONS: dict[DraftSessionStatus, frozenset[DraftSessionStatus]] = (
        _TRANSITIONS
    )

    def can_transition(
        self,
        from_status: DraftSessionStatus,
        to_status: DraftSessionStatus,
    ) -> bool:
        """Return ``True`` if ``from_status`` may move to ``to_status``."""
        return to_status in self._TRANSITIONS.get(from_status, frozenset())

    def transition(
        self, session: DraftSession, to_status: DraftSessionStatus
    ) -> DraftSession:
        """Move ``session`` to ``to_status`` and return the updated copy.

        Args:
            session: The session to transition.
            to_status: The target status.

        Returns:
            A new :class:`DraftSession` with ``status`` and
            ``updated_at`` refreshed.

        Raises:
            TransitionError: If the transition is not allowed.
        """
        if not self.can_transition(session.status, to_status):
            raise TransitionError(
                f"draft session cannot transition from "
                f"'{session.status.value}' to '{to_status.value}'"
            )

        return session.model_copy(
            update={
                "status": to_status,
                "updated_at": datetime.now(UTC),
            }
        )

    def get_buttons(self, status: DraftSessionStatus) -> dict:
        """Return the workbench button info for ``status``.

        The returned dict always contains the keys ``main_button``,
        ``auxiliary_button`` and ``forbidden_actions``.  Unknown
        statuses yield empty values so the frontend can render safely.
        """
        info = _BUTTONS.get(status)
        if info is None:
            return {
                "main_button": None,
                "auxiliary_button": None,
                "forbidden_actions": [],
            }
        return {
            "main_button": info["main_button"],
            "auxiliary_button": info["auxiliary_button"],
            "forbidden_actions": list(info["forbidden_actions"]),  # type: ignore[arg-type]
        }

    def allowed_transitions(
        self, from_status: DraftSessionStatus
    ) -> frozenset[DraftSessionStatus]:
        """Return the set of statuses reachable from ``from_status``."""
        return self._TRANSITIONS.get(from_status, frozenset())

    def is_terminal(self, status: DraftSessionStatus) -> bool:
        """Return ``True`` if ``status`` has no outgoing transitions."""
        return len(self._TRANSITIONS.get(status, frozenset())) == 0


__all__ = [
    "DraftSessionStateMachine",
    "TransitionError",
]
