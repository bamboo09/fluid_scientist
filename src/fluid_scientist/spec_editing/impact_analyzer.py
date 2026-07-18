"""Impact analysis for the spec-editing module.

The :class:`ImpactAnalyzer` determines the downstream consequences of a
patch *before* it is applied.  It answers three questions:

1. **What derived values need recomputing?**  For example, changing
   ``/physics/velocity`` means the Reynolds number must be recomputed.
2. **What artifacts are invalidated?**  For example, changing
   ``/mesh/resolution`` invalidates any previously generated mesh and
   case files.
3. **Does this require user confirmation?**  High-risk changes (e.g.
   changing the solver or the time mode) require explicit confirmation.

The analyzer uses the ``dependency_tags`` from the
:class:`~fluid_scientist.spec_editing.path_registry.PathRegistry` to
determine cascading effects — no hardcoded field-specific logic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import SimulationSpecPatch
from .path_registry import PathRegistry

__all__ = ["ImpactReport", "ImpactAnalyzer"]

#: Mapping from dependency tags to the derived spec paths that need
#: recomputation when the tag is triggered.  These tags come from the
#: ``dependency_tags`` field in :class:`PathMetadata` and represent
#: fields that *depend on* (are derived from) the changed field.
_DERIVED_PATH_MAP: dict[str, list[str]] = {
    "reynolds_number": ["/physics/reynolds_number"],
    "density": ["/physics/density"],
    "kinematic_viscosity": ["/physics/kinematic_viscosity"],
    "velocity": ["/physics/velocity"],
    "characteristic_length": ["/physics/characteristic_length"],
    "delta_t": ["/numerics/time/delta_t"],
    "courant_number": ["/numerics/time/max_courant"],
    "duration": ["/numerics/time/duration"],
    "flow_regime": ["/numerics/turbulence_model"],
    "turbulence_model": ["/numerics/turbulence_model"],
    "mesh_resolution": ["/mesh/resolution"],
    "domain_bounds": ["/geometry/domain/length", "/geometry/domain/width"],
    "write_interval": ["/numerics/time/write_interval"],
    "statistics_windows": ["/numerics/time/statistics_windows"],
    "stability": ["/numerics/time/delta_t", "/numerics/time/max_courant"],
}

#: Mapping from high-level spec areas to the artifact types they
#: invalidate when changed.
_ARTIFACT_INVALIDATION_MAP: dict[str, str] = {
    "/geometry": "mesh",
    "/mesh": "mesh",
    "/boundaries": "case",
    "/numerics": "case",
    "/physics": "case",
    "/initial_conditions": "case",
}

#: Paths whose modification always requires user confirmation.
_CONFIRMATION_PATHS: set[str] = {
    "/numerics/solver",
    "/numerics/time/mode",
    "/geometry/domain/dimensions",
    "/mesh/mesh_type",
}


class ImpactReport(BaseModel):
    """Report describing the downstream impact of a patch.

    Parameters
    ----------
    affected_paths:
        All spec paths directly or indirectly affected by the patch.
    derived_recompute_needed:
        Spec paths whose values need to be recomputed because a
        dependency changed.
    invalidation_status:
        Mapping from artifact type (``"mesh"``, ``"case"``,
        ``"results"``) to invalidation status string.
    requires_user_confirmation:
        ``True`` if the patch contains high-risk changes that require
        explicit user confirmation before application.
    risk_summary:
        Human-readable summary of the risk profile.
    """

    model_config = ConfigDict(extra="forbid")

    affected_paths: list[str] = Field(default_factory=list)
    derived_recompute_needed: list[str] = Field(default_factory=list)
    invalidation_status: dict[str, str] = Field(default_factory=dict)
    requires_user_confirmation: bool = False
    risk_summary: str = ""


class ImpactAnalyzer:
    """Analyze the downstream impact of a :class:`SimulationSpecPatch`.

    Usage::

        analyzer = ImpactAnalyzer(PathRegistry())
        report = analyzer.analyze(patch, current_spec_dict)
    """

    def __init__(self, path_registry: PathRegistry) -> None:
        self._registry = path_registry

    def analyze(
        self,
        patch: SimulationSpecPatch,
        current_spec: dict[str, Any],
    ) -> ImpactReport:
        """Analyze *patch* against *current_spec* and return an
        :class:`ImpactReport`.

        Parameters
        ----------
        patch:
            The patch to analyze.
        current_spec:
            The current spec as a plain dict (unused for path lookup
            but available for future value-based impact checks).

        Returns
        -------
        An :class:`ImpactReport` describing derived recomputation
        needs, artifact invalidation, and confirmation requirements.
        """
        affected_paths: list[str] = []
        derived_recompute: list[str] = []
        invalidation: dict[str, str] = {}
        requires_confirmation = False
        max_risk = "low"

        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}

        for op in patch.operations:
            path = op.path
            affected_paths.append(path)

            # Check risk level.
            risk = self._registry.get_risk_level(path)
            if risk_order.get(risk, 0) > risk_order.get(max_risk, 0):
                max_risk = risk

            # Check if confirmation is required.
            if path in _CONFIRMATION_PATHS:
                requires_confirmation = True

            # Determine derived recomputation needs from dependency tags.
            # We do a transitive closure: if field A changes, field B
            # depends on A (via tag), and field C depends on B, then
            # both B and C need recomputation.
            meta = self._registry.get_path_metadata(path)
            if meta is not None:
                self._collect_derived(
                    path, meta.dependency_tags, derived_recompute, set()
                )

            # Check by path prefix for artifact invalidation.
            for prefix, art in _ARTIFACT_INVALIDATION_MAP.items():
                if path.startswith(prefix) and art not in invalidation:
                    invalidation[art] = "invalidated"
                    if art in ("mesh", "case"):
                        invalidation["results"] = "invalidated"

        # High-risk patches always require confirmation.
        if max_risk in ("high", "critical"):
            requires_confirmation = True

        risk_summary = self._build_risk_summary(
            max_risk, affected_paths, derived_recompute, invalidation
        )

        return ImpactReport(
            affected_paths=affected_paths,
            derived_recompute_needed=derived_recompute,
            invalidation_status=invalidation,
            requires_user_confirmation=requires_confirmation,
            risk_summary=risk_summary,
        )

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _collect_derived(
        self,
        source_path: str,
        tags: set[str],
        collected: list[str],
        visited: set[str],
    ) -> None:
        """Recursively collect derived paths that need recomputation.

        For each tag in *tags*, look up the derived spec paths in
        :data:`_DERIVED_PATH_MAP`.  For each derived path, look up its
        own dependency tags from the :class:`PathRegistry` and recurse,
        implementing a transitive closure.

        Parameters
        ----------
        source_path:
            The path that changed (excluded from *collected*).
        tags:
            The dependency tags of the changed path.
        collected:
            Accumulator list of paths needing recomputation.
        visited:
            Set of paths already visited (prevents infinite loops).
        """
        for tag in tags:
            derived_paths = _DERIVED_PATH_MAP.get(tag, [])
            for dp in derived_paths:
                if dp == source_path or dp in collected or dp in visited:
                    continue
                collected.append(dp)
                visited.add(dp)

                # Recurse: check this derived path's own dependency tags.
                dp_meta = self._registry.get_path_metadata(dp)
                if dp_meta is not None and dp_meta.dependency_tags:
                    self._collect_derived(
                        source_path, dp_meta.dependency_tags, collected, visited
                    )

    @staticmethod
    def _build_risk_summary(
        max_risk: str,
        affected: list[str],
        derived: list[str],
        invalidation: dict[str, str],
    ) -> str:
        """Build a human-readable risk summary."""
        parts: list[str] = [f"Max risk: {max_risk}"]
        if affected:
            parts.append(f"{len(affected)} path(s) affected")
        if derived:
            parts.append(f"{len(derived)} derived value(s) need recompute")
        if invalidation:
            arts = ", ".join(invalidation.keys())
            parts.append(f"artifacts invalidated: {arts}")
        if not derived and not invalidation:
            parts.append("no cascading effects")
        return "; ".join(parts)
