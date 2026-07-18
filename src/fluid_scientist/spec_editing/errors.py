"""Patch-specific error hierarchy for the spec-editing module.

All errors raised during patch validation, application, and conflict
resolution derive from :class:`PatchError`.  This allows callers to
catch the entire family with a single ``except PatchError`` clause while
still being able to discriminate between specific failure modes.
"""

from __future__ import annotations

__all__ = [
    "PatchError",
    "PatchValidationError",
    "PatchApplicationError",
    "PathNotFoundError",
    "TypeMismatchError",
    "UnitMismatchError",
    "ImmutableFieldError",
    "VersionConflictError",
]


class PatchError(Exception):
    """Base class for all patch-related errors."""


class PatchValidationError(PatchError):
    """Raised when a patch fails validation before application.

    This covers structural problems (malformed JSON pointer, unknown
    operation type), semantic problems (type mismatch, unit mismatch),
    and policy problems (immutable field, version conflict).
    """


class PatchApplicationError(PatchError):
    """Raised when a patch cannot be applied to the target spec.

    Unlike :class:`PatchValidationError`, this is raised at *application*
    time — e.g. when a ``replace`` operation targets a path that exists
    in the schema but is absent from the concrete spec instance.
    """


class PathNotFoundError(PatchError):
    """Raised when a JSON Pointer path does not resolve in the spec.

    This may indicate either that the path is not part of the schema at
    all, or that an intermediate container is missing from the spec
    instance.
    """


class TypeMismatchError(PatchError):
    """Raised when a patch value's type does not match the schema.

    For example, assigning a string to a field whose schema declares
    ``"type": "number"``.
    """


class UnitMismatchError(PatchError):
    """Raised when a quantity's unit does not match the field dimension.

    For example, assigning ``"kg"`` to a field whose
    ``unit_dimension`` is ``"time"``.
    """


class ImmutableFieldError(PatchError):
    """Raised when a patch attempts to modify a non-mutable field.

    Fields such as ``spec_id``, ``schema_version``, ``version``, and
    ``numerics/time/mode`` are marked ``mutable=False`` in the path
    registry and cannot be changed by a patch.
    """


class VersionConflictError(PatchError):
    """Raised when the patch's ``base_version`` does not match the
    current spec version.

    This implements optimistic concurrency: if another patch was applied
    between the time the user saw version *N* and the time their patch
    arrives, the base version will not match and the patch is rejected.
    """
