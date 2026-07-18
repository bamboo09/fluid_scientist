"""Semantic spec-editing package: a generic SimulationSpecPatch engine.

This package implements a **schema-grounded patch engine** that lets the
model act as a *semantic editor* rather than a keyword classifier.
Instead of parsing user text with regexes, the model emits structured
:class:`PatchOperation` objects targeting JSON Pointer paths in the
:class:`SimulationStudySpec`.  The patch engine validates these
operations against the schema, applies them atomically, and produces
field-level diffs and impact reports.

Key components
--------------
:class:`SimulationSpecPatch`
    The top-level patch model — a list of operations plus provenance.
:class:`PatchEngine`
    The main orchestrator: validate -> analyze -> apply -> diff ->
    record.
:class:`PathRegistry`
    Schema-driven metadata for every spec path (mutable, risk, unit).
:class:`PatchValidator`
    Pre-application validation (version, path, type, unit, mutability).
:class:`PatchExecutor`
    Atomic all-or-nothing patch application.
:class:`DiffBuilder`
    Field-level diff generation between spec versions.
:class:`ImpactAnalyzer`
    Downstream-impact analysis (derived recomputation, invalidation).
:class:`UndoEngine`
    Reverse-patch generation for undo support.
:class:`QuantityResolver`
    Relative-expression resolution (e.g. "halve delta_t").
:class:`RelationResolver`
    Spatial-relation resolution (e.g. "attached_to bottom_wall").
:class:`PatchHistory`
    Append-only ledger of applied patches.

Usage::

    from fluid_scientist.spec_editing import PatchEngine, SimulationSpecPatch

    engine = PatchEngine()
    result = engine.process_patch(patch, current_spec)
    if result.errors:
        ...
    elif result.clarifications:
        ...
    else:
        new_spec = result.new_spec
"""

from __future__ import annotations

from .diff_builder import DiffBuilder, FieldDiff, SpecDiff
from .errors import (
    ImmutableFieldError,
    PatchApplicationError,
    PatchError,
    PathNotFoundError,
    PatchValidationError,
    TypeMismatchError,
    UnitMismatchError,
    VersionConflictError,
)
from .impact_analyzer import ImpactAnalyzer, ImpactReport
from .models import (
    ClarificationAlternative,
    ClarificationRequest,
    PatchOperation,
    SimulationSpecPatch,
)
from .patch_engine import PatchEngine, PatchResult
from .patch_executor import PatchExecutor
from .patch_validator import PatchValidator
from .path_registry import PathMetadata, PathRegistry
from .provenance import PatchHistory, PatchRecord
from .quantity_resolver import QuantityResolver
from .relative_patch import (
    RelativeOperator,
    RelativePatchError,
    RelativePatchExpression,
)
from .relation_resolver import RelationResolver
from .undo import UndoEngine

__all__ = [
    # Models
    "PatchOperation",
    "ClarificationAlternative",
    "ClarificationRequest",
    "SimulationSpecPatch",
    # Errors
    "PatchError",
    "PatchValidationError",
    "PatchApplicationError",
    "PathNotFoundError",
    "TypeMismatchError",
    "UnitMismatchError",
    "ImmutableFieldError",
    "VersionConflictError",
    # Path registry
    "PathMetadata",
    "PathRegistry",
    # Resolvers
    "QuantityResolver",
    "RelationResolver",
    # Relative patch
    "RelativeOperator",
    "RelativePatchError",
    "RelativePatchExpression",
    # Diff
    "FieldDiff",
    "SpecDiff",
    "DiffBuilder",
    # Validator
    "PatchValidator",
    # Executor
    "PatchExecutor",
    # Impact
    "ImpactReport",
    "ImpactAnalyzer",
    # Undo
    "UndoEngine",
    # Provenance
    "PatchRecord",
    "PatchHistory",
    # Engine
    "PatchResult",
    "PatchEngine",
]
