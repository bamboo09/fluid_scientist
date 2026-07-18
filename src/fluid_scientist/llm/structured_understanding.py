"""Model-native semantic understanding contracts and evidence validation.

The model owns semantic interpretation.  This module deliberately contains no
field-extraction regexes and never mutates a simulation spec.  Deterministic
code is limited to validating the model's structured output and its evidence
quotes before a :class:`SimulationSpecPatch` reaches the patch engine.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fluid_scientist.spec_editing.models import SimulationSpecPatch


class EvidenceQuote(BaseModel):
    """A verbatim evidence span used by one or more semantic claims."""

    model_config = ConfigDict(extra="forbid")

    quote: str
    source: Literal["current_message", "history", "confirmed_fact", "reference"]
    source_id: str = ""


class UnderstoodFact(BaseModel):
    """A model-understood fact with an explicit field-level source chain."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    path: str
    value: Any
    unit: str | None = None
    origin: Literal["USER_EXPLICIT", "FORMULA_DERIVED", "MODEL_RECOMMENDED"]
    evidence: list[EvidenceQuote] = Field(default_factory=list)
    derivation: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_auditable_origin(self) -> "UnderstoodFact":
        if self.origin == "FORMULA_DERIVED" and not self.derivation:
            raise ValueError("FORMULA_DERIVED fact requires derivation")
        if self.origin == "USER_EXPLICIT" and not self.evidence:
            raise ValueError("USER_EXPLICIT fact requires evidence")
        return self


class UnderstoodEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    semantic_type: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceQuote] = Field(default_factory=list)


class UnderstoodRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str
    predicate: str
    object_id: str
    evidence: list[EvidenceQuote] = Field(default_factory=list)


class UnderstandingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    description: str
    affected_paths: list[str] = Field(default_factory=list)
    blocking: bool = False
    alternatives: list[str] = Field(default_factory=list)


class StructuredUnderstanding(BaseModel):
    """The only semantic output accepted from the primary understanding LLM."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    case_family: str = "open_cfd_case"
    dimensionality: Literal["2D", "3D"] = "2D"
    facts: list[UnderstoodFact] = Field(default_factory=list)
    entities: list[UnderstoodEntity] = Field(default_factory=list)
    relations: list[UnderstoodRelation] = Field(default_factory=list)
    ambiguities: list[UnderstandingIssue] = Field(default_factory=list)
    conflicts: list[UnderstandingIssue] = Field(default_factory=list)
    capability_requirements: list[str] = Field(default_factory=list)
    evidence_quotes: list[EvidenceQuote] = Field(default_factory=list)
    proposed_patch: SimulationSpecPatch


class UnderstandingContext(BaseModel):
    """Complete, serializable input envelope for one understanding call."""

    model_config = ConfigDict(extra="forbid")

    user_message: str
    current_spec: dict[str, Any] | None = None
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_facts: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    workflow_skills: list[dict[str, str]] = Field(default_factory=list)
    professional_skills: list[dict[str, str]] = Field(default_factory=list)
    references: list[dict[str, str]] = Field(default_factory=list)
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: StructuredUnderstanding.model_json_schema()
    )

    def prompt_payload(self) -> str:
        """Render the exact auditable JSON payload supplied to the model."""

        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


class EvidenceValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    field_source_chain: list[dict[str, Any]] = Field(default_factory=list)


class StructuredEvidenceValidator:
    """Verify quotes and patch provenance without interpreting semantics."""

    @staticmethod
    def _corpus(context: UnderstandingContext) -> dict[str, str]:
        corpus = {"current_message": context.user_message}
        corpus["history"] = json.dumps(context.conversation_history, ensure_ascii=False)
        corpus["confirmed_fact"] = json.dumps(context.confirmed_facts, ensure_ascii=False)
        corpus["reference"] = json.dumps(context.references, ensure_ascii=False)
        return corpus

    def validate(
        self, understanding: StructuredUnderstanding, context: UnderstandingContext
    ) -> EvidenceValidationResult:
        corpus = self._corpus(context)
        errors: list[str] = []
        chain: list[dict[str, Any]] = []

        for fact in understanding.facts:
            valid_quotes: list[str] = []
            for evidence in fact.evidence:
                if evidence.quote not in corpus[evidence.source]:
                    errors.append(
                        f"{fact.path}: evidence quote not found in {evidence.source}: "
                        f"{evidence.quote!r}"
                    )
                else:
                    valid_quotes.append(evidence.quote)
            if fact.origin == "USER_EXPLICIT" and isinstance(fact.value, (int, float)):
                numbers = [float(item) for item in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", " ".join(valid_quotes))]
                if not any(abs(number - float(fact.value)) <= max(1e-12, abs(float(fact.value)) * 1e-9) for number in numbers):
                    errors.append(f"{fact.path}: explicit numeric value is absent from evidence")
            if fact.origin == "USER_EXPLICIT" and fact.unit:
                compact_quotes = "".join(valid_quotes).lower().replace("秒", "s").replace("米", "m")
                compact_unit = fact.unit.lower().replace(" ", "")
                if compact_unit not in compact_quotes.replace(" ", ""):
                    errors.append(f"{fact.path}: explicit unit {fact.unit!r} is absent from evidence")
            chain.append(
                {
                    "path": fact.path,
                    "origin": fact.origin,
                    "value": fact.value,
                    "unit": fact.unit,
                    "evidence_quotes": valid_quotes,
                    "derivation": fact.derivation,
                    "confidence": fact.confidence,
                }
            )

        for operation in understanding.proposed_patch.operations:
            if operation.source_quote not in corpus["current_message"] and operation.source_quote not in corpus["history"] and operation.source_quote not in corpus["confirmed_fact"]:
                errors.append(
                    f"{operation.path}: patch source_quote is absent from user/history/facts"
                )

        return EvidenceValidationResult(
            valid=not errors, errors=errors, field_source_chain=chain
        )


class ModelUnderstandingError(RuntimeError):
    """Raised when the semantic model is missing or returns invalid evidence."""


class ModelNativeUnderstandingService:
    """Run a semantic model and validate its structured understanding."""

    def __init__(self, model_call: Callable[[str, dict[str, Any]], dict[str, Any]] | None):
        self._model_call = model_call
        self._validator = StructuredEvidenceValidator()

    def understand(
        self, context: UnderstandingContext
    ) -> tuple[StructuredUnderstanding, EvidenceValidationResult]:
        if self._model_call is None:
            raise ModelUnderstandingError("LLM_DISABLED: semantic understanding requires a model")
        raw = self._model_call(context.prompt_payload(), context.output_schema)
        try:
            understanding = StructuredUnderstanding.model_validate(raw)
        except Exception as exc:
            raise ModelUnderstandingError(f"INVALID_STRUCTURED_UNDERSTANDING: {exc}") from exc
        validation = self._validator.validate(understanding, context)
        if not validation.valid:
            raise ModelUnderstandingError(
                "EVIDENCE_VALIDATION_FAILED: " + "; ".join(validation.errors)
            )
        return understanding, validation
