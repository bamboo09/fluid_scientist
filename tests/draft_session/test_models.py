"""Tests for draft_session.models."""

from __future__ import annotations

from datetime import datetime

from fluid_scientist.compat import UTC
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
    InputRoute,
    ResearchState,
    SessionMessage,
)

# ---------------------------------------------------------------------------
# DraftSessionStatus
# ---------------------------------------------------------------------------


class TestDraftSessionStatus:
    def test_all_expected_values_present(self) -> None:
        expected = {
            "collecting_intent",
            "batch_review",
            "clarifying",
            "draft_ready",
            "proposal_pending",
            "ready",
            "confirmed",
            "case_planning",
            "awaiting_code_extension",
            "compiled",
            "running",
            "completed",
            "failed",
        }
        actual = {status.value for status in DraftSessionStatus}
        assert actual == expected

    def test_str_value(self) -> None:
        assert str(DraftSessionStatus.COLLECTING_INTENT) == "collecting_intent"

    def test_is_str_enum(self) -> None:
        assert isinstance(DraftSessionStatus.COLLECTING_INTENT, str)


# ---------------------------------------------------------------------------
# DraftSession
# ---------------------------------------------------------------------------


class TestDraftSession:
    def test_default_creation(self) -> None:
        session = DraftSession(session_id="sess-1")
        assert session.session_id == "sess-1"
        assert session.user_id is None
        assert session.status is DraftSessionStatus.COLLECTING_INTENT
        assert session.batch_id is None
        assert session.selected_study_id is None
        assert session.research_state_id is None
        assert session.current_draft_id is None
        assert session.current_draft_version is None
        assert session.pending_question_ids == []
        assert session.pending_proposal_id is None
        assert session.pending_missing_capability_ids == []
        assert isinstance(session.created_at, datetime)
        assert isinstance(session.updated_at, datetime)

    def test_default_timestamps_are_utc(self) -> None:
        session = DraftSession(session_id="sess-1")
        assert session.created_at.tzinfo is UTC
        assert session.updated_at.tzinfo is UTC

    def test_pending_lists_are_independent(self) -> None:
        """Mutable defaults must not leak between instances."""
        a = DraftSession(session_id="a")
        b = DraftSession(session_id="b")
        a.pending_question_ids.append("q-1")
        a.pending_missing_capability_ids.append("cap-1")
        assert b.pending_question_ids == []
        assert b.pending_missing_capability_ids == []

    def test_full_creation(self) -> None:
        session = DraftSession(
            session_id="sess-2",
            user_id="user-1",
            status=DraftSessionStatus.PROPOSAL_PENDING,
            batch_id="batch-1",
            selected_study_id="study-1",
            research_state_id="rs-1",
            current_draft_id="draft-1",
            current_draft_version=3,
            pending_question_ids=["q-1", "q-2"],
            pending_proposal_id="prop-1",
            pending_missing_capability_ids=["cap-1"],
        )
        assert session.user_id == "user-1"
        assert session.status is DraftSessionStatus.PROPOSAL_PENDING
        assert session.batch_id == "batch-1"
        assert session.selected_study_id == "study-1"
        assert session.current_draft_version == 3
        assert session.pending_question_ids == ["q-1", "q-2"]


# ---------------------------------------------------------------------------
# SessionMessage
# ---------------------------------------------------------------------------


class TestSessionMessage:
    def test_user_research_request(self) -> None:
        msg = SessionMessage(
            message_id="m-1",
            session_id="sess-1",
            role="user",
            message_type="research_request",
            content="研究后台阶流动",
        )
        assert msg.role == "user"
        assert msg.message_type == "research_request"
        assert msg.linked_study_id is None
        assert isinstance(msg.created_at, datetime)

    def test_assistant_clarification_question_with_links(self) -> None:
        msg = SessionMessage(
            message_id="m-2",
            session_id="sess-1",
            role="assistant",
            message_type="clarification_question",
            content="请确认 Reynolds 数",
            linked_question_id="q-1",
            linked_study_id="study-1",
        )
        assert msg.role == "assistant"
        assert msg.linked_question_id == "q-1"
        assert msg.linked_study_id == "study-1"

    def test_change_proposal_with_draft_links(self) -> None:
        msg = SessionMessage(
            message_id="m-3",
            session_id="sess-1",
            role="assistant",
            message_type="change_proposal",
            content="建议将入口速度改为 2 m/s",
            linked_proposal_id="prop-1",
            linked_draft_id="draft-1",
            linked_draft_version=2,
        )
        assert msg.linked_proposal_id == "prop-1"
        assert msg.linked_draft_id == "draft-1"
        assert msg.linked_draft_version == 2

    def test_invalid_role_rejected(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SessionMessage(
                message_id="m-x",
                session_id="sess-1",
                role="bot",  # type: ignore[arg-type]
                message_type="error",
                content="x",
            )

    def test_invalid_message_type_rejected(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SessionMessage(
                message_id="m-x",
                session_id="sess-1",
                role="user",
                message_type="not_a_real_type",  # type: ignore[arg-type]
                content="x",
            )


# ---------------------------------------------------------------------------
# ResearchState
# ---------------------------------------------------------------------------


class TestResearchState:
    def test_minimal_creation(self) -> None:
        now = datetime.now(UTC)
        state = ResearchState(
            research_state_id="rs-1",
            session_id="sess-1",
            original_user_request="研究后台阶流动",
            created_at=now,
            updated_at=now,
        )
        assert state.research_state_id == "rs-1"
        assert state.session_id == "sess-1"
        assert state.original_user_request == "研究后台阶流动"
        assert state.selected_study_id is None
        assert state.confirmed_facts == {}
        assert state.accepted_assumptions == {}
        assert state.rejected_assumptions == {}
        assert state.unknowns == []
        assert state.blocking_issues == []
        assert state.physical_intent is None
        assert state.study_intent is None
        assert state.last_updated_by_message_id is None
        assert state.version == 1

    def test_full_creation(self) -> None:
        now = datetime.now(UTC)
        state = ResearchState(
            research_state_id="rs-2",
            session_id="sess-1",
            selected_study_id="study-1",
            original_user_request="研究后台阶流动",
            confirmed_facts={"reynolds_number": 5000},
            accepted_assumptions={"turbulence_model": "k-omega SST"},
            rejected_assumptions={"inlet_profile": "uniform"},
            unknowns=[{"field": "outlet_length", "reason": "unspecified"}],
            blocking_issues=[{"issue": "missing capability"}],
            physical_intent={"dimension": "3D", "temporal": "transient"},
            study_intent={"study_type": "backward_step"},
            last_updated_by_message_id="m-1",
            version=4,
            created_at=now,
            updated_at=now,
        )
        assert state.confirmed_facts == {"reynolds_number": 5000}
        assert state.accepted_assumptions == {"turbulence_model": "k-omega SST"}
        assert state.version == 4
        assert state.study_intent == {"study_type": "backward_step"}

    def test_mutable_defaults_are_independent(self) -> None:
        now = datetime.now(UTC)
        a = ResearchState(
            research_state_id="rs-a",
            session_id="sess-1",
            original_user_request="a",
            created_at=now,
            updated_at=now,
        )
        b = ResearchState(
            research_state_id="rs-b",
            session_id="sess-1",
            original_user_request="b",
            created_at=now,
            updated_at=now,
        )
        a.confirmed_facts["k"] = "v"
        a.unknowns.append({"x": 1})
        assert b.confirmed_facts == {}
        assert b.unknowns == []


# ---------------------------------------------------------------------------
# InputRoute
# ---------------------------------------------------------------------------


class TestInputRoute:
    def test_create(self) -> None:
        route = InputRoute(
            input_type="new_research_request",
            confidence=0.5,
            reason="default",
            should_call_llm=True,
        )
        assert route.input_type == "new_research_request"
        assert route.confidence == 0.5
        assert route.should_call_llm is True

    def test_invalid_input_type_rejected(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            InputRoute(
                input_type="bogus",  # type: ignore[arg-type]
                confidence=0.5,
                reason="x",
                should_call_llm=False,
            )
