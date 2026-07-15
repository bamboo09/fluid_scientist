"""Requirement Coverage -- map facts to requirements and compute coverage.

The :class:`RequirementCoverage` maps each extracted fact to one of four
destinations:

1. **CaseIR path** -- the fact is directly mappable to a Case IR field
   (entity, boundary, physics, etc.) via an atomic requirement.
2. **Unresolved requirement** -- the fact is recognized but the
   corresponding capability is missing or unresolved.
3. **Clarification question** -- the fact is ambiguous and requires user
   clarification before it can be mapped.
4. **Explicit rejection** -- the fact is out of scope or not applicable
   to the current simulation.

The coverage ratio is the fraction of facts that fall into category 1
(CaseIR path).  The threshold is 1.0 -- all user-stated facts must be
mapped.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.llm_pipeline.models import (
    AmbiguityDetection,
    AtomicRequirement,
    CoverageResult,
    ExtractedFact,
)


class RequirementCoverage:
    """Map facts to requirements and compute coverage.

    The coverage analyzer checks each fact against the atomic
    requirements and the ambiguity detection to determine whether the
    fact has been properly mapped.

    Coverage threshold: 1.0 (100% of user-stated facts must be mapped
    to a CaseIR path).
    """

    COVERAGE_THRESHOLD: float = 1.0

    def compute(
        self,
        facts: list[ExtractedFact],
        requirements: list[AtomicRequirement],
        ambiguity: AmbiguityDetection,
    ) -> CoverageResult:
        """Compute coverage of facts by requirements.

        Args:
            facts: The list of facts extracted in Pass 1.
            requirements: The atomic requirements from Pass 7.
            ambiguity: The ambiguity detection from Pass 2.

        Returns:
            A :class:`CoverageResult` with per-fact mappings and the
            overall coverage ratio.
        """
        fact_mappings: list[dict[str, Any]] = []
        uncovered: list[str] = []
        mapped_count = 0

        # Build ambiguity lookup for facts that need clarification.
        ambiguous_fact_ids: set[str] = self._collect_ambiguous_fact_ids(ambiguity)

        for fact in facts:
            mapping = self._map_fact(fact, requirements, ambiguous_fact_ids)
            fact_mappings.append(mapping)

            if mapping["destination"] == "case_ir_path":
                mapped_count += 1
            else:
                uncovered.append(fact.fact_id)

        total = len(facts)
        coverage = mapped_count / total if total > 0 else 1.0

        return CoverageResult(
            coverage=round(coverage, 4),
            facts=fact_mappings,
            uncovered=uncovered,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _map_fact(
        self,
        fact: ExtractedFact,
        requirements: list[AtomicRequirement],
        ambiguous_fact_ids: set[str],
    ) -> dict[str, Any]:
        """Map a single fact to its destination."""
        # If the fact is ambiguous, it needs clarification.
        if fact.fact_id in ambiguous_fact_ids:
            return {
                "fact_id": fact.fact_id,
                "category": fact.category,
                "raw_text": fact.raw_text,
                "destination": "clarification_question",
                "requirement_id": None,
                "case_ir_path": None,
                "reason": "Fact is ambiguous and requires user clarification.",
            }

        # Research goals are always mapped (they drive the pipeline).
        if fact.category == "research_goal":
            return {
                "fact_id": fact.fact_id,
                "category": fact.category,
                "raw_text": fact.raw_text,
                "destination": "case_ir_path",
                "requirement_id": None,
                "case_ir_path": "study.research_objective",
                "reason": "Research goal drives the entire pipeline.",
            }

        # Parameters are mapped to material models / physics.
        if fact.category == "parameter":
            case_ir_path = self._parameter_to_case_ir_path(fact)
            return {
                "fact_id": fact.fact_id,
                "category": fact.category,
                "raw_text": fact.raw_text,
                "destination": "case_ir_path",
                "requirement_id": None,
                "case_ir_path": case_ir_path,
                "reason": "Parameter mapped to Case IR parameter.",
            }

        # For other categories, find a matching requirement.
        matching_req = self._find_matching_requirement(fact, requirements)

        if matching_req is not None:
            # Check if the requirement is unresolved.
            if matching_req.capability_type == "new_physics":
                return {
                    "fact_id": fact.fact_id,
                    "category": fact.category,
                    "raw_text": fact.raw_text,
                    "destination": "unresolved_requirement",
                    "requirement_id": matching_req.requirement_id,
                    "case_ir_path": None,
                    "reason": (
                        "Requires new physics -- capability not yet "
                        "available."
                    ),
                }
            case_ir_path = self._category_to_case_ir_path(
                fact.category, matching_req
            )
            return {
                "fact_id": fact.fact_id,
                "category": fact.category,
                "raw_text": fact.raw_text,
                "destination": "case_ir_path",
                "requirement_id": matching_req.requirement_id,
                "case_ir_path": case_ir_path,
                "reason": "Fact mapped to atomic requirement.",
            }

        # If no matching requirement found, check if it should be rejected.
        if self._is_out_of_scope(fact):
            return {
                "fact_id": fact.fact_id,
                "category": fact.category,
                "raw_text": fact.raw_text,
                "destination": "explicit_rejection",
                "requirement_id": None,
                "case_ir_path": None,
                "reason": "Fact is out of scope for the current simulation.",
            }

        # Unmapped fact.
        return {
            "fact_id": fact.fact_id,
            "category": fact.category,
            "raw_text": fact.raw_text,
            "destination": "unresolved_requirement",
            "requirement_id": None,
            "case_ir_path": None,
            "reason": "No matching atomic requirement found.",
        }

    def _find_matching_requirement(
        self,
        fact: ExtractedFact,
        requirements: list[AtomicRequirement],
    ) -> AtomicRequirement | None:
        """Find a requirement that covers the given fact."""
        fact_value = str(fact.value).lower() if fact.value else ""
        fact_text = fact.raw_text.lower()

        # Category mapping from fact categories to requirement categories.
        category_map: dict[str, list[str]] = {
            "entity": ["geometry"],
            "boundary": ["boundary"],
            "initial_condition": ["initial_condition"],
            "observable": ["observable"],
            "constraint": ["physics", "boundary", "initial_condition"],
            "material": ["physics"],
            "time_sequence": ["physics"],
        }

        target_categories = category_map.get(fact.category, [])

        for req in requirements:
            if req.category not in target_categories:
                continue
            # Check if any keyword matches.
            for kw in req.keywords:
                kw_lower = kw.lower()
                if kw_lower == fact_value:
                    return req
                if kw_lower in fact_text or fact_text in kw_lower:
                    return req
            # Check description.
            desc_lower = req.description.lower()
            if fact_value and fact_value in desc_lower:
                return req

        return None

    def _parameter_to_case_ir_path(self, fact: ExtractedFact) -> str:
        """Map a parameter fact to a Case IR path."""
        raw_lower = fact.raw_text.lower()
        if "re" in raw_lower and "=" in raw_lower:
            return "physics.reynolds_number"
        if "m/s" in (fact.unit or "").lower():
            return "boundary_intents.inlet.velocity"
        if "diameter" in raw_lower or raw_lower.strip().startswith("d"):
            return "entities.geometry.diameter"
        if "nu" in raw_lower or "ν" in raw_lower:
            return "materials.fluid.kinematic_viscosity"
        if "rho" in raw_lower or "密度" in raw_lower:
            return "materials.fluid.density"
        if "temperature" in raw_lower or "温度" in raw_lower:
            return "physics.temperature"
        return "parameters.unknown"

    def _category_to_case_ir_path(
        self, category: str, req: AtomicRequirement
    ) -> str:
        """Map a fact category + requirement to a Case IR path."""
        path_map: dict[str, str] = {
            "geometry": "entities",
            "boundary": "boundary_intents",
            "initial_condition": "initial_conditions",
            "observable": "observables",
            "physics": "physics",
            "solver": "numerical_intent.solver",
            "mesh": "mesh_intent",
        }
        base = path_map.get(req.category, "unknown")
        # Append the first keyword as a sub-path if available.
        if req.keywords:
            return f"{base}.{req.keywords[0]}"
        return base

    def _is_out_of_scope(self, fact: ExtractedFact) -> bool:
        """Check if a fact is out of scope."""
        # Time sequence facts without clear mapping are not out of scope.
        if fact.category == "time_sequence":
            return False
        # Facts with empty raw_text are effectively out of scope.
        return not fact.raw_text.strip()

    def _collect_ambiguous_fact_ids(
        self, ambiguity: AmbiguityDetection
    ) -> set[str]:
        """Collect fact IDs that are involved in ambiguities."""
        ids: set[str] = set()
        for amb in ambiguity.ambiguities:
            for fid in amb.get("fact_ids", []):
                ids.add(fid)
        for conflict in ambiguity.conflicts:
            for fid in conflict.get("fact_ids", []):
                ids.add(fid)
        return ids


__all__ = ["RequirementCoverage"]
