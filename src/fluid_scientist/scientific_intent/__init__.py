"""Generic scientific intent models for CFD research understanding.

This package provides structured, schema-validated models for expressing
scientific intent across arbitrary CFD problems -- not just pipes,
cylinders or jets.  LLM parsing produces instances of these models;
deterministic downstream stages consume them.
"""

from fluid_scientist.scientific_intent.models import (
    AnalysisGoal,
    BoundaryIntent,
    ComparisonIntent,
    CredibilityRequirement,
    GeometryIntent,
    InitialConditionIntent,
    MaterialIntent,
    MeasurementIntent,
    MotionIntent,
    NumericalIntent,
    PhysicalModelIntent,
    PhysicsEntity,
    StudyIntent,
)

__all__ = [
    "AnalysisGoal",
    "BoundaryIntent",
    "ComparisonIntent",
    "CredibilityRequirement",
    "GeometryIntent",
    "InitialConditionIntent",
    "MaterialIntent",
    "MeasurementIntent",
    "MotionIntent",
    "NumericalIntent",
    "PhysicalModelIntent",
    "PhysicsEntity",
    "StudyIntent",
]
