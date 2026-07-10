"""State-aware input router for draft sessions."""

from __future__ import annotations

import re

from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
    InputRoute,
)

_CONFIRM_KEYWORDS = (
    "确认",
    "可以",
    "应用",
    "同意",
    "就这样",
    "ok",
    "confirm",
    "apply",
    "yes",
)
_CANCEL_KEYWORDS = ("取消", "不要", "放弃", "不同意", "cancel", "no", "reject")
_SELECTION_KEYWORDS = (
    "选择",
    "第一个",
    "第二个",
    "第三个",
    "select",
    "choose",
)
_CHANGE_KEYWORDS = (
    "改成",
    "改为",
    "设为",
    "设置为",
    "修改",
    "补充",
    "增加",
    "删除",
    "换成",
    "change",
    "add",
    "remove",
    "set",
)
_QUESTION_KEYWORDS = ("为什么", "是什么", "什么", "如何", "吗", "why", "what", "how")
_COMPILE_KEYWORDS = ("生成 case", "编译", "compile", "openfoam case")
_RUN_KEYWORDS = ("运行", "run", "submit")
_NEW_RESEARCH_KEYWORDS = (
    "新建研究",
    "新建一个",
    "开始另一个",
    "另一个实验",
    "新的研究",
    "new study",
    "new research",
    "start another",
)
_DRAFT_SUPPLEMENT_KEYWORDS = (
    "边界条件",
    "自由滑移",
    "free slip",
    "free_slip",
    "入口",
    "出口",
    "上边界",
    "下边界",
    "左边界",
    "右边界",
    "inlet",
    "outlet",
    "top",
    "bottom",
    "wall",
    "boundary",
)

_NUMBERED_LIST_PATTERN = re.compile(r"(?:^|\n)\s*\d+\s*[.)、]\s+")


def _contains_any(message: str, keywords: tuple[str, ...]) -> bool:
    lowered = message.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _contains_non_ascii(message: str) -> bool:
    return any(ord(ch) > 127 for ch in message)


class InputRouter:
    """Route one user message using state-first strong rules."""

    def route(self, user_message: str, session: DraftSession) -> InputRoute:
        message = user_message or ""

        if session.pending_proposal_id and _contains_any(message, _CONFIRM_KEYWORDS):
            return InputRoute(
                input_type="proposal_confirmation",
                intent="CONFIRM_PROPOSAL",
                confidence=0.95,
                reason="Pending proposal plus confirmation wording.",
                should_call_llm=False,
            )

        if session.pending_proposal_id and _contains_any(message, _CANCEL_KEYWORDS):
            return InputRoute(
                input_type="proposal_cancel",
                intent="REJECT_PROPOSAL",
                confidence=0.95,
                reason="Pending proposal plus cancellation wording.",
                should_call_llm=False,
            )

        if (
            session.status is DraftSessionStatus.BATCH_REVIEW
            and (_contains_any(message, _SELECTION_KEYWORDS) or _contains_non_ascii(message))
        ):
            return InputRoute(
                input_type="study_selection",
                intent="SELECT_STUDY",
                confidence=0.9,
                reason="Batch review with study-selection wording.",
                should_call_llm=False,
            )

        if (
            session.status is DraftSessionStatus.CLARIFYING
            and session.pending_question_ids
        ):
            return InputRoute(
                input_type="clarification_answer",
                intent="ANSWER_CLARIFICATION",
                confidence=0.9,
                reason="Clarifying session with pending questions.",
                should_call_llm=False,
            )

        if _NUMBERED_LIST_PATTERN.search(message):
            return InputRoute(
                input_type="batch_research_request",
                intent="NEW_RESEARCH",
                confidence=0.9,
                reason="Message contains a numbered study list.",
                should_call_llm=True,
            )

        if _contains_any(message, _NEW_RESEARCH_KEYWORDS):
            return InputRoute(
                input_type="new_research_request",
                intent="NEW_RESEARCH",
                confidence=0.92,
                reason="Message explicitly asks to start a new study.",
                should_call_llm=True,
            )

        if session.current_draft_id is not None:
            if _contains_any(message, _QUESTION_KEYWORDS):
                return InputRoute(
                    input_type="question_about_draft",
                    intent="ASK_ABOUT_DRAFT",
                    confidence=0.9,
                    reason="Active draft and question wording.",
                    should_call_llm=True,
                )
            if _contains_any(message, _CHANGE_KEYWORDS):
                return InputRoute(
                    input_type="draft_change_request",
                    intent="MODIFY_DRAFT",
                    confidence=0.9,
                    reason="Active draft and modification wording.",
                    should_call_llm=True,
                )
            if _contains_any(message, _DRAFT_SUPPLEMENT_KEYWORDS):
                return InputRoute(
                    input_type="draft_change_request",
                    intent="SUPPLEMENT_DRAFT",
                    confidence=0.82,
                    reason="Active draft and likely boundary-condition supplement.",
                    should_call_llm=True,
                )
            if _contains_non_ascii(message):
                return InputRoute(
                    input_type="draft_change_request",
                    intent="MODIFY_DRAFT",
                    confidence=0.9,
                    reason="Active draft and legacy non-ASCII modification wording.",
                    should_call_llm=True,
                )
            return InputRoute(
                input_type="unknown",
                intent="UNRESOLVED",
                confidence=0.4,
                reason="No strong rule matched in an active draft context.",
                should_call_llm=True,
            )

        if _contains_any(message, _QUESTION_KEYWORDS):
            return InputRoute(
                input_type="question_about_draft",
                intent="ASK_ABOUT_DRAFT",
                confidence=0.9,
                reason="Message contains question wording.",
                should_call_llm=True,
            )

        if _contains_any(message, _COMPILE_KEYWORDS):
            return InputRoute(
                input_type="compile_request",
                intent="UNRESOLVED",
                confidence=0.9,
                reason="Message contains compile wording.",
                should_call_llm=True,
            )

        if _contains_any(message, _RUN_KEYWORDS):
            return InputRoute(
                input_type="run_request",
                intent="UNRESOLVED",
                confidence=0.9,
                reason="Message contains run wording.",
                should_call_llm=True,
            )

        return InputRoute(
            input_type="new_research_request",
            intent="NEW_RESEARCH",
            confidence=0.5,
            reason="No strong rule matched; requires model intent classification.",
            should_call_llm=True,
        )


__all__ = ["InputRouter"]
