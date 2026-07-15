"""Pipeline data models for the multi-pass LLM decomposition pipeline.

All models enforce ``extra="forbid"`` via :class:`_PipelineBase` so that
unexpected fields raise a :class:`~pydantic.ValidationError` rather than
being silently accepted.  This catches schema drift between passes early.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _PipelineBase(BaseModel):
    """Base class for all pipeline models.

    Enforces ``extra="forbid"`` so that unexpected fields raise a
    :class:`~pydantic.ValidationError` rather than being silently
    accepted.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 1. ExtractedFact -- a single fact extracted from user text
# ---------------------------------------------------------------------------


class ExtractedFact(_PipelineBase):
    """A single fact that the user explicitly stated.

    Attributes:
        fact_id: Unique identifier (e.g. ``"F1"``).
        category: Semantic category of the fact.
        raw_text: The original text snippet from the user input.
        value: Parsed value (number, string, etc.).
        unit: Physical unit string.
        source_location: Position in the original text (for traceability).
    """

    fact_id: str
    category: Literal[
        "entity",
        "parameter",
        "initial_condition",
        "boundary",
        "time_sequence",
        "research_goal",
        "observable",
        "constraint",
        "material",
    ]
    raw_text: str
    value: Any = None
    unit: str = ""
    source_location: str = ""  # position in original text


# ---------------------------------------------------------------------------
# 2. AmbiguityDetection -- output of Pass 2
# ---------------------------------------------------------------------------


class AmbiguityDetection(_PipelineBase):
    """Ambiguities, conflicts, and unknowns detected from extracted facts.

    Attributes:
        ambiguities: Multiple interpretations of the same text.
        conflicts: Contradictory facts that cannot coexist.
        blocking_unknowns: Missing information that blocks case generation.
        non_blocking_unknowns: Missing information that can be defaulted.
    """

    ambiguities: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    blocking_unknowns: list[dict[str, Any]] = Field(default_factory=list)
    non_blocking_unknowns: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3. NormalizedConcept -- output of Pass 3
# ---------------------------------------------------------------------------


class NormalizedConcept(_PipelineBase):
    """A user description normalized to a scientific concept.

    When the description is ambiguous, multiple candidate concepts are
    preserved so they can be resolved later through clarification.

    Attributes:
        raw_text: The original user description.
        normalized_concept: The canonical scientific concept name.
        candidate_concepts: Alternative interpretations with confidence.
        confidence: Confidence score in ``[0.0, 1.0]``.
        status: Resolution status.
    """

    raw_text: str
    normalized_concept: str = ""
    candidate_concepts: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    status: Literal["CONFIRMED", "AMBIGUOUS", "UNRESOLVED"] = "CONFIRMED"


# ---------------------------------------------------------------------------
# 4. EntityGraph -- output of Pass 4
# ---------------------------------------------------------------------------


class EntityGraph(_PipelineBase):
    """Graph of entities, regions, relations, and interfaces.

    Attributes:
        entities: Geometric objects in the domain.
        regions: Solution domain regions.
        relations: Spatial relationships between entities.
        interfaces: Coupling interfaces between regions.
    """

    entities: list[dict[str, Any]] = Field(default_factory=list)
    regions: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    interfaces: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. PhysicsDecomposition -- output of Pass 5
# ---------------------------------------------------------------------------


class PhysicsDecomposition(_PipelineBase):
    """Decomposed physics configuration derived from facts and concepts.

    Attributes:
        equations: List of governing equations to solve.
        compressibility: Flow compressibility regime.
        time_mode: Steady or transient.
        turbulence: Turbulence modelling approach.
        heat_transfer: Whether heat transfer is active.
        multiphase: Whether multiphase flow is active.
        moving_mesh: Whether mesh motion is active.
        material_models: Material model definitions.
        external_forces: External body forces (gravity, etc.).
        multi_region_coupling: Coupling between regions.
        recommended_solver_module: Recommended OpenFOAM solver module.
    """

    equations: list[str] = Field(default_factory=list)
    compressibility: Literal["incompressible", "compressible"] = "incompressible"
    time_mode: Literal["steady", "transient"] = "transient"
    turbulence: str = "laminar"
    heat_transfer: bool = False
    multiphase: bool = False
    moving_mesh: bool = False
    material_models: list[dict[str, Any]] = Field(default_factory=list)
    external_forces: list[str] = Field(default_factory=list)
    multi_region_coupling: list[dict[str, Any]] = Field(default_factory=list)
    recommended_solver_module: str = "incompressibleFluid"


# ---------------------------------------------------------------------------
# 6. ObservableDecomposition -- output of Pass 6
# ---------------------------------------------------------------------------


class ObservableDecomposition(_PipelineBase):
    """Structured observables derived from scientific goals.

    Each entry in ``observables`` has the shape::

        {
            "id": str,
            "semantic_type": str,
            "target_region": str,
            "required_fields": list[str],
            "sampling": dict,
            "analysis": dict,
            "capability_status": "SUPPORTED" | "EXTENDABLE" | "REQUIRES_NEW_PHYSICS",
        }

    Attributes:
        observables: List of structured observable definitions.
    """

    observables: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 7. AtomicRequirement -- output of Pass 7
# ---------------------------------------------------------------------------


class AtomicRequirement(_PipelineBase):
    """A minimal, individually implementable requirement.

    Attributes:
        requirement_id: Unique identifier.
        category: Requirement category (geometry, boundary, mesh, etc.).
        description: Human-readable description.
        capability_type: Type of capability needed to implement this.
        keywords: Keywords for capability matching.
        mandatory: Whether this requirement is mandatory.
        depends_on: Other requirement IDs this one depends on.
    """

    requirement_id: str
    category: str  # geometry, boundary, mesh, physics, solver, observable, etc.
    description: str
    capability_type: str = ""
    keywords: list[str] = Field(default_factory=list)
    mandatory: bool = True
    depends_on: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 8. RequirementDependencyEdge -- dependency graph edges
# ---------------------------------------------------------------------------


class RequirementDependencyEdge(_PipelineBase):
    """A directed edge in the requirement dependency graph.

    Attributes:
        source: Source requirement ID.
        target: Target requirement ID.
        edge_type: Type of dependency relationship.
    """

    source: str  # requirement_id
    target: str  # requirement_id
    edge_type: Literal[
        "REQUIRES",
        "CONFLICTS_WITH",
        "DERIVED_FROM",
        "MEASURED_BY",
        "IMPLEMENTED_BY",
        "VALIDATED_BY",
    ]


# ---------------------------------------------------------------------------
# 9. CoverageResult -- output of coverage analysis
# ---------------------------------------------------------------------------


class CoverageResult(_PipelineBase):
    """Result of mapping facts to requirements.

    Attributes:
        coverage: Fraction of facts that have been mapped ``[0.0, 1.0]``.
        facts: Per-fact mapping details.
        uncovered: List of fact IDs that could not be mapped.
    """

    coverage: float = 1.0
    facts: list[dict[str, Any]] = Field(default_factory=list)
    uncovered: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 10. CriticReport -- output of Pass 8
# ---------------------------------------------------------------------------


class CriticReport(_PipelineBase):
    """Report from the decomposition critic.

    Each issue in ``issues`` has the shape::

        {
            "issue_type": str,
            "description": str,
            "severity": "info" | "warning" | "error",
            "affected_requirement": str | None,
        }

    Attributes:
        issues: List of issues found by the critic.
        passed: Whether the decomposition passed all critic checks.
    """

    issues: list[dict[str, Any]] = Field(default_factory=list)
    passed: bool = True


# ---------------------------------------------------------------------------
# 11. PipelineResult -- the aggregate result of the full pipeline
# ---------------------------------------------------------------------------


class PipelineResult(_PipelineBase):
    """Aggregate result of running all pipeline passes.

    Attributes:
        facts: Extracted facts from Pass 1.
        ambiguity_detection: Ambiguity detection from Pass 2.
        normalized_concepts: Normalized concepts from Pass 3.
        entity_graph: Entity graph from Pass 4.
        physics_decomposition: Physics decomposition from Pass 5.
        observable_decomposition: Observable decomposition from Pass 6.
        atomic_requirements: Atomic requirements from Pass 7.
        dependency_edges: Dependency edges from Pass 7.
        coverage: Coverage result.
        critic_report: Critic report from Pass 8.
        errors: Errors encountered during pipeline execution.
    """

    facts: list[ExtractedFact] = Field(default_factory=list)
    ambiguity_detection: AmbiguityDetection = Field(
        default_factory=AmbiguityDetection
    )
    normalized_concepts: list[NormalizedConcept] = Field(default_factory=list)
    entity_graph: EntityGraph = Field(default_factory=EntityGraph)
    physics_decomposition: PhysicsDecomposition = Field(
        default_factory=PhysicsDecomposition
    )
    observable_decomposition: ObservableDecomposition = Field(
        default_factory=ObservableDecomposition
    )
    atomic_requirements: list[AtomicRequirement] = Field(default_factory=list)
    dependency_edges: list[RequirementDependencyEdge] = Field(
        default_factory=list
    )
    coverage: CoverageResult = Field(default_factory=CoverageResult)
    critic_report: CriticReport = Field(default_factory=CriticReport)
    errors: list[str] = Field(default_factory=list)


__all__ = [
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
