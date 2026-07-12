"""Stable entrypoints for config-generated OpenFOAM functionObjects."""

from __future__ import annotations

from typing import Any


def residuals_function_object_config(
    *,
    name: str = "generatedResiduals",
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Return a residuals functionObject dictionary fragment."""

    return {
        name: {
            "type": "residuals",
            "libs": ["libutilityFunctionObjects.so"],
            "fields": fields or ["U", "p"],
        }
    }


__all__ = ["residuals_function_object_config"]
