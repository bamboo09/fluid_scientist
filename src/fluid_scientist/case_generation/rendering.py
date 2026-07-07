"""Deliberately tiny, non-executable renderer for generated case scalars."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from fluid_scientist.case_generation.models import GeneratedCaseDraft, GeneratedCaseParameter


class GeneratedCaseRejected(ValueError):
    """A generated case failed a trust-boundary validation.

    Messages identify the violated rule, never model-authored file content.
    """


@dataclass(frozen=True, slots=True)
class RenderedGeneratedCase:
    files: tuple[tuple[str, str], ...]

    @property
    def files_by_path(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.files))


_PLACEHOLDER = re.compile(r"\{\{ ([a-z][a-z0-9]*(?:_[a-z0-9]+)*) \}\}")
_TEMPLATE_MARKERS = ("{{", "}}", "{%", "%}", "{#", "#}")
_SAFE_ENUM = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")


def _serialize(parameter: GeneratedCaseParameter, value: object) -> str:
    if isinstance(value, bool):
        raise GeneratedCaseRejected(f"parameter {parameter.name} has an invalid type")
    if parameter.kind == "integer":
        if type(value) is not int:
            raise GeneratedCaseRejected(f"parameter {parameter.name} has an invalid type")
        number = Decimal(value)
    elif parameter.kind == "float":
        if type(value) not in (int, float):
            raise GeneratedCaseRejected(f"parameter {parameter.name} has an invalid type")
        if isinstance(value, float) and not math.isfinite(value):
            raise GeneratedCaseRejected(f"parameter {parameter.name} must be finite")
        number = Decimal(str(value))
    else:
        if type(value) is not str or not parameter.allowed_values:
            raise GeneratedCaseRejected(f"parameter {parameter.name} has an invalid type")
        if value not in parameter.allowed_values:
            raise GeneratedCaseRejected(f"parameter {parameter.name} is not allow-listed")
        if _SAFE_ENUM.fullmatch(value) is None:
            raise GeneratedCaseRejected(f"parameter {parameter.name} is not a safe token")
        return value

    assert parameter.minimum is not None and parameter.maximum is not None
    if not Decimal(str(parameter.minimum)) <= number <= Decimal(str(parameter.maximum)):
        raise GeneratedCaseRejected(f"parameter {parameter.name} is outside its bounds")
    if parameter.kind == "integer":
        return str(value)
    # repr is locale-independent and is the shortest round-trippable representation
    # for floats. Integers accepted by a float parameter remain decimal integers.
    return repr(value)


def render_generated_case(
    draft: GeneratedCaseDraft,
    values: Mapping[str, object] | None,
) -> RenderedGeneratedCase:
    """Render exact scalar placeholders without invoking a template engine."""

    parameters = {parameter.name: parameter for parameter in draft.parameters}
    supplied = dict(values or {})
    if set(supplied) != set(parameters):
        raise GeneratedCaseRejected("parameter values must exactly match the declared parameters")
    serialized = {
        name: _serialize(parameter, supplied[name]) for name, parameter in parameters.items()
    }

    rendered: list[tuple[str, str]] = []
    for case_file in draft.files:
        names = tuple(_PLACEHOLDER.findall(case_file.content))
        unknown = set(names) - set(parameters)
        if unknown:
            raise GeneratedCaseRejected("case file contains an unknown placeholder")
        content = _PLACEHOLDER.sub(lambda match: serialized[match.group(1)], case_file.content)
        if any(marker in content for marker in _TEMPLATE_MARKERS):
            raise GeneratedCaseRejected("case file contains unsupported template syntax")
        rendered.append((case_file.path, content))
    return RenderedGeneratedCase(files=tuple(rendered))


def render_defaults(draft: GeneratedCaseDraft) -> RenderedGeneratedCase:
    """Render a draft using its immutable, schema-validated defaults."""

    return render_generated_case(
        draft, {parameter.name: parameter.default for parameter in draft.parameters}
    )


__all__ = [
    "GeneratedCaseRejected",
    "RenderedGeneratedCase",
    "render_defaults",
    "render_generated_case",
]
