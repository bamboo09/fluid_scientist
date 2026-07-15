"""Pass 2: Ambiguity Detector -- detect conflicts and unknowns in facts.

The :class:`AmbiguityDetectorPass` is the second pass of the pipeline.
It examines the facts extracted in Pass 1 and identifies:

* **Ambiguities** -- text that can be interpreted in multiple ways.
* **Conflicts** -- contradictory facts that cannot coexist.
* **Blocking unknowns** -- missing information that prevents case
  generation.
* **Non-blocking unknowns** -- missing information that can be safely
  defaulted.

All detection logic is rule-based so it works without an LLM.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.llm_pipeline.models import AmbiguityDetection, ExtractedFact


class AmbiguityDetectorPass:
    """Detect ambiguities, conflicts, and unknowns from extracted facts.

    This pass is purely rule-based.  It inspects the list of
    :class:`ExtractedFact` objects for known conflict patterns and
    missing critical information.
    """

    def detect(self, facts: list[ExtractedFact]) -> AmbiguityDetection:
        """Run all ambiguity and conflict checks on *facts*.

        Args:
            facts: The list of facts extracted in Pass 1.

        Returns:
            An :class:`AmbiguityDetection` with populated lists.
        """
        ambiguities: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        blocking_unknowns: list[dict[str, Any]] = []
        non_blocking_unknowns: list[dict[str, Any]] = []

        # Run each detector.
        self._check_duplicate_parameters(facts, conflicts)
        self._check_steady_vs_spectrum(facts, conflicts)
        self._check_2d_vs_spanwise(facts, conflicts)
        self._check_isothermal_vs_heat_flux(facts, conflicts)
        self._check_periodic_vs_geometry(facts, conflicts)
        self._check_re_u_d_nu_consistency(facts, conflicts)
        self._check_outlet_ambiguity(facts, ambiguities)
        self._check_research_goal_conflicts(facts, conflicts)
        self._check_missing_geometry(facts, blocking_unknowns)
        self._check_missing_boundary(facts, blocking_unknowns)
        self._check_missing_time_mode(facts, non_blocking_unknowns)

        return AmbiguityDetection(
            ambiguities=ambiguities,
            conflicts=conflicts,
            blocking_unknowns=blocking_unknowns,
            non_blocking_unknowns=non_blocking_unknowns,
        )

    # ------------------------------------------------------------------
    # Individual detectors
    # ------------------------------------------------------------------

    def _check_duplicate_parameters(
        self,
        facts: list[ExtractedFact],
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Detect the same parameter appearing with multiple values."""
        param_map: dict[str, list[ExtractedFact]] = {}
        for f in facts:
            if f.category == "parameter":
                key = f.raw_text.strip().lower()
                # Normalize key: extract the parameter name before '='
                if "=" in key:
                    key = key.split("=")[0].strip()
                param_map.setdefault(key, []).append(f)

        for key, entries in param_map.items():
            if len(entries) < 2:
                continue
            values = [e.value for e in entries]
            # Only flag if values actually differ.
            if len(set(str(v) for v in values)) > 1:
                conflicts.append({
                    "conflict_type": "duplicate_parameter_multiple_values",
                    "parameter": key,
                    "values": values,
                    "fact_ids": [e.fact_id for e in entries],
                    "description": (
                        f"Parameter '{key}' appears with multiple "
                        f"values: {values}"
                    ),
                    "severity": "error",
                })

    def _check_steady_vs_spectrum(
        self,
        facts: list[ExtractedFact],
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Detect steady-state + spectrum/vortex_shedding conflict."""
        has_steady = any(
            f.category == "constraint" and f.value == "steady" for f in facts
        )
        has_transient_observable = any(
            f.category == "observable"
            and f.value in ("frequency_spectrum", "vortex_shedding")
            for f in facts
        )
        if has_steady and has_transient_observable:
            obs_facts = [
                f for f in facts
                if f.category == "observable"
                and f.value in ("frequency_spectrum", "vortex_shedding")
            ]
            conflicts.append({
                "conflict_type": "steady_vs_transient_observable",
                "description": (
                    "Steady-state simulation cannot produce frequency "
                    "spectrum or vortex shedding.  These observables "
                    "require a transient simulation."
                ),
                "fact_ids": [f.fact_id for f in obs_facts],
                "severity": "error",
            })

    def _check_2d_vs_spanwise(
        self,
        facts: list[ExtractedFact],
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Detect 2D simulation + spanwise analysis conflict."""
        has_2d = any(
            f.category == "constraint" and f.value == "2D" for f in facts
        )
        has_spanwise = any(
            f.category == "constraint" and f.value == "spanwise" for f in facts
        )
        if has_2d and has_spanwise:
            conflicts.append({
                "conflict_type": "2d_vs_spanwise",
                "description": (
                    "2D simulation has no spanwise direction.  Spanwise "
                    "velocity sampling requires a 3D simulation."
                ),
                "severity": "error",
            })

    def _check_isothermal_vs_heat_flux(
        self,
        facts: list[ExtractedFact],
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Detect isothermal + heat flux conflict."""
        has_isothermal = any(
            f.category == "constraint" and f.value == "isothermal" for f in facts
        )
        has_heat_flux = any(
            f.category == "observable" and f.value == "wall_heat_flux"
            for f in facts
        )
        if has_isothermal and has_heat_flux:
            conflicts.append({
                "conflict_type": "isothermal_vs_heat_flux",
                "description": (
                    "Isothermal simulation has no temperature gradient "
                    "and therefore no heat flux.  Wall heat flux "
                    "measurement requires a thermal simulation."
                ),
                "severity": "error",
            })

    def _check_periodic_vs_geometry(
        self,
        facts: list[ExtractedFact],
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Detect periodic boundary with non-periodic geometry conflict."""
        has_periodic = any(
            f.category == "boundary" and f.value == "periodic" for f in facts
        )
        # Certain geometries are not naturally periodic (sphere, box, etc.)
        non_periodic_entities = {"sphere", "box", "nozzle"}
        has_non_periodic_entity = any(
            f.category == "entity" and f.value in non_periodic_entities
            for f in facts
        )
        if has_periodic and has_non_periodic_entity:
            conflicts.append({
                "conflict_type": "periodic_vs_geometry",
                "description": (
                    "Periodic boundary conditions require a geometry "
                    "that is periodic in at least one direction.  "
                    "The detected entity type does not support "
                    "periodic boundaries."
                ),
                "severity": "warning",
            })

    def _check_re_u_d_nu_consistency(
        self,
        facts: list[ExtractedFact],
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Check Re = U * D / nu consistency when all four are present."""
        re_val: float | None = None
        u_val: float | None = None
        d_val: float | None = None
        nu_val: float | None = None
        re_id = u_id = d_id = nu_id = ""

        for f in facts:
            if f.category != "parameter":
                continue
            raw_lower = f.raw_text.lower()
            if re_val is None and ("re" in raw_lower and "=" in raw_lower):
                re_val = self._to_float(f.value)
                re_id = f.fact_id
            if u_val is None and "m/s" in (f.unit or "").lower():
                u_val = self._to_float(f.value)
                u_id = f.fact_id
            if d_val is None and (
                "diameter" in raw_lower
                or raw_lower.strip().startswith("d")
                or "直径" in raw_lower
            ):
                d_val = self._to_float(f.value)
                # Convert to meters if mm or cm
                if f.unit == "mm":
                    d_val = d_val / 1000.0 if d_val is not None else None
                elif f.unit == "cm":
                    d_val = d_val / 100.0 if d_val is not None else None
                d_id = f.fact_id
            if nu_val is None and ("nu" in raw_lower or "ν" in raw_lower or "黏度" in raw_lower):
                nu_val = self._to_float(f.value)
                nu_id = f.fact_id

        if all(v is not None for v in [re_val, u_val, d_val, nu_val]):
            assert re_val is not None and u_val is not None
            assert d_val is not None and nu_val is not None
            if nu_val != 0:
                computed_re = u_val * d_val / nu_val
                rel_error = abs(computed_re - re_val) / max(abs(re_val), 1.0)
                if rel_error > 0.05:  # 5% tolerance
                    conflicts.append({
                        "conflict_type": "re_u_d_nu_inconsistency",
                        "description": (
                            f"Reynolds number inconsistency: Re={re_val} "
                            f"but U*D/nu = {u_val}*{d_val}/{nu_val} "
                            f"= {computed_re:.2f} (relative error "
                            f"{rel_error:.1%})"
                        ),
                        "fact_ids": [re_id, u_id, d_id, nu_id],
                        "severity": "warning",
                    })

    def _check_outlet_ambiguity(
        self,
        facts: list[ExtractedFact],
        ambiguities: list[dict[str, Any]],
    ) -> None:
        """Detect outlet boundary semantic ambiguity."""
        has_generic_outlet = any(
            f.category == "boundary" and f.value == "outlet" for f in facts
        )
        has_specific_outlet = any(
            f.category == "boundary"
            and f.value in ("advective_outlet", "pressure_outlet")
            for f in facts
        )
        if has_generic_outlet and not has_specific_outlet:
            outlet_facts = [
                f for f in facts
                if f.category == "boundary" and f.value == "outlet"
            ]
            ambiguities.append({
                "ambiguity_type": "outlet_semantic_ambiguity",
                "description": (
                    "Outlet boundary condition is ambiguous: it could be "
                    "a convective (advective) outlet or a pressure outlet."
                ),
                "candidates": ["advective_outlet", "pressure_outlet"],
                "fact_ids": [f.fact_id for f in outlet_facts],
                "suggested_question": (
                    "What type of outlet should be used: convective "
                    "(advective) outlet or pressure outlet?"
                ),
            })

    def _check_research_goal_conflicts(
        self,
        facts: list[ExtractedFact],
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Detect conflicting research goals (e.g. laminar + turbulent)."""
        has_les = any(f.value == "LES" for f in facts if f.category == "constraint")
        has_rans = any(f.value == "RANS" for f in facts if f.category == "constraint")
        if has_les and has_rans:
            conflicts.append({
                "conflict_type": "les_vs_rans",
                "description": (
                    "Both LES and RANS turbulence approaches were "
                    "specified.  Only one turbulence model can be used."
                ),
                "severity": "error",
            })

    def _check_missing_geometry(
        self,
        facts: list[ExtractedFact],
        blocking_unknowns: list[dict[str, Any]],
    ) -> None:
        """Flag missing geometry information as a blocking unknown."""
        has_entity = any(f.category == "entity" for f in facts)
        if not has_entity:
            blocking_unknowns.append({
                "unknown_type": "missing_geometry",
                "description": (
                    "No geometric entity was specified.  The simulation "
                    "domain geometry is required."
                ),
                "suggested_question": (
                    "What is the geometry of the simulation domain "
                    "(e.g. cylinder, pipe, sphere)?"
                ),
                "blocking": True,
            })

    def _check_missing_boundary(
        self,
        facts: list[ExtractedFact],
        blocking_unknowns: list[dict[str, Any]],
    ) -> None:
        """Flag missing boundary conditions as a blocking unknown."""
        has_boundary = any(f.category == "boundary" for f in facts)
        has_inlet = any(
            f.value in ("inlet", "uniform_velocity_inlet")
            for f in facts if f.category == "boundary"
        )
        has_outlet = any(
            f.value in ("outlet", "advective_outlet", "pressure_outlet")
            for f in facts if f.category == "boundary"
        )
        if not has_boundary:
            blocking_unknowns.append({
                "unknown_type": "missing_boundary_conditions",
                "description": (
                    "No boundary conditions were specified."
                ),
                "suggested_question": (
                    "What are the boundary conditions (inlet, outlet, "
                    "walls)?"
                ),
                "blocking": True,
            })
        elif not has_inlet:
            non_blocking_unknowns_moved: list[dict[str, Any]] = []
            non_blocking_unknowns_moved.append({
                "unknown_type": "missing_inlet",
                "description": "No inlet boundary condition specified.",
                "suggested_question": "What is the inlet condition?",
                "blocking": True,
            })
            blocking_unknowns.extend(non_blocking_unknowns_moved)
        elif not has_outlet:
            blocking_unknowns.append({
                "unknown_type": "missing_outlet",
                "description": "No outlet boundary condition specified.",
                "suggested_question": "What is the outlet condition?",
                "blocking": True,
            })

    def _check_missing_time_mode(
        self,
        facts: list[ExtractedFact],
        non_blocking_unknowns: list[dict[str, Any]],
    ) -> None:
        """Flag missing time mode as a non-blocking unknown."""
        has_time = any(
            f.value in ("steady", "transient")
            for f in facts if f.category == "constraint"
        )
        if not has_time:
            non_blocking_unknowns.append({
                "unknown_type": "missing_time_mode",
                "description": (
                    "Steady or transient was not specified.  Defaulting "
                    "to transient."
                ),
                "recommended_default": "transient",
                "blocking": False,
            })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_float(value: Any) -> float | None:
        """Safely convert a value to float."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


__all__ = ["AmbiguityDetectorPass"]
