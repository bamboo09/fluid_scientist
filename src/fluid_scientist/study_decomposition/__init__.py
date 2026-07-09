"""Study decomposition module.

Decomposes a user's natural-language description of simulation studies
into structured :class:`~fluid_scientist.study_decomposition.models.StudyIntent`
objects.  Supports both single-study and batch inputs.

Typical flow::

    splitter = StudySplitter()
    blocks = splitter.split(user_message)      # one block per study
    # ... each block is parsed into a StudyIntent, grouped into a BatchStudyPlan
"""

from fluid_scientist.study_decomposition.condition_extractor import ConditionExtractor
from fluid_scientist.study_decomposition.models import (
    AmbiguityItem,
    BatchStudyPlan,
    ExtractedParameter,
    ObservableSpec,
    PhysicsFrame,
    StudyIntent,
)
from fluid_scientist.study_decomposition.observable_extractor import ObservableExtractor
from fluid_scientist.study_decomposition.parameter_extractor import ParameterExtractor
from fluid_scientist.study_decomposition.physics_extractor import PhysicsFrameExtractor
from fluid_scientist.study_decomposition.splitter import StudySplitter
from fluid_scientist.study_decomposition.study_type_classifier import StudyTypeClassifier

__all__ = [
    "AmbiguityItem",
    "BatchStudyPlan",
    "ConditionExtractor",
    "ExtractedParameter",
    "ObservableExtractor",
    "ObservableSpec",
    "ParameterExtractor",
    "PhysicsFrame",
    "PhysicsFrameExtractor",
    "StudyIntent",
    "StudySplitter",
    "StudyTypeClassifier",
]
