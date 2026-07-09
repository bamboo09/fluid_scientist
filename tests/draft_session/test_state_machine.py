"""Tests for draft_session.state_machine."""

from __future__ import annotations

from datetime import datetime

import pytest

from fluid_scientist.compat import UTC
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
)
from fluid_scientist.draft_session.state_machine import (
    DraftSessionStateMachine,
    TransitionError,
)


@pytest.fixture()
def sm() -> DraftSessionStateMachine:
    return DraftSessionStateMachine()


def _session(status: DraftSessionStatus) -> DraftSession:
    return DraftSession(session_id="sess-1", status=status)


# ---------------------------------------------------------------------------
# can_transition
# ---------------------------------------------------------------------------


class TestCanTransition:
    @pytest.mark.parametrize(
        "frm,to",
        [
            (DraftSessionStatus.COLLECTING_INTENT, DraftSessionStatus.BATCH_REVIEW),
            (DraftSessionStatus.COLLECTING_INTENT, DraftSessionStatus.CLARIFYING),
            (DraftSessionStatus.COLLECTING_INTENT, DraftSessionStatus.DRAFT_READY),
            (DraftSessionStatus.BATCH_REVIEW, DraftSessionStatus.DRAFT_READY),
            (DraftSessionStatus.BATCH_REVIEW, DraftSessionStatus.COLLECTING_INTENT),
            (DraftSessionStatus.CLARIFYING, DraftSessionStatus.CLARIFYING),
            (DraftSessionStatus.DRAFT_READY, DraftSessionStatus.PROPOSAL_PENDING),
            (DraftSessionStatus.DRAFT_READY, DraftSessionStatus.READY),
            (DraftSessionStatus.PROPOSAL_PENDING, DraftSessionStatus.DRAFT_READY),
            (DraftSessionStatus.READY, DraftSessionStatus.CONFIRMED),
            (DraftSessionStatus.CONFIRMED, DraftSessionStatus.CASE_PLANNING),
            (DraftSessionStatus.CASE_PLANNING, DraftSessionStatus.COMPILED),
            (
                DraftSessionStatus.CASE_PLANNING,
                DraftSessionStatus.AWAITING_CODE_EXTENSION,
            ),
            (DraftSessionStatus.COMPILED, DraftSessionStatus.RUNNING),
            (DraftSessionStatus.RUNNING, DraftSessionStatus.COMPLETED),
            (DraftSessionStatus.RUNNING, DraftSessionStatus.FAILED),
            (DraftSessionStatus.COMPLETED, DraftSessionStatus.CONFIRMED),
            (DraftSessionStatus.FAILED, DraftSessionStatus.COMPILED),
        ],
    )
    def test_allowed_transitions(
        self,
        sm: DraftSessionStateMachine,
        frm: DraftSessionStatus,
        to: DraftSessionStatus,
    ) -> None:
        assert sm.can_transition(frm, to) is True

    @pytest.mark.parametrize(
        "frm,to",
        [
            # Cannot skip stages.
            (DraftSessionStatus.COLLECTING_INTENT, DraftSessionStatus.CONFIRMED),
            (DraftSessionStatus.COLLECTING_INTENT, DraftSessionStatus.RUNNING),
            # Proposal pending cannot jump to confirmed directly.
            (DraftSessionStatus.PROPOSAL_PENDING, DraftSessionStatus.READY),
            (DraftSessionStatus.PROPOSAL_PENDING, DraftSessionStatus.CONFIRMED),
            # Confirmed cannot run directly.
            (DraftSessionStatus.CONFIRMED, DraftSessionStatus.RUNNING),
            # Compiled cannot go back to draft directly.
            (DraftSessionStatus.COMPILED, DraftSessionStatus.DRAFT_READY),
            # Running cannot go back to compiled.
            (DraftSessionStatus.RUNNING, DraftSessionStatus.COMPILED),
            # Completed cannot run.
            (DraftSessionStatus.COMPLETED, DraftSessionStatus.RUNNING),
            # Failed cannot go to ready directly.
            (DraftSessionStatus.FAILED, DraftSessionStatus.READY),
        ],
    )
    def test_forbidden_transitions(
        self,
        sm: DraftSessionStateMachine,
        frm: DraftSessionStatus,
        to: DraftSessionStatus,
    ) -> None:
        assert sm.can_transition(frm, to) is False


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


class TestTransition:
    def test_transition_updates_status_and_returns_copy(
        self, sm: DraftSessionStateMachine
    ) -> None:
        before = _session(DraftSessionStatus.COLLECTING_INTENT)
        original_updated_at = before.updated_at
        after = sm.transition(before, DraftSessionStatus.DRAFT_READY)
        assert after.status is DraftSessionStatus.DRAFT_READY
        # Original is untouched (immutable copy).
        assert before.status is DraftSessionStatus.COLLECTING_INTENT
        # updated_at is bumped.
        assert after.updated_at >= original_updated_at
        assert after.updated_at.tzinfo is UTC

    def test_transition_does_not_mutate_original(
        self, sm: DraftSessionStateMachine
    ) -> None:
        session = _session(DraftSessionStatus.READY)
        sm.transition(session, DraftSessionStatus.CONFIRMED)
        assert session.status is DraftSessionStatus.READY

    def test_invalid_transition_raises(
        self, sm: DraftSessionStateMachine
    ) -> None:
        session = _session(DraftSessionStatus.COLLECTING_INTENT)
        with pytest.raises(TransitionError, match="cannot transition"):
            sm.transition(session, DraftSessionStatus.RUNNING)

    def test_self_transition_clarifying_allowed(
        self, sm: DraftSessionStateMachine
    ) -> None:
        session = _session(DraftSessionStatus.CLARIFYING)
        after = sm.transition(session, DraftSessionStatus.CLARIFYING)
        assert after.status is DraftSessionStatus.CLARIFYING

    def test_full_happy_path(self, sm: DraftSessionStateMachine) -> None:
        """Exercise the canonical happy-path lifecycle."""
        session = DraftSession(session_id="sess-1")
        path = [
            DraftSessionStatus.DRAFT_READY,
            DraftSessionStatus.READY,
            DraftSessionStatus.CONFIRMED,
            DraftSessionStatus.CASE_PLANNING,
            DraftSessionStatus.COMPILED,
            DraftSessionStatus.RUNNING,
            DraftSessionStatus.COMPLETED,
        ]
        for target in path:
            session = sm.transition(session, target)
            assert session.status is target

    def test_error_path_failed_to_compiled(
        self, sm: DraftSessionStateMachine
    ) -> None:
        session = _session(DraftSessionStatus.RUNNING)
        session = sm.transition(session, DraftSessionStatus.FAILED)
        assert session.status is DraftSessionStatus.FAILED
        session = sm.transition(session, DraftSessionStatus.COMPILED)
        assert session.status is DraftSessionStatus.COMPILED


# ---------------------------------------------------------------------------
# get_buttons
# ---------------------------------------------------------------------------


class TestGetButtons:
    def test_collecting_intent_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.COLLECTING_INTENT)
        assert buttons["main_button"] == "继续描述需求"
        assert buttons["auxiliary_button"] == "取消"
        assert buttons["forbidden_actions"] == ["生成 Case"]

    def test_batch_review_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.BATCH_REVIEW)
        assert buttons["main_button"] == "选择任务进入草案"
        assert buttons["auxiliary_button"] == "生成全部摘要"
        assert buttons["forbidden_actions"] == ["直接编译"]

    def test_clarifying_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.CLARIFYING)
        assert buttons["main_button"] == "提交回答"
        assert buttons["auxiliary_button"] == "使用推荐假设"
        assert buttons["forbidden_actions"] == ["生成 Case"]

    def test_draft_ready_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.DRAFT_READY)
        assert buttons["main_button"] == "校验草案"
        assert buttons["auxiliary_button"] == "提出修改"
        assert buttons["forbidden_actions"] == ["提交运行"]

    def test_proposal_pending_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.PROPOSAL_PENDING)
        assert buttons["main_button"] == "确认应用修改"
        assert buttons["auxiliary_button"] == "取消修改"
        assert buttons["forbidden_actions"] == ["生成 Case"]

    def test_ready_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.READY)
        assert buttons["main_button"] == "确认实验版本"
        assert buttons["auxiliary_button"] == "继续修改"
        assert buttons["forbidden_actions"] == ["提交运行"]

    def test_confirmed_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.CONFIRMED)
        assert buttons["main_button"] == "生成 CasePlan"
        assert buttons["auxiliary_button"] == "克隆并修改"
        assert buttons["forbidden_actions"] == ["原地修改"]

    def test_case_planning_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.CASE_PLANNING)
        assert buttons["main_button"] == "查看 CasePlan"
        assert buttons["auxiliary_button"] == "返回草案"
        assert buttons["forbidden_actions"] == ["提交运行"]

    def test_awaiting_code_extension_buttons(
        self, sm: DraftSessionStateMachine
    ) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.AWAITING_CODE_EXTENSION)
        assert buttons["main_button"] == "查看缺失能力"
        assert buttons["auxiliary_button"] == "创建扩展任务"
        assert buttons["forbidden_actions"] == ["直接生成假 Case"]

    def test_compiled_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.COMPILED)
        assert buttons["main_button"] == "提交运行"
        assert buttons["auxiliary_button"] == "查看 Case 文件"
        assert buttons["forbidden_actions"] == ["应用修改"]

    def test_running_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.RUNNING)
        assert buttons["main_button"] == "查看运行状态"
        assert buttons["auxiliary_button"] == "停止任务"
        assert buttons["forbidden_actions"] == ["修改当前版本"]

    def test_completed_buttons(self, sm: DraftSessionStateMachine) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.COMPLETED)
        assert buttons["main_button"] == "查看分析报告"
        assert buttons["auxiliary_button"] == "克隆新实验"
        assert buttons["forbidden_actions"] == ["修改当前版本"]

    def test_failed_buttons_has_safe_defaults(
        self, sm: DraftSessionStateMachine
    ) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.FAILED)
        assert buttons["main_button"] == "创建修复版本"
        assert buttons["forbidden_actions"] == ["提交运行"]

    def test_all_statuses_have_button_entries(
        self, sm: DraftSessionStateMachine
    ) -> None:
        for status in DraftSessionStatus:
            buttons = sm.get_buttons(status)
            assert "main_button" in buttons
            assert "auxiliary_button" in buttons
            assert "forbidden_actions" in buttons

    def test_forbidden_actions_returned_as_copy(
        self, sm: DraftSessionStateMachine
    ) -> None:
        buttons = sm.get_buttons(DraftSessionStatus.COLLECTING_INTENT)
        buttons["forbidden_actions"].append("injected")
        fresh = sm.get_buttons(DraftSessionStatus.COLLECTING_INTENT)
        assert "injected" not in fresh["forbidden_actions"]


# ---------------------------------------------------------------------------
# allowed_transitions / is_terminal
# ---------------------------------------------------------------------------


class TestAllowedTransitionsAndTerminal:
    def test_allowed_transitions_non_empty_for_non_terminal(
        self, sm: DraftSessionStateMachine
    ) -> None:
        result = sm.allowed_transitions(DraftSessionStatus.DRAFT_READY)
        assert DraftSessionStatus.READY in result
        assert DraftSessionStatus.PROPOSAL_PENDING in result

    def test_is_terminal_false_for_all_defined_statuses(
        self, sm: DraftSessionStateMachine
    ) -> None:
        """No draft session status is strictly terminal; even completed/failed
        allow cloning or retry."""
        for status in DraftSessionStatus:
            assert sm.is_terminal(status) is False

    def test_allowed_transitions_returns_frozenset(
        self, sm: DraftSessionStateMachine
    ) -> None:
        result = sm.allowed_transitions(DraftSessionStatus.READY)
        assert isinstance(result, frozenset)


# ---------------------------------------------------------------------------
# Timestamp sanity
# ---------------------------------------------------------------------------


class TestTimestamps:
    def test_transition_sets_utc_timestamp(
        self, sm: DraftSessionStateMachine
    ) -> None:
        session = _session(DraftSessionStatus.COLLECTING_INTENT)
        after = sm.transition(session, DraftSessionStatus.CLARIFYING)
        assert isinstance(after.updated_at, datetime)
        assert after.updated_at.tzinfo is UTC
