"""Intent candidate models for multi-source extraction and conflict resolution.

Design principle:
- Regex and LLM produce independent candidates, never directly overriding each other.
- A ConflictResolver arbitrates field-by-field based on evidence, source priority, and semantic fidelity.
- Every field value carries a source_span for traceability from user text to final spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class CandidateSource(str, Enum):
    """Where a candidate value came from."""
    REGEX = "regex"
    LLM = "llm"
    USER = "user"
    FORMULA = "formula"
    DEFAULT = "default"


class ConflictType(str, Enum):
    """Types of conflicts between candidates."""
    SEMANTIC_TYPE_CONFLICT = "semantic_type_conflict"
    DUPLICATE_ENTITY = "duplicate_entity"
    VALUE_CONFLICT = "value_conflict"
    SPATIAL_CONFLICT = "spatial_conflict"
    BOUNDARY_CONFLICT = "boundary_conflict"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    MISSING_REQUIRED_FIELD = "missing_required_field"


class ConflictSeverity(str, Enum):
    BLOCKING = "blocking"
    WARNING = "warning"


class ResolutionStrategy(str, Enum):
    AGREEMENT = "agreement"                   # Both sources agree
    REGEX_ONLY = "regex_only"                 # Only regex has a value
    LLM_ONLY = "llm_only"                     # Only LLM has a value
    REGEX_WINS = "regex_wins"                 # Regex wins over LLM
    LLM_WINS = "llm_wins"                     # LLM wins over regex
    MERGED = "merged"                         # Values merged
    NEEDS_CLARIFICATION = "needs_clarification"  # Cannot auto-resolve
    DUPLICATE_REMOVED = "duplicate_removed"   # Duplicate entity removed


@dataclass
class ExtractionCandidate:
    """A single field value extracted by a specific source.

    Attributes:
        field_path: Dotted path like "geometry.objects[0].type" or "domain.length"
        value: The extracted value
        source: Which extractor produced this
        source_span: The substring of user text that supports this value
        confidence: 0.0 to 1.0
        reasoning_summary: Why this value was chosen (for LLM candidates)
    """
    field_path: str
    value: Any
    source: CandidateSource
    source_span: str | None = None
    confidence: float = 1.0
    reasoning_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_path": self.field_path,
            "value": self.value,
            "source": self.source.value,
            "source_span": self.source_span,
            "confidence": self.confidence,
            "reasoning_summary": self.reasoning_summary,
        }


@dataclass
class CandidateConflict:
    """A conflict between regex and LLM candidates for the same field.

    Attributes:
        field_path: The field where conflict occurs
        regex_value: Value from regex (None if regex didn't extract)
        llm_value: Value from LLM (None if LLM didn't extract)
        raw_text: The user text segment relevant to this conflict
        conflict_type: Classification of the conflict
        severity: blocking or warning
        resolution: How the conflict was resolved (None if unresolved)
    """
    field_path: str
    regex_value: Any
    llm_value: Any
    raw_text: str
    conflict_type: ConflictType
    severity: ConflictSeverity = ConflictSeverity.WARNING
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_path": self.field_path,
            "regex_value": self.regex_value,
            "llm_value": self.llm_value,
            "raw_text": self.raw_text,
            "conflict_type": self.conflict_type.value,
            "severity": self.severity.value,
            "resolution": self.resolution,
        }


@dataclass
class ResolvedField:
    """A field after conflict resolution.

    Attributes:
        field_path: The resolved field path
        value: The final resolved value
        raw_value: The original text that supports this value
        source_span: The user text segment
        source: Which source provided the value
        regex_candidate: What regex said (None if no regex candidate)
        llm_candidate: What LLM said (None if no LLM candidate)
        resolution: How the field was resolved
        confidence: Final confidence
        confirmed: Whether user has confirmed this value
    """
    field_path: str
    value: Any
    raw_value: str | None = None
    source_span: str | None = None
    source: CandidateSource = CandidateSource.DEFAULT
    regex_candidate: Any = None
    llm_candidate: Any = None
    resolution: ResolutionStrategy = ResolutionStrategy.AGREEMENT
    confidence: float = 1.0
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_path": self.field_path,
            "value": self.value,
            "raw_value": self.raw_value,
            "source_span": self.source_span,
            "source": self.source.value,
            "regex_candidate": self.regex_candidate,
            "llm_candidate": self.llm_candidate,
            "resolution": self.resolution.value,
            "confidence": self.confidence,
            "confirmed": self.confirmed,
        }


@dataclass
class IntentCandidateSet:
    """Complete set of candidates from all sources.

    Attributes:
        regex_candidates: All candidates from regex pipeline
        llm_candidates: All candidates from LLM
        conflicts: All detected conflicts
        unresolved: Field paths that could not be resolved
        resolved_fields: Fields after resolution
    """
    regex_candidates: list[ExtractionCandidate] = field(default_factory=list)
    llm_candidates: list[ExtractionCandidate] = field(default_factory=list)
    conflicts: list[CandidateConflict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    resolved_fields: list[ResolvedField] = field(default_factory=list)

    def get_resolved(self, field_path: str) -> ResolvedField | None:
        """Get resolved field by path."""
        for rf in self.resolved_fields:
            if rf.field_path == field_path:
                return rf
        return None

    def get_conflicts_by_type(self, conflict_type: ConflictType) -> list[CandidateConflict]:
        """Get all conflicts of a specific type."""
        return [c for c in self.conflicts if c.conflict_type == conflict_type]

    @property
    def has_blocking_conflicts(self) -> bool:
        """Check if any blocking conflicts exist."""
        return any(c.severity == ConflictSeverity.BLOCKING for c in self.conflicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "regex_candidates": [c.to_dict() for c in self.regex_candidates],
            "llm_candidates": [c.to_dict() for c in self.llm_candidates],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "unresolved": list(self.unresolved),
            "resolved_fields": [f.to_dict() for f in self.resolved_fields],
        }
