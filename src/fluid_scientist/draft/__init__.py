"""Draft workflow package.

Produces a structured, reviewable :class:`ExperimentDraft` from a
:class:`~fluid_scientist.study_decomposition.models.StudyIntent`, validates
it, and (later) manages change proposals against confirmed drafts.
"""

from fluid_scientist.draft.draft_generator import DraftGenerator
from fluid_scientist.draft.models import (
    ChangeProposal,
    DraftChange,
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
    ValidationResult,
)
from fluid_scientist.draft.validator import DraftValidator

__all__ = [
    "ChangeProposal",
    "DraftChange",
    "DraftGenerator",
    "DraftParameter",
    "DraftStatus",
    "DraftValidator",
    "ExperimentDraft",
    "ParameterSource",
    "ValidationResult",
]
