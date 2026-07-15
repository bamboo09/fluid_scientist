"""Requirement Coverage -- ensures 100% of user-stated facts are mapped.

This module implements the coverage checker described in section 15 of
the refactor plan.  Every fact extracted from the user's natural-language
description MUST be traced to a concrete element in the
:class:`~fluid_scientist.case_ir.models.RequestedCaseIR` (or to an
explicit clarification / rejection).  The coverage report is the
accountability mechanism that prevents silent information loss.

Typical usage::

    from fluid_scientist.case_ir.coverage import compute_coverage

    report = compute_coverage(facts_list, case_ir_dict)
    if not report.is_complete:
        raise ValueError(f"Unresolved facts: {report.unresolved}")
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# FactRecord -- a single fact extracted from user input
# ---------------------------------------------------------------------------

FactType = Literal[
    "entity",
    "parameter",
    "boundary",
    "initial_condition",
    "objective",
    "constraint",
    "observable",
    "time_order",
    "unknown",
]


class FactRecord(BaseModel):
    """A single fact extracted from user input.

    Each fact carries a ``mapped_to`` field that traces it to a
    concrete element in the Case IR.  The mapping value can be:

    - A Case IR path (e.g. ``"entities.cylinder_1.parameters.diameter"``)
      when the fact has been successfully incorporated.
    - ``"unresolved"`` when the fact could not be mapped.
    - ``"clarification:Q1"`` when the fact requires user clarification
      before it can be mapped.
    - ``"rejected:reason"`` when the fact was intentionally rejected
      (e.g. it conflicts with a confirmed assumption).

    Attributes:
        fact_id: Unique identifier, typically ``F1``, ``F2``, etc.
        raw_text: The original text snippet from the user's input.
        fact_type: Categorical type of the fact.
        mapped_to: Where this fact was mapped in the Case IR.
    """

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    raw_text: str
    fact_type: FactType = "unknown"
    mapped_to: str = ""


# ---------------------------------------------------------------------------
# RequirementCoverage -- aggregate coverage report
# ---------------------------------------------------------------------------


class RequirementCoverage(BaseModel):
    """Coverage report for user facts.

    Tracks the mapping status of every fact extracted from the user's
    description.  The :attr:`is_complete` property returns ``True`` only
    when every fact has been mapped to a concrete Case IR element (i.e.
    coverage is 100%).

    Attributes:
        total_facts: Total number of facts extracted.
        mapped_facts: Number of facts successfully mapped.
        coverage: Fraction of facts mapped, in ``[0.0, 1.0]``.
        facts: Ordered list of :class:`FactRecord` objects.
        unresolved: List of fact IDs that remain unresolved.
        clarifications_needed: List of fact IDs that need clarification.
    """

    model_config = ConfigDict(extra="forbid")

    total_facts: int = 0
    mapped_facts: int = 0
    coverage: float = 0.0
    facts: list[FactRecord] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    clarifications_needed: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """True if all facts are mapped (coverage >= 1.0)."""
        return self.coverage >= 1.0

    def add_fact(self, fact: FactRecord) -> None:
        """Add a fact record and update aggregate statistics."""
        self.facts.append(fact)
        self.total_facts = len(self.facts)
        self.mapped_facts = sum(
            1
            for f in self.facts
            if f.mapped_to and not f.mapped_to.startswith("unresolved")
        )
        self.coverage = (
            self.mapped_facts / self.total_facts
            if self.total_facts > 0
            else 0.0
        )
        if fact.mapped_to.startswith("unresolved"):
            self.unresolved.append(fact.fact_id)
        elif fact.mapped_to.startswith("clarification:"):
            self.clarifications_needed.append(fact.fact_id)

    def recompute(self) -> None:
        """Recompute aggregate statistics from the current fact list.

        Useful after batch modifications to ``self.facts``.
        """
        self.total_facts = len(self.facts)
        self.mapped_facts = sum(
            1
            for f in self.facts
            if f.mapped_to and not f.mapped_to.startswith("unresolved")
        )
        self.coverage = (
            self.mapped_facts / self.total_facts
            if self.total_facts > 0
            else 0.0
        )
        self.unresolved = [
            f.fact_id
            for f in self.facts
            if f.mapped_to.startswith("unresolved")
        ]
        self.clarifications_needed = [
            f.fact_id
            for f in self.facts
            if f.mapped_to.startswith("clarification:")
        ]


# ---------------------------------------------------------------------------
# compute_coverage -- functional API
# ---------------------------------------------------------------------------


def compute_coverage(
    facts: list[dict[str, Any]],
    case_ir: dict[str, Any],
) -> RequirementCoverage:
    """Compute coverage of user facts against the Case IR.

    Args:
        facts: A list of fact dictionaries.  Each dictionary should
            contain ``raw_text``, ``type``, and ``mapped_to`` keys.
        case_ir: The Case IR as a dictionary (unused in the current
            implementation but reserved for future deep-verification
            logic that cross-checks each ``mapped_to`` path against the
            actual IR structure).

    Returns:
        A :class:`RequirementCoverage` with all facts registered.
    """
    coverage = RequirementCoverage()
    for i, fact in enumerate(facts, 1):
        fact_record = FactRecord(
            fact_id=fact.get("fact_id", f"F{i}"),
            raw_text=fact.get("raw_text", ""),
            fact_type=fact.get("type", "unknown"),
            mapped_to=fact.get("mapped_to", "unresolved"),
        )
        coverage.add_fact(fact_record)
    return coverage


__all__ = [
    "FactRecord",
    "FactType",
    "RequirementCoverage",
    "compute_coverage",
]
