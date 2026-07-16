"""Open-world research intermediate representation (Research IR).

Provides the canonical, forward-compatible semantic models that faithfully
capture user research intent before any capability or compiler decisions are
made.  All models use ``ConfigDict(extra="allow")`` so that new fields added by
later pipeline stages do not break deserialization of earlier snapshots.
"""

from fluid_scientist.research_ir.models import (
    Assumption,
    BoundaryIntent,
    DomainIntent,
    GeometryEntity,
    GeometryRepresentation,
    InitialConditionIntent,
    MaterialIntent,
    Mention,
    MentionInventory,
    ObservableIntent,
    OpenWorldResearchIR,
    ParameterValue,
    PhysicsModelIntent,
    SemanticAmbiguity,
    SourceCoverage,
    SpatialRelation,
    UnresolvedMention,
)

__all__ = [
    "Assumption",
    "BoundaryIntent",
    "DomainIntent",
    "GeometryEntity",
    "GeometryRepresentation",
    "InitialConditionIntent",
    "MaterialIntent",
    "Mention",
    "MentionInventory",
    "ObservableIntent",
    "OpenWorldResearchIR",
    "ParameterValue",
    "PhysicsModelIntent",
    "SemanticAmbiguity",
    "SourceCoverage",
    "SpatialRelation",
    "UnresolvedMention",
]
