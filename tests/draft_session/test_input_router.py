"""Tests for draft_session.input_router.

Each of the ten routing rules is exercised, plus edge cases around
rule ordering (state-based rules must win over content-based rules).
"""

from __future__ import annotations

import pytest

from fluid_scientist.draft_session.input_router import InputRouter
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
)


@pytest.fixture()
def router() -> InputRouter:
    return InputRouter()


def _session(**overrides: object) -> DraftSession:
    """Build a DraftSession with sensible defaults + overrides."""
    base: dict[str, object] = {"session_id": "sess-1"}
    base.update(overrides)
    return DraftSession(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Rule 1 & 2: pending proposal confirmation / cancellation
# ---------------------------------------------------------------------------


class TestProposalRouting:
    @pytest.mark.parametrize(
        "message",
        ["确认", "可以", "应用这个修改", "就这样", "ok", "confirm", "apply"],
    )
    def test_rule1_confirm_keywords(
        self, router: InputRouter, message: str
    ) -> None:
        session = _session(pending_proposal_id="prop-1")
        route = router.route(message, session)
        assert route.input_type == "proposal_confirmation"
        assert route.confidence >= 0.9
        assert route.should_call_llm is False

    @pytest.mark.parametrize(
        "message",
        ["取消", "不要", "放弃", "cancel", "no"],
    )
    def test_rule2_cancel_keywords(
        self, router: InputRouter, message: str
    ) -> None:
        session = _session(pending_proposal_id="prop-1")
        route = router.route(message, session)
        assert route.input_type == "proposal_cancel"
        assert route.confidence >= 0.9
        assert route.should_call_llm is False

    def test_confirm_keywords_without_pending_proposal_is_not_confirmation(
        self, router: InputRouter
    ) -> None:
        """Without a pending proposal, '确认' should NOT route to confirmation."""
        session = _session()  # no pending_proposal_id
        route = router.route("确认", session)
        assert route.input_type != "proposal_confirmation"

    def test_confirm_wins_over_change_keywords(
        self, router: InputRouter
    ) -> None:
        """Rule 1 must beat rule 6 even if change words are present."""
        session = _session(
            pending_proposal_id="prop-1", current_draft_id="draft-1"
        )
        route = router.route("确认，把速度改成 2", session)
        assert route.input_type == "proposal_confirmation"


# ---------------------------------------------------------------------------
# Rule 3: batch_review + selection keywords
# ---------------------------------------------------------------------------


class TestStudySelection:
    @pytest.mark.parametrize(
        "message",
        ["第一个", "选择第二个", "后台阶流动", "select the first", "choose 2"],
    )
    def test_rule3_selection_in_batch_review(
        self, router: InputRouter, message: str
    ) -> None:
        session = _session(status=DraftSessionStatus.BATCH_REVIEW)
        route = router.route(message, session)
        assert route.input_type == "study_selection"
        assert route.confidence >= 0.9
        assert route.should_call_llm is False

    def test_selection_keywords_outside_batch_review_not_selection(
        self, router: InputRouter
    ) -> None:
        session = _session(status=DraftSessionStatus.COLLECTING_INTENT)
        route = router.route("选择第一个", session)
        assert route.input_type != "study_selection"


# ---------------------------------------------------------------------------
# Rule 4: clarifying + pending questions
# ---------------------------------------------------------------------------


class TestClarificationAnswer:
    def test_rule4_clarifying_with_pending_questions(
        self, router: InputRouter
    ) -> None:
        session = _session(
            status=DraftSessionStatus.CLARIFYING,
            pending_question_ids=["q-1"],
        )
        route = router.route("Reynolds 数是 5000", session)
        assert route.input_type == "clarification_answer"
        assert route.confidence >= 0.9
        assert route.should_call_llm is False

    def test_clarifying_without_pending_questions_not_answer(
        self, router: InputRouter
    ) -> None:
        session = _session(status=DraftSessionStatus.CLARIFYING)
        route = router.route("一些信息", session)
        # Falls through to default since no pending questions.
        assert route.input_type == "new_research_request"


# ---------------------------------------------------------------------------
# Rule 5: numbered list -> batch_research_request
# ---------------------------------------------------------------------------


class TestBatchResearchRequest:
    def test_rule5_numbered_list_with_periods(self, router: InputRouter) -> None:
        session = _session()
        message = (
            "1. 后台阶 Re=5000\n"
            "2. 圆柱绕流 Re=100\n"
            "3. 管道流动"
        )
        route = router.route(message, session)
        assert route.input_type == "batch_research_request"
        assert route.confidence >= 0.9
        assert route.should_call_llm is True

    def test_rule5_numbered_list_with_parens(self, router: InputRouter) -> None:
        session = _session()
        message = "1) 后台阶\n2) 圆柱绕流"
        route = router.route(message, session)
        assert route.input_type == "batch_research_request"

    def test_decimal_not_treated_as_batch(self, router: InputRouter) -> None:
        session = _session()
        route = router.route("入口速度 2.5 m/s", session)
        assert route.input_type != "batch_research_request"

    def test_batch_wins_over_question_keywords(
        self, router: InputRouter
    ) -> None:
        """Rule 5 must beat rule 7."""
        session = _session()
        message = "1. 为什么后台阶\n2. 圆柱是什么"
        route = router.route(message, session)
        assert route.input_type == "batch_research_request"


# ---------------------------------------------------------------------------
# Rule 6: change keywords + existing draft
# ---------------------------------------------------------------------------


class TestDraftChangeRequest:
    @pytest.mark.parametrize(
        "message",
        [
            "把速度改成 2 m/s",
            "增加一个出口边界条件",
            "删除入口扰动",
            "换成 k-epsilon 模型",
            "输出残差曲线",
            "加入温度场",
            "change the velocity",
            "add a boundary",
            "remove the inlet",
        ],
    )
    def test_rule6_change_keywords_with_draft(
        self, router: InputRouter, message: str
    ) -> None:
        session = _session(current_draft_id="draft-1")
        route = router.route(message, session)
        assert route.input_type == "draft_change_request"
        assert route.confidence >= 0.9
        assert route.should_call_llm is True

    def test_change_keywords_without_draft_not_change_request(
        self, router: InputRouter
    ) -> None:
        session = _session()  # no current_draft_id
        route = router.route("把速度改成 2", session)
        assert route.input_type != "draft_change_request"


# ---------------------------------------------------------------------------
# Rule 7: question keywords
# ---------------------------------------------------------------------------


class TestQuestionAboutDraft:
    @pytest.mark.parametrize(
        "message",
        [
            "为什么用 k-omega",
            "Reynolds 数是什么",
            "湍流模型什么意思",
            "边界条件有什么影响",
            "why is the mesh refined",
            "what is the solver",
        ],
    )
    def test_rule7_question_keywords(
        self, router: InputRouter, message: str
    ) -> None:
        session = _session()
        route = router.route(message, session)
        assert route.input_type == "question_about_draft"
        assert route.confidence >= 0.9
        assert route.should_call_llm is True


# ---------------------------------------------------------------------------
# Rule 8: compile keywords
# ---------------------------------------------------------------------------


class TestCompileRequest:
    @pytest.mark.parametrize(
        "message",
        ["生成 case", "编译这个算例", "compile the case", "openfoam case please"],
    )
    def test_rule8_compile_keywords(
        self, router: InputRouter, message: str
    ) -> None:
        session = _session()
        route = router.route(message, session)
        assert route.input_type == "compile_request"
        assert route.confidence >= 0.9
        assert route.should_call_llm is True


# ---------------------------------------------------------------------------
# Rule 9: run keywords
# ---------------------------------------------------------------------------


class TestRunRequest:
    @pytest.mark.parametrize(
        "message",
        ["运行这个算例", "run the simulation", "submit the job"],
    )
    def test_rule9_run_keywords(self, router: InputRouter, message: str) -> None:
        session = _session()
        route = router.route(message, session)
        assert route.input_type == "run_request"
        assert route.confidence >= 0.9
        assert route.should_call_llm is True


# ---------------------------------------------------------------------------
# Rule 10: default
# ---------------------------------------------------------------------------


class TestDefaultRoute:
    def test_rule10_default_new_research_request(
        self, router: InputRouter
    ) -> None:
        session = _session()
        route = router.route("研究后台阶流动的分离再附", session)
        assert route.input_type == "new_research_request"
        assert route.confidence == 0.5
        assert route.should_call_llm is True

    def test_empty_message_defaults_to_new_research(
        self, router: InputRouter
    ) -> None:
        session = _session()
        route = router.route("", session)
        assert route.input_type == "new_research_request"

    def test_reason_is_non_empty(self, router: InputRouter) -> None:
        session = _session()
        route = router.route("研究后台阶", session)
        assert route.reason
        assert isinstance(route.reason, str)


# ---------------------------------------------------------------------------
# Ordering / precedence sanity
# ---------------------------------------------------------------------------


class TestRulePrecedence:
    def test_proposal_confirm_beats_run_keyword(self, router: InputRouter) -> None:
        """A confirm keyword with a pending proposal must not become run_request."""
        session = _session(pending_proposal_id="prop-1")
        route = router.route("ok 运行吧", session)
        assert route.input_type == "proposal_confirmation"

    def test_clarifying_answer_beats_question_keyword(
        self, router: InputRouter
    ) -> None:
        """In clarifying state, an answer wins over question keywords."""
        session = _session(
            status=DraftSessionStatus.CLARIFYING,
            pending_question_ids=["q-1"],
        )
        route = router.route("为什么是 5000", session)
        assert route.input_type == "clarification_answer"

    def test_compile_beats_run(self, router: InputRouter) -> None:
        """Compile keywords (rule 8) are checked before run keywords (rule 9)."""
        session = _session()
        route = router.route("编译并运行", session)
        assert route.input_type == "compile_request"
