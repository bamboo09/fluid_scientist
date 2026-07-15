"""Repair module for OpenFOAM error diagnosis and controlled repair loop.

Components:
- error_classifier: Classifies OpenFOAM errors into categories
- repair_policy: Controls retry limits and phase freezing
- repair_context_builder: Builds context for LLM diagnosis
- llm_diagnoser: Uses LLM to diagnose errors and suggest fixes
- controlled_repair_executor: Applies fixes and validates them
- repair_orchestrator: Coordinates the full repair loop
"""

from fluid_scientist.repair.error_classifier import (
    ClassifiedError,
    ErrorCategory,
    ErrorSeverity,
    OpenFOAMErrorClassifier,
)
from fluid_scientist.repair.repair_policy import (
    RepairAttempt,
    RepairLevel,
    RepairPhase,
    RepairPolicy,
    RepairStatus,
)
from fluid_scientist.repair.repair_context_builder import RepairContextBuilder
from fluid_scientist.repair.llm_diagnoser import LLMDiagnoser
from fluid_scientist.repair.controlled_repair_executor import (
    ControlledRepairExecutor,
    RepairResult,
)
from fluid_scientist.repair.repair_orchestrator import (
    RepairOrchestrationResult,
    RepairOrchestrator,
)

__all__ = [
    "ClassifiedError",
    "ErrorCategory",
    "ErrorSeverity",
    "OpenFOAMErrorClassifier",
    "RepairAttempt",
    "RepairLevel",
    "RepairPhase",
    "RepairPolicy",
    "RepairStatus",
    "RepairContextBuilder",
    "LLMDiagnoser",
    "ControlledRepairExecutor",
    "RepairResult",
    "RepairOrchestrationResult",
    "RepairOrchestrator",
]
