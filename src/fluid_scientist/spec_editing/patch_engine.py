"""Main orchestrator for the spec-editing module.

The :class:`PatchEngine` is the single entry point for applying a
:class:`SimulationSpecPatch` to a :class:`SimulationStudySpec`.  It ties
together all the sub-components:

* :class:`PathRegistry` — schema-driven path metadata.
* :class:`QuantityResolver` — relative-expression resolution.
* :class:`PatchValidator` — pre-application validation.
* :class:`PatchExecutor` — atomic application.
* :class:`DiffBuilder` — field-level diff generation.
* :class:`ImpactAnalyzer` — downstream-impact analysis.
* :class:`UndoEngine` — reverse-patch generation.
* :class:`PatchHistory` — append-only patch ledger.

Full pipeline:

1. **Validate** — check version, paths, mutability, types, units.
2. **Check clarifications** — if the patch has blocking clarifications,
   return them without applying.
3. **Analyze impact** — determine derived recomputation needs and
   artifact invalidation.
4. **Apply** — atomically apply all operations.
5. **Build diff** — produce a :class:`SpecDiff`.
6. **Record** — add a :class:`PatchRecord` to the history.
7. **Return** — a :class:`PatchResult` with the new spec, diff, impact,
   and any errors or clarifications.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import UTC
from fluid_scientist.study_spec.models import SimulationStudySpec

from .diff_builder import DiffBuilder, SpecDiff
from .impact_analyzer import ImpactAnalyzer, ImpactReport
from .models import ClarificationRequest, SimulationSpecPatch
from .patch_executor import PatchExecutor
from .patch_validator import PatchValidator
from .path_registry import PathRegistry
from .provenance import PatchHistory, PatchRecord
from .quantity_resolver import QuantityResolver
from .undo import UndoEngine

__all__ = ["PatchResult", "PatchEngine"]


class PatchResult(BaseModel):
    """The outcome of processing a patch through the :class:`PatchEngine`.

    Parameters
    ----------
    new_spec:
        The new :class:`SimulationStudySpec` after the patch was
        applied, or ``None`` if the patch was not applied (validation
        failure or blocking clarification).
    diff:
        The :class:`SpecDiff` describing what changed, or ``None``.
    impact:
        The :class:`ImpactReport` for the patch, or ``None``.
    errors:
        List of validation error strings (empty if the patch was valid).
    clarifications:
        List of blocking :class:`ClarificationRequest` objects that
        need user input before the patch can be applied.
    """

    model_config = ConfigDict(extra="forbid")

    new_spec: SimulationStudySpec | None = None
    diff: SpecDiff | None = None
    impact: ImpactReport | None = None
    errors: list[str] = Field(default_factory=list)
    clarifications: list[ClarificationRequest] = Field(default_factory=list)


class PatchEngine:
    """Orchestrate the full patch pipeline: validate -> analyze impact
    -> apply -> diff -> record.

    Usage::

        engine = PatchEngine()
        result = engine.process_patch(patch, current_spec)
        if result.errors:
            # Surface errors to the user.
            ...
        elif result.clarifications:
            # Ask user to clarify.
            ...
        else:
            # Patch applied successfully.
            new_spec = result.new_spec
    """

    def __init__(self) -> None:
        self._path_registry = PathRegistry()
        self._quantity_resolver = QuantityResolver()
        self._validator = PatchValidator(
            self._path_registry, self._quantity_resolver
        )
        self._executor = PatchExecutor(
            self._path_registry, self._quantity_resolver, self._validator
        )
        self._diff_builder = DiffBuilder()
        self._impact_analyzer = ImpactAnalyzer(self._path_registry)
        self._undo_engine = UndoEngine()
        self._history = PatchHistory()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_patch(
        self,
        patch: SimulationSpecPatch,
        current_spec: SimulationStudySpec,
    ) -> PatchResult:
        """Process *patch* against *current_spec* through the full
        pipeline.

        Parameters
        ----------
        patch:
            The patch to process.
        current_spec:
            The current :class:`SimulationStudySpec`.

        Returns
        -------
        A :class:`PatchResult`.  If ``result.errors`` is non-empty, the
        patch was not applied.  If ``result.clarifications`` is
        non-empty, the patch needs user clarification before it can be
        applied.  Otherwise, ``result.new_spec`` contains the updated
        spec.
        """
        current_dict = current_spec.model_dump()

        # 1. Validate.
        errors = self._validator.validate(patch, current_dict)
        if errors:
            return PatchResult(errors=errors)

        # 2. Check for blocking clarifications.
        blocking_clarifications = [
            c for c in patch.clarifications if c.blocking
        ]
        if blocking_clarifications:
            return PatchResult(clarifications=blocking_clarifications)

        # 3. Analyze impact.
        impact = self._impact_analyzer.analyze(patch, current_dict)

        # 4. Apply atomically.
        try:
            new_spec, diff = self._executor.apply(patch, current_spec)
        except Exception as exc:
            return PatchResult(errors=[f"Application failed: {exc}"])

        # 5. Record in history.
        record = PatchRecord(
            patch_id=patch.patch_id,
            session_id=patch.session_id,
            base_spec_id=patch.base_spec_id,
            base_version=patch.base_version,
            new_version=new_spec.version,
            patch=patch,
            diff=diff,
            impact=impact,
            applied_at=datetime.now(UTC).isoformat(),
            applied_by="patch_engine",
            status="confirmed",
        )
        self._history.record(record)

        # 6. Return result.
        return PatchResult(
            new_spec=new_spec,
            diff=diff,
            impact=impact,
        )

    # ------------------------------------------------------------------
    # Convenience accessors for sub-components
    # ------------------------------------------------------------------

    @property
    def path_registry(self) -> PathRegistry:
        return self._path_registry

    @property
    def quantity_resolver(self) -> QuantityResolver:
        return self._quantity_resolver

    @property
    def validator(self) -> PatchValidator:
        return self._validator

    @property
    def executor(self) -> PatchExecutor:
        return self._executor

    @property
    def diff_builder(self) -> DiffBuilder:
        return self._diff_builder

    @property
    def impact_analyzer(self) -> ImpactAnalyzer:
        return self._impact_analyzer

    @property
    def undo_engine(self) -> UndoEngine:
        return self._undo_engine

    @property
    def history(self) -> PatchHistory:
        return self._history
