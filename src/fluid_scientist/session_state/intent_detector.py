"""High-level user intent detection for the spec editing workflow.

The :class:`IntentDetector` classifies the user's message into one of
six high-level intents:

* :attr:`UserIntent.CREATE_SPEC` -- create an entirely new simulation spec.
* :attr:`UserIntent.MODIFY_EXISTING_SPEC` -- modify the current spec (the
  default).
* :attr:`UserIntent.CONFIRM_PENDING_PATCH` -- confirm the pending patch.
* :attr:`UserIntent.REJECT_PENDING_PATCH` -- reject the pending patch.
* :attr:`UserIntent.UNDO_LAST_PATCH` -- undo the last applied patch.
* :attr:`UserIntent.REQUEST_EXPLANATION` -- ask a question / request an
  explanation.

IMPORTANT
---------
This is **NOT** keyword-based field extraction.  The detector only
classifies the *high-level* intent.  The actual field-level changes
(which paths to modify, what values to set) are handled entirely by the
model through the patch engine.  The detector simply tells the
orchestrator what *kind* of action the user wants, so it can route the
request appropriately.
"""

from __future__ import annotations

from fluid_scientist.compat import StrEnum

from .models import ResearchSessionState

__all__ = ["UserIntent", "IntentDetector"]


class UserIntent(StrEnum):
    """High-level user intents for the spec editing workflow.

    Members
    -------
    CREATE_SPEC:
        The user wants to create an entirely new simulation spec / study,
        separate from the current one.
    MODIFY_EXISTING_SPEC:
        The user wants to modify the current spec.  This is the default
        intent for most short messages.
    CONFIRM_PENDING_PATCH:
        The user confirms the patch that is currently pending
        confirmation.
    REJECT_PENDING_PATCH:
        The user rejects the patch that is currently pending
        confirmation.
    UNDO_LAST_PATCH:
        The user wants to undo (reverse) the last applied patch.
    REQUEST_EXPLANATION:
        The user is asking a question or requesting an explanation
        about the spec, the results, or the workflow.
    """

    CREATE_SPEC = "create_spec"
    MODIFY_EXISTING_SPEC = "modify_existing_spec"
    CONFIRM_PENDING_PATCH = "confirm_pending_patch"
    REJECT_PENDING_PATCH = "reject_pending_patch"
    UNDO_LAST_PATCH = "undo_last_patch"
    REQUEST_EXPLANATION = "request_explanation"


class IntentDetector:
    """Classifies user messages into high-level intents.

    The detection uses simple rule-based heuristics on the user's
    message text and the session state (specifically, whether a
    pending patch exists).  The rules are checked in order of
    specificity:

    1. **Question patterns** (message starts with ``"为什么"``,
       ``"怎么"``, ``"什么是"``) -> :attr:`UserIntent.REQUEST_EXPLANATION`.
    2. **Undo keywords** (``"撤销"``, ``"回退"``, ``"undo"``) ->
       :attr:`UserIntent.UNDO_LAST_PATCH`.
    3. **Create-new-experiment patterns** (``"新建实验"``,
       ``"新方案"``, etc.) -> :attr:`UserIntent.CREATE_SPEC`.
    4. **Pending-patch confirm/reject** (only if a patch is pending):
       confirm keywords (``"确认"``, ``"可以"``, ``"同意"``) ->
       :attr:`UserIntent.CONFIRM_PENDING_PATCH`; reject keywords
       (``"不"``, ``"取消"``, ``"拒绝"``) ->
       :attr:`UserIntent.REJECT_PENDING_PATCH`.
    5. **Default** -> :attr:`UserIntent.MODIFY_EXISTING_SPEC`.
    """

    #: Phrases that indicate the user wants to create a *new* spec /
    #: experiment, not modify the existing one.
    _CREATE_PATTERNS: tuple[str, ...] = (
        "新建另一个实验",
        "另外创建一个方案",
        "复制为新方案",
        "新建实验",
        "新建方案",
        "新建研究",
        "新建一个实验",
        "新建一个方案",
        "创建新实验",
        "创建新方案",
        "创建新研究",
        "另一个实验",
        "新方案",
    )

    #: Phrases that indicate the user wants to undo the last patch.
    _UNDO_PATTERNS: tuple[str, ...] = (
        "撤销",
        "回退",
        "undo",
    )

    #: Phrases that indicate confirmation of a pending patch.
    _CONFIRM_PATTERNS: tuple[str, ...] = (
        "确认",
        "可以",
        "同意",
        "没问题",
        "好的",
        "就这样",
    )

    #: Phrases that indicate rejection of a pending patch.
    _REJECT_PATTERNS: tuple[str, ...] = (
        "不",
        "取消",
        "拒绝",
        "不要",
        "不行",
    )

    #: Question prefixes that indicate the user is asking a question.
    _QUESTION_PREFIXES: tuple[str, ...] = (
        "为什么",
        "怎么",
        "什么是",
    )

    def detect_intent(
        self,
        user_message: str,
        session: ResearchSessionState,
    ) -> UserIntent:
        """Detect the high-level intent of the user's message.

        Parameters
        ----------
        user_message:
            The user's current message.
        session:
            The current session state.  Used to check whether a patch
            is pending (which gates confirm/reject detection).

        Returns
        -------
        UserIntent
            The detected intent.  See the class docstring for the
            rule ordering.
        """
        msg = user_message.strip()

        # 1. Question patterns -- message starts with a question word.
        for prefix in self._QUESTION_PREFIXES:
            if msg.startswith(prefix):
                return UserIntent.REQUEST_EXPLANATION

        # 2. Undo keywords.
        for pattern in self._UNDO_PATTERNS:
            if pattern in msg:
                return UserIntent.UNDO_LAST_PATCH

        # 3. Create-new-experiment patterns.
        for pattern in self._CREATE_PATTERNS:
            if pattern in msg:
                return UserIntent.CREATE_SPEC

        # 4. Pending-patch confirm / reject (only when a patch is
        #    actually pending).
        if session.pending_patch is not None:
            for pattern in self._CONFIRM_PATTERNS:
                if pattern in msg:
                    return UserIntent.CONFIRM_PENDING_PATCH
            for pattern in self._REJECT_PATTERNS:
                if pattern in msg:
                    return UserIntent.REJECT_PENDING_PATCH

        # 5. Default -- modify the existing spec.
        return UserIntent.MODIFY_EXISTING_SPEC
