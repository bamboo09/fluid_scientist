"""Artifact invalidation engine.

When a spec parameter changes, downstream *artifacts* (mesh, case files,
simulation results, post-processed data, reports, measurement plans) may
need to be regenerated at different levels of severity.

The :class:`InvalidationEngine` consumes a list of changed spec paths and
produces a mapping from artifact type to :class:`InvalidationStatus`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from fluid_scientist.compat import StrEnum

from .rules import RuleRegistry

__all__ = [
    "InvalidationStatus",
    "InvalidationRule",
    "InvalidationEngine",
    "ArtifactType",
]

#: The six artifact types the engine reasons about.
ArtifactType = Literal[
    "mesh", "case", "results", "postprocess", "report", "measurement_plan"
]


class InvalidationStatus(StrEnum):
    """Severity of invalidation for an artifact."""

    VALID = "valid"
    NEEDS_RECOMPUTE = "needs_recompute"
    NEEDS_RECOMPILE = "needs_recompile"
    NEEDS_RERUN = "needs_rerun"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


#: Numeric severity for conflict resolution (higher = more severe).
_SEVERITY: dict[InvalidationStatus, int] = {
    InvalidationStatus.VALID: 0,
    InvalidationStatus.NEEDS_REVIEW: 1,
    InvalidationStatus.NEEDS_RECOMPUTE: 2,
    InvalidationStatus.NEEDS_RECOMPILE: 3,
    InvalidationStatus.NEEDS_RERUN: 4,
    InvalidationStatus.BLOCKED: 5,
}


def _severity(status: InvalidationStatus) -> int:
    return _SEVERITY.get(status, 0)


class InvalidationRule(BaseModel):
    """A single invalidation rule.

    Parameters
    ----------
    source_path:
        Spec path prefix that triggers this rule.  A changed path matches
        if it equals or starts with *source_path*.
    artifact_type:
        The artifact affected.
    status:
        The :class:`InvalidationStatus` to apply.
    reason:
        Human-readable explanation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: str
    artifact_type: ArtifactType
    status: InvalidationStatus
    reason: str


#: Canonical invalidation rules derived from the CFD pipeline.
_DEFAULT_INVALIDATION_RULES: list[InvalidationRule] = [
    # --- Geometry changes ---
    InvalidationRule(
        source_path="/geometry",
        artifact_type="mesh",
        status=InvalidationStatus.NEEDS_RECOMPILE,
        reason="Geometry changed; mesh must be regenerated.",
    ),
    InvalidationRule(
        source_path="/geometry",
        artifact_type="case",
        status=InvalidationStatus.NEEDS_RECOMPILE,
        reason="Geometry changed; case files must be recompiled.",
    ),
    InvalidationRule(
        source_path="/geometry",
        artifact_type="results",
        status=InvalidationStatus.NEEDS_RERUN,
        reason="Geometry changed; simulation must be re-run.",
    ),
    # --- Numerics / time changes ---
    InvalidationRule(
        source_path="/numerics/time",
        artifact_type="case",
        status=InvalidationStatus.NEEDS_RECOMPILE,
        reason="Numerics changed; case files must be recompiled.",
    ),
    InvalidationRule(
        source_path="/numerics/time",
        artifact_type="results",
        status=InvalidationStatus.NEEDS_RERUN,
        reason="Numerics changed; simulation must be re-run.",
    ),
    # --- Material changes ---
    InvalidationRule(
        source_path="/physics/material",
        artifact_type="case",
        status=InvalidationStatus.NEEDS_RECOMPILE,
        reason="Material changed; case files must be recompiled.",
    ),
    InvalidationRule(
        source_path="/physics/material",
        artifact_type="results",
        status=InvalidationStatus.NEEDS_RERUN,
        reason="Material changed; simulation must be re-run.",
    ),
    # --- Boundary-condition changes ---
    InvalidationRule(
        source_path="/boundaries",
        artifact_type="case",
        status=InvalidationStatus.NEEDS_RECOMPILE,
        reason="Boundary conditions changed; case files must be recompiled.",
    ),
    InvalidationRule(
        source_path="/boundaries",
        artifact_type="results",
        status=InvalidationStatus.NEEDS_RERUN,
        reason="Boundary conditions changed; simulation must be re-run.",
    ),
    # --- Observation changes ---
    InvalidationRule(
        source_path="/observations",
        artifact_type="measurement_plan",
        status=InvalidationStatus.NEEDS_RECOMPUTE,
        reason="Observation target changed; measurement plan must be updated.",
    ),
    InvalidationRule(
        source_path="/observations",
        artifact_type="postprocess",
        status=InvalidationStatus.NEEDS_RECOMPUTE,
        reason="Observation target changed; post-processing must be recomputed.",
    ),
    # --- Report-only changes ---
    InvalidationRule(
        source_path="/study/title",
        artifact_type="report",
        status=InvalidationStatus.NEEDS_RECOMPUTE,
        reason="Report title changed; report must be regenerated.",
    ),
]


class InvalidationEngine:
    """Determine artifact invalidation from a set of changed spec paths.

    Parameters
    ----------
    rule_registry:
        The dependency :class:`RuleRegistry`, used to expand changed
        paths to their transitive dependents so that cascading effects
        are captured.
    """

    def __init__(self, rule_registry: RuleRegistry) -> None:
        self._registry = rule_registry
        self._rules: list[InvalidationRule] = list(
            _DEFAULT_INVALIDATION_RULES
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_rule(self, rule: InvalidationRule) -> None:
        """Register an additional invalidation rule at runtime."""
        self._rules.append(rule)

    def analyze(
        self,
        changes: list[str],
        current_artifacts: dict[str, Any],
    ) -> dict[str, InvalidationStatus]:
        """Return the invalidation status for every artifact.

        Parameters
        ----------
        changes:
            List of changed spec paths.
        current_artifacts:
            Mapping from artifact type to its current state.  Truthy /
            ``"valid"`` values mean the artifact exists and is valid.
            This is used for the *"if fields already saved"* condition
            on post-processing invalidation.

        Returns
        -------
        A dict mapping artifact type to :class:`InvalidationStatus`.
        Artifacts not affected by any change retain their current status
        (or :attr:`InvalidationStatus.VALID` if not present in
        *current_artifacts*).
        """
        result: dict[str, InvalidationStatus] = {}

        # Seed with current artifact statuses.
        for art_type, raw_state in current_artifacts.items():
            if isinstance(raw_state, InvalidationStatus):
                result[art_type] = raw_state
            elif isinstance(raw_state, str):
                try:
                    result[art_type] = InvalidationStatus(raw_state)
                except ValueError:
                    result[art_type] = (
                        InvalidationStatus.VALID if raw_state else None  # type: ignore[assignment]
                    )
            elif isinstance(raw_state, bool):
                result[art_type] = (
                    InvalidationStatus.VALID if raw_state else None  # type: ignore[assignment]
                )
            else:
                result[art_type] = InvalidationStatus.VALID

        # Expand changed paths to include transitive dependents so that
        # cascading effects are captured (e.g. material -> viscosity -> Re).
        expanded = self._expand_changes(changes)

        fields_saved = self._has_fields(current_artifacts)

        for changed in expanded:
            for rule in self._rules:
                if not self._path_matches(changed, rule.source_path):
                    continue

                # Special condition: postprocess only invalidated when
                # result fields already exist.
                if rule.artifact_type == "postprocess" and not fields_saved:
                    continue

                self._apply(result, rule.artifact_type, rule.status)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _expand_changes(self, changes: list[str]) -> list[str]:
        """Expand *changes* with their transitive dependents.

        Uses the rule registry to walk the dependency chain.
        """
        result: list[str] = list(changes)
        seen: set[str] = set(changes)
        queue: list[str] = list(changes)
        while queue:
            current = queue.pop(0)
            for rule in self._registry.get_rules_for_source(current):
                target = rule.target_path
                if target not in seen:
                    seen.add(target)
                    result.append(target)
                    queue.append(target)
        return result

    @staticmethod
    def _path_matches(changed: str, prefix: str) -> bool:
        """Return True if *changed* equals or starts with *prefix*.

        A path-segment boundary is enforced so that ``/physics/material``
        does not match ``/physics/material``.``foo`` unless the changed
        path genuinely starts with the prefix followed by ``/``.
        """
        if changed == prefix:
            return True
        if changed.startswith(prefix + "/"):
            return True
        return False

    @staticmethod
    def _has_fields(current_artifacts: dict[str, Any]) -> bool:
        """Return True if result fields already exist (are saved)."""
        raw = current_artifacts.get("results")
        if raw is None:
            return False
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            try:
                return InvalidationStatus(raw) != InvalidationStatus.VALID or raw in (
                    "valid", "saved", "exists",
                )
            except ValueError:
                return bool(raw)
        return bool(raw)

    @staticmethod
    def _apply(
        result: dict[str, InvalidationStatus],
        artifact_type: str,
        new_status: InvalidationStatus,
    ) -> None:
        """Apply *new_status* to *artifact_type*, keeping the most severe."""
        existing = result.get(artifact_type)
        if existing is None:
            result[artifact_type] = new_status
            return
        if _severity(new_status) > _severity(existing):
            result[artifact_type] = new_status
