"""Critic system prompt, result model, and prompt builder.

This module implements the Critic side of the two-call strategy.  The
Critic independently reviews the candidate patch produced by the Spec
Editor (``primary_reasoner``) and decides whether to accept or reject
it.

The Critic checks for:

* **Omitted modifications** — user intent not captured in the patch.
* **Unrelated field changes** — patch modifies fields the user did not
  mention.
* **Incorrect guesses** — model hallucinated instead of asking for
  clarification.
* **Template substitution** — unknown semantics mapped to an existing
  template.
* **Unit consistency** — units in the patch are correct and consistent.
* **Physical dependency awareness** — downstream effects considered.
* **Risk level** — high-risk changes flagged for confirmation.
* **Clarification need** — ambiguous input should produce a
  clarification, not a guess.

The Critic does NOT regenerate the patch — it only accepts or rejects
with specific corrections.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CRITIC_SYSTEM_PROMPT",
    "CriticResult",
    "build_critic_prompt",
]


#: The Critic system prompt.
CRITIC_SYSTEM_PROMPT = """\
你是 CFD 仿真方案修改的独立审查者（Critic）。

你会收到：
- 候选 SimulationSpecPatch
- 当前完整 SimulationStudySpec
- 用户本轮消息

你的检查项：
1. 是否遗漏修改：用户明确要求的修改是否都体现在 Patch 中
2. 是否改变了无关字段：Patch 是否修改了用户没有提到的字段
3. 是否错误猜测：模型是否在信息不足时猜测而非澄清
4. 是否用模板替代未知语义：未知形状/能力是否被错误映射为已有模板
5. 单位：Patch 中的单位是否与当前 spec 一致和正确
6. 物理依赖：修改是否考虑了物理量之间的依赖关系（如改速度需重算 Re）
7. 风险等级：高风险修改是否需要用户确认
8. 是否需要澄清：有歧义时是否输出了 clarification 而非猜测

审查规则：
- 只检查 Patch 的正确性和完整性，不重新生成 Patch
- 如果发现违规，必须明确列出 violations 和 required_corrections
- 如果没有违规，accepted 设为 true
- 不得因为"可以更好"而拒绝，只在有实质性问题时拒绝
- violations 和 required_corrections 必须具体可执行

输出格式（JSON）：
{
  "accepted": true,
  "violations": [],
  "required_corrections": []
}

当 accepted 为 false 时：
- violations：列出每个违规的描述
- required_corrections：列出需要修正的具体要求
"""


class CriticResult(BaseModel):
    """Result of the Critic review of a candidate patch.

    Parameters
    ----------
    accepted:
        ``True`` if the candidate patch passes all checks.  ``False``
        if there are violations that must be corrected.
    violations:
        List of violation descriptions.  Empty when ``accepted`` is
        ``True``.
    required_corrections:
        List of specific, actionable corrections the Spec Editor must
        make.  Empty when ``accepted`` is ``True``.
    """

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    violations: list[str] = Field(default_factory=list)
    required_corrections: list[str] = Field(default_factory=list)


def build_critic_prompt(
    candidate_patch: dict,
    current_spec: dict,
    user_message: str,
) -> str:
    """Build the Critic review prompt.

    Assembles the system prompt with the candidate patch, the current
    spec, and the user's message so the Critic can independently review
    whether the patch correctly and completely captures the user's
    intent.

    Parameters
    ----------
    candidate_patch:
        The candidate ``SimulationSpecPatch`` as a dict, produced by
        the Spec Editor (primary_reasoner).
    current_spec:
        The current :class:`SimulationStudySpec` as a dict.
    user_message:
        The user's raw message for this turn.

    Returns
    -------
    The complete Critic prompt string.
    """
    sections: list[str] = []

    sections.append(CRITIC_SYSTEM_PROMPT)

    sections.append(
        "## 候选 SimulationSpecPatch\n"
        + json.dumps(candidate_patch, ensure_ascii=False, indent=2)
    )

    sections.append(
        "## 当前完整 SimulationStudySpec\n"
        + json.dumps(current_spec, ensure_ascii=False, indent=2)
    )

    sections.append(f"## 用户本轮消息\n{user_message}")

    return "\n\n".join(sections)
