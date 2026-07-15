"""Pipeline Orchestrator -- run all decomposition passes in sequence.

The :class:`LLMPipeline` is the top-level entry point for the multi-pass
LLM decomposition pipeline.  It runs all passes in order, feeding the
output of each pass as input to the next, and catches errors per-pass
so that a failure in one pass does not prevent the remaining passes
from executing.

Pass sequence:

1. **Fact Extraction** -- extract explicit facts from user text.
2. **Ambiguity Detection** -- detect conflicts and unknowns.
3. **Scientific Normalization** -- normalize to canonical concepts.
4. **Entity Graph Building** -- build entity/region/relation graph.
5. **Physics Decomposition** -- determine physics configuration.
6. **Observable Decomposition** -- structure scientific goals.
7. **Atomic Requirement Decomposition** -- break into atoms + edges.
8. **Coverage Analysis** -- map facts to requirements.
9. **Critic Review** -- validate decomposition quality.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.llm_pipeline.ambiguity_detector import AmbiguityDetectorPass
from fluid_scientist.llm_pipeline.atomic_decomposer import (
    AtomicRequirementDecomposer,
)
from fluid_scientist.llm_pipeline.coverage import RequirementCoverage
from fluid_scientist.llm_pipeline.critic import DecompositionCritic
from fluid_scientist.llm_pipeline.fact_extractor import FactExtractor
from fluid_scientist.llm_pipeline.models import (
    EntityGraph,
    ExtractedFact,
    PipelineResult,
)
from fluid_scientist.llm_pipeline.observable_decomposer import (
    ObservableDecomposer,
)
from fluid_scientist.llm_pipeline.physics_decomposer import PhysicsDecomposer
from fluid_scientist.llm_pipeline.scientific_normalizer import (
    ScientificNormalizer,
)


class LLMPipeline:
    """Multi-pass LLM decomposition pipeline orchestrator.

    The pipeline runs all decomposition passes in sequence.  Each pass
    takes the outputs of previous passes as input.  Errors are caught
    per-pass and recorded in the ``errors`` list of the result, so that
    a failure in one pass does not prevent the remaining passes from
    executing.

    Parameters:
        llm_client: An optional LLM client for LLM-based extraction.
            When ``None``, all passes use rule-based logic.
    """

    def __init__(self, llm_client: Any = None) -> None:
        self._fact_extractor = FactExtractor(llm_client=llm_client)
        self._ambiguity_detector = AmbiguityDetectorPass()
        self._normalizer = ScientificNormalizer()
        self._physics_decomposer = PhysicsDecomposer()
        self._observable_decomposer = ObservableDecomposer()
        self._atomic_decomposer = AtomicRequirementDecomposer()
        self._coverage = RequirementCoverage()
        self._critic = DecompositionCritic()

    def run(self, user_text: str) -> PipelineResult:
        """Run the full decomposition pipeline on *user_text*.

        Args:
            user_text: The user's natural-language research description.

        Returns:
            A :class:`PipelineResult` with all pass outputs and any
            errors encountered.
        """
        result = PipelineResult()
        errors: list[str] = []

        # --- Pass 1: Fact Extraction ---
        facts: list[ExtractedFact] = []
        try:
            facts = self._fact_extractor.extract(user_text)
            result.facts = facts
        except Exception as exc:
            errors.append(f"Pass 1 (Fact Extraction) failed: {exc}")

        # --- Pass 2: Ambiguity Detection ---
        try:
            result.ambiguity_detection = self._ambiguity_detector.detect(facts)
        except Exception as exc:
            errors.append(f"Pass 2 (Ambiguity Detection) failed: {exc}")

        # --- Pass 3: Scientific Normalization ---
        concepts = []
        try:
            concepts = self._normalizer.normalize(facts)
            result.normalized_concepts = concepts
        except Exception as exc:
            errors.append(f"Pass 3 (Scientific Normalization) failed: {exc}")

        # --- Pass 4: Entity Graph Building ---
        try:
            result.entity_graph = self._build_entity_graph(facts)
        except Exception as exc:
            errors.append(f"Pass 4 (Entity Graph Building) failed: {exc}")

        # --- Pass 5: Physics Decomposition ---
        physics = result.physics_decomposition
        try:
            physics = self._physics_decomposer.decompose(facts, concepts)
            result.physics_decomposition = physics
        except Exception as exc:
            errors.append(f"Pass 5 (Physics Decomposition) failed: {exc}")

        # --- Pass 6: Observable Decomposition ---
        observables = result.observable_decomposition
        try:
            observables = self._observable_decomposer.decompose(facts, physics)
            result.observable_decomposition = observables
        except Exception as exc:
            errors.append(f"Pass 6 (Observable Decomposition) failed: {exc}")

        # --- Pass 7: Atomic Requirement Decomposition ---
        try:
            reqs, edges = self._atomic_decomposer.decompose(
                facts=facts,
                concepts=concepts,
                entity_graph=result.entity_graph,
                physics=physics,
                observables=observables,
                ambiguity=result.ambiguity_detection,
            )
            result.atomic_requirements = reqs
            result.dependency_edges = edges
        except Exception as exc:
            errors.append(f"Pass 7 (Atomic Requirement Decomposition) failed: {exc}")

        # --- Coverage Analysis ---
        try:
            result.coverage = self._coverage.compute(
                facts=facts,
                requirements=result.atomic_requirements,
                ambiguity=result.ambiguity_detection,
            )
        except Exception as exc:
            errors.append(f"Coverage Analysis failed: {exc}")

        # --- Pass 8: Critic Review ---
        try:
            result.critic_report = self._critic.review(
                user_text=user_text,
                facts=facts,
                concepts=concepts,
                entity_graph=result.entity_graph,
                physics=physics,
                observables=observables,
                requirements=result.atomic_requirements,
                ambiguity=result.ambiguity_detection,
                coverage=result.coverage,
            )
        except Exception as exc:
            errors.append(f"Pass 8 (Critic Review) failed: {exc}")

        result.errors = errors
        return result

    # ------------------------------------------------------------------
    # Pass 4: Entity Graph Builder
    # ------------------------------------------------------------------

    def _build_entity_graph(self, facts: list[ExtractedFact]) -> EntityGraph:
        """Build an entity graph from extracted facts.

        This is Pass 4 of the pipeline.  It constructs an
        :class:`EntityGraph` from the entity, boundary, and relation
        facts.  The graph captures:

        * **Entities** -- geometric objects (cylinder, pipe, etc.).
        * **Regions** -- solution domain regions (fluid, solid).
        * **Relations** -- spatial relationships between entities.
        * **Interfaces** -- coupling interfaces between regions.
        """
        entities: list[dict[str, Any]] = []
        regions: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        interfaces: list[dict[str, Any]] = []

        # Build entities from entity facts.
        entity_facts = [f for f in facts if f.category == "entity"]
        for i, fact in enumerate(entity_facts):
            entity_kind = str(fact.value) if fact.value else "custom"
            entity: dict[str, Any] = {
                "id": f"entity_{i + 1}",
                "kind": entity_kind,
                "parameters": {},
                "source_fact_id": fact.fact_id,
            }
            # Attach any parameters that reference this entity.
            for pf in facts:
                if pf.category == "parameter":
                    raw_lower = pf.raw_text.lower()
                    if entity_kind in raw_lower or "diameter" in raw_lower:
                        entity["parameters"]["diameter"] = {
                            "value": pf.value,
                            "unit": pf.unit,
                        }
            entities.append(entity)

        # Build a default fluid region.
        regions.append({
            "id": "region_fluid_1",
            "kind": "fluid",
            "material_ref": "",
            "physics_refs": [],
        })

        # Build relations between entities (if multiple).
        if len(entities) > 1:
            for i in range(len(entities) - 1):
                relations.append({
                    "id": f"relation_{i + 1}",
                    "type": "near",
                    "source": entities[i]["id"],
                    "target": entities[i + 1]["id"],
                    "parameters": {},
                })

        # Check for near-wall facts to build near relations.
        for fact in facts:
            if fact.category == "boundary" and fact.value == "no_slip_wall":
                # The entity is near a wall.
                for entity in entities:
                    entity["near_wall"] = True

        # Check for rotation/motion facts.
        text_lower = " ".join(f.raw_text.lower() for f in facts)
        if any(w in text_lower for w in ["rotating", "旋转", "rotation"]):
            for entity in entities:
                entity["motion"] = "rotates_about"

        return EntityGraph(
            entities=entities,
            regions=regions,
            relations=relations,
            interfaces=interfaces,
        )


__all__ = ["LLMPipeline"]
