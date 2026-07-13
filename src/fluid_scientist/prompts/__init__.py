"""Prompt template loading utilities for the Fluid Scientist workflow."""

from __future__ import annotations

from pathlib import Path

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


# Prompts that should automatically have the OpenFOAM knowledge base appended
_OPENFOAM_AUGMENTED_PROMPTS = frozenset({
    "case_plan_prompt",
    "draft_generation_prompt",
    "observable_inference",
    "case_review",
    "case_fix",
    "runtime_diagnosis",
})


def load_prompt_with_knowledge(name: str) -> str:
    """Load a prompt and append the OpenFOAM knowledge base if relevant.

    For prompts that generate OpenFOAM-related plans (case plans, drafts,
    observables, reviews, fixes), this automatically appends the comprehensive
    OpenFOAM v2406 knowledge base to the system prompt. This prevents the LLM
    from generating invalid parameters at the source.

    Args:
        name: The prompt file name without extension.

    Returns:
        The prompt text with OpenFOAM knowledge appended (if applicable).
    """
    base_prompt = load_prompt(name)
    if name in _OPENFOAM_AUGMENTED_PROMPTS:
        from fluid_scientist.prompts.openfoam_knowledge import OPENFOAM_KNOWLEDGE
        return base_prompt + "\n" + OPENFOAM_KNOWLEDGE
    return base_prompt


__all__ = ["load_prompt", "load_prompt_with_knowledge"]
