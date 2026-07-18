"""Dependency report generation.

The :class:`ReportBuilder` ties together the dependency graph, the derived
value computer, and the invalidation engine to produce a single
:class:`DependencyReport` that describes every cascading effect of a set
of spec changes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .derived_values import DerivedValueComputer
from .graph import DependencyGraph
from .invalidation import InvalidationEngine, InvalidationStatus

__all__ = ["DependencyReport", "ReportBuilder"]


class DependencyReport(BaseModel):
    """A report describing all cascading effects of spec changes.

    Parameters
    ----------
    changed_paths:
        The spec paths that were directly changed.
    affected_paths:
        All spec paths directly or indirectly affected (transitive
        dependents of the changed paths).
    derived_recompute_needed:
        List of ``(path, formula)`` tuples for derived values that need
        recomputation.
    invalidation_status:
        Mapping from artifact type to invalidation status string.
    summary:
        Human-readable summary of all cascading effects.
    """

    model_config = ConfigDict(extra="forbid")

    changed_paths: list[str] = Field(default_factory=list)
    affected_paths: list[str] = Field(default_factory=list)
    derived_recompute_needed: list[tuple[str, str]] = Field(
        default_factory=list
    )
    invalidation_status: dict[str, str] = Field(default_factory=dict)
    summary: str = ""


class ReportBuilder:
    """Build a :class:`DependencyReport` from spec changes.

    The builder orchestrates the three engines:

    * :class:`DependencyGraph` — to find transitive dependents.
    * :class:`DerivedValueComputer` — to evaluate derived values and
      determine which formulas need recomputing.
    * :class:`InvalidationEngine` — to determine artifact invalidation.
    """

    def build_report(
        self,
        changes: list[str],
        spec_dict: dict[str, Any],
        graph: DependencyGraph,
        computer: DerivedValueComputer,
        invalidation_engine: InvalidationEngine,
    ) -> DependencyReport:
        """Build a :class:`DependencyReport` for *changes*.

        Parameters
        ----------
        changes:
            List of changed spec paths.
        spec_dict:
            Plain-dict representation of the (post-change) simulation spec.
        graph:
            The dependency graph.
        computer:
            The derived-value computer.
        invalidation_engine:
            The invalidation engine.

        Returns
        -------
        A :class:`DependencyReport` summarising all cascading effects.
        """
        # 1. Collect transitive dependents (affected paths).
        affected: list[str] = []
        seen: set[str] = set(changes)
        for change in changes:
            for dep in graph.get_transitive_dependents(change):
                if dep not in seen:
                    seen.add(dep)
                    affected.append(dep)

        # 2. Determine which derived values need recomputation.
        derived_recompute: list[tuple[str, str]] = []
        for path in affected:
            value, formula = computer.compute(path, spec_dict)
            if formula is not None:
                derived_recompute.append((path, formula))

        # 3. Determine artifact invalidation.
        current_artifacts = self._extract_artifacts(spec_dict)
        invalidation_raw = invalidation_engine.analyze(
            changes, current_artifacts
        )
        invalidation_status: dict[str, str] = {
            k: str(v) for k, v in invalidation_raw.items()
        }

        # 4. Build summary.
        summary = self._build_summary(
            changes, affected, derived_recompute, invalidation_status
        )

        return DependencyReport(
            changed_paths=list(changes),
            affected_paths=affected,
            derived_recompute_needed=derived_recompute,
            invalidation_status=invalidation_status,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_artifacts(spec_dict: dict[str, Any]) -> dict[str, Any]:
        """Extract the current-artifacts dict from the spec.

        If the spec carries an ``_artifacts`` key it is used directly;
        otherwise a default dict with all artifacts marked as existing
        is returned so that the *"if fields already saved"* condition
        triggers correctly.
        """
        if "_artifacts" in spec_dict and isinstance(
            spec_dict["_artifacts"], dict
        ):
            return dict(spec_dict["_artifacts"])
        return {
            "mesh": "valid",
            "case": "valid",
            "results": "valid",
            "postprocess": "valid",
            "report": "valid",
            "measurement_plan": "valid",
        }

    @staticmethod
    def _build_summary(
        changed: list[str],
        affected: list[str],
        derived: list[tuple[str, str]],
        invalidation: dict[str, str],
    ) -> str:
        """Build a human-readable summary of cascading effects."""
        parts: list[str] = []

        parts.append(f"Changed {len(changed)} path(s).")

        if affected:
            parts.append(
                f"{len(affected)} path(s) affected by cascading dependencies."
            )
        else:
            parts.append("No cascading dependencies affected.")

        if derived:
            formulas = ", ".join(
                f"{path} ({formula})" for path, formula in derived
            )
            parts.append(f"Derived values needing recompute: {formulas}.")
        else:
            parts.append("No derived values need recomputation.")

        if invalidation:
            invalidated = [
                f"{k}={v}"
                for k, v in invalidation.items()
                if v != str(InvalidationStatus.VALID)
            ]
            if invalidated:
                parts.append(
                    f"Artifacts invalidated: {', '.join(invalidated)}."
                )
            else:
                parts.append("No artifacts invalidated.")
        else:
            parts.append("No artifacts invalidated.")

        return " ".join(parts)
