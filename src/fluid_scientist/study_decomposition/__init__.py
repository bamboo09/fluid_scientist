"""Study decomposition module.

Decomposes a user's natural-language description of simulation studies
into structured :class:`~fluid_scientist.study_decomposition.models.StudyIntent`
objects.  Supports both single-study and batch inputs.

Typical flow::

    splitter = StudySplitter()
    blocks = splitter.split(user_message)      # one block per study
    # ... each block is parsed into a StudyIntent, grouped into a BatchStudyPlan
"""

from fluid_scientist.study_decomposition.models import (
    AmbiguityItem,
    BatchStudyPlan,
    ExtractedParameter,
    ObservableSpec,
    PhysicsFrame,
    StudyIntent,
)
from fluid_scientist.study_decomposition.splitter import StudySplitter

__all__ = [
    "AmbiguityItem",
    "BatchStudyPlan",
    "ExtractedParameter",
    "ObservableSpec",
    "PhysicsFrame",
    "StudyIntent",
    "StudySplitter",
]
