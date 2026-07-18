"""Spec Editor system prompt and prompt builder.

This module implements the Spec Editor side of the model-driven spec
editing system.  The Spec Editor is the ``primary_reasoner`` that reads
the current :class:`~fluid_scientist.study_spec.models.SimulationStudySpec`
and the user's message, then emits a
:class:`~fluid_scientist.spec_editing.models.SimulationSpecPatch`
describing the minimal change.

The system prompt is intentionally **generic**: it does NOT contain
field-specific rules (no ``"if user says 仿真时间 then..."``).  Instead,
it guides the model to use the generic patch schema and JSON Pointer
paths to express any modification.  This keeps the prompt stable as new
spec fields are added — the schema is the source of truth, not the
prompt.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "SPEC_EDITOR_SYSTEM_PROMPT",
    "build_spec_editor_prompt",
    "build_user_prompt",
]


#: The Spec Editor system prompt.
#:
#: This constant contains the full role description, task list, and
#: prohibitions that govern the ``primary_reasoner``.  It is language-
#: neutral in structure but written in Chinese to match the project's
#: conversational language.
SPEC_EDITOR_SYSTEM_PROMPT = """\
你是 CFD 仿真方案的结构化编辑器，不是模板分类器。

你会收到：
- 当前完整 SimulationStudySpec
- 当前方案版本
- 当前会话事实和冲突
- 本轮用户消息
- 可用能力
- SimulationSpecPatch schema
- CFD/OpenFOAM 专业 Skill

你的任务：
1. 判断用户是在创建、修改、删除、确认、拒绝、撤销还是询问
2. 对修改只输出最小必要 Patch
3. 保留用户没有修改的所有字段
4. 精确引用用户原文作为 source_quote
5. 处理单位、相对量和几何关系
6. 有歧义时输出 clarification，不得猜测
7. 当前能力无法表达时 declare_unknown_capability
8. 不把未知形状映射为已有形状
9. 不输出 Shell、OpenFOAM 文件或执行成功声明
10. 输出必须符合给定 JSON Schema

禁止事项：
- 禁止重建完整方案，只输出最小必要 Patch
- 禁止改变用户没有提到的字段
- 禁止把未知语义映射为已有模板
- 禁止输出 Shell 命令、OpenFOAM 文件内容或执行成功声明
- 禁止在模型失败时伪装成功
- 禁止为单个字段添加专用 if/else 逻辑

输出要求：
- 每个 PatchOperation 必须包含 source_quote，精确引用用户原文
- 对相对修改（如"减半"、"增加20%"）使用表达式（operator/path/factor），不要自行心算
- 对几何关系（如"正下方"、"居中"）使用 set_relation 操作
- 对未知能力使用 declare_unknown_capability 操作
- 对歧义输入输出 clarification，给出具体可选的 alternatives
- 输出必须是符合 SimulationSpecPatch schema 的 JSON
"""


def build_user_prompt(
    user_message: str,
    current_spec: dict,
    spec_version: int,
    confirmed_facts: list,
    conflicts: list,
    skills: list,
) -> str:
    """Build the user-facing portion of the Spec Editor prompt.

    This assembles the sections that describe the *current state* of the
    conversation: the canonical spec, confirmed facts, unresolved
    conflicts, available skills, and the user's message for this turn.

    Parameters
    ----------
    user_message:
        The user's raw message for this turn.
    current_spec:
        The current :class:`SimulationStudySpec` as a dict (e.g. from
        ``model_dump()``).
    spec_version:
        The current spec version number.
    confirmed_facts:
        List of confirmed fact records from the session.
    conflicts:
        List of unresolved conflict records from the session.
    skills:
        List of available CFD/OpenFOAM skill descriptors.

    Returns
    -------
    A formatted string containing all user-facing context sections,
    separated by blank lines.
    """
    sections: list[str] = []

    sections.append(
        "## 当前完整 SimulationStudySpec（版本 "
        f"{spec_version}）\n"
        + json.dumps(current_spec, ensure_ascii=False, indent=2)
    )

    sections.append(
        "## 当前会话已确认事实\n"
        + json.dumps(confirmed_facts, ensure_ascii=False, indent=2)
    )

    sections.append(
        "## 当前未解决冲突\n"
        + json.dumps(conflicts, ensure_ascii=False, indent=2)
    )

    sections.append(
        "## 可用专业 Skills\n"
        + json.dumps(skills, ensure_ascii=False, indent=2)
    )

    sections.append(f"## 用户本轮消息\n{user_message}")

    return "\n\n".join(sections)


def build_spec_editor_prompt(
    context: dict,
    patch_schema: dict,
    current_spec: dict,
    user_message: str,
    confirmed_facts: list,
    unresolved_conflicts: list,
    skills: list,
    openfoam_env: dict,
) -> str:
    """Build the complete Spec Editor prompt with all context sections.

    The sections are assembled in the order specified by the plan
    (Section 9.3):

    1. System role and prohibitions (``SPEC_EDITOR_SYSTEM_PROMPT``)
    2. Current workflow phase (from ``context``)
    3. Current OpenFOAM environment and capabilities
    4. Currently enabled professional Skills
    5. SimulationSpecPatch JSON Schema
    6. Current complete SimulationStudySpec (via :func:`build_user_prompt`)
    7. Confirmed facts
    8. Unresolved conflicts
    9. User message for this turn
    10. Prior Critic feedback (if this is a retry — from ``context``)

    Parameters
    ----------
    context:
        Additional context dict.  May contain ``"workflow_phase"``,
        ``"session_summary"``, ``"recent_conversation"``, and
        ``"prior_critic_feedback"`` (for retries).
    patch_schema:
        The JSON Schema for ``SimulationSpecPatch`` (from
        :meth:`~fluid_scientist.study_spec.schema_export.SchemaExporter.export_patch_schema`).
    current_spec:
        The current :class:`SimulationStudySpec` as a dict.
    user_message:
        The user's raw message for this turn.
    confirmed_facts:
        List of confirmed fact records from the session.
    unresolved_conflicts:
        List of unresolved conflict records from the session.
    skills:
        List of available CFD/OpenFOAM skill descriptors.
    openfoam_env:
        Dict describing the OpenFOAM environment (version, installed
        solvers, function objects, mesh tools, etc.).

    Returns
    -------
    The complete prompt string, ready to be sent to the model.
    """
    sections: list[str] = []

    # 1. System role and prohibitions
    sections.append(SPEC_EDITOR_SYSTEM_PROMPT)

    # 2. Current workflow phase
    workflow_phase = context.get("workflow_phase", "UNDERSTANDING")
    sections.append(f"## 当前工作流阶段\n{workflow_phase}")

    # 3. OpenFOAM environment and capabilities
    sections.append(
        "## 当前 OpenFOAM 环境和能力\n"
        + json.dumps(openfoam_env, ensure_ascii=False, indent=2)
    )

    # 4. Professional Skills
    sections.append(
        "## 当前启用的专业 Skills\n"
        + json.dumps(skills, ensure_ascii=False, indent=2)
    )

    # 5. SimulationSpecPatch JSON Schema
    sections.append(
        "## SimulationSpecPatch JSON Schema\n"
        + json.dumps(patch_schema, ensure_ascii=False, indent=2)
    )

    # 6-9. User-facing context (current spec, facts, conflicts, message)
    spec_version = current_spec.get("version", 1)
    user_prompt = build_user_prompt(
        user_message=user_message,
        current_spec=current_spec,
        spec_version=spec_version,
        confirmed_facts=confirmed_facts,
        conflicts=unresolved_conflicts,
        skills=skills,
    )
    sections.append(user_prompt)

    # Full conversational and reference context.  These sections are kept
    # explicit so Prompt Trace/context-removal tests can prove that each one
    # reached the primary reasoner rather than merely existing in session
    # storage.
    sections.append(
        "## Earlier session summary\n"
        + str(context.get("session_summary", ""))
    )
    sections.append(
        "## Recent original conversation\n"
        + json.dumps(context.get("recent_conversation", []), ensure_ascii=False, indent=2)
    )
    sections.append(
        "## Selected references\n"
        + json.dumps(context.get("references", []), ensure_ascii=False, indent=2)
    )

    # 10. Prior Critic feedback (for retries)
    prior_feedback = context.get("prior_critic_feedback")
    if prior_feedback:
        sections.append(
            "## 上一轮 Critic 反馈（请在本次输出中修正）\n"
            + json.dumps(prior_feedback, ensure_ascii=False, indent=2)
        )

    return "\n\n".join(sections)
