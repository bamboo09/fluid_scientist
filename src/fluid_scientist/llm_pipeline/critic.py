"""Pass 8: Decomposition Critic -- validate the decomposition quality.

The :class:`DecompositionCritic` is the final validation pass.  It checks
the full decomposition for common quality issues:

* Missing user requirements (facts not covered by any requirement).
* Added unspoken content (requirements that the user did not state).
* Premature OpenFOAM mapping (solver-specific terms in semantic stage).
* Un-split composite requirements.
* Missing multi-region relations.
* Missing observable sampling details.
* New physics misclassified as config.
* Unnecessary clarifications.
* Missed blocking issues.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.llm_pipeline.models import (
    AmbiguityDetection,
    AtomicRequirement,
    CoverageResult,
    CriticReport,
    EntityGraph,
    ExtractedFact,
    NormalizedConcept,
    ObservableDecomposition,
    PhysicsDecomposition,
)

# OpenFOAM-specific terms that should not appear in semantic descriptions.
_OPENFOAM_TERMS: frozenset[str] = frozenset({
    "simplefoam", "pimplefoam", "pisofoam", "rhoCentralFoam",
    "interFoam", "overPimpleDyMFoam", "sonicFoam",
    "blockmesh", "snappyhexmesh", "setfields",
    "controlDict", "fvSchemes", "fvSolution",
    "inletoutlet", "pressureinletoutlet", "waveTransmissive",
    "movingwallvelocity",
})


class DecompositionCritic:
    """Validate decomposition quality against user input.

    The critic runs a series of checks on the decomposition output and
    produces a :class:`CriticReport` with issues categorized by
    severity.  If any ``error``-severity issue is found, ``passed`` is
    set to ``False``.
    """

    def review(
        self,
        user_text: str,
        facts: list[ExtractedFact],
        concepts: list[NormalizedConcept],
        entity_graph: EntityGraph,
        physics: PhysicsDecomposition,
        observables: ObservableDecomposition,
        requirements: list[AtomicRequirement],
        ambiguity: AmbiguityDetection,
        coverage: CoverageResult,
    ) -> CriticReport:
        """Run all critic checks on the decomposition.

        Args:
            user_text: The original user input text.
            facts: Facts from Pass 1.
            concepts: Normalized concepts from Pass 3.
            entity_graph: Entity graph from Pass 4.
            physics: Physics decomposition from Pass 5.
            observables: Observable decomposition from Pass 6.
            requirements: Atomic requirements from Pass 7.
            ambiguity: Ambiguity detection from Pass 2.
            coverage: Coverage result from the coverage analysis.

        Returns:
            A :class:`CriticReport` with issues and pass/fail status.
        """
        issues: list[dict[str, Any]] = []

        self._check_missing_user_requirements(facts, requirements, issues)
        self._check_added_unspoken_content(user_text, facts, requirements, issues)
        self._check_premature_openfoam_mapping(concepts, requirements, issues)
        self._check_unsplit_composite_requirements(requirements, issues)
        self._check_missing_multi_region_relations(entity_graph, physics, issues)
        self._check_missing_observable_sampling(observables, issues)
        self._check_new_physics_misclassified(physics, observables, requirements, issues)
        self._check_unnecessary_clarifications(ambiguity, issues)
        self._check_missed_blocking_issues(ambiguity, coverage, issues)

        # Determine pass/fail.
        has_errors = any(
            issue.get("severity") == "error" for issue in issues
        )

        return CriticReport(
            issues=issues,
            passed=not has_errors,
        )

    # ------------------------------------------------------------------
    # Individual critic checks
    # ------------------------------------------------------------------

    def _check_missing_user_requirements(
        self,
        facts: list[ExtractedFact],
        requirements: list[AtomicRequirement],
        issues: list[dict[str, Any]],
    ) -> None:
        """Check if any user-stated fact is not covered by a requirement."""
        # Build a set of all keywords and descriptions from requirements.
        all_keywords: set[str] = set()
        all_descs: set[str] = set()
        for req in requirements:
            for kw in req.keywords:
                all_keywords.add(kw.lower())
            all_descs.add(req.description.lower())

        for fact in facts:
            fact_covered = False
            fact_value = str(fact.value).lower() if fact.value else ""
            fact_text = fact.raw_text.lower()

            # Check if any requirement mentions this fact.
            for req in requirements:
                req_text = (req.description + " " + " ".join(req.keywords)).lower()
                if fact_value and fact_value in req_text:
                    fact_covered = True
                    break
                if fact_text and fact_text in req_text:
                    fact_covered = True
                    break

            # Research goals are inherently covered (they drive the pipeline).
            if fact.category == "research_goal":
                fact_covered = True

            # Parameters are covered by material models / physics.
            if fact.category == "parameter":
                fact_covered = True

            if not fact_covered:
                issues.append({
                    "issue_type": "missing_user_requirement",
                    "description": (
                        f"Fact '{fact.fact_id}' (category={fact.category}, "
                        f"raw_text='{fact.raw_text[:50]}') is not covered "
                        f"by any atomic requirement."
                    ),
                    "severity": "warning",
                    "affected_requirement": None,
                    "fact_id": fact.fact_id,
                })

    def _check_added_unspoken_content(
        self,
        user_text: str,
        facts: list[ExtractedFact],
        requirements: list[AtomicRequirement],
        issues: list[dict[str, Any]],
    ) -> None:
        """Check if requirements add content the user did not state."""
        user_lower = user_text.lower()
        user_values = {
            str(f.value).lower() for f in facts if f.value is not None
        }
        user_texts = {f.raw_text.lower() for f in facts}

        for req in requirements:
            # Skip physics requirements -- they are derived, not added.
            if req.category in ("physics", "solver"):
                continue
            # Check if the requirement description references something
            # the user did not mention.
            req_words = set(req.description.lower().split())
            # Filter out common words.
            common_words = {
                "boundary", "condition", "geometry", "definition",
                "mesh", "refinement", "for", "the", "a", "an",
                "requirement", "capability",
            }
            significant_words = req_words - common_words
            found_in_user = False
            for word in significant_words:
                if word in user_lower or word in user_values or word in user_texts:
                    found_in_user = True
                    break
            if not found_in_user and significant_words:
                issues.append({
                    "issue_type": "added_unspoken_content",
                    "description": (
                        f"Requirement '{req.requirement_id}' may reference "
                        f"content the user did not state: "
                        f"'{req.description}'"
                    ),
                    "severity": "info",
                    "affected_requirement": req.requirement_id,
                })

    def _check_premature_openfoam_mapping(
        self,
        concepts: list[NormalizedConcept],
        requirements: list[AtomicRequirement],
        issues: list[dict[str, Any]],
    ) -> None:
        """Check for OpenFOAM-specific terms in semantic descriptions."""
        # Check normalized concepts.
        for concept in concepts:
            concept_lower = concept.normalized_concept.lower()
            for term in _OPENFOAM_TERMS:
                if term in concept_lower:
                    issues.append({
                        "issue_type": "premature_openfoam_mapping",
                        "description": (
                            f"Normalized concept '{concept.normalized_concept}' "
                            f"contains OpenFOAM-specific term '{term}'. "
                            f"Semantic stage should not use solver-specific "
                            f"terminology."
                        ),
                        "severity": "warning",
                        "affected_requirement": None,
                    })

        # Check requirement descriptions (except solver category).
        for req in requirements:
            if req.category == "solver":
                continue
            desc_lower = req.description.lower()
            for term in _OPENFOAM_TERMS:
                if term in desc_lower:
                    issues.append({
                        "issue_type": "premature_openfoam_mapping",
                        "description": (
                            f"Requirement '{req.requirement_id}' description "
                            f"contains OpenFOAM-specific term '{term}'."
                        ),
                        "severity": "warning",
                        "affected_requirement": req.requirement_id,
                    })

    def _check_unsplit_composite_requirements(
        self,
        requirements: list[AtomicRequirement],
        issues: list[dict[str, Any]],
    ) -> None:
        """Check for requirements that bundle multiple capabilities."""
        composite_indicators = [
            " and ", " with ", " including ", " plus ",
        ]
        for req in requirements:
            desc_lower = req.description.lower()
            for indicator in composite_indicators:
                if indicator in desc_lower:
                    issues.append({
                        "issue_type": "unsplit_composite_requirement",
                        "description": (
                            f"Requirement '{req.requirement_id}' may be "
                            f"composite (contains '{indicator}'): "
                            f"'{req.description}'.  Consider splitting into "
                            f"separate atomic requirements."
                        ),
                        "severity": "info",
                        "affected_requirement": req.requirement_id,
                    })
                    break

    def _check_missing_multi_region_relations(
        self,
        entity_graph: EntityGraph,
        physics: PhysicsDecomposition,
        issues: list[dict[str, Any]],
    ) -> None:
        """Check for missing relations when multiple entities exist."""
        entity_count = len(entity_graph.entities)
        relation_count = len(entity_graph.relations)
        interface_count = len(entity_graph.interfaces)

        if entity_count > 1 and relation_count == 0:
            issues.append({
                "issue_type": "missing_multi_region_relations",
                "description": (
                    f"Multiple entities ({entity_count}) detected but no "
                    f"spatial relations defined.  The relationship between "
                    f"entities is unknown."
                ),
                "severity": "warning",
                "affected_requirement": None,
            })

        if physics.multi_region_coupling and interface_count == 0:
            issues.append({
                "issue_type": "missing_multi_region_interfaces",
                "description": (
                    "Multi-region coupling is indicated but no interfaces "
                    "are defined between regions."
                ),
                "severity": "warning",
                "affected_requirement": None,
            })

    def _check_missing_observable_sampling(
        self,
        observables: ObservableDecomposition,
        issues: list[dict[str, Any]],
    ) -> None:
        """Check for observables missing sampling details."""
        for obs in observables.observables:
            sampling = obs.get("sampling", {})
            if not isinstance(sampling, dict) or not sampling:
                issues.append({
                    "issue_type": "missing_observable_sampling",
                    "description": (
                        f"Observable '{obs.get('id', '?')}' "
                        f"(type={obs.get('semantic_type', '?')}) has no "
                        f"sampling configuration."
                    ),
                    "severity": "warning",
                    "affected_requirement": None,
                })
                continue

            # Check for essential sampling fields.
            required_sampling_keys = {"type", "target", "frequency"}
            missing_keys = required_sampling_keys - set(sampling.keys())
            if missing_keys:
                issues.append({
                    "issue_type": "incomplete_observable_sampling",
                    "description": (
                        f"Observable '{obs.get('id', '?')}' sampling is "
                        f"missing keys: {sorted(missing_keys)}"
                    ),
                    "severity": "info",
                    "affected_requirement": None,
                })

    def _check_new_physics_misclassified(
        self,
        physics: PhysicsDecomposition,
        observables: ObservableDecomposition,
        requirements: list[AtomicRequirement],
        issues: list[dict[str, Any]],
    ) -> None:
        """Check if new physics is misclassified as a config change."""
        for obs in observables.observables:
            if obs.get("capability_status") == "REQUIRES_NEW_PHYSICS":
                obs_type = obs.get("semantic_type", "unknown")
                # Check if there's a corresponding new_physics requirement.
                has_new_physics_req = any(
                    req.capability_type == "new_physics"
                    and obs_type in req.keywords
                    for req in requirements
                )
                if not has_new_physics_req:
                    issues.append({
                        "issue_type": "new_physics_misclassified_as_config",
                        "description": (
                            f"Observable '{obs.get('id', '?')}' "
                            f"(type={obs_type}) requires new physics but "
                            f"no new_physics requirement was created.  "
                            f"It may have been misclassified as a config change."
                        ),
                        "severity": "error",
                        "affected_requirement": None,
                    })

    def _check_unnecessary_clarifications(
        self,
        ambiguity: AmbiguityDetection,
        issues: list[dict[str, Any]],
    ) -> None:
        """Check for clarifications that ask about things the user stated."""
        # This is a heuristic check: if a blocking unknown references
        # something that appears in the facts, it may be unnecessary.
        for unknown in ambiguity.blocking_unknowns:
            desc = unknown.get("description", "").lower()
            # If the description mentions "missing" but the unknown is
            # actually about a specific choice (not truly missing), flag it.
            if "missing" in desc and unknown.get("recommended_default"):
                issues.append({
                    "issue_type": "unnecessary_clarification",
                    "description": (
                        f"Blocking unknown '{unknown.get('unknown_type', '?')}' "
                        f"has a recommended default but is marked as blocking. "
                        f"Consider making it non-blocking with the default."
                    ),
                    "severity": "info",
                    "affected_requirement": None,
                })

    def _check_missed_blocking_issues(
        self,
        ambiguity: AmbiguityDetection,
        coverage: CoverageResult,
        issues: list[dict[str, Any]],
    ) -> None:
        """Check if blocking issues were missed."""
        # If coverage is not 100% but no blocking unknowns exist,
        # a blocking issue may have been missed.
        if coverage.coverage < 1.0 and not ambiguity.blocking_unknowns:
            issues.append({
                "issue_type": "missed_blocking_issue",
                "description": (
                    f"Coverage is {coverage.coverage:.0%} but no blocking "
                    f"unknowns were detected.  Uncovered facts "
                    f"({coverage.uncovered}) may indicate a missed "
                    f"blocking issue."
                ),
                "severity": "error",
                "affected_requirement": None,
            })

        # If there are conflicts but no blocking unknowns, flag it.
        if ambiguity.conflicts and not ambiguity.blocking_unknowns:
            issues.append({
                "issue_type": "missed_blocking_issue",
                "description": (
                    f"{len(ambiguity.conflicts)} conflict(s) detected but "
                    f"no blocking unknowns.  Conflicts typically indicate "
                    f"missing or contradictory information that should be "
                    f"flagged as blocking."
                ),
                "severity": "warning",
                "affected_requirement": None,
            })


__all__ = ["DecompositionCritic"]
