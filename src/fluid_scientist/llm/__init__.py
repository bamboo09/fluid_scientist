"""LLM client, orchestration, and audit contracts."""

from fluid_scientist.llm.client import LLMClient

from .structured_understanding import (
    EvidenceQuote,
    EvidenceValidationResult,
    ModelNativeUnderstandingService,
    ModelUnderstandingError,
    StructuredEvidenceValidator,
    StructuredUnderstanding,
    UnderstandingContext,
    UnderstandingIssue,
    UnderstoodEntity,
    UnderstoodFact,
    UnderstoodRelation,
)

__all__ = [
    "LLMClient",
    "EvidenceQuote",
    "EvidenceValidationResult",
    "ModelNativeUnderstandingService",
    "ModelUnderstandingError",
    "StructuredEvidenceValidator",
    "StructuredUnderstanding",
    "UnderstandingContext",
    "UnderstandingIssue",
    "UnderstoodEntity",
    "UnderstoodFact",
    "UnderstoodRelation",
]
