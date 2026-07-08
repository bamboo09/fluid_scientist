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


__all__ = ["load_prompt"]
