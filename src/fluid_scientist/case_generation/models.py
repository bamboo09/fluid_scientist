"""Strict, provider-neutral contracts for generated OpenFOAM case drafts."""

from __future__ import annotations

import math
from collections.abc import Iterable
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

LowerSnakeCase = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    ),
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
JsonScalar = str | int | float

_MAX_FILE_BYTES = 1_000_000
_MAX_TOTAL_FILE_BYTES = 8 * 1024 * 1024
_MAX_METADATA_ITEM_BYTES = 2 * 1024
_MAX_METADATA_BYTES = 64 * 1024
_MAX_ENUM_VALUE_BYTES = 120
_MAX_ENUM_BYTES = 4 * 1024
_MAX_NUMERIC_MAGNITUDE = Decimal("1e100")


class GeneratedCaseFile(BaseModel):
    """One UTF-8 text member proposed for a generated case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
    content: str = Field(max_length=_MAX_FILE_BYTES)

    @field_validator("path", "content", mode="before")
    @classmethod
    def require_string_fields(cls, value: object) -> object:
        if type(value) is not str:
            raise ValueError("file path and content must be strings")
        return value

    @field_validator("path")
    @classmethod
    def require_relative_looking_path(cls, value: str) -> str:
        # Canonicalization and traversal/root allow-list checks deliberately belong to
        # the second-stage safety validator. The wire contract only excludes paths
        # that are unambiguously absolute.
        if value.startswith(("/", "\\")) or (len(value) >= 2 and value[1] == ":"):
            raise ValueError("file path must be relative")
        return value

    @field_validator("content")
    @classmethod
    def require_utf8_content(cls, value: str) -> str:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("file content must be valid UTF-8 text") from error
        if len(encoded) > _MAX_FILE_BYTES:
            raise ValueError("file content exceeds 1,000,000 UTF-8 bytes")
        return value


class GeneratedCaseParameter(BaseModel):
    """A bounded scalar parameter that trusted rendering code may substitute."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: LowerSnakeCase
    kind: Literal["float", "integer", "enum"]
    unit: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ] | None = None
    minimum: JsonScalar | None = None
    maximum: JsonScalar | None = None
    default: JsonScalar
    regression_values: tuple[JsonScalar, ...] = Field(min_length=2, max_length=32)
    allowed_values: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("minimum", "maximum", "default", mode="before")
    @classmethod
    def reject_bool_and_nonfinite_scalars(cls, value: JsonScalar | None) -> JsonScalar | None:
        if value is not None and type(value) not in (str, int, float):
            raise ValueError("parameter values must be JSON scalars")
        if isinstance(value, bool):
            raise ValueError("parameter values must not be booleans")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("parameter values must be finite")
        return value

    @field_validator("regression_values", mode="before")
    @classmethod
    def validate_regression_scalars(cls, values: object) -> object:
        if not isinstance(values, (list, tuple)):
            raise ValueError("regression values must be an array")
        for value in values:
            if type(value) not in (str, int, float):
                raise ValueError("regression values must be JSON scalars")
            if isinstance(value, bool):
                raise ValueError("regression values must not be booleans")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("regression values must be finite")
        return values

    @model_validator(mode="after")
    def validate_kind_contract(self) -> GeneratedCaseParameter:
        if self.kind == "enum":
            return self._validate_enum()
        return self._validate_numeric()

    def _validate_enum(self) -> GeneratedCaseParameter:
        if self.unit is not None or self.minimum is not None or self.maximum is not None:
            raise ValueError("enum parameters cannot define units or numeric bounds")
        if not self.allowed_values:
            raise ValueError("enum parameters require allowed_values")
        if len(set(self.allowed_values)) != len(self.allowed_values):
            raise ValueError("allowed_values must be unique")
        if any(not value.strip() for value in self.allowed_values):
            raise ValueError("allowed_values must be non-empty strings")
        if any(len(value.encode("utf-8")) > _MAX_ENUM_VALUE_BYTES for value in self.allowed_values):
            raise ValueError("allowed_values items cannot exceed 120 UTF-8 bytes")
        if sum(len(value.encode("utf-8")) for value in self.allowed_values) > _MAX_ENUM_BYTES:
            raise ValueError("allowed_values cannot exceed 4 KiB in total")
        if not isinstance(self.default, str) or self.default not in self.allowed_values:
            raise ValueError("enum default must be one of allowed_values")
        if any(
            not isinstance(value, str) or value not in self.allowed_values
            for value in self.regression_values
        ):
            raise ValueError("enum regression values must be in allowed_values")
        return self

    def _validate_numeric(self) -> GeneratedCaseParameter:
        if self.allowed_values is not None:
            raise ValueError("numeric parameters cannot define allowed_values")
        if self.minimum is None or self.maximum is None:
            raise ValueError("numeric parameters require minimum and maximum")
        values = (self.minimum, self.maximum, self.default, *self.regression_values)
        if self.kind == "integer":
            if any(type(value) is not int for value in values):
                raise ValueError("integer parameter values must be integers")
        elif any(type(value) not in (int, float) for value in values):
            raise ValueError("float parameter values must be numeric")

        decimal_values = tuple(Decimal(str(value)) for value in values)
        if any(abs(value) > _MAX_NUMERIC_MAGNITUDE for value in decimal_values):
            raise ValueError("numeric parameter exceeds supported magnitude (1e100)")
        minimum, maximum = decimal_values[:2]
        if minimum > maximum:
            raise ValueError("minimum cannot exceed maximum")
        if any(not minimum <= value <= maximum for value in decimal_values[2:]):
            raise ValueError("default and regression values must be within bounds")
        return self


class GeneratedCaseDraft(BaseModel):
    """A bounded text manifest authored by a Case Builder model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ]
    objective: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4_000)
    ]
    solver: Literal["incompressibleFluid"]
    preprocessing: tuple[Literal["blockMesh", "checkMesh"], ...]
    parameters: tuple[GeneratedCaseParameter, ...] = Field(max_length=64)
    files: tuple[GeneratedCaseFile, ...] = Field(min_length=1, max_length=64)
    requested_outputs: tuple[LowerSnakeCase, ...] = Field(min_length=1, max_length=64)
    assumptions: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=64)
    limitations: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=64)

    @field_validator("assumptions", "limitations")
    @classmethod
    def bound_metadata_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(value.encode("utf-8")) > _MAX_METADATA_ITEM_BYTES for value in values):
            raise ValueError("assumption and limitation items cannot exceed 2 KiB")
        return values

    @model_validator(mode="after")
    def validate_manifest(self) -> GeneratedCaseDraft:
        if self.preprocessing != ("blockMesh", "checkMesh"):
            raise ValueError("preprocessing must be ordered as blockMesh then checkMesh")
        self._require_unique((item.path for item in self.files), "file paths")
        self._require_unique((item.name for item in self.parameters), "parameter names")
        self._require_unique(self.requested_outputs, "requested outputs")
        metadata_bytes = sum(
            len(value.encode("utf-8")) for value in (*self.assumptions, *self.limitations)
        )
        if metadata_bytes > _MAX_METADATA_BYTES:
            raise ValueError("assumptions and limitations cannot exceed 64 KiB in total")
        total_bytes = sum(len(item.content.encode("utf-8")) for item in self.files)
        if total_bytes > _MAX_TOTAL_FILE_BYTES:
            raise ValueError("generated case content exceeds 8 MiB")
        return self

    @staticmethod
    def _require_unique(values: Iterable[str], label: str) -> None:
        materialized = tuple(values)
        if len(set(materialized)) != len(materialized):
            raise ValueError(f"{label} must be unique")


class GeneratedCaseDraftView(BaseModel):
    """Credential-free API projection of an immutable generated draft."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: Identifier
    project_id: Identifier
    plan_id: Identifier
    plan_version: int = Field(ge=1, strict=True)
    version: int = Field(ge=1, strict=True)
    provider: Identifier
    model: Identifier
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    draft: GeneratedCaseDraft


__all__ = [
    "GeneratedCaseDraft",
    "GeneratedCaseDraftView",
    "GeneratedCaseFile",
    "GeneratedCaseParameter",
]
