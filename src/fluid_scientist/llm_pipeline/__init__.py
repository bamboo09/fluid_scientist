"""Multi-pass LLM decomposition pipeline for the Fluid Scientist project.

This package implements a multi-pass decomposition pipeline that takes a
user's natural-language research description and decomposes it into a
structured set of atomic requirements, physics configuration,
observables, and a dependency graph.

The pipeline consists of the following passes:

1. :class:`FactExtractor` -- extract explicit facts from user text.
2. :class:`AmbiguityDetectorPass` -- detect conflicts and unknowns.
3. :class:`ScientificNormalizer` -- normalize to canonical concepts.
4. Entity graph building (internal to :class:`LLMPipeline`).
5. :class:`PhysicsDecomposer` -- determine physics configuration.
6. :class:`ObservableDecomposer` -- structure scientific goals.
7. :class:`AtomicRequirementDecomposer` -- break into atoms + edges.
8. :class:`DecompositionCritic` -- validate decomposition quality.

Supporting components:

* :class:`RequirementCoverage` -- map facts to requirements and compute
  coverage.
* :class:`LLMPipeline` -- the top-level orchestrator that runs all
  passes in sequence.

All data models are defined in :mod:`fluid_scientist.llm_pipeline.models`.
"""

from __future__ import annotations

from fluid_scientist.llm_pipeline.ambiguity_detector import AmbiguityDetectorPass
from fluid_scientist.llm_pipeline.atomic_decomposer import (
    AtomicRequirementDecomposer,
)
from fluid_scientist.llm_pipeline.coverage import RequirementCoverage
from fluid_scientist.llm_pipeline.critic import DecompositionCritic
from fluid_scientist.llm_pipeline.fact_extractor import FactExtractor
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
    PipelineResult,
    RequirementDependencyEdge,
)
from fluid_scientist.llm_pipeline.observable_decomposer import (
    ObservableDecomposer,
)
from fluid_scientist.llm_pipeline.physics_decomposer import PhysicsDecomposer
from fluid_scientist.llm_pipeline.pipeline import LLMPipeline
from fluid_scientist.llm_pipeline.scientific_normalizer import (
    ScientificNormalizer,
)

__all__ = [
    # Pipeline classes
    "AmbiguityDetectorPass",
    "AtomicRequirementDecomposer",
    "DecompositionCritic",
    "FactExtractor",
    "LLMPipeline",
    "ObservableDecomposer",
    "PhysicsDecomposer",
    "RequirementCoverage",
    "ScientificNormalizer",
    # Data models
    "AmbiguityDetection",
    "AtomicRequirement",
    "CoverageResult",
    "CriticReport",
    "EntityGraph",
    "ExtractedFact",
    "NormalizedConcept",
    "ObservableDecomposition",
    "PhysicsDecomposition",
    "PipelineResult",
    "RequirementDependencyEdge",
]
