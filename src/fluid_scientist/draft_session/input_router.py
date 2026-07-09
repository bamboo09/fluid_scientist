"""Strong-rule-first, LLM-fallback input router for draft sessions.

The :class:`InputRouter` classifies an incoming user message into one of
the canonical :class:`InputRoute` categories.  Classification is driven
by a fixed, ordered set of *strong rules* that combine keyword
detection with the current :class:`DraftSession` state.  When no strong
rule fires the router falls back to ``new_research_request`` and flags
the decision as ambiguous so the orchestrator can optionally refine it
with an LLM call.

The rule ordering matters: state-based rules (pending proposal, batch
review, clarifying) are checked *before* content-based rules so that,
for example, ``"ok"`` resolves to ``proposal_confirmation`` when a
proposal is pending rather than to a generic acknowledgement.
"""

from __future__ import annotations

import re

from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
    InputRoute,
)

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

_CONFIRM_KEYWORDS: tuple[str, ...] = (
    "确认",
    "可以",
    "应用",
    "就这样",
    "ok",
    "confirm",
    "apply",
)
_CANCEL_KEYWORDS: tuple[str, ...] = (
    "取消",
    "不要",
    "放弃",
    "cancel",
    "no",
)
_SELECTION_KEYWORDS: tuple[str, ...] = (
    "第",
    "选择",
    "后台阶",
    "select",
    "choose",
)
_CHANGE_KEYWORDS: tuple[str, ...] = (
    "改成",
    "增加",
    "删除",
    "换成",
    "输出",
    "加入",
    "change",
    "add",
    "remove",
)
_QUESTION_KEYWORDS: tuple[str, ...] = (
    "为什么",
    "是什么",
    "什么意思",
    "有什么影响",
    "why",
    "what",
)
_COMPILE_KEYWORDS: tuple[str, ...] = (
    "生成 case",
    "编译",
    "compile",
    "openfoam case",
)
_RUN_KEYWORDS: tuple[str, ...] = ("运行", "run", "submit")

# Matches a numbered list item such as "1. ", "2) " or "1、".
_NUMBERED_LIST_PATTERN = re.compile(r"(?:^|\n)\s*\d+\s*[.、)]\s+")


def _contains_any(message: str, keywords: tuple[str, ...]) -> bool:
    """Case-insensitive substring search for any of ``keywords``."""
    lowered = message.lower()
    return any(keyword in lowered for keyword in keywords)


# ---------------------------------------------------------------------------
# InputRouter
# ---------------------------------------------------------------------------


class InputRouter:
    """Route a user message to an :class:`InputRoute`.

    The router is stateless: it derives its decision purely from the
    message text and the supplied :class:`DraftSession`.  Callers are
    expected to pass the *current* session snapshot so the router can
    account for pending proposals, clarifying questions, etc.
    """

    def route(self, user_message: str, session: DraftSession) -> InputRoute:
        """Route ``user_message`` based on strong rules first.

        Rules are evaluated in the order documented in the module
        docstring.  The first matching rule wins.  If no rule matches
        the router returns ``new_research_request`` with a low
        confidence and ``should_call_llm=True`` so the orchestrator can
        refine the decision.
        """
        message = user_message or ""

        # Rule 1: pending proposal + confirm keywords.
        if session.pending_proposal_id and _contains_any(
            message, _CONFIRM_KEYWORDS
        ):
            return InputRoute(
                input_type="proposal_confirmation",
                confidence=0.95,
                reason=(
                    "Session has a pending proposal and the message "
                    "contains confirmation keywords."
                ),
                should_call_llm=False,
            )

        # Rule 2: pending proposal + cancel keywords.
        if session.pending_proposal_id and _contains_any(
            message, _CANCEL_KEYWORDS
        ):
            return InputRoute(
                input_type="proposal_cancel",
                confidence=0.95,
                reason=(
                    "Session has a pending proposal and the message "
                    "contains cancellation keywords."
                ),
                should_call_llm=False,
            )

        # Rule 3: batch_review status + selection keywords.
        if (
            session.status is DraftSessionStatus.BATCH_REVIEW
            and _contains_any(message, _SELECTION_KEYWORDS)
        ):
            return InputRoute(
                input_type="study_selection",
                confidence=0.9,
                reason=(
                    "Session is in batch_review and the message "
                    "contains study-selection keywords."
                ),
                should_call_llm=False,
            )

        # Rule 4: clarifying status + pending questions.
        if (
            session.status is DraftSessionStatus.CLARIFYING
            and session.pending_question_ids
        ):
            return InputRoute(
                input_type="clarification_answer",
                confidence=0.9,
                reason=(
                    "Session is clarifying with pending questions; the "
                    "message is treated as an answer by default."
                ),
                should_call_llm=False,
            )

        # Rule 5: numbered list pattern -> batch research request.
        if _NUMBERED_LIST_PATTERN.search(message):
            return InputRoute(
                input_type="batch_research_request",
                confidence=0.9,
                reason=(
                    "Message contains a numbered list of research "
                    "tasks."
                ),
                should_call_llm=True,
            )

        # Rule 6: change keywords + existing draft.
        if (
            session.current_draft_id is not None
            and _contains_any(message, _CHANGE_KEYWORDS)
        ):
            return InputRoute(
                input_type="draft_change_request",
                confidence=0.9,
                reason=(
                    "A draft exists and the message contains "
                    "modification keywords."
                ),
                should_call_llm=True,
            )

        # Rule 7: question keywords.
        if _contains_any(message, _QUESTION_KEYWORDS):
            return InputRoute(
                input_type="question_about_draft",
                confidence=0.9,
                reason="Message contains question keywords.",
                should_call_llm=True,
            )

        # Rule 8: compile keywords.
        if _contains_any(message, _COMPILE_KEYWORDS):
            return InputRoute(
                input_type="compile_request",
                confidence=0.9,
                reason="Message contains compile / case-generation keywords.",
                should_call_llm=True,
            )

        # Rule 9: run keywords.
        if _contains_any(message, _RUN_KEYWORDS):
            return InputRoute(
                input_type="run_request",
                confidence=0.9,
                reason="Message contains run / submit keywords.",
                should_call_llm=True,
            )

        # Rule 10: default.
        return InputRoute(
            input_type="new_research_request",
            confidence=0.5,
            reason=(
                "No strong rule matched; treating the message as a new "
                "research request."
            ),
            should_call_llm=True,
        )


__all__ = ["InputRouter"]
