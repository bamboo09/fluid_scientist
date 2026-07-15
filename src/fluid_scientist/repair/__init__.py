"""Repair module for OpenFOAM error diagnosis and controlled repair loop."""

from fluid_scientist.repair.error_classifier import (
    ClassifiedError,
    ErrorCategory,
    ErrorSeverity,
    OpenFOAMErrorClassifier,
)

__all__ = [
    "ClassifiedError",
    "ErrorCategory",
    "ErrorSeverity",
    "OpenFOAMErrorClassifier",
]
