"""Prompt template loading and model-driven spec-editing prompts.

This package provides two layers:

1. **File-based prompt loading** (:func:`load_prompt`) — loads ``.txt``
   prompt templates from disk.  This is the legacy mechanism used by
   the draft-session and study-decomposition pipelines.

2. **Model-driven spec-editing prompts** — the Spec Editor and Critic
   system prompts, prompt builders, and the two-call strategy that
   together implement the model-driven spec editing loop described in
   the refactor plan (Sections 9–10).
"""

from __future__ import annotations

from pathlib import Path

from .critic import CRITIC_SYSTEM_PROMPT, CriticResult, build_critic_prompt
from .spec_editor import (
    SPEC_EDITOR_SYSTEM_PROMPT,
    build_spec_editor_prompt,
    build_user_prompt,
)
from .two_call_strategy import TwoCallStrategy

_PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Load a prompt template by name (without the .txt extension).

    Args:
        name: The prompt file name without extension, e.g. "workbench_edit".

    Returns:
        The raw text content of the prompt file.

    Raises:
        FileNotFoundError: If the prompt file does not exist.
    """
    file_path = _PROMPT_DIR / f"{name}.txt"
    if not file_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {file_path}")
    return file_path.read_text(encoding="utf-8")


__all__ = [
    # File-based loading
    "load_prompt",
    # Spec Editor
    "SPEC_EDITOR_SYSTEM_PROMPT",
    "build_spec_editor_prompt",
    "build_user_prompt",
    # Critic
    "CRITIC_SYSTEM_PROMPT",
    "CriticResult",
    "build_critic_prompt",
    # Two-call strategy
    "TwoCallStrategy",
]
