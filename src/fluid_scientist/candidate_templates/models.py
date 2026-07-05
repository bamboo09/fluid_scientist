"""Candidate template library lifecycle models.

A successful generated case is not automatically a template.  The researcher
explicitly chooses whether to create a candidate; publication into the active
template registry requires repeatable validation and a second human approval.

State machine::

    DRAFT -> STATIC_VALIDATED -> PILOT_PASSED -> CANDIDATE_APPROVED
         -> REGRESSION_PASSED -> PUBLISHED

Rejection records a reason and is reversible through a new version.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints

from fluid_scientist.case_generation.models import Identifier


class CandidateState(str, Enum):
    DRAFT = "draft"
    STATIC_VALIDATED = "static_validated"
    PILOT_PASSED = "pilot_passed"
    CANDIDATE_APPROVED = "candidate_approved"
    REGRESSION_PASSED = "regression_passed"
    PUBLISHED = "published"
    REJECTED = "rejected"


_ALLOWED_TRANSITIONS: dict[CandidateState, frozenset[CandidateState]] = {
    CandidateState.DRAFT: frozenset(
        {CandidateState.STATIC_VALIDATED, CandidateState.REJECTED}
    ),
    CandidateState.STATIC_VALIDATED: frozenset(
        {CandidateState.PILOT_PASSED, CandidateState.REJECTED}
    ),
    CandidateState.PILOT_PASSED: frozenset(
        {CandidateState.CANDIDATE_APPROVED, CandidateState.REJECTED}
    ),
    CandidateState.CANDIDATE_APPROVED: frozenset(
        {CandidateState.REGRESSION_PASSED, CandidateState.REJECTED}
    ),
    CandidateState.REGRESSION_PASSED: frozenset(
        {CandidateState.PUBLISHED, CandidateState.REJECTED}
    ),
    CandidateState.PUBLISHED: frozenset(),
    CandidateState.REJECTED: frozenset(),
}

Reason = Annotated[
    StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class CandidateTransitionError(ValueError):
    """Raised when a state transition is not allowed."""


def assert_transition(current: CandidateState, target: CandidateState) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise CandidateTransitionError(
            f"candidate template cannot transition from {current.value} to {target.value}"
        )


class CandidateTemplateRecord(BaseModel):
    """Credential-free API projection of a candidate template."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: Identifier
    draft_id: Identifier
    project_id: Identifier
    plan_id: Identifier
    plan_version: int = Field(ge=1, strict=True)
    draft_version: int = Field(ge=1, strict=True)
    archive_sha256: Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    state: CandidateState
    rejection_reason: str | None = None
    created_at: str
    updated_at: str


class CreateCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: Identifier


class RejectCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class ApproveCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


__all__ = [
    "ApproveCandidateRequest",
    "CandidateState",
    "CandidateTemplateRecord",
    "CandidateTransitionError",
    "CreateCandidateRequest",
    "RejectCandidateRequest",
    "assert_transition",
]
